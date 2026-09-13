# Credit Brief

A date-organised, bilingual (EN / 中文) news dashboard for a family office that is a limited
partner in the **credit strategies** of 13 named managers across 8 credit sub-sectors.

It runs itself: a GitHub Action harvests the news every three hours, three model passes decide
what belongs in the brief, and the result is committed as static JSON that GitHub Pages serves.
There is no server and no database.

| Deliverable | Where it is |
|---|---|
| (1) Working webpage | the GitHub Pages URL for this repository |
| (2) Source code and scripts | this repository |
| (3) How it updates | [§ Updating](#updating) |
| (4a) Write-up: how news is selected | [§ How news is selected](#how-news-is-selected) |
| (4b) Write-up: limitations and next steps | [§ Limitations and next steps](#limitations-and-next-steps) |

---

## The page

- Opens on today's news. No query string needed.
- Items are ordered **newest first, strictly by publication time**. Nothing is pinned and
  nothing is grouped by importance.
- Each item is a headline, two to four sentences, a link to the original, and small-print tags.
- The language toggle sits in the **top-right corner** and swaps the entire page — chrome,
  headlines, summaries and tag labels — with no reload and no refetch, because both languages
  ship inside the same JSON file.
- The URL carries the date and the language (`?date=2026-09-12&lang=zh`), so any view can be
  copied and shared.
- A quiet day is shown as a quiet day. The brief is never padded to look busy.

### The tags

| Tag | Appears when | Means |
|---|---|---|
| `Key` / `重点` | importance is Tier 1 only | one of the few things that matter today |
| Manager | one per confirmed manager | which holding this touches |
| Sub-sector | one per confirmed sub-sector | which exposure this touches |

The `Key` tag is a deliberate addition beyond the brief's literal request for manager and
sub-sector tags. It makes the system's judgement legible without disturbing the chronological
order, and it is binary on purpose: a tag that appeared on every item would carry no
information. Tier 2 items carry no importance tag, and Tier 3 never reaches the page.

Because the order is chronological, a `Key` item will often sit *below* an untagged one. That
is intended — the reader scans down the day in time order and the tag catches the eye where it
matters.

---

## Quick start

```bash
pip install -r requirements.txt

# Everything offline: no API key, no network. Deterministic stand-ins replace the
# three model calls and the trap fixtures replace the news sources.
python -m pipeline.run --mock-llm --from-fixture tests/fixtures/trap_cases.json

# Credentials for a live run. `.env` is gitignored and is never committed.
cp .env.example .env && $EDITOR .env

# Harvest real sources and stop after the rule layer, printing the funnel.
python -m pipeline.run --dry-run

# A full live run.
python -m pipeline.run

python -m pytest tests/ -q          # the trap fixtures are the contract
python -m http.server -d docs 8000  # view the page
```

### Credentials

Two secrets, both read from the environment and nowhere else. `pipeline/config.py`
reads them in `api_key()` and `edgar_user_agent()`; nothing writes them anywhere.

| Variable | What it is | Where to get it |
|---|---|---|
| `ANTHROPIC_API_KEY` | Agents 2 and 3 and the summariser | <https://console.anthropic.com/settings/keys> |
| `EDGAR_USER_AGENT` | `"your-project your-email@example.com"` — the SEC requires a contact address | any address you control |

**Locally**, put them in `.env` (gitignored, loaded automatically) or `export` them in
your shell. A real environment variable always wins over `.env`, so a stale local file
can never shadow what CI supplies.

**In GitHub Actions**, put them in *Settings → Secrets and variables → Actions →
New repository secret*, under exactly those names. `.github/workflows/update.yml`
passes them into the run step as environment variables, and a later step greps the
build output for credential-shaped strings and fails the job rather than commit one.

Never put a key in `config/`, in `docs/`, or in any tracked file. The repository has to
be public for Pages and Actions to be free.

| Flag | Effect |
|---|---|
| `--mock-llm` | replaces all three model calls with deterministic rule-based stand-ins |
| `--from-fixture PATH` | loads items from JSON instead of the network |
| `--dry-run` | stops after the rule layer and prints the funnel |
| `--lookback-hours N` | overrides the harvest window (use `168` for a first best-effort backfill) |
| `--max-items N` | overrides the per-run summary cap |
| `--data-dir PATH` | writes somewhere other than `docs/data` |

---

## Updating

**The brief updates itself. There is no human in the loop.**

- **Schedule.** `.github/workflows/update.yml` runs `python -m pipeline.run` on a cron every
  three hours (`0 */3 * * *`, UTC). Each run harvests, filters, summarises, writes JSON into
  `docs/data/`, commits and pushes. GitHub Pages redeploys on the push.
- **Manual update.** The same workflow accepts `workflow_dispatch`: Actions → *update-brief* →
  *Run workflow*. It takes two optional inputs — `lookback_hours` (use `168` for a wider
  first sweep) and `mock` (run with no API spend).
- **Failure is contained.** Every source is fetched in isolation; a dead feed logs a warning and
  contributes nothing. One broken source never fails a run. And because the site is static, a
  completely failed run changes nothing the reader sees — the last good build keeps serving.
- **The tests gate the deploy.** The workflow runs `pytest` before building. A change to the
  entity table or the prompts that breaks a trap fixture cannot reach the page.
- **Nothing is paid for twice.** A URL-hash seen-set means no item is ever classified or
  summarised in two different runs, so re-running is free and safe.
- **Storage.** A 90-day rolling window; older day-files are deleted and the seen-set is pruned
  to match. Each day is tens of kilobytes, and git history is a free audit trail.
- **Secrets.** `ANTHROPIC_API_KEY` and `EDGAR_USER_AGENT` live in GitHub Actions Secrets and are
  read from the environment. The workflow greps the build output for credential-shaped strings
  and refuses to commit if it finds one.

### Keeping it alive

The page and the pipeline fail independently, which is the point of the design.

**The page.** It is static files on a public branch, served by GitHub Pages. It stays up
for as long as the repository exists, stays public and has Pages enabled — no server, no
certificate to renew, no dependency that can expire. Every run that has ever succeeded is
in git history, so nothing is lost even if `docs/data` were deleted. The one thing that
changes the URL is renaming the account or the repository; a custom domain would insulate
against that.

**The updates.** Four things can stop them, in rough order of likelihood:

| What stops it | What the reader sees | What to do |
|---|---|---|
| `ANTHROPIC_API_KEY` expires, is revoked, or the account runs out of credit | The last good build, plus the staleness notice | Replace the secret |
| GitHub disables the schedule after 60 days of repository inactivity | Same | Not expected here: every run rewrites `index.json` and appends to `_runs.jsonl`, so every run commits and the repository is never inactive. GitHub also emails before disabling |
| A source changes its feed URL | Nothing — one dead source logs a warning and contributes nothing | Fix it when the run log shows it returning zero |
| The workflow itself breaks | Same as above | GitHub emails the owner on a failed run |

In every one of those cases the page keeps serving. That is deliberate: a static site cannot
go down because a pipeline run failed. The risk it creates is a page that quietly presents
week-old news as current, so when the last successful run is older than three scheduled
intervals the page says so, in both languages, above the items.

**Cost** is the only recurring commitment: the Anthropic API, targeted at under US$0.50 a
day. Runners and hosting are free for public repositories. **Storage** is a 90-day rolling
window on the served files; raise `retention_days` in `config/settings.yaml` to keep more,
though git history retains every day regardless.

One caveat worth knowing so a late timestamp is not mistaken for a fault: scheduled workflows on
free runners can be delayed by several minutes under load, and GitHub disables schedules on
repositories with no activity for 60 days.

### Deployment checklist

1. Push this repository to GitHub (public, so Pages and Actions are free).
2. Settings → Pages → deploy from the default branch, `/docs` folder. (GitHub serves a branch
   folder only from `/` or `/docs`, which is why the site does not live in `site/`.)
3. Settings → Secrets and variables → Actions: add `ANTHROPIC_API_KEY`, and
   `EDGAR_USER_AGENT` as `"your-project your-email@example.com"` — the SEC requires a
   descriptive User-Agent with a contact address.
4. Actions → *update-brief* → *Run workflow*, optionally with `lookback_hours: 168`.
5. Confirm the run committed to `docs/data` and the page updated. **A pipeline that has only
   ever run locally is not finished.**

---

## How news is selected

### The sources, and why these

| Source | Type | Priority | Why |
|---|---|---|---|
| SEC EDGAR full-text search | primary documents | 4 | 8-K / 10-Q / 10-K for the listed BDCs in the portfolio — OTF, OBDC, FSK, BCSF, MFIC, KREF. Free, structured, unpaywalled, and first-party. A NAV mark or a new credit facility appears here before it appears anywhere else |
| SEC and Federal Reserve press feeds | primary documents | 4 | A rule affecting private credit, BDCs, CLOs or mortgages is a Tier 1 event by definition, and these are the announcements of exactly that |
| Trade press RSS — Alternative Credit Investor, ABF Journal, Private Equity Wire, HousingWire, National Mortgage News, Private Debt Investor | curated | 3 | Already filtered by humans who cover this asset class. Priority 3 means their copy of a story wins the primary link |
| Bing News RSS | aggregator | 2 | Direct publisher links, wide reach |
| Google News RSS | aggregator | 1 | Broadest recall; redirect links, so it is used as the fallback copy |

Priority decides which copy of a duplicated story becomes the primary link, so the reader is
sent to the press release rather than to an aggregator's rewrite of it.

Queries are **generated from the entity table**, not hand-written: one per manager, one per
weak alias with its disambiguating context, one per named vehicle, one per sub-sector, a
Europe-specific variant of each sub-sector, and one per macro theme. Sixty queries in total.
Adding a manager to `config/entities.yaml` adds its queries, its matching and its prompt scope
in a single edit.

### The entity model

`config/entities.yaml` is the geology under everything. It encodes the manager × sub-sector
matrix, because the brief's two lists are not independent — they pair up, and a sub-sector
story is only useful if it can be connected back to a holding.

| Sub-sector | Primary managers | Confidence |
|---|---|---|
| Software (tech lending) | Blue Owl, OTF, KKR Credit, Apollo, Neuberger Berman | high |
| Private credit / direct lending | Blue Owl, OTF, KKR, Apollo, Bain Capital Credit, NB, HSBC AM, PAG | high |
| GP stakes | Blue Owl (GP Strategic Capital, ex-Dyal), Neuberger Berman | medium |
| Aircraft leasing | Guggenheim, Bain Capital (JB Aircraft Finance), KKR, Apollo | **medium — see limitations** |
| Asset-based lending | BasePoint, Apollo (Atlas SP, MidCap), KKR ABF, HSBC AM, Bayview | high |
| Real estate credit | Pretium, KKR, Apollo, PAG, HSBC AM | high |
| Mortgage | Bayview (MSR, RMBS, Lakeview), Pretium (Progress Residential) | high |
| CLO | CIFC, Guggenheim, NB, Bain Capital Credit, KKR, Blue Owl | high |

Each manager carries strong aliases (a word-boundary hit is enough), weak aliases (which need a
co-occurring context term), context terms, exclusion terms that veto the manager outright, and
its vehicles and tickers. Acronyms match case-sensitively; everything else does not; everything
matches on word boundaries.

That machinery exists because these names collide with unrelated subjects, and a name alone is
never evidence:

| Token | Wrong match it must survive |
|---|---|
| Apollo | Apollo 11 and NASA, Apollo Hospitals, Apollo Tyres, the Apollo Theater |
| Guggenheim | the museum, the fellowship, Bilbao |
| PAG | Penske Automotive Group, which trades under those letters |
| Bain | Bain & Company, the consultancy |
| NB | the bare letters are never matched — only "Neuberger Berman" or a named NB unit |
| Bayview | hospitals, avenues, neighbourhoods |
| Basepoint | Basepoint Business Centres, a UK serviced-office operator |
| HSBC | HSBC the bank, as distinct from HSBC Asset Management |
| OTF | ordinary uses of those three letters |

### The credit-only rule

This is the most important rule in the system, and it is treated as a correctness constraint
rather than a matter of taste.

The family office is an LP in these managers' **credit** strategies. It is not an investor in
their private equity, their infrastructure, their real-estate equity or their venture funds. So
`entities.yaml` states an explicit IN / OUT boundary for every multi-strategy manager, and that
prose is injected verbatim into the filter prompts. For KKR, for example: in scope are KKR
Credit, FSK, KREF, real-estate credit, Global Atlantic as the credit-funding balance sheet, and
firm-level events bearing on the credit platform; out of scope are private-equity buyouts,
infrastructure equity, real-estate equity and portfolio-company news with no credit angle.

**A worked example, from a live harvest on 13 September 2026.** The harvest surfaced
*"Apollo Global Is Said in Talks to Acquire J&J's Orthopedics Unit"*. The rule layer matched it
to Apollo and passed it up — deliberately, because hiding it at the keyword stage would keep the
decision out of the audit log. Agent 2 then rejected it: Apollo is a tracked manager, but an
orthopedics buyout is private equity, and private equity is not a business line this family
office is an LP in. The same harvest surfaced *"Apollo Global Management curbed redemptions in
its private credit fund"* — same manager, different business line, and a Tier 1 event.

Two further cases are carried as permanent test fixtures because they are the ones most likely
to slip through: the August 2026 Apollo/KKR investment in **Atlantic Aviation**, which is
aviation *infrastructure* equity and not aircraft-leasing *credit* despite aircraft leasing
being a tracked sub-sector; and a Blue Owl **net-lease retail portfolio** acquisition, which is
real-estate equity despite real-estate credit being tracked.

### The three passes

**The rule layer** runs first and is free. It matches the entity table and drops anything that
names neither a tracked manager nor a tracked sub-sector. It makes no relevance judgement — it
decides only whether an item is worth a model's attention — and its output is handed to Agent 2
explicitly labelled as hints that are frequently wrong.

**Agent 2 — the filter.** `claude-haiku-4-5`, twelve items per call, forced structured output so
the model cannot answer in prose. It receives the portfolio with every manager's credit scope,
the sub-sector definitions, the homonym warnings, the importance tiers and the exclusion list in
full. It returns, per item: relevant, credit-related, importance tier, the manager and
sub-sector ids it actually confirmed, the region, and a one-line reason written to the audit log.
Items are kept when `relevant && importance <= 2 && (a manager or a sub-sector was confirmed)`.
Everything else goes to a discard pool **with its reason** — it is not thrown away.

**Agent 3 — the second look.** `claude-sonnet-5`. The cost of a false negative (missing OTF's
non-accruals jumping) is far higher than the cost of a false positive (one dull item), so Agent 3
audits what Agent 2 discarded.

The design constraint that makes this worth paying for: Agent 3 must not re-ask Agent 2's
question with the same information, because that produces the same answer, forever, at a cost.
It therefore has strictly more to work with, in three specific ways.

1. **More text.** Agent 2 saw a headline and a snippet. Agent 3 fetches the article body. The
   fact that matters — which lender led the facility — is usually in the fourth paragraph.
2. **A wider view.** Agent 2 judged each item alone. Agent 3 receives the shortlist as a batch
   and is asked to spot clustering: five separately unremarkable items about one borrower are
   collectively a signal.
3. **A different question.** Not *"is this relevant?"* but **"if this is true, which manager or
   sub-sector in the portfolio is affected, and how?"** That inversion catches the story that
   never names a tracked manager but hits an underlying exposure.

It does not see the whole pool. A weighted scorer ranks the discards — a rule-layer manager match
that Agent 2 rejected scores +3, a first-party or EDGAR source +3, tracked trade press +2, a
named vehicle or ticker +2, a Tier 3 that was nonetheless credit-related +2, a hard credit-event
word in the title +2, a sub-sector hit with no manager +1, and no rule match at all −5 — and at
most the top 25 are sent. Rescued items go straight to the summariser; they are not returned to
Agent 2, because that round trip adds no information and Agent 3 is the better-informed judge.

If Agent 3 rescues more than 40% of its shortlist on three consecutive runs, the run log records
a loud warning: that means Agent 2's threshold is mis-set and a human should look.

**The summariser.** `claude-sonnet-5`, one call per kept item, forced structured output. It
fetches the article body first and falls back to the snippet when extraction fails, recording
which in `grounded_on` so the write-up can report how much of the brief rests on a paywalled
standfirst. The grounding rule is stated plainly in the prompt: use nothing beyond the supplied
text, and if only a snippet is available, write less — a fabricated number is worse than no
summary, because it destroys the reader's trust in every other item on the page. The Chinese is
**written independently rather than translated**, from a fixed glossary so terminology stays
consistent across days; manager names, fund names and tickers stay in English inside it.

### The importance tiers, in full

**Tier 1 — key.** A direct event at a tracked manager's credit business: a fund close, BDC
earnings, a NAV / dividend / non-accrual disclosure, a redemption gate, a change to a credit
facility, an SEC filing carrying new information, a credit-business leadership change, a
regulatory or legal action, or a material portfolio credit event. Or a market-moving sub-sector
event: a large default or restructuring, a rule affecting private credit, BDCs, CLOs or
mortgages, or a central-bank decision with clear credit implications.

**Tier 2 — notable.** Sector data and trends — default rates, spreads, issuance, fundraising
totals. A tracked manager's ordinary transaction in a tracked sub-sector. Notable European
developments. Credible reporting on stress or opportunity.

**Tier 3 — excluded, and never shown.**

- product marketing, and retail feeder / ETF / interval-fund launches with no portfolio
  consequence;
- interviews, op-eds, columns and listicles containing no new fact;
- conference notices and event announcements;
- awards and league-table promotions;
- personnel hires below head-of-business level;
- **non-credit deals by multi-strategy managers**;
- passing mentions, where the manager or sub-sector is incidental;
- share-price commentary with no underlying event;
- a duplicate of something already published that day.

Tie-breaks are asymmetric on purpose: between Tier 2 and Tier 3 choose Tier 3, and between
Tier 1 and Tier 2 choose Tier 2. The brief is short by design, and a thin honest day beats a
padded one.

### US and Europe

The brief is primarily US-focused with roughly a fifth of attention on Europe. **That is treated
as a property of the output, not of the queries.** There are dedicated European queries so that
European stories are reachable, every item carries a region assigned by Agent 2, and a rolling
14-day European share is computed and logged. There is **no quota and no forced injection**. If
a week's European news is thin, the share is low, and the honest answer is to report the number
rather than engineer it.

Asia is not a target region. Asian news enters only when it concerns a tracked manager directly —
PAG is Hong Kong-based, HSBC Asset Management has Asian operations — or a tracked sub-sector.
PAG closing an Asian private credit fund is in scope because PAG is a holding, not because Asia
is covered.

### The numbers

<!-- BEGIN GENERATED NUMBERS -->
> **How these were produced.** Agent 1 and the rule layer ran normally against live
> sources. Agents 2 and 3 and the summariser were applied **by hand**, following the
> rules in `config/prompts/`, because this build had no `ANTHROPIC_API_KEY`. The
> harvest, dedupe and rule-layer figures are therefore machine-measured; the filter and
> summary figures are hand-applied, and the token usage is zero. Re-run
> `python -m pipeline.report` after the first live run to replace this block.

_Measured over 1 runs, 2026-09-13T14:27:58Z to 2026-09-13T14:27:58Z. Regenerate with `python -m pipeline.report`._

| Stage | Items |
|---|---:|
| Items fetched from all sources | 4,360 |
| Published inside the lookback window | 287 |
| After URL canonicalisation and title clustering | 287 |
| Matched a tracked manager or sub-sector (rule layer) | 106 |
| Not seen in an earlier run | 106 |
| Kept by Agent 2 | 26 |
| Shortlisted from Agent 2's discard pool | 0 |
| Rescued by Agent 3 | 0 |
| Summarised and published | 26 |

Across 3 days, **26 items** were published, of which **3** (12%) carry the `Key` tag.

Region mix, measured on published output:

| Region | Items | Share |
|---|---:|---:|
| US | 17 | 65% |
| Europe | 7 | 27% |
| Asia | 0 | 0% |
| Global | 2 | 8% |

Summaries grounded on the full article: **14/26** (54%); the rest on the headline and standfirst alone, which is what a paywall leaves reachable.

Coverage by manager:

| Manager | Items |
|---|---:|
| KKR | 3 |
| Apollo Global Management | 2 |
| Neuberger Berman | 1 |
| Blue Owl | 1 |
| Blue Owl Technology Finance | 1 |

Coverage by sub-sector:

| Sub-sector | Items |
|---|---:|
| Private credit | 16 |
| Mortgage | 6 |
| Asset-based lending | 5 |
| Software | 3 |
| CLO | 2 |
| Real estate credit | 2 |
| Aircraft leasing | 1 |

**Agent 3 rescued 0 items.** The pattern matters more than the count: what it keeps rescuing names a blind spot in the entity table.

Source failures recorded (a failing source logs a warning and contributes nothing; it never fails a run):

- `feed pdi unavailable` × 1
<!-- END GENERATED NUMBERS -->

**Harvest, measured against live sources on 13 September 2026** (`--dry-run`, so these are real
numbers for every stage up to and including the rule layer). Two shorter windows, for shape:

| Stage | 8-hour window | 48-hour window |
|---|---:|---:|
| Queries issued | 60 | 60 |
| Items fetched | 4,369 | 4,360 |
| Published inside the window | 4 | 86 |
| After URL canonicalisation and title clustering | 4 | 73 |
| Matched a manager or sub-sector (rule layer) | 4 | 50 |

The 8-hour column was measured on a Sunday morning US Eastern, and the newest item across all
publisher feeds at that moment was 13.5 hours old. The brief correctly showed a quiet window as
a quiet window. The 48-hour column is the more representative shape: roughly 4,400 raw items
collapse to 73 distinct recent stories, of which 50 name something in the portfolio and are
worth a model's attention. Of nine configured sources, eight returned items; Private Debt
Investor's public feed returned none, which is recorded below.

**Filtering, measured offline against the 19 trap fixtures** (`--mock-llm`, which exercises the
plumbing rather than the prompts' judgement): 19 items in, 13 candidates after the rule layer —
the six pure-homonym cases were vetoed before any model call and cost nothing — 7 kept, 6
shortlisted from the discard pool, 1 rescued by Agent 3, 8 published. All 11 "must exclude"
fixtures were excluded and all 7 "must include" fixtures were included.

> **Still to do before this goes in front of a reader.** Agents 2 and 3 and the summariser have
> never executed against the live API. The judgement they encode has been exercised by hand and
> the results are on the page, but the prompts themselves are untested at runtime. Step 5 of the
> build order is the gate: with `ANTHROPIC_API_KEY` set, run the trap fixtures and confirm that
> every "must exclude" case is excluded and every "must include" case included, then run
> `python -m pipeline.report` and replace the generated block above with the model's own
> numbers.

---

## Limitations and next steps

**Paywalls.** The best private-credit reporting — Bloomberg, 9fin, Creditflux, PitchBook LCD,
and Private Debt Investor itself — is subscription-only. Headlines and standfirsts are reachable
and are enough to triage; the substance is not. Private Debt Investor's public RSS feed returned
zero entries in every test run. The `grounded_on` field records, per item, whether a summary
rests on the full article or only on a snippet, so the size of this gap is visible in the data
rather than asserted here.

**Non-public information.** LP quarterly reports, capital-call notices and fundraising materials
are not public, and they routinely matter more than anything in the news. This system cannot see
them, and no amount of engineering changes that.

**The borrower blind spot — the most valuable thing to fix next.** A story about a software
company being downgraded is missed if it does not name a tracked manager, even when that company
sits in a tracked BDC's loan book. Agent 3 recovers some of these when the article body names the
lender, which is exactly what the planted recall fixture tests. But the systematic fix is to
parse the schedules of investments out of the listed BDCs' 10-Q filings — OTF, OBDC, FSK, BCSF,
MFIC — into a portfolio-company watchlist, and add it to the entity table as a fourth class of
entity alongside managers, vehicles and sub-sectors. EDGAR already supplies the filings, and the
matching machinery already exists. *This is the highest-value improvement available.*

**Aircraft leasing is the thinnest row in the matrix.** The brief names aircraft leasing as an
invested sub-sector, so at least one of the 13 managers carries that exposure, but which one is
inferred rather than confirmed: Guggenheim through its aviation-finance lineage, Bain Capital
through JB Aircraft Finance, KKR and Apollo through aviation ABF. The row is marked
`confidence: medium` in `entities.yaml`. Confirming the actual holding with the family office
would sharpen it immediately — and watching what Agent 3 rescues is the cheapest way to find out
whether the row is wrong, because a filter blind spot shows up as a rescue pattern.

**BasePoint is identified with medium confidence** as BasePoint Capital / BasePoint Group of
New York — asset-based financing to specialty-finance originators, MCA and small-business ABS,
and the December 2025 acquisition of International Personal Finance. If that is a different firm,
one edit to `entities.yaml` corrects it. The exclusion terms for Basepoint Business Centres hold
regardless.

**Private markets are quiet by nature.** Direct-lending deals frequently are not reported at all.
Coverage lags, it is incomplete, and a manager's best quarter and its worst can both pass without
a headline. A brief built on news will always under-report a market that does most of its
business privately.

**No human feedback loop.** Borderline judgements will drift as the prompt meets situations it
was not written for, and nothing currently corrects that drift. *Next step: a thumbs-up / down
control on each item, writing confirmed decisions back into the prompt as few-shot examples. The
audit log already stores every decision with its reason, so the data side of this is done.*

**The Chinese is unreviewed.** The glossary enforces consistency across days; it does not
guarantee idiomatic quality, and nobody who reads Chinese professionally has checked the output.

**Agent 3's rescue log is a diagnostic, not just a feature.** What it keeps rescuing names the
filter's blind spots better than any prose in this section can. `python -m pipeline.report`
prints the rescues with the exposure each one names; reading that list periodically is the
intended maintenance ritual for this system.

**Tier 1 calibration needs watching.** The tag means something only because it is rare — two to
four items on a normal day, none at all on a quiet one. If most of a day's items come back
Tier 1, that is a prompt bug, not a busy day, and it should be fixed in
`config/prompts/agent2_filter.md` rather than tolerated.

---

## Repository layout

```
config/
  entities.yaml            the 13 managers, 8 sub-sectors, aliases, homonyms, themes
  settings.yaml            runtime knobs and the source list
  prompts/                 agent2_filter.md, agent3_second_look.md, summarize.md
pipeline/
  config.py                config loading, US-Eastern date logic, secret access
  sources.py               Agent 1: query construction, fetchers, body extraction
  dedupe.py                URL canonicalisation and title clustering
  entities.py              the deterministic rule layer
  agents.py                Agents 2 and 3, the summariser, the offline stand-ins
  store.py                 JSON read/write, index, retention, seen-set, run log
  run.py                   orchestration and CLI
  report.py                turns the run log into the numbers this README quotes
docs/                      served by GitHub Pages straight from the branch
  index.html, assets/      the page: no framework, no build step, no storage
  data/                    generated and committed: index.json, YYYY-MM-DD.json,
                           _seen.json, _runs.jsonl
tests/
  fixtures/trap_cases.json the 19 cases that must behave correctly before shipping
  test_*.py
.github/workflows/update.yml
```

### Data contracts

`docs/data/YYYY-MM-DD.json` holds the day's items, **already sorted by `published_at`
descending** — the frontend renders in file order and never re-sorts, because importance is a
tag and must never become a position. `index.json` carries the date list with counts and the
bilingual tag names. `_seen.json` is the URL-hash set that makes re-runs free. `_runs.jsonl` is
one line per run: the full funnel, token usage, the EU share, every Agent 3 rescue with its
stated exposure, and any source errors. It is kept in the repository but is not served as part
of the page.

### Testing

```bash
python -m pytest tests/ -q
```

- `test_entities.py` — every homonym in the table, the acronym case-sensitivity rule, the
  bare-`NB` prohibition, and the rule layer's candidacy contract.
- `test_dedupe.py` — tracking parameters stripped, aggregator and AMP redirects unwrapped, two
  outlets' copies of one story collapsed with the higher-priority link primary.
- `test_daykey.py` — 21:30 ET on 12 September and 02:00 UTC on 13 September land on the same day,
  in both US daylight and standard time.
- `test_store.py` — idempotency, ordering, retention boundaries, and that no internal field ever
  reaches a file the site serves.
- `test_trap_cases.py` — the 19 fixtures, plus the Agent 3 recall test and a check that the
  article body never leaks into the rule layer, which would make Agent 3 redundant.
- `test_frontend_contract.py` — every field the page reads is a field the pipeline writes, the
  toggle is top-right and refetches nothing, no browser storage is used, the `Key` tag renders
  for Tier 1 only, and the tags stay small print.

### Cost

Only the Anthropic API costs anything; the runners and the hosting are free. The rule layer
eliminates most items before any call, Agent 2 batches twelve at a time on the cheapest model,
Agent 3 sees at most 25 items per run, summaries are written only for kept items and capped at
20 per run, and the seen-set means nothing is paid for twice. Target: under US$0.50 a day.
