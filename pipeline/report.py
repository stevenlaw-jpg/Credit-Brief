"""Turn `_runs.jsonl` into the numbers the write-up is required to quote.

BUILD_SPEC §11.1 asks for real funnel numbers rather than adjectives, and §11.2
asks for a summary of what Agent 3 has been rescuing, because that pattern names
the filter's blind spots better than prose can.

    python -m pipeline.report            # markdown, for the README
    python -m pipeline.report --json     # the same numbers, machine-readable
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from typing import Any

from .config import load_entities
from .store import Store

FUNNEL = [
    ("fetched", "Items fetched from all sources"),
    ("in_window", "Published inside the lookback window"),
    ("after_dedupe", "After URL canonicalisation and title clustering"),
    ("candidates", "Matched a tracked manager or sub-sector (rule layer)"),
    ("new", "Not seen in an earlier run"),
    ("agent2_kept", "Kept by Agent 2"),
    ("agent3_shortlist", "Shortlisted from Agent 2's discard pool"),
    ("agent3_rescued", "Rescued by Agent 3"),
    ("summarised", "Summarised and published"),
]


def collect(store: Store | None = None, limit: int | None = None) -> dict[str, Any]:
    store = store or Store()
    runs = store.read_runs(limit)
    real = [r for r in runs if r.get("fetched")]

    totals = {key: sum(int(r.get(key) or 0) for r in real) for key, _ in FUNNEL}
    rescues = [x for r in real for x in (r.get("rescues") or [])]
    errors = [e for r in real for e in (r.get("errors") or [])]
    usage = {
        "input_tokens": sum(int((r.get("usage") or {}).get("input_tokens") or 0) for r in real),
        "output_tokens": sum(int((r.get("usage") or {}).get("output_tokens") or 0) for r in real),
        "calls": sum(int((r.get("usage") or {}).get("calls") or 0) for r in real),
    }
    eu = [r["eu_share_14d"] for r in real if r.get("eu_share_14d") is not None]

    mix = Counter()
    for date in store.dates():
        for item in store.read_day(date).get("items", []):
            mix[item.get("region") or "Global"] += 1

    gps = Counter()
    sectors = Counter()
    tier1 = 0
    published = 0
    grounded = Counter()
    for date in store.dates():
        for item in store.read_day(date).get("items", []):
            published += 1
            if int(item.get("importance", 2)) == 1:
                tier1 += 1
            grounded[item.get("grounded_on", "unknown")] += 1
            for g in item.get("gp_ids", []):
                gps[g] += 1
            for s in item.get("sector_ids", []):
                sectors[s] += 1

    return {
        "runs": len(real),
        "first_run": real[0]["run_at"] if real else None,
        "last_run": real[-1]["run_at"] if real else None,
        "funnel": totals,
        "published": published,
        "tier1": tier1,
        "tier1_share": round(tier1 / published, 3) if published else None,
        "region_mix": dict(mix),
        "eu_share_latest": eu[-1] if eu else None,
        "days": len(store.dates()),
        "gp_counts": dict(gps.most_common()),
        "sector_counts": dict(sectors.most_common()),
        "grounded_on": dict(grounded),
        "rescues": rescues,
        "errors": Counter(e.split(":")[0] for e in errors).most_common(8),
        "usage": usage,
    }


def markdown(data: dict[str, Any]) -> str:
    ent = load_entities()
    gp_names = {g.id: g.name for g in ent.gps}
    sector_names = {s.id: s.name for s in ent.sectors}
    out: list[str] = []
    w = out.append

    if not data["runs"]:
        return ("_No completed runs yet. These numbers are generated from "
                "`site/data/_runs.jsonl` by `python -m pipeline.report`._")

    w(f"_Measured over {data['runs']} runs, "
      f"{data['first_run']} to {data['last_run']}. "
      f"Regenerate with `python -m pipeline.report`._\n")

    w("| Stage | Items |")
    w("|---|---:|")
    for key, label in FUNNEL:
        w(f"| {label} | {data['funnel'].get(key, 0):,} |")
    w("")

    published = data["published"]
    if published:
        w(f"Across {data['days']} days, **{published} items** were published, of which "
          f"**{data['tier1']}** ({data['tier1_share']:.0%}) carry the `Key` tag.\n")

        mix = data["region_mix"]
        total = sum(mix.values()) or 1
        w("Region mix, measured on published output:\n")
        w("| Region | Items | Share |")
        w("|---|---:|---:|")
        for region in ("US", "Europe", "Asia", "Global"):
            n = mix.get(region, 0)
            w(f"| {region} | {n} | {n / total:.0%} |")
        w("")

        grounded = data["grounded_on"]
        if grounded:
            article = grounded.get("article", 0)
            w(f"Summaries grounded on the full article: **{article}/{published}** "
              f"({article / published:.0%}); the rest on the headline and standfirst "
              f"alone, which is what a paywall leaves reachable.\n")

    if data["gp_counts"]:
        w("Coverage by manager:\n")
        w("| Manager | Items |")
        w("|---|---:|")
        for gid, n in data["gp_counts"].items():
            w(f"| {gp_names.get(gid, gid)} | {n} |")
        w("")

    if data["sector_counts"]:
        w("Coverage by sub-sector:\n")
        w("| Sub-sector | Items |")
        w("|---|---:|")
        for sid, n in data["sector_counts"].items():
            w(f"| {sector_names.get(sid, sid)} | {n} |")
        w("")

    rescues = data["rescues"]
    w(f"**Agent 3 rescued {len(rescues)} items.** The pattern matters more than the "
      f"count: what it keeps rescuing names a blind spot in the entity table.\n")
    for r in rescues[:10]:
        w(f"- _{r.get('title','')[:110]}_ — {r.get('affected_exposure','')[:180]}")
    if rescues:
        w("")

    usage = data["usage"]
    if usage["calls"]:
        w(f"Model usage: {usage['calls']:,} calls, {usage['input_tokens']:,} input and "
          f"{usage['output_tokens']:,} output tokens.\n")

    if data["errors"]:
        w("Source failures recorded (a failing source logs a warning and contributes "
          "nothing; it never fails a run):\n")
        for label, n in data["errors"]:
            w(f"- `{label}` × {n}")
        w("")

    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipeline.report")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of markdown")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--limit", type=int, default=None, help="use only the last N runs")
    args = ap.parse_args(argv)

    data = collect(Store(args.data_dir), args.limit)
    print(json.dumps(data, indent=2, ensure_ascii=False) if args.json else markdown(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
