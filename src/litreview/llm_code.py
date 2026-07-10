from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

from litreview.cache import ResponseCache
from litreview.config import FacetSpec, StudyConfig
from litreview.models import normalize_title

SCREEN_STAGE = "screen"
DEFAULT_LLM_MODEL = "gpt-4o-mini"


@dataclass
class LlmCodingStats:
    screen_api_calls: int = 0
    screen_cache_hits: int = 0
    facet_api_calls: int = 0
    facet_cache_hits: int = 0
    screened: int = 0
    facet_labels: int = 0
    skipped_exclude_for_facets: int = 0


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

        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                },
            },
        )
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

    def code_records(self, rows: list[dict[str, str]]) -> list[dict[str, str]]:
        """Stage A all rows, then Stage B per facet for include+maybe only."""
        coded: list[dict[str, str]] = []
        for row in rows:
            out = dict(row)
            out["llm_model"] = self.model
            out["screen"] = self.screen_row(out)
            self.stats.screened += 1
            coded.append(out)

        stage_b_values = {"include", "maybe"}
        for row in coded:
            screen = (row.get("screen") or "").strip().casefold()
            if screen not in stage_b_values:
                self.stats.skipped_exclude_for_facets += 1
                for facet in self.config.facets:
                    row.setdefault(facet.name, "")
                continue
            for facet in self.config.facets:
                row[facet.name] = self.code_facet(row, facet)
                self.stats.facet_labels += 1
        return coded
