"""BUILD_SPEC §10.2: running twice produces no duplicates; retention deletes the
right files."""

import os

import json

import pytest

from pipeline.store import Store


@pytest.fixture()
def store(tmp_path):
    return Store(tmp_path)


def _item(i, published, region="US", importance=2):
    return {"id": i, "title_en": f"Title {i}", "title_zh": f"标题 {i}",
            "summary_en": "s", "summary_zh": "摘要", "url": f"https://x.com/{i}",
            "source": "Src", "published_at": published, "gp_ids": [], "sector_ids": [],
            "region": region, "importance": importance, "grounded_on": "article"}


def test_running_twice_produces_no_duplicates(store):
    items = [_item("a", "2026-09-12T10:00:00Z"), _item("b", "2026-09-12T21:00:00Z")]
    store.merge_day("2026-09-12", items)
    store.merge_day("2026-09-12", items)
    assert len(store.read_day("2026-09-12")["items"]) == 2


def test_items_are_stored_newest_first(store):
    store.merge_day("2026-09-12", [_item("a", "2026-09-12T10:00:00Z"),
                                   _item("b", "2026-09-12T21:00:00Z"),
                                   _item("c", "2026-09-12T15:00:00Z")])
    assert [i["id"] for i in store.read_day("2026-09-12")["items"]] == ["b", "c", "a"]


def test_merge_updates_in_place(store):
    store.merge_day("2026-09-12", [_item("a", "2026-09-12T10:00:00Z")])
    updated = _item("a", "2026-09-12T10:00:00Z")
    updated["title_en"] = "Revised"
    store.merge_day("2026-09-12", [updated])
    day = store.read_day("2026-09-12")
    assert len(day["items"]) == 1 and day["items"][0]["title_en"] == "Revised"


def test_internal_fields_never_reach_the_day_file(store):
    """The site serves these files. Nothing internal may leak into them."""
    item = _item("a", "2026-09-12T10:00:00Z")
    item["api_key"] = "sk-should-never-appear"
    item["raw_body"] = "x" * 5000
    store.merge_day("2026-09-12", [item])
    text = store.day_path("2026-09-12").read_text()
    assert "sk-should-never-appear" not in text
    assert "raw_body" not in text


def test_retention_deletes_old_files_and_prunes_seen(store):
    store.merge_day("2026-09-12", [_item("new", "2026-09-12T10:00:00Z")])
    store.merge_day("2026-05-01", [_item("old", "2026-05-01T10:00:00Z")])
    seen = {"new": {"date": "2026-09-12", "first_seen": "x"},
            "old": {"date": "2026-05-01", "first_seen": "x"}}
    store.write_seen(seen)

    result = store.prune(retention_days=90, today="2026-09-12")
    assert result["pruned_days"] == ["2026-05-01"]
    assert store.dates() == ["2026-09-12"]
    assert set(store.read_seen()) == {"new"}


def test_retention_keeps_the_boundary_day(store):
    """A 90-day window includes today and the 89 days before it."""
    store.merge_day("2026-06-16", [_item("edge", "2026-06-16T10:00:00Z")])
    store.prune(retention_days=90, today="2026-09-13")
    assert "2026-06-16" in store.dates()


def test_index_lists_only_dates_with_files_newest_first(store):
    store.merge_day("2026-09-10", [_item("a", "2026-09-10T10:00:00Z")])
    store.merge_day("2026-09-12", [_item("b", "2026-09-12T10:00:00Z"),
                                   _item("c", "2026-09-12T11:00:00Z")])
    index = store.write_index()
    assert [d["date"] for d in index["dates"]] == ["2026-09-12", "2026-09-10"]
    assert index["dates"][0]["count"] == 2
    assert "blue-owl" in index["names"]["gps"]
    assert "clo" in index["names"]["sectors"]


def test_eu_share_is_none_when_nothing_to_measure(store):
    assert store.eu_share(today="2026-09-12") is None


def test_eu_share_measures_output(store):
    store.merge_day("2026-09-12", [
        _item("a", "2026-09-12T10:00:00Z", region="US"),
        _item("b", "2026-09-12T11:00:00Z", region="Europe"),
        _item("c", "2026-09-12T12:00:00Z", region="US"),
        _item("d", "2026-09-12T13:00:00Z", region="US"),
    ])
    assert store.eu_share(today="2026-09-12") == 0.25


def test_run_log_round_trip(store):
    store.append_run({"run_at": "2026-09-12T00:00:00Z", "fetched": 10})
    store.append_run({"run_at": "2026-09-12T03:00:00Z", "fetched": 12})
    rows = store.read_runs()
    assert len(rows) == 2 and rows[-1]["fetched"] == 12


def test_corrupt_day_file_does_not_raise(store):
    store.day_path("2026-09-12").write_text("{ not json")
    assert store.read_day("2026-09-12")["items"] == []


def test_day_file_is_valid_json_with_required_shape(store):
    store.merge_day("2026-09-12", [_item("a", "2026-09-12T10:00:00Z")])
    payload = json.loads(store.day_path("2026-09-12").read_text())
    assert set(payload) == {"date", "updated_at", "items"}
    assert payload["date"] == "2026-09-12"


def test_deferred_items_are_not_marked_seen(tmp_path):
    """An item that passed the filter but lost its place to the per-run cap must
    stay unseen, or it is discarded permanently — and the cap binds precisely on
    the busiest days, when losing news is least acceptable."""
    from pathlib import Path

    from pipeline.run import run

    fixture = Path(__file__).parent / "fixtures" / "trap_cases.json"
    first = run(mock_llm=True, fixture=str(fixture), data_dir=str(tmp_path),
                no_prune=True, max_items=3)
    assert first["summarised"] == 3
    assert first["deferred"] >= 1

    store = Store(tmp_path)
    seen = store.read_seen()
    published = {i["id"] for d in store.dates() for i in store.read_day(d)["items"]}
    assert published <= set(seen), "published items should be marked seen"

    # The next run picks the deferred items up rather than losing them.
    second = run(mock_llm=True, fixture=str(fixture), data_dir=str(tmp_path),
                 no_prune=True, max_items=20)
    assert second["new"] == first["deferred"]
    assert second["summarised"] >= 1
    total = sum(len(store.read_day(d)["items"]) for d in store.dates())
    assert total > first["summarised"], "deferred items never made it to the page"


# ------------------------------------------------- exposure tags (the matrix)

def test_exposure_managers_are_shown_when_no_manager_is_named(tmp_path):
    """A market-level story names no manager, so on its own it leaves the reader
    to remember which of his holdings it touches. The matrix already knows."""
    from pipeline.config import load_entities

    ent = load_entities()
    item = {"gp_ids": [], "sector_ids": ["clo"]}
    exposure = Store.exposure_gp_ids(item, ent)
    assert "cifc" in exposure, "the pure-CLO manager must lead a CLO story"
    assert len(exposure) <= 4, "more than four turns the tag row into noise"


def test_exposure_is_empty_when_a_manager_is_named(tmp_path):
    """The story has already answered the question. Padding the row with firms
    that merely operate in the sector would bury the one actually in the news."""
    from pipeline.config import load_entities

    ent = load_entities()
    item = {"gp_ids": ["bain-capital"], "sector_ids": ["aircraft-leasing"]}
    assert Store.exposure_gp_ids(item, ent) == []


def test_exposure_never_duplicates_across_sectors(tmp_path):
    from pipeline.config import load_entities

    ent = load_entities()
    exposure = Store.exposure_gp_ids(
        {"gp_ids": [], "sector_ids": ["software", "private-credit"]}, ent)
    assert len(exposure) == len(set(exposure))


def test_exposure_is_written_to_the_day_file(tmp_path):
    store = Store(tmp_path)
    item = _item("a", "2026-09-12T10:00:00Z")
    item["gp_ids"] = []
    item["sector_ids"] = ["clo"]
    store.merge_day("2026-09-12", [item])
    written = store.read_day("2026-09-12")["items"][0]
    assert written["exposure_gp_ids"], "exposure was computed but never persisted"


def test_tag_labels_are_short_in_both_languages(tmp_path):
    """Tags are small print and are scanned, not read."""
    index = Store(tmp_path).write_index()
    for gid, entry in index["names"]["gps"].items():
        for lang in ("en", "zh"):
            assert len(entry[lang]) <= 24, f"{gid}.{lang} is too long for a tag"


def test_dotenv_never_overrides_a_real_environment_variable(tmp_path, monkeypatch):
    """CI supplies the key through the environment. A stale local .env must not
    be able to shadow it."""
    import pipeline.config as config

    env_file = tmp_path / ".env"
    env_file.write_text('ANTHROPIC_API_KEY="from-file"\nEDGAR_USER_AGENT=from-file\n')
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-environment")
    monkeypatch.setattr(config, "_ENV_LOADED", False)
    config.load_dotenv(env_file)
    assert config.api_key() == "from-environment"
    assert os.environ["EDGAR_USER_AGENT"] == "from-file"


def test_dotenv_is_optional(tmp_path, monkeypatch):
    import pipeline.config as config

    monkeypatch.setattr(config, "_ENV_LOADED", False)
    config.load_dotenv(tmp_path / "does-not-exist")   # must not raise
