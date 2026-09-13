# Credit Brief — Build Specification

A date-organised, bilingual (EN/中文) news dashboard for a family office that is an LP in
the **credit strategies** of 13 named fund managers across 8 credit sub-sectors.

This document is the complete build spec. Implement it as written. Where it says
**DECISION**, the choice is already made — do not re-litigate it. Where it says
**VERIFY**, stop and confirm with the user before proceeding.

---

## 0. What is actually being graded

The client's brief ends with: *"part of this exercise is seeing what you judge to be
relevant and important news for an investor with this portfolio."*

The reviewer is an investment manager. On Monday morning he will:

1. Open the link on his phone. **The page must load instantly with today's news.**
2. Scan 6–12 items. **He is checking whether these are the items he would have picked.**
3. Read the write-up. **He is checking whether the filtering logic reads like an analyst.**

Therefore the engineering budget is: make it simple, correct, and impossible to break.
Spend the remaining effort on the entity model and the filter prompts. A plain page where
every item is right beats a sophisticated page that includes one KKR private-equity buyout.

**The single most damaging failure is including a non-credit deal by a mega-fund.** The
brief explicitly warns about this. Treat it as a correctness bug, not a style issue.

---

## 1. Hard requirements from the brief

| # | Requirement | Non-negotiable behaviour |
|---|---|---|
| R1 | Web-based dashboard organised by date | Static site, one archive page per date |
| R2 | Opens on today's news by default | No query string needed; `/` shows today |
| R3 | Click into previous dates | Date navigation; URL carries the date so it can be shared |
| R4 | History starts at build date | No backfill requirement. See §7.6 |
| R5 | Rolling window to save storage | 90 days; older day-files deleted |
| R6 | Toggle in the **top-right corner**, EN ↔ 中文 | Switches the **entire page**, same content in both languages |
| R7 | Each item = a few sentences explaining what happened | 2–4 sentences, factual, grounded in the source |
| R8 | Followed by a link to the original article | Link must resolve; prefer the primary source |
| R9 | Small-print tags underneath: GP and/or sub-sector | Small type, visually subordinate to the summary |
| R10 | US-focused, ~20% Europe | Measured on **output**, not on queries. See §5.4 |
| R11 | Credit strategies only | See §4.3 — the most important rule in the system |

**DECISION — ordering.** Items are sorted **newest first, strictly by publication time**.
There is no importance-based grouping, no "top stories" section, no pinning. Importance never
affects position.

**DECISION — importance is a tag.** Importance is surfaced as a small tag on the item, sitting
in the same small-print row as the GP and sub-sector tags. It is **binary**: only Tier 1 items
carry a `Key` / `重点` tag; Tier 2 items carry none. A tag that appears on every item carries no
information — the absence of the tag is what makes its presence meaningful. Tier 3 never
reaches the page at all.

This is a deliberate addition beyond the brief's literal tag requirement (R9 asks for GP and
sub-sector tags). It is worth the deviation because it makes the system's judgement legible to
the reviewer — which is the thing actually being assessed — without disturbing the
chronological reading order he asked for. The tag is visually distinct from the GP and
sub-sector tags, because it is a judgement rather than a factual category.

**DECISION — item anatomy.** Headline → summary (2–4 sentences) → source line with link and
timestamp → small-print tags (importance if Tier 1, then GP, then sub-sector). Nothing else. No
editorial commentary, no "why this matters", no analyst take.

---

## 2. Architecture

No server, no database, no backend. The entire system is:

```
GitHub Actions (cron, every 3h)
   └─> python -m pipeline.run
         ├─ Agent 1  harvest      → candidate items
         ├─         normalise + dedupe
         ├─ Agent 2  filter       → keep / discard  (+ discard pool)
         ├─ Agent 3  second look  → rescue from discard pool
         ├─         write EN+ZH summaries for kept items
         └─ write site/data/*.json
   └─> git commit && push
         └─> GitHub Pages redeploys automatically
                └─> reader opens URL, browser fetches static JSON and renders
```

- **Hosting**: GitHub Pages from `/site` on the default branch. This is deliverable (1).
- **Storage**: JSON files committed to the repo. Tens of KB per day; git history is a free audit trail.
- **Compute**: GitHub Actions ephemeral runners. Nothing is always-on.
- **Cost**: only the Anthropic API. Target < US$0.50/day.

**SECURITY — non-negotiable.** `ANTHROPIC_API_KEY` lives in GitHub Actions Secrets and is
read from the environment. It must never appear in the repo, in committed JSON, in logs, or
in any file the site serves. The repo is public (required for free Pages + Actions), so
scan committed output for anything that looks like a key before the first push.

---

## 3. Repository layout

```
.
├── README.md                      # deliverable (3)+(4) live here; see §11
├── BUILD_SPEC.md                  # this file
├── requirements.txt
├── config/
│   ├── entities.yaml              # GP × sub-sector matrix, aliases, homonyms  ← the geology
│   ├── settings.yaml              # runtime knobs, source list
│   └── prompts/
│       ├── agent2_filter.md
│       ├── agent3_second_look.md
│       └── summarize.md
├── pipeline/
│   ├── __init__.py
│   ├── config.py                  # config loading, US-Eastern date logic
│   ├── sources.py                 # Agent 1: harvest
│   ├── dedupe.py                  # URL canonicalisation + title clustering
│   ├── entities.py                # rule-layer matcher
│   ├── agents.py                  # Agent 2, Agent 3, summariser (Claude calls)
│   ├── store.py                   # JSON read/write, index, retention, seen-set
│   └── run.py                     # orchestration + CLI
├── site/
│   ├── index.html
│   ├── assets/{style.css, app.js}
│   └── data/                      # generated; committed
│       ├── index.json
│       ├── YYYY-MM-DD.json
│       ├── _seen.json
│       └── _runs.jsonl            # audit log; not served to users but kept in repo
├── tests/
│   ├── fixtures/trap_cases.json   # §10.1 — must pass before shipping
│   └── test_*.py
└── .github/workflows/update.yml
```

---

## 4. The entity model (`config/entities.yaml`)

This file is the geology under everything. Agent 1 builds queries from it, the rule layer
matches on it, Agent 2 and Agent 3 receive its scope descriptions in their prompts. **An
error here is systematic, not incidental.**

### 4.1 GP × sub-sector matrix

The brief says the listed GPs and sub-sectors are *"the areas we have already invested in"*.
They are not two independent lists — they pair up. Encode the pairing so the system knows
which manager to watch for each sub-sector, and so a sub-sector story can be connected to a
holding.

| Sub-sector | Primary GPs | Confidence |
|---|---|---|
| Software (tech lending) | Blue Owl (OTF, OTIC), KKR Credit, Apollo, NB | high |
| Private credit / direct lending | all 13 to some degree; core: Blue Owl, OTF, KKR, Apollo, Bain Capital Credit, NB, HSBC AM, PAG | high |
| GP stakes | Blue Owl (GP Strategic Capital, ex-Dyal), NB (Dyal-adjacent / strategic capital), Bonaccord-style vehicles | medium |
| Aircraft leasing | **Guggenheim** (Guggenheim Aviation lineage, aviation ABS, private-jet finance), **Bain Capital** (JB Aircraft Finance, launched Jun 2026), **KKR** (aviation financings, e.g. BOND), **Apollo** (aviation/ABF via Atlas SP) | medium — **VERIFY** |
| Asset-backed lending | **Basepoint** (asset-based financing to specialty-finance originators; MCA/small-business ABS; acquired IPF), Apollo (Atlas SP, MidCap), KKR ABF, HSBC AM | high |
| Real estate (credit) | Pretium, KKR real estate credit, Apollo real estate credit, PAG real estate credit | high |
| Mortgage | **Bayview** (MSR, whole loans, RMBS, Lakeview servicing), **Pretium** (residential credit, SFR, Progress Residential) | high |
| CLO | **CIFC**, Guggenheim, NB, Bain Capital Credit, KKR Credit, Blue Owl | high |

**VERIFY before first run.** The aircraft-leasing row is the weakest. The client says they
are invested in aircraft leasing, so at least one of the 13 must carry that exposure. Ask the
user to confirm which manager(s) it is. Until confirmed, keep the row as written and set
`confidence: medium` in the YAML so it shows up in the limitations section of the write-up.

**Trap worth knowing:** in Aug 2026 Apollo and KKR announced a partnership around Atlantic
Aviation (FBO / aviation infrastructure, ~$10bn). This is *infrastructure equity*, not
aircraft-leasing credit. It must be **excluded**. Put it in the trap fixtures (§10.1).

### 4.2 Per-GP schema

```yaml
gps:
  - id: blue-owl
    name: Blue Owl
    name_zh: Blue Owl                 # manager names stay in English in the ZH UI
    strong_aliases: [...]             # a word-boundary hit alone is enough
    weak_aliases:   [...]             # needs a context_term co-hit (homonym protection)
    context_terms:  [...]
    exclude_terms:  [...]             # a hit here vetoes the GP entirely
    vehicles:       [...]             # BDCs/tickers: OBDC, OTF, FSK, BCSF ...
    sectors:        [software, private-credit, gp-stakes, clo]
    credit_scope: >
      IN SCOPE: ...   OUT OF SCOPE: ...
```

`credit_scope` is prose passed verbatim into the Agent 2 / Agent 3 prompts. Write it as an
instruction to a human analyst, naming both what counts and what does not.

### 4.3 The credit-only rule

For each multi-strategy manager, state the boundary explicitly:

- **KKR** — IN: KKR Credit (direct lending, asset-based finance, CLOs), FS KKR Capital Corp
  (FSK), real-estate credit, Global Atlantic as credit-funding balance sheet, firm-level events
  that affect the credit platform (credit leadership, regulation, litigation, fundraising).
  OUT: private-equity buyouts, infrastructure equity, real-estate equity, portfolio-company
  news with no credit angle.
- **Apollo** — IN: credit platform (direct lending, Atlas SP, Apollo Debt Solutions BDC,
  MidCap Financial, CLOs, real-estate credit), Athene as the insurance balance sheet, firm-level
  credit events. OUT: PE buyouts, infrastructure/aviation-infrastructure equity, sports/other
  equity investments.
- **Bain Capital** — IN: Bain Capital Credit, Bain Capital Specialty Finance (BCSF), private
  credit, structured credit, special situations, JB Aircraft Finance. OUT: Bain Capital PE,
  Bain Capital Ventures, and the unrelated consultancy **Bain & Company**.
- **Blue Owl** — IN: Blue Owl Credit and its BDCs (OBDC, OTF, OCIC), GP Strategic Capital
  (GP stakes is a tracked sub-sector). OUT: net-lease real-estate equity, digital-infrastructure
  equity — unless the story is about financing or a credit vehicle.
- **NB (Neuberger Berman)** — IN: private credit/debt, CLO management, strategic capital /
  GP stakes. OUT: mutual funds, equity strategies, wealth-product news. Never match the bare
  letters "NB".
- **HSBC AM** — IN: HSBC Asset Management alternatives: private credit, ABL, infrastructure
  debt, real-estate debt. OUT: HSBC the bank — group earnings, retail banking, wealth products.
- **PAG** — IN: PAG Credit & Markets (private credit, real-estate credit, special situations).
  OUT: PAG private-equity buyouts and real-estate equity.
- **Guggenheim** — IN: Guggenheim Investments / Partners credit: corporate credit, private
  credit, CLOs, structured credit, aviation finance. OUT: the museum, the fellowship, and
  investment-banking advisory mandates that are not themselves credit-market events.

Single-strategy managers (OTF, Pretium, Bayview, CIFC, Basepoint) are in scope across
everything they do — for them the constraint is homonym protection, not business-line filtering.

### 4.4 Homonyms — mandatory

| Token | Wrong match | Handling |
|---|---|---|
| `Apollo` | Apollo 11 / NASA, Apollo Hospitals, Apollo Tyres, Apollo Theater, Apollo.io | weak alias + context terms; exclude list |
| `Guggenheim` | Museum, Fellowship, Bilbao | weak alias + context; exclude list |
| `PAG` | Penske Automotive Group, Punjab, other initialisms | weak alias, **case-sensitive**, context terms (`Hong Kong`, `credit`, `Weijian Shan`); exclude `Penske` |
| `Bayview` | place names, hospitals, streets | weak alias + context (`mortgage`, `MSR`, `RMBS`, `Lakeview`) |
| `Bain` | **Bain & Company** (consultancy) | exclude `Bain & Company`, `Bain & Co` |
| `NB` | any two-letter use | **never** match bare `NB`; only `Neuberger Berman` and named NB units |
| `OTF` | "one-third", other tickers | weak alias + BDC context (`Blue Owl`, `BDC`, `NAV`, `NYSE`) |
| `CIFC` | — | fine as-is, but require a credit context term |
| `Basepoint` | Basepoint Business Centres (UK offices) | exclude `business centre`, `workspace`, `office space` |

Acronym matching (`PAG`, `NB`, `OTF`, `CIFC`, `ABL`, `CLO`, `MSR`, `ABS`, `BDC`) is
**case-sensitive**. Everything else is case-insensitive. All matching is word-boundary based.

### 4.5 Sub-sectors

```yaml
sectors:
  - id: software
    name: Software
    name_zh: 软件
    keywords: [...]
    requires_credit_context: true    # "software" alone is not a credit story
```

`requires_credit_context: true` for `software`, `abl`, `real-estate`; a keyword hit only
counts when a credit-context term (`loan`, `lending`, `credit`, `debt`, `spread`, `default`,
`BDC`, `CLO`, `refinanc`, `covenant`, `non-accrual`, `NAV`, `securitis/zation`, …) is also
present, or a GP has already matched.

---

## 5. Agent 1 — Harvest

### 5.1 Query construction

Build queries from the entity table, not from a hand-written list:

- Per GP: `"<canonical name>" (credit OR lending OR loan OR BDC OR fund OR CLO OR debt OR financing)`
- Per GP with a weak alias: `"<alias>" <two context terms> (credit OR …)`
- Per vehicle/ticker: `"<vehicle name>"` (e.g. `"Blue Owl Technology Finance"`, `"FS KKR Capital"`)
- Per sub-sector: `(<kw1> OR <kw2>) (investor OR fund OR lender OR loan)`
- Per macro theme (from `entities.themes`): private-credit regulation, BDC non-accruals,
  default rates, bank–private-credit partnerships, CLO issuance/AAA spreads, software+AI credit risk,
  European direct lending, rate decisions with credit implications.
- Europe-specific variants for the main sub-sectors (see §5.4).

~40–60 queries. Log the count.

### 5.2 Sources

| Source | Type | Priority | Notes |
|---|---|---|---|
| SEC EDGAR full-text search | primary | 4 | 8-K / 10-Q / 10-K for listed BDCs: OTF, OBDC, FSK, BCSF. Free, structured, no paywall. Requires a descriptive `User-Agent` with contact email (`EDGAR_USER_AGENT` env) |
| Publisher RSS (Private Debt Investor, Alternative Credit Investor, etc.) | trade press | 3 | Some paywalled — headline + standfirst only. Still triageable |
| Bing News RSS | aggregator | 2 | Direct publisher links |
| Google News RSS | aggregator | 1 | Redirect links; used as the fallback copy |

Priority decides which copy of a duplicated story becomes the primary link. Every fetcher is
isolated: a failing source logs a warning and contributes nothing. **One broken feed must
never fail the run.**

### 5.3 Time window and the date key

**DECISION — the dashboard date is the US Eastern calendar day of publication.**

Rationale: the readership is in Hong Kong, the news is American. If the day boundary is set in
HKT, then at 08:30 HKT Monday the "today" bucket is only a couple of hours old and nearly
empty, while the whole of the US Friday session sits under a different date. Using the
US-Eastern calendar day means that when Hong Kong opens in the morning, "today" contains the
US session that has just finished, and the date label matches the date the article itself
carries.

```python
def day_key(published_at_utc) -> str:
    return published_at_utc.astimezone(ZoneInfo("America/New_York")).date().isoformat()
```

Normal runs look back `lookback_hours: 8` (cron is every 3h — deliberate overlap). Items with
no parseable publish time are treated as "now".

### 5.4 US / Europe balance

The brief says primarily US with roughly 20% Europe. **This is an output property, not a query
property.** Implement it as:

1. Dedicated European queries so European stories are actually reachable.
2. A `region` field on every item (`US` / `Europe` / `Asia` / `Global`), assigned by Agent 2.
3. A rolling 14-day EU share computed in `_runs.jsonl` and surfaced in the run log.
4. **No quota, no forced injection.** If the week's European news is thin, the share is low and
   that is the honest answer. Report the actual number in the write-up rather than engineering it.

`Asia` exists only because PAG is Hong Kong-based and HSBC AM is UK-based with Asian operations.
Asian news is included when it concerns a tracked GP or a tracked sub-sector directly; it is not
a target region of its own.

---

## 6. Agents 2 and 3 — the filter

### 6.1 Rule layer (deterministic, runs before Agent 2)

Entity matching from §4. Output per item: `gp_ids`, `sector_ids`, `region_hint`,
`credit_context: bool`. An item is a **candidate** if it matched at least one GP or one
sub-sector. Non-candidates are dropped without an API call.

This layer only decides "is this worth a model's attention". It is auditable and cheap; it
does not make relevance judgements.

### 6.2 Agent 2 — filter

- Model: `claude-haiku-4-5-20251001`. Batch 12 items per call.
- Input per item: headline, source, published time, snippet, rule-layer tags (labelled as
  *hints that may be wrong*).
- Forced structured output via tool use (`tool_choice: {"type":"tool","name":"..."}`):

```json
{"id":"...","relevant":true,"credit_related":true,"importance":1,
 "gp_ids":["otf"],"sector_ids":["software","private-credit"],
 "region":"US","reason":"one line, for the audit log"}
```

- Keep if `relevant && importance <= 2 && (gp_ids or sector_ids)`.
- Everything else goes to the **discard pool** with its reason — do not throw it away, Agent 3
  needs it.

Importance tiers (full text goes in the prompt, and verbatim into the write-up):

- **1 — key**: direct event at a tracked GP's credit business (fund close, BDC
  earnings/NAV/dividend/non-accruals, redemption gates, credit-facility change, SEC filing with
  new information, credit-business leadership change, regulatory or legal action, a material
  portfolio credit event); or a market-moving sub-sector event (large default or restructuring,
  a rule affecting private credit / BDCs / CLOs / mortgages, a central-bank decision with clear
  credit implications).
- **2 — notable**: sector data and trends (default rates, spreads, issuance, fundraising
  totals), a tracked GP's ordinary transaction in a tracked sub-sector, notable European
  developments, credible reporting on stress or opportunity.
- **3 — exclude**: product marketing and retail feeder/ETF launches with no portfolio
  consequence; interviews, op-eds and listicles with no new fact; conference notices; awards;
  hires below head-of-business; **non-credit deals by multi-strategy managers**; passing
  mentions; share-price commentary with no underlying event; a duplicate of something already
  published that day.

Tie-breaks: between 2 and 3 choose 3; between 1 and 2 choose 2.

**Tier 1 is user-visible.** It renders as a `Key` / `重点` tag on the page (§8.2), so the
assignment is a public claim, not an internal score. State this in the prompt: mark Tier 1 only
when the reader would agree at a glance that this is one of the day's few things that matter. If
most of a day's items come back Tier 1, the tag has stopped meaning anything — treat that as a
prompt bug. Expect roughly 2–4 Tier 1 items on a normal day, and none at all on a quiet one.

### 6.3 Agent 3 — second look at the discard pool

**Purpose**: the cost of a false negative (missing OTF's non-accruals jumping) is far higher
than the cost of a false positive (one dull item). Agent 3 audits what Agent 2 threw away.

**Critical design constraint:** Agent 3 must not simply re-ask Agent 2's question with the same
information — that produces the same answer and an infinite loop that costs money and changes
nothing. Agent 3 must have **strictly more** to work with, in three specific ways:

1. **More text.** Agent 2 saw headline + snippet. Agent 3 fetches the article body
   (`trafilatura`) for the items it examines. Key facts (which lender led the facility) are
   frequently absent from the headline.
2. **A wider view.** Agent 2 judged each item alone. Agent 3 receives the shortlist *as a batch*
   and is asked to spot clustering: five separately-unremarkable items about the same borrower
   are collectively a signal.
3. **A different question.** Not "is this relevant?" but **"if this is true, which manager or
   sub-sector in the portfolio is affected, and how?"** This inverts the direction of reasoning
   and catches stories that never name a tracked manager but hit an underlying exposure.

**Shortlisting** (do not send the whole pool — rank, then take the top N, `agent3_max: 25`):

| Signal | Weight | Why |
|---|---|---|
| Rule layer matched a GP, Agent 2 said not relevant | +3 | direct contradiction |
| Source is EDGAR or a first-party press release | +3 | high-trust source discarded |
| Source is tracked trade press (PDI etc.) | +2 | curated by humans already |
| Item mentions a known vehicle/ticker (OTF, FSK, BCSF, OBDC) | +2 | |
| Agent 2's `importance` was 3 but `credit_related` was true | +2 | near-miss |
| Title contains a hard credit event word (default, restructuring, non-accrual, downgrade, redemption, bankruptcy, covenant) | +2 | |
| Matched a sub-sector but no GP | +1 | the blind spot the matrix cannot cover |
| Rule layer matched nothing at all | −5 | almost certainly noise |

- Model: `claude-sonnet-5` (fall back to `claude-sonnet-4-6`, then `claude-sonnet-4-5-20250929`).
- Output per item: `rescue: bool`, `importance`, `gp_ids`, `sector_ids`, `affected_exposure`
  (free text — which holding and how), `reason`.
- Rescued items go **straight to the summariser**. They are not sent back to Agent 2 — that
  round trip adds no information. Agent 3 is the higher-context judge; its call stands.
- Also emit a batch-level `cluster_note` when several discarded items point at the same
  borrower/issuer/theme.

**The most valuable output of Agent 3 is not the rescued items — it is the pattern in what it
rescues.** If it keeps rescuing aircraft-leasing stories, the matrix row for aircraft leasing is
wrong. Log every rescue with its reason to `_runs.jsonl`; §11 requires summarising this in the
write-up.

Guard rails: cap at `agent3_max` items per run; if Agent 3 rescues more than 40% of its
shortlist on three consecutive runs, log a loud warning — Agent 2's threshold is mis-set and a
human should look.

### 6.4 Summariser

- Model: `claude-sonnet-5` (same fallback chain). One call per kept item.
- Fetch the article body first; if extraction fails or returns < 300 chars, fall back to the
  snippet and set `grounded_on: "snippet"`.
- Forced tool output: `title_en, title_zh, summary_en, summary_zh`.
- **Grounding rule, stated in the prompt:** use nothing beyond the supplied text. No figure,
  name or date that is not in the source. If only a snippet is available, write less. A
  fabricated number is worse than no summary.
- **Chinese is written independently, not translated.** Same facts, no extra facts. Manager
  names, tickers and fund names stay in English. Use the fixed glossary (§9.3) so terminology is
  consistent across days.
- No opinions, no forecasts, no evaluative adjectives.

### 6.5 Cost control

- Rule layer eliminates most items before any API call.
- Agent 2 batches 12 at a time on the cheapest model.
- Agent 3 sees at most 25 items per run.
- Summaries only for kept items, capped at `max_new_items_per_run: 20`.
- A `_seen.json` URL-hash set means no item is ever classified or summarised twice.

---

## 7. Data contracts

### 7.1 `site/data/YYYY-MM-DD.json`

```json
{
  "date": "2026-09-12",
  "updated_at": "2026-09-13T06:04:11Z",
  "items": [
    {
      "id": "9f2a1c77b3e04d51",
      "title_en": "Blue Owl Technology Finance reports Q2 NAV of $17.20 a share",
      "title_zh": "Blue Owl Technology Finance 公布二季度每股净资产值 17.20 美元",
      "summary_en": "2–4 sentences.",
      "summary_zh": "独立撰写的中文摘要。",
      "url": "https://...",
      "also_urls": ["https://..."],
      "source": "Business Wire",
      "published_at": "2026-09-12T21:30Z",
      "gp_ids": ["blue-owl", "otf"],
      "sector_ids": ["software", "private-credit"],
      "region": "US",
      "importance": 1,
      "grounded_on": "article",
      "rescued_by_agent3": false,
      "reason": "audit only; never rendered"
    }
  ]
}
```

Items are stored **sorted by `published_at` descending**. The frontend does not re-sort.

### 7.2 `site/data/index.json`

```json
{
  "generated_at": "...",
  "timezone": "America/New_York",
  "retention_days": 90,
  "update_interval_hours": 3,
  "names": {
    "gps":     {"blue-owl": {"en": "Blue Owl", "zh": "Blue Owl"}},
    "sectors": {"clo": {"en": "CLO", "zh": "CLO"}}
  },
  "dates": [{"date": "2026-09-12", "count": 9, "updated_at": "..."}]
}
```

`dates` is sorted newest first and contains only dates that have a file.

### 7.3 `_seen.json`, `_runs.jsonl`

`_seen.json`: `{url_hash: {date, first_seen}}`, pruned with the retention window.

`_runs.jsonl`, one line per run — this is the evidence base for the write-up:

```json
{"run_at":"...","fetched":412,"in_window":355,"after_dedupe":228,"candidates":64,
 "new":31,"agent2_kept":7,"agent3_shortlist":19,"agent3_rescued":2,"summarised":9,
 "eu_share_14d":0.18,"pruned_days":1,"usage":{"input_tokens":...,"output_tokens":...},
 "errors":["feed X unavailable"]}
```

### 7.4 Idempotency

Same run twice → no duplicate items, no duplicate API spend. Keyed on the canonical-URL hash.
Merging into an existing day file is by `id`.

### 7.5 Retention

Delete `YYYY-MM-DD.json` older than 90 days; prune `_seen.json` to match; rebuild `index.json`.

### 7.6 First run / backfill

**DECISION:** the brief explicitly permits history to start at the build date. Do **not** promise
a backfill. Attempt a best-effort one only from EDGAR (which supports real date ranges) with
`--lookback-hours 168`; news-search RSS will not return a useful week of history. Whatever
comes back, comes back. State plainly in the write-up that history begins on the build date.

---

## 8. Frontend (`site/`)

Plain HTML/CSS/JS. No framework, no build step, no bundler. No `localStorage` or
`sessionStorage`.

### 8.1 Behaviour

- On load: read `?date=` and `?lang=` from the URL. Default date = the most recent date in
  `index.json` that is ≤ today (US Eastern); default lang = `en`.
- Fetch `data/index.json`, then `data/<date>.json`.
- Render items **in file order** (already newest-first). No client-side sorting or grouping.
- Date navigation lists only dates present in `index.json`, newest first, with item counts.
  Selecting one updates the URL via `history.replaceState` so it can be copied and shared.
- **Language toggle sits in the top-right corner** (R6). It swaps every string on the page —
  chrome, headlines, summaries, tag labels — with no page reload and no refetch, because both
  languages ship in the same JSON. It persists to `?lang=`.
- Empty day: a plain line saying there is no relevant news for that date, plus one sentence
  noting the brief refreshes every three hours. Never a blank page.
- Failure to load `index.json`: show a short message telling the reader to reload. Never a
  silent blank.
- Header shows the last-updated timestamp and the refresh cadence — the visible proof that the
  system runs itself.

### 8.2 Item rendering

```
<headline>
<2–4 sentence summary>
Read the original (Source) · 12 Sept, 21:30 ET [· also reported by x.com, y.com]
[Key] [GP tag] [GP tag] [sub-sector tag] [sub-sector tag]        ← small print
```

Tags, in a single row, in this order: importance (only when `importance == 1`), then GP tags,
then sub-sector tags. Three visually distinguishable classes:

| Class | Appears when | Treatment |
|---|---|---|
| Importance | `importance == 1` only | The one tag carrying the accent colour. Label: `Key` / `重点`. Never rendered for Tier 2 |
| GP | one per matched manager | Filled, distinct hue from sub-sector |
| Sub-sector | one per matched sub-sector | Filled, quieter than GP |

All three are clearly smaller and quieter than the summary text (R9). The importance tag is the
loudest of the three but must still read as small print — it is a marker, not a badge. Region is
**not** a tag; if shown at all it belongs in the source line.

Because ordering is chronological, a Tier 1 item may well sit below Tier 2 items. That is
intended: the reader scans down the day in time order and the tag catches the eye where it
matters.

### 8.3 Design constraints

- Mobile first: the reviewer will open this on a phone from an email link.
- Readable at arm's length; body text no smaller than 16px on mobile.
- Line length under ~75 characters.
- CJK gets a suitable font stack and slightly looser line-height than the Latin text.
- Visible keyboard focus; `prefers-reduced-motion` respected; sufficient contrast.
- Restrained palette. This is a document for an investment professional, not a product landing
  page. One accent colour, used sparingly.
- Timestamps display in ET with the zone labelled, matching the date buckets.

---

## 9. Prompts (`config/prompts/`)

### 9.1 `agent2_filter.md`

System prompt containing: the LP framing; the full GP list with `credit_scope` text injected;
the sub-sector list; the credit-only rule (§4.3) stated as the central constraint; the
importance tiers and exclusion list (§6.2) in full; the homonym warning (§4.4); the tie-break
rules; and the instruction to judge only from the supplied headline and snippet, never from
assumed facts. Rule-layer tags are presented as fallible hints.

### 9.2 `agent3_second_look.md`

System prompt containing: the same portfolio context; an explicit statement that these items
were **already rejected** and the job is to find mistakes, not to agree; the inverted question
(*"if this is true, which holding is affected and how?"*); the instruction that the full article
text is now available and may contain what the headline omitted; the instruction to look across
the batch for clustering; and a warning not to rescue an item merely because it is *about*
finance — the test remains a concrete link to a tracked manager or sub-sector.

### 9.3 `summarize.md`

System prompt containing: the reader (an investment manager scanning on a phone); the grounding
rule; the 2–4 sentence limit; the no-opinion rule; the independent-Chinese rule; and the
glossary.

Glossary (use consistently; extend rather than improvise):

```
private credit 私募信贷 · direct lending 直接贷款 · BDC 业务发展公司（BDC）
GP stakes GP 股权投资 · aircraft leasing 飞机租赁 · asset-based lending 资产支持贷款
asset-based finance 资产支持融资 · real estate credit 房地产信贷
commercial real estate 商业地产 · mortgage 抵押贷款 · MSR 抵押贷款服务权
RMBS 住房抵押贷款支持证券 · CLO 贷款抵押证券（CLO） · leveraged loan 杠杆贷款
broadly syndicated loan 广泛银团贷款 · unitranche 单一分层贷款 · non-accrual 非应计
PIK 实物支付（PIK） · redemption 赎回 · NAV 净资产值 · spread 利差 · basis points 基点
default 违约 · covenant 契约条款 · fund close 完成募集 · first/final close 首轮/最终关账
tender offer 要约收购 · secondaries 二级市场交易 · fundraising 募资 · dividend 分红
leverage 杠杆 · credit facility 信贷额度 · borrower 借款人 · lender 贷款方
sponsor 私募股权发起人 · software 软件 · SaaS 软件即服务
```

---

## 10. Testing

### 10.1 Trap cases — `tests/fixtures/trap_cases.json`

These must behave correctly before shipping. Each fixture is a synthetic item clearly marked
`[TEST]` so it can never reach production output.

**Must be EXCLUDED:**

1. `KKR agrees to buy a theme-park operator for $4bn` — PE buyout by a tracked GP. *The single
   most important test in the suite.*
2. `Apollo and KKR to back Atlantic Aviation at ~$10bn valuation` — aviation **infrastructure
   equity**, not aircraft-leasing credit. Tests that the aircraft-leasing keywords do not drag in
   equity deals.
3. `Apollo 11 anniversary exhibition opens at museum` — homonym.
4. `Guggenheim Museum unveils new wing` — homonym.
5. `Bain & Company report says consulting demand rebounds` — wrong Bain.
6. `PAG dealership group posts record quarter` — Penske Automotive Group.
7. `Basepoint Business Centres opens new workspace in Reading` — wrong Basepoint.
8. `HSBC reports group pre-tax profit` — the bank, not the asset manager.
9. `Blue Owl acquires net-lease retail portfolio` — real-estate equity by a tracked GP.
10. `New private credit interval fund launches for retail investors` — product marketing.
11. `Podcast: a veteran investor on the future of private credit` — no new fact.

**Must be INCLUDED:**

12. `Blue Owl Technology Finance reports Q2 results, NAV $17.20, non-accruals 0.4%` — Tier 1.
13. `Bayview Asset Management acquires $10bn mortgage servicing portfolio` — Tier 1.
14. `PAG closes $2bn Asia private credit fund` — tracked GP, tracked sub-sector, Asia allowed.
15. `CLO AAA spreads tighten to 120bp as issuance sets monthly record` — Tier 2, sub-sector only.
16. `European direct lending deal count falls in Q3` — Tier 2, Europe.
17. `Software borrowers face refinancing squeeze as AI reshapes demand` — Tier 2, the sub-sector
    thesis the portfolio is exposed to.
18. `Bain Capital launches JB Aircraft Finance` — tracked GP entering a tracked sub-sector.

**Agent 3 recall test:** an item whose headline names no tracked manager (`Mid-market software
firm X misses interest payment`) but whose body names a tracked BDC as lead lender. Agent 2
(headline + snippet only) should discard it; Agent 3 (with the body) should rescue it. This is
the test that proves Agent 3 earns its cost.

### 10.2 Unit tests

- `dedupe`: tracking params stripped; two outlets' copies of one story collapse to one with the
  higher-priority link primary.
- `entities`: every homonym row in §4.4.
- `day_key`: an article at 21:30 ET on 12 Sept and one at 02:00 UTC on 13 Sept both land on
  `2026-09-12`.
- `store`: running twice produces no duplicates; retention deletes the right files.

### 10.3 Offline mode

`--mock-llm` replaces all three model calls with deterministic rule-based stand-ins, and
`--from-fixture` loads items from JSON instead of the network, so the whole pipeline and the
frontend can be exercised with no API key. `--dry-run` stops after the rule layer and prints the
funnel.

---

## 11. Deliverables

The client asked for four things. Map them explicitly:

1. **Working webpage** — the GitHub Pages URL.
2. **Source code and scripts** — this repository.
3. **How it updates** — `README.md` §"Updating", covering: fully automatic; GitHub Actions cron
   every 3 hours; manual trigger via `workflow_dispatch`; no human in the loop; failure of a
   single source does not stop a run; the page is static so it keeps serving even if a run fails;
   90-day rolling window.
4. **Write-up** — `README.md` §"How news is selected" and §"Limitations and next steps".

### 11.1 Write-up: filtering logic (4a)

Cover: the source list and why those sources; the entity model and the GP × sub-sector matrix;
the credit-only rule with a worked example of something excluded; the three-agent pipeline and
what each agent adds; the importance tiers and the exclusion list in full; **real funnel numbers
from `_runs.jsonl`**, not adjectives; the actual measured US/Europe split.

### 11.2 Write-up: limitations and next steps (4b)

Be specific and honest:

- **Paywalls.** The best private-credit reporting (Bloomberg, 9fin, Creditflux, PDI, PitchBook
  LCD) is subscription-only. Headlines and standfirsts are reachable; the substance is not.
- **Non-public information.** LP quarterly reports and fundraising materials are not public, and
  they often matter more than news.
- **The borrower blind spot.** A story about a software company being downgraded will be missed
  if it does not name a tracked manager, even when that company sits in a tracked BDC's book.
  *Next step: parse BDC 10-Q schedules of investments into a portfolio-company watchlist and add
  it to the entity table.* This is the highest-value improvement available.
- **Aircraft leasing is the thinnest row in the matrix** — see §4.1 VERIFY.
- **Private markets are quiet by nature.** Direct-lending deals frequently are not reported at
  all; news coverage lags and is incomplete.
- **No human feedback loop.** Borderline judgements will drift over time. *Next step: a
  thumbs-up/down control on each item, feeding confirmed decisions back as few-shot examples.*
- **Chinese output is unreviewed.** The glossary enforces consistency, not idiomatic quality.
- **Agent 3's rescue log is a diagnostic** — report what it has been rescuing, because the
  pattern names the filter's blind spots better than any prose can.

---

## 12. GitHub Actions (`.github/workflows/update.yml`)

```yaml
name: update-brief
on:
  schedule:
    - cron: "0 */3 * * *"      # every 3 hours, UTC
  workflow_dispatch:            # manual trigger — satisfies "manual update" in deliverable (3)
permissions:
  contents: write
concurrency:
  group: update-brief
  cancel-in-progress: false
jobs:
  update:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12", cache: pip }
      - run: pip install -r requirements.txt
      - run: python -m pipeline.run
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          EDGAR_USER_AGENT:  ${{ secrets.EDGAR_USER_AGENT }}
      - name: Commit
        run: |
          git config user.name  "credit-brief-bot"
          git config user.email "actions@github.com"
          git add site/data
          git diff --staged --quiet || git commit -m "brief: $(date -u +%Y-%m-%dT%H:%MZ)"
          git push
```

GitHub Pages: serve from the default branch, `/site` folder.

Note the cron caveat: scheduled workflows on free runners can be delayed by several minutes
under load, and GitHub disables schedules on repositories with no activity for 60 days. Neither
matters over the assessment window, but mention the first in the README so a late timestamp is
not mistaken for a fault.

---

## 13. Build order

Build in this sequence and verify each step before moving on.

1. **Skeleton + config loading.** `entities.yaml` with all 13 GPs, 8 sectors, the matrix, and
   homonym handling. Unit-test the matcher against §4.4 before writing anything else.
2. **Dedupe + store + `day_key`.** Unit tests green.
3. **Offline pipeline.** `--mock-llm --from-fixture tests/fixtures/trap_cases.json`. Every
   fixture reaches the rule layer with the expected tags.
4. **Frontend against fixture data.** Verify: reverse-chron order (a Tier 1 item sitting below
   a Tier 2 item is correct, not a bug), the top-right toggle swaps the entire page, tags render
   small with the `Key` tag only on Tier 1, empty state works, mobile layout holds, URL carries
   date and language.
5. **Agent 2** with the real API. Run the trap fixtures. **Every "must exclude" case must be
   excluded and every "must include" case included before proceeding.** Iterate the prompt until
   this holds.
6. **Agent 3** with the recall test. Confirm it rescues the planted item and that its shortlist
   scoring behaves.
7. **Summariser.** Check grounding by reading 5 summaries against their sources. Check the
   Chinese reads as written, not translated.
8. **Live run.** Real sources, one full pass. Read every kept item. If any item would make the
   reviewer frown, fix the prompt, not the item.
9. **Deploy.** Pages live; Actions secret set; **trigger the workflow manually and confirm it
   commits and republishes on its own.** A pipeline that only ever ran locally is not done.
10. **README.** Write the two write-up sections using the real numbers now in `_runs.jsonl`.

---

## 14. Open questions for the user

Resolve these before step 1; each changes the entity table or the prompts.

1. **Aircraft leasing** — which of the 13 managers carries this exposure? (§4.1 VERIFY)
2. **Basepoint** — confirm the identification: BasePoint Capital / BasePoint Group, New York,
   asset-based financing to specialty-finance originators, MCA and small-business ABS, acquired
   International Personal Finance (UK) in Dec 2025. Correct if this is a different firm.
3. **PAG and Asia** — PAG's news flow is overwhelmingly Asian, but the brief specifies US and
   Europe. Include PAG's Asian credit news (recommended, since PAG is a tracked holding), or
   restrict to its US/European activity?
4. **Default language** — `en` is the default in this spec (the brief is in English and the
   reader is the investment manager). Change if the team reads primarily in Chinese.
