# Lit-review matrix generator

YAML study instructions in → Google Scholar search → CSV matrix out.

## Approach: SerpAPI (recommended)

Google Scholar has **no official API**, and DIY scraping (`scholarly`, Playwright, etc.) gets CAPTCHAs and IP blocks quickly.

This project uses **[SerpAPI's Google Scholar engine](https://serpapi.com/google-scholar-api)**:

- Structured JSON (title, snippet, venue/year summary, citation counts, links)
- Handles proxies / CAPTCHAs on their side
- Billed **per search request (page)**, not per paper — typically ~10 results/page
- Free tier available to try; paid plans if you run larger reviews

Set your key in `.env` (see `.env.example`):

```bash
SERPAPI_API_KEY=...
```

## Install

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
pip install -e ".[dev]"
```

## YAML config

See [`examples/sample_study.yaml`](examples/sample_study.yaml). Important fields:

| Field | Purpose |
| --- | --- |
| `title` | Tentative study title |
| `study_id` | Optional slug used in the output folder name |
| `output_dir` | Optional explicit output folder |
| `inclusion_criteria` / `exclusion_criteria` | Free text for your records (screening stays manual in Excel) |
| `year_from` / `year_to` | Passed to Scholar as `as_ylo` / `as_yhi` |
| `max_pages` / `max_results` | Stop at whichever limit hits first |
| `queries` | Boolean strings sent to Scholar as `q` (optionally named) |

## Run

```bash
litreview examples/sample_study.yaml
# or
python -m litreview examples/sample_study.yaml
```

Each run writes a folder like `outputs/<study_id>-<timestamp>/` containing:

- `matrix.csv` — open in Excel
- `metadata.yaml` — study instructions + run stats
- `cache/` — raw SerpAPI JSON pages (re-runs reuse these and avoid re-billing)

CSV columns: `title`, `year`, `venue`, `abstract`, `citation_count`, `paper_url`, `query`, `doi`, `keywords`.

Notes:

- **abstract** is Scholar’s result snippet (not a full abstract).
- **DOI** is extracted from URLs/snippets when present; often missing.
- **keywords** are usually empty from Scholar search hits; column kept for later enrichment.
- Duplicates are dropped as soon as a matching **normalized title** or **DOI** appears (first hit wins).

## Screening

Inclusion/exclusion criteria are copied into `metadata.yaml` for reference. Candidate screening is **manual in Excel** for now.
