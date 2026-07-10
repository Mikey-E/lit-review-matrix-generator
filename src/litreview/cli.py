from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from litreview.cache import ResponseCache
from litreview.config import load_config
from litreview.dedupe import Deduper
from litreview.openalex import OpenAlexClient, enrich_rows
from litreview.output import build_metadata, resolve_run_dir, write_matrix, write_metadata
from litreview.scholar import ScholarClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="litreview",
        description=(
            "Run Google Scholar searches from a YAML study config and write a "
            "CSV lit-review matrix plus metadata."
        ),
    )
    parser.add_argument(
        "config",
        type=Path,
        help="Path to YAML study instructions (see examples/sample_study.yaml)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable reading/writing the on-disk API response cache.",
    )
    parser.add_argument(
        "--no-openalex",
        action="store_true",
        help="Skip OpenAlex enrichment (abstracts, keywords, missing DOIs).",
    )
    return parser


def run(
    config_path: Path,
    *,
    use_cache: bool = True,
    use_openalex: bool = True,
) -> Path:
    load_dotenv()
    config = load_config(config_path)
    run_dir = resolve_run_dir(config)
    run_dir.mkdir(parents=True, exist_ok=True)

    # Shared project cache so re-runs do not re-bill SerpAPI / re-hit OpenAlex.
    shared_cache_root = Path(".cache")
    serpapi_cache = (
        ResponseCache(shared_cache_root / "serpapi") if use_cache else None
    )
    client = ScholarClient(cache=serpapi_cache)
    deduper = Deduper()
    rows = []

    for query in config.queries:
        for paper in client.iter_query_results(query, config):
            if deduper.keep(paper):
                rows.append(paper)

    openalex_stats = None
    if use_openalex and rows:
        openalex_cache = (
            ResponseCache(shared_cache_root / "openalex") if use_cache else None
        )
        openalex = OpenAlexClient(cache=openalex_cache)
        rows = enrich_rows(rows, openalex)
        openalex_stats = openalex.stats

    write_matrix(run_dir / "matrix.csv", rows, extra_columns=config.coding_columns)
    write_metadata(
        run_dir / "metadata.yaml",
        build_metadata(
            config,
            rows_written=len(rows),
            duplicates_dropped=deduper.dropped,
            api_calls=client.api_calls,
            cache_hits=client.cache_hits,
            run_dir=run_dir,
            openalex_stats=openalex_stats,
        ),
    )
    return run_dir


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        run_dir = run(
            args.config,
            use_cache=not args.no_cache,
            use_openalex=not args.no_openalex,
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote matrix to {run_dir / 'matrix.csv'}")
    print(f"Wrote metadata to {run_dir / 'metadata.yaml'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
