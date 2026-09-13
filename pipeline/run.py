"""Orchestration and CLI. BUILD_SPEC §2, §6.5, §7.

The funnel, in order:

    harvest -> window -> dedupe -> rule layer -> seen-set
            -> Agent 2 -> keep / discard pool
            -> Agent 3 shortlist -> rescue
            -> summarise -> day files -> index -> retention -> run log

Every stage records its count. Those counts are the evidence base for the
write-up, which is required to quote real numbers rather than adjectives (§11.1).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import agents
from .config import day_key, iso_z, load_entities, load_settings, now_utc
from .dedupe import dedupe
from .entities import match_item
from .sources import fetch_article_body, harvest
from .store import Store

log = logging.getLogger("pipeline.run")


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("trafilatura").setLevel(logging.ERROR)


# --------------------------------------------------------------------------

def load_fixture(path: str) -> list[dict]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    items = payload["items"] if isinstance(payload, dict) else payload
    out = []
    for it in items:
        it = dict(it)
        it.setdefault("priority", 1)
        it.setdefault("source", "Fixture")
        it["published_at"] = _as_dt(it.get("published_at"))
        it["url"] = it.get("url") or f"https://fixture.test/{it.get('id','x')}"
        out.append(it)
    return out


def _as_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            from dateutil import parser
            dt = parser.parse(value)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:                                   # noqa: BLE001
            pass
    return now_utc()


def _print_funnel(stats: dict) -> None:
    order = ["queries", "fetched", "in_window", "after_dedupe", "candidates", "new",
             "agent2_kept", "agent3_shortlist", "agent3_rescued", "summarised"]
    width = max(len(k) for k in order)
    print("\n  funnel")
    print("  " + "-" * (width + 10))
    for key in order:
        if key in stats:
            print(f"  {key:<{width}}  {stats[key]:>6}")
    print()


# --------------------------------------------------------------------------

def run(*, mock_llm: bool = False, fixture: str | None = None, dry_run: bool = False,
        lookback_hours: int | None = None, data_dir: str | None = None,
        max_items: int | None = None, no_prune: bool = False) -> dict:
    ent = load_entities()
    cfg = load_settings()
    store = Store(data_dir)
    errors: list[str] = []
    started = now_utc()

    stats: dict[str, Any] = {}

    # ---- Agent 1: harvest ------------------------------------------------
    if fixture:
        items = load_fixture(fixture)
        stats.update({"queries": 0, "fetched": len(items), "in_window": len(items)})
        log.info("loaded %d fixture items from %s", len(items), fixture)
    else:
        items, hstats = harvest(ent, cfg, lookback_hours, errors)
        stats.update(hstats)

    # ---- normalise + dedupe ---------------------------------------------
    for it in items:
        if isinstance(it.get("published_at"), datetime):
            it["published_at"] = iso_z(it["published_at"])
    items = dedupe(items)
    stats["after_dedupe"] = len(items)

    # ---- rule layer ------------------------------------------------------
    candidates = []
    for it in items:
        rule = match_item(it, ent)
        it["_rule"] = rule
        if rule.is_candidate:
            candidates.append(it)
    stats["candidates"] = len(candidates)
    log.info("rule layer: %d/%d candidates", len(candidates), len(items))

    if dry_run:
        stats["new"] = 0
        _print_funnel(stats)
        for it in candidates[:40]:
            r = it["_rule"]
            print(f"  · {it.get('title','')[:78]:80s} gp={r.gp_ids} sec={r.sector_ids}")
        return stats

    # ---- seen-set: never classify or summarise the same URL twice (§6.5) --
    seen = store.read_seen()
    fresh = [it for it in candidates if it["id"] not in seen]
    stats["new"] = len(fresh)
    log.info("seen-set: %d new of %d candidates", len(fresh), len(candidates))

    cap = max_items if max_items is not None else cfg.limits.get("max_new_items_per_run", 20)

    if not fresh:
        return _finish(store, stats, errors, started, [], "", ent, no_prune)

    llm = agents.LLM(cfg, mock=mock_llm)

    # ---- Agent 2 ---------------------------------------------------------
    decisions = agents.agent2_filter(fresh, llm, ent, cfg)
    by_id = {d["id"]: d for d in decisions}

    kept: list[tuple[dict, dict]] = []
    pool: list[tuple[dict, dict]] = []
    for it in fresh:
        d = by_id.get(it["id"])
        if d and agents.keep_decision(d):
            kept.append((it, d))
        elif d:
            pool.append((it, d))
    stats["agent2_kept"] = len(kept)
    stats["agent2_discarded"] = len(pool)
    log.info("agent 2: kept %d, discarded %d", len(kept), len(pool))

    # ---- Agent 3 ---------------------------------------------------------
    shortlist = agents.build_shortlist(pool, ent, cfg.limits.get("agent3_max", 25))
    stats["agent3_shortlist"] = len(shortlist)

    # Agent 3's advantage is the article body — fetch it before asking (§6.3).
    if shortlist and not mock_llm:
        for item, _, _, _ in shortlist:
            if not item.get("body"):
                item["body"] = fetch_article_body(item["url"])

    verdicts, cluster_note = agents.agent3_second_look(shortlist, llm, ent, cfg)
    rescued = []
    rescue_log = []
    short_by_id = {item["id"]: (item, d) for item, d, _, _ in shortlist}
    for v in verdicts:
        if not v.get("rescue"):
            continue
        entry = short_by_id.get(str(v.get("id")))
        if not entry:
            continue
        item, original = entry
        merged = {
            "id": item["id"],
            "relevant": True,
            "credit_related": True,
            "importance": int(v.get("importance", 2)),
            "gp_ids": v.get("gp_ids", []),
            "sector_ids": v.get("sector_ids", []),
            "region": v.get("region") or original.get("region") or "US",
            "reason": v.get("reason", ""),
            "affected_exposure": v.get("affected_exposure", ""),
            "rescued_by_agent3": True,
        }
        if not (merged["gp_ids"] or merged["sector_ids"]):
            log.info("ignoring rescue of %s: no exposure named", item["id"])
            continue
        rescued.append((item, merged))
        rescue_log.append({
            "id": item["id"], "title": item.get("title", "")[:140],
            "source": item.get("source"), "importance": merged["importance"],
            "gp_ids": merged["gp_ids"], "sector_ids": merged["sector_ids"],
            "affected_exposure": merged["affected_exposure"][:300],
            "reason": merged["reason"][:300],
            "agent2_reason": original.get("reason", "")[:200],
        })
    stats["agent3_rescued"] = len(rescued)
    if cluster_note:
        log.info("agent 3 cluster note: %s", cluster_note)

    # Guard rail (§6.3): a high rescue rate means Agent 2's threshold is mis-set.
    if shortlist:
        ratio = len(rescued) / len(shortlist)
        stats["agent3_rescue_ratio"] = round(ratio, 3)
        warn_at = cfg.limits.get("agent3_rescue_warn_ratio", 0.40)
        if ratio > warn_at:
            recent = [r.get("agent3_rescue_ratio", 0) for r in store.read_runs(2)]
            if len(recent) >= 2 and all(r > warn_at for r in recent):
                msg = (f"LOUD WARNING: agent 3 rescued {ratio:.0%} of its shortlist for a "
                       f"third consecutive run. Agent 2's threshold is mis-set — a human "
                       f"should look at config/prompts/agent2_filter.md.")
                log.error(msg)
                errors.append(msg)

    # ---- summarise -------------------------------------------------------
    final = kept + rescued
    final.sort(key=lambda pair: pair[0].get("published_at") or "", reverse=True)
    deferred: set[str] = set()
    if len(final) > cap:
        log.info("capping %d items at max_new_items_per_run=%d; %d deferred to the next run",
                 len(final), cap, len(final) - cap)
        deferred = {item["id"] for item, _ in final[cap:]}
        final = final[:cap]
    stats["deferred"] = len(deferred)

    written: list[dict] = []
    for item, decision in final:
        if not mock_llm and not item.get("body"):
            item["body"] = fetch_article_body(item["url"])
        summary = agents.summarise(item, llm, ent, cfg)
        record = {
            "id": item["id"],
            "title_en": summary.get("title_en") or item.get("title", ""),
            "title_zh": summary.get("title_zh") or item.get("title", ""),
            "summary_en": summary.get("summary_en", ""),
            "summary_zh": summary.get("summary_zh", ""),
            "url": item["url"],
            "also_urls": item.get("also_urls", []),
            "source": item.get("source", ""),
            "also_sources": item.get("also_sources", []),
            "published_at": item.get("published_at"),
            "gp_ids": decision.get("gp_ids", []),
            "sector_ids": decision.get("sector_ids", []),
            "region": decision.get("region", "US"),
            "importance": int(decision.get("importance", 2)),
            "grounded_on": summary.get("grounded_on", "snippet"),
            "rescued_by_agent3": bool(decision.get("rescued_by_agent3")),
            "reason": decision.get("reason", ""),
        }
        if decision.get("affected_exposure"):
            record["affected_exposure"] = decision["affected_exposure"]
        written.append(record)
    stats["summarised"] = len(written)

    # ---- persist ---------------------------------------------------------
    by_date: dict[str, list[dict]] = {}
    for record in written:
        key = day_key(_as_dt(record["published_at"]), cfg)
        by_date.setdefault(key, []).append(record)
    for date, records in sorted(by_date.items()):
        store.merge_day(date, records)
        log.info("wrote %d items to %s", len(records), date)
    stats["days_touched"] = len(by_date)

    # Every item Agent 2 or Agent 3 saw is marked seen, kept or not — the point of
    # the seen-set is that no item is ever paid for twice.
    #
    # The exception is an item that passed the filter but lost its place to the
    # per-run cap. Marking that seen would discard it permanently, and the cap
    # binds precisely on the busiest days, when losing news is least acceptable.
    # It is left unseen so the next run — three hours later, still inside the
    # eight-hour window — picks it up. The re-classification costs one Haiku
    # batch; a silently dropped Tier 1 item costs the reader's trust.
    for it in fresh:
        if it["id"] in deferred:
            continue
        store.mark_seen(seen, it["id"], day_key(_as_dt(it.get("published_at")), cfg))
    store.write_seen(seen)

    stats["usage"] = llm.usage.as_dict()
    return _finish(store, stats, errors, started, rescue_log, cluster_note, ent, no_prune)


def _finish(store: Store, stats: dict, errors: list[str], started: datetime,
            rescue_log: list[dict], cluster_note: str, ent, no_prune: bool) -> dict:
    if not no_prune:
        pruned = store.prune()
        stats["pruned_days"] = len(pruned["pruned_days"])
        stats["pruned_seen"] = pruned["pruned_seen"]

    store.write_index(ent)

    eu = store.eu_share(14)
    stats["eu_share_14d"] = eu
    stats["region_mix_14d"] = store.region_mix(14)

    record = {
        "run_at": iso_z(started),
        "duration_s": round((now_utc() - started).total_seconds(), 1),
        **{k: v for k, v in stats.items() if k not in ("usage",)},
        "usage": stats.get("usage", {}),
        "rescues": rescue_log,
        "cluster_note": cluster_note,
        "errors": errors,
    }
    store.append_run(record)

    log.info("done in %.1fs | eu_share_14d=%s | errors=%d",
             record["duration_s"], eu, len(errors))
    _print_funnel(stats)
    return stats


# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.run",
        description="Build the credit brief. See BUILD_SPEC.md.")
    parser.add_argument("--mock-llm", action="store_true",
                        help="replace all model calls with deterministic stand-ins")
    parser.add_argument("--from-fixture", metavar="PATH",
                        help="load items from a JSON fixture instead of the network")
    parser.add_argument("--dry-run", action="store_true",
                        help="stop after the rule layer and print the funnel")
    parser.add_argument("--lookback-hours", type=int, default=None,
                        help="override the harvest window (first run: try 168)")
    parser.add_argument("--data-dir", default=None, help="override site/data")
    parser.add_argument("--max-items", type=int, default=None,
                        help="override max_new_items_per_run")
    parser.add_argument("--no-prune", action="store_true", help="skip retention this run")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    setup_logging(args.verbose)
    try:
        run(mock_llm=args.mock_llm, fixture=args.from_fixture, dry_run=args.dry_run,
            lookback_hours=args.lookback_hours, data_dir=args.data_dir,
            max_items=args.max_items, no_prune=args.no_prune)
    except KeyboardInterrupt:
        log.warning("interrupted")
        return 130
    except Exception as exc:                                # noqa: BLE001
        log.exception("run failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
