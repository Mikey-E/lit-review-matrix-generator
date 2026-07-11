from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

from litreview.cache import ResponseCache
from litreview.config import SCREEN_COLUMN, FacetSpec, StudyConfig
from litreview.models import normalize_title

SCREEN_STAGE = "screen"
DEFAULT_LLM_MODEL = "gpt-5.6-luna"


def _supports_temperature(model: str) -> bool:
    # GPT-5.6 family currently only accepts the default temperature.
    return not model.startswith("gpt-5.6")


@dataclass
class LlmCodingStats:
    screen_api_calls: int = 0
    screen_cache_hits: int = 0
    facet_api_calls: int = 0
    facet_cache_hits: int = 0
    screened: int = 0
    screen_skipped_existing: int = 0
    facet_labels: int = 0
    skipped_exclude_for_facets: int = 0
    facet_skipped_existing: int = 0


def paper_cache_key(row: dict[str, str]) -> str:
    doi = (row.get("doi") or "").strip().casefold()
    if doi:
        return f"doi:{doi}"
    title = normalize_title(row.get("title") or "")
    year = (row.get("year") or "").strip()
    return f"title:{title}|year:{year}"


def _paper_context(row: dict[str, str]) -> str:
    return "\n".join(
        [
            f"Title: {row.get('title') or ''}",
            f"Year: {row.get('year') or ''}",
            f"Venue: {row.get('venue') or ''}",
            f"Abstract: {row.get('abstract') or ''}",
        ]
    )


class LlmCoder:
    """Stage A screen + Stage B per-facet coding via OpenAI (temp=0, strict JSON)."""

    def __init__(
        self,
        config: StudyConfig,
        *,
        api_key: str | None = None,
        cache: ResponseCache | None = None,
        client: OpenAI | None = None,
    ) -> None:
        if not config.screen_values:
            raise ValueError("LLM coding requires screen.values in the study YAML.")
        self.config = config
        self.model = config.llm_model or DEFAULT_LLM_MODEL
        key = (api_key or os.environ.get("OPENAI_API_KEY", "")).strip()
        if client is None and not key:
            raise RuntimeError(
                "Missing OpenAI key. Set OPENAI_API_KEY in the environment "
                "or a .env file (see .env.example)."
            )
        self.client = client or OpenAI(api_key=key)
        self.cache = cache
        self.stats = LlmCodingStats()

    def _complete_json(
        self,
        *,
        cache_params: dict[str, Any],
        system: str,
        user: str,
        schema_name: str,
        schema: dict[str, Any],
        stage: str,
    ) -> dict[str, Any]:
        if self.cache is not None:
            cached = self.cache.get(cache_params)
            if cached is not None:
                if stage == SCREEN_STAGE:
                    self.stats.screen_cache_hits += 1
                else:
                    self.stats.facet_cache_hits += 1
                return cached

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                },
            },
        }
        if _supports_temperature(self.model):
            kwargs["temperature"] = 0

        response = self.client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content or "{}"
        payload = json.loads(content)
        if not isinstance(payload, dict):
            raise RuntimeError("OpenAI returned non-object JSON.")

        if stage == SCREEN_STAGE:
            self.stats.screen_api_calls += 1
        else:
            self.stats.facet_api_calls += 1

        # Persist rationale + raw decision for audit; CSV only gets the label.
        record = {
            "model": self.model,
            "stage": stage,
            "response": payload,
        }
        if self.cache is not None:
            self.cache.put(cache_params, record)
        return record

    def screen_row(self, row: dict[str, str]) -> str:
        allowed = list(self.config.screen_values)
        schema = {
            "type": "object",
            "properties": {
                "screen": {"type": "string", "enum": allowed},
                "rationale": {"type": "string"},
            },
            "required": ["screen", "rationale"],
            "additionalProperties": False,
        }
        system = (
            "You are assisting with a systematic literature review. "
            "Apply the inclusion and exclusion criteria strictly to the paper "
            "metadata provided. Prefer 'maybe' when evidence in the title/abstract "
            "is insufficient to decide. Return only the JSON object."
        )
        user = (
            f"Study title: {self.config.title}\n\n"
            f"Inclusion criteria:\n{self.config.inclusion_criteria}\n\n"
            f"Exclusion criteria:\n{self.config.exclusion_criteria}\n\n"
            f"Allowed screen values: {', '.join(allowed)}\n\n"
            f"Paper:\n{_paper_context(row)}\n\n"
            "Decide screen = include, exclude, or maybe."
        )
        cache_params = {
            "provider": "openai",
            "model": self.model,
            "stage": SCREEN_STAGE,
            "paper": paper_cache_key(row),
            "inclusion": self.config.inclusion_criteria,
            "exclusion": self.config.exclusion_criteria,
            "allowed": allowed,
        }
        record = self._complete_json(
            cache_params=cache_params,
            system=system,
            user=user,
            schema_name="screen_decision",
            schema=schema,
            stage=SCREEN_STAGE,
        )
        value = str(record["response"].get("screen") or "").strip()
        if value not in allowed:
            raise RuntimeError(f"Invalid screen value from model: {value!r}")
        return value

    def code_facet(self, row: dict[str, str], facet: FacetSpec) -> str:
        allowed = list(facet.values)
        schema = {
            "type": "object",
            "properties": {
                "value": {"type": "string", "enum": allowed},
                "rationale": {"type": "string"},
            },
            "required": ["value", "rationale"],
            "additionalProperties": False,
        }
        system = (
            "You are coding literature-review facets from title/abstract metadata. "
            f"Assign exactly one value for the facet '{facet.name}' from the allowed "
            "enumeration. Prefer 'unclear' when the abstract does not support a "
            "confident label. Return only the JSON object."
        )
        user = (
            f"Study title: {self.config.title}\n\n"
            f"Facet: {facet.name}\n"
            f"Allowed values: {', '.join(allowed)}\n\n"
            f"Paper:\n{_paper_context(row)}\n\n"
            f"Choose the best single value for '{facet.name}'."
        )
        cache_params = {
            "provider": "openai",
            "model": self.model,
            "stage": f"facet:{facet.name}",
            "paper": paper_cache_key(row),
            "facet": facet.name,
            "allowed": allowed,
        }
        record = self._complete_json(
            cache_params=cache_params,
            system=system,
            user=user,
            schema_name=f"facet_{facet.name}",
            schema=schema,
            stage=f"facet:{facet.name}",
        )
        value = str(record["response"].get("value") or "").strip()
        if value not in allowed:
            raise RuntimeError(
                f"Invalid {facet.name} value from model: {value!r}"
            )
        return value

    def _existing_screen(self, row: dict[str, str]) -> str:
        # Prefer the current column; accept legacy "screen" on older matrices.
        value = (row.get(SCREEN_COLUMN) or row.get("screen") or "").strip()
        allowed = {v.casefold(): v for v in self.config.screen_values}
        key = value.casefold()
        if key in allowed:
            return allowed[key]
        return ""

    def code_records(self, rows: list[dict[str, str]]) -> list[dict[str, str]]:
        """Stage A for rows missing include/exclude, then Stage B for include+maybe."""
        coded: list[dict[str, str]] = []
        total = len(rows)
        need_screen = sum(1 for r in rows if not self._existing_screen(r))
        print(
            f"LLM Stage A ({SCREEN_COLUMN}): {need_screen}/{total} rows need coding...",
            flush=True,
        )
        for i, row in enumerate(rows, start=1):
            out = dict(row)
            existing = self._existing_screen(out)
            if existing:
                out[SCREEN_COLUMN] = existing
                self.stats.screen_skipped_existing += 1
            else:
                out["llm_model"] = self.model
                out[SCREEN_COLUMN] = self.screen_row(out)
                self.stats.screened += 1
            coded.append(out)
            if i == 1 or i % 25 == 0 or i == total:
                print(
                    f"  {SCREEN_COLUMN} {i}/{total} "
                    f"(new={self.stats.screened} "
                    f"kept={self.stats.screen_skipped_existing} "
                    f"api={self.stats.screen_api_calls} "
                    f"cache={self.stats.screen_cache_hits})",
                    flush=True,
                )

        stage_b_values = {"include", "maybe"}
        to_code = [
            r for r in coded
            if (r.get(SCREEN_COLUMN) or "").strip().casefold() in stage_b_values
            and any(not (r.get(f.name) or "").strip() for f in self.config.facets)
        ]
        print(
            f"LLM Stage B (facets): {len(to_code)} include/maybe rows "
            f"need facet fills x {len(self.config.facets)} facets...",
            flush=True,
        )
        papers_faceted = 0
        for i, row in enumerate(coded, start=1):
            screen = (row.get(SCREEN_COLUMN) or "").strip().casefold()
            if screen not in stage_b_values:
                self.stats.skipped_exclude_for_facets += 1
                for facet in self.config.facets:
                    row.setdefault(facet.name, "")
                continue
            filled_any = False
            for facet in self.config.facets:
                if (row.get(facet.name) or "").strip():
                    self.stats.facet_skipped_existing += 1
                    continue
                row["llm_model"] = row.get("llm_model") or self.model
                row[facet.name] = self.code_facet(row, facet)
                self.stats.facet_labels += 1
                filled_any = True
            if filled_any:
                papers_faceted += 1
                if (
                    papers_faceted == 1
                    or papers_faceted % 10 == 0
                    or papers_faceted == len(to_code)
                ):
                    print(
                        f"  facets on {papers_faceted}/{len(to_code)} papers "
                        f"(api={self.stats.facet_api_calls} "
                        f"cache={self.stats.facet_cache_hits})",
                        flush=True,
                    )
        return coded
