from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from litreview.config import StudyConfig
from litreview.models import CSV_COLUMNS, PaperRow


def resolve_run_dir(config: StudyConfig, base: Path | None = None) -> Path:
    if config.output_dir is not None:
        return config.output_dir.resolve()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = config.study_id or "run"
    root = (base or Path("outputs")).resolve()
    return root / f"{slug}-{stamp}"


def write_matrix(path: Path, rows: list[PaperRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig adds a BOM so Excel on Windows detects UTF-8 (avoids â€¦ mojibake).
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.to_dict())


def write_metadata(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, sort_keys=False, allow_unicode=True)


def build_metadata(
    config: StudyConfig,
    *,
    rows_written: int,
    duplicates_dropped: int,
    api_calls: int,
    cache_hits: int,
    run_dir: Path,
    openalex_stats: Any | None = None,
) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "rows_written": rows_written,
        "duplicates_dropped": duplicates_dropped,
        "serpapi_api_calls": api_calls,
        "serpapi_cache_hits": cache_hits,
    }
    if openalex_stats is not None:
        stats["openalex"] = {
            "looked_up": openalex_stats.looked_up,
            "matched": openalex_stats.matched,
            "unmatched": openalex_stats.unmatched,
            "abstracts_filled": openalex_stats.abstracts_filled,
            "keywords_filled": openalex_stats.keywords_filled,
            "dois_filled": openalex_stats.dois_filled,
            "api_calls": openalex_stats.api_calls,
            "cache_hits": openalex_stats.cache_hits,
        }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "study_id": config.study_id,
        "title": config.title,
        "source_config": str(config.source_path) if config.source_path else None,
        "inclusion_criteria": config.inclusion_criteria,
        "exclusion_criteria": config.exclusion_criteria,
        "year_from": config.year_from,
        "year_to": config.year_to,
        "max_pages": config.max_pages,
        "max_results": config.max_results,
        "queries": [{"name": q.name, "q": q.q} for q in config.queries],
        "screening": "manual (Excel)",
        "source": "google_scholar",
        "provider": "serpapi",
        "enrichment": "openalex" if openalex_stats is not None else None,
        "run_dir": str(run_dir),
        "files": {
            "matrix_csv": "matrix.csv",
            "metadata": "metadata.yaml",
            "shared_cache_dir": ".cache",
        },
        "stats": stats,
        "notes": [
            "Scholar provides discovery snippets; OpenAlex enrichment fills fuller abstracts when available.",
            "Keywords come from OpenAlex keywords/topics/concepts when Scholar has none.",
            "Missing DOIs are filled from OpenAlex matches (DOI lookup first, then title).",
            "Inclusion/exclusion criteria are recorded for reference; screening is manual.",
        ],
    }
