from litreview.fulltext import pdf_urls_from_work, quote_supported_by_text
from litreview.config import FacetSpec, QuerySpec, StudyConfig
from litreview.llm_code import LlmCoder
from litreview.resolve_unclear import UnclearFacetResolver


def test_quote_supported_by_text_requires_real_excerpt():
    text = "We evaluated yield in a commercial greenhouse using RGB and thermal cameras."
    assert quote_supported_by_text(
        "yield in a commercial greenhouse using RGB", text
    )
    assert not quote_supported_by_text("short", text)
    assert not quote_supported_by_text(
        "this quote is not present in the paper text at all", text
    )


def test_pdf_urls_from_work_collects_oa_and_arxiv():
    work = {
        "best_oa_location": {"pdf_url": "https://example.com/a.pdf"},
        "primary_location": {"pdf_url": "https://example.com/a.pdf"},
        "locations": [{"pdf_url": "https://example.com/b.pdf"}],
        "ids": {"arxiv": "https://arxiv.org/abs/1234.5678"},
    }
    urls = pdf_urls_from_work(work)
    assert urls[0] == "https://example.com/a.pdf"
    assert "https://example.com/b.pdf" in urls
    assert any("arxiv.org/pdf/1234.5678" in u for u in urls)


def test_resolve_facet_rejects_non_unclear_without_quote(monkeypatch):
    config = StudyConfig(
        title="T",
        queries=[QuerySpec(q="q")],
        screen_values=["include", "exclude", "maybe"],
        facets=[FacetSpec(name="cea_setting", values=["greenhouse", "unclear"])],
        llm_model="gpt-5.6-luna",
        max_pages=1,
    )
    coder = LlmCoder(config, client=object(), api_key="unused")  # type: ignore[arg-type]
    resolver = UnclearFacetResolver(config, coder=coder, openalex=object())  # type: ignore[arg-type]

    def fake_complete(**kwargs):
        return {
            "response": {
                "value": "greenhouse",
                "evidence_quote": "not in the document",
                "rationale": "guess",
            }
        }

    monkeypatch.setattr(coder, "_complete_json", fake_complete)
    got = resolver.resolve_facet_from_fulltext(
        {"title": "x", "abstract": "y"},
        config.facets[0],
        "We studied tomatoes in a commercial greenhouse under LED lighting.",
    )
    assert got is None
    assert resolver.stats.skipped_bad_evidence == 1
