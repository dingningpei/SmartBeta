# Phase 1 Worker Task — Task D: Fama-MacBeth Cross-Sectional Regression Engine

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
/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-d-fama-macbeth
```

`cd` there before running any command. It is a git worktree already
checked out on branch `phase1/task-d-fama-macbeth`, branched from
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
`phase1/task-a-universe` — plus beta estimation, portfolio-sort engine,
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
your branch (`phase1/task-d-fama-macbeth`), and report back:
(a) the exact function signatures you settled on,
(b) any settings constants you'd want added to the shared `Settings`
dataclass,
(c) test results,
(d) anything you scoped down or left as a stretch goal.

Do not merge your branch, do not touch `master`, and do not modify files
outside the ones listed as yours below.

---

## Task D specifics: Fama-MacBeth cross-sectional regression engine

**File ownership** (create these; touch nothing else):

- `smart_beta/engines/fama_macbeth.py` (new)
- `tests/test_fama_macbeth.py` (new)

`smart_beta/engines/__init__.py` already exists (empty, from Phase 0) —
do not modify it; just add your new module file inside `engines/`.

### Context: bugs to fix

Implement a proper Fama-MacBeth cross-sectional regression, fixing bugs
from the original `BetaEffect.ipynb`:

1. It computed `bm = dwse / mk`, but `dwse` is a sales-like field, not
   book value — so the "book-to-market" factor was mislabeled and was
   actually closer to a Sales/Price ratio. Lesson for your implementation:
   accept named characteristic columns and don't assume semantics from a
   variable's name; document what each characteristic actually is.
2. EBITDA was used in raw, unstandardized currency units, producing
   coefficients ~1e-8 in magnitude — meaningless to interpret or compare.
   Your engine must cross-sectionally standardize (or z-score) and
   winsorize (`settings.winsorize_lower_pct` / `winsorize_upper_pct`)
   each characteristic before regressing, every period.
3. Only mean coefficients and mean R² were reported — no significance
   testing at all. Your engine must report Newey-West-adjusted t-stats on
   the time-series of per-period coefficients (`settings.newey_west_lags`
   lags). Implement this yourself directly via
   `statsmodels.api.OLS(...).fit(cov_type="HAC", cov_kwds={"maxlags": ...})`
   on the coefficient time series — do not depend on Task E's
   `engines/inference.py`, it may not be merged yet; a small amount of
   duplicated Newey-West logic between your task and Task E is expected
   and will be reconciled during integration.

### Deliverable

`smart_beta/engines/fama_macbeth.py`, roughly:

```python
def fama_macbeth(panel, characteristic_cols, ret_col, date_col="date",
                  industry_col=None, settings=DEFAULT_SETTINGS) -> FamaMacBethResult
    # FamaMacBethResult: per-period coefficients, mean coefficients,
    # Newey-West t-stats, mean R^2
```

### Tests

Use `synthetic_source`'s `signal` characteristic
(`smart_beta/data/sources/synthetic.py`) as your known-answer test: its
value at date *t* is built to have a known linear effect
(`ground_truth.true_signal_coef`) on the return at date *t + 1* (see the
module's docstring). Regress next-period return on the **lagged** signal
and verify you recover a coefficient close to `true_signal_coef` with a
clearly significant `|t-stat|`.

Add a negative-control test (e.g. shuffle the signal across stocks within
each date before regressing) confirming the coefficient collapses toward
insignificance when the true relationship is destroyed.

Add a test confirming winsorization/standardization actually runs — e.g.
feed a characteristic with an extreme outlier and confirm it doesn't
dominate the regression the way it would with no winsorization (compare
against a manually-computed unwinsorized OLS to show the difference is
real, not just "the function ran").
