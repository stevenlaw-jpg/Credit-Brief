"""The frontend reads these files directly. If the pipeline changes a field name
and the page stops rendering it, nothing else in the suite would notice — the
page would just go quietly blank. These tests hold the two sides together.
"""

import json
import re
from pathlib import Path

import pytest

from pipeline.run import run
from pipeline.store import Store

SITE = Path(__file__).parent.parent / "docs"
APP_JS = (SITE / "assets" / "app.js").read_text(encoding="utf-8")
CSS = (SITE / "assets" / "style.css").read_text(encoding="utf-8")
HTML = (SITE / "index.html").read_text(encoding="utf-8")
FIXTURE = Path(__file__).parent / "fixtures" / "trap_cases.json"


def _strip_comments(js: str) -> str:
    js = re.sub(r"/\*.*?\*/", " ", js, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", " ", js)


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    d = tmp_path_factory.mktemp("site-data")
    run(mock_llm=True, fixture=str(FIXTURE), data_dir=str(d), no_prune=True)
    store = Store(d)
    index = json.loads((d / "index.json").read_text())
    day = store.read_day(store.dates()[0])
    return index, day


# ------------------------------------------------------------- data contract

ITEM_FIELDS_READ_BY_APP = [
    "title_en", "title_zh", "summary_en", "summary_zh", "url", "also_urls",
    "source", "published_at", "gp_ids", "sector_ids", "importance",
]


def test_day_file_supplies_every_field_the_page_reads(generated):
    _, day = generated
    for item in day["items"]:
        for field in ITEM_FIELDS_READ_BY_APP:
            assert field in item, f"item {item['id']} lacks {field}, which app.js renders"


def test_index_supplies_every_field_the_page_reads(generated):
    index, _ = generated
    for field in ("generated_at", "update_interval_hours", "retention_days", "names", "dates"):
        assert field in index
    assert "gps" in index["names"] and "sectors" in index["names"]
    for table in index["names"].values():
        for entry in table.values():
            assert "en" in entry and "zh" in entry, "a tag has no label in both languages"


def test_tag_ids_used_by_items_all_resolve_to_names(generated):
    index, day = generated
    for item in day["items"]:
        for gp in item["gp_ids"]:
            assert gp in index["names"]["gps"], f"{gp} would render as a raw id"
        for sector in item["sector_ids"]:
            assert sector in index["names"]["sectors"], f"{sector} would render as a raw id"


def test_dates_are_newest_first(generated):
    index, _ = generated
    dates = [d["date"] for d in index["dates"]]
    assert dates == sorted(dates, reverse=True)


def test_items_are_newest_first(generated):
    """The page renders in file order and never re-sorts (§8.1)."""
    _, day = generated
    stamps = [i["published_at"] for i in day["items"]]
    assert stamps == sorted(stamps, reverse=True)


def test_audit_only_fields_are_present_but_never_rendered(generated):
    """`reason` is written for the audit log. It must not appear in app.js."""
    _, day = generated
    assert any("reason" in i for i in day["items"])
    assert "item.reason" not in APP_JS
    assert "affected_exposure" not in APP_JS


# -------------------------------------------------------- hard requirements

def test_language_toggle_is_in_the_top_right(generated):
    """R6. The toggle is the last element of the masthead's flex row, which is
    justify-content: space-between — so it sits hard right."""
    assert 'class="lang-toggle"' in HTML
    masthead = HTML.split('class="masthead-inner"')[1].split("</header>")[0]
    assert masthead.index("masthead-text") < masthead.index("lang-toggle"), \
        "the toggle is not the trailing element of the masthead row"
    inner = CSS.split(".masthead-inner {")[1].split("}")[0]
    assert "justify-content: space-between" in inner
    assert "flex: 0 0 auto" in CSS.split(".lang-toggle {")[1].split("}")[0]


def test_language_toggle_swaps_the_whole_page_without_refetching():
    """R6: same content in both languages, no reload, no refetch."""
    assert "renderAll()" in APP_JS
    set_lang = _strip_comments(APP_JS.split("function setLang")[1].split("function bind")[0])
    assert "fetch" not in set_lang, "the toggle refetches; both languages ship in one file"
    assert "renderAll" in set_lang
    assert "location.reload" not in APP_JS


def test_language_persists_to_the_url():
    assert 'lang=" + state.lang' in APP_JS
    assert "history.replaceState" in APP_JS


def test_no_browser_storage_is_used():
    """BUILD_SPEC §8: no localStorage or sessionStorage."""
    for banned in ("localStorage", "sessionStorage", "document.cookie", "indexedDB"):
        assert banned not in APP_JS, f"{banned} is used but is forbidden"


def test_no_framework_or_build_step():
    assert "<script src=\"assets/app.js\"></script>" in HTML
    assert "import " not in APP_JS.split("/*")[0]
    for cdn in ("unpkg", "cdn.jsdelivr", "cdnjs", "react", "vue"):
        assert cdn not in HTML.lower()


def test_key_tag_renders_only_for_tier_one():
    """§8.2: the importance tag is never rendered for Tier 2."""
    assert 'Number(item.importance) === 1' in APP_JS
    assert ".tag-key" in CSS


def test_tag_order_is_importance_then_gp_then_sector():
    render = APP_JS.split("var tags = li.querySelector")[1].split("if (!tags.childNodes")[0]
    assert render.index("tag-key") < render.index("tag-gp") < render.index("tag-sector")


def test_three_tag_classes_are_visually_distinct():
    for cls in (".tag-key", ".tag-gp", ".tag-sector"):
        assert cls in CSS
    key = CSS.split(".tag-key {")[1].split("}")[0]
    gp = CSS.split(".tag-gp     {")[1].split("}")[0]
    assert "--accent" in key and "--accent" not in gp, "only the Key tag carries the accent"


def test_tags_are_small_print():
    """R9: small type, visually subordinate to the summary."""
    tag = CSS.split(".tag {")[1].split("}")[0]
    size = float(re.search(r"font-size:\s*([\d.]+)rem", tag).group(1))
    summary = CSS.split(".item-summary {")[1].split("}")[0]
    summary_size = float(re.search(r"font-size:\s*([\d.]+)rem", summary).group(1))
    assert size < summary_size * 0.75


def test_body_text_is_at_least_16px_on_mobile():
    body = CSS.split("\nbody {")[1].split("}")[0]
    px = float(re.search(r"font-size:\s*(\d+)px", body).group(1))
    assert px >= 16


def test_line_length_is_constrained():
    measure = re.search(r"--measure:\s*(\d+)ch", CSS)
    assert measure and int(measure.group(1)) <= 75


def test_cjk_has_its_own_stack_and_looser_leading():
    assert "--font-cjk" in CSS
    zh = CSS.split('body[lang="zh"] {')[1].split("}")[0]
    latin = CSS.split("\nbody {")[1].split("}")[0]
    zh_lh = float(re.search(r"line-height:\s*([\d.]+)", zh).group(1))
    latin_lh = float(re.search(r"line-height:\s*([\d.]+)", latin).group(1))
    assert zh_lh > latin_lh


def test_accessibility_basics():
    assert ":focus-visible" in CSS and "outline:" in CSS
    assert "prefers-reduced-motion" in CSS
    assert "skip-link" in HTML
    assert 'aria-pressed' in HTML


def test_dark_mode_is_handled():
    assert "prefers-color-scheme: dark" in CSS
    assert 'name="color-scheme"' in HTML


def test_empty_day_and_load_failure_are_never_blank():
    """§8.1: never a blank page, never a silent failure."""
    assert "emptyNote" in APP_JS and "errorTitle" in APP_JS
    assert "showStatus(t().empty" in APP_JS
    assert "showStatus(t().errorTitle" in APP_JS


def test_every_string_exists_in_both_languages():
    en = set(re.findall(r"^\s{6}(\w+):", APP_JS.split("en: {")[1].split("zh: {")[0], re.M))
    zh = set(re.findall(r"^\s{6}(\w+):", APP_JS.split("zh: {")[1].split("};")[0], re.M))
    assert en == zh, f"untranslated chrome strings: {en ^ zh}"


def test_default_date_is_the_most_recent_not_after_today():
    fn = APP_JS.split("function defaultDate")[1].split("function setLang")[0]
    assert "todayInMarketTz" in fn and "<= today" in fn


def test_timestamps_are_rendered_in_eastern_time_with_the_zone_labelled():
    assert 'var TZ = "America/New_York"' in APP_JS
    fn = APP_JS.split("function formatStamp")[1].split("function hostOf")[0]
    assert "timeZone: TZ" in fn and "t().tz" in fn


def test_links_open_safely():
    assert 'rel="noopener noreferrer"' in HTML and 'target="_blank"' in HTML


def test_no_secret_or_internal_note_is_embedded_in_the_page():
    for text in (HTML, APP_JS, CSS):
        assert "sk-ant" not in text and "ANTHROPIC_API_KEY" not in text


def test_exposure_tags_are_visually_distinct_from_named_managers():
    """An inferred exposure must never be readable as a confirmed mention."""
    assert ".tag-exposure" in CSS
    exposure = CSS.split(".tag-exposure {")[1].split("}")[0]
    gp = CSS.split(".tag-gp     {")[1].split("}")[0]
    assert "background: transparent" in exposure and "background: var(--gp-bg)" in gp
    assert "box-shadow: inset" in exposure, "the exposure tag is not outlined"


def test_exposure_tags_render_after_the_confirmed_tags():
    render = APP_JS.split("var tags = li.querySelector")[1].split("el.items.appendChild")[0]
    assert render.index("tag-sector") < render.index("tag-exposure")


def test_the_tag_convention_is_explained_on_the_page():
    """The filled/outlined distinction is not self-evident, so it is stated."""
    en = APP_JS.split("en: {")[1].split("zh: {")[0]
    assert "outlined" in en and "named in the story" in en
    zh = APP_JS.split("zh: {")[1].split("};")[0]
    assert "描边" in zh and "点名" in zh


def test_data_fetches_are_cache_busted():
    """GitHub Pages serves these with a ten-minute max-age; a reader refreshing
    just after a run must not be shown the previous build."""
    fn = APP_JS.split("function fetchJson")[1].split("function dayVersion")[0]
    assert '"?v="' in fn and 'cache: "no-store"' in fn
    assert "dayVersion(date)" in APP_JS, "day files are not versioned by updated_at"


def test_aggregator_hosts_are_not_credited_as_publishers():
    assert "AGGREGATORS" in APP_JS
    assert "news.google.com" in APP_JS.split("var AGGREGATORS")[1].split("};")[0]
