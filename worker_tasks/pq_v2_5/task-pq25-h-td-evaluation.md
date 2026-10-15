# P-Q v2.5 — wave 3: task PQ25-H (L-TD orchestration, TD cells, TD series) (FROZEN 2026-10-14; launch authorized)

## Authority

- design (frozen) and erratum E1;
- the binding constraints in `worker_tasks/pq_v2_5/pqv25-plan.md`;
- the PQ25-T re-plan (`task-pq25-t-td-scenario.md`);
- base: master at freeze, which includes `pqv25/authority.py`, `pqv25/td_scenario.py` and
  the oracle.

## Frozen code read by the planner (the anchors this spec is written against)

| Item | Location | What it does |
|---|---|---|
| `pqv2_evaluation.build_pqv2_diagnostics` | :1427–1877 | steps 1–9 |
| Event dispatch | :1561 → `_build_event_diagnostics` (:2013) | builds the K0/K1 inputs, calls `build_pqv2_diagnostics(..., _event_dispatch=False, event_scenario=K0)`, re-evaluates a K1 panel (`_scenario_panel`) with a K1 declaration (`factor_panel_sha256` replaced), and merges with `_merge_event_diagnostics` |
| Step 4 | :1583–1604 | `diag_b = _d7b.build_development_diagnostics(...)`, then `_reconstruct_b` and `_reconcile_b` |
| Steps 5–7 | :1612–1695 | `_sources.load_source_set`, `_realize_std_x_rank`, `_dv_frame`, `_factor_lookup`, `_scenario_lookup`, `_ranked_members`, `_labels_for_formation(…, scenario_t0_lookup, scenario_ts_lookup, fold=…)`, `_cross_section_keys`, `_assignment_for_formation(…, labels, …)` |
| Step 8 | :1698 | `_envelope.compose_series(...)` and its gate |
| Step 9 | :1722–1816 | the B cell takes the D7b economic value and oriented t (`diag_b.economic_*` / `t_*` / `n_obs_*`); the L cell uses `_period_cell_arrays(envelope, scenario)` and `_estimate_l(envelope, ordered_b, scenario)` |
| D7b orientation | `delisting_bridge` :2433–2439 | `economic = value` and `t` as computed if sign = +1; both negated if sign = −1 |

## Content

### 1. `smart_beta/dataset_v1/pqv25/evaluation.py`

**`build_pqv25_diagnostics(root, declaration, factor_spec, factor_panel, partition, *, <the frozen keyword set>, d=TD_D) -> PQv25Diagnostics`**

1. **Authority first:** `verify_authority_v2_5(repo_root)` runs before any data access.
2. **Base call:** call the frozen `build_pqv2_diagnostics` **unchanged**, with the same
   arguments, to get `base`.
   - If `base.not_assessed` is set, the TD cells are NOT_ASSESSED with the same reason.
3. **Non-event path:** run the **TD core** (§2) once.
   - **Event path** (`str(certified) == EVENT_SIMPLE`): re-express
     `_build_event_diagnostics`'s K0/K1 construction with the frozen helpers
     `_events.build_event_inputs`, `_tri_frame`, `_event_roles`, `_scenario_panel`,
     `_replace` and `_d7b._canonical_factor_hash`.
   - Run the TD core per event scenario, with `event_layer` / `event_scenario`, and the
     K1 declaration exactly as the frozen code forms it.
   - Name the cells `B-TD-K0`, `L-TD-K0`, `B-TD-K1` and `L-TD-K1`.
   - Mirror the frozen K0-gate short-circuit (`k0.not_assessed is not None and
     k0.certified_class is None`).
4. **Return:** `PQv25Diagnostics(base=base, td_cells=..., b_periods_td=..., d=d,
   methodology_sha256=AUTHORITY_V2_5.methodology_sha256, ed_id=..., ce_id=...)`.
   - The frozen `base` object is carried **unmodified**.
   - A `cells` property returns `base.cells + td_cells`.

### 2. The TD core (non-event; also called per event scenario)

**Re-expresses frozen steps 4–9 for the scenario TD only.**

- **Step 4:**
  - `reconstruct_td(...)` from PQ25-T (it self-checks T0/TS against the frozen
    `_scenario_panels`);
  - `diag_b = base.b_diagnostics`, or `_d7b.build_development_diagnostics(...)` per
    event scenario;
  - **also** call the frozen `_reconcile_b((rec.t0, rec.ts), diag_b, sign)`.
- **Steps 5–7:** identical frozen helper calls, except that the labels are built as
  `_labels_for_formation(formation, universe, source_set, loaded.label_by_key,
  scenario_t0_lookup, scenario_td_lookup, fold=fold)`.
  - The `ts` slot carries TD (erratum E1.2).
  - The same labels go into `_assignment_for_formation`, as the frozen step 7 does.
- **Step 8:** the frozen `compose_series` with identical arguments, and the same gate
  behaviour. A gate reason makes the TD cells NOT_ASSESSED with that reason.
- **Step 9 for TD:**
  - **The B-TD cell:**
    - `value = sign · rec.td.value` (D7b orientation);
    - `t_stat = rec.td.t_stat`, negated if sign = −1;
    - `n_obs = rec.td.n_obs`;
    - availability, status and reason as the frozen B cell;
    - the period arrays from `_period_cell_arrays(envelope_td, "TS")`.
  - **The L-TD cell:** `_estimate_l(envelope_td, ordered_b_td, "TS")`, where
    `ordered_b_td` comes from `_b_series_for_metric(rec.td, metric_basis)`. All other
    fields are as in the frozen L cell.
  - **Fields:** `cell = "B-TD"` / `"L-TD"`, `scenario = "TD"`, with the
    `declaration_hash` of the declaration used.
- **In-line self-check (fail-closed, `PQv25InvariantError`)**, on every call:
  - the **T0 slot** of `envelope_td` must reproduce the frozen T0 results;
  - `_estimate_l(envelope_td, ordered_b_t0, "T0")` must equal the base `L-T0` cell's
    value / t / n_obs / status / reason / period arrays **bitwise**;
  - the B-T0 period arrays must also equal the base ones.
  - This proves the re-expressed steps 5–8 match the frozen run on every production call.

### 3. `smart_beta/dataset_v1/pqv25/series.py`

- **`export_td_series(...)`:** the per-period TD export, mirroring the record shape of
  the frozen `pqv2_series.export_series` for the TD cells: periods, b_series, l_series,
  gross/net.
- **Not modified:** `pqv2_series.py`.

## Tests (`tests/test_pqv25_evaluation.py`; synthetic only)

1. **d = −1:** every TD cell (B and L; non-event, plus event K0/K1) equals the frozen TS
   cell of the same base call in value, t_stat, n_obs, status, reason, period arrays,
   period_causes and cause_union.
   - **Floats are compared by bit pattern** (`struct.pack('>d', x)`), so `-0.0` is not
     equal to `0.0`. This is the PQ25-T review note.
2. **d = 0:** the TD cells equal the T0 cells in the same bitwise sense.
3. **d = −0.55:** for a SIMPLE fixture, the L-TD per-period values agree with the
   PQ25-G oracle L lower envelope within 1e-12. Monotonicity holds: for every period,
   L-TD at d = −0.55 lies between L-TS (d = −1) and L-T0 (d = 0).
4. **The in-line self-check is live:** a monkeypatched helper that perturbs one T0 label
   raises `PQv25InvariantError`.
5. **Gates propagate:** a base `not_assessed` and a `compose_series` gate both give TD
   cells NOT_ASSESSED with the same reason.
6. **Bound files:** `verify_authority_v2_5()` passes after a run, and the frozen
   `build_pqv2_diagnostics` output is byte-identical with or without the v2.5 wrapper
   (the wrapper never mutates it).
7. **Determinism:** two runs produce identical outputs.

**Fixtures:** reuse the PQ2-H / C3 parity / M0-4 terminal fixtures, plus an event
fixture from the existing PQ23 tests. The worker locates them and names them in its
report.

## Owned files (all new)

- `smart_beta/dataset_v1/pqv25/evaluation.py`
- `smart_beta/dataset_v1/pqv25/series.py`
- `tests/test_pqv25_evaluation.py`
- `tests/fixtures/pqv25/evaluation/**`

## Launch

- One Pi worker via Herdr, worktree `worktrees/task-pq25-h-td-evaluation`.
- **Stop rule:** if any named frozen helper is missing or behaves differently, or if the
  in-line T0 self-check cannot be made bitwise-equal, **STOP and report**. Do not
  redesign.
- Review, merge and barrier follow CLAUDE.md §4, plus `verify_authority_v2_5()`.

## Freeze (user, 2026-10-14: "冻结第三波的 spec，并授权启动 PQ25-H")

This spec is frozen. The PQ25-H Pi launch is authorized.
