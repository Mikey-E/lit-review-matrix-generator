# Lit-review matrix generator

YAML study instructions in → Google Scholar search → OpenAlex enrichment → optional OpenAI coding → CSV matrix out.

## Pipeline

1. **Google Scholar via [SerpAPI](https://serpapi.com/google-scholar-api)** — discover papers from your boolean queries
2. **[OpenAlex](https://openalex.org/)** (free) — fill fuller abstracts, keywords/topics, and missing DOIs
3. **OpenAI (optional)** — Stage A `screen` (include/exclude/maybe), then Stage B facets one-at-a-time for include+maybe

```bash
SERPAPI_API_KEY=...
OPENALEX_MAILTO=you@example.com   # optional but recommended
OPENAI_API_KEY=...                # only needed for --llm-code / litreview code
```

## Install

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
pip install -e ".[dev]"
```

## YAML config

See [`studies/multimodal-cea-yield-cost.yaml`](studies/multimodal-cea-yield-cost.yaml) and [`examples/sample_study.yaml`](examples/sample_study.yaml).

| Field | Purpose |
| --- | --- |
| `title` | Tentative study title |
| `study_id` | Optional slug used in the output folder name |
| `output_dir` | Optional explicit output folder |
| `inclusion_criteria` / `exclusion_criteria` | Used for records + LLM Stage A |
| `year_from` / `year_to` | Passed to Scholar as `as_ylo` / `as_yhi` |
| `max_pages` / `max_results` | Stop at whichever limit hits first |
| `queries` | Boolean strings sent to Scholar as `q` (optionally named) |
| `screen` | Allowed `screen` values |
| `facets` | Coding facets + allowed values (empty CSV columns; values also in metadata) |
| `llm.model` | OpenAI model id (default `gpt-4o-mini` if coding without YAML model) |

## Run

```bash
# Harvest only
litreview studies/multimodal-cea-yield-cost.yaml
# equivalent:
litreview harvest studies/multimodal-cea-yield-cost.yaml

# Harvest + OpenAI draft coding
litreview harvest studies/multimodal-cea-yield-cost.yaml --llm-code

# Code an existing matrix (post-harvest)
litreview code studies/multimodal-cea-yield-cost.yaml outputs/some-run/matrix.csv
```

Each harvest writes `outputs/<study_id>-<timestamp>/` with `matrix.csv` and `metadata.yaml`.

Shared caches: `.cache/serpapi`, `.cache/openalex`, `.cache/openai`.

### LLM coding details

- Temperature **0**, **strict JSON schema** enums from YAML
- Stage A input: title, abstract, venue, year + inclusion/exclusion criteria
- Stage B input: title, abstract, venue, year + one facet’s allowed values (no inclusion criteria)
- Stage B runs only when `screen` is `include` or `maybe`
- CSV gets `llm_model`; rationales are stored in the OpenAI cache for audit
- Labels are **drafts** — review `maybe` / `unclear` and spot-check the rest

CSV column order: `title`, `screen` (when configured), `abstract`, then remaining metadata/coding fields.

`openalex_error` is empty on success/unmatched; on API failures it stores a short code like `http_400`.

The CSV is **UTF-8 with BOM** for Excel on Windows.
