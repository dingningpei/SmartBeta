# Dataset v1.2 — P-Q v2.5: calibrated terminal-stress scenario TD — design (DRAFT; document-only; nothing frozen)

- **Authority:** `docs/campaign4_m1d_terminal_stress_design_draft.md` (direction frozen
  2026-10-14: B1, d = −0.55; decision D-M1d-3 authorizes this design document).
- **Base:** P-Q v2.4 (`AUTHORITY_V2_4`, base v2.3).
- **Scope:** add a third terminal scenario **TD** beside T0 and TS, for Campaign-4
  ranking and confirmation. T0 and TS are unchanged and stay reported.
- **Mapping source:** a read-only map of the code paths (planner-delegated, 2026-10-14);
  file:line references are cited inline.

## 1. Definition

For each formation and each member with D7b admissibility `ADMISSIBLE_TERMINAL` or
`ADMISSIBLE_TERMINAL_EMPTY` (the exact set TS acts on, per `delisting_bridge`
:1729–1743):

    v^TD = (1 + R)(1 + τ^TD_leg) − 1,   τ^TD = d (economic long), −d (economic short), 0 (middle),   d = −0.55

- **R** is the observed component, as in T0/TS (R = 0 for `ADMISSIBLE_TERMINAL_EMPTY`).
- **All other members:** v^TD = v^T0.
- **The leg** is the B-based economic leg D7b fixes before P-Q runs (:1702–1717). TD
  **inherits TS's keying exactly**: the stressed side follows B's leg even where the L
  sandwich places the member differently. This is the same documented property TS
  already has; it is not a new choice.
- **Monotonicity:** 1 + τ ∈ {0.45, 1.00, 1.55} ≥ 0, so v^TD is monotone non-decreasing
  in R. The v2.1 §9 bound-passing argument holds unchanged.

## 2. Binding constraint: new modules only (differs from v2.4)

- **The v2.4 precedent** (`..._pq_v2_4_fold_firewall.md` §3, path (i)) edited the
  non-pinned P-Q modules with a default-preserving parameter.
- **v2.5 cannot do that.** The Campaign-3 CAMPAIGN record (attempt 4, K-PIT seq 46) now
  binds the SHA-256 of:
  - `campaign3_run.PQ_PINNED_FILES`: pqv2_core, evaluation, dependency, events, sources,
    envelope, labels, assign_simple, assign_sandwich, series;
  - `CAMPAIGN3_CODE_FILES`, which includes `campaign3_screen.py`;
  - `RESERVE_CHAIN_FILES`, which includes `confirm.py`.
- **Editing any of them breaks re-verification** of Campaign-3's records and of any
  future T-C or forward test of `ed358470`.
- **Rule:** v2.5 touches **no** file in `pqv2_core.PINNED_CODE` or in those three lists.
  Everything is new code that imports frozen code read-only.

## 3. Why TD is derivable without modifying frozen code (the algebra)

1. **The B estimand.** For a terminal-admissible member the T0 value equals R exactly:
   `t0_value = (1 + r)(1 + 0) − 1` (:1737). So, member by member,
   `v^TD = (1 + v^T0)(1 + τ^TD_leg) − 1`, with leg and admissibility read from D7b's
   `AccountingRecord`s (:831–856).
   - The TD panel is therefore a deterministic transform of the T0 panel plus the
     records.
   - It is evaluated by D7b's scenario-agnostic `_evaluate_scenario` (:2054).
2. **The L envelope.** The label layer forms scenario bounds as
   `x_lo = (1 + v^X)·lo − 1` (`pqv2_labels._scenario` :232–236, applied at :406–409).
   Substituting v^TD:

       td_lo = (1 + v^T0)(1 + τ)·lo − 1 = (1 + t0_lo)(1 + τ) − 1,   and likewise td_hi

   Multiplication commutes, and 1 + τ ≥ 0 keeps lo ≤ hi. **The TD label envelope is a
   deterministic transform of the T0 label envelope.**
3. **The period L.** `pqv2_envelope._scenario_L` (:835–858) and its Prop-9 check
   (L ≤ LS_b) consume label bounds and B values per scenario.
   - v2.5 calls the frozen functions **read-only**, feeding them TD-transformed label
     envelopes and TD B values in the scenario slot.
   - The exact call seam, either reuse of the TS slot or a direct call of
     `_scenario_L`, is fixed in the implementation spec after a planner read of the
     signatures. **If no seam exists without editing a bound file → HARD STOP** and
     report.
4. **Causes and status.** The causes are those of T0 for the same member/period, since
   TD changes values, not resolvability. The status follows the frozen per-period rules.

## 4. Architecture (new package `smart_beta/dataset_v1/pqv25/`)

| Module | Content |
|---|---|
| `authority.py` | `AUTHORITY_V2_5` (this document's frozen hash, base v2.4, ED/CE lines) and `verify_authority_v2_5`. It delegates the v2.4 → v2.1 chain and the PINNED_CODE hash check to the frozen `pqv2_core.verify_authority` (import-only). It also verifies the three C3-bound file lists are byte-unchanged |
| `td_scenario.py` | B-TD: the T0 panel plus D7b records → the TD panel → `_evaluate_scenario`. It replicates the D7b `_reconcile` invariants that apply to a derived scenario: same NaN mask as T0, same admissible pairs, same grouping identity, same n_obs |
| `td_envelope.py` | the label-envelope transform (§3.2) and the period L-TD through the frozen envelope functions; the Prop-9 check |
| `evaluation.py` | the `build_pqv25_diagnostics(...)` wrapper. It calls the frozen v2.4 `build_pqv2_diagnostics` unchanged, then appends the cells `B-TD`, `L-TD` (event candidates: `-K0/-K1`). Existing cells are passed through byte-identical |
| `series.py` | per-period TD export (the `pqv2_series` pattern), new code |

**Campaign-4 consumers** (specified later, in the Campaign-4 implementation spec, not
here):
- a ranking wrapper: minimum over {T0, TD} × K0/K1 of the L t;
- a confirmation wrapper: B ∧ L under {T0, TD} × K0/K1, where `confirm.py` is frozen.

## 5. Equivalence barrier (binding before any use)

1. **d = −1 reproduces TS.** For every factor in the C1 goldens, the M0-2 six and the C3
   IS top-5, B-TD and L-TD (values, statuses, reasons, causes) at d = −1 are
   **identical** to the frozen B-TS and L-TS. IS only.
2. **d = 0 reproduces T0** in the same way.
3. **Pass-through:** the existing T0/TS cells from `build_pqv25_diagnostics` are
   byte-identical to `build_pqv2_diagnostics`.
4. **Bound files unchanged:** the SHA-256 of every file in `PINNED_CODE` and the three C3
   lists equals its recorded value. This runs as a test, at every barrier.
5. **An independent oracle:** a brute-force member-level TD computation, written
   separately (the PQ22-G/PQ23-G oracle pattern), agrees with the module on synthetic
   and IS data.

Any mismatch → STOP. The comparison is never patched.

## 6. Identity literals (finalized at freeze)

- **New ED line:** appends `terminal_stress_td(d=-0.55)` with version `pq-v2.5-ed-6`.
- **New CE line:** `pq-v2.5-ce-5`.
- **CS:** unchanged.
- **Exact canonical lines:** written into this document at freeze; their hashes enter
  `AUTHORITY_V2_5`.

## 7. Tentative task plan (after this design is frozen; each wave frozen separately)

| Wave | Task | Content |
|---|---|---|
| 1 | PQ25-A | `authority.py` + verification tests |
| 1 | PQ25-G | the independent TD oracle (synthetic) |
| 2 | PQ25-T | `td_scenario.py` + `td_envelope.py` |
| 3 | PQ25-H | `evaluation.py` + `series.py` |
| 4 | PQ25-X | the equivalence barrier (§5), with a real IS run by the planner (K-PIT copy) |

## 8. Decisions needed

| ID | Question | Recommendation |
|---|---|---|
| V25-1 | adopt the new-modules-only architecture (§2) | yes; it is forced by the C3 bindings |
| V25-2 | TD keyed on B's economic leg, as TS is (§1) | yes; identical semantics to TS, only τ differs |
| V25-3 | freeze this design | after your review |
