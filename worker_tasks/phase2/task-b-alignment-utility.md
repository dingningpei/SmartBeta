# Phase 2 Worker Task — P2-B: Canonical Alignment Contract + Shared Lag Utility

## Background (read this first)

`smart_beta` is a China A-share factor-investing research package. Phase 0
built a schema-driven, long-format pandas foundation
(`smart_beta/data/schema.py`, `smart_beta/data/sources/base.py`, a
synthetic fixture generator, `smart_beta/config/settings.py`). Phase 1 then
built six independent modules in parallel git worktrees (universe
construction, beta estimation, a portfolio-sort engine, a Fama-MacBeth
engine, statistical inference utilities, and benchmark factor
construction), each reviewed independently and merged into `master`. Phase
1 finished at commit `37f64c9` with all six tasks merged and 115/115 tests
passing.

Two things emerged from the Phase 1 integration review that this task
resolves:

1. **Duplicated per-stock lagging logic.** `smart_beta/engines/fama_macbeth.py`
   implements its own `lag_characteristics()` (a per-stock `groupby().shift()`
   helper used to align a characteristic observed at date *t* with the
   return at date *t+1*). `smart_beta/benchmarks/capm.py` and
   `smart_beta/benchmarks/ch4.py` independently implement the same kind of
   shift inline (`_load_panel`'s `panel.groupby(STOCK_COL)[col].shift(1)`,
   `_add_extra_lag`, `ch4._add_turnover_proxy`). Three independent
   implementations of the same operation.
2. **An unenforced alignment convention.** `smart_beta/factors/beta.py`'s
   `rolling_ols_beta`/`dimson_beta` deliberately compute "beta as of date
   *t*" **inclusive of the return at *t*** (a defensible, standard
   convention — Frazzini-Pedersen define rolling beta the same way).
   That means a beta panel must be lagged one period before being paired
   with the same date's return in a portfolio sort or a cross-sectional
   regression — otherwise the return being explained partly determined the
   very beta estimate explaining it, a mechanical/contemporaneous bias.
   Nothing currently forces this; it is only a documented convention in
   `beta.py`'s docstring.

**Your job is to build the single, canonical, reusable lag utility that
resolves both** — generalizing `fama_macbeth.py`'s existing
`lag_characteristics()` rather than inventing something new.

This is Phase 2, Wave 1 — one of three tasks (P2-A, P2-B, P2-E1) starting
in parallel from the same `master` baseline. The other two are:
reconciling deferred `Settings`/`schema.py` additions, and exposing
reusable primitives from the portfolio-sort engine. **Assume neither of
their deliverables exists yet in your worktree**, and do not import from
files owned by another task. All three Wave 1 tasks touch completely
disjoint files, so there is no need to coordinate or wait on them.

**You are building the utility only in this task — you are not wiring it
into `fama_macbeth.py` or `benchmarks/*.py` yet.** That consumption work
is explicitly a separate, later task (P2-D and P2-E2, Wave 2, which do not
start until this task and P2-A are both merged). Building the utility now
without touching its future consumers is what keeps this task's file
ownership disjoint from every other Wave 1 task.

**Your working directory for this task is:**

```
/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-b-alignment-utility
```

`cd` there before running any command. It is a git worktree on branch
`phase2/task-b-alignment-utility`, branched from `master`. A venv already
exists at `.venv` with the project installed (`pip install -e ".[dev]"`)
and `.venv/bin/pytest` passing (115/115) before you start. If anything
looks stale, re-run `.venv/bin/pip install -e ".[dev]"` from that
directory. Run tests with `.venv/bin/pytest`.

## File ownership

**You may create exactly these two files, and no others:**

- `smart_beta/data/align.py` (new)
- `tests/test_align.py` (new)

**You must not modify anything else**, including but not limited to:
`smart_beta/engines/fama_macbeth.py` (do not touch it, even though it has
a function you are generalizing — read it for reference only),
`smart_beta/benchmarks/*.py`, `smart_beta/config/settings.py`,
`smart_beta/data/schema.py`, `smart_beta/factors/*`,
`smart_beta/engines/portfolio_sort.py`, `smart_beta/engines/inference.py`.

## What to do

### Reference: the function you are generalizing

`smart_beta/engines/fama_macbeth.py` currently has (read it, but do not
edit it):

```python
def lag_characteristics(
    panel: pd.DataFrame,
    characteristic_cols: Sequence[str],
    periods: int = 1,
    date_col: str = "date",
    stock_col: str = "stock_id",
) -> pd.DataFrame:
    """Shift characteristics forward within each stock. ..."""
    characteristic_cols = list(characteristic_cols)
    missing = [
        c
        for c in [*characteristic_cols, date_col, stock_col]
        if c not in panel.columns
    ]
    if missing:
        raise ValueError(f"panel is missing required columns: {missing}")

    out = panel.copy()
    out = out.sort_values([stock_col, date_col], kind="mergesort")
    out[characteristic_cols] = out.groupby(stock_col, sort=False)[
        characteristic_cols
    ].shift(periods)
    return out.reset_index(drop=True)
```

### Deliverable: `smart_beta/data/align.py`

Create a module with (at minimum) this public function, matching the
behavior above but generalized so the name doesn't imply "characteristics
only" (it must work identically on a factor panel whose value column is
named `value`, e.g. the output of `smart_beta.factors.beta.rolling_ols_beta`):

```python
def lag_panel(
    panel: pd.DataFrame,
    columns: Sequence[str],
    periods: int = 1,
    date_col: str = DATE_COL,
    stock_col: str = STOCK_COL,
) -> pd.DataFrame:
    """Shift one or more columns forward within each stock.

    Returns a copy of ``panel`` (sorted by ``[stock_col, date_col]``) in
    which each column in ``columns`` has been shifted down by ``periods``
    rows within its stock. With ``periods=1`` the row at date *t* therefore
    carries the value observed at *t - periods*, alongside whatever else is
    already recorded for *t* (e.g. the realized return) -- the alignment
    needed for a predictive regression or a portfolio sort where the
    characteristic must be observed strictly before the return it explains.

    Implemented as a per-stock groupby shift rather than positional numpy
    slicing, so stocks with different listing dates cannot be misaligned by
    a global row offset. The input frame is not modified.
    """
```

Use `DATE_COL`/`STOCK_COL` from `smart_beta.data.schema` as the parameter
defaults (they are the literal strings `"date"`/`"stock_id"`, so this is
compatible with `fama_macbeth.py`'s existing string-literal defaults).

**In the module docstring** (at the top of `align.py`), write out the
alignment convention explicitly, referencing `smart_beta.factors.beta`'s
beta estimators by name: state plainly that `rolling_ols_beta`/
`dimson_beta` are inclusive of the current date and MUST be passed through
`lag_panel` before being paired with a same-date return anywhere
downstream (portfolio sorts, Fama-MacBeth regressions, benchmark spanning
tests). This module-level documentation is the canonical place a future
integrator (Phase 2's pipeline task) will look to get this right.

### Required tests: `tests/test_align.py`

Port `fama_macbeth.py`'s existing `lag_characteristics` test coverage
(read `tests/test_fama_macbeth.py`'s
`test_lag_characteristics_shifts_within_stock_and_preserves_input` for the
existing pattern) plus:

- shift correctness across multiple stocks with **different listing
  histories** (e.g. one stock's data starts later than another's — prove
  the shift never leaks a value across the stock boundary);
- no input mutation (assert the original panel is unchanged after the
  call, e.g. via a deep-copy comparison);
- multiple columns shifted at once in one call;
- `periods > 1` shifts correctly;
- **a new test using the real synthetic beta pipeline**: call
  `smart_beta.factors.beta.rolling_ols_beta(...)` on
  `synthetic_source.get_returns(...)` (the same pattern
  `tests/test_beta.py` uses), pass the result through `lag_panel`, and
  assert that the lagged value at date *t* for a given stock equals the
  *unlagged* value at date *t - 1* for that same stock. This is a direct
  regression test for the exact cross-module alignment hazard this task
  exists to prevent — do not skip it.

## Non-goals

- Do not modify `fama_macbeth.py` to use your new `lag_panel` — that is
  P2-D's job in Wave 2, after this task is merged.
- Do not modify `benchmarks/*.py` to use your new `lag_panel` — that is
  P2-E2's job in Wave 2.
- Do not create or modify anything under `smart_beta/config/`,
  `smart_beta/engines/portfolio_sort.py`, or `smart_beta/data/schema.py` —
  other Wave 1 tasks own or depend on those staying stable.

## Required tests / acceptance criteria

- All 115 pre-existing tests continue to pass unchanged (you have not
  touched any file they depend on).
- `tests/test_align.py` passes, including the beta-alignment regression
  test described above.
- `git diff --stat` against your branch's merge-base with `master` shows
  changes to exactly `smart_beta/data/align.py` and `tests/test_align.py`
  (both new files) — no existing file touched at all.

## When done

Run the full test suite (`.venv/bin/pytest`) from your worktree, commit
your changes on branch `phase2/task-b-alignment-utility`, and report back:

(a) the exact `lag_panel` signature you settled on;
(b) confirmation the module docstring states the beta-lagging convention
explicitly;
(c) test results (should be 115 pre-existing + your new `test_align.py`
tests, all passing);
(d) the `git diff --stat` output, so file-ownership compliance can be
verified at a glance.

Do not merge your branch, do not touch `master`, and do not modify files
outside the two listed above.
