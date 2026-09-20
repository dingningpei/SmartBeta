# Task P5B-ST1 — Historical ST Integration Repair

Base master: `6ec8eea80152e0f8e0b63187ecd9f227307fcca6`
Branch: `phase5b/task-p5b-st1-historical-st-integration`
Discovered by: P5B-6 Stage 3 (`docs/phase5b/ch3_gate_a/stage3/STAGE3_REPORT.md`,
branch `phase5b/task-p5b-6-ch3-pilot`, commit `24b7d08564fc62f4cf649a10ad1408cc0add25f8`).

## 1. Purpose

Repair **only B1**: `TushareAShareSource.get_trading_status()`
(`smart_beta/vendors/tushare/source.py`) currently fails to supply the
already-supported historical `name_by_date` input to
`map_to_trading_status()` (`smart_beta/vendors/tushare/market_data.py`),
so `is_st` is structurally always `pd.NA`, causing
`ChinaAShareTradabilityPolicy` (`smart_beta/research_inputs/tradability.py`)
to fail closed for every row.

This task is an **integration repair only**. It is explicitly **NOT**:

- a CH3 methodology change;
- a tradability-policy relaxation;
- a B2 (fundamentals coverage) repair;
- a new empirical pilot;
- a live-data acquisition task.

## 2. Frozen semantics (do not deviate)

Preserve the already-certified historical ST interpretation exactly as it
already exists in the codebase:

```
bak_basic historical per-date name
    -> name_by_date
    -> map_to_trading_status(..., name_by_date=...)
    -> is_st_name(...)
    -> is_st
```

- Do not invent another ST heuristic.
- Do not use current/static `stock_basic.name` as PIT evidence.
- Do not change `ChinaAShareTradabilityPolicy`'s NA-handling
  (`flags_ok &= ~universe[col].fillna(True).astype(bool)` must remain
  fail-closed on unknown/NA `is_st`).
- Unknown historical ST state must continue to fail closed.

## 3. First — verify existing capability (before editing any production code)

Independently locate and confirm, citing file/line:

- the existing `bak_basic` fetch capability (client/transport level);
- its returned schema (fields, especially any per-date/`trade_date` field
  and the `name` field);
- existing normalization of that schema, if any;
- the existing `ST_NAME_CERTIFICATION_STATUS` constant in `market_data.py`;
- the existing `002450.SZ` historical specimen backing that certification
  (`*ST康得` on 2021-04-13 vs. `康得新` on 2018-04-13);
- `map_to_trading_status(name_by_date=...)`'s exact accepted shape and
  behavior (`smart_beta/vendors/tushare/market_data.py`);
- any existing tests already exercising `bak_basic` or
  `name_by_date` (e.g. `tests/test_tushare_market_data.py`, fixtures named
  `bak_basic_trade_date-*`).

Determine the **narrowest existing interface** `TushareAShareSource` should
consume — prefer reuse over duplication. Do not hard-code ST strings in
`source.py` if `is_st_name` already owns that semantic rule. Do not
duplicate mapping logic that already exists in `market_data.py`.

## 4. Implementation

Wire historical `bak_basic` name evidence into the assembled
`TushareAShareSource.get_trading_status()` path. Conceptual flow:

```
get_trading_status(start, end)
    +-- existing daily/suspend_d/stk_limit inputs (unchanged)
    +-- historical name evidence (new: bak_basic-sourced, per trade date)
    -> map_to_trading_status(..., name_by_date=<PIT historical names>)
```

Follow existing repository abstractions and conventions (fixture-backed
client calls, `_rows()` helper, per-`ts_code` loop) exactly as the other
inputs in this method already do.

## 5. PIT requirement

For each trading date `t`, the ST name used to classify `t` must be the
historical name applicable **at** `t`. Never use today's/current name to
classify an earlier date. No backfill from a later known name into an
earlier date. No look-ahead. If historical name evidence for `t` is
unavailable, `is_st` at `t` must remain unknown/fail-closed per the
existing contract — never silently defaulted to `False`.

## 6. Date alignment — test explicitly

- exact-date historical name resolution;
- a transition into ST;
- a transition out of ST;
- missing historical name for a given date;
- a normal (non-ST) name;
- a `*ST` name;
- an `ST` name;
- any other Chinese-name pattern already owned by `is_st_name` (do not
  invent new patterns; use only what `is_st_name` already recognizes).

Do not broaden the ST definition unless the existing certified semantics
already require it.

## 7. No live capture

Use only existing fixtures, existing certified specimen evidence
(`002450.SZ`), and deterministic synthetic fixtures constructed for this
task's own unit tests. **No Tushare live calls. No proxy calls. No
credential use. No P5B-6 Resume-4.** If implementation cannot be validated
without new live data, STOP and report the exact missing evidence
requirement rather than acquiring it.

## 8. Test the assembled source (not just the isolated mapper)

The key regression test must exercise the assembled
`TushareAShareSource.get_trading_status()`, not merely
`map_to_trading_status()` in isolation. Prove at minimum:

- historical non-ST date -> `is_st == False`;
- historical ST/`*ST` date -> `is_st == True`;
- missing historical evidence -> `is_st` remains unknown/fail-closed
  compatible (`pd.NA`, never `False`).

## 9. Downstream integration test

Add a narrow, deterministic test demonstrating:
`TushareAShareSource` -> `ChinaAShareTradabilityPolicy` — with valid
historical ST evidence present, the policy no longer rejects every stock
merely because `is_st` is structurally NA. Also prove: missing ST evidence
still fails closed. Do not require real P5B-6 data for this test —
construct minimal synthetic fixtures.

## 10. Phase 4D-B certification

Do not rewrite historical Phase 4D-B certification
(`docs/phase4d_b_tushare_certification.md`,
`tests/test_phase4d_b_certification.py`). Record in the completion report:

```
PHASE 4D-B HISTORICAL CERTIFICATION: UNCHANGED
PHASE 5B DISCOVERED INTEGRATION DEFECT: REPAIRED / NOT REPAIRED
```

The new tests extend downstream integration coverage; they do not
retroactively change what Phase 4D-B claimed or tested.

## 11. P5B-6 / B2 boundary — do not overclaim

After a B1 repair, do **not** claim P5B-6 Stage 3 is now S3-A. B2 remains
independently unresolved (4/84 capture identities missing, original
Policy C budget exhausted, no Resume-4 authorized). Therefore, regardless
of this task's outcome:

```
P5B-6 Stage 4:  NOT AUTHORIZED
Barrier 4a:     NOT PASSED
P5B-7:          BLOCKED
```

## 12. CH4 boundary

A successful B1 repair may establish `CH4 ST/TRADABILITY SOFTWARE PATH:
REPAIRED`. It does **not** establish `CH4 END-TO-END EMPIRICAL READINESS:
READY`, because no live pilot rerun is authorized by this task. Preserve
`CH4 TURNOVER DATA READINESS: READY` from Stage 3 unchanged.

## 13. Branch / ownership

This task owns: `smart_beta/vendors/tushare/source.py`,
`smart_beta/vendors/tushare/market_data.py` (only if reuse genuinely
requires a small addition — prefer zero changes here if the existing
`name_by_date` parameter already suffices), and new test files under
`tests/` for this integration. Do not touch any P5B-6 branch file, any
Phase 5A file, or any other sealed phase's certification file. This task
runs on its own dedicated branch (`phase5b/task-p5b-st1-historical-st-integration`),
separate from the terminal P5B-6 empirical-capture history, so the
integration repair's provenance stays clean and independently reviewable.

## 14. Verification (run all, report each)

- new ST integration tests (this task's own);
- relevant existing Tushare adapter tests
  (`tests/test_tushare_source.py`, `tests/test_tushare_market_data.py`);
- tradability-policy tests;
- P5B-3 CH3/CH4 methodology tests;
- Phase 4D-B certification tests (`tests/test_phase4d_b_certification.py`);
- Phase 5A certification/regression checks
  (`tests/test_phase5a_certification.py`);
- full offline test suite (`-m "not network"` or equivalent).

Verify Phase 5A production files remain byte-identical (no diff under
`smart_beta/` paths owned by Phase 5A). Verify zero network calls occurred
during the entire task.

## 15. Required completion report format

Return exactly this report, then stop:

```
PHASE 5B P5B-ST1 — HISTORICAL ST INTEGRATION REPAIR REPORT

BASE MASTER:
BRANCH:
ROOT CAUSE:
EXISTING CERTIFIED CAPABILITY REUSED:
PRODUCTION CHANGES:
PIT ALIGNMENT:
ASSEMBLED-SOURCE TEST: PASS / FAIL
DOWNSTREAM TRADABILITY TEST: PASS / FAIL
MISSING-ST FAIL-CLOSED TEST: PASS / FAIL
PHASE 4D-B HISTORICAL CERTIFICATION: UNCHANGED
B1: REPAIRED / BLOCKED
B2: UNCHANGED / BLOCKED
CH4 TURNOVER DATA READINESS: READY / unchanged
CH4 ST/TRADABILITY SOFTWARE PATH: REPAIRED / BLOCKED
P5B-6 STAGE 4: NOT AUTHORIZED
BARRIER 4a: NOT PASSED
P5B-7: BLOCKED
LIVE CALLS: 0
NEW TESTS:
FULL OFFLINE REGRESSION:
PHASE 5A BYTE IDENTITY:
EXACT WORKER COMMIT:
INDEPENDENT REVIEW:
MERGE AUTHORIZED: NO
NEXT LIVE CAPTURE AUTHORIZED: NO
NEXT WAVE AUTHORIZED: NO
```
