# PQ21-A — P-Q v2.1 core: cause vocabulary, CauseSet, flag/cell interfaces, versioned authority (FROZEN)

- **Authority:**
  - methodology `docs/discovery_dataset_v1_2_pq_v2_1_methodology.md` (SHA-256
    `66772bb1b12a134ca35a3ff5e3d475e00c998b95d3cdcb1e3223fd5f012d5d1e`), M§8A, M§13,
    M§15, M§17;
  - plan `docs/discovery_dataset_v1_2_pq_v2_1_implementation_plan.md` (SHA-256
    `007ec880982086412f99422c7aa9330158bbfaae61f950539ec537e0b39a77f9`), §1, §2 (row
    A), §5;
  - base task `worker_tasks/pq_v2/task-pq2-a-core.md` for everything not changed here.
- **Wave:** v2.1 Wave 1. **Dependencies:** none.
- **Owned (only):**
  - `smart_beta/dataset_v1/pqv2_core.py`;
  - `tests/test_pqv2_core.py`;
  - `tests/fixtures/pqv2/core/mutations.json`.

  Any other file → STOP.
- **Not in A:** A owns **vocabulary and representation, never relevance**. No dependency
  selection, no flag classification, no correction sets, no theorem logic, no data
  reads.

## 1. Versioned authority (historical v2 stays reconstructable)

1. **`MethodologyAuthority`** (frozen dataclass):
   - fields `version: str`, `methodology_relative: str`, `methodology_sha256`, `cs_id`,
     `ed_id`, `ce_id`.
2. **The two authorities:**

   | Constant | version | methodology document | methodology SHA-256 | CS-ID | ED-ID | CE-ID |
   |---|---|---|---|---|---|---|
   | `AUTHORITY_V2` (historical) | `"P-Q v2"` | `docs/discovery_dataset_v1_2_pq_v2_methodology.md` | `a31ed9c49cd3a03208ae28539a8b5f5531916e31fac32bbd60e2af07746fa56d` | `432de097…bdae2` (full value as today) | `e979924ee4efc365155d8a79089fb940a28ebede7099f67373f0d654bd479aeb` | `605fec93…0dbaaf` |
   | `AUTHORITY_V2_1` | `"P-Q v2.1"` | `docs/discovery_dataset_v1_2_pq_v2_1_methodology.md` | `66772bb1b12a134ca35a3ff5e3d475e00c998b95d3cdcb1e3223fd5f012d5d1e` | `432de09746bbc4c16c332d662c24d6d7369787d55e8b704e3a521494b73bdae2` | `65bd7d1341e70674132bc7729870876adf5457fe9ce4c277d0f1148df343a4d6` | `605fec93812d4165b6bf455a9fb453cd7c0c6c4a9f5a2a0fc418b53b0d0dbaaf` |

3. **Current authority:** `CURRENT_AUTHORITY = AUTHORITY_V2_1`. The existing module
   constants `METHODOLOGY_SHA256`, `CS_ID`, `ED_ID`, `CE_ID` and the methodology path
   used by `verify_authority` equal `CURRENT_AUTHORITY`'s values. Existing importers keep
   working unchanged.
4. **`verify_authority(repo_root=None, authority=CURRENT_AUTHORITY)`** and
   **`recompute_identity_hashes(repo_root=None, authority=CURRENT_AUTHORITY)`:** the same
   checks as today, against `authority`.
   - `verify_authority(authority=AUTHORITY_V2)` must still pass on master (the v2
     document is preserved byte-identical).
   - `PINNED_CODE` is unchanged.
5. **`require_current_authority(methodology_sha256, cs_id, ed_id, ce_id)`:** raises
   `PQv2BindingError` unless all four equal `CURRENT_AUTHORITY`. The stale v2
   methodology hash and the stale v2 ED-ID are rejected.

## 2. Vocabulary (string constants; exact spellings are frozen)

- **`NotAssessedReason.SOURCE_UNRESOLVED = "PQV2_SOURCE_UNRESOLVED"`:** added to `ALL`.
  `RESERVE_UNKNOWN = "PQV2_RESERVE_UNKNOWN"` is unchanged. They are distinct strings and
  never alias. Module alias `PQV2_SOURCE_UNRESOLVED`.
- **`LabelStatus.UNKNOWN_SOURCE = "UNKNOWN_SOURCE"`:** added to `ALL`.
  `UNKNOWN_RESERVE = "UNKNOWN_RESERVE"` is unchanged.
- **`SourceState`:**
  - `RESOLVED = "RESOLVED"`, `UNKNOWN_SOURCE = "UNKNOWN_SOURCE"`,
    `UNKNOWN_RESERVE = "UNKNOWN_RESERVE"`;
  - `ALL`;
  - `UNKNOWN = (UNKNOWN_SOURCE, UNKNOWN_RESERVE)`.
- **`cause_for_source_state(state) -> str`:**
  - UNKNOWN_SOURCE ↦ `PQV2_SOURCE_UNRESOLVED`;
  - UNKNOWN_RESERVE ↦ `PQV2_RESERVE_UNKNOWN`;
  - RESOLVED or anything else → `PQv2InputError`, because RESOLVED is not a cause.
- **`label_status_for_causes(cause_set) -> str`:** for a non-empty cause set with an
  UNKNOWN cause, `PQV2_RESERVE_UNKNOWN` present ↦ `UNKNOWN_RESERVE`, else
  `UNKNOWN_SOURCE` (M§8A.4 precedence). Otherwise `PQv2InputError`.

## 3. CauseSet (cell-level; M§8A.4)

- **`PERIOD_CAUSE_PRECEDENCE`** = (`PQV2_RESERVE_UNKNOWN`, `PQV2_SOURCE_UNRESOLVED`,
  `PQV2_SIGMA_SUPPORT`, `PQV2_WINSOR_BLOCK`, `PQV2_EMPTY_LEG_CONFIGURATION`). This is the
  complete M§8A.4 list, highest first. `UNKNOWN_CAUSES` = the first two.
- **`CauseSet`** (frozen dataclass, field `causes: tuple[str, ...]`):
  - **Construction:** `CauseSet.of(*reasons)` / `CauseSet.from_iterable(it)`.
    - Every element must be in `PERIOD_CAUSE_PRECEDENCE`; otherwise `PQv2InputError`.
      That rejects `"RESOLVED"`, the whole-evaluation gate reasons, statistic reasons and
      unknown strings, because gates are not period causes (M§8A.4).
    - Duplicates collapse.
  - **Canonical order:** ascending **lexicographic order of the reason strings** (M§8A.4
    "sorted tuple (by canonical string)"). This is never insertion order, set order or
    enum declaration order.
    `{SOURCE, RESERVE}` → `("PQV2_RESERVE_UNKNOWN", "PQV2_SOURCE_UNRESOLVED")`.
  - **Empty:** `EMPTY_CAUSES = CauseSet(())` means no cause, i.e. assessable as far as
    causes go. `is_empty` is True, `primary` is None, and it serializes to `[]`.
  - **Properties:** `is_empty`, `has_unknown` (any of `UNKNOWN_CAUSES`), and `primary`
    (the highest-precedence member, or None if empty). Also `union(other) -> CauseSet`,
    `to_list() -> list[str]` (canonical order), `__iter__`, `__len__`, `__contains__`.
  - **Contract:** `primary` is a presentation/disposition field. It **never replaces**
    the full cause set; every consumer must persist `to_list()`.
- **Short-circuit contract helper:** `must_short_circuit(cause_set) -> bool` =
  `cause_set.has_unknown`. Downstream layers must return NOT_ASSESSED **before** any
  resolved-state precondition when this is True. A documents this; it does not
  implement D/E/F logic.

## 4. NotAssessed (backward compatible)

- **New field:** `causes: CauseSet | None = None`. `NotAssessed(reason, detail)` keeps
  working exactly as today (`causes` None).
- **`NotAssessed.from_causes(cause_set, detail=None)`:** requires a non-empty
  `CauseSet`, and sets `reason = cause_set.primary`.
- **Consistency:** if `causes` is given directly, `reason` must equal `causes.primary`;
  otherwise `PQv2InvariantError`.
- **`cause_set` property:**
  - `causes` if set;
  - else `CauseSet.of(reason)` if `reason` is in `PERIOD_CAUSE_PRECEDENCE`;
  - else None (gate and statistic reasons).

## 5. Flag-level interface (for B / C in Wave 2)

- **`UnresolvedFlag`** (frozen dataclass): `security_key: str`, `s: date`,
  `prev_s: date | None`, `state: str`.
  - `state` must be in `SourceState.UNKNOWN`; RESOLVED → `PQv2InputError`.
  - Property `cause` = `cause_for_source_state(state)`.
  - It has **no** `m_obs` or correction-set field, so an unresolved flag cannot carry a
    numerical correction.
- **Placement:** in Wave 2, B places `UnresolvedFlag` values in
  `SourceSet.unknown_by_key[key]`, as a tuple sorted by `(s, state)`. C maps the flags
  that reach a cell into that cell's `CauseSet`.
- **`SourceSet` is unchanged in Wave 1:** it still accepts the current
  `unknown_by_key` forms, so the merged B/C/H keep working until Wave 2.

## 6. Required tests (`tests/test_pqv2_core.py`; synthetic only; all existing core tests keep passing, updated only where they assert the v2 constants)

1. SOURCE and RESERVE reasons, statuses and states are distinct and unequal.
2. SOURCE serializes to `"PQV2_SOURCE_UNRESOLVED"` exactly.
3. RESERVE serializes to `"PQV2_RESERVE_UNKNOWN"` exactly.
4. `CauseSet` is insertion-order independent (all permutations give an identical tuple).
5. A duplicate cause collapses.
6. SOURCE + RESERVE retains both.
7. SOURCE + RESERVE has primary RESERVE.
8. SOURCE + SIGMA_SUPPORT retains both; primary SOURCE.
9. RESERVE + WINSOR_BLOCK retains both; primary RESERVE.
10. Empty behaviour is exactly as §3, and `NotAssessed.from_causes(EMPTY_CAUSES)` raises.
11. RESOLVED is rejected by `CauseSet`, `cause_for_source_state` and `UnresolvedFlag`;
    gate and statistic reasons are rejected by `CauseSet`.
12. Canonical serialization (D7b `canonical_json` of `to_list()`) is identical across
    two fresh processes.
13. The same holds with different `PYTHONHASHSEED` values (identical bytes and SHA-256).
14. `verify_authority()` binds the v2.1 methodology hash.
15. The ED-ID binding equals `65bd7d13…`.
16. `require_current_authority` with the v2 methodology hash → `PQv2BindingError`.
17. `require_current_authority` with the v2 ED-ID → `PQv2BindingError`.
18. CS-ID equals the frozen unchanged value.
19. CE-ID equals the frozen unchanged value.
20. Authority tamper (one byte in a temp copy of the v2.1 document) is rejected. Also:
    `verify_authority(authority=AUTHORITY_V2)` passes on the preserved v2 document.

## 7. Mutations (`tests/fixtures/pqv2/core/mutations.json`; all existing A mutations stay killed)

| ID | Mutation |
|---|---|
| V21-M12a | primary chosen by insertion order / first-seen instead of precedence |
| V21-M15 | v2 methodology hash kept as current |
| A21-M1 | SOURCE_UNRESOLVED aliased to `"PQV2_RESERVE_UNKNOWN"` |
| A21-M2 | `cause_for_source_state(UNKNOWN_SOURCE)` returns RESERVE |
| A21-M3 | `CauseSet` keeps only the primary |
| A21-M4 | canonical order = insertion order |
| A21-M5 | duplicates retained |
| A21-M6 | precedence SOURCE > RESERVE |
| A21-M7 | SIGMA_SUPPORT / WINSOR_BLOCK outrank SOURCE |
| A21-M8 | v2 ED-ID kept as current |
| A21-M9 | CS_ID or CE_ID constant altered |
| A21-M10 | `CauseSet` accepts RESOLVED |

Every non-equivalent mutation must be killed.

## 8. Stop conditions

Base plan §13, plus:
- a needed change outside the owned files;
- any existing merged module or test failing because of an A change that cannot be made
  backward compatible within A.

Then STOP and report. Do not edit B–H.
