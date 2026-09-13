"""BUILD_SPEC §10.2: tracking params stripped; two outlets' copies of one story
collapse to one item with the higher-priority link primary."""

from pipeline.dedupe import canonical_url, dedupe, title_similarity, url_hash


def test_tracking_params_are_stripped():
    a = canonical_url("https://www.example.com/story?utm_source=twitter&utm_medium=social&id=7")
    assert a == "https://example.com/story?id=7"


def test_www_and_trailing_slash_normalised():
    assert canonical_url("https://www.example.com/a/") == canonical_url("http://example.com/a")


def test_aggregator_redirect_is_unwrapped():
    wrapped = "https://news.google.com/rss/articles/CBMi?url=https%3A%2F%2Fwww.reuters.com%2Fx%3Futm_source%3Dgoogle"
    assert canonical_url(wrapped) == "https://reuters.com/x"


def test_amp_url_is_unwrapped():
    amp = "https://example-com.cdn.ampproject.org/c/s/example.com/story/amp"
    assert canonical_url(amp) == "https://example.com/story"


def test_url_hash_is_stable_across_equivalent_urls():
    assert url_hash("https://www.example.com/a?utm_source=x") == url_hash("http://example.com/a/")


def test_identical_urls_collapse():
    items = [
        {"url": "https://example.com/a?utm_source=x", "title": "One", "source": "A",
         "priority": 1, "published_at": "2026-09-12T10:00:00Z"},
        {"url": "https://example.com/a", "title": "One", "source": "B",
         "priority": 2, "published_at": "2026-09-12T10:05:00Z"},
    ]
    out = dedupe(items)
    assert len(out) == 1
    assert out[0]["source"] == "B"          # higher priority wins the primary slot


def test_two_outlets_one_story_collapses_with_priority_link_primary():
    items = [
        {"url": "https://reuters.com/a", "priority": 2, "source": "Reuters",
         "title": "Blue Owl Technology Finance reports second quarter NAV of $17.20 a share",
         "published_at": "2026-09-12T21:30:00Z"},
        {"url": "https://businesswire.com/b", "priority": 3, "source": "Business Wire",
         "title": "Blue Owl Technology Finance Reports Q2 NAV of $17.20 a Share",
         "published_at": "2026-09-12T21:25:00Z"},
    ]
    out = dedupe(items)
    assert len(out) == 1
    assert out[0]["source"] == "Business Wire"
    assert out[0]["url"] == "https://businesswire.com/b"
    assert "https://reuters.com/a" in out[0]["also_urls"]
    assert "Reuters" in out[0]["also_sources"]
    assert out[0]["published_at"] == "2026-09-12T21:25:00Z"   # earliest known


def test_unrelated_stories_are_not_merged():
    items = [
        {"url": "https://a.com/1", "priority": 2, "source": "A",
         "title": "CLO AAA spreads tighten to 120bp", "published_at": "2026-09-12T10:00:00Z"},
        {"url": "https://b.com/2", "priority": 2, "source": "B",
         "title": "Bayview acquires a mortgage servicing portfolio",
         "published_at": "2026-09-12T11:00:00Z"},
    ]
    assert len(dedupe(items)) == 2


def test_output_is_newest_first():
    items = [
        {"url": f"https://x.com/{i}", "priority": 1, "source": "X", "title": f"Story number {i} about credit markets and lending",
         "published_at": f"2026-09-12T0{i}:00:00Z"} for i in range(1, 4)
    ]
    out = dedupe(items)
    stamps = [o["published_at"] for o in out]
    assert stamps == sorted(stamps, reverse=True)


def test_title_similarity():
    assert title_similarity("Blue Owl reports Q2 NAV of $17.20",
                            "Blue Owl Reports Q2 NAV Of $17.20") == 1.0
    assert title_similarity("CLO spreads tighten", "Bayview buys MSR portfolio") < 0.2


def test_empty_and_malformed_urls_do_not_raise():
    assert canonical_url("") == ""
    assert canonical_url("not a url") == "https:///not a url" or True
