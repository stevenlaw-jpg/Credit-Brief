"""Configuration loading and the US-Eastern date logic.

BUILD_SPEC §5.3: the dashboard date is the US Eastern calendar day of publication.
The readership is in Hong Kong and the news is American; bucketing by HKT would
leave "today" nearly empty at the Hong Kong open while the whole US session sat
under a different date.
"""

from __future__ import annotations

import functools
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
PROMPT_DIR = CONFIG_DIR / "prompts"
# GitHub Pages will serve a branch folder only from "/" or "/docs", so the
# site lives in docs/ rather than site/. Serving straight from the branch
# means the page keeps working even when a pipeline run fails.
SITE_DIR = ROOT / "docs"
DATA_DIR = SITE_DIR / "data"


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@dataclass(frozen=True)
class GP:
    id: str
    name: str
    name_zh: str
    name_short: str = ""
    name_short_zh: str = ""
    strong_aliases: list[str] = field(default_factory=list)
    weak_aliases: list[str] = field(default_factory=list)
    context_terms: list[str] = field(default_factory=list)
    exclude_terms: list[str] = field(default_factory=list)
    vehicles: list[str] = field(default_factory=list)
    sectors: list[str] = field(default_factory=list)
    credit_scope: str = ""
    confidence: str = "high"


@dataclass(frozen=True)
class Sector:
    id: str
    name: str
    name_zh: str
    keywords: list[str] = field(default_factory=list)
    primary_gps: list[str] = field(default_factory=list)
    requires_credit_context: bool = False
    scope: str = ""
    confidence: str = "high"


@dataclass(frozen=True)
class Entities:
    gps: list[GP]
    sectors: list[Sector]
    themes: list[dict[str, Any]]
    credit_context_terms: list[str]
    hard_event_terms: list[str]
    regions: dict[str, list[str]]

    @property
    def gp_by_id(self) -> dict[str, GP]:
        return {g.id: g for g in self.gps}

    @property
    def sector_by_id(self) -> dict[str, Sector]:
        return {s.id: s for s in self.sectors}


@dataclass(frozen=True)
class Settings:
    raw: dict[str, Any]

    def __getitem__(self, key: str) -> Any:
        return self.raw[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw.get(key, default)

    @property
    def timezone(self) -> str:
        return self.raw.get("timezone", "America/New_York")

    @property
    def limits(self) -> dict[str, Any]:
        return self.raw.get("limits", {})

    @property
    def models(self) -> dict[str, Any]:
        return self.raw.get("models", {})

    @property
    def sources(self) -> list[dict[str, Any]]:
        return [s for s in self.raw.get("sources", []) if s.get("enabled", True)]


@functools.lru_cache(maxsize=1)
def load_entities(path: str | None = None) -> Entities:
    data = _read_yaml(Path(path) if path else CONFIG_DIR / "entities.yaml")
    gps = [
        GP(
            id=g["id"],
            name=g["name"],
            name_zh=g.get("name_zh", g["name"]),
            name_short=g.get("name_short") or g["name"],
            name_short_zh=g.get("name_short_zh") or g.get("name_zh") or g["name"],
            strong_aliases=list(g.get("strong_aliases") or []),
            weak_aliases=list(g.get("weak_aliases") or []),
            context_terms=list(g.get("context_terms") or []),
            exclude_terms=list(g.get("exclude_terms") or []),
            vehicles=list(g.get("vehicles") or []),
            sectors=list(g.get("sectors") or []),
            credit_scope=(g.get("credit_scope") or "").strip(),
            confidence=g.get("confidence", "high"),
        )
        for g in data.get("gps", [])
    ]
    sectors = [
        Sector(
            id=s["id"],
            name=s["name"],
            name_zh=s.get("name_zh", s["name"]),
            keywords=list(s.get("keywords") or []),
            primary_gps=list(s.get("primary_gps") or []),
            requires_credit_context=bool(s.get("requires_credit_context", False)),
            scope=(s.get("scope") or "").strip(),
            confidence=s.get("confidence", "high"),
        )
        for s in data.get("sectors", [])
    ]
    return Entities(
        gps=gps,
        sectors=sectors,
        themes=list(data.get("themes") or []),
        credit_context_terms=list(data.get("credit_context_terms") or []),
        hard_event_terms=list(data.get("hard_event_terms") or []),
        regions={k: list(v) for k, v in (data.get("regions") or {}).items()},
    )


@functools.lru_cache(maxsize=1)
def load_settings(path: str | None = None) -> Settings:
    return Settings(_read_yaml(Path(path) if path else CONFIG_DIR / "settings.yaml"))


@functools.lru_cache(maxsize=8)
def load_prompt(name: str) -> str:
    return (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------- date logic

def market_tz(settings: Settings | None = None) -> ZoneInfo:
    return ZoneInfo((settings or load_settings()).timezone)


def day_key(published_at_utc: datetime, settings: Settings | None = None) -> str:
    """The dashboard date for an instant: its US-Eastern calendar day.

    An article published 21:30 ET on 12 Sept and one published 02:00 UTC on
    13 Sept are the same US session and must land on the same key.
    """
    if published_at_utc.tzinfo is None:
        published_at_utc = published_at_utc.replace(tzinfo=timezone.utc)
    return published_at_utc.astimezone(market_tz(settings)).date().isoformat()


def today_key(settings: Settings | None = None) -> str:
    return day_key(datetime.now(timezone.utc), settings)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_z(dt: datetime) -> str:
    """UTC ISO-8601 with a trailing Z and no microseconds."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


_ENV_LOADED = False


def load_dotenv(path: Path | None = None) -> None:
    """Read `.env` from the repo root into the environment, for local runs.

    A real environment variable always wins, so this never overrides what CI
    supplies. `.env` is in `.gitignore`; it exists so a key can live somewhere
    untracked instead of being pasted into a file git would commit. Values are
    never logged.
    """
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True
    env_path = path or (ROOT / ".env")
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            name = name.strip()
            value = value.strip().strip('"').strip("'")
            if name and name not in os.environ:
                os.environ[name] = value
    except OSError:
        pass


def api_key() -> str | None:
    """The Anthropic key, read from the environment only.

    SECURITY (BUILD_SPEC §2): it must never appear in the repo, in committed
    JSON, in logs or in anything the site serves. In CI it comes from GitHub
    Actions Secrets; locally it comes from the shell or from an untracked `.env`.
    """
    load_dotenv()
    key = os.environ.get("ANTHROPIC_API_KEY")
    return key.strip() if key else None


def edgar_user_agent(settings: Settings | None = None) -> str:
    load_dotenv()
    s = settings or load_settings()
    env = s.get("http", {}).get("edgar_user_agent_env", "EDGAR_USER_AGENT")
    return os.environ.get(env) or s.get("http", {}).get("user_agent", "credit-brief/1.0")
