# Phase 1 Worker Task — Task C: Portfolio Sort Engine

## Common Phase 1 background (read this first)

You are implementing one task of Phase 1 of the `smart_beta` project. This
is a from-2020 China A-share factor-investing research codebase,
originally four standalone, buggy Jupyter notebooks (`Beta.ipynb`,
`BetaEffect.ipynb`, `CAPM.ipynb`, `Factor_Effect.ipynb` — still present at
the repo root; read them for context on what the old, broken version did,
but do not modify or delete them). Phase 0 replaced the notebooks'
position-indexed numpy arrays with a schema-driven, long-format pandas
package to eliminate an entire class of alignment bugs found in review
(off-by-one rolling windows, NaN silently coerced to 0, weighted means
normalized by the wrong denominator, etc.).

**Your working directory for this task is:**

```
/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-c-portfolio-sort
```

`cd` there before running any command. It is a git worktree already
checked out on branch `phase1/task-c-portfolio-sort`, branched from
`master` at commit `34058fa` ("Add Phase 0 package skeleton..."). A venv
already exists at `.venv` in that worktree with the project installed
(`pip install -e ".[dev]"` — pandas 3.0.5, numpy, scipy, statsmodels,
scikit-learn, pytest all present and `.venv/bin/pytest` passing 17/17
before you start). If anything looks stale, re-run
`.venv/bin/pip install -e ".[dev]"` from that directory. Run tests with
`.venv/bin/pytest`.

Fixed foundation, already merged to `master`, **DO NOT MODIFY**:

- `smart_beta/data/schema.py` (long-format panel schema, `validate_panel()`)
- `smart_beta/data/sources/base.py` (`DataSource` abstract interface)
- `smart_beta/data/sources/synthetic.py` (`SyntheticDataSource`,
  `make_default_fixture()`)
- `smart_beta/config/settings.py` (`Settings` dataclass, `DEFAULT_SETTINGS`)
- `tests/conftest.py` (pytest fixture `synthetic_source`)

If you find you need a new named constant in `Settings` or a new schema in
`schema.py`, do **not** edit those files — five other workers are
implementing other Phase 1 tasks in parallel worktrees against the same
base, and editing shared files risks merge conflicts across tasks.
Instead hardcode locally with a `# TODO(settings): ...` comment and flag
it explicitly in your final report.

You are working in isolation from five parallel tasks (universe
construction — **already complete and approved**, on branch
`phase1/task-a-universe` — plus beta estimation, Fama-MacBeth engine,
inference utilities, and benchmark factor construction, all still in
progress). Do not assume any of their deliverables exist in your
worktree, and do not import from files owned by another task. If your
logic conceptually overlaps with another task's, duplication is fine for
Phase 1 — it gets reconciled during integration. Do not attempt
cross-task integration yourself.

Your code must not mutate input DataFrames in place. Always `.copy()`
before modifying.

**When done:** run the full test suite (not just your new file) from your
worktree to confirm you haven't broken anything, commit your changes on
your branch (`phase1/task-c-portfolio-sort`), and report back:
(a) the exact function signatures you settled on,
(b) any settings constants you'd want added to the shared `Settings`
dataclass,
(c) test results,
(d) anything you scoped down or left as a stretch goal.

Do not merge your branch, do not touch `master`, and do not modify files
outside the ones listed as yours below.

---

## Task C specifics: Portfolio sort engine

**File ownership** (create these; touch nothing else):

- `smart_beta/engines/portfolio_sort.py` (new)
- `tests/test_portfolio_sort.py` (new)

`smart_beta/engines/__init__.py` already exists (empty, from Phase 0) —
do not modify it; just add your new module file inside `engines/`.

### Context: bugs to fix

Implement generic N-way (and double) portfolio sorting, fixing bugs found
in the original `CAPM.ipynb` / `Factor_Effect.ipynb` `calresult()`
function:

1. Value-weighted group return was divided by `nansum(market cap)` over
   the **whole cross-section** rather than over just that group's stocks
   — so the five groups' "VW returns" summed to roughly the market return
   rather than each being a proper within-group weighted mean. The weight
   denominator must always be the **group's own** weight-column sum.
2. The lowest group used `<=` at its percentile boundary while other
   groups used strict `<`/`>`, silently dropping boundary-tied
   observations. Groups must partition the cross-section with no drops.
3. `if len(a) == 0 | len(b) == 0` used bitwise `|` instead of logical `or`
   — beyond the operator bug, an empty cross-section was silently skipped
   rather than surfaced. Your version should make this an explicit,
   visible choice (e.g. return NaN for that date, not silently omit the
   row).
4. `CAPM.ipynb`'s "Short-Term Reversal" section actually passed the
   `momentum` array as the sort characteristic instead of the reversal
   variable — identical output to the momentum section, an unnoticed
   copy-paste bug. Design your API so the sort characteristic, forward
   return, and weight columns are explicit, unambiguous named arguments.

### Deliverable

`smart_beta/engines/portfolio_sort.py`, roughly:

```python
def sort_portfolios(panel, char_col, ret_col, weight_col, date_col="date",
                     n_groups=DEFAULT_SETTINGS.n_portfolio_groups) -> pd.DataFrame
    # per-date, per-group equal-weighted AND value-weighted forward return

def double_sort_portfolios(panel, char_col_1, char_col_2, ret_col, weight_col,
                            n_groups_1, n_groups_2, date_col="date") -> pd.DataFrame

def long_short_return(sorted_returns, low_group=1, high_group=n_groups) -> pd.Series
```

### Tests

Hand-build a small DataFrame with an analytically known answer to verify:

- VW group return matches a manual weighted average restricted to that
  group (directly targeting bug #1);
- group sizes across a date partition the full cross-section — no rows
  silently dropped at boundaries (bug #2);
- empty-cross-section dates are handled explicitly and visibly, not
  silently skipped (bug #3);
- a test that would have caught the momentum/reversal copy-paste bug —
  i.e. feeding two different characteristic columns produces two
  different, column-specific results (bug #4).

Also add at least one test on `double_sort_portfolios` confirming it
composes two characteristics correctly (e.g. a size-then-beta double sort
on a small hand-built panel).
