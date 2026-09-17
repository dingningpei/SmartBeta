# Phase 4C Engine Migration Certification (P4C-11)

Final Phase 4C gate. This document records what was **actually verified**
when the Phase 0-2 research engines were migrated onto the trusted
`smart_beta.pit.source.PITDataSource` / `PointInTimeView` boundary, and what
remains **NOT CERTIFIED** from Phase 4B. It is the checked-in,
human-readable half of the executable certification in
`tests/test_phase4c_certification.py`; every disposition below corresponds to
a real assertion in that file. No line here claims a capability that file
does not execute.

Baseline: `master` at the P4C-8/P4C-9/P4C-10 merges (merge-base
`ec4c972`), with `barrier 4b` full-suite-green confirmed. This task edits
**no production code** — it only exercises it. Every claim below was
re-derived by calling the real merged functions directly, not by importing
and re-running P4C-8/P4C-9/P4C-10's own test files.

Executable authority: `tests/test_phase4c_certification.py` (27 tests, all
passing). Run with `.venv/bin/pytest
tests/test_phase4c_certification.py`.

Three-valued outcome, never collapsed: a check may be **PASS**, **FAIL**,
**NOT CERTIFIED**, or **NOT RUN — reason**. A compliance/structural check
passing is never read here as a semantic certification; they answer
different questions.

---

## Dispositions (completeness invariant: all 13 considered, none silent)

**[1] No migrated pipeline/benchmark imports the legacy `DataSource`** — **PASS**
`tests/test_phase4c_certification.py::test_01_no_migrated_file_imports_legacy_datasource`
AST-inspects `pipelines/{_common,beta_portfolio,fama_macbeth_premium}.py`
and `benchmarks/{capm,ff3,ff5}.py`; none imports
`smart_beta.data.sources.base`, any `smart_beta.data.sources.*` module, or
the name `DataSource`.

**[2] `ch3.py`/`ch4.py` still import it (genuinely left untouched)** — **PASS**
`test_02_ch3_ch4_still_import_legacy_datasource` confirms both files still
have `from smart_beta.data.sources.base import DataSource`. The inverse of
[1] holds, so CH3/CH4 were not accidentally migrated or accidentally broken.
`git diff --stat master...HEAD` for this task shows zero changes under
`smart_beta/benchmarks/ch3.py`/`ch4.py`.

**[3] No vendor import anywhere under pipelines/benchmarks/engines/factors/research_inputs** — **PASS**
`test_03_no_vendor_import_in_research_layers` AST-scans every `.py` under
those five packages (≥ 10 files) for any `smart_beta.vendors` import; none
exists. Vendor coupling remains confined to `smart_beta/vendors/tiingo/`
and the `PITDataSource` boundary.

**[4] Frozen Phase-2 lag invariants reproduced with hand-computable values** — **PASS**
* `test_04a_beta_t_minus_1_to_return_t_hand_computed` builds a tiny
  `PITDataSource` with equal market caps, so the rolling betas are exact by
  hand: window `{A,B}` gives `beta_A=0, beta_B=2`; window `{B,C}` gives
  `beta_A=2, beta_B=0`. At `C` the pipeline sorts on the **lagged** beta
  (`A` low, `B` high) and the value-weighted long-short is exactly
  `ret_B - ret_A = 2 - 4 = -2`. A contemporaneous sort would reverse the
  groups and give `+2`; the test asserts both.
* `test_04b_total_mcap_t_minus_1_to_weighting_return_t_hand_computed`
  arranges market caps `100/900` at `D0` and returns `0.10/0.20` at `D1`
  with `n_groups=1`; the value-weighted group return at `D1` is exactly
  `(0.10*100 + 0.20*900)/1000 = 0.19` (contemporaneous weights would give
  `0.11`). Both are asserted.

**[5] Realized-return consumers use `adj_ret` (real AAPL 2020-08-31 split)** — **PASS**
The real, fixture-fed `TiingoPITSource` over the recorded AAPL EOD
(`aapl_eod_prices_2020-08-20_2020-09-05.json`, `splitFactor=4.0`) is run end
to end through one migrated pipeline and one migrated benchmark:
* `test_05a_pipeline_realized_returns_are_adj_ret_on_real_split` —
  `build_beta_sorted_portfolios` reports `+0.03391222482623224` on the split
  date (the certified true return), not the raw `-0.7415219437934419`.
* `test_05b_benchmark_realized_returns_are_adj_ret_on_real_split` —
  `compute_market_excess_return` reports the same adjusted figure.
Both hardcode the true/raw returns from the Phase 4B certification record
(anti-tautology: never re-derived via `compute_adjusted_returns`).

**[6] No migrated file's executed code path ever reads `float_mcap`** — **PASS**
`test_06_no_migrated_executed_path_reads_float_mcap` proves this three ways:
(a) no migrated file imports or AST-references the float column;
(b) `get_capitalization_weights` returns only `date, stock_id, total_mcap`;
(c) a *tripwire* `PITDataSource` whose market-cap frame raises on any
`float_mcap` column read is fed through the beta-sort pipeline, CAPM, FF3 and
FF5 with no exception, and a synthetic float/total trap confirms the
effective lagged weight equals `total_mcap` (6.0e8 float vs 1.0e9 total).
`ch3/ch4`'s retained private helpers are not on this path.

**[7] Incomplete fundamentals cannot silently shrink the sample by default** — **PASS**
`test_07_incomplete_fundamentals_fail_closed_by_default` calls the real
merged functions directly against the real, genuinely unreconcilable AAPL
`2026-01-01..2026-12-31` statements (fiscal 2026 Q1 has no normalized
match). With `allow_partial_fundamentals` omitted (default `False`),
`build_fama_macbeth_premium`, `compute_ff3_factors`, and `compute_ff5_factors`
all raise `FundamentalsCoverageError`; the report carries exactly the four
decomposed unresolved intervals and every failure reason names
`FiscalPeriodReconciliationError`. The signature defaults are asserted to be
`False` in the same test.

**[8] Tradability policy explicit with no default; no suspension-equivalence claim** — **PASS**
`test_08a_tradability_policy_and_risk_free_have_no_default` inspects the
real signatures of the five public migrated functions plus
`build_universe_and_tradable_returns`/`get_tradability`: `policy` has no
default anywhere, and `risk_free` has no default on the migrated public
surface. `test_08b_us_zero_volume_policy_ignores_suspension_flags` shows a
frame with `is_suspended=True` is *ignored* by
`USZeroVolumeTradabilityPolicy` (only `is_zero_volume` is read; a
zero-volume-free, suspension-flagged row is tradable), that a missing
`is_zero_volume` column is a hard error, and that the class docstring
disclaims equivalence. There is no fabricated exchange-suspension signal.

**[9] Risk-free is a separately injected `RiskFreeProvider`, never a `PITDataSource` method** — **PASS**
`test_09_risk_free_is_separately_injected` asserts `PITDataSource` has no
`get_risk_free` attribute, that every `.get_risk_free(...)` call on the
migrated path is made on the caller-injected `risk_free` object, and
behaviorally that `MKT == gross_market_return - injected_rf` for a nonzero
constant rate. **Documented exception:** `benchmarks/capm.py` retains its
legacy `_load_panel` helper (the documented CH3/CH4 compatibility surface
from P4C-10), which duck-types `source.get_risk_free`; it is not called by
the migrated `_load_pit_panel` or any migrated public function, and the test
pins that it is the only such legacy call.

**[10] Identifier continuity remains machine-visible and `certified=False` for real Tiingo identifiers** — **PASS**
`test_10_identifier_continuity_not_certified_for_real_tiingo` drives the
real operational resolution path: `TiingoPITSource` calls `get_meta` and
`resolve_stock_id`, which for both AAPL and TWTR falls back to the mutable
ticker (`is_permanent=False`, `source_field="ticker"`; the daily meta
carries no `permaTicker`). `check_identifier_continuity` raises under the
default `fail` policy and, under both `warn` and `allow`, returns
`IdentifierContinuityEvidence(certified=False, non_permanent_stock_ids=("AAPL",
"TWTR"), proceeded_under_override=True)`. The migration did **not** upgrade
this; the underlying finding remains NOT CERTIFIED (see [13]).

**[11] Behavior-preservation numeric regressions pass** — **PASS**
Each migrated function was called directly and compared for **exact**
equality (`assert_frame_equal`/`assert_series_equal`) against an independent
pre-Phase-4C reconstruction built from the unchanged legacy
`DataSource`/`build_tradable_universe`/`_load_panel` primitives over the same
`SyntheticDataSource(seed=42)` scenario. The earlier tasks' own test files
were not imported or re-run.
* `test_11a_beta_portfolio_behavior_preservation` — universe, beta,
  beta_lagged, sorted_returns, long_short all byte-identical (max abs diff
  0.0).
* `test_11b_fama_macbeth_behavior_preservation` — coefficients, std_errors,
  mean_coefficients identical; the recovered signal coefficient is
  `0.02 ± 0.01`.
* `test_11c_capm_behavior_preservation`,
  `test_11d_ff3_behavior_preservation`,
  `test_11e_ff5_behavior_preservation` — all factor frames identical (max
  abs diff 0.0) for CAPM, FF3 and FF5.
The fixture has no corporate actions, so `adj_ret == ret`; the comparison
therefore also documents that the raw-vs-adjusted change is a no-op there.

**[12] Real `TiingoPITSource` end-to-end paths, scoped to what is supported** — **PASS (with expected Phase-4B limitations reported, not smoothed)**
`test_12a_real_tiingo_pipeline_and_benchmark_end_to_end` runs
`build_beta_sorted_portfolios` and `compute_market_excess_return` against the
real AAPL 2020 split specimen; the split-date factor is the certified true
return, and the pipeline emits a schema-conformant result.
`test_12b_real_tiingo_fama_macbeth_supported_scope` runs
`build_fama_macbeth_premium` over the real reconcilable
`2026-03-01..2026-07-31` evidence with **complete coverage**, and confirms
that the wide `2026-01-01..2026-12-31` range — and the same range through
FF3/FF5 — fails closed with `FundamentalsCoverageError` (the certified
Phase-4B ranged-query limitation, not a Phase 4C defect).
`test_12c_real_tiingo_twtr_plan_tier_is_expected_failure` confirms TWTR's
`get_fundamentals`/`get_market_cap` raise `TiingoAPIError` HTTP 400 with the
real `"DOW 30"` plan-tier body, while TWTR's supported raw history persists
through its real last EOD date `2022-10-28`. These are the **expected,
correct** outcomes and are reported as such, never as a false PASS.

**[13] Five Phase-4B NOT CERTIFIED findings remain unchanged** — **PASS (all five unchanged)**
Each finding was independently re-verified against the real merged code at
this migration's merge-base; none changed. See the five verbatim lines and
evidence in the next section.

---

## The five Phase-4B NOT CERTIFIED lines (restated, all confirmed UNCHANGED)

`RESTATEMENT/VINTAGE RECONSTRUCTION = NOT CERTIFIED` — **unchanged.**
Real evidence under the current plan tier is the RGEN HTTP 400 plan-tier
block (`rgen_fundamentals_asreported_error.json`); the only real mapped
facts carry exactly one vintage each, so `is_restatement=False` remains a
schema-constrained placeholder, not a verified non-restatement.
`check_fundamentals_vintages_preserved` / `check_restatement_not_backfilled`
are still never invoked against `TiingoPITSource`. Backed by
`test_13a_restatement_vintage_reconstruction_still_not_certified`.

`IDENTIFIER CONTINUITY = NOT CERTIFIED` — **unchanged.**
`resolve_stock_id` resolves AAPL and TWTR via the mutable-ticker fallback
(`is_permanent=False`, `source_field="ticker"`); `permaTicker` lives on an
endpoint `TiingoPITSource` never wires. Phase 4C's structured guard still
surfaces this as machine-visible `certified=False` evidence under every
policy mode. Backed by `test_10_...` and
`test_13b_identifier_continuity_still_not_certified`.

`FLOAT MARKET CAP = NOT CERTIFIED` (approximated: `float_mcap ==
total_mcap`, no distinct vendor float-adjusted figure observed) — **unchanged.**
`FLOAT_MARKET_CAP_IS_APPROXIMATED is True`; the real adapter's
`get_market_cap` sets `float_mcap == total_mcap` unconditionally. No
migrated path can consume the float approximation. Backed by
`test_13c_float_market_cap_still_not_certified` and [6].

`TWTR DELISTING CORROBORATION = NOT CERTIFIED` (real trailing zero-volume
run = 1 day; frozen minimum = 5 days) — **unchanged.**
The real TWTR EOD has exactly one trailing zero-volume row (`2022-10-28`),
against the frozen `_MIN_CORROBORATING_ZERO_VOLUME_DAYS = 5`;
`source.get_listing_info()` reports `delist_date = NaT`. The threshold was
not lowered. Backed by `test_13d_twtr_delisting_corroboration_still_not_certified`.

`FUNDAMENTALS RANGED-QUERY SEMANTICS = NOT CERTIFIED` — **unchanged.**
The real bounded `2026-03-01..2026-07-31` pair reconciles (two periods),
while the real full-year `2026-01-01..2026-12-31` pair is not reconcilable
(fiscal 2026 Q1 as-reported has no normalized match) and the fail-closed
mapper raises `FiscalPeriodReconciliationError` for the whole batch. The
adapter still passes the caller's range through unverified. Backed by
`test_13e_fundamentals_ranged_query_semantics_still_not_certified` and [7]/[12].

**No genuinely new evidence was surfaced that would change any of the five
findings.** The migration neither upgraded nor downgraded them.

---

## What remains deferred

* **CH3/CH4 PIT migration.** `benchmarks/ch3.py`/`ch4.py` remain on the
  legacy `DataSource`; their tests are untouched and still pass. Liu-
  Stambaugh-Yuan is China-A-share-specific and has no honest US equivalent
  to fabricate.
* **Phase 4D China adapter.** A real China `PITDataSource` and CH3/CH4's PIT
  migration are Phase 4D's job.
* **A real production `RiskFreeProvider` data source.** `ConstantRiskFreeProvider`
  and `SyntheticFixtureRiskFreeProvider` are deterministic/test references
  only; no production US rate source was selected.
* **`FactorSpec` / Phase 5.** The schema/registry work that would let factor
  definitions be declared and composed is not built here.

---

## Completeness statement

No check considered relevant in principle is silently absent. Every one of
the 13 required checks above has an explicit disposition and a named
executable test; the five Phase-4B NOT CERTIFIED lines are restated
verbatim and confirmed unchanged; the deferred-scope list above makes this a
standalone record of Phase 4C's final state. The report-synchronization test
`test_certification_report_synchronized_with_dispositions` is a guard only:
it asserts every anchored disposition line and every restated line is
present, alongside — never instead of — the real executable checks above.
