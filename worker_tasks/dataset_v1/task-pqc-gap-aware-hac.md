# PQC — Gap-aware HAC for the P-Q QUARANTINED estimand (Dataset v1.2) — FROZEN BEFORE IMPLEMENTATION

- **Status:** FROZEN BEFORE IMPLEMENTATION. Implementation requires a separate
  launch authorization. Master plan: `worker_tasks/dataset_v1/dataset-v1-2-pq-plan.md`.
- **Authority, in precedence order** (on any conflict, stop and report):
  1. `docs/discovery_dataset_v1_2_pq_quarantine_rules.md` §Q9 —
     `df5bec69103eca5ad9196b3342e5013fab30a3b85cdb3a5332b033dcac5db886`
     (`quarantine_rule_hash`);
  2. `docs/discovery_dataset_v1_2_pq_amendment.md` §3 (R2) —
     `9261a55b9b82a5708dded65bfd3083d5a960cfa4375681b1b23390da62fb3ed0`.
- **Owned files (new only):**
  - `smart_beta/engines/gap_hac.py`;
  - `tests/test_dataset_v1_2_gap_hac.py`;
  - `tests/fixtures/dataset_v1_2/pqc/**`, including `mutations.json`.
- **Must not modify any existing file.** In particular, `smart_beta/engines/inference.py`,
  `smart_beta/evaluation/metrics.py`, `smart_beta/config/settings.py` and every
  other Phase-7 file are read-only. `metrics.py` is hash-pinned by D7b, so changing
  it would break D7b.
- **Provider or network calls: 0.** No data artifact is read.

**Pinned read-only code (SHA-256; fail closed on mismatch in tests):**

| File | SHA-256 |
|---|---|
| `smart_beta/engines/inference.py` | `516b290e37098a27d8f5d9864cf473ef4d541cb2465359b3d340b5948c825b51` |
| `smart_beta/evaluation/metrics.py` | `747282a2ee7cc7b63d0511d29ab3bdf8c3c9aceea9fb965dd64de543d76831a8` |
| `smart_beta/config/settings.py` | `08ecf0d24e2376bd1346d83e165b254f7e0d2a60408d48f0bfeb84221bee43af` |

## 1. Scope

PQC adds **one narrow capability**: the mean and Newey-West t-statistic of a series
that lives on an **original formation-time index with explicit gaps**.

PQC changes no existing estimator, lag policy, metric, threshold or caller. It
introduces no new estimator family, no general inference rewrite, and no
refactoring.

## 2. Current ownership point (fact, read-only)

- `metrics._newey_west_aggregate(values)` drops non-finite values, **compresses**
  the rest, and calls `engines.inference.newey_west_ols(arr, ones, L)` with
  `L = DEFAULT_SETTINGS.newey_west_lags` (currently 6). That is statsmodels `OLS`
  with `cov_type="HAC"`, `maxlags=L` and a Bartlett kernel.
- **Edge rules:** n < 2 gives (NaN, NaN, n); a non-finite mean gives NaN; zero
  range (`np.ptp == 0`) gives (mean, NaN, n); a non-finite t gives NaN.
- **The lag policy is a fixed L.** It is not horizon-dependent; no horizon- or
  overlap-aware lag rule exists in the current code. PQC preserves exactly this
  policy: the same L, the same kernel, the same small-sample conventions.

## 3. API (frozen)

```python
@dataclass(frozen=True)
class GapAwareHACResult:
    mean: float                 # NaN when status != "OK" and n_obs < 2
    t_stat: float               # NaN unless status == "OK"
    n_obs: int                  # finite observations on the index
    n_index: int                # length of the original index
    n_interior_gaps: int        # non-finite positions strictly between first and last finite
    status: str                 # "OK" | "INSUFFICIENT_SUPPORT" | "ZERO_RANGE"
                                # | "NONPOSITIVE_VARIANCE" | "NONFINITE"
    lags: int

def gap_aware_mean_tstat(values_on_index: Sequence[float], *, lags: int | None = None) -> GapAwareHACResult
```

- **Input:** `values_on_index[i]` is the value at **original index position i**
  (0 … T−1, consecutive positions of the frozen evaluation schedule). NaN or ±inf
  marks a gap.
  - The caller (PQB) builds this array from the original schedule. PQC never
    reorders.
  - Pandas inputs are rejected unless converted by the caller; 2-D input is
    rejected.
- **`lags`:** `None` means `DEFAULT_SETTINGS.newey_west_lags`. An explicit value is
  allowed only for tests and must be an int ≥ 0.

## 4. Semantics (frozen)

1. **Available set:** A = {i : value_i finite}, with n = |A|.
   - If n < 2: `INSUFFICIENT_SUPPORT` (mean NaN, t NaN).
2. **Mean:** x̄ = mean over A, computed in index order.
   - Non-finite gives `NONFINITE`.
   - Zero range over A gives `ZERO_RANGE` (the mean is returned, t is NaN).
3. **Residuals:** u_i = value_i − x̄ for i ∈ A.
4. **Lag-k covariance uses original distance only:**
   Γ_k = Σ over i with i ∈ A **and** i+k ∈ A of u_i · u_{i+k}, for k = 1 … L.
   - **A pair contributes to lag k only when its original index distance is
     exactly k.**
   - No pair is formed across a gap by adjacency after filtering.
5. **Long-run sum:** S = Γ_0 + 2 Σ_{k=1..L} w_k Γ_k, with Γ_0 = Σ_{i∈A} u_i² and
   w_k = 1 − k/(L+1) (Bartlett).
6. **Variance and t:** Var(x̄) = c · S / n², where **c is exactly the small-sample
   factor that `newey_west_ols` (statsmodels HAC defaults as called) applies with
   nobs = n and one regressor**. Then t = x̄ / √Var(x̄).
   - **Planner pre-check:** on a synthetic 10-point series, the existing
     `_newey_west_aggregate` t-statistic equals x̄/√(S/n²) to 1e-15, so **c = 1**
     (no small-sample correction) under the current statsmodels call.
   - The parity tests below remain the binding definition.
   - If S ≤ 0, or Var is non-finite: `NONPOSITIVE_VARIANCE` (t NaN). With gaps, the
     masked Bartlett sum is not guaranteed positive semi-definite. **No
     correction, truncation, alternative kernel or fallback is applied.**
7. **No fallback:** no call to compressed-time `newey_west_ols`, and no other
   estimator, is ever used to produce the result.
8. **Determinism:** pure function. Summation is in index order, there is no
   randomness, and the output does not depend on the timezone or locale.

**Binding parity definition** (this pins c and every statsmodels convention).
For every gap-free finite input (no interior gaps; leading and trailing non-finite
positions allowed), the result must satisfy all of:
- `mean` equals `metrics._newey_west_aggregate(compressed)[0]` **bit-for-bit**;
- `t_stat` is within relative 1e-10 (absolute 1e-12 near 0) of
  `metrics._newey_west_aggregate(compressed)[1]`;
- `n_obs` and the edge statuses agree with the existing edge rules.

## 5. Overlap and horizon interaction

- Forward-return overlap is handled only through the unchanged fixed L applied on
  original index distance.
- PQC adds no horizon-dependent lag rule.
- If a future authority changes the lag policy, PQC must be re-frozen. PQC never
  infers L from h.

## 6. Required tests (synthetic only; socket guard; credential sentinel)

1. Parity on gap-free series: random seeds × n ∈ {2, 3, 5, 7, 8, 13, 50, 200}.
2. Leading and trailing NaN only: equals the compressed result.
3. The canonical example: positions t1, t2, [gaps], t8, t9. Hand-computed Γ_k proves
   that t2 and t8 never pair for k = 1, and that they pair only if 8 − 2 ≤ L at
   k = 6.
4. A gap at distance exactly L+1 contributes nothing.
5. Weights w_k exact; L = 0 reduces to the White-type sum. Parity at L = 0 against
   `newey_west_ols(…, 0)`.
6. n = 0, 1, 2 edge statuses.
7. Zero range → `ZERO_RANGE`.
8. A constructed negative-S gapped series → `NONPOSITIVE_VARIANCE`, t NaN, and no
   exception.
9. Inf values are treated as gaps.
10. Invariance under timezone and locale (subprocess with `TZ=Asia/Tokyo LC_ALL=C`).
11. Input immutability (the caller's array is not modified).
12. Rejects 2-D input, pandas reordering, and non-int or negative lags.
13. A pinned-code hash check for the three read-only files.
14. A static check that the module does not import `newey_west_ols` for result
    production. It may be imported only in tests for parity.
15. The default L equals `DEFAULT_SETTINGS.newey_west_lags`.
16. A bit-equal mean against a numpy reference over A in index order.

## 7. Required mutations (`tests/fixtures/dataset_v1_2/pqc/mutations.json`; planner re-executes all)

- G1 compress before the lag sums (adjacency pairing)
- G2 pair across gaps using rank positions
- G3 drop the Bartlett weights (uniform)
- G4 Bartlett w_k = 1 − k/L
- G5 lag range k = 0 … L−1
- G6 one-sided covariance (missing factor 2)
- G7 divide by n−1 instead of the parity-defined c
- G8 omit c
- G9 fallback to `newey_west_ols` when gaps exist
- G10 clip a negative S to 0 and return t = ±inf or a finite value
- G11 return a finite t on `ZERO_RANGE`
- G12 n < 2 returns a finite mean
- G13 treat inf as a value
- G14 mean over all positions with NaN = 0
- G15 default L hard-coded to a value other than the settings value
- G16 reorder the input by value
- G17 mutate the caller's array
- G18 count leading or trailing NaN as interior gaps
- G19 L inferred from array length
- G20 use the residuals of the compressed mean of a different subset

**20 mutations.** Any surviving non-equivalent mutation means STOP BEFORE MERGE.

## 8. Barrier (planner)

- review;
- all tests, plus all 20 mutations re-executed by the planner, plus a no-op
  control;
- the parity sweep re-run independently by the planner;
- a credential-scrubbed full suite;
- a network-denial guard.

## 9. Certification and nonclaims

**Proposed certification:** PQC certifies a deterministic mean and Newey-West
t-statistic over a gapped original-index series. Lag pairs are formed only at the
exact original distance. It has bit/1e-10 parity with the existing compressed
estimator on gap-free input, and fails closed (NaN with a typed status) on
insufficient support, zero range, non-finite values and non-positive long-run
variance.

**Not claimed:**
- that the masked Bartlett estimator is PSD with gaps (it is not guaranteed);
- improved efficiency or coverage;
- any change to B-estimand inference;
- any factor result.

## Report

- files changed;
- test and mutation counts, with test-to-item and mutation-to-test maps;
- the commit SHA;
- the line `sealed files modified: NONE`.
