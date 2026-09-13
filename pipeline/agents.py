"""Agent 2 (filter), Agent 3 (second look) and the summariser. BUILD_SPEC §6.

All three use forced tool use, so the model cannot answer in prose and the
pipeline never parses free text. Each has a model fallback chain, because a run
that dies on a model alias change is a run that silently stops updating the page.

`--mock-llm` swaps every model call for a deterministic rule-based stand-in so
the whole pipeline and the frontend can be exercised with no API key (§10.3).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from .config import Entities, Settings, api_key, load_entities, load_prompt, load_settings
from .entities import any_hits, has_hard_event, match_text

log = logging.getLogger("pipeline.agents")


# --------------------------------------------------------------------------
# Prompt assembly — the portfolio block is generated from entities.yaml so the
# prompt can never drift out of step with the entity table.
# --------------------------------------------------------------------------

def portfolio_block(entities: Entities) -> str:
    lines = []
    for gp in entities.gps:
        aliases = ", ".join(gp.strong_aliases[:4] + gp.vehicles[:3])
        lines.append(f"### {gp.name}  (id: `{gp.id}`)")
        if aliases:
            lines.append(f"Also appears as: {aliases}")
        lines.append(gp.credit_scope)
        lines.append("")
    return "\n".join(lines).strip()


def sector_block(entities: Entities) -> str:
    lines = []
    for s in entities.sectors:
        lines.append(f"- **{s.name}** (id: `{s.id}`) — {s.scope}")
    return "\n".join(lines)


def render_prompt(name: str, entities: Entities) -> str:
    text = load_prompt(name)
    return (text
            .replace("{{PORTFOLIO}}", portfolio_block(entities))
            .replace("{{SECTORS}}", sector_block(entities)))


# --------------------------------------------------------------------------
# Tool schemas
# --------------------------------------------------------------------------

def _ids_enum(entities: Entities) -> tuple[list[str], list[str]]:
    return [g.id for g in entities.gps], [s.id for s in entities.sectors]


def agent2_tool(entities: Entities) -> dict:
    gp_ids, sector_ids = _ids_enum(entities)
    return {
        "name": "record_decisions",
        "description": "Record a filtering decision for every item in the batch.",
        "input_schema": {
            "type": "object",
            "properties": {
                "decisions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "relevant": {"type": "boolean"},
                            "credit_related": {"type": "boolean"},
                            "importance": {"type": "integer", "enum": [1, 2, 3]},
                            "gp_ids": {"type": "array", "items": {"type": "string", "enum": gp_ids}},
                            "sector_ids": {"type": "array",
                                           "items": {"type": "string", "enum": sector_ids}},
                            "region": {"type": "string",
                                       "enum": ["US", "Europe", "Asia", "Global"]},
                            "reason": {"type": "string",
                                       "description": "One line, for the audit log."},
                        },
                        "required": ["id", "relevant", "credit_related", "importance",
                                     "gp_ids", "sector_ids", "region", "reason"],
                    },
                }
            },
            "required": ["decisions"],
        },
    }


def agent3_tool(entities: Entities) -> dict:
    gp_ids, sector_ids = _ids_enum(entities)
    return {
        "name": "record_second_look",
        "description": "Record which previously rejected items should be rescued.",
        "input_schema": {
            "type": "object",
            "properties": {
                "verdicts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "rescue": {"type": "boolean"},
                            "importance": {"type": "integer", "enum": [1, 2, 3]},
                            "gp_ids": {"type": "array", "items": {"type": "string", "enum": gp_ids}},
                            "sector_ids": {"type": "array",
                                           "items": {"type": "string", "enum": sector_ids}},
                            "region": {"type": "string",
                                       "enum": ["US", "Europe", "Asia", "Global"]},
                            "affected_exposure": {
                                "type": "string",
                                "description": "Which holding is touched and how, in one "
                                               "concrete sentence. Required when rescuing.",
                            },
                            "reason": {"type": "string"},
                        },
                        "required": ["id", "rescue", "importance", "gp_ids", "sector_ids",
                                     "reason"],
                    },
                },
                "cluster_note": {
                    "type": "string",
                    "description": "One observation if several items point at the same "
                                   "borrower, issuer or theme. Empty string if none.",
                },
            },
            "required": ["verdicts", "cluster_note"],
        },
    }


SUMMARY_TOOL = {
    "name": "record_summary",
    "description": "Record the bilingual headline and summary for one item.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title_en": {"type": "string", "description": "Plain English headline, <110 chars."},
            "title_zh": {"type": "string", "description": "中文标题，独立撰写。"},
            "summary_en": {"type": "string", "description": "2-4 factual sentences."},
            "summary_zh": {"type": "string", "description": "2-4 句中文摘要，独立撰写。"},
        },
        "required": ["title_en", "title_zh", "summary_en", "summary_zh"],
    },
}


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------

@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0

    def add(self, resp_usage: Any) -> None:
        self.calls += 1
        self.input_tokens += getattr(resp_usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(resp_usage, "output_tokens", 0) or 0

    def as_dict(self) -> dict:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "calls": self.calls}


class LLM:
    """Thin wrapper: forced tool use, model fallback, retry on transient errors."""

    def __init__(self, settings: Settings | None = None, mock: bool = False) -> None:
        self.settings = settings or load_settings()
        self.mock = mock
        self.usage = Usage()
        self._client = None
        if not mock:
            key = api_key()
            if not key:
                raise RuntimeError(
                    "ANTHROPIC_API_KEY is not set. Use --mock-llm to run offline.")
            import anthropic
            self._client = anthropic.Anthropic(api_key=key)

    def _chain(self, model: str) -> list[str]:
        return [model] + list(self.settings.models.get("fallbacks", {}).get(model, []))

    def call_tool(self, *, model: str, system: str, user: str, tool: dict,
                  max_tokens: int = 4096, temperature: float = 0.0) -> dict:
        """Return the tool input dict. Raises only when every model in the chain fails."""
        last: Exception | None = None
        for candidate in self._chain(model):
            for attempt in range(3):
                try:
                    resp = self._client.messages.create(
                        model=candidate,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        system=system,
                        messages=[{"role": "user", "content": user}],
                        tools=[tool],
                        tool_choice={"type": "tool", "name": tool["name"]},
                    )
                    self.usage.add(resp.usage)
                    for block in resp.content:
                        if getattr(block, "type", None) == "tool_use":
                            return dict(block.input)
                    raise RuntimeError("model returned no tool_use block")
                except Exception as exc:                    # noqa: BLE001
                    last = exc
                    name = type(exc).__name__
                    if name in ("NotFoundError", "BadRequestError", "PermissionDeniedError"):
                        log.warning("model %s unavailable (%s); trying next in chain",
                                    candidate, name)
                        break                                # move to the next model
                    sleep = 2 ** attempt
                    log.warning("call failed (%s), retry in %ss", name, sleep)
                    time.sleep(sleep)
        raise RuntimeError(f"all models failed for {model}: {last}")


# --------------------------------------------------------------------------
# Agent 2 — filter (§6.2)
# --------------------------------------------------------------------------

def _item_for_prompt(item: dict, rule: Any, include_body: bool = False) -> dict:
    out = {
        "id": item["id"],
        "headline": item.get("title", ""),
        "source": item.get("source", ""),
        "published": str(item.get("published_at", "")),
        "snippet": (item.get("summary") or "")[:700],
        "rule_hints": {
            "gp_ids": getattr(rule, "gp_ids", []),
            "sector_ids": getattr(rule, "sector_ids", []),
            "region_hint": getattr(rule, "region_hint", "Global"),
            "credit_context": getattr(rule, "credit_context", False),
        },
    }
    if include_body and item.get("body"):
        out["article_text"] = item["body"][:6000]
    return out


def agent2_filter(items: list[dict], llm: LLM, entities: Entities | None = None,
                  settings: Settings | None = None) -> list[dict]:
    """Returns one decision dict per item, in input order where possible."""
    ent = entities or load_entities()
    cfg = settings or load_settings()
    if not items:
        return []
    if llm.mock:
        return [_mock_agent2(it, ent) for it in items]

    system = render_prompt("agent2_filter", ent)
    tool = agent2_tool(ent)
    batch_size = cfg.limits.get("agent2_batch_size", 12)
    model = cfg.models.get("agent2", "claude-haiku-4-5-20251001")

    decisions: dict[str, dict] = {}
    for start in range(0, len(items), batch_size):
        batch = items[start:start + batch_size]
        payload = [_item_for_prompt(it, it.get("_rule")) for it in batch]
        user = (
            "Classify every item below. Return exactly one decision per item, keyed by "
            "the `id` given.\n\n"
            + json.dumps(payload, ensure_ascii=False, indent=1)
        )
        try:
            result = llm.call_tool(model=model, system=system, user=user, tool=tool,
                                   max_tokens=4096)
        except Exception as exc:                            # noqa: BLE001
            log.error("agent2 batch failed, treating as rejected: %s", exc)
            continue
        for d in result.get("decisions", []):
            if d.get("id"):
                decisions[str(d["id"])] = d

    out = []
    for it in items:
        d = decisions.get(it["id"])
        if d is None:
            # No decision came back: fail closed, but keep it in the pool so
            # Agent 3 can still see it.
            d = {"id": it["id"], "relevant": False, "credit_related": False,
                 "importance": 3, "gp_ids": [], "sector_ids": [], "region": "Global",
                 "reason": "no decision returned by agent 2"}
        out.append(d)
    return out


def keep_decision(d: dict) -> bool:
    """BUILD_SPEC §6.2: keep if relevant && importance <= 2 && (gp_ids or sector_ids)."""
    return bool(d.get("relevant")
                and int(d.get("importance", 3)) <= 2
                and (d.get("gp_ids") or d.get("sector_ids")))


# --------------------------------------------------------------------------
# Agent 3 — second look (§6.3)
# --------------------------------------------------------------------------

SHORTLIST_WEIGHTS = {
    "rule_gp_but_rejected": 3,
    "high_trust_source": 3,
    "trade_press": 2,
    "known_vehicle": 2,
    "near_miss": 2,
    "hard_event_word": 2,
    "sector_no_gp": 1,
    "no_rule_match": -5,
}

HIGH_TRUST_SOURCES = ("sec edgar", "business wire", "businesswire", "globenewswire",
                      "pr newswire", "prnewswire")
TRADE_PRESS_PRIORITY = 3


def shortlist_score(item: dict, decision: dict, entities: Entities | None = None
                    ) -> tuple[int, list[str]]:
    ent = entities or load_entities()
    rule = item.get("_rule") or match_text(
        f"{item.get('title','')} {item.get('summary','')}", ent)
    score = 0
    why: list[str] = []

    def add(key: str) -> None:
        nonlocal score
        score += SHORTLIST_WEIGHTS[key]
        why.append(key)

    if rule.gp_ids and not decision.get("relevant"):
        add("rule_gp_but_rejected")

    source = (item.get("source") or "").lower()
    if any(s in source for s in HIGH_TRUST_SOURCES) or item.get("origin") == "edgar":
        add("high_trust_source")
    elif int(item.get("priority", 0)) >= TRADE_PRESS_PRIORITY:
        add("trade_press")

    blob = f"{item.get('title','')} {item.get('summary','')}"
    vehicles = [v for gp in ent.gps for v in gp.vehicles]
    tickers = ["OTF", "FSK", "BCSF", "OBDC", "OCIC", "MFIC", "KREF", "OTIC"]
    if any(t in blob.split() for t in tickers) or any(v.lower() in blob.lower()
                                                      for v in vehicles if len(v) > 6):
        add("known_vehicle")

    if int(decision.get("importance", 3)) == 3 and decision.get("credit_related"):
        add("near_miss")

    if has_hard_event(item.get("title", ""), ent):
        add("hard_event_word")

    if rule.sector_ids and not rule.gp_ids:
        add("sector_no_gp")

    if not rule.gp_ids and not rule.sector_ids:
        add("no_rule_match")

    return score, why


def build_shortlist(pool: list[tuple[dict, dict]], entities: Entities | None = None,
                    limit: int = 25) -> list[tuple[dict, dict, int, list[str]]]:
    scored = []
    for item, decision in pool:
        score, why = shortlist_score(item, decision, entities)
        scored.append((item, decision, score, why))
    scored.sort(key=lambda t: t[2], reverse=True)
    return [t for t in scored[:limit] if t[2] > 0]


def agent3_second_look(shortlist: list[tuple[dict, dict, int, list[str]]], llm: LLM,
                       entities: Entities | None = None,
                       settings: Settings | None = None) -> tuple[list[dict], str]:
    ent = entities or load_entities()
    cfg = settings or load_settings()
    if not shortlist:
        return [], ""
    if llm.mock:
        return [_mock_agent3(item, ent) for item, _, _, _ in shortlist], "mock: no clustering"

    system = render_prompt("agent3_second_look", ent)
    tool = agent3_tool(ent)
    model = cfg.models.get("agent3", "claude-sonnet-5")

    payload = []
    for item, decision, score, why in shortlist:
        entry = _item_for_prompt(item, item.get("_rule"), include_body=True)
        entry["rejected_because"] = decision.get("reason", "")
        payload.append(entry)

    user = (
        "These items were all rejected by the first-pass filter. The full article text is "
        "included where it could be retrieved. Decide which are mistakes.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=1)
    )
    try:
        result = llm.call_tool(model=model, system=system, user=user, tool=tool,
                               max_tokens=8192)
    except Exception as exc:                                # noqa: BLE001
        log.error("agent3 failed: %s", exc)
        return [], ""
    return result.get("verdicts", []), result.get("cluster_note", "") or ""


# --------------------------------------------------------------------------
# Summariser (§6.4)
# --------------------------------------------------------------------------

def summarise(item: dict, llm: LLM, entities: Entities | None = None,
              settings: Settings | None = None) -> dict:
    ent = entities or load_entities()
    cfg = settings or load_settings()
    min_body = cfg.limits.get("min_body_chars", 300)

    body = (item.get("body") or "").strip()
    grounded_on = "article" if len(body) >= min_body else "snippet"
    text = body if grounded_on == "article" else (item.get("summary") or "")

    if llm.mock:
        return _mock_summary(item, grounded_on)

    system = render_prompt("summarize", ent)
    user = (
        f"Source publication: {item.get('source','unknown')}\n"
        f"Published: {item.get('published_at','')}\n"
        f"Original headline: {item.get('title','')}\n"
        f"Text available: {'full article' if grounded_on == 'article' else 'snippet only'}\n\n"
        f"--- TEXT ---\n{text[:9000]}\n--- END TEXT ---\n\n"
        "Write the headline and the 2-4 sentence summary, in English and in Chinese, "
        "using nothing beyond the text above."
    )
    try:
        result = llm.call_tool(model=cfg.models.get("summarize", "claude-sonnet-5"),
                               system=system, user=user, tool=SUMMARY_TOOL,
                               max_tokens=1500, temperature=0.2)
    except Exception as exc:                                # noqa: BLE001
        log.error("summariser failed for %s: %s", item.get("id"), exc)
        return _mock_summary(item, grounded_on, degraded=True)

    result["grounded_on"] = grounded_on
    return result


# --------------------------------------------------------------------------
# Offline stand-ins (§10.3). Deterministic, rule-based, no network.
# --------------------------------------------------------------------------

_EXCLUDE_HINTS = (
    "buy", "buys", "acquires a", "acquire", "buyout", "takeover", "stake in the company",
    "interval fund", "podcast", "webinar", "conference", "award", "op-ed", "opinion",
    "launches for retail", "retail investors", "net-lease", "net lease",
)
_EQUITY_HINTS = ("theme-park", "theme park", "infrastructure", "airport", "fixed-base",
                 "fbo", "net-lease", "net lease", "buyout", "equity stake")
_TIER1_HINTS = ("non-accrual", "nav", "default", "restructuring", "downgrade", "redemption",
                "bankruptcy", "results", "acquires $", "servicing portfolio", "closes $",
                "final close", "dividend", "8-k", "10-q")
# Market-level language. A sector match with none of this is a single-issuer story,
# which — absent a tracked manager — is precisely the blind spot Agent 3 exists to
# cover, so the offline stand-in discards it and lets Agent 3 do its job.
_MARKET_LEVEL_HINTS = ["spreads", "issuance", "deal count", "index", "survey", "data",
                       "volumes", "borrowers", "lenders", "managers", "squeeze",
                       "tighten", "tightened", "widen", "widened", "record",
                       "quarterly data", "average", "aggregate"]


def _mock_agent2(item: dict, ent: Entities) -> dict:
    """A crude but honest stand-in: applies the shape of the rule, not its judgement."""
    rule = item.get("_rule") or match_text(
        f"{item.get('title','')} {item.get('summary','')}", ent)
    blob = f"{item.get('title','')} {item.get('summary','')}".lower()

    equity = any(h in blob for h in _EQUITY_HINTS)
    marketing = any(h in blob for h in ("interval fund", "retail investors", "podcast",
                                        "webinar", "conference", "award"))
    sector_only = bool(rule.sector_ids) and not rule.gp_ids
    # Word-boundary, not substring: "mid-market" must not count as "market".
    market_level = bool(any_hits(blob, _MARKET_LEVEL_HINTS))
    relevant = (rule.is_candidate and not equity and not marketing
                and not (sector_only and not market_level))
    importance = 3
    if relevant:
        importance = 1 if (rule.gp_ids and any(h in blob for h in _TIER1_HINTS)) else 2
    return {
        "id": item["id"],
        "relevant": relevant,
        "credit_related": bool(rule.credit_context or rule.sector_ids),
        "importance": importance,
        "gp_ids": rule.gp_ids,
        "sector_ids": rule.sector_ids,
        "region": rule.region_hint if rule.region_hint != "Global" else "US",
        "reason": (
            "mock: equity or marketing pattern" if (equity or marketing)
            else "mock: sub-sector match on a single issuer, no tracked manager named"
            if (sector_only and not market_level)
            else f"mock: rule match {rule.gp_ids or rule.sector_ids}" if relevant
            else "mock: no rule match"),
    }


def _mock_agent3(item: dict, ent: Entities) -> dict:
    """Rescue when the body names a tracked GP that the headline did not."""
    head = match_text(f"{item.get('title','')} {item.get('summary','')}", ent)
    full = match_text(f"{item.get('title','')} {item.get('summary','')} "
                      f"{item.get('body','')}", ent)
    new_gps = [g for g in full.gp_ids if g not in head.gp_ids]
    rescue = bool(new_gps)
    return {
        "id": item["id"],
        "rescue": rescue,
        "importance": 1 if rescue and has_hard_event(item.get("title", ""), ent) else 2,
        "gp_ids": full.gp_ids,
        "sector_ids": full.sector_ids,
        "region": full.region_hint if full.region_hint != "Global" else "US",
        "affected_exposure": (f"mock: article body names {', '.join(new_gps)}"
                              if rescue else ""),
        "reason": "mock: body reveals a tracked manager absent from the headline"
                  if rescue else "mock: nothing new in the body",
    }


def _mock_summary(item: dict, grounded_on: str, degraded: bool = False) -> dict:
    title = item.get("title", "").replace("[TEST] ", "")
    snippet = (item.get("body") or item.get("summary") or "").strip()
    sentences = [s.strip() for s in snippet.replace("\n", " ").split(". ") if s.strip()][:3]
    body_en = ". ".join(sentences)
    if body_en and not body_en.endswith("."):
        body_en += "."
    return {
        "title_en": title[:110] or "(untitled)",
        "title_zh": f"[模拟] {title[:100]}" or "（无标题）",
        "summary_en": body_en or "No summary available offline.",
        "summary_zh": f"[模拟中文摘要] {body_en[:300]}" if body_en else "离线模式无摘要。",
        "grounded_on": grounded_on,
        "degraded": degraded,
    }
