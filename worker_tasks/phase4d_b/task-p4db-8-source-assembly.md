# Phase 4D-B Worker Task — P4DB-8: `TushareAShareSource` Assembly (Wave 3)

## Background (read this first)

Read `worker_tasks/phase4d_b/phase4d-b-plan.md` in full. This is the
Wave 3 task — it depends on P4DB-4, P4DB-5, P4DB-6, and P4DB-7 all being
merged to `master` (Barrier 2 passed). You are the only task in Wave 3;
nothing runs in parallel with you.

**Your job:** wire the four Wave 2 modules plus P4DB-1's client and
P4DB-2's identifiers and P4DB-3's calendar into one concrete
`smart_beta.pit.source.PITDataSource` implementation,
`TushareAShareSource`. This mirrors exactly what Tiingo's P4B-8
(`smart_beta/vendors/tiingo/source.py`) did — **read that file in full**
before writing anything; your job is structurally the same assembly, a
different vendor underneath.

**Your working directory** will be a git worktree at
`/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4db-8-source-assembly`
on branch `phase4d_b/task-p4db-8-source-assembly`, branched from `master`
after Barrier 2.

## File ownership

**You may create exactly:**

- `smart_beta/vendors/tushare/source.py`
- `tests/test_tushare_source.py`
- `tests/fixtures/tushare/source/` (only if you need fixtures beyond
  what the four Wave 2 tasks' own fixture directories already provide —
  prefer reusing theirs by importing their fixture-loading helpers if
  such a pattern exists in Tiingo's `test_tiingo_source.py`; check before
  duplicating fixture files)

**You must not modify** any Wave 1/Wave 2 module (`client.py`,
`proxy_client.py`, `identifiers.py`, `calendar_source.py`,
`market_data.py`, `corporate_actions.py`, `fundamentals.py`,
`listing.py`), `smart_beta/pit/*`, `smart_beta/data/*`, or any existing
test file. If assembly reveals a genuine defect in a Wave 2 module (the
Phase 4B precedent: P4B-8's review found the market-cap and determinism
gaps that became P4B-M1/M2 and P4B-D1/R1 follow-ups), **do not fix it
yourself** — report it as a specific, named finding for the planner to
scope as a follow-up task, exactly as Phase 4B did.

## Required assembly

```python
class TushareAShareSource(PITDataSource):
    def __init__(self, client: TushareClient): ...
    def trading_calendar(self) -> TradingCalendar: ...
    def get_raw_returns(self, start, end) -> pd.DataFrame: ...
    def get_corporate_actions(self, start, end) -> pd.DataFrame: ...
    def get_market_cap(self, start, end) -> pd.DataFrame: ...
    def get_fundamentals(self, start, end, fields) -> pd.DataFrame: ...
    def get_trading_status(self, start, end) -> pd.DataFrame: ...
    def get_listing_info(self) -> pd.DataFrame: ...
```

Every method delegates to the corresponding Wave 2 module's mapping
function(s), resolving identifiers via P4DB-2's `resolve_stock_id` and
using P4DB-3's calendar for `trading_calendar()`. **Do not re-implement
any mapping logic here** — this file is pure wiring.

## Integration checks specific to this phase (verify, don't assume)

1. **Determinism** (mirrors the Phase 4B D1/R1 finding — check whether
   the same issue exists here before assuming it doesn't). Run Phase
   3's existing `pit.compliance.check_deterministic_results` against
   your assembled `TushareAShareSource`. If your fundamentals rows carry
   a `retrieved_at` provenance column (P4DB-6, policy 9) the same way
   Tiingo's carry `_ingested_at`, expect the same class of failure Phase
   4B found and already fixed via `check_deterministic_results`'
   canonical-columns-only comparison (P4B-D1, already merged and
   unmodified — you should not need to touch it, only confirm it still
   handles your new provenance columns correctly; if it does not, report
   this as a specific blocking finding, do not modify `compliance.py`
   yourself).
2. **The CH3 join-or-suppress behavior survives assembly.** Run the
   `600518.SH` and `002450.SZ` CH3-suppression tests from P4DB-6 against
   the fully assembled source (not just the unit-level `fundamentals.py`
   module) to confirm nothing in assembly accidentally re-introduces a
   guessed vintage join.
3. **Real end-to-end reconciliation.** Run the full `000001.SZ` FY2022
   cumulative-vs-single-quarter reconciliation (Gate 1 evidence) through
   the assembled source's `get_fundamentals`, confirming the exact-match
   arithmetic still holds after resolving through identifiers and the
   full pipeline.
4. **Schema conformance across all seven methods** —
   `check_schema_conformance` (Phase 3, unmodified) exercises every
   `PITDataSource` method; run it and report the result honestly, the
   same way Phase 4B's market-cap gap was found this way rather than
   assumed away.

## Fixture inputs

Reuse the fixture subdirectories already recorded by P4DB-1 through
P4DB-7 wherever possible; add new ones under `tests/fixtures/tushare/
source/` only for genuinely new integration-level scenarios not covered
by any single module's own tests.

## Required tests

- `check_schema_conformance` passes for all seven methods, or a
  documented, specific failure is reported (not silently worked around).
- `check_deterministic_results` result, with the provenance-column
  interaction explicitly checked per item 1 above.
- The CH3 suppression and Gate-1 reconciliation checks from items 2-3
  above, run at the assembled-source level.
- `resolve_stock_id` is wired correctly (assert which underlying call
  path `TushareAShareSource` actually uses — mirror the Phase 4B finding
  style where P4B-8's review confirmed exactly which method path was
  wired, not just that "an" identifier resolution happened).

## Non-goals

No new mapping logic. No fixes to Wave 2 modules — report findings
instead.

## Acceptance criteria

- `.venv/bin/pytest` green, full suite unaffected.
- Zero live network calls in `pytest`.
- Every integration check in "Integration checks specific to this
  phase" above has an explicit, reported result — pass, fail, or a named
  follow-up finding — never silently skipped.

## Commands to run

```bash
cd /Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4db-8-source-assembly
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
git diff --stat master...HEAD
```

## Expected commit scope

One commit (or a small number) on branch
`phase4d_b/task-p4db-8-source-assembly`, touching only the files listed
above.

## When done

Report: (a) the exact `TushareAShareSource` method-by-method wiring; (b)
the result of each integration check above, explicitly; (c) any Wave 2
defect found, named specifically, with a proposed follow-up task shape
(mirroring how P4B-M1/M2 and P4B-D1/R1 were scoped) rather than a fix;
(d) test results; (e) `git diff --stat`. Do not merge, do not touch
`master`, do not create follow-up worktrees yourself.
