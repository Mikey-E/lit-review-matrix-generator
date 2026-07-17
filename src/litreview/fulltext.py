from __future__ import annotations

import io
import os
import re
from dataclasses import dataclass
from typing import Any

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
        landing = str(loc.get("landing_page_url") or "").strip()
        if landing.casefold().endswith(".pdf"):
            urls.append(landing)

    _from_loc(work.get("best_oa_location"))
    _from_loc(work.get("primary_location"))
    locations = work.get("locations")
    if isinstance(locations, list):
        for loc in locations:
            _from_loc(loc)

    oa = work.get("open_access")
    if isinstance(oa, dict):
        oa_url = str(oa.get("oa_url") or "").strip()
        if oa_url:
            urls.append(oa_url)

    ids = work.get("ids") if isinstance(work.get("ids"), dict) else {}
    arxiv = str(ids.get("arxiv") or "").strip()
    if arxiv:
        if "/abs/" in arxiv:
            urls.append(arxiv.replace("/abs/", "/pdf/"))
        elif arxiv.startswith("http"):
            urls.append(arxiv)
        else:
            urls.append(f"https://arxiv.org/pdf/{arxiv}")

    return _unique(urls)


def unpaywall_pdf_urls(
    doi: str,
    *,
    email: str | None = None,
    session: requests.Session | None = None,
) -> list[str]:
    doi = extract_doi(doi)
    if not doi:
        return []
    mail = (
        email
        or os.environ.get("UNPAYWALL_EMAIL")
        or os.environ.get("OPENALEX_MAILTO")
        or ""
    ).strip()
    if not mail or "@" not in mail:
        return []
    sess = session or requests.Session()
    url = f"https://api.unpaywall.org/v2/{doi}"
    try:
        response = sess.get(
            url,
            params={"email": mail},
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=30,
        )
    except requests.RequestException:
        return []
    if response.status_code >= 400:
        return []
    try:
        payload = response.json()
    except ValueError:
        return []
    if not isinstance(payload, dict):
        return []
    urls: list[str] = []
    best = payload.get("best_oa_location")
    if isinstance(best, dict) and best.get("url_for_pdf"):
        urls.append(str(best["url_for_pdf"]))
    for loc in payload.get("oa_locations") or []:
        if isinstance(loc, dict) and loc.get("url_for_pdf"):
            urls.append(str(loc["url_for_pdf"]))
    return _unique(urls)


def semantic_scholar_pdf_urls(
    doi: str, *, session: requests.Session | None = None
) -> list[str]:
    doi = extract_doi(doi)
    if not doi:
        return []
    sess = session or requests.Session()
    url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}"
    try:
        response = sess.get(
            url,
            params={"fields": "openAccessPdf"},
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=30,
        )
    except requests.RequestException:
        return []
    if response.status_code >= 400:
        return []
    try:
        payload = response.json()
    except ValueError:
        return []
    if not isinstance(payload, dict):
        return []
    oa = payload.get("openAccessPdf")
    if isinstance(oa, dict) and oa.get("url"):
        return [str(oa["url"]).strip()]
    return []


def normalize_for_quote_match(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def quote_supported_by_text(quote: str, full_text: str) -> bool:
    q = normalize_for_quote_match(quote)
    if len(q) < MIN_QUOTE_LEN:
        return False
    body = normalize_for_quote_match(full_text)
    if q in body:
        return True
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
        response = sess.get(
            url, headers=headers, timeout=DOWNLOAD_TIMEOUT_S, allow_redirects=True
        )
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
    if data.lstrip()[:1] == b"<" or "html" in content_type:
        return FullTextResult(error="not_pdf")
    if data[:5] != b"%PDF-":
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
    sess = session or requests.Session()
    urls: list[str] = []

    paper_url = str(row.get("paper_url") or "").strip()
    if paper_url.casefold().endswith(".pdf") or "arxiv.org/pdf/" in paper_url.casefold():
        urls.append(paper_url)

    work, err = resolve_openalex_work(row, openalex)
    if work is not None:
        urls.extend(pdf_urls_from_work(work))

    doi = extract_doi(row.get("doi") or "")
    if doi:
        urls.extend(unpaywall_pdf_urls(doi, session=sess))
        urls.extend(semantic_scholar_pdf_urls(doi, session=sess))

    urls = _unique(urls)
    if not urls:
        return FullTextResult(error=err or "no_oa_pdf")

    last = FullTextResult(error="no_oa_pdf")
    for url in urls[:8]:
        result = download_pdf_text(url, session=sess)
        if result.text:
            return result
        last = result
    return last
