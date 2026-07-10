from __future__ import annotations

import os
from typing import Any, Iterator

from serpapi import GoogleSearch

from litreview.cache import ResponseCache
from litreview.config import QuerySpec, StudyConfig
from litreview.models import PaperRow, paper_from_organic

# SerpAPI Google Scholar default page size is 10; max documented is 20.
PAGE_SIZE = 10


class ScholarClient:
    def __init__(
        self,
        api_key: str | None = None,
        cache: ResponseCache | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("SERPAPI_API_KEY", "").strip()
        if not self.api_key:
            raise RuntimeError(
                "Missing SerpAPI key. Set SERPAPI_API_KEY in the environment "
                "or a .env file (see .env.example)."
            )
        self.cache = cache
        self.api_calls = 0
        self.cache_hits = 0

    def _fetch_page(self, params: dict[str, Any]) -> dict[str, Any]:
        # GoogleSearch may mutate its params dict; keep a stable copy for cache keys.
        cache_params = dict(params)
        if self.cache is not None:
            cached = self.cache.get(cache_params)
            if cached is not None:
                self.cache_hits += 1
                return cached

        search = GoogleSearch(dict(params))
        payload = search.get_dict()
        if not isinstance(payload, dict):
            raise RuntimeError("Unexpected SerpAPI response type.")
        if payload.get("error"):
            raise RuntimeError(f"SerpAPI error: {payload['error']}")

        self.api_calls += 1
        if self.cache is not None:
            self.cache.put(cache_params, payload)
        return payload

    def iter_query_results(
        self,
        query: QuerySpec,
        config: StudyConfig,
    ) -> Iterator[PaperRow]:
        collected = 0
        page = 0
        while True:
            if config.max_pages is not None and page >= config.max_pages:
                break
            if config.max_results is not None and collected >= config.max_results:
                break

            remaining = None
            if config.max_results is not None:
                remaining = config.max_results - collected
            num = PAGE_SIZE if remaining is None else min(PAGE_SIZE, remaining)

            params: dict[str, Any] = {
                "engine": "google_scholar",
                "q": query.q,
                "api_key": self.api_key,
                "hl": "en",
                "start": page * PAGE_SIZE,
                "num": num,
            }
            if config.year_from is not None:
                params["as_ylo"] = config.year_from
            if config.year_to is not None:
                params["as_yhi"] = config.year_to

            payload = self._fetch_page(params)
            organic = payload.get("organic_results") or []
            if not organic:
                break

            for item in organic:
                if not isinstance(item, dict):
                    continue
                yield paper_from_organic(item, query.label)
                collected += 1
                if config.max_results is not None and collected >= config.max_results:
                    return

            if len(organic) < num:
                break
            page += 1
