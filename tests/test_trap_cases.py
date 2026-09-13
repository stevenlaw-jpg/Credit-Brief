"""BUILD_SPEC §10.1. These must behave correctly before shipping.

Run offline (`--mock-llm`) this suite proves the plumbing: that excluded items
never reach a day file, that the rule layer vetoes the homonyms, and that the
Agent 3 rescue path works end to end. Run against the real API (§13 step 5) the
same expectations test the prompts' judgement.
"""

import json
from pathlib import Path

import pytest

from pipeline.agents import LLM, build_shortlist, keep_decision
from pipeline.config import load_entities, load_settings
from pipeline.entities import match_item, match_text
from pipeline.run import run
from pipeline.store import Store

FIXTURE = Path(__file__).parent / "fixtures" / "trap_cases.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))["items"]
BY_ID = {c["id"]: c for c in CASES}
ENT = load_entities()


@pytest.fixture(scope="module")
def offline_run(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("data")
    stats = run(mock_llm=True, fixture=str(FIXTURE), data_dir=str(data_dir), no_prune=True)
    store = Store(data_dir)
    published = []
    for date in store.dates():
        published.extend(store.read_day(date)["items"])
    return stats, published, store


def _titles(items):
    return " || ".join(i["title_en"] for i in items)


# ------------------------------------------------------------- must exclude

@pytest.mark.parametrize("case_id", [c["id"] for c in CASES
                                     if not c["expect"]["keep"] and not c["expect"].get("rescue")])
def test_must_be_excluded(case_id, offline_run):
    _, published, _ = offline_run
    case = BY_ID[case_id]
    ids = {i["id"] for i in published}
    from pipeline.dedupe import url_hash
    assert url_hash(case["url"]) not in ids, (
        f"{case_id} reached the page but must be excluded: {case['expect']['why']}\n"
        f"  title: {case['title']}"
    )


def test_no_test_fixture_ever_reaches_production_wording(offline_run):
    """Every fixture is marked [TEST]; nothing unmarked may masquerade as one."""
    for case in CASES:
        assert case["title"].startswith("[TEST]")


# ------------------------------------------------------------- must include

@pytest.mark.parametrize("case_id", [c["id"] for c in CASES if c["expect"]["keep"]])
def test_must_be_included(case_id, offline_run):
    _, published, _ = offline_run
    case = BY_ID[case_id]
    from pipeline.dedupe import url_hash
    match = [i for i in published if i["id"] == url_hash(case["url"])]
    assert match, (f"{case_id} was dropped but must be included: {case['expect']['why']}\n"
                   f"  title: {case['title']}")
    item = match[0]
    expect = case["expect"]
    if "gp_ids" in expect:
        assert set(expect["gp_ids"]) <= set(item["gp_ids"]), (
            f"{case_id}: expected GPs {expect['gp_ids']}, got {item['gp_ids']}")
    if "sector_ids" in expect:
        assert set(expect["sector_ids"]) <= set(item["sector_ids"]), (
            f"{case_id}: expected sectors {expect['sector_ids']}, got {item['sector_ids']}")


# --------------------------------------------------- the homonyms never even
# reach a model: the rule layer must veto them, saving the API call.

@pytest.mark.parametrize("case_id", ["trap-03", "trap-04", "trap-05", "trap-06",
                                     "trap-07", "trap-08"])
def test_homonyms_are_not_candidates(case_id):
    case = BY_ID[case_id]
    rule = match_item({"title": case["title"], "summary": case["summary"],
                       "source": case["source"]}, ENT)
    assert not rule.is_candidate, (
        f"{case_id} became a candidate and would cost an API call: "
        f"gp={rule.gp_ids} sector={rule.sector_ids}")


def test_kkr_buyout_is_a_candidate_but_is_rejected(offline_run):
    """The rule layer must NOT hide this one — Agent 2 has to make the call,
    on the record, so the audit log shows the credit-only rule being applied."""
    case = BY_ID["trap-01"]
    rule = match_item({"title": case["title"], "summary": case["summary"]}, ENT)
    assert rule.is_candidate and "kkr" in rule.gp_ids
    _, published, _ = offline_run
    assert "theme-park" not in _titles(published).lower()


# ------------------------------------------------------ the Agent 3 recall test

def test_agent3_rescues_the_planted_item(offline_run):
    """The test that proves Agent 3 earns its cost (BUILD_SPEC §10.1).

    Headline names no tracked manager; the body names Blue Owl Technology
    Finance as lead lender. Agent 2 must miss it; Agent 3 must catch it.
    """
    stats, published, _ = offline_run
    case = BY_ID["trap-19-agent3-recall"]
    from pipeline.dedupe import url_hash
    match = [i for i in published if i["id"] == url_hash(case["url"])]
    assert match, "the planted item was never rescued"
    item = match[0]
    assert item["rescued_by_agent3"] is True
    assert "otf" in item["gp_ids"] or "blue-owl" in item["gp_ids"], (
        "rescued, but without naming the exposure the body revealed")
    assert stats["agent3_rescued"] >= 1


def test_agent2_cannot_see_what_agent3_sees():
    """Agent 3's whole advantage is the article body. If the rule layer or the
    Agent 2 payload saw the body, Agent 3 would be redundant."""
    case = BY_ID["trap-19-agent3-recall"]
    head_only = match_text(f"{case['title']} {case['summary']}", ENT)
    with_body = match_text(f"{case['title']} {case['summary']} {case['body']}", ENT)
    assert not head_only.gp_ids
    assert "otf" in with_body.gp_ids

    item = dict(case)
    item["id"] = "x"
    assert not match_item(item, ENT).gp_ids, "the body leaked into the rule layer"


# ------------------------------------------------------------------- funnel

def test_funnel_counts_are_recorded(offline_run):
    stats, _, _ = offline_run
    for key in ("fetched", "after_dedupe", "candidates", "new", "agent2_kept",
                "agent3_shortlist", "agent3_rescued", "summarised"):
        assert key in stats, f"{key} missing from the run record"
    assert stats["candidates"] <= stats["after_dedupe"]
    assert stats["agent2_kept"] <= stats["new"]


def test_every_published_item_has_both_languages(offline_run):
    _, published, _ = offline_run
    for item in published:
        for field in ("title_en", "title_zh", "summary_en", "summary_zh"):
            assert item.get(field), f"{item['id']} is missing {field}"


def test_every_published_item_names_an_exposure(offline_run):
    """An item with no GP and no sub-sector cannot be justified to the reader."""
    _, published, _ = offline_run
    for item in published:
        assert item["gp_ids"] or item["sector_ids"], f"{item['id']} names no exposure"


def test_tier_3_never_reaches_the_page(offline_run):
    _, published, _ = offline_run
    assert all(i["importance"] in (1, 2) for i in published)


def test_run_is_idempotent(tmp_path):
    """BUILD_SPEC §7.4: same run twice, no duplicate items, no duplicate spend."""
    first = run(mock_llm=True, fixture=str(FIXTURE), data_dir=str(tmp_path), no_prune=True)
    second = run(mock_llm=True, fixture=str(FIXTURE), data_dir=str(tmp_path), no_prune=True)
    assert second["new"] == 0, "the seen-set did not suppress a repeat run"
    assert second.get("summarised", 0) == 0, "a repeat run paid for summaries again"
    store = Store(tmp_path)
    total = sum(len(store.read_day(d)["items"]) for d in store.dates())
    assert total == first["summarised"]


def test_index_is_written(offline_run):
    _, _, store = offline_run
    index = json.loads((store.dir / "index.json").read_text())
    assert index["dates"], "index lists no dates"
    assert index["timezone"] == "America/New_York"
    assert "otf" in index["names"]["gps"]


def test_no_secret_ever_reaches_the_data_directory(offline_run):
    """SECURITY (BUILD_SPEC §2): the repo is public and serves these files."""
    _, _, store = offline_run
    for path in store.dir.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert "sk-ant" not in text
            assert "ANTHROPIC_API_KEY" not in text


# ------------------------------------------------------- shortlist behaviour

def test_shortlist_scoring_prefers_contradictions():
    """A rule-layer GP match that Agent 2 rejected outranks an unmatched item."""
    contradiction = ({"id": "a", "title": "Blue Owl Credit reports non-accruals",
                      "summary": "", "source": "Business Wire", "priority": 3,
                      "_rule": match_text("Blue Owl Credit reports non-accruals", ENT)},
                     {"id": "a", "relevant": False, "credit_related": True, "importance": 3,
                      "reason": "x"})
    noise = ({"id": "b", "title": "Local bakery wins an award", "summary": "",
              "source": "Blog", "priority": 1,
              "_rule": match_text("Local bakery wins an award", ENT)},
             {"id": "b", "relevant": False, "credit_related": False, "importance": 3,
              "reason": "x"})
    shortlist = build_shortlist([noise, contradiction], ENT, limit=25)
    assert shortlist[0][0]["id"] == "a"
    assert "b" not in [s[0]["id"] for s in shortlist], "unmatched noise was shortlisted"


def test_keep_decision_contract():
    assert keep_decision({"relevant": True, "importance": 1, "gp_ids": ["otf"],
                          "sector_ids": []})
    assert not keep_decision({"relevant": True, "importance": 3, "gp_ids": ["otf"],
                              "sector_ids": []})
    assert not keep_decision({"relevant": False, "importance": 1, "gp_ids": ["otf"],
                              "sector_ids": []})
    assert not keep_decision({"relevant": True, "importance": 2, "gp_ids": [],
                              "sector_ids": []})


def test_llm_refuses_to_run_without_a_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        LLM(load_settings(), mock=False)
