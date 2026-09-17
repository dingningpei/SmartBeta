# Phase 4C Worker Task — P4C-1: RiskFreeProvider Contract

## Background (read this first)

`smart_beta` is migrating its Phase 0-2 research engines onto the trusted
Phase 3 PIT boundary (`PITDataSource`/`PointInTimeView`) without coupling
any engine to a specific vendor. Read `worker_tasks/phase4c/phase4c-plan.md`
in full before starting — it is the frozen architecture record for all
eleven Phase 4C tasks.

**Why this task exists.** Every current risk-free-rate consumer
(`pipelines/beta_portfolio.py`, and `benchmarks/capm.py`'s shared
`_load_panel`, reused unchanged by `ff3.py`/`ff5.py`) does the identical
thing: fetch `(date, rf)`, join by `date` alone (never `stock_id`), and
subtract it from a return series to get an excess return. This confirms
risk-free is genuinely market-level data, not a per-security PIT concern
— it correctly does **not** belong on `PITDataSource` (which has exactly
seven abstract methods, all either per-security or, for
`trading_calendar`, market-structural; none is risk-free). This task
freezes a separate, minimal contract for it.

**What this task explicitly does NOT do:** choose a real production US
risk-free data source. That remains an open, unresolved architecture/data
decision, deliberately deferred — do not research or select a real
vendor/API for US treasury/T-bill rates. You are building the *contract*
and *reference/deterministic implementations* only.

**Your working directory for this task will be a git worktree** at
`/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4c-1-risk-free`
on branch `phase4c/task-p4c-1-risk-free`, branched from `master` at
`634c31c`. Run tests with `.venv/bin/pytest`.

This is Phase 4C, Wave 1 — one of four parallel tasks (P4C-1, P4C-2,
P4C-3, P4C-4), each owning completely disjoint new files. Assume none of
the other three exist in your worktree; do not import from them.

## File ownership

**You may create exactly these files, and no others:**

- `smart_beta/research_inputs/__init__.py` (empty marker — this new
  package will be extended by three sibling Wave-1 tasks and later waves;
  do not add exports here)
- `smart_beta/research_inputs/risk_free.py`
- `tests/test_research_inputs_risk_free.py`

**You must not modify anything else.** In particular:
- Do not touch `smart_beta/pit/source.py` (`PITDataSource`) — risk-free
  is deliberately not a method on it; do not add one.
- Do not create `research_inputs/tradability.py`,
  `research_inputs/fundamentals_coverage.py`,
  `research_inputs/identifier_continuity.py`, or `research_inputs/inputs.py`
  — those are other tasks, in other waves.
- Do not touch `smart_beta/pipelines/*`, `smart_beta/benchmarks/*`,
  `smart_beta/data/*`, `smart_beta/vendors/*`, `smart_beta/factors/*`,
  `smart_beta/engines/*`, or `pyproject.toml`.
- Do not select or wire a real US risk-free data source.

## What to build

```python
from __future__ import annotations

import abc
from datetime import date

import pandas as pd


class RiskFreeProvider(abc.ABC):
    """Market-level risk-free (or policy) rate provider.

    Deliberately NOT part of PITDataSource: a risk-free rate is a
    market-wide series, joined by date alone, never by stock_id -- every
    current consumer in the codebase confirms this (read
    pipelines/beta_portfolio.py and benchmarks/capm.py's _load_panel
    before writing anything). This ABC is the entire generic contract
    Phase 4C needs; do not add methods beyond what those real call sites
    require.
    """

    @abc.abstractmethod
    def get_risk_free(self, start: date | str, end: date | str) -> pd.DataFrame:
        """Risk-free rate for [start, end].

        Returns a long-format DataFrame with exactly two columns: `date`
        (datetime-like) and `rf` (float), one row per period the provider
        has a rate for. Units must match the return series this will be
        subtracted from (e.g. a monthly simple rate for a monthly return
        panel) -- this is the caller's responsibility to arrange, not this
        contract's to enforce, since the provider has no way to know what
        return series it will be paired with.

        Temporal semantics: NOT bitemporal. A risk-free/policy rate is
        treated as same-day knowable (effective time == knowledge time),
        mirroring Phase 3's own bitemporal decision table precedent for
        same-day market data (smart_beta/pit/schema.py) -- this is an
        existing precedent being reused, not a new rule invented here.
        Never mutates any input; implementations that wrap another
        object's data must return a fresh, independently-owned frame.
        """
```

Provide exactly two concrete implementations:

1. `SyntheticFixtureRiskFreeProvider` — wraps the existing synthetic
   fixture's own `rf` data (read `smart_beta/data/sources/synthetic.py`'s
   `get_risk_free` method for the exact real behavior to reproduce) so
   the China/synthetic path is byte-identical to what it is today. This
   is the reference implementation later behavior-preservation tests
   (P4C-6, P4C-8, P4C-10) will compare against.
2. `ConstantRiskFreeProvider` — a trivial, deterministic implementation
   returning a fixed rate for every date in `[start, end]` (using the
   real trading-day dates the caller-supplied calendar or date range
   implies — read the constructor signature you design carefully so this
   is genuinely useful for tests, not a toy). This exists purely so
   later tasks' tests have a simple, real, non-mocked `RiskFreeProvider`
   to construct without needing the full synthetic fixture — it is
   **not** a placeholder for a real US rate; document this explicitly in
   its own docstring so no later task mistakes it for one.

## Required tests

1. Both implementations satisfy `isinstance(..., RiskFreeProvider)`.
2. `SyntheticFixtureRiskFreeProvider` produces output identical (values
   and dates) to calling the existing `SyntheticDataSource.get_risk_free`
   directly for the same range — a genuine behavior-preservation
   assertion, not a tautology (compare against the real existing method's
   real output, captured independently in the test, not re-derived from
   the new class's own logic).
3. `ConstantRiskFreeProvider` returns the configured rate for every
   requested date; a schema/shape test confirms exactly the two columns
   `date`, `rf`.
4. Neither implementation mutates any input it wraps.
5. A test asserting the contract's temporal-semantics documentation
   claim is testable in principle: the same `(start, end)` called twice
   produces identical output (determinism) — this doesn't prove the
   "same-day knowable" semantic claim by itself (that's a documentation
   commitment, not a runtime-checkable property), but determinism is the
   one part of it that is checkable, so check it.

## Non-goals

- Do not choose, research, or wire a real US risk-free vendor/API.
- Do not add a risk-free method to `PITDataSource` or any Phase 3 file.
- Do not build a general macro-data framework — exactly this one ABC and
  two implementations.
- Do not import anything from `smart_beta.vendors.*`.

## Acceptance criteria

- All pre-existing tests (as of `634c31c`) continue to pass unchanged.
- All required tests above pass.
- `git diff --stat` against your branch's merge-base with `master` shows
  changes to exactly `smart_beta/research_inputs/__init__.py`,
  `smart_beta/research_inputs/risk_free.py`, and
  `tests/test_research_inputs_risk_free.py`.

## Commands to run

```bash
cd /Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4c-1-risk-free
.venv/bin/pip install -e ".[dev]"   # create the venv first if it does not exist
.venv/bin/pytest
git diff --stat master...HEAD
```

## Expected commit scope

One commit (or a small number) on branch `phase4c/task-p4c-1-risk-free`,
touching only the files listed above.

## If you discover a contract contradiction

If anything about the real current risk-free call sites (read the actual
files, not this spec's summary) contradicts what's described here, stop
and report the discrepancy in your final report rather than improvising a
different contract on your own.

## When done

Report: (a) the exact `RiskFreeProvider` ABC and both implementations'
signatures; (b) confirmation the synthetic-fixture implementation's
output matches the real existing `SyntheticDataSource.get_risk_free`
exactly, with the values you compared; (c) test results; (d)
`git diff --stat`. Do not merge, do not touch `master`, do not modify
files outside the list above.
