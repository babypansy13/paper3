# Morning Papers

Static HTML app generated for GitHub Pages.

Open `index.html` directly, or publish the repository with GitHub Pages using the `main` branch and `/root` folder.

## Daily updates

The repository includes a GitHub Actions workflow at `.github/workflows/daily-update.yml`.
It runs every day at 08:00 Beijing Time, fetches recent papers from Crossref and Semantic Scholar, regenerates `index.html`, and commits the update.

The journal and field list lives in `config/journals.json`.

Optional repository secrets:

- `CROSSREF_MAILTO`: your email address, recommended by Crossref etiquette.
- `SEMANTIC_SCHOLAR_API_KEY`: optional Semantic Scholar API key for higher rate limits.
- `UNSPLASH_ACCESS_KEY`: optional Unsplash API key for more reliable Kodak 5207 / Gold 200 / Cinestill-style image search. Without it, the page falls back to query-based Unsplash image URLs.
