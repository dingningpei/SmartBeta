# Dataset v1.2 — P-Q v2 implementation plan (SPECIFICATION FROZEN; IMPLEMENTATION NOT AUTHORIZED)

- **Status:** specification only. Authorized by "P-Q v2 IMPLEMENTATION-SPEC DESIGN", on
  master `0777a109d58b6682448f914098674c470f5c7f67`. Worker launch, worktrees and
  implementation each need a separate authorization.
- **Methodology authority (frozen; not redesigned here):**
  `docs/discovery_dataset_v1_2_pq_v2_methodology.md`.
  - Methodology identity (document SHA-256):
    `a31ed9c49cd3a03208ae28539a8b5f5531916e31fac32bbd60e2af07746fa56d`
  - CS-ID `432de09746bbc4c16c332d662c24d6d7369787d55e8b704e3a521494b73bdae2`
  - ED-ID `e979924ee4efc365155d8a79089fb940a28ebede7099f67373f0d654bd479aeb`
  - CE-ID `605fec93812d4165b6bf455a9fb453cd7c0c6c4a9f5a2a0fc418b53b0d0dbaaf`
  - "M§n" below refers to section n of that document.
- **Barrier specification:** `docs/discovery_dataset_v1_2_pq_v2_integration_barrier.md`.
- **Worker tasks:** `worker_tasks/pq_v2/task-pq2-*.md`.
- **History:** P-Q v1 and historical Campaign-1 (permanently 7 NOT_ASSESSED) are not
  touched. P-Q v2 is prospective.

---

## 1. Current-architecture map (read on master `0777a10`)

| Component | File | Role for v2 | Classification |
|---|---|---|---|
| P-Q v1 overlay (flags, primitive/input/label states, identity) | `smart_beta/dataset_v1/quarantine.py` | `Overlay.flags` (D5 `CORP_ACTION_SUSPECT` with trusted prev/next sessions) is reused **read-only** as the M§2 flag source; `verify_dataset_v1_2_identity` is reused for dataset binding | **UNCHANGED** |
| P-Q v1 A/B estimator + classification | `smart_beta/dataset_v1/pq_evaluation.py` | historical v1 only; v2 never calls `classify_pq` and never builds A cells | **UNCHANGED** |
| O1 structural residual | `smart_beta/dataset_v1/pq_reporting.py` | v1 only | **UNCHANGED** |
| Gap-aware HAC | `smart_beta/engines/gap_hac.py` | **not used by v2** (§6.4) | **UNCHANGED** |
| D7a returns/labels | `smart_beta/dataset_v1/returns.py` | `labels.parquet` (state, endpoint, `observed_segment_end`, terminal); `daily_returns.parquet` (`ca_event_count` cross-check); its bindings resolve D3 `ca_events.parquet` | **UNCHANGED** (read-only) |
| D7b bridge | `smart_beta/dataset_v1/delisting_bridge.py` | B unchanged (`build_development_diagnostics`). The private helpers (`_Resolver`, `_resolve_fold`, `_load_d7a`, `_validate_factor_panel`, `_base_panel`, `_winsorize`, `_frozen_groups`, `_observed_panel`, `_admissible_returns`, `_terminal_obs_returns`, `_scenario_panels`, `_evaluate_scenario`) are reused read-only under a pinned hash, as PQB `_reconstruct_b` already does. `content_hash`/`canonical_json` are reused | **UNCHANGED** |
| D7c trusted inputs | `smart_beta/dataset_v1/trusted_inputs.py` | TRI/ret/dv TrustedInputs over the masked full cross-section. The observed m_s = 1 + `daily_total_return` at source session s | **UNCHANGED** |
| Phase-6 evaluator / transforms | `smart_beta/spec/*.py` | evaluates the factor and the certified sub-expressions (M, V) for H4/H5. Semantics: standardize ddof = 0, rank `average`, linear-quantile winsorize | **UNCHANGED** |
| Phase-7 portfolio / sort / metrics | `smart_beta/engines/portfolio_sort.py`, `smart_beta/evaluation/{portfolio,engine,metrics}.py` | grouping `rank(method="first")`, turnover convention, `long_short_mean_tstat` (Newey-West over finite values) | **UNCHANGED** |
| Campaign-1 executor / runner / ledger | `smart_beta/campaign/{campaign1,runner,ledger}.py` | Campaign-1 is permanently P-Q v1. **No v2 dispatch is added** (§9) | **UNCHANGED** |
| Diagnostics store (F-2) | `smart_beta/campaign/diagnostics_store.py` | generic `persist(obj, dir)` over `obj.to_dict()` / `obj.diagnostics_hash`; v2 diagnostics are made compatible with it | **UNCHANGED** (reused) |
| v2 core types, identity, S2, combinatorics | `smart_beta/dataset_v1/pqv2_core.py` | — | **NEW** (PQ2-A) |
| v2 source set + label envelope | `smart_beta/dataset_v1/pqv2_sources.py`, `pqv2_labels.py` | — | **NEW** (PQ2-B) |
| v2 exact dependency + input uncertainty | `smart_beta/dataset_v1/pqv2_dependency.py` | — | **NEW** (PQ2-C) |
| v2 simple-class assignment | `smart_beta/dataset_v1/pqv2_assign_simple.py` | — | **NEW** (PQ2-D) |
| v2 H4/H5 sandwich | `smart_beta/dataset_v1/pqv2_assign_sandwich.py` | — | **NEW** (PQ2-E) |
| v2 envelope composition / turnover | `smart_beta/dataset_v1/pqv2_envelope.py` | — | **NEW** (PQ2-F) |
| v2 evaluation entry, statistics, classification, diagnostics | `smart_beta/dataset_v1/pqv2_evaluation.py` | — | **NEW** (PQ2-H) |
| Independent brute-force oracle | `tests/pqv2_oracle/` | — | **NEW** (PQ2-G, test-only) |

**Additive only.** **No existing production file is modified.** No dataset artifact is built, so there is
no Dataset v1.3. P-Q v2 is computed per evaluation from the admitted Dataset-v1.2
artifacts.

## 2. Versioning architecture

1. **Separate modules, no dispatch inside v1.** v1 code paths are unchanged and stay
   reproducible byte for byte. v2 is reachable only through
   `pqv2_evaluation.build_pqv2_diagnostics`.
2. **Authority binding (PQ2-A `verify_authority`)**, run at every v2 entry before any
   data read:
   - (a) the SHA-256 of `docs/discovery_dataset_v1_2_pq_v2_methodology.md` equals
     `METHODOLOGY_SHA256`;
   - (b) the three canonical identity lines are extracted from that document (the
     fenced block following each of the headers "Correction-set identity (CS-ID)",
     "Exact-dependency identity (ED-ID)" and "Certified-expression identity (CE-ID)"),
     each is hashed with SHA-256, and each hash equals its constant;
   - (c) the pinned hashes of every reused production file (§5) match.
   - **Any mismatch raises `PQv2BindingError`.** It fails closed and nothing is
     evaluated.
3. **Declaration binding.** `PQv2EvaluationDeclaration` carries the four identities.
   If they differ from the constants, the result is typed blank diagnostics with reason
   `PQV2_IDENTITY_MISMATCH`, produced before any data read or evaluation. This is the
   same pattern as v1 `_declaration_reason`.
4. **Content addressing.** Every diagnostics object records the four identities and
   `pqv2_code_sha256` (a hash over the v2 module files), and is content-addressed with
   D7b `content_hash`.
5. **No backward compatibility that rewrites artifacts.** v2 never reads, migrates or
   re-renders a v1 or Campaign-1 artifact.

## 3. Task DAG and waves

| Task | Purpose | Depends on | Wave |
|---|---|---|---|
| **PQ2-A** | core types, identity binding, S2, positive-interval arithmetic, typed reasons, leg-extremum combinatorics, winsorization block check | — | 1 |
| **PQ2-G** | independent brute-force oracle (test-only; methodology + frozen production semantics; **imports no `pqv2_*` module**) | — | 1 |
| **PQ2-B** | real-data source set (flags, m_s, MISDATED candidates, firewall) + Candidate-E label envelope | A (G for tests) | 2 |
| **PQ2-C** | certified dependency engine (log-linear TRI, lag, rolling, arithmetic, cross-section), CE classifier, per-cell input uncertainty (A_t, M and V intervals) | A (G for tests) | 2 |
| **PQ2-D** | simple-class assignment (H1–H3, H6–H7) + simple D_t | A, C (G) | 3 |
| **PQ2-E** | H4/H5 score intervals, σ support, certain/possible sandwich, ambiguous-band D_t | A, C (G) | 3 |
| **PQ2-F** | per-period L composition: leg extrema, T0/TS, TO^hi, cost, sign, metric gate, period NOT_ASSESSED | A, B, D, E (G) | 4 |
| **PQ2-H** | evaluation entry: bindings, B reuse + reconciliation, sub-expression realization, L statistics, classification, diagnostics, persistence compatibility | A–F (G) | 5 |
| **Planner** | integration barriers PQ2-I1…I15, then (separately authorized) PQ2-ADM | all | 6 |

**Wave contents:**
- **Wave 1:** A and G, in parallel.
- **Wave 2:** B and C, in parallel.
- **Wave 3:** D and E, in parallel.
- **Wave 4:** F.
- **Wave 5:** H.

**Merge order:** each wave's tasks are merged (reviewed exact SHAs, `--no-ff`) before
the next wave's worktrees are created **from the post-merge master**.
- **Why G comes first:** G merges in wave 1 so that **every analytic task's tests run
  against an oracle written before, and independently of, the analytic code**.

## 4. Ownership map (no production or test file has two owners)

| Task | Production files | Test / fixture files |
|---|---|---|
| PQ2-A | `smart_beta/dataset_v1/pqv2_core.py` | `tests/test_pqv2_core.py`, `tests/fixtures/pqv2/core/**` |
| PQ2-G | — | `tests/pqv2_oracle/__init__.py`, `tests/pqv2_oracle/oracle.py`, `tests/test_pqv2_oracle_self.py`, `tests/fixtures/pqv2/oracle/**` |
| PQ2-B | `smart_beta/dataset_v1/pqv2_sources.py`, `smart_beta/dataset_v1/pqv2_labels.py` | `tests/test_pqv2_sources.py`, `tests/test_pqv2_labels.py`, `tests/fixtures/pqv2/labels/**` |
| PQ2-C | `smart_beta/dataset_v1/pqv2_dependency.py` | `tests/test_pqv2_dependency.py`, `tests/fixtures/pqv2/dependency/**` |
| PQ2-D | `smart_beta/dataset_v1/pqv2_assign_simple.py` | `tests/test_pqv2_assign_simple.py`, `tests/fixtures/pqv2/simple/**` |
| PQ2-E | `smart_beta/dataset_v1/pqv2_assign_sandwich.py` | `tests/test_pqv2_assign_sandwich.py`, `tests/fixtures/pqv2/sandwich/**` |
| PQ2-F | `smart_beta/dataset_v1/pqv2_envelope.py` | `tests/test_pqv2_envelope.py`, `tests/fixtures/pqv2/envelope/**` |
| PQ2-H | `smart_beta/dataset_v1/pqv2_evaluation.py` | `tests/test_pqv2_evaluation.py`, `tests/fixtures/pqv2/evaluation/**` |

**Forbidden for every task:**
- all existing production files;
- `docs/**`, `worker_tasks/**`;
- `data_store/**` and any dataset artifact;
- `knowledge_pit/**`;
- `tests/` files not listed for that task.

A need outside a task's ownership is a **STOP**.

## 5. Pinned reused code (PQ2-A constant `PINNED_CODE`; verified at entry)

Reused files are pinned by their SHA-256 at the base of wave 1:
- `smart_beta/dataset_v1/delisting_bridge.py`
- `quarantine.py`, `returns.py`, `trusted_inputs.py`
- `smart_beta/spec/transforms.py`, `smart_beta/spec/evaluator.py`
- `smart_beta/engines/portfolio_sort.py`
- `smart_beta/evaluation/portfolio.py`, `smart_beta/evaluation/engine.py`,
  `smart_beta/evaluation/metrics.py`
- `smart_beta/campaign/diagnostics_store.py`

PQ2-A records the values from the base master. The planner verifies them at review.

## 6. Production interfaces and types (normative summary; full detail in the task specs)

### 6.1 Core (PQ2-A, `pqv2_core.py`)

**Constants:**
- `METHODOLOGY_SHA256`, `CS_ID`, `ED_ID`, `CE_ID`, `PINNED_CODE`;
- `S2_LO = 0.5`, `S2_HI = 1.5`;
- `DEVELOPMENT_END = 2023-06-30`, `RESERVE_START = 2023-07-03`;
- `MISDATED_WINDOW = 5`;
- `MISDATED_ACTION_TYPES = ("split", "dividend")`.

**Errors:** `PQv2Error` ⊃ `PQv2BindingError`, `PQv2InputError`, `PQv2InvariantError`.

**`PositiveInterval(lo, hi)`:**
- requires 0 < lo ≤ hi < ∞, all finite;
- operations `mul`, `inv`, `hull`, `contains`, `is_unit`.

**`s2_absolute(m_obs)`** = [min(0.5, m), max(1.5, m)]; **`s2_relative(m_obs)`** = that
interval / m. If m_obs is non-finite or ≤ 0, raise `PQv2InputError`.

**Typed status vocabularies** (string constants, exhaustive):
- `LabelStatus`: `ENVELOPE`, `EMPTY_NO_DEPENDENCY`, `FACTUAL_MISSING`,
  `UNKNOWN_RESERVE`.
- `NotAssessedReason`:
  - binding and scope: `PQV2_IDENTITY_MISMATCH`, `PQV2_DATASET_IDENTITY_MISMATCH`,
    `PQV2_OVERLAY_MISMATCH`, `PQV2_EXPRESSION_NOT_CERTIFIED`,
    `PQV2_SIGN_NOT_CERTIFIED`, `PQV2_METRIC_NOT_CERTIFIED`;
  - period preconditions: `PQV2_RESERVE_UNKNOWN`, `PQV2_SIGMA_SUPPORT`,
    `PQV2_WINSOR_BLOCK`, `PQV2_EMPTY_LEG_CONFIGURATION`;
  - statistics: `PQV2_L_PERIOD_NOT_ASSESSED`, `PQV2_B_STATISTIC_UNAVAILABLE`,
    `PQV2_L_STATISTIC_UNAVAILABLE`.
- `AssignmentRule`: `SIMPLE_EXACT`, `SANDWICH_CONSERVATIVE_SUPERSET`.
- `ClassificationStatus`: `CORRECTION_SENSITIVITY_SCREEN_PASS`,
  `QUARANTINE_SENSITIVE`, `INVARIANT_VIOLATION_FAIL_CLOSED`,
  `DEVELOPMENT_SCREEN_FAIL`, `NOT_ASSESSED`.

**Dataclasses (frozen):**
- `UncertainTransition(security_key, s, prev_s, next_s, m_obs, rel: PositiveInterval)`;
- `MisdatedCandidate(security_key, s, d, phi_d)`;
- `SourceSet(transitions_by_key, candidates_by_key, unknown_by_key)`.

**`leg_min_mean(forced, pool, n_take)` / `leg_max_mean(...)`:** the exact extremum of
the mean over finite values of `forced ∪ chosen`, over all `chosen ⊆ pool` with
|chosen| = n_take.
- If **any** admissible choice has no finite value, it returns `None` and the reason is
  `PQV2_EMPTY_LEG_CONFIGURATION` (the M§13 precondition).
- Method: enumerate a = the number of finite pool values chosen. For a min, take the a
  lowest finite pool values and fill the rest with NaN members. O(k²).

**`winsor_block_ok(intervals, q, s_low, s_high)`** implements the M§11 deterministic
sufficient check, with h = q·(n−1):
- κ_lo = the (⌊h⌋+2)-th smallest upper bound; the low side passes if
  #{v^lo ≤ κ_lo} ≤ s_low;
- the high side is symmetric with κ_hi and s_high;
- an interval may be (−∞, +∞) for a simple-class uncertain member.

### 6.2 Sources and labels (PQ2-B)

**`load_source_set(root, *, overlay, d7a_bindings, ti_inputs, sessions) -> SourceSet`**
(`pqv2_sources.py`):
- **Flags:** from `overlay.flags` only.
- **m_s:** 1 + the D7c `daily_total_return` at source session s. If s is not a VALID
  return, raise `PQv2InputError`.
- **Candidates (M§7):**
  - read from the D7a-bound D3 `ca_events.parquet`;
  - action ∈ {split, dividend}; same D4 key, inside its D4 interval, with
    effective_date ≤ `DEVELOPMENT_END`;
  - at a trusted session d ≠ s with |pos(d) − pos(s)| ≤ 5;
  - φ_d = the product of `adjustment_factor` over the events on d (the same rule as
    `compute_adjusted_returns`);
  - cross-check: the recorded event count on d equals D7a `ca_event_count` where D7a has
    the key and session; a mismatch raises.
- **Firewall:** if pos(s) + 5 > pos(DEVELOPMENT_END), the flag's candidate set is
  undeterminable, so s goes into `unknown_by_key` (M§8 firewall rule). Rows dated
  ≥ `RESERVE_START` are never read. A row after `DEVELOPMENT_END` in any input raises
  `PQv2InputError`.

**`label_envelope(label_record, transitions, candidates, unknown) -> LabelEnvelope`**
(`pqv2_labels.py`) implements M§6, M§8 and M§9:
- **Status:**
  - COMPLETE (ordinary or terminal), with dependency window [f, next(e′)] → `ENVELOPE`;
  - `EMPTY_OBSERVED_PATH_IDENTITY` → `EMPTY_NO_DEPENDENCY` (factor 1, never
    quarantined);
  - INCOMPLETE or not admissible → `FACTUAL_MISSING` (NaN stays NaN);
  - e′ = `DEVELOPMENT_END`, or a dependency on an `unknown` flag → `UNKNOWN_RESERVE`.
- **Factors:**
  - s = f gives Q_s⁻¹; s ∈ (f, e′] gives Q_s; s = next(e′) gives Q_s⁻¹;
  - each MISDATED candidate whose pair {s, d} has exactly one member in (f, e′] adds a
    discrete alternative φ_d^{+1} (s inside) or φ_d^{−1} (d inside);
  - per flag, the factor set is hull(Λ_s ∪ alternatives);
  - envelope multipliers `lo` = ∏ min and `hi` = ∏ max.
- **Scenario values:** v^X_lo = (1 + v^X_obs)·lo − 1 and v^X_hi = (1 + v^X_obs)·hi − 1,
  where v^X_obs is the D7b record value (T0 or TS). If v^X_obs is NaN the result is NaN.
  If 1 + v^X_obs = 0 (TS long terminal), the result is −1 exactly.

### 6.3 Dependency (PQ2-C), assignment (PQ2-D, PQ2-E), composition (PQ2-F)

**`certify(factor_spec) -> CertifiedClass | NotAssessed`** (PQ2-C): matches by the
SHA-256 of the canonical JSON of `(expression, sign, input roles)` against the
**seven** frozen Campaign-1 expressions (derived from `docs/campaign1_factorspecs.json`
`f93a218d…`).
- C1-H1–H3 and C1-H6–H7 → `SIMPLE`; C1-H4 and C1-H5 → `STD_X_RANK`.
- Anything else → `PQV2_EXPRESSION_NOT_CERTIFIED`. **CE is enumerative** (CE-ID lists
  hypotheses).
- Sign ≠ +1 → `PQV2_SIGN_NOT_CERTIFIED`.

**`dependency_windows(expression)`** (PQ2-C): from the node rules in M§10:
- `tri` ratios give a log-linear coefficient support (y, x];
- `lag` shifts;
- `rolling` gives numerical window unions;
- arithmetic with a constant keeps the support; other arithmetic gives a union;
- `cross_section` gives a whole-row marker.

It must reproduce the M§10 Campaign-1 table exactly. An unsupported node gives
`PQV2_EXPRESSION_NOT_CERTIFIED`.

**`input_uncertainty(cls, windows, source_set, formation)`** (PQ2-C) returns, per
cross-section cell:
- **simple class:** an `uncertain: bool` (A_t), or `UNKNOWN`;
- **STD_X_RANK:**
  - an M interval: M_obs times the product of per-flag hulls over the Prop.-2
    positions (s = y → Q⁻¹; s ∈ (y, x] → Q; s = next(x) → Q⁻¹), with MISDATED
    alternatives tested for containment in (y, x];
  - a V interval: dv cells u ∈ {prev s, s} receive κ ∈ Q_s⁻¹ and Q_s respectively;
    several flags on one cell multiply; closed-form linear-fractional extremes
    (short-window cells at hi and long-only cells at lo for the max, and the reverse for
    the min);
  - or `UNKNOWN`.
- **Exactness of the hulls:** taking hulls of the finite MISDATED unions is **exact** for
  the score extremes, because g is linear in M and in μ separately. Taking the product
  over flags is a superset of the global one-misdating reading. This is disclosed.

**`simple_assignment(...)`** (PQ2-D) implements M§11.1 Prop. 4:
- clean order fixed (rank-first ties by row order);
- long candidates TopClean(s_L − j) ∪ S, with S ⊆ A_t;
- `long_lo` / `short_hi` via `leg_min_mean` / `leg_max_mean` per j, minimized or
  maximized over j;
- D_t = min(|U_t|, G·|A_t|).

**`sandwich_assignment(...)`** (PQ2-E) implements M§11.2:
- σ support (≥ 2 distinct finite clean M values over the full cross-section);
- μ interval;
- average-rank insertion bounds;
- the score interval [g^lo, g^hi] as min/max of R(M − μ) over endpoint combinations;
- C and P per the frozen formulas;
- positions r_min = 1 + #{j ≠ i : g_j^hi < g_i^lo} and
  r_max = n − #{j ≠ i : g_j^lo > g_i^hi};
- possible groups = the buckets over [r_min, r_max];
- D_t = #{i : possible groups ≠ {B group}};
- the tagged rule is `SANDWICH_CONSERVATIVE_SUPERSET`, never exact.
- **Invariant:** B's group lies in the possible set; otherwise raise
  `PQv2InvariantError`.

**Reserve UNKNOWN in factor inputs:** if any relevant cell is `UNKNOWN`, the period is
`PQV2_RESERVE_UNKNOWN`. For the simple class the relevant cells are the universe
members; for the sandwich class they are the full cross-section, because suspects
outside the universe still move μ and the ranks. This is checked in PQ2-D and PQ2-E.

**`compose_period(...)`** (PQ2-F) implements M§12 and M§13:
- winsorization block check, then the leg extrema, then
  TO^hi = min(1, (changed^B + D_t + D_{t−1}) / |U_{t−1} ∪ U_t|);
- cost^hi = c·TO^hi, applied only for the NET metric (`TURNOVER_COST_ADJUSTED`); the
  GROSS metric (`LONG_SHORT`) carries no cost term; any other metric gives
  `PQV2_METRIC_NOT_CERTIFIED`;
- sign −1 gives `PQV2_SIGN_NOT_CERTIFIED`;
- changed^B and the first-period convention mirror `evaluation/portfolio` exactly (a
  reconciliation test);
- L_t ≤ LS^B_t is asserted as an invariant, because B ∈ Ω;
- an `UNKNOWN_RESERVE` label of a member in the possible long or short set gives
  `PQV2_RESERVE_UNKNOWN` for that period.

### 6.4 Evaluation, statistics and classification (PQ2-H)

`build_pqv2_diagnostics(root, declaration, factor_spec, factor_panel, partition, *,
overlay, inputs, …) -> PQv2Diagnostics` (the same shape as the v1 entry):

1. Bind identities and run the authority checks. On a declaration mismatch, return
   typed blank diagnostics.
2. Verify the dataset identity and the overlay.
3. Certify the expression, sign and metric.
4. B is exactly D7b `build_development_diagnostics`. Reconstruct the D7b internals as
   PQB does and reconcile them bit-exactly.
5. Load the source set restricted to the fold.
6. For STD_X_RANK, realize the sub-expressions M and V through the Phase-6 evaluator
   over the full masked cross-section. Assert
   |F_i − R_i(M_i − μ)/σ| ≤ 1e-12·max(1, |F_i|) for every finite cell, otherwise raise.
7. Run the per-formation dependency, assignment, labels and composition steps.
8. **Compute the L statistic.** Apply exactly B's estimator for the metric (Phase-7
   `long_short_mean_tstat`, Newey-West over finite periods) to the L series **on B's
   finite-period index**.
   - If **any** of B's finite periods has L_t NOT_ASSESSED, the L statistic is
     unavailable (`PQV2_L_PERIOD_NOT_ASSESSED`).
   - **Derivation (not a new choice):** Prop. 9 is a per-period inequality, so L is "the
     conservative result" (M§14) only on B's own period set. A statistic over a subset
     of periods is not an envelope of B, and `gap_hac` is not used.
9. **Classification.** `classify_pqv2(diag, screens) -> PQv2Record` applies the M§14
   table **per scenario X ∈ {T0, TS}**. Screen outcomes are inputs (a Campaign defines
   them). Aggregation across scenarios belongs to the Campaign precommitment, not P-Q v2.
   - No status is ACCEPT or a confirmation.
   - `INVARIANT_VIOLATION_FAIL_CLOSED` is a **typed classification, not an exception**.
     Because the t-statistic is not monotone under L ≤ B, B FAIL with L PASS can occur
     legitimately (lower variance). Its frozen consequence is fail-closed.
10. **`PQv2Diagnostics`:**
    - contents: cells B-T0, B-TS, L-T0, L-TS (value, t_stat, n_obs, status, reason);
      per-period summaries (the M§14 / §7 list); identities; certification class;
      assignment rule; exposure counts; and the D7b `b_diagnostics` attached;
    - `diagnostics_hash = content_hash(to_dict())`;
    - compatible with `diagnostics_store.persist` **unchanged**.

## 7. Persisted v2 diagnostics (reconstructs the report without recomputation)

**Per evaluation:**
- the four identities and `pqv2_code_sha256`;
- the certification class and `AssignmentRule`;
- the assessment status and reason;
- the B and L cells (value, t, n_obs, status) for T0 and TS;
- the support used (n finite periods).

**Per period:**
- `L_t^{T0}`, `L_t^{TS}`, `LS^B_t`;
- the period status and reason;
- `TO^B`, `TO^hi`, `D_t`;
- simple class: |A_t|; sandwich: |C_long|, |P_long|, |C_short|, |P_short|;
- the winsorization block outcome and σ support;
- counts of label envelopes by status;
- the number of uncertain transitions by position class (formation endpoint, interior,
  ending endpoint, numerical);
- the number of MISDATED alternatives used;
- the number of reserve-UNKNOWN members.

**Not persisted:** per-member detail, to bound the size. The persisted per-period arrays
are content-hashed inside the payload.

## 8. Mutation plan (frozen targets; owner in parentheses)

Every non-equivalent mutation must be killed. Equivalent ones need an individual
justification approved at planner review.

| ID | Mutation | Owner |
|---|---|---|
| V2-M01 | omit the formation-day (s = f) label dependency | B |
| V2-M02 | no interior cancellation (an interior flag also contributes Q⁻¹ at next(s)) | B |
| V2-M03 | omit the MISDATED paired event | B (sources), C (factor windows) |
| V2-M04 | mark every ±5 session uncertain instead of only recorded events | B |
| V2-M05 | S1 instead of S2 in `PositiveInterval.contains` / the set constructor (expected killable via `contains`; the extremes are identical by M§3) | A |
| V2-M06 | loader reads rows ≥ `RESERVE_START` / the firewall filter is removed | B |
| V2-M07 | UNKNOWN treated as clean (e′ = DEVELOPMENT_END) | B |
| V2-M08 | TRI propagated from the segment anchor (v1 absorption) | C |
| V2-M09 | wrong TRI window endpoint (off by one: (f−252, f−21]) | C |
| V2-M10 | ddof = 1 in the σ-support check (**expected equivalent**: σ > 0 ⟺ ≥ 2 distinct values for any ddof when n ≥ 2, and both fail at n = 1; must be justified). The oracle's standardize ddof mutation is killed by the G cross-check against `transforms` | E (+G) |
| V2-M11 | rank method `min` or `first` instead of `average` in the insertion bounds | E |
| V2-M12 | suspect volume-rank movement ignored (R fixed at observed) | E |
| V2-M13 | clean-clean crossing ignored (μ fixed at observed) | E |
| V2-M14 | sandwich tagged `SIMPLE_EXACT`, or P replaced by the observed membership | E |
| V2-M15 | tie-at-cap omitted (⌊h⌋+1 instead of ⌊h⌋+2, or clipped count only) | A |
| V2-M16 | old G·k turnover bound used for the sandwich class | F |
| V2-M17 | sign −1 evaluated instead of `PQV2_SIGN_NOT_CERTIFIED` | F, H |
| V2-M18 | typed NaN converted to a bounded value (FACTUAL_MISSING enveloped) | B, F |
| V2-M19 | identity or hash binding check bypassed | A, H |

**Additional task-local mutations** are listed in each task spec.

**Fixture format:** the mutation sets are `tests/fixtures/pqv2/<task>/mutations.json`,
in the `scratchpad/pq_mutrun.py` format with a CONTROL. The planner re-runs them at
review.

## 9. Campaign integration boundary (prospective; not implemented here)

- **Campaign-1:** stays bound to P-Q v1. **No change** to `campaign1.py`, `runner.py` or
  `ledger.py`. **No automatic migration.**
- **How a future Campaign requests P-Q v2:** its own frozen precommitment and executor
  spec (a separate authorization) declare `pq_methodology = "P-Q-v2"` plus
  `METHODOLOGY_SHA256`, `CS_ID`, `ED_ID` and `CE_ID`. The executor:
  - builds a `PQv2EvaluationDeclaration`;
  - calls `pqv2_evaluation.build_pqv2_diagnostics` and `classify_pqv2`;
  - persists through `diagnostics_store.persist`;
  - defines its own screens, minimum periods and scenario aggregation.
- **CE consequence (disclosed):** only the seven frozen Campaign-1 expressions are
  certified. A new expression evaluates to `PQV2_EXPRESSION_NOT_CERTIFIED` until a
  frozen CE amendment certifies it.

## 10. Frozen-methodology coverage matrix

| Methodology clause | Owner | Test anchor |
|---|---|---|
| M§1 interpretation; M§18 non-claims and claim language | H (diagnostics claim fields), barrier I9 | `test_pqv2_evaluation` claim-language test |
| M§2 definitions (grouping, legs, turnover, multiplier) | A, D, E, F | reconciliation against D7b and portfolio |
| M§3 S2, Q_s, no K | A | core tests + V2-M05 |
| M§4 identity premise (non-claim recorded) | H | diagnostics non-claim field |
| M§5 exact-dependency invariant | B, C | oracle equality and containment |
| M§6 Prop. 1, s = f, EMPTY | B | V2-M01, M02 |
| M§7 MISDATED | B (sources), B and C (window effects) | V2-M03, M04 |
| M§8 Prop. 3 envelope, firewall | B | oracle equality, V2-M06, M07 |
| M§9 missingness, T0/TS order | B, F | V2-M18 |
| M§10 Prop. 2, the table, generic rules, primitive dependency, DV non-claim | C | frozen-table fixture, V2-M08, M09 |
| M§11 winsorization block check | A (check), F (application) | V2-M15 |
| M§11.1 Prop. 4 and simple D_t | D | oracle containment |
| M§11.2 standardization, intervals, ranks, Prop. 5/6, D_t | E | oracle containment, V2-M10–M14 |
| M§12 Prop. 7/8 | A (extremum), D, E, F | oracle min-LS and max-TO |
| M§13 L, sign, Prop. 9, preconditions | F, H | V2-M16, M17; L ≤ B invariant |
| M§14 classification | H | full-table tests |
| M§15 certification boundary, identities | A (identities), C (`certify`) | V2-M19, uncertified → NOT_ASSESSED |
| M§16 oracle requirements | G (oracle), A–F (comparisons) | barrier I9 case census |
| M§17 migration | H, planner | barriers I2, I13, I14 |

**Theorem preconditions and their fail-closed tests:**

| Precondition | Fail-closed test |
|---|---|
| winsorization block | a fixture where the check fails → `PQV2_WINSOR_BLOCK` |
| σ support | all clean M equal → `PQV2_SIGMA_SUPPORT` |
| empty-leg configuration | → `PQV2_EMPTY_LEG_CONFIGURATION` |
| reserve | → `PQV2_RESERVE_UNKNOWN` |
| sign −1 | → `PQV2_SIGN_NOT_CERTIFIED` |
| uncertified expression | → `PQV2_EXPRESSION_NOT_CERTIFIED` |
| uncertified metric | → `PQV2_METRIC_NOT_CERTIFIED` |
| identity mismatch | → typed blank / `PQv2BindingError` |

**Declared assumptions and where each is represented explicitly:**
- S2: A constants;
- MISDATED, one-at-a-time: B/C, plus the disclosed superset;
- D4 identity: H non-claim field;
- DV model: C, where the CA branch never touches dv;
- Cartesian M/V: E rule tag.

## 11. Resource and performance review

**Expensive paths:**
1. **Sub-expression realization for H4/H5** (M and V over the full masked cross-section,
   about 2138 × 10353). This is the same order as one factor realization, plus one more.
   It is done **once per hypothesis per evaluation and shared across folds**.
2. **Score intervals and sandwich:** O(N_cs + N_U log N_U) per formation, with a
   few-thousand cross-section and 500 universe members. Negligible.
3. **MISDATED:** at most 10 candidates per flag; hulls mean nothing is enumerated
   combinatorially.
4. **Labels:** O(1) amortized per (member, formation) via flags indexed per key and
   sorted (`bisect`).
5. **Diagnostics:** per-period arrays (about 100 periods × about 20 fields × 2
   scenarios). Small.

**Determinism:**
- no randomness;
- every iteration is over sorted keys;
- a canonical float repr through D7b `canonical_json`;
- a bound on running time is measured at admission (PQ2-ADM).

**No optimization may weaken the methodology.** Any approximation is a STOP.

## 12. Security, environment and Pi policy (for the later authorized launch)

- **Workers:** Pi through Herdr only (`deepseek` / `deepseek-v4-flash`), one worktree
  per task, branch `pq2/task-pq2-<x>`, worktree `worktrees/task-pq2-<x>`.
- **Test invocations:** every run scrubs TIINGO_API_KEY, TUSHARE_PROXY_TOKEN,
  TUSHARE_BASIC_PROXY_TOKEN, TUSHARE_API_TOKEN, DEEPSEEK_API_KEY, ANTHROPIC_API_KEY,
  ANTHROPIC_AUTH_TOKEN and OPENAI_API_KEY process-locally; no network (netguard).
- **Data access:**
  - no real-data reads in worker tests; **synthetic fixtures only**;
  - no reserve, holdout or `dsc_evidence` access; no `raw/` or `reserve_sealed/`;
  - no K-PIT access.

## 13. Stop conditions (any → STOP and report)

- methodology ambiguity, or evidence contradicting the frozen methodology;
- a needed file outside the task's ownership;
- a pinned-hash mismatch at base;
- the oracle disagreeing with a frozen proposition, which is a methodology or
  specification defect (not an implementation defect);
- an analytic module being unable to pass without importing or copying oracle code;
- any optimization that changes a bound;
- `tests/pqv2_oracle` not importable without a configuration change;
- a secret or credential risk;
- a merge conflict;
- an unexplained regression.

## 14. Planner pre-freeze review (performed before commit)

| Check | Result |
|---|---|
| Every frozen clause has an owner | §10 |
| Every theorem precondition has a fail-closed test | §10 |
| Declared assumptions are explicit | §10 |
| Unsupported expression, sign −1, reserve fail closed | §6.3–6.4, V2-M17, V2-M06/M07 |
| P-Q v1 reproducible | no v1 file in any ownership list; barrier I2 |
| Campaign-1 untouched | barrier I14 |
| Oracle independence | G merges first; it imports no `pqv2_*`; it recomputes from closes/CA/volumes through its own numpy implementations; it is cross-checked against production transforms only for semantic equality (G self-test) |
| No two owners per file | §4 |

**Interpretations recorded (derived from frozen text; flagged to the user):**
- (i) the L statistic is restricted to B's period set (§6.4 step 8);
- (ii) MISDATED candidates within 5 sessions of `DEVELOPMENT_END` give UNKNOWN (M§8
  firewall);
- (iii) CE is enumerative, by expression hash (M§15 CE-ID);
- (iv) scenario aggregation is delegated to the Campaign.
