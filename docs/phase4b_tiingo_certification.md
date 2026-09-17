# Phase 4B Tiingo Certification (P4B-9)

Final Phase 4B gate. This document records what was **actually verified**
when Phase 3's already-merged, already-parameterized
`smart_beta.pit.compliance.check_*` functions were run against the real,
assembled `TiingoPITSource` over real recorded Tiingo specimens, and what
remains **NOT CERTIFIED**. It is the checked-in, human-readable half of the
executable certification in `tests/test_tiingo_certification.py`; every
disposition below corresponds to an assertion in that file.

Baseline: `master` after P4B-8, the Market Cap Bridge (P4B-M1/M2), and the
Determinism/Replay Barrier (P4B-D1/R1). Full suite: **698 pre-existing tests
+ the certification tests, all green.**

Three-valued outcome, never collapsed: a check may be **PASS**, **FAIL**,
**NOT CERTIFIED**, or **NOT RUN — reason**. A compliance check passing is a
*structural/mechanical* result and is never read here as a *semantic vendor
certification*. In particular, `schema_conformance_get_market_cap` passing
does **not** mean `FLOAT MARKET CAP` is certified; they answer different
questions.

---

## How the certification was run

`TiingoPITSource(["AAPL", "TWTR"], client=<TiingoClient over
replay_transport(recordings, param_recordings=...)>)`, built from live
Tiingo captures committed under `tests/fixtures/tiingo/certification/`
(the API key is never stored). P4B-R1's integrated `param_recordings`
mechanism disambiguates the `asReported=true` / normalized statements pair
that share one URL path; no local workaround is used.

**Plan-tier finding (affects the universe).** Tiingo's fundamentals
endpoints (`/statements` and `/daily`) return **HTTP 400** for any
non-DOW-30 ticker under the current plan tier. This was re-confirmed live
for TWTR and is recorded verbatim as `twtr_statements_asreported.json`,
`twtr_statements_normalized.json`, and `twtr_fundamentals_daily.json`:

> `Error: Free and Power plans are limited to the DOW 30. ...`

Consequently a two-ticker `get_fundamentals` / `get_market_cap` call raises
for *any* range. The structural (schema/determinism/no-shared-state) checks
are therefore run over the **AAPL-only** universe, where those endpoints are
supported; the TWTR survivorship check runs over the **two-ticker** universe
and records the plan-tier failure rather than hiding it.

---

## Check dispositions (completeness invariant)

Every check considered relevant in principle has an explicit disposition.
No considered check is silently absent.

| Check | Disposition | Evidence / reason |
| --- | --- | --- |
| `check_schema_conformance` | **PASS** | AAPL-only universe, 2020-01-01..2026-12-31: `trading_calendar` + all six panel methods conform. |
| `check_no_shared_mutable_state` | **PASS** | All six panel methods return independently-owned frames. |
| `check_deterministic_results` | **PASS** | All six panel methods canonical-column-identical across two calls at a real, non-empty range — the end-to-end proof of P4B-D1. |
| `check_delisted_security_history_present` | **FAIL (expected, mixed)** | TWTR: `get_raw_returns` PASS, `get_trading_status` PASS, `get_market_cap` FAIL (plan-tier 400), `delisted_listing_info` FAIL (`delist_date=NaT`). See below. |
| `check_exchange_calendar_recognizes_holiday` | **PASS** | 2021-01-01 is not a trading day. |
| `check_exchange_calendar_month_end` | **PASS** | 2019-03 real trading month-end is 2019-03-29 (naive 2019-03-31 is a Sunday). |
| `check_corporate_action_raw_facts` | **PASS** | AAPL 2020-08-31: `adjustment_factor=4.0`; raw return is the documented unadjusted −0.7415219437934419. |
| `check_survivorship_through_view` | **PASS** | TWTR survives `PointInTimeView.as_of(2022-10-28).adjusted_returns(...)`. |
| `check_build_panel_uses_exchange_calendar` | **PASS** | 2023-12: observes 2023-12-29 and excludes naive 2023-12-31. |
| `check_corporate_action_adjustment_correct` | **PASS** | AAPL split-day adjusted return equals the one externally documented true return 0.03391222482623224 (anti-tautology: not recomputed via `compute_adjusted_returns`). |
| `check_trading_status_flag_is_date_specific` | **PASS** | TWTR `is_zero_volume` True only on 2022-10-28, False on adjacent days. |
| `check_future_announcement_not_visible` | **PASS** | Real AAPL specimen: report period 2026-06-27 invisible at as-of 2026-07-01, visible at knowledge date 2026-07-31 (revenue 109,417,000,000). |
| `check_fundamentals_vintages_preserved` | **NOT CERTIFIED** | Deliberately never invoked against `TiingoPITSource`: no multi-vintage specimen exists under current access. |
| `check_restatement_not_backfilled` | **NOT CERTIFIED** | Deliberately never invoked. `is_restatement=False` is a schema-constrained placeholder, not a verified claim. |
| `check_float_and_total_market_cap_distinct` | **NOT RUN — no distinct vendor float-adjusted figure exists** | Tiingo exposes only one `marketCap` figure; there is no honest `expected_float ≠ expected_total` to assert. |
| `run_reference_compliance_suite` | **NOT RUN — meaningful only against `SyntheticPITSource`** | It consumes the synthetic reference fixture; never invoked here. |

### The P4B-D1 determinism proof

`check_deterministic_results` passes for all six panel methods at a
**non-empty** range. This is the concrete, end-to-end demonstration that
P4B-D1's canonical-column comparison works against the real,
provenance-bearing adapter (`_ingested_at` differs between calls but is not a
schema column, so it is correctly ignored; canonical values still match).

---

## Required certification lines

### RESTATEMENT/VINTAGE RECONSTRUCTION = NOT CERTIFIED

Tiingo's `asReported` endpoint returns at most one vintage per fact under the
current plan tier, so `is_restatement=False` is a schema-constrained
placeholder, not evidence of a non-restated fact. The real RGEN specimen
(`tests/fixtures/tiingo/client/rgen_fundamentals_asreported_error.json`)
is an **HTTP 400** plan-tier block, confirming that the multi-vintage
evidence needed to certify this does not exist under current access.
`check_fundamentals_vintages_preserved` and
`check_restatement_not_backfilled` are therefore never invoked against
`TiingoPITSource`.

### IDENTIFIER CONTINUITY = NOT CERTIFIED

Re-verified against the merged
`smart_beta/vendors/tiingo/identifiers.py` and the merged
`TiingoPITSource._resolve_stock_id`: `permaTicker` is real but lives on
`GET /tiingo/fundamentals/meta`, which `TiingoPITSource` never wires. The
source only calls `get_meta` (`GET /tiingo/daily/{ticker}`), so AAPL and
TWTR both resolve via the mutable-`ticker` fallback with
`is_permanent=False` and `source_field="ticker"`. This is asserted directly
in `test_identifier_continuity_is_ticker_fallback`.

### FLOAT MARKET CAP = NOT CERTIFIED (approximated: float_mcap == total_mcap, no distinct vendor float-adjusted figure observed)

`map_eod_to_market_cap` (P4B-M2) unconditionally sets
`float_mcap = total_mcap`; the machine-checkable marker
`FLOAT_MARKET_CAP_IS_APPROXIMATED` is `True`. There is no conditional case.
This is asserted in `test_float_market_cap_is_approximation`.
`schema_conformance_get_market_cap` passing is a structural result only and
must never be read as certifying this semantic line.

### TWTR DELISTING CORROBORATION = NOT CERTIFIED (real trailing zero-volume run = 1 day; frozen minimum = 5 days)

Real TWTR EOD has exactly **one** trailing zero-volume row (2022-10-28,
`volume == 0`), against P4B-7's frozen
`_MIN_CORROBORATING_ZERO_VOLUME_DAYS = 5`. Applying the frozen policy to real
evidence therefore yields `delist_date = NaT`, asserted directly via
`source.get_listing_info()` (`pd.isna(...) is True`). The threshold is
**not** lowered to manufacture a pass.

### FUNDAMENTALS RANGED-QUERY SEMANTICS = NOT CERTIFIED

Verified live (AAPL; the only test in this file that makes a live call):

* A range bounded to periods with coverage on both endpoints
  (`2026-03-01..2026-07-31`) **reconciles** — 2026 Q3 and Q2 appear on both
  the as-reported and normalized sides, and `map_asreported_to_fundamentals`
  returns both periods.
* A full calendar-year range (`2026-01-01..2026-12-31`) is **NOT
  reconcilable**: the as-reported response contains 2026 Q1, whose
  normalized period-end record does not exist, and the fail-closed mapper
  correctly raises `FiscalPeriodReconciliationError` for the whole batch.

The adapter's assumption — that passing one `[start, end]` to both statement
endpoints yields a mutually reconcilable fiscal-identity subset — is
therefore **not generally true**; whether it holds depends on whether the
range happens to include an as-reported period with no normalized
counterpart. `TiingoPITSource.get_fundamentals` passes the caller's range
through unverified, so this remains NOT CERTIFIED. The
fail-closed reconciliation is never weakened: the unreconcilable outcome is
the finding, not a defect to route around. Evidence fixtures:
`aapl_statements_*_2026-01-01_2026-12-31.json` (unreconcilable) and
`aapl_statements_*_2026-03-01_2026-07-31.json` (reconcilable).

### DIVIDEND ADJUSTMENT SEMANTICS — deliberately omitted

Re-verified against the merged `smart_beta/vendors/tiingo/corporate_actions.py`:
`DIVIDEND_SEMANTICS_CERTIFIED = True`. The real AAPL 2020-08-07 $0.82
specimen was independently corroborated against the Nasdaq Dividend History
API and Apple's SEC Form 8-K (accession `0000320193-20-000060`), so the
conditional dividend-semantics NOT CERTIFIED line is **correctly omitted**.
This task neither upgraded nor downgraded that finding. `test_dividend_semantics_line_is_correctly_omitted_or_present`
guards the omission.

---

## TWTR survivorship: exact breakdown

`check_delisted_security_history_present(stock_id="TWTR",
expected_last_date=2022-10-28, query 2022-10-20..2022-10-28)`:

| Sub-check | Result | Why |
| --- | --- | --- |
| `delisted_history_get_raw_returns` | **PASS** | TWTR raw-return rows genuinely persist through 2022-10-28. |
| `delisted_history_get_trading_status` | **PASS** | TWTR trading-status rows genuinely persist through 2022-10-28. |
| `delisted_history_get_market_cap` | **FAIL** | `GET /tiingo/fundamentals/TWTR/daily` returns **HTTP 400** (non-DOW-30 plan limit). A vendor-access limitation, not a dropped-data omission. |
| `delisted_listing_info` | **FAIL** | `get_listing_info()` reports `delist_date=NaT`, because the real trailing zero-volume run is length 1 against the frozen minimum of 5. |

**This `delisted_listing_info` FAIL is the expected, correct consequence of
the frozen corroboration policy applied to real evidence — it is not a code
defect and must not be read as one.** Likewise, the market-cap FAIL is a
plan-tier access limit, not an adapter defect. The spec's expectation that
the market-cap row-presence sub-check would PASS does not survive contact
with the real vendor plan tier and is reported here as the honest result.

---

## What remains deferred

Copied from `worker_tasks/phase4b/phase4b-plan.md`'s scope section, so this
document is a standalone record of Phase 4B's final state:

* **SEC/XBRL ingestion** — not built.
* **Any cross-vendor compositing/merge class** — not built.
* **Index membership / a production "all US equities" universe feed** — not built.
* **Phase 0-2 engine migration (Phase 4C)** — not built.
* **China A-share adapter (Phase 4D)** — not built.
