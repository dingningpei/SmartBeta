# Phase 1 Worker Task — Task E: Statistical Inference Utilities

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
/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-e-inference
```

`cd` there before running any command. It is a git worktree already
checked out on branch `phase1/task-e-inference`, branched from `master`
at commit `34058fa` ("Add Phase 0 package skeleton..."). A venv already
exists at `.venv` in that worktree with the project installed
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
Fama-MacBeth engine, and benchmark factor construction, all still in
progress). Do not assume any of their deliverables exist in your
worktree, and do not import from files owned by another task. If your
logic conceptually overlaps with another task's, duplication is fine for
Phase 1 — it gets reconciled during integration. Do not attempt
cross-task integration yourself.

Your code must not mutate input DataFrames in place. Always `.copy()`
before modifying.

**When done:** run the full test suite (not just your new file) from your
worktree to confirm you haven't broken anything, commit your changes on
your branch (`phase1/task-e-inference`), and report back:
(a) the exact function signatures you settled on,
(b) any settings constants you'd want added to the shared `Settings`
dataclass,
(c) test results,
(d) anything you scoped down or left as a stretch goal.

Do not merge your branch, do not touch `master`, and do not modify files
outside the ones listed as yours below.

---

## Task E specifics: Statistical inference utilities

**File ownership** (create these; touch nothing else):

- `smart_beta/engines/inference.py` (new)
- `tests/test_inference.py` (new)

`smart_beta/engines/__init__.py` already exists (empty, from Phase 0) —
do not modify it; just add your new module file inside `engines/`.

### Context: why this is needed

The original notebooks had no Newey-West correction anywhere and no joint
significance testing — only single-factor CAPM alpha estimates read off a
plain OLS summary. Implement the shared inference utilities the rest of
the package (and, eventually, Task D's Fama-MacBeth engine and Task F's
benchmark spanning tests) will build on.

### Deliverable

`smart_beta/engines/inference.py`, roughly:

```python
def newey_west_ols(y, X, lags=DEFAULT_SETTINGS.newey_west_lags)
    # thin wrapper ensuring cov_type="HAC" with the given lag count;
    # return the fitted statsmodels result object directly

def grs_test(alphas, residual_cov, factor_means, factor_cov, n_obs) -> (f_stat, p_value)
    # Gibbons-Ross-Shanken test that a vector of portfolio alphas is
    # jointly zero, given a benchmark factor model
```

Treat a bootstrap significance utility (comparing an observed t-stat
against a bootstrap null distribution of "random factor" t-stats, in the
spirit of Harvey-Liu-Zhu (2016)'s multiple-testing concerns) as an
optional stretch goal; the two functions above are the required core.

### Tests

- `newey_west_ols`: generate synthetic AR(1)-autocorrelated residuals with
  a known coefficient; confirm the NW standard error is larger than a
  naive OLS standard error (the correction should visibly engage) while
  the point estimate of the coefficient is essentially unchanged (NW
  changes standard errors, not point estimates).
- `grs_test`: a Monte Carlo calibration check — construct many
  replications (e.g. 200) of synthetic multi-portfolio excess returns with
  **true alpha = 0** for all portfolios, and confirm the test's rejection
  rate at the 5% level is close to 5% (not systematically inflated).
  Then construct a second scenario with a deliberately injected non-zero
  alpha and confirm the test rejects with high power.
