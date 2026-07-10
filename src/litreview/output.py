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
    with path.open("w", encoding="utf-8", newline="") as f:
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
) -> dict[str, Any]:
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
        "run_dir": str(run_dir),
        "files": {
            "matrix_csv": "matrix.csv",
            "metadata": "metadata.yaml",
            "cache_dir": "cache",
        },
        "stats": {
            "rows_written": rows_written,
            "duplicates_dropped": duplicates_dropped,
            "serpapi_api_calls": api_calls,
            "cache_hits": cache_hits,
        },
        "notes": [
            "abstract column is Google Scholar's result snippet (not a full abstract).",
            "keywords are usually empty from Scholar search results; column reserved for later enrichment.",
            "DOI is extracted from URLs/snippets when present; often missing.",
            "Inclusion/exclusion criteria are recorded for reference; screening is manual.",
        ],
    }
