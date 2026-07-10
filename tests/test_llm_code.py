import json

from litreview.config import FacetSpec, StudyConfig, QuerySpec
from litreview.llm_code import LlmCoder


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self) -> None:
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        schema = kwargs["response_format"]["json_schema"]["schema"]
        enum = schema["properties"][
            "screen" if "screen" in schema["properties"] else "value"
        ]["enum"]
        if "screen" in schema["properties"]:
            payload = {"screen": enum[0], "rationale": "test screen"}
        else:
            payload = {"value": enum[0], "rationale": "test facet"}
        assert kwargs["temperature"] == 0
        return _FakeResponse(json.dumps(payload))


class _FakeChat:
    def __init__(self) -> None:
        self.completions = _FakeCompletions()


class _FakeOpenAI:
    def __init__(self) -> None:
        self.chat = _FakeChat()


def _config() -> StudyConfig:
    return StudyConfig(
        title="Test Study",
        queries=[QuerySpec(q="test")],
        inclusion_criteria="Include multimodal CEA papers.",
        exclusion_criteria="Exclude text-only models.",
        screen_values=["include", "exclude", "maybe"],
        facets=[
            FacetSpec(name="modality", values=["rgb+thermal", "unclear"]),
            FacetSpec(name="outcome", values=["yield", "unclear"]),
        ],
        llm_model="gpt-4o-mini",
        max_pages=1,
    )


def test_code_records_screens_then_facets_for_include_only():
    client = _FakeOpenAI()
    coder = LlmCoder(_config(), client=client, api_key="unused")
    rows = [
        {
            "title": "A multimodal greenhouse yield model",
            "year": "2022",
            "venue": "Computers and Electronics in Agriculture",
            "abstract": "We fuse RGB and thermal imagery to predict yield.",
            "doi": "10.1000/test",
        },
        {
            "title": "A poetry LLM",
            "year": "2021",
            "venue": "arXiv",
            "abstract": "We generate poems with a language model.",
            "doi": "10.1000/other",
        },
    ]

    # Force first include, second exclude by controlling fake enum pick order:
    # Fake always picks enum[0]. For screen that's always "include".
    # So both would be include — adjust fake to alternate.
    completions = client.chat.completions
    original_create = completions.create
    state = {"n": 0}

    def create(**kwargs):
        state["n"] += 1
        schema = kwargs["response_format"]["json_schema"]["schema"]
        props = schema["properties"]
        if "screen" in props:
            # first paper include, second exclude
            screen = "include" if state["n"] == 1 else "exclude"
            return _FakeResponse(
                json.dumps({"screen": screen, "rationale": "x"})
            )
        enum = props["value"]["enum"]
        return _FakeResponse(
            json.dumps({"value": enum[0], "rationale": "y"})
        )

    completions.create = create  # type: ignore[method-assign]

    coded = coder.code_records(rows)
    assert coded[0]["screen"] == "include"
    assert coded[0]["modality"] == "rgb+thermal"
    assert coded[0]["outcome"] == "yield"
    assert coded[0]["llm_model"] == "gpt-4o-mini"
    assert coded[1]["screen"] == "exclude"
    assert coded[1].get("modality", "") == ""
    assert coder.stats.screened == 2
    assert coder.stats.facet_labels == 2
    assert coder.stats.skipped_exclude_for_facets == 1
    assert original_create  # keep reference for lint silence
