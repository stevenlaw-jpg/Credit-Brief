"""BUILD_SPEC §10.2: an article at 21:30 ET on 12 Sept and one at 02:00 UTC on
13 Sept both land on 2026-09-12."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from pipeline.config import day_key, iso_z, today_key


def test_et_evening_and_next_utc_morning_share_a_key():
    et_evening = datetime(2026, 9, 12, 21, 30, tzinfo=ZoneInfo("America/New_York"))
    utc_morning = datetime(2026, 9, 13, 2, 0, tzinfo=timezone.utc)
    assert day_key(et_evening) == "2026-09-12"
    assert day_key(utc_morning) == "2026-09-12"


def test_midnight_boundary_et():
    assert day_key(datetime(2026, 9, 13, 3, 59, tzinfo=timezone.utc)) == "2026-09-12"
    assert day_key(datetime(2026, 9, 13, 4, 1, tzinfo=timezone.utc)) == "2026-09-13"


def test_naive_datetime_is_treated_as_utc():
    assert day_key(datetime(2026, 9, 13, 2, 0)) == "2026-09-12"


def test_winter_offset_differs_from_summer():
    """EST is UTC-5, EDT is UTC-4; the boundary moves and must move with it."""
    assert day_key(datetime(2026, 1, 13, 4, 30, tzinfo=timezone.utc)) == "2026-01-12"
    assert day_key(datetime(2026, 7, 13, 3, 30, tzinfo=timezone.utc)) == "2026-07-12"


def test_iso_z_format():
    out = iso_z(datetime(2026, 9, 13, 6, 4, 11, 123456, tzinfo=timezone.utc))
    assert out == "2026-09-13T06:04:11Z"


def test_today_key_is_a_date_string():
    assert len(today_key()) == 10 and today_key().count("-") == 2
