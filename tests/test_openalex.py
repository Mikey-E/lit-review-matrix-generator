from litreview.models import PaperRow
from litreview.openalex import (
    EnrichmentStats,
    apply_openalex_fields,
    keywords_from_work,
    reconstruct_abstract,
)


def test_reconstruct_abstract():
    inverted = {
        "Hello": [0],
        "world": [1],
        "from": [2],
        "OpenAlex": [3],
    }
    assert reconstruct_abstract(inverted) == "Hello world from OpenAlex"
    assert reconstruct_abstract(None) == ""
    assert reconstruct_abstract({}) == ""


def test_keywords_prefer_keywords_then_topics():
    work = {
        "keywords": [{"display_name": "tutoring"}, {"display_name": "AI"}],
        "topics": [{"display_name": "Education"}],
    }
    assert keywords_from_work(work) == "tutoring; AI"

    work = {"topics": [{"display_name": "Machine learning"}, {"display_name": "Education"}]}
    assert keywords_from_work(work) == "Machine learning; Education"


def test_apply_openalex_fills_missing_fields():
    row = PaperRow(
        title="A Study",
        year="2021",
        venue="Venue",
        abstract="Short snippet …",
        citation_count="3",
        paper_url="http://example.com",
        query="q1",
        scholar_rank="12",
        scholar_page="2",
        doi="",
        keywords="",
    )
    work = {
        "doi": "https://doi.org/10.1000/xyz",
        "abstract_inverted_index": {
            "This": [0],
            "is": [1],
            "a": [2],
            "full": [3],
            "abstract": [4],
            "with": [5],
            "enough": [6],
            "detail": [7],
            "to": [8],
            "replace": [9],
            "the": [10],
            "truncated": [11],
            "scholar": [12],
            "snippet": [13],
            "easily": [14],
        },
        "keywords": [{"display_name": "ITS"}, {"display_name": "higher education"}],
    }
    stats = EnrichmentStats()
    enriched = apply_openalex_fields(row, work, stats)
    assert enriched.doi == "10.1000/xyz"
    assert "full abstract" in enriched.abstract
    assert enriched.keywords == "ITS; higher education"
    assert enriched.scholar_rank == "12"
    assert enriched.scholar_page == "2"
    assert stats.dois_filled == 1
    assert stats.abstracts_filled == 1
    assert stats.keywords_filled == 1


def test_apply_openalex_does_not_overwrite_existing_doi_or_keywords():
    row = PaperRow(
        title="A Study",
        year="2021",
        venue="Venue",
        abstract="Already long enough abstract that should not be treated as empty.",
        citation_count="3",
        paper_url="http://example.com",
        query="q1",
        scholar_rank="1",
        scholar_page="1",
        doi="10.1/keep",
        keywords="keep-me",
    )
    work = {
        "doi": "https://doi.org/10.1/other",
        "abstract_inverted_index": {"x": [0]},
        "keywords": [{"display_name": "new"}],
    }
    stats = EnrichmentStats()
    enriched = apply_openalex_fields(row, work, stats)
    assert enriched.doi == "10.1/keep"
    assert enriched.keywords == "keep-me"
    assert stats.dois_filled == 0
    assert stats.keywords_filled == 0
