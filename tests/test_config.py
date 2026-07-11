from pathlib import Path

import pytest
import yaml

from litreview.config import load_config


def test_load_sample_config():
    path = Path("examples/sample_study.yaml")
    config = load_config(path)
    assert config.study_id == "sample-ai-tutoring"
    assert config.year_from == 2018
    assert config.max_pages == 2
    assert len(config.queries) == 2
    assert config.queries[0].label == "its-higher-ed"


def test_queries_as_strings(tmp_path: Path):
    path = tmp_path / "study.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "title": "T",
                "queries": ['"foo" AND bar'],
                "max_results": 10,
            }
        ),
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.queries[0].q == '"foo" AND bar'
    assert config.max_pages is None
    assert config.max_results == 10


def test_load_study_facets_and_screen():
    path = Path("studies/multimodal-cea-yield-cost.yaml")
    config = load_config(path)
    assert config.screen_values == ["include", "exclude", "maybe"]
    assert [f.name for f in config.facets] == [
        "modality",
        "cea_setting",
        "outcome",
        "method_family",
        "evidence_type",
        "contribution_type",
    ]
    assert "method_model" in config.facets[-1].values
    assert config.coding_columns == [
        "include/exclude",
        "modality",
        "cea_setting",
        "outcome",
        "method_family",
        "evidence_type",
        "contribution_type",
        "llm_model",
    ]
    assert config.matrix_columns[:4] == ["title", "include/exclude", "abstract", "year"]
    assert config.llm_model == "gpt-5.6-luna"


def test_requires_queries(tmp_path: Path):
    path = tmp_path / "bad.yaml"
    path.write_text("title: T\nqueries: []\n", encoding="utf-8")
    with pytest.raises(ValueError, match="queries"):
        load_config(path)
