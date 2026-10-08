# PQ21-C — P-Q v2.1 dependency-local cause propagation (FROZEN)

- **Authority:**
  - methodology `docs/discovery_dataset_v1_2_pq_v2_1_methodology.md` (`66772bb1…5d1e`),
    M§8A.2, M§8A.4, M§10;
  - plan `docs/discovery_dataset_v1_2_pq_v2_1_implementation_plan.md` (`007ec880…77f9`),
    §2 row C, §4, §5;
  - the **actual A API** on master `35bb3ec`: `UnresolvedFlag`, `cause_for_source_state`,
    `CauseSet`, `EMPTY_CAUSES`, `SourceSet`;
  - base task `worker_tasks/pq_v2/task-pq2-c-dependency.md` for everything not changed
    here.
- **Branch:** new `pq2/task-pq21-c` from master `35bb3ec`.
- **Owned (only):**
  - `smart_beta/dataset_v1/pqv2_dependency.py`;
  - `tests/test_pqv2_dependency.py`;
  - `tests/fixtures/pqv2/dependency/**`.

  Anything else → STOP.
- **C determines relevance only.** It never classifies source resolvability (B does). It
  never sorts or ranks causes itself (A's `CauseSet` does).

## 1. Input contract

- `SourceSet.unknown_by_key[key]` is a tuple of A's `UnresolvedFlag`.
- C must also accept that tuple after H's `_session_only_source_set` projection, which
  passes `UnresolvedFlag` objects through unchanged.
- **Any other entry form** (bare session, reason triple) raises `PQv2InputError`. No
  bare-session UNKNOWN is accepted.
- `candidates_by_key` may contain candidates whose flag s is unresolved. They are used
  only for reach, never for factors.

## 2. Propagation (`input_uncertainty`)

For each cell (a key at the formation):
- **Reach:** apply the existing frozen rules to each `UnresolvedFlag`:
  - algebraic s ∈ [y, next(x)];
  - numerical {prev s, s, next s} ∩ W ≠ ∅;
  - dv cells {prev s, s} ∩ W;
  - MISDATED pair effects of its recorded candidates (exactly one of {s, d} in the
    algebraic window, or s or d in a numerical window).
- **Cell cause set:** `CauseSet.from_iterable(cause_for_source_state(f.state) for
  reaching f)`. If nothing reaches the cell, `EMPTY_CAUSES`.
- **Output, `InputUncertainty`:**
  - **new** `causes: Mapping[str, CauseSet]` for every key evaluated (the full
    cross-section for STD_X_RANK, as today);
  - the existing `unknown: Mapping[str, bool]` is kept, equal to
    `not causes[key].is_empty` (D, E and F consume it until Wave 3/4).
- **No numerical correction from an unresolved flag:** it never contributes a factor or
  an interval. Its cell's M/V intervals may still be built from RESOLVED flags, but the
  cell is UNKNOWN through its `causes`.
- **Unchanged:**
  - `certify`, `dependency_windows`, `frozen_table.json`, the canonical keys and the
    seven certified expressions;
  - sign −1 rejected; no structural fallback.

## 3. Required tests (synthetic SourceSets built with A's types)

- an unrelated SOURCE flag → `EMPTY_CAUSES`;
- a reached SOURCE flag → {`PQV2_SOURCE_UNRESOLVED`};
- an unrelated RESERVE flag → empty; a reached RESERVE flag → {`PQV2_RESERVE_UNKNOWN`};
- SOURCE + RESERVE reaching one cell → both retained (`to_list()` canonical, primary
  RESERVE);
- duplicate SOURCE flags → one cause;
- input order reversed → identical `CauseSet`;
- the same unresolved flag reaches cell A but not cell B;
- endpoint inclusion/exclusion at y, next(x), and the numerical and dv window edges;
- pair-effect reach;
- STD_X_RANK: an unresolved flag on a non-universe key is reported for that key;
- bare-session or triple `unknown_by_key` entries → `PQv2InputError`;
- `unknown` bool map equals `not causes.is_empty`;
- all H1–H7 frozen-table and certification regressions pass unchanged (sign −1,
  perturbed window, extra node rejected).

## 4. Mutations (add to `tests/fixtures/pqv2/dependency/mutations.json`; all existing ones stay killed)

| ID | Mutation |
|---|---|
| V21-M05 | cause dropped (empty `causes` / bool only) |
| C21-M1 | an unrelated unresolved flag poisons the cell |
| C21-M2 | a reached SOURCE flag dropped |
| C21-M3 | a reached RESERVE flag dropped |
| C21-M4 | SOURCE mapped to RESERVE |
| C21-M5 | RESERVE mapped to SOURCE |
| C21-M6 | only the primary cause retained |
| C21-M7 | traversal-order-dependent result (first flag only) |
| C21-M8 | a dependency endpoint shifted for unresolved reach |
| C21-M9 | bare-session entries accepted (structural fallback) |

## 5. Stop conditions

Base plan §13, plus:
- the A API being insufficient;
- any H1–H7 dependency needing to change;
- D, E or F breaking because of the C change in a way not fixable inside C.
