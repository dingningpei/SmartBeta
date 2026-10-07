# Dataset v1.2 — P-Q v2.1 implementation plan (SPECIFICATION FROZEN; IMPLEMENTATION NOT AUTHORIZED)

- **Status:** specification only. Authorized by "P-Q v2.1 METHODOLOGY AMENDMENT —
  DOCUMENT-ONLY FREEZE AUTHORIZED", on master `11319e0`. Workers, worktrees, merges,
  recertification and ADM each need a separate authorization.
- **Methodology authority:** `docs/discovery_dataset_v1_2_pq_v2_1_methodology.md`
  ("M§n" below). Its identity constants are listed in §1.
- **Base plan:** `docs/discovery_dataset_v1_2_pq_v2_implementation_plan.md` stays
  authoritative for everything not changed here (architecture, ownership conventions,
  security, oracle independence, mutation-runner format, stop conditions).
- **Base barrier spec:** `docs/discovery_dataset_v1_2_pq_v2_integration_barrier.md`,
  executed against the v2.1 identities.
- **Preserved and unmerged:** PQ2-B-R1 `4f758d0` (ticker reuse → per-D4-key interval
  assignment). It is compatible with v2.1 (M§17).

## 1. Identities bound by v2.1

| Identity | Value |
|---|---|
| Methodology | SHA-256 of `docs/discovery_dataset_v1_2_pq_v2_1_methodology.md` as committed (recorded in the freeze report) |
| CS-ID | `432de09746bbc4c16c332d662c24d6d7369787d55e8b704e3a521494b73bdae2` (unchanged) |
| ED-ID | `65bd7d1341e70674132bc7729870876adf5457fe9ce4c277d0f1148df343a4d6` (new) |
| CE-ID | `605fec93812d4165b6bf455a9fb453cd7c0c6c4a9f5a2a0fc418b53b0d0dbaaf` (unchanged) |

## 2. Affected and unaffected layers (verified against master `11319e0`)

| Layer | Owner | Why it changes | Change |
|---|---|---|---|
| A core | `pqv2_core.py` | it has no `UNKNOWN_SOURCE` / `PQV2_SOURCE_UNRESOLVED`; `NotAssessed` holds a single reason; its constants bind v2 | add `LabelStatus.UNKNOWN_SOURCE`, `NotAssessedReason.SOURCE_UNRESOLVED = "PQV2_SOURCE_UNRESOLVED"`; `SourceState` {RESOLVED, UNKNOWN_SOURCE, UNKNOWN_RESERVE}; `NotAssessed.causes: tuple[str, ...]` (sorted, distinct; `reason` = primary by the M§8A.4 precedence); `primary_reason(causes)` helper with the frozen precedence; constants `METHODOLOGY_SHA256`, `ED_ID` and `_METHODOLOGY_RELATIVE` → v2.1 document (CS_ID, CE_ID and PINNED_CODE unchanged) |
| B sources/labels | `pqv2_sources.py`, `pqv2_labels.py` | `_observed_m` raises globally on unresolved m_s; labels know only `UNKNOWN_RESERVE` | sources: classify each flag per M§8A.1 in that order; **no raise** for unresolved m_s; unresolved flags are absent from `transitions_by_key` and recorded in `unknown_by_key` as `(session, prev_session, state)` with state ∈ {UNKNOWN_SOURCE, UNKNOWN_RESERVE}; MISDATED candidates are still enumerated for UNKNOWN_SOURCE flags. Labels: dependency-local per M§8A.2 (positions and pair effects); status UNKNOWN_SOURCE / UNKNOWN_RESERVE by precedence, plus the label cause set. **Continues on branch `pq2/task-pq2-b-r1` after `4f758d0`** |
| C dependency | `pqv2_dependency.py` | `unknown[key]` is a bool; `_normalize_unknown` keeps sessions only | per-cell UNKNOWN **cause set** (empty = resolved); reach uses the frozen rules (own positions, numerical windows, dv cells, and pair effects of the unknown flag's recorded candidates); no dependency logic moves to B |
| D simple | `pqv2_assign_simple.py` | it emits `PQV2_RESERVE_UNKNOWN` only | NotAssessed carries the union cause set of the universe members' factor-input cells; precedence primary |
| E sandwich | `pqv2_assign_sandwich.py` | same as D | NotAssessed carries the union cause set over the full cross-section; checked before σ support |
| F envelope | `pqv2_envelope.py` | the winsorization check runs before the reserve check; it emits only `RESERVE_UNKNOWN` | the order becomes: (1) assignment NotAssessed, keeping its cause set; (2) UNKNOWN labels of possible-leg members, keeping their causes; (3) **only if the cause set is empty**, the winsorization block and resolved-state checks; per-period `causes` plus primary `reason`; never a generic replacement |
| H evaluation | `pqv2_evaluation.py` | `_session_only_source_set` drops causes; diagnostics have no cause sets | keep the causes when projecting the source set; persist per-period cause sets, the statistic-level union of cause sets, and per-cause counts (`n_unknown_source`, `n_unknown_reserve`); identity values come from core; a v2 declaration (old identities) gives a typed blank `PQV2_IDENTITY_MISMATCH` |
| G oracle | `tests/pqv2_oracle/` | **no change**: UNKNOWN cases claim no numerical bound (M§13 theorem-domain ruling), and resolved-state semantics are unchanged | regression only (existing G self-tests must pass unchanged) |

## 3. Waves (auditable; nothing is launched under this authorization)

| Wave | Task | Branch | Depends on |
|---|---|---|---|
| 1 | **PQ21-A** (core vocabulary, cause sets, precedence, v2.1 identity constants) | new `pq2/task-pq21-a` | — |
| 2 | **PQ21-B**: B-R1 is kept as its own commit `4f758d0`; a new v2.1 commit follows it on `pq2/task-pq2-b-r1` (after syncing master) | existing | A |
| 2 | **PQ21-C** (cause-set propagation) | new | A |
| 3 | **PQ21-D**, **PQ21-E** | new | A, C |
| 4 | **PQ21-F** | new | A, B, D, E |
| 5 | **PQ21-H** | new | A–F |
| 6 | planner: **full I1–I15 recertification** of the v2.1 master (additive evidence `docs/discovery_dataset_v1_2_pq_v2_1_software_barrier.md`); then **PQ2-ADM restarted from ADM-1** (separate authorization) | — | all |

- **Ownership:** the same files as the base plan §4. No production file has two owners.
  G, D7a/b/c, the Campaign modules, P-Q v1 and docs are forbidden to every worker.
- **B-R1:** B-R1's real-data acceptance probe is completed during Wave 2, after the
  v2.1 classification removes the global abort.

## 4. Required synthetic tests (owners in parentheses)

- **Relevance:**
  - an unrelated unresolved flag does not poison a cell (B labels, C, F);
  - a dependent UNKNOWN_SOURCE cell → NOT_ASSESSED with `PQV2_SOURCE_UNRESOLVED` (B, C,
    D, E, F, H);
  - a dependent UNKNOWN_RESERVE cell → NOT_ASSESSED with `PQV2_RESERVE_UNKNOWN`
    (unchanged path, now cause-typed).
- **Cause preservation:** the causes stay distinct through H diagnostics (H).
- **No substitution:**
  - D7a is never substituted: a D7a VALID return with D7c unresolved → still
    UNKNOWN_SOURCE (B);
  - another D7c date is never substituted (B);
  - no period or security is dropped: the period stays in B's index as NOT_ASSESSED (F,
    H).
- **Multi-cause determinism:**
  - several UNKNOWN_SOURCE causes are deterministic (C, F);
  - UNKNOWN_SOURCE plus UNKNOWN_RESERVE is deterministic and auditable: primary
    `PQV2_RESERVE_UNKNOWN`, cause set contains both (B labels, F, H);
  - traversal or row order permuted → identical cause sets and primary (B, C, F).
- **Theorem domain:** no theorem is invoked outside the resolved-state domain; under
  UNKNOWN, no interval, no envelope, and no winsorization block evaluation (D, E, F).
- **Classification order:** (B)
  - a missing row inside development → UNKNOWN_SOURCE;
  - next(s) > `DEVELOPMENT_END` → UNKNOWN_RESERVE;
  - MISDATED undeterminable → UNKNOWN_RESERVE;
  - NaN and ±inf → UNKNOWN_SOURCE;
  - a never-in-D7c key reached by a dependency → UNKNOWN_SOURCE, unreached → no effect.
- **Reserve boundary:** zero reserve reads (B, H; file-open audit).
- **Ticker reuse:** stays correct (the B-R1 tests are retained).
- **Identity:** v2.1 identities are bound; v2 identities are rejected (A, H).

## 5. Required mutations (each must be killed)

| ID | Mutation | Owner |
|---|---|---|
| V21-M01 | global first-missing-m_s abort restored | B |
| V21-M02 | UNKNOWN_SOURCE treated as clean (λ = 1 / RESOLVED) | B |
| V21-M03 | UNKNOWN_SOURCE relabelled `PQV2_RESERVE_UNKNOWN` | B, C, D, E, F (each owns its own emission point) |
| V21-M04 | UNKNOWN_RESERVE relabelled `PQV2_SOURCE_UNRESOLVED` | B, F |
| V21-M05 | cause dropped in C (bool or empty set) | C |
| V21-M06 | cause dropped in D / E | D, E |
| V21-M07 | cause dropped in F (generic or primary-only) | F |
| V21-M08 | H collapses causes (drops cause set / per-cause counts) | H |
| V21-M09 | D7a return substituted for missing D7c m_s | B |
| V21-M10 | adjacent-date (another D7c session) substitution | B |
| V21-M11 | unresolved flag dropped (omitted from `unknown_by_key`) | B |
| V21-M12 | traversal-order-dependent primary reason (precedence removed / first-seen) | A, F |
| V21-M13 | resolved-state check (winsorization block) evaluated before UNKNOWN short-circuit | F |
| V21-M14 | next(s) > `DEVELOPMENT_END` classified UNKNOWN_SOURCE | B |
| V21-M15 | v2 identity constants retained | A |

All existing mutations stay killed. H-M14 stays a recorded fixture-tooling ambiguity
(no repair is authorized).

## 6. Recertification and admission

- **Recertification:** after Wave 5, a **full I1–I15** run on the v2.1 master. This is
  the base barrier spec with v2.1 identities; I1 additionally verifies that the v2
  document and its evidence are byte-unchanged. Evidence is additive; the v2 evidence is
  not modified.
- **Admission:** then PQ2-ADM is **restarted from ADM-1**, recording the exact Dataset
  v1.2, D7c and overlay identities. ADM-2 additionally reports the structural census
  (resolved / UNKNOWN_SOURCE / UNKNOWN_RESERVE; reached-by-≥1-dependency vs never
  reached; cells made NOT_ASSESSED; per-H1–H7 breakdown if obtainable without empirical
  evaluation) and completes the B-R1 interval probe.

## 7. Stop conditions (in addition to the base plan §13)

- the cause set cannot be propagated without a new owner;
- a theorem would need to be invoked under UNKNOWN;
- D7a substitution would be needed;
- any layer needs a methodology choice not settled in M§8A;
- B-R1 conflicts with v2.1;
- additional real-data inspection is needed to choose semantics.
