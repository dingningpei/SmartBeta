# Phase 4C Worker Task — P4C-10: Migrate benchmarks/{capm,ff3,ff5}.py

## Background (read this first)

Read `worker_tasks/phase4c/phase4c-plan.md` in full first. This task
starts only after P4C-6 is merged (this task does **not** depend on
P4C-7 — `benchmarks/*.py` fetches its own panels directly and does not
call `pipelines._common`; confirm this is still true by reading the real
current files before starting, and stop and report if you find it
isn't). Full-suite-green from P4C-7's merge is not required for this
task's *start*, but do check `master` for whatever has actually landed
by the time you begin.

This is Wave 4b — one of three parallel tasks (P4C-8, P4C-9, P4C-10).
Assume P4C-8's and P4C-9's changes do not exist in your worktree; do not
touch `pipelines/*`.

**Why one task owns three files.** `benchmarks/ff3.py` and
`benchmarks/ff5.py` import private helpers directly from
`benchmarks/capm.py` (`_load_panel`, `_LAG_COL`, `_market_factor`, etc.)
— they are not independently migratable. Changing `capm.py`'s
`_load_panel` signature necessarily changes what `ff3.py`/`ff5.py` can
call. Read all three files, plus `benchmarks/ch3.py`/`ch4.py` (which you
will **not** modify but must not break), before writing anything.

**CH3/CH4 stay exactly as they are.** `benchmarks/ch3.py` and
`benchmarks/ch4.py` continue importing `capm.py`'s private helpers **as
they exist today** — you must not change their calling contract. Since
you are changing `_load_panel`'s signature for CAPM/FF3/FF5, and CH3/CH4
also import from `capm.py`, you have two real options: (a) keep the old,
`DataSource`-based private helpers in `capm.py` under their current
names, unused by the new public `compute_market_excess_return`, but
still present and correct for CH3/CH4 to import; or (b) something
equivalent that achieves the same outcome. Pick the one that produces
the least duplication while guaranteeing CH3/CH4's tests need zero
changes. State which you chose and why in your report.

## File ownership

**You may modify exactly these:**

- `smart_beta/benchmarks/capm.py` (amend)
- `smart_beta/benchmarks/ff3.py` (amend)
- `smart_beta/benchmarks/ff5.py` (amend)
- `tests/test_benchmarks_capm_ff.py` (new — the migrated CAPM/FF3/FF5
  tests, moved and fixed from the current `tests/test_benchmarks.py`)
- `tests/test_benchmarks_ch.py` (new — the CH3/CH4 tests, moved
  **unmodified** from `tests/test_benchmarks.py`, still exercising them
  against `SyntheticDataSource` exactly as today)
- `tests/test_benchmarks.py` — **delete this file** once its contents
  are fully moved into the two files above.
- `tests/fixtures/phase4c/benchmarks/` — any fixture files you need
  (this exact path, reserved for this task only)

**You must not modify anything else**, including
`smart_beta/benchmarks/ch3.py`, `smart_beta/benchmarks/ch4.py` (their
own code, not just their tests, stays byte-for-byte unchanged),
`smart_beta/pipelines/*`, `smart_beta/research_inputs/*`,
`smart_beta/data/sources/{base,synthetic}.py` (CH3/CH4's tests need
these unchanged), or `pyproject.toml`.

## The API you consume (already merged — read the actual files)

```python
# smart_beta.pit.view
class PointInTimeView: ...

# smart_beta.research_inputs.inputs (P4C-6, frozen)
def get_realized_returns(view, start, end) -> pd.DataFrame: ...       # adj_ret
def get_capitalization_weights(view, start, end) -> pd.DataFrame: ... # total_mcap
def get_tradability(view, start, end, keys, policy, settings) -> pd.DataFrame: ...
def get_fundamentals(...) -> FundamentalsRetrievalResult: ...

# smart_beta.research_inputs.risk_free (P4C-1, frozen)
class RiskFreeProvider(abc.ABC): ...
```

## What to build

Amend `compute_market_excess_return` and the shared private `_load_panel`
(and whatever else genuinely needs to change) to accept a
`PointInTimeView`, a `TradabilityPolicy`, and a `RiskFreeProvider`,
instead of a `DataSource`:

```python
def compute_market_excess_return(
    view: "PointInTimeView",
    start: date | str,
    end: date | str,
    *,
    policy: "TradabilityPolicy",
    risk_free: "RiskFreeProvider",
    settings: Settings = DEFAULT_SETTINGS,
) -> pd.DataFrame:
```

`compute_ff3_factors`/`compute_ff5_factors` mirror the same parameter
shape (read their real current signatures and adapt each consistently).
`_load_panel`'s new version must use `adj_ret`/`total_mcap` from
`research_inputs`, and fundamentals fields (for FF3's book-to-market,
FF5's profitability/investment proxies) via `get_fundamentals`'s
coverage-aware retrieval, defaulting to strict completeness (same
non-negotiable default as P4C-9 — do not make benchmark construction an
exception to strict-by-default).

**Frozen invariants** (identical in spirit to P4C-8's, applied here):
lag discipline for market cap and any lagged characteristic stays
exactly where it is today, in this file's own `_load_panel`/lag helpers
— it does not move into `research_inputs`. Realized returns are
`adj_ret`. Weighting is `total_mcap`. Risk-free is the injected provider.

## Required tests

1. **CH3/CH4 tests untouched and green.** `tests/test_benchmarks_ch.py`
   is the exact content of the CH3/CH4-relevant tests from the old
   `test_benchmarks.py`, calling `compute_ch3_factors`/`compute_ch4_factors`
   exactly as today, against `SyntheticDataSource`, and passes without
   modification to any assertion.
2. **CAPM/FF3/FF5 migrated tests pass.** The moved-and-fixed content of
   `tests/test_benchmarks_capm_ff.py`, adapted to the new signatures.
3. **`adj_ret`/`total_mcap` regressions**, mirroring P4C-8's required
   tests 4 and 5, applied to `compute_market_excess_return` (and at
   least one of FF3/FF5).
4. **Fundamentals coverage strictness for FF3/FF5**, mirroring P4C-9's
   required test 2/3 — a genuinely unreconcilable fundamentals range
   raises by default; an explicit opt-in surfaces coverage evidence.
5. **Real `TiingoPITSource` end-to-end proof** for at least
   `compute_market_excess_return`, scoped to what the plan tier
   genuinely supports.
6. **China behavior-preservation** for all three migrated functions.
7. No `DataSource` import remains in `capm.py`, `ff3.py`, or `ff5.py`.
   (`ch3.py`/`ch4.py` still import it — that's correct and expected,
   confirm you have not touched those two files at all via `git diff
   --stat`.)

## Non-goals

- Do not migrate, touch, or "improve" `ch3.py`/`ch4.py` in any way.
- Do not fabricate a US-equivalent turnover/suspension factor for CH4.
- Do not default `policy` or `risk_free`.

## Acceptance criteria

- `tests/test_benchmarks_ch.py` and `tests/test_benchmarks_capm_ff.py`
  both pass in full.
- All required tests above pass.
- `git diff --stat` against your branch's merge-base with `master` shows
  changes to exactly `smart_beta/benchmarks/{capm,ff3,ff5}.py`,
  `tests/test_benchmarks_capm_ff.py` (new), `tests/test_benchmarks_ch.py`
  (new), files under `tests/fixtures/phase4c/benchmarks/`, and the
  deletion of `tests/test_benchmarks.py` — `ch3.py` and `ch4.py` show
  zero diff.

## Commands to run

```bash
cd /Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4c-10-benchmark-migration
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest tests/test_benchmarks_capm_ff.py tests/test_benchmarks_ch.py -v
git diff --stat master...HEAD
```

Full-suite-green is not required at this task's own gate (P4C-8/P4C-9
run in parallel and may not be merged into your worktree yet). Barrier
4b is the real gate.

## Expected commit scope

One commit (or a small number) on branch
`phase4c/task-p4c-10-benchmark-migration`, touching only the files
listed above.

## If you discover a contract contradiction

In particular: if `benchmarks/*.py` turns out to actually depend on
`pipelines._common` (contradicting this spec's Background claim), stop
and report it rather than silently adding a dependency on P4C-7.

## When done

Report: (a) the exact new signatures for all three public functions; (b)
how you resolved the CH3/CH4 shared-private-helper compatibility question
(option a/b from Background, or your own); (c) confirmation CH3/CH4's
code and tests are byte-for-byte unchanged; (d) confirmation of the
`adj_ret`/`total_mcap` and fundamentals-strictness regressions; (e) the
real `TiingoPITSource` end-to-end result; (f) test results; (g)
`git diff --stat`. Do not merge, do not touch `master`, do not modify
files outside the list above.
