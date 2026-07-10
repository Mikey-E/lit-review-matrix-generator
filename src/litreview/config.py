from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class QuerySpec:
    q: str
    name: str | None = None

    @property
    def label(self) -> str:
        return self.name or self.q


@dataclass
class StudyConfig:
    title: str
    queries: list[QuerySpec]
    study_id: str | None = None
    output_dir: Path | None = None
    inclusion_criteria: str = ""
    exclusion_criteria: str = ""
    year_from: int | None = None
    year_to: int | None = None
    max_pages: int | None = None
    max_results: int | None = None
    source_path: Path | None = None

    def __post_init__(self) -> None:
        if not self.queries:
            raise ValueError("Config must include at least one query under 'queries'.")
        if self.max_pages is None and self.max_results is None:
            # Sensible default so a misconfigured run cannot burn the whole API quota.
            self.max_pages = 5
        if self.max_pages is not None and self.max_pages < 1:
            raise ValueError("max_pages must be >= 1 when set.")
        if self.max_results is not None and self.max_results < 1:
            raise ValueError("max_results must be >= 1 when set.")
        if (
            self.year_from is not None
            and self.year_to is not None
            and self.year_from > self.year_to
        ):
            raise ValueError("year_from cannot be greater than year_to.")


def _require_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if value is None or str(value).strip() == "":
        raise ValueError(f"Missing required config field: {key}")
    return str(value).strip()


def _optional_int(data: dict[str, Any], key: str) -> int | None:
    if key not in data or data[key] is None or data[key] == "":
        return None
    return int(data[key])


def _parse_queries(raw: Any) -> list[QuerySpec]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("'queries' must be a non-empty list.")
    queries: list[QuerySpec] = []
    for i, item in enumerate(raw):
        if isinstance(item, str):
            queries.append(QuerySpec(q=item.strip()))
            continue
        if not isinstance(item, dict) or "q" not in item:
            raise ValueError(
                f"queries[{i}] must be a string or a mapping with a 'q' field."
            )
        q = str(item["q"]).strip()
        if not q:
            raise ValueError(f"queries[{i}].q must be a non-empty string.")
        name = item.get("name")
        queries.append(
            QuerySpec(q=q, name=str(name).strip() if name else None)
        )
    return queries


def load_config(path: Path) -> StudyConfig:
    path = path.resolve()
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError("YAML config root must be a mapping.")

    output_dir = data.get("output_dir")
    return StudyConfig(
        title=_require_str(data, "title"),
        queries=_parse_queries(data.get("queries")),
        study_id=(
            str(data["study_id"]).strip()
            if data.get("study_id") not in (None, "")
            else None
        ),
        output_dir=Path(output_dir) if output_dir else None,
        inclusion_criteria=str(data.get("inclusion_criteria") or "").strip(),
        exclusion_criteria=str(data.get("exclusion_criteria") or "").strip(),
        year_from=_optional_int(data, "year_from"),
        year_to=_optional_int(data, "year_to"),
        max_pages=_optional_int(data, "max_pages"),
        max_results=_optional_int(data, "max_results"),
        source_path=path,
    )
