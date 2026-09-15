# Phase 1 Worker Task — Task B: Beta Estimation

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
/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-b-beta
```

`cd` there before running any command. It is a git worktree already
checked out on branch `phase1/task-b-beta`, branched from `master` at
commit `34058fa` ("Add Phase 0 package skeleton..."). A venv already
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
`phase1/task-a-universe` — plus portfolio-sort engine, Fama-MacBeth
engine, inference utilities, and benchmark factor construction, all still
in progress). Do not assume any of their deliverables exist in your
worktree, and do not import from files owned by another task. If your
logic conceptually overlaps with another task's, duplication is fine for
Phase 1 — it gets reconciled during integration. Do not attempt
cross-task integration yourself.

Your code must not mutate input DataFrames in place — this is a specific
bug found in the original `Beta.ipynb` (`rankrt_none = rankrt` with no
`.copy()`, followed by in-place NaN-to-zero coercion that corrupted the
shared array). Always `.copy()` before modifying.

**When done:** run the full test suite (not just your new file) from your
worktree to confirm you haven't broken anything, commit your changes on
your branch (`phase1/task-b-beta`), and report back:
(a) the exact function signatures you settled on,
(b) any settings constants you'd want added to the shared `Settings`
dataclass,
(c) test results,
(d) anything you scoped down or left as a stretch goal.

Do not merge your branch, do not touch `master`, and do not modify files
outside the ones listed as yours below.

---

## Task B specifics: Beta estimation

**File ownership** (create these; touch nothing else):

- `smart_beta/factors/base.py` (new)
- `smart_beta/factors/beta.py` (new)
- `tests/test_beta.py` (new)

You own the entire `smart_beta/factors/` directory for this phase — no
other Phase 1 task touches it.

### Context: bugs to fix

Implement beta estimators, fixing three concrete bugs found in the
original `Beta.ipynb`:

1. `rankrt_none = rankrt` followed by in-place `rankrt_none[isnan] = 0` —
   no `.copy()`, so the source return matrix was mutated, and missing
   (pre-listing/suspended) months were silently treated as 0% return.
2. The output beta matrix was `np.zeros((nr, nc))`-initialized, so a stock
   with no estimate got `beta = 0`, indistinguishable from a genuinely
   near-zero beta. Un-estimated betas must be `NaN`.
3. The "enough data" check required a *fully complete* window (zero
   missing months) — brittle. Replace with a minimum-valid-observation-
   count threshold (a settings constant, `beta_min_valid_obs`, already
   present in `DEFAULT_SETTINGS`) computed over whatever non-NaN months
   exist in the window.

### Deliverable

`smart_beta/factors/base.py`: a small `Factor` interface —
`compute(panel: pd.DataFrame) -> pd.DataFrame` returning a long-format
`(date, stock_id, value)` frame. Keep it minimal; this is Phase 1's only
`factors/` task so you own the whole directory and can shape this however
makes the concrete estimators below cleanest to implement.

`smart_beta/factors/beta.py`, implementing at least:

```python
def rolling_ols_beta(returns, market_return, risk_free, settings=DEFAULT_SETTINGS) -> pd.DataFrame
def shrink_beta(beta_panel, shrinkage=0.6, target=1.0) -> pd.DataFrame  # Frazzini-Pedersen style
```

Attempt a Dimson beta (adds a lagged market-return regressor to absorb
non-synchronous trading) as a stretch goal — the synthetic fixture is
monthly only, so this is testable but less strongly motivated than with
daily data; note in your report if you scope it down.

### Tests

Use `synthetic_source.ground_truth.true_betas` and `.market_return`
(`smart_beta/data/sources/synthetic.py`) to verify `rolling_ols_beta`
recovers betas correlated with truth (e.g. correlation > 0.8 across
stocks — this mirrors Phase 0's own smoke test in
`tests/test_synthetic_source.py`, but now testing your actual estimator
rather than a throwaway `np.polyfit`).

Add explicit regression tests targeting the three bugs above directly:

- your function does **not** mutate its input DataFrames (bug #1);
- beta is `NaN`, not `0`, when valid observations in the window fall below
  `settings.beta_min_valid_obs` (bugs #2/#3);
- a window with some missing months but still >= `beta_min_valid_obs`
  valid months still produces an estimate (proving you didn't just
  reimplement the old "zero missing months" requirement under a new
  name).
