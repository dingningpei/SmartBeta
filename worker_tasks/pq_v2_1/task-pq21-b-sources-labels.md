# PQ21-B — P-Q v2.1 source classification + cause-aware labels (FROZEN)

- **Authority:**
  - methodology `docs/discovery_dataset_v1_2_pq_v2_1_methodology.md` (`66772bb1…5d1e`),
    M§2, M§3, M§6–M§8, **M§8A**;
  - plan `docs/discovery_dataset_v1_2_pq_v2_1_implementation_plan.md` (`007ec880…77f9`),
    §2 row B, §4, §5;
  - the **actual A API** on master `35bb3ec` (`pqv2_core`): `SourceState`,
    `UnresolvedFlag`, `cause_for_source_state`, `CauseSet`, `EMPTY_CAUSES`,
    `label_status_for_causes`, `NotAssessed`, `LabelStatus.UNKNOWN_SOURCE`,
    `PQV2_SOURCE_UNRESOLVED`, `PQV2_RESERVE_UNKNOWN`, `DEVELOPMENT_END`,
    `MISDATED_WINDOW`;
  - base task `worker_tasks/pq_v2/task-pq2-b-sources-labels.md` for everything not
    changed here.
- **Branch:** the existing `pq2/task-pq2-b-r1` (worktree `worktrees/task-pq2-b-r1`). It
  contains B-R1 `4f758d0` unchanged, plus a planner merge of master `35bb3ec`. Add **one
  new commit** on top.
  - **Do not** amend, rebase, squash or redo `4f758d0`.
  - B-R1 semantics (event → every D4 key whose development-clipped interval contains it)
    are preserved exactly.
- **Owned (only):**
  - `smart_beta/dataset_v1/pqv2_sources.py`, `smart_beta/dataset_v1/pqv2_labels.py`;
  - `tests/test_pqv2_sources.py`, `tests/test_pqv2_labels.py`;
  - `tests/fixtures/pqv2/labels/mutations.json`.

  Anything else → STOP.

## 1. Source classification (`load_source_set`), per flag, in this order (M§8A.1)

Use the overlay flag row (`flag_session` s, `prev_session`, `next_session`,
`next_beyond_development`) and the trusted sessions.

1. **UNKNOWN_RESERVE** if:
   - (a) `next_beyond_development` is true, or next(s) is None, or
     next(s) > `DEVELOPMENT_END`; or
   - (b) pos(s) + `MISDATED_WINDOW` > pos(`DEVELOPMENT_END`).

   **No row dated after `DEVELOPMENT_END` is read**, either to classify or to resolve.
2. **UNKNOWN_SOURCE** if next(s) ≤ `DEVELOPMENT_END` and the D7c `daily_total_return`
   for the key at row next(s) is not usable. Not usable means:
   - the row label is absent from the D7c frame;
   - the key is not a column;
   - the value is NaN, +inf or −inf;
   - or 1 + value ≤ 0.
3. **RESOLVED** otherwise. m_obs = 1 + value; `UncertainTransition` as today (S2/Q_s
   unchanged).

**The v2 global raise for unresolved m_s is removed.** Other existing input validation
(malformed overlay, D4 key/interval errors, CA validation, `ca_event_count` cross-check,
post-firewall rows in inputs) is unchanged.

**Output (`SourceSet`):**
- `transitions_by_key`: **RESOLVED flags only**.
- `unknown_by_key[key]`: a tuple of A's `UnresolvedFlag(security_key, s, prev_s, state)`,
  sorted by `(s, state)`, with state UNKNOWN_SOURCE or UNKNOWN_RESERVE.
  - **No** bare sessions, **no** reason-string triples, **no** m_obs.
  - Every unresolved flag appears exactly once. None is dropped.
- `candidates_by_key`: recorded MISDATED candidates (§7 of the base spec, B-R1
  assignment) are enumerated for **RESOLVED and UNKNOWN_SOURCE** flags. They need only
  recorded events, so C can compute pair-effect reach. For UNKNOWN_RESERVE flags, only
  candidates with d ≤ `DEVELOPMENT_END` are enumerated.
- **No substitution:** never D7a, another D7c session, a fill, interpolation, zero or
  λ = 1.

## 2. Labels (`label_envelope`), dependency-local (M§8A.2, M§8A.4)

- **Input:** `unknown` is the key's `UnresolvedFlag` tuple, or None.
- **Reach:** a flag reaches the label iff:
  - its own position is in [f, next(e*)]; or
  - a recorded candidate pair (s, d) has exactly one of {s, d} in (f, e*].
- **Label causes:** a `CauseSet` holding:
  - the union of `cause_for_source_state(flag.state)` over the reaching unresolved
    flags;
  - plus `PQV2_RESERVE_UNKNOWN` if e* == `DEVELOPMENT_END` (the existing M§8 firewall).
- **If the causes are non-empty:**
  - status = `label_status_for_causes(causes)`;
  - values NaN;
  - no envelope computed;
  - `LabelEnvelope.causes` = the cause set;
  - exposure counts `n_unknown_source` and `n_unknown_reserve`.
- **Otherwise:** behaviour is unchanged (ENVELOPE / EMPTY / FACTUAL_MISSING), with
  `causes = EMPTY_CAUSES`.
- **Unrelated flags have no effect.** SOURCE and RESERVE are never converted into each
  other.
- **EMPTY_NO_DEPENDENCY** labels consult no flag (unchanged).
- **Interim note (recorded):** until PQ21-F lands, the merged F only recognizes
  `UNKNOWN_RESERVE` labels. B must still emit `UNKNOWN_SOURCE` exactly. F's Wave-4 spec
  must close this gap.

## 3. Required tests (synthetic only)

**Source classification:**
- finite valid m_s → RESOLVED;
- absent row inside development → UNKNOWN_SOURCE;
- NaN, +inf and −inf → UNKNOWN_SOURCE;
- 1 + v ≤ 0 → UNKNOWN_SOURCE;
- next(s) > `DEVELOPMENT_END` (or `next_beyond_development`) → UNKNOWN_RESERVE, with a
  file-open audit showing no reserve path;
- undeterminable MISDATED (pos(s) + 5 > dev end) → UNKNOWN_RESERVE, even when m_s is
  finite;
- a never-in-D7c key → `UnresolvedFlag` UNKNOWN_SOURCE, with no global abort;
- a valid return on another date for the same key does not substitute;
- a D7a VALID return does not substitute;
- unresolved flags have no m_obs (not in `transitions_by_key`);
- every unresolved flag appears exactly once;
- deterministic ordering, with flag rows permuted.

**Labels:**
- a reaching SOURCE flag → UNKNOWN_SOURCE / `PQV2_SOURCE_UNRESOLVED`;
- a reaching RESERVE flag → UNKNOWN_RESERVE;
- SOURCE + RESERVE → status UNKNOWN_RESERVE, causes contain both;
- an unrelated unresolved flag → unchanged envelope;
- a pair-effect reach (s outside the window, d inside);
- e* == dev end combined with a SOURCE flag → both causes;
- resolved numerical behaviour is unchanged: all existing label and oracle-equality tests
  pass unchanged.

**B-R1 (retained):** unique ticker; disjoint and overlapping reuse; inclusive endpoints;
development clipping; D4 and event row-order invariance; D7a agreement.

## 4. Mutations (add to `tests/fixtures/pqv2/labels/mutations.json`; all existing ones, including B-M6, stay killed)

| ID | Mutation |
|---|---|
| V21-M01 | global first-unresolved-m_s abort restored |
| V21-M02 | UNKNOWN_SOURCE treated as RESOLVED / λ = 1 |
| V21-M03b | SOURCE flag emitted as UNKNOWN_RESERVE (sources) |
| V21-M03l | SOURCE label status emitted as UNKNOWN_RESERVE (labels) |
| V21-M04 | RESERVE emitted as UNKNOWN_SOURCE |
| V21-M09 | D7a return substituted |
| V21-M10 | another D7c session substituted |
| V21-M11 | unresolved flag dropped from `unknown_by_key` |
| V21-M14 | next(s) > dev end classified UNKNOWN_SOURCE |
| B21-M1 | label ignores unresolved-flag reach (cause dropped) |
| B21-M2 | an unrelated unresolved flag poisons the label |
| B21-M3 | label cause set keeps only the primary |

## 5. Stop conditions

Base plan §13, plus:
- the A API being insufficient;
- B-R1 not being preservable as its own commit;
- any classification edge case not settled by M§8A.1.
