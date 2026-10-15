# P-Q v2.5 — wave 2: task PQ25-T (B-TD scenario) (FROZEN 2026-10-14; launch authorized)

## Authority

- design `docs/discovery_dataset_v1_2_pq_v2_5_terminal_td_design_draft.md` (frozen);
- erratum E1 `docs/discovery_dataset_v1_2_pq_v2_5_erratum_e1.md`;
- the binding constraints in `worker_tasks/pq_v2_5/pqv25-plan.md`, which apply
  unchanged;
- base: master at freeze (wave 1 merged: `pqv25/authority.py`, the
  `tests/pqv2_oracle/td_v25.py` oracle).

## Re-plan of design §7, recorded here (planner, 2026-10-14)

**Why the design §4 split changes.** A planner read of the frozen orchestration
(`pqv2_evaluation.build_pqv2_diagnostics`, :1427–1884, steps 1–9) shows two things:
- **Step 4 blocks TS-slot substitution.** It reconciles B bit-exactly against D7b's own
  `build_development_diagnostics` (`_reconcile_b`, :1604; B cells from
  `diag_b.economic_ts`, :1735), so TD cannot simply be substituted into the TS slot of
  that call.
- **Step 7 also needs TD labels.** It feeds the labels into `_assignment_for_formation`
  as well as into `compose_series`, so TD labels must replace TS labels from step 7 on.

**The adopted approach** follows the project's P7 amendment A1 precedent: new code
**re-expresses** the needed steps, and **equivalence proves** the re-expression (at
d = −1 the TD outputs must equal the frozen TS outputs byte for byte).

**The waves become:**

| Wave | Task | Content |
|---|---|---|
| 2 | **PQ25-T (this task)** | B-TD |
| 3 | PQ25-H | the L-TD orchestration (re-expressed steps 5–9 with TD labels), the cells and the series |
| 4 | PQ25-X | the equivalence barrier with a real IS run |

The design's `td_envelope.py` is folded into PQ25-H. **No methodology changes.**

## Content: `smart_beta/dataset_v1/pqv25/td_scenario.py`

1. **`reconstruct_td(root, declaration, factor_spec, factor_panel, partition, bindings, *, d=TD_D)`**
   - **Steps it replicates:** `pqv2_evaluation._reconstruct_b` (:654–721) **step for
     step**, using the same frozen D7b helpers, import-only:
     - `_Resolver`, `_resolve_fold`, `_load_d7a`, `_validate_factor_panel`;
     - `_base_panel`, `_winsorize`, `_frozen_groups`;
     - `_observed_panel`, `_admissible_returns`, `_terminal_obs_returns`.
   - **Returns:** `TDReconstruction(t0, ts, td, records, groups, loaded)`, where each
     scenario carries `portfolio, panel, gross, net, value, t_stat, n_obs`, as
     `_ScenarioB` does.
2. **`scenario_panels_td(*, winsorized, groups, r_obs, observed_returns, declaration, loaded, sign, d)`**
   - **What it is:** a re-expression of the D7b `_scenario_panels` loop
     (`delisting_bridge` :1674–1785) that produces **three** value columns (T0, TS, TD)
     in the identical row order.
   - **Leg logic:** the same per-row logic, using the frozen helpers `_admissibility`,
     `_as_bool`, `_as_date` and the frozen constants.
   - **The TD value:**
     - for `ADMISSIBLE_TERMINAL` / `ADMISSIBLE_TERMINAL_EMPTY` members, exactly
       `(1.0 + r) * (1.0 + tau) - 1.0`, with r from `r_obs` (0.0 for `_EMPTY`) and
       `tau = d` (economic long), `-d` (economic short), or `0.0` (middle);
     - for plain `ADMISSIBLE` members, the observed return itself;
     - NaN where D7b gives NaN;
     - the same `D7bConsistencyError` admissibility checks.
3. **In-line equivalence (fail-closed on every call).**
   - `reconstruct_td` **also** calls the frozen `_d7b._scenario_panels`.
   - It asserts that the re-expressed T0 and TS panels equal the frozen ones
     (`pandas.testing.assert_frame_equal(..., check_exact=True)`) and that the records
     are equal.
   - Any difference raises `TDReconstructionError`. That makes every production run
     self-proving.
4. **Evaluation:** `_d7b._evaluate_scenario(panel_td, base)` gives the TD
   `portfolio / value / t_stat / n_obs`.
5. **TD invariants (fail-closed):**
   - TD's finite mask equals T0's;
   - the same (date, stock) rows;
   - the same groups;
   - members outside the two terminal states have TD == T0 bitwise.

## Tests (`tests/test_pqv25_td_scenario.py`; synthetic only)

**Fixtures to reuse:**
- the C3 parity assembly (`tests/fixtures/campaign3_launch/parity.py`);
- the M0-4 terminal fixture (`tests/fixtures/m0/ts/affected.py`), which has terminal
  members in both legs.

**Tests:**
1. **d = −1:** TD panel, gross/net series, value, t_stat and n_obs are **identical** to
   the frozen TS (exact frame equality and exact float equality).
2. **d = 0:** TD is identical to T0 in the same sense.
3. **d = −0.55:** every member value equals the PQ25-G oracle `member_value` **bitwise**.
   Period gross long-short agrees with the oracle within 1e-12, since summation order
   may differ.
4. **In-line equivalence:** a monkeypatched frozen `_scenario_panels` that returns an
   altered TS panel makes `reconstruct_td` raise `TDReconstructionError`. This proves
   the self-check is live.
5. **TD invariants:** an injected inconsistency (for example, a TD value changed on a
   non-terminal member) raises.
6. **Bound files:** `verify_authority_v2_5()` still passes after the module is imported
   and run.
7. **Determinism:** two runs produce identical outputs.

## Owned files (all new)

- `smart_beta/dataset_v1/pqv25/td_scenario.py`
- `tests/test_pqv25_td_scenario.py`
- `tests/fixtures/pqv25/td_scenario/**`

## Launch

- One Pi worker via Herdr, worktree `worktrees/task-pq25-t-td-scenario`.
- Review, merge and barrier follow CLAUDE.md §4, plus `verify_authority_v2_5()`.

## Freeze (user, 2026-10-14: "确认， 授权")

This spec is frozen, including the wave re-plan (T → H → X). The PQ25-T Pi launch is authorized.
