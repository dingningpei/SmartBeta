# PQC — Specification Erratum E1: the frozen gap-masked Bartlett long-run sum is positive by construction (ADOPTED)

- **Status:** ADOPTED. The user approved it on 2026-10-01 ("E1 APPROVED — FREEZE PQC
  ERRATUM AND MERGE PQC").
- **This is a standalone erratum.** The frozen files it corrects are **not** edited
  and remain history.
- **It corrects:**
  - `worker_tasks/dataset_v1/task-pqc-gap-aware-hac.md` (bab2296),
    `820f8d45d44c8cd69e353dc88b3d8fa93704cbf9b5b13fbd0cbc1e440217dfef`:
    §4 item 6, §6 test 8, §7 (G8, G10 status), §9 nonclaims;
  - `worker_tasks/dataset_v1/dataset-v1-2-pq-plan.md` (bab2296),
    `a196d4f31822a1909f590947d97ad038e5fe70f74064a3b6814ad2643067a0a1`:
    §11 row "Hidden estimator change".
- **The governing PQC authority is now** the frozen PQC spec **plus this erratum**.
  Where they differ, this erratum governs.
- **Classification:** a frozen-specification defect, which was a planner error.
  It is **not** an implementation defect.
  - It was reported by the PQC worker at commit
    `d157521a708dd1aec74210107fc04ef61f8bd1b8`.
  - It was independently verified by the planner.

## 1. The defect

The frozen text stated that, with gaps, "the masked Bartlett sum is not guaranteed
positive semi-definite" (PQC §4 item 6, and the §9 nonclaim). Plan §11 repeated
this ("Gapped cases fail closed on non-positive variance").

From it, PQC §6 test 8 required a constructed ordinary finite gapped series with
negative S, and §7 implied that mutation G10 must be killed.

**For the frozen PQC construction this is false. Ordinary quarantine gaps do not
create a negative-S case.**

## 2. Correction (narrowly scoped to the frozen PQC construction)

Under the frozen PQC finite-sample construction:
- residuals u_i = x_i − x̄ over the available positions of a finite original index;
- Γ_k summed only over pairs at exact original distance k ≤ L;
- S = Γ_0 + 2 Σ_{k=1..L} w_k Γ_k with w_k = 1 − k/(L+1) and the frozen fixed L;
- finite inputs that are non-degenerate (at least two available values, not all
  equal).

Under these conditions, **the long-run sum S is strictly positive by construction.**

**Rationale, specific to this construction:**
- Let ũ be the full-index residual vector with ũ_i = 0 at gap positions. Then
  S = ũᵀ T ũ, where T_{ij} = w_{|i−j|} for |i − j| ≤ L and 0 otherwise, with w_0 = 1.
- This T is the triangular-weight Toeplitz matrix of the frozen weights. Its entries
  are the autocorrelation sequence of a length-(L+1) constant window, whose spectral
  density is a non-negative, not identically zero (Fejér-type) function. So T is
  positive definite for every finite index length.
- Non-degenerate input gives ũ ≠ 0, hence S > 0.
- Gaps only zero components of ũ. They do not change T.
- This claim is made **only** for the frozen PQC weights, lag structure and finite
  inputs. No claim is made about other kernels, weightings or estimators.

**Independent planner verification** (synthetic only):
- the minimum eigenvalue of T over index lengths ≤ 39 with L = 6 is about 0.0079 (> 0);
- 200,000 random masked trials gave 0 cases with S < −1e-12.

## 3. Numerical guard (kept unchanged)

- The implementation guard (S ≤ 0, or a non-finite S or variance, gives
  `NONPOSITIVE_VARIANCE`, t = NaN, and no exception) **is kept** as a defensive
  numerical fail-closed condition.
- It is reachable through floating-point overflow or non-finite arithmetic.
- Its existence does **not** imply that a finite non-degenerate ordinary input
  naturally produces S ≤ 0.

## 4. Test 8 (replacement)

Frozen test 8 ("a constructed negative-S gapped series → `NONPOSITIVE_VARIANCE`") is
replaced by:
- **A.** A numerical overflow / non-finite case demonstrating the guard:
  `NONPOSITIVE_VARIANCE`, t NaN, no exception.
- **B.** A diagnostic property test confirming S ≥ 0 (positive for non-degenerate
  input) on valid finite gapped inputs under the frozen construction, including that
  T is positive definite.

Estimator semantics are not changed to manufacture a negative case. Implemented at
`d157521` as `test_item08_nonpositive_variance_fail_closed` (A) and
`test_item08_evidence_masked_long_run_is_psd` (B).

## 5. Mutation classification

| Mutation | Classification | Equivalence argument (independently verified) |
|---|---|---|
| G8 (omit c) | **EQUIVALENT** | c = 1.0 under the parity definition. `long_run / n²` and `1.0 * long_run / n²` are bit-identical in IEEE-754, so no externally observable output can differ |
| G10 (clip a negative S to 0) | **EQUIVALENT** | The mutated code sets S := 0 when S ≤ 0 and then reaches the same unmodified condition `long_run <= 0.0 or not isfinite(...)`, returning `NONPOSITIVE_VARIANCE` with t NaN. That is exactly the original outcome for every S ≤ 0. For S > 0 or non-finite S, the mutation is inactive. So for every reachable state, original and mutated paths return the same result |

- G8 and G10 are **not counted as killed**. Their survival is **not** a
  mutation-barrier failure.
- Every other frozen mutation (G1–G7, G9, G11–G20; 18 in total) is non-equivalent and
  must remain killed.
- The no-op control is recorded separately.

## 6. §9 nonclaim and plan §11 (corrected wording)

- **PQC §9, "Not claimed":** replace "that the masked Bartlett estimator is PSD with
  gaps (it is not guaranteed)" with "any property beyond the stated parity and the
  positivity of S by construction (no efficiency, size or coverage claim)".
- **Plan §11, "Hidden estimator change":** read "… Gapped inputs give S > 0 by
  construction for the frozen PQC weights (§2 of this erratum). The
  non-positive-variance guard is a numerical fail-closed condition only."

## 7. Impact

| Item | Changed? |
|---|---|
| estimator definition (mean, Γ_k, S, c, t) | **NO** |
| original formation-distance lag semantics | **NO** |
| L selection (`DEFAULT_SETTINGS.newey_west_lags`) | **NO** |
| overlap semantics | **NO** |
| public API (`gap_aware_mean_tstat`, `GapAwareHACResult`) | **NO** |
| typed failure states | **NO** |
| no-fallback rule / no-new-estimator rule | **NO** |
| deterministic behaviour | **NO** |

- **Methodology impact:** NONE.
- **Implementation impact:** NONE. `d157521` conforms to PQC plus E1 with no code
  change.
