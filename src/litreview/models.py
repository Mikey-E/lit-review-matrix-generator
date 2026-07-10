from __future__ import annotations

import re
from dataclasses import asdict, dataclass


DOI_RE = re.compile(
    r"\b(10\.\d{4,9}/[-._;()/:A-Z0-9]+)\b",
    re.IGNORECASE,
)
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


@dataclass
class PaperRow:
    title: str
    year: str
    venue: str
    abstract: str
    citation_count: str
    paper_url: str
    query: str
    scholar_rank: str
    scholar_page: str
    doi: str
    keywords: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


CSV_COLUMNS = [
    "title",
    "year",
    "venue",
    "abstract",
    "citation_count",
    "paper_url",
    "query",
    "scholar_rank",
    "scholar_page",
    "doi",
    "keywords",
]


def normalize_title(title: str) -> str:
    cleaned = title.casefold().strip()
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def extract_doi(*candidates: str | None) -> str:
    for text in candidates:
        if not text:
            continue
        match = DOI_RE.search(text)
        if match:
            return match.group(1).rstrip(").,;")
    return ""


def parse_publication_summary(summary: str | None) -> tuple[str, str]:
    """Return (year, venue) from Scholar's publication_info.summary string."""
    if not summary:
        return "", ""

    # Typical: "Authors - Venue, 2020 - domain.com" or "Authors - 2020 - domain.com"
    parts = [p.strip() for p in summary.split(" - ")]
    if len(parts) < 2:
        year_match = YEAR_RE.search(summary)
        return (year_match.group(0) if year_match else "", "")

    middle = parts[1]
    year_match = YEAR_RE.search(middle)
    year = year_match.group(0) if year_match else ""

    venue = middle
    if year:
        venue = re.sub(rf",?\s*{re.escape(year)}\s*$", "", middle).strip(" ,")
    # If the middle segment was only a year, there is no venue.
    if venue == year:
        venue = ""
    return year, venue


def paper_from_organic(
    result: dict,
    query_label: str,
    *,
    scholar_rank: int,
    scholar_page: int,
) -> PaperRow:
    title = str(result.get("title") or "").strip()
    link = str(result.get("link") or "").strip()
    snippet = str(result.get("snippet") or "").strip()
    pub = result.get("publication_info") or {}
    summary = pub.get("summary") if isinstance(pub, dict) else None
    year, venue = parse_publication_summary(summary if isinstance(summary, str) else None)

    inline = result.get("inline_links") or {}
    cited_by = inline.get("cited_by") if isinstance(inline, dict) else None
    citation_count = ""
    if isinstance(cited_by, dict) and cited_by.get("total") is not None:
        citation_count = str(cited_by["total"])

    doi = extract_doi(link, snippet, summary if isinstance(summary, str) else None)

    # Scholar organic results rarely expose keywords; keep column for Excel / later enrichment.
    keywords = ""
    if isinstance(result.get("keywords"), list):
        keywords = "; ".join(str(k) for k in result["keywords"])
    elif isinstance(result.get("keywords"), str):
        keywords = result["keywords"]

    return PaperRow(
        title=title,
        year=year,
        venue=venue,
        abstract=snippet,
        citation_count=citation_count,
        paper_url=link,
        query=query_label,
        scholar_rank=str(scholar_rank),
        scholar_page=str(scholar_page),
        doi=doi,
        keywords=keywords,
    )
