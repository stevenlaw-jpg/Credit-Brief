"""The deterministic rule layer. BUILD_SPEC §4.4, §6.1.

This layer decides one thing only: is an item worth a model's attention. It is
auditable and free. It does not make relevance judgements — that is Agent 2's
job, and the tags produced here are handed to Agent 2 explicitly labelled as
fallible hints.

Matching rules:
  * word-boundary based, always;
  * acronym-shaped aliases (^[A-Z]{2,6}$) match case-sensitively, everything
    else case-insensitively — so "PAG" does not match "pag" inside a slug and
    "ABL" does not match "able";
  * a hit in `exclude_terms` vetoes the GP outright, however strong the alias;
  * `strong_aliases` and `vehicles` stand alone; `weak_aliases` need a
    co-occurring `context_term` (homonym protection).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

from .config import Entities, load_entities

ACRONYM_RE = re.compile(r"^[A-Z][A-Z0-9&.\-]{1,5}$")


def _is_acronym(term: str) -> bool:
    return bool(ACRONYM_RE.match(term)) and term.upper() == term


@lru_cache(maxsize=4096)
def _pattern(term: str, case_sensitive: bool) -> re.Pattern[str]:
    """Word-boundary pattern for a literal term.

    `\b` misbehaves next to punctuation (".io", "&", "-"), so the boundaries are
    written as explicit lookarounds on word characters.
    """
    escaped = re.escape(term).replace(r"\ ", r"\s+")
    # Tolerate a plural/possessive on the final token: "non-accruals" is the
    # same signal as "non-accrual", and "BDCs" the same as "BDC".
    pat = rf"(?<![A-Za-z0-9]){escaped}(?:'s|s|es)?(?![A-Za-z0-9])"
    return re.compile(pat, 0 if case_sensitive else re.IGNORECASE)


def term_hits(text: str, term: str) -> bool:
    if not term:
        return False
    return bool(_pattern(term, _is_acronym(term)).search(text))


def any_hits(text: str, terms: list[str]) -> list[str]:
    return [t for t in terms if term_hits(text, t)]


def _prefix_hits(text: str, terms: list[str]) -> list[str]:
    """Like `any_hits` but allows stem terms such as "refinanc" / "securitis".

    A term that is not acronym-shaped and does not end in a word character
    boundary requirement is matched as a prefix, so "refinanc" catches
    "refinance", "refinancing" and "refinanced".
    """
    out = []
    for t in terms:
        if _is_acronym(t):
            if term_hits(text, t):
                out.append(t)
            continue
        pat = re.compile(rf"(?<![A-Za-z0-9]){re.escape(t)}", re.IGNORECASE)
        if pat.search(text):
            out.append(t)
    return out


@dataclass
class RuleMatch:
    """What the rule layer concluded about one item."""

    gp_ids: list[str] = field(default_factory=list)
    sector_ids: list[str] = field(default_factory=list)
    region_hint: str = "Global"
    credit_context: bool = False
    vetoed_gp_ids: list[str] = field(default_factory=list)
    evidence: dict[str, list[str]] = field(default_factory=dict)

    @property
    def is_candidate(self) -> bool:
        """BUILD_SPEC §6.1: a candidate matched at least one GP or one sub-sector."""
        return bool(self.gp_ids or self.sector_ids)


def match_text(text: str, entities: Entities | None = None) -> RuleMatch:
    ent = entities or load_entities()
    text = text or ""
    result = RuleMatch()

    credit_terms = _prefix_hits(text, ent.credit_context_terms)
    result.credit_context = bool(credit_terms)
    if credit_terms:
        result.evidence["credit_context"] = credit_terms[:6]

    # ---- GPs ------------------------------------------------------------
    for gp in ent.gps:
        vetoes = any_hits(text, gp.exclude_terms)
        if vetoes:
            result.vetoed_gp_ids.append(gp.id)
            result.evidence[f"veto:{gp.id}"] = vetoes
            continue

        strong = any_hits(text, gp.strong_aliases) + any_hits(text, gp.vehicles)
        if strong:
            result.gp_ids.append(gp.id)
            result.evidence[f"gp:{gp.id}"] = strong[:4]
            continue

        weak = any_hits(text, gp.weak_aliases)
        if weak:
            ctx = any_hits(text, gp.context_terms)
            if ctx:
                result.gp_ids.append(gp.id)
                result.evidence[f"gp:{gp.id}"] = (weak[:2] + ["+ctx:" + ctx[0]])
            else:
                result.evidence[f"weak-unconfirmed:{gp.id}"] = weak[:2]

    # ---- sectors --------------------------------------------------------
    gp_matched = bool(result.gp_ids)
    for sector in ent.sectors:
        kws = any_hits(text, sector.keywords)
        if not kws:
            continue
        if sector.requires_credit_context and not (result.credit_context or gp_matched):
            result.evidence[f"sector-unconfirmed:{sector.id}"] = kws[:3]
            continue
        result.sector_ids.append(sector.id)
        result.evidence[f"sector:{sector.id}"] = kws[:4]

    # A GP match implies nothing about sub-sector, but a GP whose sector list
    # is a singleton and which matched with no sector hit is still usefully
    # tagged — that is left to Agent 2 rather than guessed here.

    result.region_hint = infer_region(text, ent)
    return result


def infer_region(text: str, entities: Entities | None = None) -> str:
    """A hint only. Agent 2 assigns the authoritative `region` (BUILD_SPEC §5.4)."""
    ent = entities or load_entities()
    scores = {region: len(any_hits(text, terms)) for region, terms in ent.regions.items()}
    if not scores:
        return "Global"
    # Ties break towards the brief's primary region, so a story mentioning both
    # New York and London is not silently counted as European.
    priority = {"US": 3, "Europe": 2, "Asia": 1}
    best = max(scores, key=lambda r: (scores[r], priority.get(r, 0)))
    return best if scores[best] > 0 else "Global"


def has_hard_event(text: str, entities: Entities | None = None) -> bool:
    """Title contains a hard credit-event word (Agent 3 shortlist signal, §6.3)."""
    ent = entities or load_entities()
    return bool(_prefix_hits(text, ent.hard_event_terms))


def match_item(item: dict, entities: Entities | None = None) -> RuleMatch:
    """Match over title + snippet + source name.

    Deliberately NOT the article body. Agent 2 judges on the headline and the
    snippet, and Agent 3's entire advantage is that it later sees the body
    (BUILD_SPEC §6.3). Letting the body leak into the rule-layer hints here
    would erase that difference and make Agent 3 redundant.
    """
    blob = " ".join(
        str(item.get(k) or "") for k in ("title", "summary", "snippet", "source")
    )
    return match_text(blob, entities)
