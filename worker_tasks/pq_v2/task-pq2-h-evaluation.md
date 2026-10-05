# PQ2-H — P-Q v2 evaluation entry: bindings, B reuse, sub-expression realization, L statistics, classification, diagnostics (FROZEN)

- **Authority:**
  - methodology M§1, M§4, M§13, M§14, M§15, M§17, M§18;
  - plan §2, §6.4, §7, §9.
- **Wave:** 5. **Dependencies:** PQ2-A…F (and G) merged.
- **Owned:**
  - `smart_beta/dataset_v1/pqv2_evaluation.py` (new);
  - `tests/test_pqv2_evaluation.py` (new);
  - `tests/fixtures/pqv2/evaluation/**` (new, including `mutations.json` and the
    synthetic end-to-end dataset fixture).
- **Forbidden:** every other file, including all `smart_beta/campaign/*.py`. **No
  Campaign wiring:** plan §9 is prospective.

## 1. `PQv2EvaluationDeclaration` (frozen dataclass)

- **Fields:**
  - `base` (a D7b `CampaignEvaluationDeclaration`);
  - `dataset_id`, `dataset_identity_hash`;
  - `pq_id`, `pq_hash` (the v1 overlay, used as the flag source);
  - `methodology_sha256`, `cs_id`, `ed_id`, `ce_id`;
  - `pq_methodology = "P-Q-v2"`.
- **Methods:** `to_dict()` and `declaration_hash` (D7b `content_hash`).

## 2. `build_pqv2_diagnostics(...)` (signature and steps exactly as plan §6.4 1–8)

- **Step 1:** `pqv2_core.verify_authority()` (raises on mismatch). A declaration
  identity mismatch returns blank diagnostics with `PQV2_IDENTITY_MISMATCH`, produced
  **before** any data access (test: a spy on the resolver sees zero reads).
- **Step 2:** `quarantine.verify_dataset_v1_2_identity` and overlay pq_id/pq_hash →
  `PQV2_DATASET_IDENTITY_MISMATCH` / `PQV2_OVERLAY_MISMATCH`.
- **Step 3:** `pqv2_dependency.certify`, and the metric/sign gates → typed blanks.
- **Step 4:** B = `delisting_bridge.build_development_diagnostics` (unchanged), plus the
  internal reconstruction via the D7b private helpers (as PQB `_reconstruct_b` does).
  The reconstructed B T0/TS values, t and n_obs must be **bit-equal** to D7b's, else
  raise `PQv2InvariantError`.
- **Step 5:** `pqv2_sources.load_source_set` (fold formations, plus the lookback needed
  by the dependency windows).
- **Step 6 (STD_X_RANK only):** realize M and V by evaluating the operand
  sub-expressions as `FactorSpec`s through the Phase-6 evaluator, over the same
  TrustedInputs and masked full cross-section. Assert
  |F_i − R_i(M_i − μ)/σ| ≤ 1e-12·max(1, |F_i|) on every finite cell (σ ddof = 0, R
  average rank). Realize once per hypothesis and share across folds within one call.
- **Step 7:** per formation:
  - `input_uncertainty` → (`simple_assignment` | `sandwich_assignment`);
  - `label_envelope` for the universe members;
  - `compose_series`.
- **Step 8: L statistic** for X ∈ {T0, TS}:
  - **estimator:** apply the same Phase-7 estimator B used for the metric
    (`metrics.long_short_mean_tstat` over the finite values) to the L series restricted
    to B's finite-period index;
  - **unavailability:** if any such period has L_t NotAssessed, the cell is `None` with
    reason `PQV2_L_PERIOD_NOT_ASSESSED`. **No gap-aware estimator, no dropping of
    periods** (plan §6.4 step 8 derivation);
  - **B unavailable:** if the B statistic is unavailable, the reason is
    `PQV2_B_STATISTIC_UNAVAILABLE`.

## 3. `PQv2Diagnostics` (frozen dataclass)

**Content:** plan §7, exactly. It includes:
- the cells `B-T0`, `B-TS`, `L-T0`, `L-TS`;
- the per-period arrays;
- the identities and `pqv2_code_sha256`;
- `certified_class`, `assignment_rule`, `relaxations`;
- `non_claims`, the fixed tuple of the M§18 sentences, including **"P-Q v2 does not
  sensitivity-test the correctness of security identity."** and **"the correction
  model is declared, not empirically certified containment"**;
- the attached D7b `b_diagnostics` and `b_diagnostics_hash`.

**Hashing and persistence:**
- `to_dict()` uses only canonical-JSON-safe values;
- `diagnostics_hash = delisting_bridge.content_hash(self.to_dict())`;
- **must** round-trip through `campaign.diagnostics_store.persist` unchanged, which is
  a test.

## 4. `classify_pqv2(diagnostics, screens) -> PQv2Record`

- **Input:** screen outcomes per cell (`PQv2ScreenOutcome(cell, passed: bool | None)`).
- **Per scenario X**, the M§14 table:

  | B | L | Status |
  |---|---|---|
  | PASS | PASS | `CORRECTION_SENSITIVITY_SCREEN_PASS` |
  | PASS | FAIL | `QUARANTINE_SENSITIVE` |
  | FAIL | PASS | `INVARIANT_VIOLATION_FAIL_CLOSED` (a typed status; **never raises**) |
  | FAIL | FAIL | `DEVELOPMENT_SCREEN_FAIL` |
  | `None`, or a cell statistic unavailable | — | `NOT_ASSESSED` |

- **Record fields:**
  - `advancement_permitted[X]` is True only for PASS;
  - `accept = False`, constant;
  - `confirmation = False`, constant;
  - no aggregation across X (plan §9).

## 5. Required tests (synthetic end-to-end fixture; no real data)

- **End-to-end:** a tiny synthetic Dataset-v1.2-shaped root (D7a / D7c / overlay / D3 CA
  events built by the fixture writer, bound with test bindings, as the PQB tests do).
  Run SIMPLE and STD_X_RANK hypotheses; diagnostics are deterministic in two fresh
  processes (identical hash).
- **Gates:** identity mismatch (no data reads); authority tamper → raises; dataset or
  overlay mismatch; uncertified expression; sign −1 (`V2-M17`); metric IC.
- **B and L:** B reconciliation bit-equal; L ≤ B per period; the L statistic NOT_ASSESSED
  when one period is NOT_ASSESSED.
- **Classification:** the full table over all combinations, including None.
- **Claims:** `non_claims` present and exact; no "ACCEPT", "confirmed", "bounds the true
  return" or "robust to every possible correction" string appears in any diagnostics or
  record field (a claim-language test).
- **Persistence:** `persist` round-trip and replay idempotence.
- **History:** importing `pqv2_evaluation` does not import or alter
  `pq_evaluation.classify_pq`; P-Q v1 tests still pass.

## 6. Mutations (`tests/fixtures/pqv2/evaluation/mutations.json`)

| ID | Mutation |
|---|---|
| V2-M17 | sign gate removed |
| V2-M19 | declaration identity comparison removed, or `verify_authority` call removed |
| H-M1 | L statistic computed over finite L periods only (dropping NOT_ASSESSED periods) |
| H-M2 | the gap-aware HAC used for L |
| H-M3 | B taken from the reconstruction without bit-equality reconciliation |
| H-M4 | B FAIL / L PASS raises instead of the typed status |
| H-M5 | B FAIL / L PASS classified as PASS |
| H-M6 | `accept` / `confirmation` True for PASS |
| H-M7 | the sub-expression consistency assertion removed |
| H-M8 | `b_diagnostics` not attached / not hashed |

## 7. Stop conditions

Plan §13. In addition:
- STOP if B reconciliation is impossible without modifying D7b;
- STOP if sub-expression realization needs evaluator changes;
- STOP if a synthetic Dataset-v1.2-shaped root cannot be built with test bindings
  alone.
