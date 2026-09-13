"""Agent 1 — harvest. BUILD_SPEC §5.

Queries are built from the entity table rather than hand-written, so adding a GP
to entities.yaml automatically adds its queries, its rule-layer matching and its
prompt scope in one edit.

Isolation is the rule that matters operationally: every fetcher is wrapped, a
failure logs a warning and contributes nothing, and one broken feed never fails
the run.
"""

from __future__ import annotations

import logging
import re
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .config import Entities, Settings, edgar_user_agent, load_entities, load_settings, now_utc

log = logging.getLogger("pipeline.sources")

@dataclass
class Query:
    text: str
    origin: str          # gp:blue-owl | sector:clo | theme:clo-issuance | vehicle:FSK
    region_bias: str = "US"


# --------------------------------------------------------------------------
# Query construction (§5.1)
# --------------------------------------------------------------------------

EU_HINT = "(Europe OR European OR UK OR Germany OR France)"


def build_queries(entities: Entities | None = None, settings: Settings | None = None) -> list[Query]:
    ent = entities or load_entities()
    cfg = settings or load_settings()
    qual = cfg.get("query_credit_qualifier",
                   "(credit OR lending OR loan OR BDC OR fund OR CLO OR debt OR financing)")
    queries: list[Query] = []

    # Per GP: canonical name + credit qualifier.
    for gp in ent.gps:
        queries.append(Query(f'"{gp.name}" {qual}', f"gp:{gp.id}"))
        # A weak alias needs two context terms to be worth searching on at all.
        for alias in gp.weak_aliases:
            ctx = gp.context_terms[:2]
            if ctx and alias.lower() != gp.name.lower():
                ctx_str = " ".join(f'"{c}"' for c in ctx)
                queries.append(Query(f'"{alias}" {ctx_str} {qual}', f"gp:{gp.id}"))
        # Per vehicle / ticker: distinctive enough to stand alone.
        for vehicle in gp.vehicles:
            if len(vehicle) > 5:                       # skip bare tickers; too noisy alone
                queries.append(Query(f'"{vehicle}"', f"vehicle:{gp.id}"))

    # Per sub-sector.
    for sector in ent.sectors:
        kws = [k for k in sector.keywords[:4]]
        if not kws:
            continue
        kw_str = " OR ".join(f'"{k}"' for k in kws)
        queries.append(Query(f"({kw_str}) (investor OR fund OR lender OR loan)",
                             f"sector:{sector.id}"))
        # Europe-specific variant so European stories are actually reachable (§5.4).
        queries.append(Query(f"({kw_str}) {EU_HINT}", f"sector:{sector.id}", region_bias="Europe"))

    # Per macro theme.
    for theme in ent.themes:
        queries.append(Query(theme["query"], f"theme:{theme['id']}",
                             region_bias=theme.get("region_bias", "US")))

    max_q = cfg.limits.get("max_queries", 60)
    if len(queries) > max_q:
        # Keep GP and theme coverage complete; trim the long tail of vehicle queries.
        primary = [q for q in queries if not q.origin.startswith("vehicle:")]
        secondary = [q for q in queries if q.origin.startswith("vehicle:")]
        queries = (primary + secondary)[:max_q]
    return queries


# --------------------------------------------------------------------------
# HTTP plumbing
# --------------------------------------------------------------------------

def _requests():
    import requests
    return requests


def _get(url: str, *, headers: dict | None = None, timeout: int = 20,
         retries: int = 2, backoff: float = 2.0):
    requests = _requests()
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers=headers or {}, timeout=timeout)
            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                raise RuntimeError(f"HTTP {resp.status_code}")
            resp.raise_for_status()
            return resp
        except Exception as exc:                       # noqa: BLE001 - deliberate catch-all
            last = exc
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
    raise RuntimeError(f"GET failed: {url[:120]} ({last})")


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, time.struct_time):
        return datetime(*value[:6], tzinfo=timezone.utc)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        from dateutil import parser as dateparser
        dt = dateparser.parse(str(value))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:                                  # noqa: BLE001
        return None


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def clean_text(raw: str | None, limit: int = 1200) -> str:
    if not raw:
        return ""
    import html
    text = html.unescape(_TAG_RE.sub(" ", str(raw)))
    return _WS_RE.sub(" ", text).strip()[:limit]


# --------------------------------------------------------------------------
# Fetchers. Each returns a list of raw item dicts and raises on failure; the
# caller isolates them.
# --------------------------------------------------------------------------

def fetch_rss(url: str, source_name: str, priority: int, timeout: int = 20,
              retries: int = 2) -> list[dict]:
    import feedparser

    resp = _get(url, headers={"User-Agent": load_settings().get("http", {}).get(
        "user_agent", "credit-brief/1.0")}, timeout=timeout, retries=retries)
    feed = feedparser.parse(resp.content)
    items = []
    for entry in feed.entries:
        link = entry.get("link") or ""
        if not link:
            continue
        published = _parse_time(entry.get("published_parsed") or entry.get("updated_parsed")
                                or entry.get("published") or entry.get("updated"))
        items.append({
            "url": link,
            "title": clean_text(entry.get("title"), 400),
            "summary": clean_text(entry.get("summary") or entry.get("description"), 1200),
            "source": _entry_source(entry, source_name),
            "priority": priority,
            "published_at": published,
            "origin": f"rss:{source_name}",
        })
    return items


def _entry_source(entry: Any, fallback: str) -> str:
    """Aggregator feeds carry the real publisher in `source` or after a dash."""
    src = entry.get("source")
    if isinstance(src, dict) and src.get("title"):
        return clean_text(src["title"], 80)
    title = entry.get("title") or ""
    if " - " in title:
        tail = title.rsplit(" - ", 1)[-1].strip()
        if 2 < len(tail) < 45:
            return clean_text(tail, 80)
    return fallback


def fetch_news_search(endpoint: str, query: str, source_name: str, priority: int,
                      timeout: int = 20) -> list[dict]:
    url = endpoint.format(query=urllib.parse.quote_plus(query))
    # retries=0: one of sixty queries failing costs almost nothing, but sixty
    # queries each retrying twice with backoff can cost the whole job timeout.
    items = fetch_rss(url, source_name, priority, timeout, retries=0)
    for it in items:
        # Aggregator titles are "Headline - Publisher"; strip the suffix.
        if " - " in it["title"]:
            head, tail = it["title"].rsplit(" - ", 1)
            if 2 < len(tail) < 45:
                it["title"] = head.strip()
        it["origin"] = f"search:{source_name}"
        it["query"] = query
    return items


# 8-K item codes worth naming. The code alone means nothing to a model; the
# label is what tells Agent 2 that a filing is a credit event rather than a
# routine disclosure.
EDGAR_8K_ITEMS = {
    "1.01": "entry into a material definitive agreement",
    "1.02": "termination of a material definitive agreement",
    "1.03": "bankruptcy or receivership",
    "2.02": "results of operations and financial condition",
    "2.03": "creation of a direct financial obligation",
    "2.04": "acceleration or increase of a financial obligation",
    "2.06": "material impairment",
    "3.03": "material modification to the rights of security holders",
    "4.02": "non-reliance on previously issued financial statements",
    "5.02": "departure or appointment of directors or principal officers",
    "7.01": "regulation FD disclosure",
    "8.01": "other events",
    "9.01": "financial statements and exhibits",
}


def fetch_edgar(entity_names: list[str], forms: list[str], priority: int,
                start: str, end: str, timeout: int = 20) -> list[dict]:
    """SEC EDGAR full-text search.

    Free, structured and not paywalled — the highest-trust source in the list.
    Requires a descriptive User-Agent with a contact address (EDGAR_USER_AGENT).

    One filing produces several documents (the form plus its exhibits). They are
    collapsed here, on the accession number, keeping the primary document, so a
    single 8-K does not arrive as three near-identical items.
    """
    ua = edgar_user_agent()
    headers = {"User-Agent": ua, "Accept": "application/json"}
    by_accession: dict[str, dict] = {}

    for name in entity_names:
        params = {"q": f'"{name}"', "forms": ",".join(forms),
                  "startdt": start, "enddt": end}
        url = "https://efts.sec.gov/LATEST/search-index?" + urllib.parse.urlencode(params)
        try:
            payload = _get(url, headers=headers, timeout=timeout).json()
        except Exception as exc:                        # noqa: BLE001
            log.warning("edgar query failed for %s: %s", name, exc)
            continue

        for hit in (payload.get("hits", {}).get("hits") or []):
            src = hit.get("_source", {})
            adsh = src.get("adsh") or (hit.get("_id") or "").split(":")[0]
            cik = (src.get("ciks") or [""])[0]
            doc = (hit.get("_id") or "").split(":")[-1]
            if not (cik and adsh and doc):
                continue

            try:
                sequence = int(src.get("sequence") or 99)
            except (TypeError, ValueError):
                sequence = 99

            prior = by_accession.get(adsh)
            if prior is not None and prior["_sequence"] <= sequence:
                continue                                 # keep the primary document

            display = (src.get("display_names") or [name])[0]
            company = display.split("  (")[0].strip()
            form = src.get("form") or (src.get("root_forms") or ["filing"])[0]
            items = [EDGAR_8K_ITEMS.get(i, i) for i in (src.get("items") or [])]

            article = "an" if form[:1].upper() in "AEFHILMNORSX8" else "a"
            detail = f"{company} filed {article} {form} with the SEC"
            if items:
                detail += ": " + "; ".join(items)
            if src.get("period_ending"):
                detail += f" (period ending {src['period_ending']})"
            detail += "."

            by_accession[adsh] = {
                "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                       f"{adsh.replace('-', '')}/{doc}",
                "title": f"{company} files {form}"
                         + (f" — {items[0]}" if items else ""),
                "summary": detail,
                "source": "SEC EDGAR",
                "priority": priority,
                "published_at": _parse_time(src.get("file_date")),
                "origin": "edgar",
                "query": name,
                "_sequence": sequence,
            }
        time.sleep(0.2)                                  # EDGAR asks for <10 req/s

    out = []
    for item in by_accession.values():
        item.pop("_sequence", None)
        out.append(item)
    return out


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def harvest(entities: Entities | None = None, settings: Settings | None = None,
            lookback_hours: int | None = None,
            errors: list[str] | None = None) -> tuple[list[dict], dict[str, int]]:
    """Run every enabled source. Returns (items, stats).

    No exception from a single source escapes this function.
    """
    ent = entities or load_entities()
    cfg = settings or load_settings()
    errors = errors if errors is not None else []
    hours = lookback_hours if lookback_hours is not None else cfg.get("lookback_hours", 8)
    timeout = cfg.limits.get("feed_timeout", 20)

    queries = build_queries(ent, cfg)
    log.info("built %d queries", len(queries))

    raw: list[dict] = []
    stats = {"queries": len(queries), "sources_ok": 0, "sources_failed": 0}
    empty: list[str] = []

    def run(label: str, fn: Callable[[], list[dict]]) -> None:
        try:
            got = fn()
            raw.extend(got)
            stats["sources_ok"] += 1
            if not got:
                empty.append(label)
            log.info("%s -> %d items", label, len(got))
        except Exception as exc:                        # noqa: BLE001 - isolation is the point
            stats["sources_failed"] += 1
            msg = f"{label} unavailable: {type(exc).__name__}: {str(exc)[:160]}"
            errors.append(msg)
            log.warning(msg)

    for source in cfg.sources:
        stype = source.get("type")
        priority = int(source.get("priority", 1))
        name = source.get("name", source["id"])

        if stype == "rss":
            run(f"feed {source['id']}",
                lambda s=source, n=name, p=priority: fetch_rss(s["url"], n, p, timeout))

        elif stype == "edgar":
            end = now_utc().date()
            start = end - timedelta(days=max(2, hours // 24 + 2))
            run("edgar",
                lambda s=source, p=priority: fetch_edgar(
                    s.get("entities", []), s.get("forms", ["8-K"]), p,
                    start.isoformat(), end.isoformat(), timeout))

        elif stype in ("bing_news", "google_news"):
            endpoint = source["endpoint"]
            for q in queries:
                run(f"{source['id']} <{q.origin}>",
                    lambda e=endpoint, q=q, n=name, p=priority:
                        _tag(fetch_news_search(e, q.text, n, p, timeout), q))

    # Only report feeds, not individual search queries: a single query returning
    # nothing is normal, a publisher feed returning nothing is a dead source.
    dead = sorted({lbl for lbl in empty if lbl.startswith(("feed ", "edgar"))})
    if dead:
        stats["empty_sources"] = dead
        log.warning("sources returned nothing: %s", ", ".join(dead))

    stats["fetched"] = len(raw)
    items = [it for it in raw if it.get("url")]
    items = _apply_window(items, hours)
    stats["in_window"] = len(items)
    return items, stats


def _tag(items: list[dict], query: Query) -> list[dict]:
    for it in items:
        it["query_origin"] = query.origin
        it.setdefault("region_bias", query.region_bias)
    return items


def _apply_window(items: list[dict], hours: int) -> list[dict]:
    """Keep items published inside the lookback window.

    Items with no parseable publish time are treated as "now" (§5.3) — an
    undated item from a live feed is almost always fresh, and dropping it would
    silently lose whole publishers whose feeds omit timestamps.
    """
    cutoff = now_utc() - timedelta(hours=hours)
    out = []
    for it in items:
        published = it.get("published_at")
        if published is None:
            it["published_at"] = now_utc()
            it["undated"] = True
            out.append(it)
            continue
        if isinstance(published, str):
            published = _parse_time(published) or now_utc()
            it["published_at"] = published
        if published >= cutoff:
            out.append(it)
    return out


# --------------------------------------------------------------------------
# Article body extraction (Agent 3 and the summariser both need this, §6.3/§6.4)
# --------------------------------------------------------------------------

def fetch_article_body(url: str, timeout: int | None = None) -> str:
    """Best-effort body extraction. Returns "" on any failure — never raises."""
    cfg = load_settings()
    timeout = timeout or cfg.limits.get("article_fetch_timeout", 20)
    try:
        import trafilatura
        resp = _get(url, headers={"User-Agent": cfg.get("http", {}).get(
            "user_agent", "credit-brief/1.0")}, timeout=timeout, retries=1)
        text = trafilatura.extract(resp.text, include_comments=False,
                                   include_tables=False, no_fallback=False)
        return clean_text(text, 12000) if text else ""
    except Exception as exc:                            # noqa: BLE001
        log.debug("body extraction failed for %s: %s", url[:80], exc)
        return ""
