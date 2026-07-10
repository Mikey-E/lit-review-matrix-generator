from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import requests

from litreview.cache import ResponseCache
from litreview.models import PaperRow, extract_doi, normalize_title

OPENALEX_BASE = "https://api.openalex.org"
USER_AGENT = "lit-review-matrix-generator (https://github.com/Mikey-E/lit-review-matrix-generator)"


@dataclass
class EnrichmentStats:
    looked_up: int = 0
    matched: int = 0
    abstracts_filled: int = 0
    keywords_filled: int = 0
    dois_filled: int = 0
    cache_hits: int = 0
    api_calls: int = 0
    unmatched: int = 0


def reconstruct_abstract(inverted: dict[str, Any] | None) -> str:
    """Rebuild plain-text abstract from OpenAlex abstract_inverted_index."""
    if not inverted:
        return ""
    pairs: list[tuple[int, str]] = []
    for word, positions in inverted.items():
        if not isinstance(positions, list):
            continue
        for pos in positions:
            try:
                pairs.append((int(pos), str(word)))
            except (TypeError, ValueError):
                continue
    if not pairs:
        return ""
    pairs.sort(key=lambda item: item[0])
    return " ".join(word for _, word in pairs)


def keywords_from_work(work: dict[str, Any]) -> str:
    names: list[str] = []

    raw_keywords = work.get("keywords")
    if isinstance(raw_keywords, list):
        for item in raw_keywords:
            if isinstance(item, dict):
                name = item.get("display_name") or item.get("keyword")
            else:
                name = item
            if name:
                names.append(str(name).strip())

    if not names:
        topics = work.get("topics")
        if isinstance(topics, list):
            for topic in topics:
                if isinstance(topic, dict) and topic.get("display_name"):
                    names.append(str(topic["display_name"]).strip())

    if not names:
        concepts = work.get("concepts")
        if isinstance(concepts, list):
            # Prefer higher-score concepts when present.
            scored: list[tuple[float, str]] = []
            for concept in concepts:
                if not isinstance(concept, dict) or not concept.get("display_name"):
                    continue
                score = concept.get("score")
                try:
                    scored.append((float(score), str(concept["display_name"]).strip()))
                except (TypeError, ValueError):
                    scored.append((0.0, str(concept["display_name"]).strip()))
            scored.sort(key=lambda item: item[0], reverse=True)
            names = [name for _, name in scored[:8]]

    # Preserve order, drop empties/dupes.
    seen: set[str] = set()
    unique: list[str] = []
    for name in names:
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        unique.append(name)
    return "; ".join(unique)


def doi_from_work(work: dict[str, Any]) -> str:
    return extract_doi(str(work.get("doi") or ""), str(work.get("ids", {}).get("doi") or ""))


def _snippet_looks_truncated(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    if stripped.endswith("…") or stripped.endswith("..."):
        return True
    # Scholar snippets are typically short; prefer OpenAlex when clearly longer.
    return len(stripped) < 400


def apply_openalex_fields(row: PaperRow, work: dict[str, Any], stats: EnrichmentStats) -> PaperRow:
    abstract = reconstruct_abstract(work.get("abstract_inverted_index"))
    keywords = keywords_from_work(work)
    doi = doi_from_work(work)

    new_abstract = row.abstract
    new_keywords = row.keywords
    new_doi = row.doi

    if abstract and _snippet_looks_truncated(row.abstract):
        if abstract != row.abstract:
            new_abstract = abstract
            stats.abstracts_filled += 1
    elif abstract and len(abstract) > len(row.abstract.strip()):
        new_abstract = abstract
        stats.abstracts_filled += 1

    if keywords and not row.keywords.strip():
        new_keywords = keywords
        stats.keywords_filled += 1

    if doi and not row.doi.strip():
        new_doi = doi
        stats.dois_filled += 1

    return PaperRow(
        title=row.title,
        year=row.year,
        venue=row.venue,
        abstract=new_abstract,
        citation_count=row.citation_count,
        paper_url=row.paper_url,
        query=row.query,
        scholar_rank=row.scholar_rank,
        scholar_page=row.scholar_page,
        doi=new_doi,
        keywords=new_keywords,
    )


class OpenAlexClient:
    def __init__(
        self,
        *,
        mailto: str | None = None,
        cache: ResponseCache | None = None,
        min_interval_s: float = 0.1,
        session: requests.Session | None = None,
    ) -> None:
        self.mailto = (mailto or os.environ.get("OPENALEX_MAILTO", "")).strip() or None
        self.cache = cache
        self.min_interval_s = min_interval_s
        self.session = session or requests.Session()
        self._last_request_at = 0.0
        self.stats = EnrichmentStats()

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_interval_s:
            time.sleep(self.min_interval_s - elapsed)

    def _get_json(self, path_or_url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = dict(params or {})
        if self.mailto:
            params.setdefault("mailto", self.mailto)

        if path_or_url.startswith("http"):
            url = path_or_url
        else:
            url = f"{OPENALEX_BASE}{path_or_url}"

        cache_params = {"url": url, **params}
        if self.cache is not None:
            cached = self.cache.get(cache_params)
            if cached is not None:
                self.stats.cache_hits += 1
                return cached

        self._throttle()
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        response = self.session.get(url, params=params, headers=headers, timeout=30)
        self._last_request_at = time.monotonic()
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Unexpected OpenAlex response type.")
        self.stats.api_calls += 1
        if self.cache is not None:
            self.cache.put(cache_params, payload)
        return payload

    def fetch_by_doi(self, doi: str) -> dict[str, Any] | None:
        doi = doi.strip()
        if not doi:
            return None
        # OpenAlex work IDs for DOIs use the canonical https://doi.org/... form.
        work_id = f"https://doi.org/{doi}"
        try:
            payload = self._get_json(f"/works/{quote(work_id, safe=':/')}")
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return None
            raise
        return payload if payload.get("id") else None

    def fetch_by_title(self, title: str) -> dict[str, Any] | None:
        title = title.strip()
        if not title:
            return None
        payload = self._get_json(
            "/works",
            {
                "search": title,
                "per_page": 5,
            },
        )
        results = payload.get("results") or []
        target = normalize_title(title)
        best: dict[str, Any] | None = None
        for work in results:
            if not isinstance(work, dict):
                continue
            candidate = normalize_title(str(work.get("display_name") or work.get("title") or ""))
            if candidate == target:
                return work
            if best is None and candidate and (
                candidate in target or target in candidate
            ):
                best = work
        return best

    def lookup_work(self, row: PaperRow) -> dict[str, Any] | None:
        self.stats.looked_up += 1
        if row.doi.strip():
            work = self.fetch_by_doi(row.doi)
            if work is not None:
                self.stats.matched += 1
                return work
        work = self.fetch_by_title(row.title)
        if work is not None:
            self.stats.matched += 1
            return work
        self.stats.unmatched += 1
        return None


def enrich_rows(rows: list[PaperRow], client: OpenAlexClient) -> list[PaperRow]:
    enriched: list[PaperRow] = []
    for row in rows:
        work = client.lookup_work(row)
        if work is None:
            enriched.append(row)
            continue
        enriched.append(apply_openalex_fields(row, work, client.stats))
    return enriched
