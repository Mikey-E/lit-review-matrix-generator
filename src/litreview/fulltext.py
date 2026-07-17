from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import requests
from pypdf import PdfReader

from litreview.models import extract_doi, normalize_title
from litreview.openalex import OpenAlexClient, USER_AGENT

MAX_CHARS = 100_000
MIN_QUOTE_LEN = 20
DOWNLOAD_TIMEOUT_S = 45


@dataclass
class FullTextResult:
    text: str = ""
    source_url: str = ""
    error: str = ""


def _unique(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        key = url.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def pdf_urls_from_work(work: dict[str, Any] | None) -> list[str]:
    if not work:
        return []
    urls: list[str] = []

    def _from_loc(loc: Any) -> None:
        if not isinstance(loc, dict):
            return
        pdf = str(loc.get("pdf_url") or "").strip()
        if pdf:
            urls.append(pdf)

    _from_loc(work.get("best_oa_location"))
    _from_loc(work.get("primary_location"))
    locations = work.get("locations")
    if isinstance(locations, list):
        for loc in locations:
            _from_loc(loc)

    ids = work.get("ids") if isinstance(work.get("ids"), dict) else {}
    arxiv = str(ids.get("arxiv") or "").strip()
    if arxiv:
        # OpenAlex stores like https://arxiv.org/abs/...
        if "/abs/" in arxiv:
            urls.append(arxiv.replace("/abs/", "/pdf/") + ".pdf")
        elif arxiv.startswith("http"):
            urls.append(arxiv)
        else:
            urls.append(f"https://arxiv.org/pdf/{arxiv}.pdf")

    return _unique(urls)


def normalize_for_quote_match(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def quote_supported_by_text(quote: str, full_text: str) -> bool:
    q = normalize_for_quote_match(quote)
    if len(q) < MIN_QUOTE_LEN:
        return False
    body = normalize_for_quote_match(full_text)
    if q in body:
        return True
    # Tolerate minor whitespace/punctuation drift by checking a shortened core.
    core = re.sub(r"[^\w\s]", "", q)
    core = re.sub(r"\s+", " ", core).strip()
    if len(core) < MIN_QUOTE_LEN:
        return False
    body_core = re.sub(r"[^\w\s]", "", body)
    body_core = re.sub(r"\s+", " ", body_core).strip()
    return core in body_core


def extract_pdf_text(data: bytes, *, max_chars: int = MAX_CHARS) -> str:
    reader = PdfReader(io.BytesIO(data))
    parts: list[str] = []
    total = 0
    for page in reader.pages:
        try:
            chunk = page.extract_text() or ""
        except Exception:  # noqa: BLE001 - keep going on bad pages
            continue
        chunk = chunk.strip()
        if not chunk:
            continue
        parts.append(chunk)
        total += len(chunk)
        if total >= max_chars:
            break
    text = "\n\n".join(parts).strip()
    if len(text) > max_chars:
        text = text[:max_chars]
    return text


def download_pdf_text(url: str, *, session: requests.Session | None = None) -> FullTextResult:
    sess = session or requests.Session()
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/pdf,*/*",
    }
    try:
        response = sess.get(url, headers=headers, timeout=DOWNLOAD_TIMEOUT_S, allow_redirects=True)
    except requests.RequestException as exc:
        return FullTextResult(error=f"download_error:{type(exc).__name__}")
    if response.status_code in {401, 403, 451}:
        return FullTextResult(error=f"paywall_or_forbidden:{response.status_code}")
    if response.status_code == 404:
        return FullTextResult(error="pdf_not_found")
    if response.status_code >= 400:
        return FullTextResult(error=f"http_{response.status_code}")

    content_type = (response.headers.get("Content-Type") or "").casefold()
    data = response.content or b""
    looks_pdf = (
        "pdf" in content_type
        or data[:5] == b"%PDF-"
        or urlparse(response.url).path.casefold().endswith(".pdf")
    )
    if not looks_pdf:
        return FullTextResult(error="not_pdf")
    if len(data) < 1000:
        return FullTextResult(error="pdf_too_small")
    try:
        text = extract_pdf_text(data)
    except Exception as exc:  # noqa: BLE001
        return FullTextResult(error=f"extract_error:{type(exc).__name__}")
    if len(text.strip()) < 200:
        return FullTextResult(error="extract_too_short")
    return FullTextResult(text=text, source_url=str(response.url))


def resolve_openalex_work(
    row: dict[str, str], client: OpenAlexClient
) -> tuple[dict[str, Any] | None, str]:
    doi = extract_doi(row.get("doi") or "")
    if doi:
        try:
            work = client.fetch_by_doi(doi)
            if work:
                return work, ""
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "?"
            return None, f"openalex_http_{status}"
        except requests.RequestException as exc:
            return None, f"openalex_request:{type(exc).__name__}"
    title = (row.get("title") or "").strip()
    if not title:
        return None, "no_doi_or_title"
    try:
        work = client.fetch_by_title(title)
        if work and normalize_title(str(work.get("display_name") or "")) == normalize_title(
            title
        ):
            return work, ""
        if work:
            return work, ""
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        return None, f"openalex_http_{status}"
    except requests.RequestException as exc:
        return None, f"openalex_request:{type(exc).__name__}"
    return None, "openalex_unmatched"


def fetch_fulltext_for_row(
    row: dict[str, str],
    openalex: OpenAlexClient,
    *,
    session: requests.Session | None = None,
) -> FullTextResult:
    work, err = resolve_openalex_work(row, openalex)
    if err and work is None:
        return FullTextResult(error=err)
    urls = pdf_urls_from_work(work)
    if not urls:
        return FullTextResult(error="no_oa_pdf")
    sess = session or requests.Session()
    last = FullTextResult(error="no_oa_pdf")
    for url in urls[:5]:
        result = download_pdf_text(url, session=sess)
        if result.text:
            return result
        last = result
    return last
