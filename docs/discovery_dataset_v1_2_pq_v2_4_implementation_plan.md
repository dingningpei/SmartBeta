# P-Q v2.4 — implementation plan, path (i) (SPECIFICATION FROZEN 2026-10-09; Waves 1–3 authorized)

- **Methodology:** `docs/discovery_dataset_v1_2_pq_v2_4_fold_firewall.md` (frozen
  `6b667c5`, `695973b9…`), chain v2.3 → v2.2 → v2.1.
- **Identities:**

  | Identity | Value |
  |---|---|
  | ED v2.4 | `f8370828ae292f9e5bfc0a5a4e7054ba79c33fe88c1646793ad0f1d0dcc88d93` |
  | CE v2.4 | `c40d8724ff6b6ee7862a07e39eb008da0e667a3826599e3850dff4e4993fa4c2` |
  | CS | unchanged |

- **Base master:** `bc24a8f` or later.
- **Planner self-check:**
  - **Literals:** the fold literals are below.
  - **Pinned files:** none of the four modules is in `PINNED_CODE` (verified); the pinned
    files stay untouched.
  - **Frozen consumers:**
    - Campaign-2 (v2.1, through `pqv2_evaluation`);
    - the C3-X evaluator (v2.3, through `pqv2_evaluation`);
    - the v2.1–v2.3 goldens and cross-checks.

    Every one must pass unchanged, because the default fold is the development fold.
  - **K-PIT:** untouched.

## 1. Fold contract (A)

- **`Fold(name, start, end)`.** The frozen literal is
  `DEVELOPMENT_FOLD = Fold("development", None, date(2023, 6, 30))`.
- **The reserve fold** is constructed only from a P7 `AccessGrant`, as
  `Fold("reserve", date(2023, 7, 3), C)`. P-Q accepts any `Fold`; the gate lives in
  P7 `reserve_chain.access`.
- **The new cause** `PQV2_FOLD_END_UNKNOWN`. In the `CauseSet` order it sits immediately
  after `PQV2_RESERVE_UNKNOWN`. Dev runs never emit it.
- **`AUTHORITY_V2_4`:** the chain base is v2.3; admitted authorities = (v2.1, v2.2, v2.3,
  v2.4); CURRENT stays v2.1.

## 2. Tasks (path (i): minimal fold parameter; **the default is the development fold, byte-identical to v2.3**)

| Wave | Task | Owned | Depends on |
|---|---|---|---|
| 1 | **PQ24-A** core | `pqv2_core.py`, its tests and fixtures | — |
| 1 | **PQ24-G** oracle | `tests/pqv2_oracle/**`, the oracle tests, a new `tests/test_pqv2_oracle_fold.py` | — |
| 2 | **PQ24-B** sources/labels/events | `pqv2_sources.py`, `pqv2_labels.py`, `pqv2_events.py`, their tests and fixtures | A |
| 3 | **PQ24-H** evaluation | `pqv2_evaluation.py`, its tests and fixtures, the cross-check tests | A, B, G |
| 4 | planner: full I1–I15 (v2.4) | evidence | all |

**PQ24-B:**
- **Every boundary reference** becomes the fold's end E. That covers the flag next(s) > E,
  the label e* == E, the MISDATED window, and the event K ≤ E. The default is
  `DEVELOPMENT_FOLD` everywhere.
- **The reserve fold:**
  - the source loader accepts **stitched** inputs: dev rows < 2023-07-03 and reserve
    rows ≥ 2023-07-03, as supplied by P7 R7-T;
  - it classifies each flag by its own date's source;
  - flags beyond E → `PQV2_FOLD_END_UNKNOWN`;
  - the event loader reads `reserve_sealed/edgar-*` **only** when given a reserve Fold.
- **Tests:**
  - the default path is byte-identical (all existing tests pass unchanged);
  - synthetic reserve-fold cases: beyond E, stitched classification at the boundary,
    events at E and E + 1, the MISDATED window crossing E;
  - no reserve path is opened under the default fold (file-open audit).
- **Mutations:**
  - B24-M1: E fixed at `DEVELOPMENT_END` in the reserve fold;
  - B24-M2: a reserve-dated flag classified from dev sources;
  - B24-M3: the beyond-E cause mislabelled RESERVE;
  - B24-M4: the reserve events read under the default fold.

**PQ24-H:**
- `build_pqv2_diagnostics(..., fold=DEVELOPMENT_FOLD)` threads the fold to B and C.
- **Diagnostics** add `fold` **only when it is non-default**, so v2.1–v2.3 diagnostics
  stay byte-identical (the PQ22-E1 pattern).
- **Tests:**
  - all goldens unchanged;
  - an end-to-end synthetic reserve fold with stitched inputs, for a C1 spec and an
    EVENT_G spec;
  - the fold recorded.
- **Mutations:**
  - H24-M1: the fold is not threaded to the labels;
  - H24-M2: `fold` appears in default-fold diagnostics.

**PQ24-G:** fold-end oracle worlds. Default ≡ v2.3; reserve-fold brute-force checks of
membership ⊆, L ≥ min and turnover ≤.

## 3. Barrier (planner)

- **Mode:** a full I1–I15 against v2.4.
- **I2 additions:**
  - the default-fold byte-identity of every v2.1–v2.3 golden;
  - the Campaign-2 executor tests;
  - the C3-X tests.
- **I12 addition:** 0 reserve opens under the default fold.
- **No real reserve data is read.** The real reserve-fold evaluation happens only in the
  authorized P6 confirmation.

## 4. Stops

- A default-path byte difference.
- Any pinned file must change.
- The reserve-fold semantics are ambiguous for a concrete case.
