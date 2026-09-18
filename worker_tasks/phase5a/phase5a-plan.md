# Phase 5A Frozen Plan: First Real End-to-End Run (US / Tiingo / CAPM)

Baseline: `master` at `0d15655d64ed23480153631c90b5a0af3bcf3d4b` (Phase
4D-B complete, tagged `phase4d-b-complete`). This document is the frozen
record of Phase 5A's design, produced after a roadmap review and three
rounds of pre-spec methodology resolution (observation frequency, Gate B
universe framing, and risk-free holding-interval semantics). It is the
reference every P5A-1..5 worker spec points back to instead of
re-deriving context.

## Research objective (verbatim, binding)

Demonstrate for the first time that the existing trusted pipeline can
consume real Tiingo observations and a real, provenance-tracked
risk-free series and produce a reproducible, inspectable, hand-verifiable
CAPM `MKT` observation series end to end.

This is an integration/empirical pilot. It is explicitly **not**:
publication-grade CAPM replication; representative-US-market research;
investment-performance evaluation; portfolio-performance infrastructure.

## Why this phase exists (evidence, not assumption)

A prior roadmap review found that `smart_beta.benchmarks.capm.
compute_market_excess_return`, `smart_beta.research_inputs.*`, and
`smart_beta.vendors.tiingo.source.TiingoPITSource` are each independently
migrated, tested, and (for Tiingo) certified — but **no factor, on any
market, has ever been run from real vendor data through this pipeline to
a real, inspectable output.** `smart_beta.research_inputs.risk_free`'s
own module docstring states directly: "No real production US risk-free
data source is selected here. Choosing one remains an open, deliberately
deferred architecture/data decision." Phase 5A closes exactly this gap
and no more.

## Frozen observation semantics (do not redefine)

- **Frequency: daily.** `compute_market_excess_return` is frequency-
  agnostic; Tiingo's `adj_ret` is close-to-close over **consecutive
  vendor trading observations**, not a fixed calendar-day step. No
  monthly-resampling utility exists anywhere in this repository, and
  Phase 5A does not add one.
- **Weight:** the existing one-observation-per-stock lag of `total_mcap`
  (`total_mcap_lag`, `_PIT_WEIGHT_COL` in `capm.py`) — unchanged,
  untouched.
- **Market return:** `Rm_t` = value-weighted `adj_ret_t` across surviving
  eligible observations, weighted by `total_mcap_lag_t`, computed
  entirely by the existing `_market_factor`/`_value_weighted_returns`/
  `group_return_stats` chain. Missing-return exclusion, non-positive-
  weight exclusion, the inner-join tradability screen, and within-group
  weight renormalization are **all already-owned, already-tested
  behavior of the trusted pipeline** (`smart_beta/benchmarks/capm.py`,
  `smart_beta/engines/portfolio_sort.py`) — no Phase 5A task
  reimplements, wraps, or overrides any of it.
- **Final factor:** `MKT_t = Rm_t - Rf_t`, produced by the existing,
  unmodified `compute_market_excess_return`.

## Frozen risk-free source and transformation (do not redefine)

- **Source:** FRED series `DGS3MO` — Market Yield on U.S. Treasury
  Securities at 3-Month Constant Maturity, Quoted on an Investment
  Basis. Daily, percent, not seasonally adjusted, published on Treasury
  business days only.
- **Evidence limitation, preserved verbatim, never silently upgraded:**
  *"the short-maturity/simple-interest interpretation of DGS3MO is
  supported by mutually consistent Treasury.gov, FRED, and academic
  documentation, but the primary Treasury Yield Curve Methodology
  technical publication has not been directly read in full."* This is
  the single canonical sentence — every task that touches the
  risk-free provider or cites its semantics must reproduce it
  byte-for-byte (whitespace-normalized), never a paraphrase, softening,
  or drop.
- **Frozen transformation (a named Phase 5A approximation, not a claim
  of exactness):**

  ```
  Rf_t = (DGS3MO_source(t) / 100) * (delta_calendar_days(t) / 365)
  ```

  where `delta_calendar_days(t)` is the actual calendar-day gap between
  trading date `t` and the immediately preceding trading observation in
  the **same return panel** (Monday→Tuesday = 1; Friday→Monday = 3), and
  `DGS3MO_source(t)` is DGS3MO(t) if published, else the most recent
  **strictly prior** published DGS3MO observation. **`/252` compounding
  is explicitly rejected and must never be substituted** — the verified
  bond-equivalent-yield convention for a sub-182-day bill is simple
  interest on a 365-day year, and using the return's own actual elapsed
  calendar days (not a uniform trading-day step) is what makes `Rf_t`
  represent the same holding interval as `adj_ret_t`.
- **Non-tautological frozen test specimens (resolves B2):** the following
  expected values are canonical constants, computed with plain Python
  arithmetic on literals — never by calling `TreasuryBillRiskFreeProvider`,
  its transformation helper, or any other production code implementing
  this formula. Every task that tests this formula (P5A-1 and P5A-5) must
  compare production output against these frozen literals, not against a
  value the test itself derives by re-running the implementation.

  ```
  Specimen A: annualized yield = 5.00%, delta_calendar_days = 1
              expected rf = 0.05 * 1 / 365 = 0.0001369863013698630...

  Specimen B: annualized yield = 5.00%, delta_calendar_days = 3
              expected rf = 0.05 * 3 / 365 = 0.0004109589041095890...
  ```

  **Required negative guard:** also assert the production `rf` does
  **not** match what `/252` compounding would give for the same inputs
  (e.g. for Specimen A, `(1.05)**(1/252) - 1 = 0.0001939...`) by more
  than the stated tolerance — a test that would fail loudly if `/252`
  were ever substituted for the frozen `/365` simple-interest formula.

  In addition to these synthetic specimens, at least one **real** fixture
  specimen is required (P5A-1): the raw DGS3MO value, its source date,
  the two real equity dates involved, the hand-computed
  `delta_calendar_days`, and the hand-computed expected `rf` must all be
  recorded as frozen literals in the test (derived by the implementer
  with a calculator/independent script, not by calling the class under
  test), then compared against the fixture-replayed production output.
- **Staleness:** maximum 3 business days between the most recent
  available DGS3MO observation and the equity trading date being priced.
  Beyond that: **fail closed** with a named error — never a silently
  stale value.
- **First panel observation:** `Rf = NaN` (no preceding trading
  observation exists within the requested range to compute
  `delta_calendar_days` from). No task fetches outside the requested
  range to manufacture a first-row value.
- **No future leakage, structurally:** the lookup only ever scans
  strictly backward from `t`.
- **Retained diagnostic provenance per emitted `rf` observation:**
  equity trading date, preceding equity trading date,
  `delta_calendar_days`, DGS3MO source date used, raw annualized DGS3MO
  value, staleness (business days), and the transformed `rf` — carried
  as an internal/diagnostic surface on the concrete provider class, not
  as a change to the frozen two-column `RiskFreeProvider.get_risk_free`
  contract.

## Sanctioned Artifact-A decomposition (resolves B1 — CAPM ownership)

`compute_market_excess_return` (the existing, unmodified public function)
remains the **sole authority** for `MKT`. No Phase 5A task may call any
`smart_beta/benchmarks/capm.py` private helper (`_market_factor`,
`_value_weighted_returns`, `_value_weighted_by`, `_load_pit_panel`, or any
other `_`-prefixed name in that module), and no Phase 5A task may write a
second implementation of value-weighting, tradability screening, or lag
discipline to reproduce a market-return-shaped number. Artifact A's
`market_return`/`risk_free_return` columns are populated by exactly this
frozen algebraic decomposition, and no other:

```
authoritative_MKT  = compute_market_excess_return(...)  ["MKT" column]
authoritative_rf   = risk_free.get_risk_free(start, end) ["rf" column]
                      # risk_free is the SAME RiskFreeProvider instance
                      # passed into compute_market_excess_return, called
                      # a second time by the orchestrator for evidence —
                      # not a new provider, not a new call shape
derived_market_return = authoritative_MKT + authoritative_rf
```

This is arithmetic on two already-trusted outputs, not a parallel CAPM
implementation. **Required invariant, checked by an executable test:**
`derived_market_return - authoritative_rf == authoritative_MKT` (within
the frozen numerical tolerance, `1e-9` relative — see P5A-3). The
invariant is trivially true by construction; the test exists to catch a
future refactor that breaks the decomposition, not to prove the
decomposition itself.

**`universe_count` (Artifact A) — frozen, explicitly weakened definition:**
the count of names `get_tradability` (the existing, trusted, public
function, called with the exact same `policy`/`settings` instance passed
into `compute_market_excess_return`) marks tradable for that date. This
is stated explicitly as an **upper bound**, not an exact count of names
that actually entered that date's value-weighted `Rm` — a tradable name
can still be excluded downstream by the trusted pipeline's own
non-positive/missing-weight or missing-return screen (`capm.py`'s
`_value_weighted_returns`), which this diagnostic does not, and must not,
independently re-derive.

**Artifact B diagnostic fields — frozen, explicitly weakened where a
field cannot be obtained without private-API use or semantic
duplication:**
- Eligible/excluded names: from `get_tradability` only (tradability-policy
  exclusions, named with reason). Any *additional* exclusion the trusted
  pipeline applies internally (non-positive lagged weight, missing
  return) is **not** independently reproduced as named diagnostic
  evidence here — P5A-3's own by-hand reconstruction is where that full
  chain gets independently redone, for the one date it selects.
- Constituent `adj_ret`, lagged `total_mcap` per name: from the existing,
  trusted, public `get_realized_returns` / `get_capitalization_weights` /
  `smart_beta.data.align.lag_panel` calls (all already used, unmodified,
  by the trusted pipeline itself; calling them a second time for
  diagnostic display is reuse, not duplication, because they are pure
  functions of the same inputs).
- **Normalized per-name weights are explicitly *removed* from Artifact
  B's requirements.** The trusted pipeline's within-group renormalization
  depends on the full eligible/holdable set at that date (a cross-
  sectional computation this task does not have a trusted, public,
  non-duplicative way to reproduce). Artifact B instead reports the raw
  lagged `total_mcap` per eligible name — labeled explicitly as "raw
  trace evidence, not a reproduction of the trusted pipeline's internal
  normalized weight" — sufficient for P5A-3 to compute the true
  normalized weight by hand itself for its one chosen date.
- Risk-free diagnostic fields (raw source value, source date,
  `delta_calendar_days`, transformed `rf`): from P5A-1's own retained
  diagnostic provenance surface — unchanged.

**No Phase 5A task may "fix" a diagnostic gap by writing new CAPM logic.**
If a required diagnostic field cannot be obtained this way, it is removed
or weakened (as above), not reimplemented.

## RF date-grid ownership (resolves B4 — architectural mismatch)

The frozen RF transformation needs `delta_calendar_days(t)`, the calendar
gap between equity trading date `t` and the immediately preceding
trading date in the **same return panel**. The `RiskFreeProvider` ABC
(`get_risk_free(start, end) -> DataFrame[date, rf]`) has no way to
receive that sequence implicitly, and this plan does **not** change the
ABC (inspection found an interface-compatible design — below — so no ABC
change is justified).

**Ownership, frozen and exclusive:**
- `TreasuryBillRiskFreeProvider` (P5A-1) is constructed with an explicit
  trading-date sequence (interface option (a) in `task-p5a-1...md` — now
  **mandatory**, not merely preferred; option (b) is rejected because it
  cannot support the cross-check below). The provider itself never
  discovers, fetches, or guesses this sequence.
- **Construction happens outside `run_capm_pilot`** (its frozen signature
  receives `risk_free` already built), **by whoever assembles a pilot
  run** (the live-recording script, the offline test setup, or P5A-4 for
  Gate B) — using a single, reusable helper P5A-2 exports from
  `capm_pilot.py`, `derive_trading_dates(view, start, end)`, which is a
  thin wrapper over the exact same trusted, pure, public call the panel
  itself is built from: `get_realized_returns(view, start, end)`
  (unmodified, already used internally by `_load_pit_panel`). The caller
  builds its own `PointInTimeView` from the same `tiingo_client`/
  `tickers`/`start`/`end` it will also pass to `run_capm_pilot`, calls
  `derive_trading_dates` on it, and constructs
  `TreasuryBillRiskFreeProvider(dgs3mo_source, trading_dates=<those exact
  dates>)`.
- **Validation happens inside `run_capm_pilot`,** because only it (via
  its own `tiingo_client` argument) can independently derive the
  authoritative date sequence to check the *given* `risk_free` against,
  regardless of how or where the caller built it. Before calling
  `compute_market_excess_return`, `run_capm_pilot` builds its own
  internal view, calls `derive_trading_dates` on it a second time, and
  asserts `set(risk_free.get_risk_free(start, end)["date"]) == set(that
  same sorted-unique date sequence)`, raising a named, fail-closed
  exception (defined in `capm_pilot.py`, not in any trusted module) on
  mismatch.
- Because both derivations call the identical pure function with
  identical arguments, they are structurally guaranteed to agree absent
  a real bug — the assertion exists to catch that bug loudly, as a named
  error, instead of letting a silent `NaN` appear from the unrelated
  left-join inside `_load_pit_panel` and being mistaken for a legitimate
  first-observation or staleness `NaN`.
- This resolves the mismatch without inventing a new interface: the ABC
  is unchanged, `ConstantRiskFreeProvider`'s existing constructor-
  supplied-dates pattern is the precedent, and the *only* new rule is
  "P5A-2 owns one exported derivation helper and one internal validation
  check; P5A-1 never derives its own dates."

## Staleness: frozen operational rule (resolves B3)

"Maximum staleness: 3 business days" is now a **deterministic Phase 5A
data-freshness convention**, not a certification of true Treasury-market
publication-calendar semantics:

```
staleness_business_days(source_date, equity_date) =
    len(TradingCalendar.from_weekdays_excluding_holidays(
        source_date, equity_date)) - 1
```

using the existing, already-generic, dependency-free
`smart_beta.pit.calendar.TradingCalendar.from_weekdays_excluding_holidays`
constructor (no `holidays` argument supplied — a plain Monday–Friday
weekday count). No new calendar subsystem is built. This explicitly does
**not** claim to equal the true count of Treasury-market closures (it
does not exclude Columbus Day, Veterans Day, or any other Treasury-only
holiday that falls on a weekday) — it is a deliberately conservative,
deterministic simplification adequate for a bounded pilot, and every task
and the final certification must state this scope limitation verbatim
when citing the staleness rule.

**Ordering, frozen:** the first row of any `[start, end]` call is
unconditionally `rf = NaN` (no preceding trading date exists to compute
`delta_calendar_days` from) — the staleness check is never evaluated for
that row. Every subsequent row is independently subject to the staleness
check above, comparing `DGS3MO_source(t)`'s actual publication date
against `t`.

**Required boundary tests (P5A-1):**
- exactly 3 business days stale -> accepted.
- 4 business days stale -> named exception.
- a queried date has no published DGS3MO observation at or before it at
  all (not merely a first-row case) -> the same named exception (an
  unbounded gap is trivially beyond the 3-business-day threshold).
- a future-dated DGS3MO observation is never eligible, at any staleness
  -> covered by the existing no-future-leakage test, restated here as a
  staleness-adjacent boundary case.

## Frozen inference semantics (do not redefine)

- Diagnostic regression: `MKT_t = alpha + epsilon_t`, i.e.
  `smart_beta.engines.inference.newey_west_ols(y=MKT, X=ones)` — the
  existing, unmodified function. No new statistics code anywhere in
  Phase 5A.
- Alpha = arithmetic sample mean of non-NaN daily `MKT`.
- Covariance: the existing HAC implementation, unchanged.
- Lag: `DEFAULT_SETTINGS.newey_west_lags = 6` — the same constant
  `smart_beta/pipelines/beta_portfolio.py` already uses. **This is a
  frozen Phase 5A diagnostic convention inherited from existing
  machinery, not a certification that lag 6 is the statistically
  optimal HAC bandwidth for daily observations** — every task and the
  final certification must carry this caveat forward verbatim.
- Minimum 20 non-NaN `MKT` observations before the NW statistic is
  reported; below that, report `NOT RUN — insufficient observations`,
  never a statistic computed from too few points.
- NaN `MKT` rows are dropped before inference, never coerced to zero.

## Gate A — frozen claim

**Purpose: REAL-DATA END-TO-END EXECUTION.**

Universe: **AAPL, MSFT, GOOGL** (fixed, small, chosen for tractability
and Tiingo entitlement accessibility — **not** because the window is
free of ordinary corporate actions). Ordinary cash dividends are
expected and are not a disqualifying condition: they are already fully
owned by the trusted adjusted-return path (`adj_ret`) and Phase 5A does
not re-derive or special-case them. The only exclusion criterion is a
**stock split or spin-off**, which changes share-count/identity in a way
worth avoiding for a first pilot's tractability, not because it would be
mishandled. Window: approximately 3 months; exact start/end dates are
frozen in P5A-2's own spec before any live recording happens (not
invented here, so the worker records real, deliberate dates rather than
whatever happens to be convenient at implementation time).

**Allowed claim:** the real-data chain executes end to end and produces
reproducible, inspectable `MKT` observations without silently
compensating for an upstream defect.

**Not demonstrated, verbatim (the single canonical sentence, reproduced
byte-for-byte in every task that states it):** *"Gate A does not
demonstrate representative market coverage, scalability, a US market
factor, CAPM replication, economic significance, or statistical
significance."* The output must never be labeled a representative
market factor anywhere — code comment, docstring, artifact header, or
report.

## Gate B — frozen claim

**Purpose: BOUNDED FIXED-UNIVERSE SCALABILITY.**

Scale target: ~30 names, ~12 months, reusing P5A-2's orchestration
module unmodified with different parameters.

**Universe rule (ex-ante reproducible, frozen procedure):**
1. Freeze a single, dated, publicly-verifiable DJIA constituent
   snapshot (the snapshot date itself is part of the recorded
   provenance).
2. Probe entitlement for each name using the exact Tiingo endpoint
   `total_mcap` depends on (`/tiingo/fundamentals/{ticker}/daily`,
   mirroring the same plan-tier check that already produced TWTR's real
   HTTP 400 in `tests/fixtures/tiingo/certification/manifest.json`).
3. Explicitly record every excluded/inaccessible name and why.
4. Freeze the surviving list **before** any return/factor computation
   begins.
5. Hold that list constant for the entire Gate B window, regardless of
   any real-world reconstitution, delisting, or entitlement change.

**Required terminology, everywhere (code, comments, artifacts, report):**
*"a fixed universe selected from a dated DJIA constituent snapshot."*
**Never:** "the DJIA," "a historical DJIA universe," "a representative
US market portfolio," or "a survivorship-safe index universe." No
historical index-membership infrastructure is built.

**Allowed claim:** BOUNDED FIXED-UNIVERSE SCALABILITY — the same Gate A
chain, unmodified, executes correctly at ~30 names/~12 months without
new universe-history infrastructure.

**Not demonstrated:** representative US market coverage, survivorship-
safe historical membership, publication-grade CAPM replication,
investment performance.

## Upstream-defect rule (binding on every task)

Phase 5A is a **consumer** of the trusted architecture: PIT temporal
selection, the Tiingo adapter, adjusted-return ownership, market-cap
semantics, corporate actions, the trading calendar, tradability policy,
identifiers, the existing CAPM computation, and the existing inference
engine are all **read-only dependencies**. If real execution exposes a
genuine defect in any of them, the affected gate **stops**; the defect is
recorded as a named barrier finding for a separate, independently
reviewed corrective task. No Phase 5A task may patch around, wrap, or
silently compensate for an upstream defect inside its own orchestration
code.

## Evidence taxonomy (preserved, never conflated)

`LIVE-RECORDED` / `FIXTURE-REPLAYED` / `CONTRACT-MODELED` / `CONSTRUCTED`
/ `HAND-VERIFIED` / `NOT CERTIFIED`. A passing offline test is not live
empirical execution. Gate A PASS is not market-factor certification.
Gate B PASS is not representative-US-market certification. A successful
risk-free transformation is not exact Treasury-holding-return
certification.

## Subpackage / artifact layout

```
smart_beta/research_inputs/
    risk_free_treasury.py        # TreasuryBillRiskFreeProvider           [P5A-1]
smart_beta/pipelines/
    capm_pilot.py                 # run_capm_pilot(...) orchestration       [P5A-2]
                                   # (reused, unmodified, by P5A-4)
scripts/
    fetch_phase5a_risk_free_fixtures.py (optional, one-time recorder,    [P5A-1]
                                        non-shipped/non-test, mirrors
                                        scripts/fetch_tiingo_fixtures.py)
    fetch_phase5a_gate_a_fixtures.py  (optional, one-time recorder,       [P5A-2]
                                        same precedent)
    fetch_phase5a_gate_b_fixtures.py  (optional, one-time recorder,       [P5A-4]
                                        same precedent)
tests/
    test_risk_free_treasury.py                                            [P5A-1]
    fixtures/risk_free/treasury/          (Gate A window)                 [P5A-1]
    fixtures/risk_free/treasury_gate_b/   (Gate B window, new dir)        [P5A-4]
    test_capm_pilot_gate_a.py                                             [P5A-2]
    fixtures/tiingo/phase5a_gate_a/                                       [P5A-2]
    test_phase5a_hand_verification.py     (no production code)           [P5A-3]
    test_capm_pilot_gate_b.py                                             [P5A-4]
    fixtures/tiingo/phase5a_gate_b/                                       [P5A-4]
    test_phase5a_certification.py                                        [P5A-5]
docs/
    phase5a/gate_a/                 (machine-readable outputs, diagnostics) [P5A-2]
    phase5a/gate_b/                 (universe snapshot + outputs)          [P5A-4]
    phase5a_capm_certification.md                                        [P5A-5]
```

## Task table

| Task | Objective | Files | Depends on |
|---|---|---|---|
| P5A-1 | Real, provenance-tracked Treasury risk-free provider | `research_inputs/risk_free_treasury.py`, tests, `tests/fixtures/risk_free/treasury/`, optional `scripts/fetch_phase5a_risk_free_fixtures.py` | none |
| P5A-2 | Gate A orchestration + live evidence + artifacts | `pipelines/capm_pilot.py`, tests, `tests/fixtures/tiingo/phase5a_gate_a/`, `docs/phase5a/gate_a/`, optional `scripts/fetch_phase5a_gate_a_fixtures.py` | P5A-1 |
| P5A-3 | Independent Gate A hand verification | test file only | P5A-2 |
| P5A-4 | Gate B fixed-universe scalability pilot | new fixtures + Gate B artifacts, optional `scripts/fetch_phase5a_gate_b_fixtures.py`, reuses P5A-2's module unmodified | P5A-1, P5A-2, P5A-3 |
| P5A-5 | Phase 5A certification/findings | `docs/phase5a_capm_certification.md`, its test | P5A-1..4 |

## Dependency DAG (sequential — each task's evidence gates the next)

```
P5A-1  ->  P5A-2  ->  P5A-3  ->  P5A-4  ->  P5A-5
```

No parallel waves: unlike Phase 4D-B's vendor-adapter build-out, each
Phase 5A task consumes the *previous task's own real evidence* (P5A-2
needs a real risk-free series to run at all; P5A-3 needs P5A-2's actual
recorded numbers to hand-verify; P5A-4 needs P5A-2's orchestration
proven correct by P5A-3 before scaling it up; P5A-5 needs all real
evidence to exist before certifying it). Running these in parallel would
mean reviewing speculative work against evidence that doesn't exist yet.

## Frozen policies (binding on every task)

1. **No new statistical methodology.** `newey_west_ols` is used exactly
   as it exists; no automatic HAC-bandwidth selection, no alternative
   estimator.
2. **No new resampling infrastructure.** Daily frequency only.
3. **No modification to any already-trusted module.** `capm.py`,
   `research_inputs/inputs.py`, `research_inputs/tradability.py`,
   `research_inputs/risk_free.py` (the ABC itself), `pit/*`,
   `pit/calendar.py`'s `TradingCalendar`, `data/align.py`'s `lag_panel`,
   `vendors/tiingo/*` are read-only dependencies for every P5A task.
   "Read-only" means importing and *calling* the existing public
   functions/classes exactly as they exist is authorized (e.g. P5A-2
   calling `get_tradability`/`get_realized_returns`/
   `get_capitalization_weights`/`lag_panel` a second time for diagnostic
   evidence, or P5A-1 calling `TradingCalendar.from_weekdays_excluding_
   holidays` for staleness) — it never means editing their source, and it
   never extends to any `_`-prefixed private symbol anywhere in these
   modules, which stays strictly off-limits regardless of purpose.
   `risk_free.py`'s existing `RiskFreeProvider` ABC,
   `SyntheticFixtureRiskFreeProvider`, and `ConstantRiskFreeProvider`
   are untouched; P5A-1 adds a new concrete class in a **new** module,
   never edits the existing one.
4. **No credentials committed.** The same discipline established across
   every prior phase: tokens/API keys read from the environment only,
   never logged, never written into a fixture or manifest.
5. **Every live-recorded fixture carries explicit provenance** (source,
   retrieval date, request parameters) mirroring the Tiingo/Tushare
   fixture-manifest pattern exactly.
6. **P5A-3 owns no production code** — it is a pure, independent
   arithmetic check against P5A-2's own committed raw fixtures, never a
   call into `compute_market_excess_return` itself (that would be
   circular, not independent verification).
7. **P5A-5 owns only certification/reporting files** — no production
   code, no new empirical claims beyond what P5A-1..4 already produced.

## Final Phase 5A barrier (binding on P5A-5, verified independently at review time)

PASS requires all of:
1. Real Tiingo observations, live-recorded with provenance.
2. Real DGS3MO observations, live-recorded with provenance.
3. Gate A executes end to end.
4. At least one Gate A `MKT` observation independently hand-verified
   (P5A-3), within a stated numerical tolerance.
5. Deterministic offline fixture replay (zero live network calls in
   `pytest`).
6. No PIT/data-ownership violation found anywhere in the new code.
7. No upstream defect silently compensated for — any found is a named
   barrier finding instead.
8. Gate B has an explicit disposition (pass with the bounded claim, or a
   named design barrier if the frozen universe rule proves unworkable).
9. If Gate B passes, its claim remains exactly "bounded fixed-universe
   scalability" — never upgraded in wording.
10. Machine-readable outputs exist for every passing gate.
11. Diagnostic evidence is sufficient to reconstruct every calculation
    step by hand.
12. Statistical summaries follow the frozen diagnostic convention
    (lag 6, ≥20 observations, explicit non-optimality caveat).
13. The findings/certification report preserves every evidence
    distinction (live-recorded vs. fixture-replayed vs. hand-verified vs.
    NOT CERTIFIED) without conflation.
14. Full existing regression suite passes (no Phase 0–4D-B test
    regresses).
15. No credentials or secrets are committed anywhere in the diff.
16. Every NOT CERTIFIED boundary (from this plan and from anything Gate
    A/B discovers) remains explicit in the final report.
17. `smart_beta/pipelines/capm_pilot.py`'s source contains no call into
    any `smart_beta/benchmarks/capm.py` private (`_`-prefixed) helper,
    `authoritative_MKT` is used unmodified from `compute_market_excess_
    return`, the `derived_market_return` invariant test passes, the
    RF-provider trading-date sequence is proven (not assumed) identical
    to the panel date grid `compute_market_excess_return` consumes, and
    the staleness business-day rule is the frozen, explicitly-scoped
    convention above — never silently upgraded into a claim of true
    Treasury-calendar certification.

## Deferred / out of scope (explicit, not silently dropped)

Tushare, China, CH3, CH4, FF5 repair, transaction costs, compounded NAV,
benchmark-relative alpha, autonomous factor discovery, every Phase 4D-B
follow-up candidate, production deployment, historical index-membership
infrastructure, broad adapter refactoring, new resampling infrastructure,
new statistical methodology.
