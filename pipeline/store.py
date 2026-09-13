"""JSON persistence: day files, the index, the seen-set, retention, the run log.

BUILD_SPEC §7. Everything here is plain JSON committed to the repo — tens of KB
per day, and git history is a free audit trail.

Two invariants this module is responsible for:
  * items in a day file are sorted by `published_at` descending, because the
    frontend renders in file order and never re-sorts (§7.1, §8.1);
  * running the same run twice produces no duplicate items and no duplicate API
    spend (§7.4), which is what the seen-set is for.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import date as _date, timedelta
from pathlib import Path
from typing import Any

from .config import (DATA_DIR, Entities, iso_z, load_entities, load_settings, now_utc,
                     today_key)

DAY_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")

# Keys persisted on an item in a day file. Anything not listed here is internal
# to the pipeline and must not be written to a file the site serves.
ITEM_FIELDS = (
    "id", "title_en", "title_zh", "summary_en", "summary_zh", "url", "also_urls",
    "source", "also_sources", "published_at", "gp_ids", "sector_ids",
    "exposure_gp_ids", "region", "importance", "grounded_on", "rescued_by_agent3",
    "reason", "affected_exposure",
)

# How many inferred-exposure managers to show before the row becomes noise.
MAX_EXPOSURE_GPS = 4


def _atomic_write(path: Path, text: str) -> None:
    """Write via a temp file in the same directory, then rename.

    A run that is killed mid-write must not leave a truncated JSON file that the
    site would fail to parse.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _write_json(path: Path, payload: Any) -> None:
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n")


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


class Store:
    def __init__(self, data_dir: Path | str | None = None) -> None:
        self.dir = Path(data_dir) if data_dir else DATA_DIR
        self.dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------- day files
    def day_path(self, date: str) -> Path:
        return self.dir / f"{date}.json"

    def read_day(self, date: str) -> dict[str, Any]:
        return _read_json(self.day_path(date), {"date": date, "updated_at": None, "items": []})

    @staticmethod
    def exposure_gp_ids(item: dict, entities: Entities) -> list[str]:
        """Managers exposed to this item's sub-sectors but NOT named in the story.

        A market-level story — "CLO AAA spreads tighten", "European direct lending
        deal count falls" — names no manager, so on its own it leaves the reader to
        remember which of his holdings it touches. The GP x sub-sector matrix already
        knows; this surfaces it.

        Returns nothing when a manager IS named: the story has already answered the
        question, and padding the row with firms that merely operate in the sector
        would bury the one that is actually in the news.
        """
        if item.get("gp_ids"):
            return []
        by_id = entities.sector_by_id
        out: list[str] = []
        for sector_id in item.get("sector_ids") or []:
            sector = by_id.get(sector_id)
            if not sector:
                continue
            for gp_id in sector.primary_gps:          # ordered by relevance in the YAML
                if gp_id not in out:
                    out.append(gp_id)
        return out[:MAX_EXPOSURE_GPS]

    def merge_day(self, date: str, new_items: list[dict]) -> dict[str, Any]:
        """Merge by `id`; a re-run updates in place rather than appending."""
        day = self.read_day(date)
        ent = load_entities()
        existing = {it["id"]: it for it in day.get("items", []) if it.get("id")}
        for item in new_items:
            item = {**item, "exposure_gp_ids": self.exposure_gp_ids(item, ent)}
            clean = {k: item.get(k) for k in ITEM_FIELDS if item.get(k) is not None}
            clean.setdefault("also_urls", [])
            clean.setdefault("rescued_by_agent3", False)
            existing[clean["id"]] = {**existing.get(clean["id"], {}), **clean}

        items = sorted(
            existing.values(),
            key=lambda it: (it.get("published_at") or "", it.get("id") or ""),
            reverse=True,
        )
        day = {"date": date, "updated_at": iso_z(now_utc()), "items": items}
        _write_json(self.day_path(date), day)
        return day

    def dates(self) -> list[str]:
        out = []
        for p in self.dir.glob("*.json"):
            m = DAY_FILE_RE.match(p.name)
            if m:
                out.append(m.group(1))
        return sorted(out, reverse=True)

    # ----------------------------------------------------------------- index
    def write_index(self, entities: Entities | None = None) -> dict[str, Any]:
        ent = entities or load_entities()
        settings = load_settings()
        dates = []
        for date in self.dates():
            day = self.read_day(date)
            dates.append({
                "date": date,
                "count": len(day.get("items", [])),
                "updated_at": day.get("updated_at"),
            })
        index = {
            "generated_at": iso_z(now_utc()),
            "timezone": settings.timezone,
            "retention_days": settings.get("retention_days", 90),
            "update_interval_hours": settings.get("update_interval_hours", 3),
            "default_language": settings.get("default_language", "en"),
            "names": {
                # Tag labels only. The full name lives on the GP and goes into
                # the prompts; a tag is scanned, so it uses the short form.
                "gps": {g.id: {"en": g.name_short, "zh": g.name_short_zh}
                        for g in ent.gps},
                "sectors": {s.id: {"en": s.name, "zh": s.name_zh} for s in ent.sectors},
            },
            "dates": dates,
        }
        _write_json(self.dir / "index.json", index)
        return index

    # -------------------------------------------------------------- seen-set
    def read_seen(self) -> dict[str, dict[str, str]]:
        return _read_json(self.dir / "_seen.json", {})

    def write_seen(self, seen: dict[str, dict[str, str]]) -> None:
        _write_json(self.dir / "_seen.json", seen)

    def mark_seen(self, seen: dict, item_id: str, date: str) -> None:
        if item_id not in seen:
            seen[item_id] = {"date": date, "first_seen": iso_z(now_utc())}

    # -------------------------------------------------------------- run log
    def append_run(self, record: dict[str, Any]) -> None:
        path = self.dir / "_runs.jsonl"
        line = json.dumps(record, ensure_ascii=False, sort_keys=False)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def read_runs(self, limit: int | None = None) -> list[dict[str, Any]]:
        path = self.dir / "_runs.jsonl"
        if not path.exists():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows[-limit:] if limit else rows

    # ------------------------------------------------------------ retention
    def prune(self, retention_days: int | None = None, today: str | None = None) -> dict[str, Any]:
        """Delete day files outside the window and prune the seen-set to match."""
        settings = load_settings()
        days = retention_days if retention_days is not None else settings.get("retention_days", 90)
        ref = (_date.fromisoformat(today) if today
               else _date.fromisoformat(today_key(settings)))
        cutoff = (ref - timedelta(days=days - 1)).isoformat()

        pruned_days = []
        for date in self.dates():
            if date < cutoff:
                self.day_path(date).unlink(missing_ok=True)
                pruned_days.append(date)

        seen = self.read_seen()
        kept = {k: v for k, v in seen.items() if (v.get("date") or "") >= cutoff}
        pruned_seen = len(seen) - len(kept)
        if pruned_seen:
            self.write_seen(kept)

        return {"pruned_days": pruned_days, "pruned_seen": pruned_seen, "cutoff": cutoff}

    # --------------------------------------------------------- EU share (§5.4)
    def eu_share(self, window_days: int = 14, today: str | None = None) -> float | None:
        """Rolling European share of published items, measured on output.

        Returns None when there is nothing to measure, so an empty window is not
        reported as 0% Europe.
        """
        ref = (_date.fromisoformat(today) if today else _date.fromisoformat(today_key()))
        start = (ref - timedelta(days=window_days - 1)).isoformat()
        total = eu = 0
        for date in self.dates():
            if date < start:
                continue
            for item in self.read_day(date).get("items", []):
                total += 1
                if item.get("region") == "Europe":
                    eu += 1
        return round(eu / total, 4) if total else None

    def region_mix(self, window_days: int = 14, today: str | None = None) -> dict[str, int]:
        ref = (_date.fromisoformat(today) if today else _date.fromisoformat(today_key()))
        start = (ref - timedelta(days=window_days - 1)).isoformat()
        mix: dict[str, int] = {}
        for date in self.dates():
            if date < start:
                continue
            for item in self.read_day(date).get("items", []):
                r = item.get("region") or "Global"
                mix[r] = mix.get(r, 0) + 1
        return mix
