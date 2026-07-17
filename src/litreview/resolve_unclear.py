from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from litreview.config import FacetSpec, StudyConfig
from litreview.fulltext import fetch_fulltext_for_row, quote_supported_by_text
from litreview.llm_code import LlmCoder, _paper_context, paper_cache_key
from litreview.openalex import OpenAlexClient


@dataclass
class ResolveUnclearStats:
    rows_considered: int = 0
    rows_with_fulltext: int = 0
    rows_skipped_no_fulltext: int = 0
    unclear_cells: int = 0
    resolved: int = 0
    left_unclear: int = 0
    skipped_bad_evidence: int = 0
    facet_api_calls: int = 0
    facet_cache_hits: int = 0
    fulltext_errors: dict[str, int] = field(default_factory=dict)


class UnclearFacetResolver:
    """Re-code only unclear facets using OA full text + quote evidence gate."""

    def __init__(
        self,
        config: StudyConfig,
        *,
        coder: LlmCoder,
        openalex: OpenAlexClient,
    ) -> None:
        if not config.facets:
            raise ValueError("resolve-unclear requires facets in the study YAML.")
        self.config = config
        self.coder = coder
        self.openalex = openalex
        self.stats = ResolveUnclearStats()

    def unclear_facets(self, row: dict[str, str]) -> list[FacetSpec]:
        out: list[FacetSpec] = []
        for facet in self.config.facets:
            if (row.get(facet.name) or "").strip().casefold() == "unclear":
                out.append(facet)
        return out

    def resolve_facet_from_fulltext(
        self, row: dict[str, str], facet: FacetSpec, full_text: str
    ) -> str | None:
        """Return a non-unclear label if evidence-backed; else None to leave cell."""
        allowed = list(facet.values)
        schema = {
            "type": "object",
            "properties": {
                "value": {"type": "string", "enum": allowed},
                "evidence_quote": {"type": "string"},
                "rationale": {"type": "string"},
            },
            "required": ["value", "evidence_quote", "rationale"],
            "additionalProperties": False,
        }
        system = (
            "You are coding literature-review facets from FULL PAPER TEXT. "
            f"Assign exactly one value for facet '{facet.name}' from the allowed "
            "enumeration. Use 'unclear' unless the paper text explicitly supports "
            "another value. If you choose a non-unclear value, evidence_quote MUST be "
            "a verbatim excerpt from the provided paper text (at least ~20 characters) "
            "that supports the label. Do not guess or rely on outside knowledge. "
            "Return only the JSON object."
        )
        # Keep prompt bounded; full_text already truncated upstream.
        user = (
            f"Study title: {self.config.title}\n\n"
            f"Facet: {facet.name}\n"
            f"Allowed values: {', '.join(allowed)}\n\n"
            f"Bibliographic context:\n{_paper_context(row)}\n\n"
            f"Paper full text (may be truncated):\n{full_text}\n\n"
            f"Current value is 'unclear'. Resolve '{facet.name}' only with explicit "
            "textual support; otherwise return unclear."
        )
        cache_params = {
            "provider": "openai",
            "model": self.coder.model,
            "stage": f"resolve_unclear:{facet.name}",
            "paper": paper_cache_key(row),
            "facet": facet.name,
            "allowed": allowed,
            "text_fingerprint": str(len(full_text)),
        }
        before_api = self.coder.stats.facet_api_calls
        before_cache = self.coder.stats.facet_cache_hits
        record = self.coder._complete_json(
            cache_params=cache_params,
            system=system,
            user=user,
            schema_name=f"resolve_{facet.name}",
            schema=schema,
            stage=f"resolve_unclear:{facet.name}",
        )
        self.stats.facet_api_calls += self.coder.stats.facet_api_calls - before_api
        self.stats.facet_cache_hits += self.coder.stats.facet_cache_hits - before_cache

        response: dict[str, Any] = record.get("response") or {}
        value = str(response.get("value") or "").strip()
        quote = str(response.get("evidence_quote") or "").strip()
        if value not in allowed:
            self.stats.skipped_bad_evidence += 1
            return None
        if value.casefold() == "unclear":
            self.stats.left_unclear += 1
            return None
        if not quote_supported_by_text(quote, full_text):
            self.stats.skipped_bad_evidence += 1
            return None
        self.stats.resolved += 1
        return value

    def resolve_row(self, row: dict[str, str]) -> dict[str, str]:
        out = dict(row)
        unclear = self.unclear_facets(out)
        if not unclear:
            return out
        self.stats.rows_considered += 1
        self.stats.unclear_cells += len(unclear)

        full = fetch_fulltext_for_row(out, self.openalex)
        if not full.text:
            self.stats.rows_skipped_no_fulltext += 1
            key = full.error or "unknown"
            self.stats.fulltext_errors[key] = self.stats.fulltext_errors.get(key, 0) + 1
            return out

        self.stats.rows_with_fulltext += 1
        out["llm_model"] = out.get("llm_model") or self.coder.model
        for facet in unclear:
            resolved = self.resolve_facet_from_fulltext(out, facet, full.text)
            if resolved:
                out[facet.name] = resolved
        return out

    def resolve_records(
        self,
        rows: list[dict[str, str]],
        *,
        only_include: bool = True,
        progress_every: int = 5,
    ) -> list[dict[str, str]]:
        coded = [dict(r) for r in rows]
        targets: list[int] = []
        for i, row in enumerate(rows):
            label = (
                row.get("include/exclude") or row.get("screen") or ""
            ).strip().casefold()
            if only_include and label != "include":
                continue
            if self.unclear_facets(row):
                targets.append(i)

        total = len(targets)
        print(
            f"resolve-unclear: {total} include rows with unclear facets...",
            flush=True,
        )
        done = 0
        for i in targets:
            coded[i] = self.resolve_row(rows[i])
            done += 1
            if done == 1 or done % progress_every == 0 or done == total:
                print(
                    f"  row {done}/{total} "
                    f"fulltext={self.stats.rows_with_fulltext} "
                    f"resolved={self.stats.resolved} "
                    f"left_unclear={self.stats.left_unclear} "
                    f"bad_evidence={self.stats.skipped_bad_evidence} "
                    f"no_pdf={self.stats.rows_skipped_no_fulltext} "
                    f"api={self.stats.facet_api_calls} cache={self.stats.facet_cache_hits}",
                    flush=True,
                )
        return coded
