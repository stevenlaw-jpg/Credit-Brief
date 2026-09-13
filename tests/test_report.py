"""The write-up is required to quote real funnel numbers rather than adjectives
(BUILD_SPEC §11.1). `pipeline.report` is what produces them, so it has to keep
working as the run log grows."""

import json

import pytest

from pipeline.report import collect, markdown
from pipeline.run import run
from pipeline.store import Store
from tests.test_trap_cases import FIXTURE


@pytest.fixture()
def populated(tmp_path):
    run(mock_llm=True, fixture=str(FIXTURE), data_dir=str(tmp_path), no_prune=True)
    return Store(tmp_path)


def test_report_on_an_empty_log_does_not_raise(tmp_path):
    data = collect(Store(tmp_path))
    assert data["runs"] == 0
    assert "No completed runs yet" in markdown(data)


def test_report_sums_the_funnel(populated):
    data = collect(populated)
    assert data["runs"] == 1
    assert data["funnel"]["fetched"] == 19
    assert data["funnel"]["candidates"] == 13
    assert data["funnel"]["summarised"] == data["published"]


def test_report_counts_tier_one_and_regions(populated):
    data = collect(populated)
    assert 0 < data["tier1"] <= data["published"]
    assert sum(data["region_mix"].values()) == data["published"]


def test_report_lists_agent3_rescues_with_their_exposure(populated):
    data = collect(populated)
    assert data["rescues"], "the rescue log is the diagnostic the README relies on"
    assert all("affected_exposure" in r for r in data["rescues"])


def test_markdown_is_renderable_and_quotes_numbers(populated):
    md = markdown(collect(populated))
    assert "| Stage | Items |" in md
    assert "Agent 3 rescued" in md
    assert "{{" not in md and "None" not in md.split("Regenerate")[0]


def test_report_json_round_trips(populated):
    json.dumps(collect(populated))
