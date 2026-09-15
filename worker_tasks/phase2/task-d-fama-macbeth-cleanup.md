# Phase 2 Worker Task — P2-D: Fama-MacBeth Cleanup (Wave 2)

## Background (read this first)

`smart_beta` is a China A-share factor-investing research package. Phase 1
built six independent modules in parallel git worktrees, each reviewed and
merged. Phase 2 Wave 1 then landed three foundational pieces, all now on
`master`:

- `smart_beta.data.align.lag_panel` — the package's single canonical
  per-stock lag utility (generalizes what used to be a local
  `lag_characteristics` function inside `smart_beta/engines/fama_macbeth.py`).
- `smart_beta.engines.inference.newey_west_ols` — the package's single
  canonical Newey-West (HAC) OLS wrapper.
- Four new `Settings` fields, including `fama_macbeth_min_obs: int = 3`
  (replacing a local module constant that used to live in
  `smart_beta/engines/fama_macbeth.py`).

**Your job is to make `smart_beta/engines/fama_macbeth.py` use all three of
these instead of its own local, duplicate implementations — without
changing `fama_macbeth()`'s public behavior at all.** This is a pure
internal refactor. The acceptance bar is that every existing
`fama_macbeth` test still passes with **unchanged numerical tolerances**.

**Your working directory for this task is:**

```
/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-d-fama-macbeth-cleanup
```

`cd` there before running any command. It is a git worktree on branch
`phase2/task-d-fama-macbeth-cleanup`, branched from `master` after Wave 1
merged. A venv already exists at `.venv` with the project installed
(`pip install -e ".[dev]"`) and `.venv/bin/pytest` passing (133/133) before
you start. If anything looks stale, re-run
`.venv/bin/pip install -e ".[dev]"` from that directory. Run tests with
`.venv/bin/pytest`.

This is Phase 2, Wave 2 — one of two tasks (P2-D, P2-E2) starting in
parallel now that Wave 1 (P2-A, P2-B, P2-E1) is merged. The other, P2-E2,
refactors `smart_beta/benchmarks/*.py` to reuse
`smart_beta.engines.portfolio_sort`'s newly-public `assign_groups`/
`group_return_stats` and `data.align.lag_panel`. You do not depend on
P2-E2 and it does not depend on you; you touch completely disjoint files.

## File ownership

**You may modify exactly these two files, and no others:**

- `smart_beta/engines/fama_macbeth.py`
- `tests/test_fama_macbeth.py`

**You must not modify anything else**, including but not limited to:
`smart_beta/data/align.py`, `smart_beta/engines/inference.py`,
`smart_beta/engines/portfolio_sort.py`, `smart_beta/benchmarks/*`,
`smart_beta/config/settings.py`, `smart_beta/data/schema.py`,
`smart_beta/factors/*`.

## The three APIs you will consume (final, as merged — read the actual
files in your worktree before writing code, this is a summary for orientation)

### `smart_beta.data.align.lag_panel`

```python
def lag_panel(
    panel: pd.DataFrame,
    columns: Sequence[str],
    periods: int = 1,
    date_col: str = DATE_COL,   # smart_beta.data.schema.DATE_COL == "date"
    stock_col: str = STOCK_COL, # smart_beta.data.schema.STOCK_COL == "stock_id"
) -> pd.DataFrame
```

Per-stock `groupby(stock_col).shift(periods)`; returns a new, sorted,
reset-index frame; never mutates its input. This is functionally identical
to your module's current `lag_characteristics` (same algorithm, same
`sort_values([stock_col, date_col], kind="mergesort")` + `groupby(...).shift(...)`
pattern) — it was generalized from it. Read `smart_beta/data/align.py`'s
module docstring; it explicitly documents the alignment convention your
module's own docstring currently paraphrases.

### `smart_beta.engines.inference.newey_west_ols`

```python
def newey_west_ols(
    y: ArrayLike,
    X: ArrayLike,
    lags: int = DEFAULT_SETTINGS.newey_west_lags,
) -> RegressionResultsWrapper
```

Thin wrapper: reshapes a 1-D `X` to a column, raises `ValueError` if
`lags < 0`, and returns `sm.OLS(y, X).fit(cov_type="HAC", cov_kwds={"maxlags": int(lags)})`.
Note it does **not** special-case `n == 0` or `n == 1` — your module's
existing `_newey_west_mean` does, and you must keep that guard (see below).

### `Settings.fama_macbeth_min_obs`

Already on `master` with default `3`, replacing what is currently your
module's local `_MIN_OBS_PER_PERIOD = 3` constant.

## What to do

Your module currently has, at module scope:

```python
_MIN_OBS_PER_PERIOD = 3
```

used as `min_required = max(_MIN_OBS_PER_PERIOD, len(feature_names) + 1)`
inside `fama_macbeth()`. Replace this with `settings.fama_macbeth_min_obs`
(the `settings` parameter already flows into `fama_macbeth()`; use it
instead of the module constant). Delete `_MIN_OBS_PER_PERIOD`.

Your module currently has:

```python
def lag_characteristics(
    panel: pd.DataFrame,
    characteristic_cols: Sequence[str],
    periods: int = 1,
    date_col: str = "date",
    stock_col: str = "stock_id",
) -> pd.DataFrame:
    ...  # identical algorithm to lag_panel
```

Delete this function entirely from `smart_beta/engines/fama_macbeth.py`
and remove it from the module's `__all__`. It is not imported anywhere
outside this module's own test file (verified: `grep -rn
"lag_characteristics" --include="*.py" .` from the repo root only matches
`smart_beta/engines/fama_macbeth.py` and `tests/test_fama_macbeth.py`), so
this is a safe removal within your owned files. Update
`tests/test_fama_macbeth.py`'s import from
`from smart_beta.engines.fama_macbeth import (..., lag_characteristics, ...)`
to `from smart_beta.data.align import lag_panel`, and update every call
site (`lag_characteristics(panel, fields, periods=1, date_col=DATE_COL,
stock_col=STOCK_COL)` becomes `lag_panel(panel, fields, periods=1,
date_col=DATE_COL, stock_col=STOCK_COL)` — same argument order and
defaults, so this should be a mechanical rename at call sites). The test
file currently also has a dedicated
`test_lag_characteristics_shifts_within_stock_and_preserves_input` test
that duplicates coverage now owned by `tests/test_align.py`
(P2-B's test file, not yours) — remove this test rather than renaming it
in place, since testing `data.align.lag_panel`'s own contract belongs in
`tests/test_align.py`, not here. Update the module docstring's
"Alignment note" paragraph to reference `smart_beta.data.align.lag_panel`
instead of the now-deleted local `lag_characteristics`.

Your module currently has:

```python
def _newey_west_mean(
    series: pd.Series, lags: int
) -> tuple[float, float, float, float]:
    values = pd.to_numeric(series, errors="coerce").dropna().to_numpy(dtype=float)
    n = values.size
    if n == 0:
        return (np.nan, np.nan, np.nan, np.nan)
    if n == 1:
        return (float(values[0]), np.nan, np.nan, np.nan)

    maxlags = int(max(0, min(int(lags), n - 1)))
    design = np.ones((n, 1))
    fit = sm.OLS(values, design).fit(
        cov_type="HAC", cov_kwds={"maxlags": maxlags}
    )
    return (
        float(fit.params[0]),
        float(fit.bse[0]),
        float(fit.tvalues[0]),
        float(fit.pvalues[0]),
    )
```

**Keep the `n == 0` and `n == 1` early-return guards exactly as they are**
— `newey_west_ols` does not special-case tiny samples and calling it with
`n <= 1` may error or misbehave, so those guards must run first, before
you ever call `newey_west_ols`. For `n >= 2`, replace the direct
`sm.OLS(values, design).fit(...)` call with a call to
`smart_beta.engines.inference.newey_west_ols(values, design, lags=maxlags)`
(you can still pass a `(n, 1)` all-ones column, or a 1-D `np.ones(n)` array
since `newey_west_ols` reshapes 1-D `X` automatically — either is fine, but
prefer the 1-D form since it's what `newey_west_ols`'s own docstring shows
as the simple case) and extract `fit.params[0]`, `fit.bse[0]`,
`fit.tvalues[0]`, `fit.pvalues[0]` exactly as before. You may keep the
function name `_newey_west_mean` (it's a small private orchestration
wrapper around the shared primitive now, which is a legitimate thing to
keep) or remove it and call `newey_west_ols` directly at each of the two
call sites in `fama_macbeth()` — your choice, as long as the `n==0`/`n==1`
guards are preserved and the numerical result for `n >= 2` is unchanged.

## Non-goals

- Do not touch `smart_beta/data/align.py` or
  `smart_beta/engines/inference.py` — you consume them, you don't modify
  them.
- Do not change `fama_macbeth()`'s public signature, `FamaMacBethResult`'s
  shape, or `winsorize_and_standardize`'s behavior.
- Do not add new features (e.g. don't try to make `fama_macbeth_min_obs`
  independently configurable beyond what `settings` already provides).

## Required tests / acceptance criteria

- Every pre-existing `tests/test_fama_macbeth.py` test passes with
  **unchanged numerical tolerances** — in particular
  `test_recovers_true_signal_coefficient` and
  `test_tstats_match_manual_newey_west_hac`, which are sensitive to the
  exact Newey-West computation and are the strongest evidence the
  refactor is behavior-preserving.
- Add a test that overriding `Settings(fama_macbeth_min_obs=<some other value>)`
  actually changes which periods get skipped (proves the field is really
  wired to `Settings`, not just renamed-and-hardcoded).
- `git grep -n "lag_characteristics\|_MIN_OBS_PER_PERIOD"
  smart_beta/engines/fama_macbeth.py` returns nothing — proves the
  duplication is actually gone, not just supplemented.
- `git diff --stat` against your branch's merge-base with `master` shows
  changes to exactly `smart_beta/engines/fama_macbeth.py` and
  `tests/test_fama_macbeth.py`.

## When done

Run the full test suite (`.venv/bin/pytest`) from your worktree, commit
your changes on branch `phase2/task-d-fama-macbeth-cleanup`, and report
back:

(a) confirmation `lag_characteristics` and `_MIN_OBS_PER_PERIOD` are gone
and `lag_panel`/`settings.fama_macbeth_min_obs` are used instead;
(b) how you refactored `_newey_west_mean` (kept as a thin wrapper vs.
inlined at call sites) and confirmation the `n==0`/`n==1` guards are
intact;
(c) test results;
(d) the `git diff --stat` output.

Do not merge your branch, do not touch `master`, and do not modify files
outside the two listed above.
