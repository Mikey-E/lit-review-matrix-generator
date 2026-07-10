from litreview.dedupe import Deduper
from litreview.models import (
    PaperRow,
    extract_doi,
    normalize_title,
    parse_publication_summary,
    paper_from_organic,
)


def test_normalize_title():
    assert normalize_title("Hello, World!") == "hello world"
    assert normalize_title("  AI-Tutoring  ") == "ai tutoring"


def test_extract_doi():
    assert (
        extract_doi("https://doi.org/10.1234/abc.def")
        == "10.1234/abc.def"
    )
    assert extract_doi("no doi here") == ""


def test_parse_publication_summary():
    year, venue = parse_publication_summary(
        "A Author - Nature Education, 2020 - nature.com"
    )
    assert year == "2020"
    assert venue == "Nature Education"

    year, venue = parse_publication_summary("A Author - 2019 - example.com")
    assert year == "2019"
    assert venue == ""


def test_paper_from_organic():
    result = {
        "title": "A Study of Tutors",
        "link": "https://doi.org/10.1000/xyz123",
        "snippet": "We studied tutors in universities.",
        "publication_info": {
            "summary": "J Doe - Computers & Education, 2021 - Elsevier"
        },
        "inline_links": {"cited_by": {"total": 42}},
    }
    row = paper_from_organic(
        result, "q1", scholar_rank=11, scholar_page=2
    )
    assert row.title == "A Study of Tutors"
    assert row.year == "2021"
    assert row.venue == "Computers & Education"
    assert row.citation_count == "42"
    assert row.doi == "10.1000/xyz123"
    assert row.query == "q1"
    assert row.scholar_rank == "11"
    assert row.scholar_page == "2"


def _row(**overrides: str) -> PaperRow:
    base = dict(
        title="T",
        year="2020",
        venue="",
        abstract="",
        citation_count="1",
        paper_url="http://a",
        query="q1",
        scholar_rank="1",
        scholar_page="1",
        doi="",
        keywords="",
    )
    base.update(overrides)
    return PaperRow(**base)


def test_dedupe_by_title_and_doi():
    deduper = Deduper()
    a = _row(title="Same Title", citation_count="1", paper_url="http://a", query="q1")
    b = _row(
        title="same title!",
        year="2021",
        citation_count="2",
        paper_url="http://b",
        query="q2",
        scholar_rank="5",
        scholar_page="1",
    )
    c = _row(
        title="Other",
        citation_count="3",
        paper_url="http://c",
        doi="10.1/abc",
    )
    d = _row(
        title="Different Title",
        citation_count="4",
        paper_url="http://d",
        query="q2",
        doi="10.1/ABC",
    )
    assert deduper.keep(a) is True
    assert deduper.keep(b) is False
    assert deduper.keep(c) is True
    assert deduper.keep(d) is False
    assert deduper.kept == 2
    assert deduper.dropped == 2
