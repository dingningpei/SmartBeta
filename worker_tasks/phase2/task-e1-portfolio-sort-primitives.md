# Phase 2 Worker Task — P2-E1: Expose Reusable Primitives from the Portfolio-Sort Engine

## Background (read this first)

`smart_beta` is a China A-share factor-investing research package. Phase 0
built a schema-driven, long-format pandas foundation. Phase 1 then built
six independent modules in parallel git worktrees (universe construction,
beta estimation, a portfolio-sort engine, a Fama-MacBeth engine,
statistical inference utilities, and benchmark factor construction), each
reviewed independently and merged into `master`. Phase 1 finished at
commit `37f64c9` with all six tasks merged and 115/115 tests passing.

The Phase 1 integration review found that `smart_beta/benchmarks/capm.py`
independently reimplements logic that now exists properly in
`smart_beta/engines/portfolio_sort.py`: rank-based bucket assignment
(`portfolio_sort.py`'s private `_assign_groups` vs. `capm.py`'s own
private `_assign_groups`) and within-group value-weighted return
computation (`portfolio_sort.py`'s private `_group_return_stats` vs.
`capm.py`'s own `_value_weighted_returns`/`_value_weighted_by`). Both were
written independently in parallel Phase 1 worktrees and are functionally
equivalent but textually duplicated.

**Your job is only to make `portfolio_sort.py`'s existing private helpers
public and independently testable, so a later task can point
`benchmarks/*.py` at them instead of its private duplicates.** You are
**not** touching `benchmarks/*.py` yourself — that is a separate, later
task (P2-E2, Wave 2, which does not start until this task is merged).

This is Phase 2, Wave 1 — one of three tasks (P2-A, P2-B, P2-E1) starting
in parallel from the same `master` baseline. The other two are:
reconciling deferred `Settings`/`schema.py` additions, and building a
shared per-stock lag utility. **Assume neither of their deliverables
exists yet in your worktree**, and do not import from files owned by
another task. All three Wave 1 tasks touch completely disjoint files, so
there is no need to coordinate or wait on them.

**Critically: this task must not change any existing public behavior.**
`sort_portfolios`, `double_sort_portfolios`, and `long_short_return` are
already reviewed, approved, and merged. This task is a pure "make private
things public" refactor — same algorithm, same numbers, just a new name
and a `__all__` entry. The acceptance bar is that every existing test
passes with **identical** results, proving nothing changed.

**Your working directory for this task is:**

```
/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-e1-portfolio-sort-primitives
```

`cd` there before running any command. It is a git worktree on branch
`phase2/task-e1-portfolio-sort-primitives`, branched from `master`. A venv
already exists at `.venv` with the project installed
(`pip install -e ".[dev]"`) and `.venv/bin/pytest` passing (115/115) before
you start. If anything looks stale, re-run
`.venv/bin/pip install -e ".[dev]"` from that directory. Run tests with
`.venv/bin/pytest`.

## File ownership

**You may modify exactly these two files, and no others:**

- `smart_beta/engines/portfolio_sort.py`
- `tests/test_portfolio_sort.py`

**You must not modify anything else**, including but not limited to:
`smart_beta/benchmarks/*.py` (a later task reuses what you expose here,
but does not touch it in this task), `smart_beta/engines/fama_macbeth.py`,
`smart_beta/engines/inference.py`, `smart_beta/factors/*`,
`smart_beta/data/*`, `smart_beta/config/settings.py`.

## What to do

`smart_beta/engines/portfolio_sort.py` currently has two private helpers
relevant here (read the full file for context before changing anything):

```python
def _assign_groups(values: pd.Series, n_groups: int) -> pd.Series:
    """Partition ``values`` into ``n_groups`` equal-ish buckets. ..."""
    ...

def _group_return_stats(
    members: pd.DataFrame, ret_col: str, weight_col: str
) -> tuple[float, float, int, int, float]:
    """Equal- and value-weighted return for one already-formed group. ..."""
    ...
```

1. Rename `_assign_groups` to `assign_groups` and `_group_return_stats` to
   `group_return_stats` (drop the leading underscore; keep everything else
   — parameters, return shape, docstring content, internal logic —
   byte-for-byte identical). Update every internal call site within
   `portfolio_sort.py` (`sort_portfolios`, `double_sort_portfolios`) to use
   the new names.
2. Add both new names to the module's `__all__` (add one if the module
   doesn't already export one; if it already has `__all__`, extend it —
   check the current file).
3. Expand each function's docstring slightly to note it is now part of
   the module's public, reusable surface (e.g. "Exposed for reuse by other
   modules that need the same group-assignment/value-weighting logic
   without depending on `sort_portfolios`'s full aggregation loop, such as
   `smart_beta.benchmarks`.").
4. Leave `_empty_result_frame` and any other private helper alone unless
   another module will need it too — only promote what's actually needed
   for reuse (the two named above).

## Required tests

- All existing `tests/test_portfolio_sort.py` tests must pass with
  **identical** results after the rename (they call `sort_portfolios`/
  `double_sort_portfolios`, which internally call the renamed functions —
  if any existing test's expected values change, you have introduced a
  behavior change, which is out of scope; find and fix the cause rather
  than adjusting the test).
- Add direct unit tests for `assign_groups` and `group_return_stats`
  called on their own (not just indirectly through `sort_portfolios`), so
  a future caller (like `benchmarks/*.py`) has test coverage confirming
  their contract independent of the aggregation loop around them. Cover
  at minimum: `assign_groups` on a small hand-built `Series` with known
  bucket boundaries (including a tie straddling a boundary, and `n < n_groups`
  producing some empty buckets); `group_return_stats` on a small hand-built
  group `DataFrame` with a known equal- and value-weighted answer,
  including the empty-group case (`n_stocks == 0`) and NaN-return/NaN-weight
  handling.

## Non-goals

- Do not touch `smart_beta/benchmarks/capm.py` even though it has the
  duplicate logic this task is meant to eventually replace — consuming
  these newly-public functions there is a separate, later task.
- Do not change `sort_portfolios`/`double_sort_portfolios`/
  `long_short_return`'s public signatures or behavior.
- Do not add new sorting features, options, or edge-case handling beyond
  exposing what already exists.

## Acceptance criteria

- 33 pre-existing `test_portfolio_sort.py` tests pass with unchanged
  results (rename-only, not a behavior change).
- New direct unit tests for `assign_groups`/`group_return_stats` pass.
- `git diff --stat` against your branch's merge-base with `master` shows
  changes to exactly `smart_beta/engines/portfolio_sort.py` and
  `tests/test_portfolio_sort.py` — no other file touched.
- `git grep -n "_assign_groups\|_group_return_stats"
  smart_beta/engines/portfolio_sort.py` finds no remaining references to
  the old private names (confirms the rename is complete, not just
  additive).

## When done

Run the full test suite (`.venv/bin/pytest`) from your worktree, commit
your changes on branch `phase2/task-e1-portfolio-sort-primitives`, and
report back:

(a) the exact public signatures of `assign_groups` and
`group_return_stats`;
(b) confirmation that all internal call sites were updated and no old
private names remain;
(c) test results (should be 33 pre-existing + your new direct unit tests,
all passing, 115 total suite-wide plus your additions);
(d) the `git diff --stat` output, so file-ownership compliance can be
verified at a glance.

Do not merge your branch, do not touch `master`, and do not modify files
outside the two listed above.
