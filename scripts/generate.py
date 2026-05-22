#!/usr/bin/env python3
"""
Generate the Morning Papers static homepage.

Data sources:
- Crossref: finds recent journal articles.
- Semantic Scholar: enriches DOI results with abstract, citation count, and open links.

The script is dependency-free so it can run cleanly in GitHub Actions.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import os
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "journals.json"
OUTPUT_PATH = ROOT / "index.html"
CACHE_PATH = ROOT / ".cache" / "papers.json"


FIELD_IMAGES = {
    "Consumer Research": "https://images.unsplash.com/photo-1511988617509-a57c8a288659?fm=jpg&q=70&w=1200&auto=format&fit=crop",
    "Marketing": "https://images.unsplash.com/photo-1556742049-0cfed4f6a45d?fm=jpg&q=70&w=1200&auto=format&fit=crop",
    "Psychology": "https://images.unsplash.com/photo-1497366754035-f200968a6e72?fm=jpg&q=70&w=1200&auto=format&fit=crop",
    "Public Policy": "https://images.unsplash.com/photo-1529107386315-e1a2ed48a620?fm=jpg&q=70&w=1200&auto=format&fit=crop",
    "Family Studies": "https://images.unsplash.com/photo-1504151932400-72d4384f04b3?fm=jpg&q=70&w=1200&auto=format&fit=crop",
    "Urban Studies": "https://images.unsplash.com/photo-1449824913935-59a10b8d2000?fm=jpg&q=70&w=1200&auto=format&fit=crop",
    "Mental Health": "https://images.unsplash.com/photo-1506126613408-eca07ce68773?fm=jpg&q=70&w=1200&auto=format&fit=crop",
    "Food Industry / Science / Design": "https://images.unsplash.com/photo-1498837167922-ddd27525d352?fm=jpg&q=70&w=1200&auto=format&fit=crop",
    "Management": "https://images.unsplash.com/photo-1552664730-d307ca884978?fm=jpg&q=70&w=1200&auto=format&fit=crop",
}


def seeded_index(parts: list[str], length: int) -> int:
    seed = "|".join(parts)
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % max(length, 1)


def image_for_paper(field: dict[str, Any], doi: str, date: str, config: dict[str, Any]) -> str:
    style = config["site"].get("image_style", "")
    queries = field.get("image_queries") or [field["name"]]
    query = queries[seeded_index([field["name"], doi, date], len(queries))]
    full_query = f"{query}, {style}".strip(", ")

    unsplash_key = os.environ.get("UNSPLASH_ACCESS_KEY")
    if unsplash_key:
        image = search_unsplash_image(full_query, doi)
        if image:
            return image

    encoded = urllib.parse.quote(full_query)
    return f"https://source.unsplash.com/1200x800/?{encoded}"


def daily_fortune(config: dict[str, Any], day: dt.date) -> str:
    fortunes = config["site"].get("fortunes") or ["Begin before you feel ready."]
    return fortunes[seeded_index([day.isoformat()], len(fortunes))]


def search_unsplash_image(query: str, doi: str) -> str:
    params = {
        "query": query,
        "orientation": "landscape",
        "per_page": "10",
        "content_filter": "high",
    }
    url = "https://api.unsplash.com/search/photos?" + urllib.parse.urlencode(params)
    headers = {"Authorization": f"Client-ID {os.environ['UNSPLASH_ACCESS_KEY']}"}
    data = request_json(url, headers=headers, retries=1)
    results = data.get("results", []) if data else []
    if not results:
        return ""

    result = results[seeded_index([query, doi], len(results))]
    urls = result.get("urls", {})
    image_url = urls.get("regular") or urls.get("full") or urls.get("raw")
    if not image_url:
        return ""
    separator = "&" if "?" in image_url else "?"
    return f"{image_url}{separator}fm=jpg&q=72&w=1200&fit=crop"


@dataclass
class Paper:
    field: str
    title: str
    journal: str
    year: str
    date: str
    doi: str
    url: str
    abstract: str
    authors: list[str]
    citations: int
    image: str

    @property
    def score(self) -> tuple[str, int, str]:
        return (self.date or "0000-00-00", self.citations, self.title)


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def request_json(url: str, headers: Optional[dict[str, str]] = None, retries: int = 2) -> Optional[dict[str, Any]]:
    headers = headers or {}
    headers.setdefault("User-Agent", "MorningPapersBot/1.0 (GitHub Actions)")
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=25) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            if attempt == retries:
                print(f"warn: failed request {url}: {exc}")
                return None
            time.sleep(1.5 * (attempt + 1))
    return None


def clean_text(value: Any) -> str:
    if isinstance(value, list):
        value = " ".join(str(v) for v in value if v)
    if value is None:
        return ""
    value = re.sub(r"<[^>]+>", " ", str(value))
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def published_date(item: dict[str, Any]) -> str:
    for key in ("published-online", "published-print", "published", "created", "issued"):
        parts = item.get(key, {}).get("date-parts")
        if parts and parts[0]:
            year, month, day = (parts[0] + [1, 1])[:3]
            try:
                return dt.date(int(year), int(month), int(day)).isoformat()
            except ValueError:
                continue
    return ""


def authors_from_crossref(item: dict[str, Any]) -> list[str]:
    authors = []
    for author in item.get("author", [])[:4]:
        name = " ".join(part for part in [author.get("given"), author.get("family")] if part)
        if name:
            authors.append(name)
    return authors


def crossref_search(journal: str, start: dt.date, end: dt.date) -> list[dict[str, Any]]:
    params = {
        "query.container-title": journal,
        "filter": f"type:journal-article,from-pub-date:{start.isoformat()},until-pub-date:{end.isoformat()}",
        "sort": "published",
        "order": "desc",
        "rows": "8",
        "select": "DOI,title,container-title,published,published-online,published-print,created,issued,URL,abstract,author,is-referenced-by-count",
    }
    mailto = os.environ.get("CROSSREF_MAILTO")
    if mailto:
        params["mailto"] = mailto
    url = "https://api.crossref.org/works?" + urllib.parse.urlencode(params)
    data = request_json(url)
    return data.get("message", {}).get("items", []) if data else []


def semantic_scholar_by_doi(doi: str) -> dict[str, Any]:
    if not doi:
        return {}
    fields = "title,abstract,year,url,citationCount,authors,venue,externalIds,openAccessPdf,tldr"
    doi_key = urllib.parse.quote(f"DOI:{doi}", safe="")
    url = f"https://api.semanticscholar.org/graph/v1/paper/{doi_key}?fields={fields}"
    headers = {}
    api_key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
    if api_key:
        headers["x-api-key"] = api_key
    data = request_json(url, headers=headers, retries=1)
    return data or {}


def build_paper(field: dict[str, Any], item: dict[str, Any], config: dict[str, Any]) -> Optional[Paper]:
    doi = clean_text(item.get("DOI")).lower()
    title = clean_text(item.get("title"))
    journal = clean_text(item.get("container-title"))
    if not doi or not title:
        return None

    scholar = semantic_scholar_by_doi(doi)
    abstract = clean_text(scholar.get("abstract")) or clean_text(item.get("abstract"))
    tldr = clean_text((scholar.get("tldr") or {}).get("text"))
    if tldr and len(tldr) < len(abstract or ""):
        abstract = tldr

    authors = [clean_text(a.get("name")) for a in scholar.get("authors", [])[:4] if a.get("name")]
    if not authors:
        authors = authors_from_crossref(item)

    url = clean_text((scholar.get("openAccessPdf") or {}).get("url")) or clean_text(scholar.get("url")) or clean_text(item.get("URL"))
    date = published_date(item)
    year = str(scholar.get("year") or (date[:4] if date else ""))
    citations = int(scholar.get("citationCount") or item.get("is-referenced-by-count") or 0)

    return Paper(
        field=field["name"],
        title=title,
        journal=journal,
        year=year,
        date=date,
        doi=doi,
        url=url or f"https://doi.org/{doi}",
        abstract=abstract,
        authors=authors,
        citations=citations,
        image=image_for_paper(field, doi, date, config),
    )


def collect_papers(config: dict[str, Any]) -> list[Paper]:
    tz = ZoneInfo(config["site"].get("timezone", "Asia/Shanghai"))
    today = dt.datetime.now(tz).date()
    lookback = int(os.environ.get("LOOKBACK_DAYS", config["site"].get("lookback_days", 60)))
    start = today - dt.timedelta(days=lookback)

    by_doi: dict[str, Paper] = {}
    for field in config["fields"]:
        print(f"field: {field['name']}")
        for journal in field["journals"]:
            print(f"  journal: {journal}")
            for item in crossref_search(journal, start, today):
                paper = build_paper(field, item, config)
                if paper and paper.doi not in by_doi:
                    by_doi[paper.doi] = paper
                time.sleep(0.12)

    papers = list(by_doi.values())
    papers.sort(key=lambda p: p.score, reverse=True)

    selected: list[Paper] = []
    used_fields: set[str] = set()
    limit = int(config["site"].get("papers_per_day", 9))

    for paper in papers:
        if paper.field not in used_fields:
            selected.append(paper)
            used_fields.add(paper.field)
        if len(selected) >= limit:
            break
    for paper in papers:
        if len(selected) >= limit:
            break
        if paper not in selected:
            selected.append(paper)

    if selected:
        CACHE_PATH.parent.mkdir(exist_ok=True)
        CACHE_PATH.write_text(json.dumps([paper.__dict__ for paper in selected], ensure_ascii=False, indent=2), encoding="utf-8")
        return selected

    if CACHE_PATH.exists():
        cached = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        return [Paper(**paper) for paper in cached]

    raise RuntimeError("No papers found and no cache exists.")


def short_abstract(text: str, max_chars: int = 360) -> str:
    text = clean_text(text)
    if not text:
        return "Semantic Scholar / Crossref 暂无摘要。建议点进原文查看 abstract。"
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0].rstrip(".,;:") + "..."


def render_html(config: dict[str, Any], papers: list[Paper]) -> str:
    tz = ZoneInfo(config["site"].get("timezone", "Asia/Shanghai"))
    now = dt.datetime.now(tz)
    date_label = now.strftime("%Y.%m.%d")
    machine_date = now.strftime("%Y-%m-%d %H:%M")
    fortune = daily_fortune(config, now.date())
    nav = "\n".join(
        f'        <li><a href="#p{i}"><span>{html.escape(p.field)}</span>{html.escape(p.title)}</a></li>'
        for i, p in enumerate(papers, 1)
    )
    cards = "\n".join(render_card(i, paper) for i, paper in enumerate(papers, 1))

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="{html.escape(config['site']['name'])}">
<meta name="theme-color" content="#eee8db">
<meta name="description" content="{html.escape(config['site']['tagline'])}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Noto+Sans+TC:wght@400;500;600;700&display=swap" rel="stylesheet">
<title>{html.escape(config['site']['name'])} · {machine_date}</title>
<style>
  :root {{
    --bg: #eee8db;
    --paper: #f7f2e8;
    --ink: #25231f;
    --muted: #756e61;
    --line: #d7cdbc;
    --accent: #9d2f27;
    --blue: #315b73;
    --shadow: rgba(70, 54, 32, .13);
  }}
  * {{ box-sizing: border-box; }}
  html {{ scroll-behavior: smooth; }}
  body {{
    margin: 0;
    background: var(--bg);
    color: var(--ink);
    font-family: "Inter", "Noto Sans TC", system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
    font-size: 17px;
    line-height: 1.64;
    -webkit-font-smoothing: antialiased;
  }}
  body::before {{
    content: "";
    position: fixed;
    inset: 0;
    pointer-events: none;
    opacity: .22;
    mix-blend-mode: multiply;
    background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='160' height='160'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='1.2' numOctaves='2'/></filter><rect width='100%25' height='100%25' filter='url(%23n)' opacity='0.18'/></svg>");
  }}
  main {{
    width: min(720px, 100%);
    margin: 0 auto;
    padding: max(32px, env(safe-area-inset-top)) 20px 64px;
  }}
  header {{
    min-height: 72svh;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 56px 0 42px;
    text-align: center;
    border-bottom: 1px solid var(--line);
  }}
  .kicker {{
    font: 700 12px/1.2 Inter, system-ui, sans-serif;
    letter-spacing: .18em;
    color: var(--accent);
    text-transform: uppercase;
  }}
  h1 {{
    margin: 18px 0 18px;
    font: 700 clamp(48px, 15vw, 92px)/.92 Inter, system-ui, sans-serif;
    letter-spacing: 0;
  }}
  .meta {{
    max-width: 29rem;
    color: var(--accent);
    font: 600 17px/1.55 Inter, "Noto Sans TC", system-ui, sans-serif;
  }}
  nav {{
    margin: 32px 0 42px;
    border-top: 1px solid var(--line);
    border-bottom: 1px solid var(--line);
  }}
  nav ol {{
    list-style: none;
    margin: 0;
    padding: 8px 0;
  }}
  nav a {{
    display: grid;
    gap: 2px;
    padding: 10px 0;
    color: var(--ink);
    text-decoration: none;
    border-bottom: 1px solid rgba(37,35,31,.08);
  }}
  nav li:last-child a {{ border-bottom: 0; }}
  nav span {{
    color: var(--blue);
    font: 700 12px/1.3 Inter, system-ui, sans-serif;
  }}
  article {{
    margin: 0 0 34px;
    overflow: hidden;
    background: var(--paper);
    border: 1px solid rgba(70,54,32,.14);
    border-radius: 8px;
    box-shadow: 0 14px 38px var(--shadow);
  }}
  .photo {{
    position: relative;
    aspect-ratio: 16 / 10;
    background: #d7cdbc;
    overflow: hidden;
  }}
  .photo img {{
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
    filter: saturate(.82) contrast(.94) sepia(.08);
  }}
  .photo::after {{
    content: "";
    position: absolute;
    inset: 0;
    background: linear-gradient(180deg, transparent 45%, rgba(0,0,0,.38));
  }}
  .badge {{
    position: absolute;
    left: 16px;
    bottom: 14px;
    z-index: 1;
    padding: 6px 9px;
    border-radius: 999px;
    background: rgba(247,242,232,.88);
    color: var(--ink);
    font: 600 12px/1 Inter, system-ui, sans-serif;
  }}
  .content {{ padding: 22px 20px 24px; }}
  h2 {{
    margin: 0 0 8px;
    font: 700 27px/1.12 Inter, "Noto Sans TC", system-ui, sans-serif;
    letter-spacing: 0;
  }}
  .journal {{
    margin: 0 0 14px;
    color: var(--muted);
    font: 500 14px/1.5 Inter, system-ui, sans-serif;
  }}
  .abstract {{ margin: 0 0 16px; }}
  .links {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    font: 600 14px/1.2 Inter, system-ui, sans-serif;
  }}
  .links a {{
    color: var(--ink);
    text-decoration: none;
    border-bottom: 1px solid var(--accent);
  }}
  .doi {{
    color: var(--muted);
    text-align: right;
    overflow-wrap: anywhere;
    font-weight: 500;
  }}
  footer {{
    margin-top: 44px;
    color: var(--muted);
    font: 500 12px/1.55 Inter, system-ui, sans-serif;
  }}
  @media (max-width: 520px) {{
    main {{ padding-left: 16px; padding-right: 16px; }}
    h2 {{ font-size: 24px; }}
    .links {{ display: block; }}
    .doi {{ margin-top: 10px; text-align: left; }}
  }}
</style>
</head>
<body>
<main>
  <header>
    <div class="kicker">Daily academic briefing</div>
    <h1>{html.escape(config['site']['name'])}</h1>
    <div class="meta">{date_label}<br>{html.escape(fortune)}</div>
  </header>
  <nav aria-label="Today's papers">
    <ol>
{nav}
    </ol>
  </nav>
{cards}
  <footer>
    自动生成页面。期刊清单见 <code>config/journals.json</code>；如果某天没有足够新论文，页面会保留最近一次可生成的结果。
  </footer>
</main>
</body>
</html>
"""


def render_card(index: int, paper: Paper) -> str:
    author_line = ", ".join(paper.authors[:3])
    if len(paper.authors) > 3:
        author_line += " et al."
    detail = " · ".join(part for part in [paper.journal, paper.year, author_line] if part)
    return f"""  <article id="p{index}">
    <div class="photo">
      <img src="{html.escape(paper.image)}" alt="{html.escape(paper.field)} visual" loading="lazy">
      <div class="badge">{index:02d} · {html.escape(paper.field)}</div>
    </div>
    <div class="content">
      <h2>{html.escape(paper.title)}</h2>
      <p class="journal">{html.escape(detail)}</p>
      <p class="abstract">{html.escape(short_abstract(paper.abstract))}</p>
      <div class="links">
        <a href="{html.escape(paper.url)}" target="_blank" rel="noopener">Read paper</a>
        <div class="doi">DOI: {html.escape(paper.doi)}</div>
      </div>
    </div>
  </article>"""


def main() -> None:
    config = load_config()
    papers = collect_papers(config)
    OUTPUT_PATH.write_text(render_html(config, papers), encoding="utf-8")
    print(f"wrote {OUTPUT_PATH} with {len(papers)} papers")


if __name__ == "__main__":
    main()
