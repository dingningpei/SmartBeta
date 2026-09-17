# Phase 4C Worker Task — P4C-6: Trusted Research Inputs (the central boundary)

## Background (read this first)

Read `worker_tasks/phase4c/phase4c-plan.md` in full first. This task
starts only after P4C-1, P4C-2, P4C-3, P4C-4, and P4C-5 are all merged
and Barrier 2 is green. Confirm your worktree's `master` merge-base
contains all five before starting.

**This is the single most important module in Phase 4C.** Everything
downstream (Waves 4a/4b) depends on it, and it is the one place that
turns "a `PointInTimeView` over some `PITDataSource`" into panels the
existing pure research engines can consume. Read this whole section
before writing a line of code.

**This is explicitly NOT a compatibility shim recreating the old
`DataSource` contract.** Do not design this module by asking "what would
`get_returns`/`get_market_cap`/etc. look like if they took a
`PointInTimeView` instead of a `DataSource`." Every function here makes
its own consequential choice visible in its name and its output column
name — there is no function whose job is merely "look like the old
`DataSource` method but backed by PIT data."

**Read the actual current files before writing anything:**
`smart_beta/pit/view.py` (`PointInTimeView`, `AsOfSnapshot`),
`smart_beta/pit/schema.py` (the PIT column names: `ADJUSTED_RETURN_COL`
= `adj_ret`, `RAW_RETURN_COL` = `raw_ret`, `TOTAL_MARKET_CAP_COL` =
`total_mcap`, `FLOAT_MARKET_CAP_COL` = `float_mcap`), and the merged
`research_inputs/{risk_free,tradability,fundamentals_coverage,
identifier_continuity}.py` from P4C-1..4.

**Your working directory for this task will be a git worktree** at
`/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4c-6-trusted-research-inputs`
on branch `phase4c/task-p4c-6-trusted-research-inputs`, branched from
`master` after Barrier 2. Run tests with `.venv/bin/pytest`.

## File ownership

**You may create exactly these files, and no others:**

- `smart_beta/research_inputs/inputs.py`
- `tests/test_research_inputs.py`

**Never create** `smart_beta/research_inputs/__init__.py` — already
exists (P4C-1's file, already merged by the time this task starts).

**You must not modify anything else**, including any other
`research_inputs/*.py` file, `smart_beta/pit/*`, `smart_beta/vendors/*`,
`smart_beta/data/*`, `smart_beta/pipelines/*`, `smart_beta/benchmarks/*`,
or `pyproject.toml`.

## What to build

```python
from __future__ import annotations

from datetime import date
from typing import Sequence

import pandas as pd

from smart_beta.pit.view import PointInTimeView
from smart_beta.pit.schema import (
    ADJUSTED_RETURN_COL, TOTAL_MARKET_CAP_COL, DATE_COL, STOCK_COL,
)
from smart_beta.research_inputs.tradability import TradabilityPolicy
from smart_beta.research_inputs.fundamentals_coverage import (
    FundamentalsRetrievalResult, retrieve_fundamentals,
)


def get_realized_returns(
    view: PointInTimeView, start: date | str, end: date | str,
) -> pd.DataFrame:
    """(date, stock_id, adj_ret) -- explicitly ADJUSTED returns, via
    view.as_of(...).adjusted_returns(...) or the equivalent PointInTimeView
    call (read pit/view.py for the exact real method to call over a
    range). This is the ONLY function in this package that produces a
    realized-return panel; every research engine consuming realized
    returns for actual return measurement (not for a lagged sort
    characteristic) goes through this, never raw_ret directly.

    Never renames adj_ret to `ret`. Callers that need a specific column
    name for a downstream engine parameter (e.g. portfolio_sort.sort_
    portfolios's ret_col) pass "adj_ret" as that parameter's value; this
    function does not rename its own output to accommodate them.
    """


def get_capitalization_weights(
    view: PointInTimeView, start: date | str, end: date | str,
) -> pd.DataFrame:
    """(date, stock_id, total_mcap) -- explicitly TOTAL market cap, never
    float_mcap. See phase4c-plan.md's frozen finding: no current engine
    needs a certified free-float figure; total_mcap is Tiingo's real
    observed marketCap, float_mcap is only ever a documented
    approximation of it. This function must not expose float_mcap at
    all -- not as an optional column, not behind a flag."""


def get_tradability(
    view: PointInTimeView,
    start: date | str,
    end: date | str,
    keys: pd.DataFrame,
    policy: TradabilityPolicy,
    settings: "Settings",
) -> pd.DataFrame:
    """(date, stock_id, is_tradable, ...). Fetches trading_status,
    listing_info, and market_cap from `view` and delegates the actual
    decision to `policy.evaluate(...)` -- this function's only job is
    fetching the PIT-shaped inputs the policy needs and calling it.
    `policy` has no default here (unlike build_tradable_universe's
    default-to-China convenience) -- the caller (a pipeline task) must
    choose explicitly which market's policy applies to its universe.
    """


def get_fundamentals(
    view_or_source, start, end, fields, *, allow_partial: bool = False,
) -> "FundamentalsRetrievalResult":
    """Thin pass-through to research_inputs.fundamentals_coverage.
    retrieve_fundamentals, over the source PointInTimeView wraps (or the
    source directly -- decide and document which based on
    fundamentals_coverage.py's real signature; report if this requires a
    PITDataSource rather than a PointInTimeView, since coverage
    orchestration operates on raw vendor calls, not the trusted "as of"
    resolution PointInTimeView adds -- read both files and resolve this
    precisely, do not guess)."""
```

Also expose, as thin, explicitly-named pass-throughs (not reimplementations):

- Whatever this module needs to let a caller obtain a
  `RiskFreeProvider`-backed rate series and an `IdentifierContinuityEvidence`
  check for its configured universe — decide whether these belong as
  functions in this module or whether callers should import
  `research_inputs.risk_free`/`research_inputs.identifier_continuity`
  directly (both are already public, frozen APIs from Wave 1). **Prefer
  direct import by callers over adding redundant re-export functions
  here** unless you find a concrete reason a pipeline task would need
  this module to compose them — if you add such a function, name it as
  precisely as the others above and document why it wasn't left as a
  direct import.

## Frozen semantics (binding, do not deviate)

- `get_realized_returns` returns `adj_ret`. Never `ret`, never `raw_ret`
  as the returned realized-return figure.
- `get_capitalization_weights` returns `total_mcap`. Never `float_mcap`.
- `get_tradability` requires an explicit `policy` argument — no default.
- Every function's docstring states, in its own words, *why* the choice
  it makes is the correct one (referencing the plan document's findings
  is fine — do not just assert it).
- This module must not import `smart_beta.vendors.tiingo` (or any other
  vendor package) anywhere.
- Construction of any object this module returns must not eagerly fetch
  more than the caller asked for — mirror `PointInTimeView`'s own
  "construction fetches nothing, a call fetches exactly what it needs"
  discipline.

## Required tests

1. `get_realized_returns` returns exactly `date, stock_id, adj_ret`
   (schema check) and its values match `PointInTimeView`'s own adjusted
   returns for the same range, called directly (anti-tautology: compare
   against the already-frozen Phase 3 primitive's real output, not a
   value this module re-derives independently).
2. `get_capitalization_weights` returns exactly `date, stock_id,
   total_mcap`; `float_mcap` never appears anywhere in its output under
   any circumstance, including when the underlying source's market-cap
   panel has both columns (assert this explicitly with a real fixture
   that has both).
3. `get_tradability` with `USZeroVolumeTradabilityPolicy` produces a
   sensible `is_tradable` mask end-to-end against a real, fixture-fed
   `TiingoPITSource`-backed `PointInTimeView` (reuse existing Phase 4B
   Tiingo fixtures, copied into your own fixture subdirectory per the
   established per-task fixture-ownership convention — do not import
   another task's fixture files directly).
4. `get_tradability` with `ChinaAShareTradabilityPolicy` against a
   synthetic-fixture-backed source still works (behavior parity with
   the pre-Phase-4C path for China).
5. `get_fundamentals` end to end: a real, non-empty range succeeds and
   returns resolved data; a range constructed to include a genuine
   coverage gap (reuse the real evidence already captured in
   `tests/fixtures/tiingo/certification/` if convenient, copied into
   your own subdirectory) raises `FundamentalsCoverageError` by default
   and returns a `FundamentalsRetrievalResult` under `allow_partial=True`.
6. No function in this module imports `smart_beta.vendors.tiingo` —
   confirm by reading your own import list (reviewer will verify).
7. Every function's real end-to-end path is exercised against BOTH a
   synthetic-fixture-backed source (behavior-preservation reference) and
   the real merged `TiingoPITSource` (fed by real fixtures) — for at
   least `get_realized_returns` and `get_capitalization_weights`.

## Fixture inputs

Copy (do not import) whatever real Tiingo fixture files you need from
`tests/fixtures/tiingo/{client,source,certification}/` into a new
`tests/fixtures/research_inputs/` subdirectory you own. Note in your
report exactly which files you copied and from where, so provenance
stays traceable.

## Non-goals

- Do not migrate any pipeline or benchmark file — that is Wave 4's job.
- Do not add a risk-free or identifier-continuity re-implementation —
  those are P4C-1/P4C-4's frozen deliverables; consume them.
- Do not weaken any P4C-1..5 contract to make this module simpler.
- Do not add a default `policy` to `get_tradability`.

## Acceptance criteria

- All pre-existing tests continue to pass unchanged.
- All required tests above pass.
- `git diff --stat` against your branch's merge-base with `master` shows
  changes to exactly `smart_beta/research_inputs/inputs.py`,
  `tests/test_research_inputs.py`, and files under
  `tests/fixtures/research_inputs/`.

## Commands to run

```bash
cd /Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4c-6-trusted-research-inputs
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
git diff --stat master...HEAD
```

## Expected commit scope

One commit (or a small number) on branch
`phase4c/task-p4c-6-trusted-research-inputs`, touching only the files
listed above.

## If you discover a contract contradiction

In particular: if `get_fundamentals`'s relationship between
`PointInTimeView` and the coverage orchestration (which needs a
`PITDataSource`, not a view) turns out to be awkward or contradictory
once you read both real files, **stop and report the exact contradiction**
rather than silently redesigning either P4C-3's or `PointInTimeView`'s
contract.

## When done

Report: (a) the exact final signatures of every function in this module;
(b) how you resolved the `PointInTimeView`-vs-`PITDataSource` question
for `get_fundamentals`; (c) confirmation of the `adj_ret`/`total_mcap`-only
guarantees, with the specific tests that prove them; (d) confirmation of
zero `smart_beta.vendors.*` imports; (e) test results; (f)
`git diff --stat`. Do not merge, do not touch `master`, do not modify
files outside the list above.
