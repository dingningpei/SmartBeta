# Discovery loop — roadmap (FROZEN 2026-10-08; user-approved; each milestone still needs its own authorization)

- **Status:** frozen roadmap (user approval 2026-10-08: "按照你的建议"). It is a roadmap, not a methodology. Every milestone below needs its
  own authorization, and every methodology or data change needs its own freeze.
- **Goal:** a loop that can produce **new information about alpha** under the project's
  discipline: point-in-time inputs, preregistration, multiplicity accounting, sealed
  confirmation data, and the declared data-sensitivity model (P-Q).
- **Why a new plan:** the 7 Campaign-1 hypotheses are textbook price factors whose
  published effects decay. McLean & Pontiff (2016): 58% lower post-publication. Hou, Xue
  & Zhang (2020): 65% of 452 anomalies fail |t| ≥ 1.96. They are also undetectable at the
  power the current data allows (about 1.2 annual net Sharpe needed for 50% power at
  m = 7).

## 1. What already exists (re-use, do not rebuild)

| Layer | Component | Status |
|---|---|---|
| Hypothesis generation | Phase 9 (`smart_beta/research/`: proposal registry, history, ResearchPolicy, generator boundary for LLM nondeterminism, FactorSpec admission, novelty/redundancy, stopping policy, state machine) | built and sealed; exercised once on Pilot 1A (26 DJIA names) |
| Knowledge / multiplicity | Phase 10 (`smart_beta/science/`: Knowledge-PIT, the GENERATOR / HUMAN channels, EvidenceFootprint, PreRegistration with families, Holm semantics, ScientificAssessment) | built and sealed |
| Inference for confirmatory claims | Phase-10 InferenceProcedure admission (Track P) | **zero admitted procedures**; Track P never started |
| Data | Dataset v1.2, admitted for development; D-SC cross-section of about 4,000 names; TOP_500 / TOP_1000 universes; reserve 2023-07 onward | admitted |
| Data sensitivity | P-Q v2.1 (software certified; PQ2-ADM PASS) | certifies **only the 7 C1 expressions** |

## 2. Hard prerequisites found during planning

1. **Certification breadth (methodology).** CE-ID is enumerative. Any generator-proposed
   expression → `PQV2_EXPRESSION_NOT_CERTIFIED`. A **P-Q v2.2 amendment** must certify
   **parameterized expression classes** derived from the existing proofs:
   - TRI-ratio windows of any (k1 < k2);
   - own-cell monotone transforms;
   - rolling windows of any length;
   - `standardize(M) × rank(V)` with any certified M and V.

   Anything outside the classes stays fail-closed.
2. **Confirmatory inference (methodology).** No InferenceProcedure is admitted, so no
   confirmatory claim is possible. Track P (PA-0 → P10-P → PA) must run before any
   reserve-based confirmation.
3. **Power (design).** About 10.7 total years of clean data (2016–2026-08) must be split
   across exploration, development screen and confirmation. Realistic targets must be
   **high signal-to-noise**:
   - event-driven signals (several independent events per firm-year);
   - broader universes (mid caps, where anomalies are larger);
   - few strong hypotheses per confirmatory family (m ≤ 3).

   Every Campaign must carry a precommitted power analysis, with a minimum-detectable
   Sharpe target ≤ about 0.7.

## 3. Data expansion (cheapest first)

| Step | Source | Cost | What it unlocks | Main risk |
|---|---|---|---|---|
| D-a | the existing D-SC full cross-section / TOP_1000 | 0 (already admitted) | mid-cap breadth | higher trading costs; needs a cost-model review |
| D-b | **SEC EDGAR filing index** (10-Q / 10-K / 8-K Item 2.02 dates) | free | event dates with exact public timestamps, which are point-in-time by construction | **CIK ↔ D4-key mapping for delisted names**; the earlier SEC probe resolved only 2 of 45 ended keys by full-text search, so the bulk-index route must be measured first |
| D-c | **EDGAR XBRL financial data** (about 2009+) | free | fundamentals that fit the 2016+ window: earnings surprise proxies, accruals, profitability | XBRL tag heterogeneity; restatement and vintage handling (knowledge date = filing acceptance time) |
| D-d | Sharadar (purchase) | subscription | 1998+ survivorship-free prices + fundamentals | deferred; revisit only if D-a…D-c are insufficient |

**Each data step:** a feasibility probe → a Dataset v1.3 contract amendment → D-chain
tasks → an admission review.

**New input types need their own sensitivity rules.** Fundamentals and event dates are
not price CA suspects, so a P-Q-style "knowledge-date / vintage" model must be frozen for
them. It is part of the P-Q v2.2 scope or a sibling model.

## 4. Milestones (each a barrier; none authorized)

| # | Milestone | Output | Depends on |
|---|---|---|---|
| M0 | Campaign-2 pipeline validation | assessability evidence | option (a) COMPLETE (`c90b13c`; synthetic registration dry run PASS); option (b) not authorized |
| M1 | P-Q v2.2: parameterized certification classes (+ the fundamentals / event-date sensitivity model) | frozen methodology + implementation + I1–I15 | M0 |
| M2 | EDGAR feasibility probe (CIK mapping coverage for D4 keys incl. delisted; filing-date coverage by year) | go / no-go for D-b / D-c | DONE: CONDITIONAL GO (`docs/edgar_feasibility_m2.md`); follow-ups folded into M3 |
| M3 | Dataset v1.3: EDGAR events (+ XBRL subset) with point-in-time knowledge dates; includes the M2 follow-ups (mapping precision audit, `acceptanceDateTime` timing rule, 8-K Item 2.02 coverage, fail-closed unmapped keys) | admitted dataset | M2 go |
| M4 | Generator integration: the Phase-9 loop on Dataset v1.3; every generated candidate logged (GENERATOR channel); budget accounting | a loop that produces preregistrable proposals | M1, M3 |
| M5 | Track P: admit the first InferenceProcedure | the confirmatory-claim capability | — (parallel) |
| M6 | Campaign-3: a small preregistered family (m ≤ 3, strong priors, e.g. post-earnings-announcement drift variants in mid caps), with a precommitted power analysis, development screen, then confirmation on the reserve under Phase-10 assessment | the first result that can carry real information about alpha | M1, M3–M5 |

## 5. First candidate hypotheses (illustrative, not frozen)

Hypotheses with independent literature support **and** a high event count, robust to
publication decay in mid caps:
- **Post-earnings-announcement drift (PEAD):** a surprise proxy from the announcement
  return or the XBRL EPS change; hold 1–3 months. Events are dated by EDGAR filing /
  8-K 2.02.
- **Earnings-announcement premium:** returns around scheduled announcements.
- **Profitability / accruals in mid caps:** XBRL; monthly; costs modelled.

These are to be proposed through the generator **or** the HUMAN channel, and frozen
before any data is read for them.

## 6. Current order (user-approved)

M1 and M3 are designed in parallel. Each design stops at its own freeze barrier.

## 7. Non-claims

- This is a plan, not evidence. No hypothesis here has been tested. No data source here
  has been acquired or certified.
- The literature figures are cited from the original abstracts, verified 2026-10-08.
