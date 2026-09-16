# Phase 3 Frozen Architecture: Point-in-Time Research Data Foundation

Baseline: `master` at commit `371be00` (Phase 2 complete, tagged
`phase2-complete`, 143/143 tests green).

This document is the frozen record of Phase 3's design, including four
refinements made before any Wave 1 code was written. It supersedes the
earlier draft's `discovery/`/`validation/` design (moved to Phases 6-7)
and incorporates the refinements below into the architecture already
approved in direction.

## Roadmap (Phases 3-7), unchanged in direction

| Phase | Scope |
|---|---|
| **3** | Point-in-Time Research Data Foundation (this document). Vendor-independent canonical PIT data model + trusted as-of query engine + adversarial synthetic fixture + compliance suite. No evaluation, registry, loop, or vendor choice. |
| **4** | Vendor adapter (choice made here, not before) + migration of existing research engines (`data.universe`, `factors.beta`, `engines.*`, `benchmarks.*`, `pipelines.*`) to consume the Phase 3 PIT boundary instead of today's `DataSource`, behavior-preserving against the existing 143 tests. |
| **5** | Declarative `FactorSpec` + trusted expression engine: hypothesis generation selects from a constrained vocabulary (fields, ratios, differences, rolling transforms, rank/winsorize/standardize, explicit lag-by-N); it cannot express temporal alignment, formation lag, universe construction, or return alignment, because those aren't in the language. |
| **6** | Evaluation protocol + robustness/multiple-testing layer: IS/OOS splitting, walk-forward validation, a true untouched final holdout, parameter-sensitivity, subperiod stability, turnover/cost-adjusted returns, exposure/redundancy vs. accepted factors, multiple-testing-aware acceptance. A fixed t-stat is at most one cheap initial screen inside this, never the definition of "validated." |
| **7** | Experiment registry (full evidence packages, not just accept/reject) + skeptical judge (checks new evidence against registry history before finalizing) + the orchestration loop. Hypothesis generation itself is the least-defined piece and gets its own design round when this phase is reached. |

## Four refinements incorporated into Phase 3

### Refinement 1 — Bitemporal semantics, explicit and decided per entity

Every PIT entity distinguishes **effective/event time** (when the fact
applies) from **knowledge/availability time** (when it became knowable).
The decision, made explicitly rather than mechanically generalized
everywhere:

| Entity | Bitemporal? | Effective-time column | Knowledge-time column | Vintage/append-only? |
|---|---|---|---|---|
| Fundamentals | **Yes** | `report_period_end` | `knowledge_date` | Yes — a restatement is a **new row** with a later `knowledge_date`; the original row is never overwritten. `is_restatement` marks non-original vintages for audit. |
| Corporate actions | **Yes** | `effective_date` (ex-date) | `knowledge_date` | Yes, same pattern — an amended/withdrawn action is a new row; `is_superseded` marks it. A split/dividend can be announced then later amended before its effective date, an analogous risk to a restatement. |
| Listing/delisting | **No** | `list_date`/`delist_date` | — | No. A delisting decision is not restated the way a financial figure is; the relevant PIT risk (survivorship bias) is a *completeness/query-behavior* concern — does the source still return this stock's history at an as-of date before its eventual delisting — not a knowledge-time-vs-effective-time modeling concern. Tested by the compliance suite (Refinement 4), not by an extra schema column. |
| Trading status (suspend/limit/ST) | **No** | (the date itself) | — | No. Same-day, immediately-observable facts by construction; effective time and knowledge time coincide. |
| Market data (returns), market cap | **No, by documented assumption** | (the trading date) | — | Not modeled in Phase 3. Real vendor delivery lag is a real but separate concern, deferred; the `knowledge_date` column name is reserved and the pattern already generalizes if this needs revisiting later — it is not a redesign to add it. |

`KNOWLEDGE_DATE_COL = "knowledge_date"` is one canonical name reused across
every bitemporal schema, so a single resolver algorithm ("latest row with
`knowledge_date <= as_of`, grouped by entity + effective-time column") can
be written once (Phase 3, Wave 2) and reused for both fundamentals and
corporate actions rather than two bespoke implementations.

### Refinement 2 — Single owner of corporate-action adjustment

`PITDataSource` does **not** have a `get_adjusted_returns()` method.
Splitting ownership between the interface and `pit/corporate_actions.py`
was the exact ambiguity this refinement resolves. Instead:

- **Vendor adapters** (and `SyntheticPITSource`) expose only raw facts:
  `get_raw_returns(...)` (vendor-native, unadjusted) and
  `get_corporate_actions(...)` (the fact table above).
- **`pit/corporate_actions.py`** (trusted package logic, Wave 2) is the
  **one** place that computes `compute_adjusted_returns(raw, actions) ->
  pd.DataFrame`. Nothing else performs this computation.
- This prevents double-adjustment by construction: a vendor that happens
  to also expose pre-adjusted prices is not queried for them by the
  canonical path at all. A vendor with *only* pre-adjusted data and no
  raw/actions available is an explicit, documented per-adapter exception
  (Phase 4 concern), never an ambiguous package-wide behavior.

### Refinement 3 — Enforcement boundary stated explicitly

**Phase 3 = the trusted PIT boundary exists and is compliance-tested** (in
isolation, via `SyntheticPITSource`). **Phase 4 = existing research
engines migrate behind that boundary**, making PIT usage package-wide.
Phase 3 alone does not make bypassing point-in-time discipline impossible
anywhere in the package — `smart_beta/data/*`, `factors/*`, `engines/*`,
`benchmarks/*`, `pipelines/*` continue to use today's `DataSource` and are
not touched. This is stated here so no later summary overclaims Phase 3's
completion the way "beta lag" and "market-cap lag" were each, in turn,
initially missed in Phase 2 before review caught them — the lesson
generalizes: state the boundary of what's actually proven, not what's
merely intended.

### Refinement 4 — Strengthened adversarial requirements (binding on P3-G/P3-H, later waves)

`SyntheticPITSource` and `compliance.py` must together contain deliberate,
named traps for at least:

1. future-announcement leakage (a query at `as_of=t` must never see a
   `knowledge_date > t` row);
2. restatement backfill leakage (an as-of query before a restatement's
   `knowledge_date` must see the original vintage, never the restated one);
3. delisted-name/survivorship omission (a source that silently drops a
   stock's history once it is known, today, to be delisted must fail);
4. calendar-month-end vs. exchange-trading-month-end mistakes (a
   deliberately constructed month whose calendar-last-day is a weekend/
   holiday must expose the discrepancy — this is why `TradingCalendar`
   below has `month_end_trading_date()` as a first-class method, not an
   afterthought);
5. corporate-action return discontinuity or double adjustment;
6. float-market-cap vs. total-market-cap semantic confusion (hence two
   separate, never-conflated columns, not one ambiguous `mcap`);
7. trading-status timing errors where relevant.

**P3-H must include meta-tests**: for each trap category, a deliberately
broken reference `PITDataSource` implementation, with a test proving the
compliance suite actually fails against it. A compliance test that cannot
be shown to fail on a broken implementation has no teeth. (Binding on
Wave 4; recorded here so it isn't lost between now and then.)

## Phase 3 subpackage layout

```
smart_beta/pit/
    __init__.py             # empty marker (already scaffolded; multi-owner package, no re-exports)
    calendar.py             # TradingCalendar                                    [P3-A, Wave 1]
    schema.py               # canonical PIT schemas + reuses data.schema's PanelSchema  [P3-B, Wave 1]
    corporate_actions.py    # compute_adjusted_returns() -- sole adjustment owner [P3-C, Wave 2]
    fundamentals.py         # latest_known_value() vintage resolver               [P3-D, Wave 2]
    source.py               # PITDataSource abstract interface                    [P3-E, Wave 2]
    view.py                  # PointInTimeView / AsOfSnapshot (trusted query engine) [P3-F, Wave 3]
    synthetic.py             # SyntheticPITSource (adversarial fixture)            [P3-G, Wave 3]
    compliance.py             # reusable compliance suite + meta-tests             [P3-H, Wave 4, standalone]
tests/test_pit_*.py
```

## Task table (P3-A..H)

| Task | Objective | Files | Depends on | Wave |
|---|---|---|---|---|
| P3-A | Trading calendar | `pit/calendar.py`, `tests/test_pit_calendar.py` | none | 1 |
| P3-B | Canonical PIT schemas + one Settings field | `pit/schema.py`, `config/settings.py` (additive), `tests/test_pit_schema.py` | none | 1 |
| P3-C | Corporate-action adjustment (sole owner) | `pit/corporate_actions.py`, tests | P3-B | 2 |
| P3-D | Fundamentals vintage resolver | `pit/fundamentals.py`, tests | P3-B | 2 |
| P3-E | `PITDataSource` interface | `pit/source.py`, tests | P3-A, P3-B | 2 |
| P3-F | `PointInTimeView` (trusted engine) | `pit/view.py`, tests | P3-C, P3-D, P3-E | 3 |
| P3-G | `SyntheticPITSource` (adversarial fixture) | `pit/synthetic.py`, tests | P3-A, P3-C, P3-D, P3-E | 3 |
| P3-H | Compliance suite + meta-tests | `pit/compliance.py`, tests | P3-F, P3-G (hard) | 4, standalone |

## Dependency DAG

```
Wave 1 (parallel)     P3-A (calendar)          P3-B (schema + 1 settings field)
                            |                        |     \        \
Barrier 1                    |                        |      \        \
                              v                        v       v        v
Wave 2 (parallel)        P3-E (source iface)    P3-C (corp actions)  P3-D (fundamentals)
                       (needs A,B)                 (needs B)            (needs B)
                              \_______________________|_______________________/
Barrier 2                                             |
                                    ___________________|___________________
                                   v                                       v
Wave 3 (parallel)             P3-F (view)                          P3-G (synthetic source)
                          (needs C,D,E)                          (needs A,C,D,E)
                                   \_______________________________/
Barrier 3
                                              v
Wave 4 (standalone)                    P3-H (compliance + meta-tests)
                                     (needs F,G -- hard deps)
```

## Status

Only Wave 1 (P3-A, P3-B) has worker specs, worktrees, and venvs as of this
commit. Waves 2-4 are designed above but not yet spec'd; each is written
against the actual merged APIs of the wave before it, per this project's
established practice.
