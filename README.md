# Lit-review matrix generator

YAML study instructions in → Google Scholar search → OpenAlex enrichment → CSV matrix out.

## Pipeline

1. **Google Scholar via [SerpAPI](https://serpapi.com/google-scholar-api)** — discover papers from your boolean queries
2. **[OpenAlex](https://openalex.org/)** (free) — fill fuller abstracts, keywords/topics, and missing DOIs

SerpAPI is billed **per search page** (~10 results). OpenAlex is free; set `OPENALEX_MAILTO` for their polite pool.

```bash
SERPAPI_API_KEY=...
OPENALEX_MAILTO=you@example.com   # optional but recommended
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
| `screen` | Optional allowed values for a manual `screen` CSV column |
| `facets` | Optional coding facets; each becomes an empty CSV column; allowed values go in metadata |

Minimal smoke test (1 SerpAPI credit): [`examples/smoke_test.yaml`](examples/smoke_test.yaml).

## Run

```bash
litreview examples/smoke_test.yaml
# or
python -m litreview examples/sample_study.yaml

# Scholar only (skip OpenAlex):
litreview examples/smoke_test.yaml --no-openalex
```

Each run writes a folder like `outputs/<study_id>-<timestamp>/` containing:

- `matrix.csv` — open in Excel
- `metadata.yaml` — study instructions + run stats

Shared response cache lives in `.cache/serpapi` and `.cache/openalex` so re-runs avoid re-billing / re-fetching.

CSV columns: `title`, `year`, `venue`, `abstract`, `citation_count`, `paper_url`, `query`, `scholar_rank`, `scholar_page`, `doi`, `keywords`.

`scholar_rank` is 1-based position within that query’s relevance-ranked results; `scholar_page` is `((rank - 1) // 10) + 1`. After dedupe, the kept row keeps the rank/page from the **first** query that found it.

The CSV is written as **UTF-8 with BOM** so Excel on Windows displays ellipses (`…`), dashes, and accents correctly.

Notes:

- Scholar snippets are replaced with OpenAlex abstracts when a match is found and the snippet looks truncated/short.
- Keywords come from OpenAlex (`keywords`, else `topics`, else `concepts`).
- Missing DOIs are filled from OpenAlex (DOI lookup first, then title match).
- Coverage is imperfect — some papers won’t match or won’t have abstracts.
- Duplicates are dropped as soon as a matching **normalized title** or **DOI** appears (first hit wins).

## Screening

Inclusion/exclusion criteria are copied into `metadata.yaml` for reference. Candidate screening is **manual in Excel** for now.
