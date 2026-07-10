from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from litreview.cache import ResponseCache
from litreview.config import load_config
from litreview.dedupe import Deduper
from litreview.llm_code import DEFAULT_LLM_MODEL, LlmCoder
from litreview.openalex import OpenAlexClient, enrich_records, enrich_rows
from litreview.output import (
    build_metadata,
    read_matrix,
    resolve_run_dir,
    write_matrix,
    write_metadata,
)
from litreview.scholar import ScholarClient


def _shared_cache_root() -> Path:
    return Path(".cache")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="litreview",
        description=(
            "Lit-review matrix generator: Scholar harvest, OpenAlex enrichment, "
            "optional OpenAI screening/coding."
        ),
    )
    sub = parser.add_subparsers(dest="command")

    harvest = sub.add_parser(
        "harvest",
        help="Search Google Scholar and write a matrix CSV (default command).",
    )
    harvest.add_argument("config", type=Path, help="YAML study config")
    harvest.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable on-disk API response caches.",
    )
    harvest.add_argument(
        "--no-openalex",
        action="store_true",
        help="Skip OpenAlex enrichment.",
    )
    harvest.add_argument(
        "--llm-code",
        action="store_true",
        help="After harvest, run OpenAI Stage A/B coding into the matrix.",
    )

    code = sub.add_parser(
        "code",
        help="Run OpenAI Stage A/B coding on an existing matrix CSV.",
    )
    code.add_argument("config", type=Path, help="YAML study config")
    code.add_argument(
        "matrix",
        type=Path,
        help="Path to matrix.csv (or a run directory containing matrix.csv)",
    )
    code.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable on-disk OpenAI response cache.",
    )

    enrich = sub.add_parser(
        "enrich",
        help="Re-run OpenAlex enrichment on an existing matrix CSV (no SerpAPI).",
    )
    enrich.add_argument("config", type=Path, help="YAML study config")
    enrich.add_argument(
        "matrix",
        type=Path,
        help="Path to matrix.csv (or a run directory containing matrix.csv)",
    )
    enrich.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable on-disk OpenAlex response cache.",
    )
    return parser


def _resolve_matrix_path(matrix: Path) -> Path:
    matrix = matrix.resolve()
    if matrix.is_dir():
        candidate = matrix / "matrix.csv"
        if not candidate.exists():
            raise FileNotFoundError(f"No matrix.csv in directory: {matrix}")
        return candidate
    if not matrix.exists():
        raise FileNotFoundError(f"Matrix not found: {matrix}")
    return matrix


def _ensure_llm_model(config) -> None:
    if not config.llm_model:
        config.llm_model = DEFAULT_LLM_MODEL


def _run_llm_coding(config, records: list[dict[str, str]], *, use_cache: bool):
    _ensure_llm_model(config)
    cache = (
        ResponseCache(_shared_cache_root() / "openai") if use_cache else None
    )
    coder = LlmCoder(config, cache=cache)
    return coder.code_records(records), coder.stats


def run_harvest(
    config_path: Path,
    *,
    use_cache: bool = True,
    use_openalex: bool = True,
    llm_code: bool = False,
) -> Path:
    load_dotenv()
    config = load_config(config_path)
    run_dir = resolve_run_dir(config)
    run_dir.mkdir(parents=True, exist_ok=True)

    serpapi_cache = (
        ResponseCache(_shared_cache_root() / "serpapi") if use_cache else None
    )
    client = ScholarClient(cache=serpapi_cache)
    deduper = Deduper()
    rows = []

    for query in config.queries:
        print(f"Scholar query: {query.label}", flush=True)
        for paper in client.iter_query_results(query, config):
            if deduper.keep(paper):
                rows.append(paper)
    print(
        f"Scholar done: {len(rows)} unique rows "
        f"({client.api_calls} API calls, {client.cache_hits} cache hits, "
        f"{deduper.dropped} dupes dropped)",
        flush=True,
    )

    openalex_stats = None
    if use_openalex and rows:
        openalex_cache = (
            ResponseCache(_shared_cache_root() / "openalex") if use_cache else None
        )
        openalex = OpenAlexClient(cache=openalex_cache)
        rows = enrich_rows(rows, openalex)
        openalex_stats = openalex.stats

    llm_stats = None
    output_rows: list = rows
    if llm_code:
        records = []
        for row in rows:
            record = row.to_dict()
            for col in config.coding_columns:
                record.setdefault(col, "")
            records.append(record)
        output_rows, llm_stats = _run_llm_coding(
            config, records, use_cache=use_cache
        )

    # Ensure llm_model column exists in header when coding ran or llm configured.
    if llm_code:
        _ensure_llm_model(config)

    write_matrix(run_dir / "matrix.csv", output_rows, extra_columns=config.coding_columns)
    write_metadata(
        run_dir / "metadata.yaml",
        build_metadata(
            config,
            rows_written=len(output_rows),
            duplicates_dropped=deduper.dropped,
            api_calls=client.api_calls,
            cache_hits=client.cache_hits,
            run_dir=run_dir,
            openalex_stats=openalex_stats,
            llm_stats=llm_stats,
        ),
    )
    return run_dir


def run_code(
    config_path: Path,
    matrix_path: Path,
    *,
    use_cache: bool = True,
) -> Path:
    load_dotenv()
    config = load_config(config_path)
    _ensure_llm_model(config)
    matrix_path = _resolve_matrix_path(matrix_path)
    records = read_matrix(matrix_path)
    coded, llm_stats = _run_llm_coding(config, records, use_cache=use_cache)
    write_matrix(matrix_path, coded, extra_columns=config.coding_columns)

    meta_path = matrix_path.parent / "metadata.yaml"
    write_metadata(
        meta_path,
        build_metadata(
            config,
            rows_written=len(coded),
            duplicates_dropped=0,
            api_calls=0,
            cache_hits=0,
            run_dir=matrix_path.parent,
            openalex_stats=None,
            llm_stats=llm_stats,
        ),
    )
    return matrix_path


def run_enrich(
    config_path: Path,
    matrix_path: Path,
    *,
    use_cache: bool = True,
) -> Path:
    load_dotenv()
    config = load_config(config_path)
    matrix_path = _resolve_matrix_path(matrix_path)
    records = read_matrix(matrix_path)

    openalex_cache = (
        ResponseCache(_shared_cache_root() / "openalex") if use_cache else None
    )
    openalex = OpenAlexClient(cache=openalex_cache)
    if openalex.api_key is None:
        print(
            "warning: OPENALEX_API_KEY not set; anonymous quota is tiny and may 429.",
            flush=True,
        )
    enriched = enrich_records(records, openalex)
    write_matrix(matrix_path, enriched, extra_columns=config.coding_columns)

    meta_path = matrix_path.parent / "metadata.yaml"
    write_metadata(
        meta_path,
        build_metadata(
            config,
            rows_written=len(enriched),
            duplicates_dropped=0,
            api_calls=0,
            cache_hits=0,
            run_dir=matrix_path.parent,
            openalex_stats=openalex.stats,
            llm_stats=None,
        ),
    )
    print(
        "OpenAlex stats: "
        f"matched={openalex.stats.matched} unmatched={openalex.stats.unmatched} "
        f"abstracts={openalex.stats.abstracts_filled} keywords={openalex.stats.keywords_filled} "
        f"dois={openalex.stats.dois_filled} api_calls={openalex.stats.api_calls} "
        f"cache_hits={openalex.stats.cache_hits} errors={openalex.stats.errors}",
        flush=True,
    )
    return matrix_path


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Backward compatible: `litreview config.yaml` => harvest
    if argv and not argv[0].startswith("-") and argv[0] not in {
        "harvest",
        "code",
        "enrich",
    }:
        argv = ["harvest", *argv]

    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2

    try:
        if args.command == "harvest":
            run_dir = run_harvest(
                args.config,
                use_cache=not args.no_cache,
                use_openalex=not args.no_openalex,
                llm_code=args.llm_code,
            )
            print(f"Wrote matrix to {run_dir / 'matrix.csv'}")
            print(f"Wrote metadata to {run_dir / 'metadata.yaml'}")
            return 0

        if args.command == "enrich":
            matrix_path = run_enrich(
                args.config,
                args.matrix,
                use_cache=not args.no_cache,
            )
            print(f"Updated matrix at {matrix_path}")
            print(f"Wrote metadata to {matrix_path.parent / 'metadata.yaml'}")
            return 0

        matrix_path = run_code(
            args.config,
            args.matrix,
            use_cache=not args.no_cache,
        )
        print(f"Updated matrix at {matrix_path}")
        print(f"Wrote metadata to {matrix_path.parent / 'metadata.yaml'}")
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
