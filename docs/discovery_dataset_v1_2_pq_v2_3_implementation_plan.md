# P-Q v2.3 — implementation plan (SPECIFICATION FROZEN 2026-10-09; Waves 1–3 batch-authorized)

- **Status:** FROZEN by user approval ("批准", 2026-10-09). Waves 1–3 are batch-authorized, and each commit is reviewed
  at its exact SHA. Wave 4 (barrier and ADM) is planner work and stops at its barrier.
- **Methodology:** `docs/discovery_dataset_v1_2_pq_v2_3_event_grammar.md` (`d4ec7f6c…`,
  "E§n"), bound to v2.2 `ab12886c…` and v2.1 `66772bb1…`.
- **Also binding:**
  - M1b (`94309a61…`);
  - Dataset v1.3 (acq `f0d0ee1b…`, ev `ff5e83ec…`, admission `ecbee07`);
  - PQ22-E1 (selectable authority; `CURRENT_AUTHORITY` stays v2.1);
  - PQ22-E2 (two-tier cross-check).
- **Base master:** `0a1e51f`.
- **Planner self-check** (feedback-spec-literals-and-consumers):
  - every seed, window, semantic id and identity literal is stated below;
  - frozen consumers of the shared constants were grepped: Campaign-2 (`campaign2.py`,
    `runner2.py`) binds v2.1 and is untouched because CURRENT does not change;
  - **pinned files** (`pqv2_core.PINNED_CODE`, including `trusted_inputs.py` and
    `spec/*`) must not change. The event inputs are built by a **new** module.

## 1. Identities

| Identity | Value |
|---|---|
| Methodology v2.3 | the SHA-256 of `docs/discovery_dataset_v1_2_pq_v2_3_event_grammar.md` = `d4ec7f6caaa6cc74a340fdcf28b5d2030b3a650c383518284099ebd5a0b26aad` |
| Base chain | v2.2 `ab12886c…` → v2.1 `66772bb1…` |
| ED-ID v2.3 | `b3907c69878342fa9e8b07cdf35e4ddd6b47823fe0388ee9fb1762235e01ea7b` (E§6 line) |
| CE-ID v2.3 | `f3c7c7dfaa7de050b991fd64e7d586e69c787707834116d827301ad332d24a91` (E§6 line) |
| CS-ID | unchanged `432de097…bdae2` |

## 2. Event inputs as Phase-6 TrustedInputs (new module; the binding conventions)

- **Semantic ids** (role → semantic_id), the frozen literals:

  | Role | Semantic id |
  |---|---|
  | `ear` | `event_ear_k_kp1` |
  | `days_since_ea` | `event_days_since_ea` |
  | `recent_ea_w<W>` (one role per W; 2 ≤ W ≤ 504) | `event_recent_ea_w<W>` |

- **The bridge-lag convention (binding; the ADM-13 lesson):** the value at frame row f
  uses only information dated ≤ date(f − 1), the same as D7c.
  - **Observability:** e is observed at row f iff K(e) + 1 ≤ date(f − 1).
  - **`ear` at row f** for e* = TRI_dated(K+1) / TRI_dated(K−1) − 1, where
    **TRI_dated(d) is read from the D7c TRI frame at the row whose prev is d** (the
    E-4-R2 rule).
  - **`days_since_ea`** = the trading-session count from K(e*) to date(f − 1),
    inclusive of date(f − 1) and exclusive of K(e*); always ≥ 1.
- **Lookback:** e* must satisfy K(e*) ≥ the session 504 trading sessions before
  date(f − 1). Otherwise every event primitive at row f is NaN.
- **Scenarios:** **K0** (the v1.3 `k_session`) and **K1** (for B_e events only, K = the
  next trading session after `k_session`; other events are unchanged).
  - **B_e** holds iff the index-header `acceptance_et` ∈ [15:45:00, 16:00:00) America/New_York
    on a trusted session (M1b §3).
  - The event panels are built separately for K0 and K1.
- **Evidence class:** CONSTRUCTED. Capability: daily, `has_knowledge_date=True`,
  POINT_IN_TIME, history = the frame rows (the fixture `_trusted` pattern).

## 3. Tasks, ownership, waves

| Wave | Task | Owned (only) | Depends on |
|---|---|---|---|
| 1 | **PQ23-A** core | `pqv2_core.py`, `tests/test_pqv2_core.py`, `tests/fixtures/pqv2/core/**` | — |
| 1 | **PQ23-B** events (new module) | **new** `smart_beta/dataset_v1/pqv2_events.py`, **new** `tests/test_pqv2_events.py`, **new** `tests/fixtures/pqv2/events/**` | — (reads v1.3 artifacts and D7c through existing read-only APIs) |
| 1 | **PQ23-G** oracle | `tests/pqv2_oracle/**`, `tests/test_pqv2_oracle_self.py`, `tests/test_pqv2_oracle_grammar.py`, **new** `tests/test_pqv2_oracle_events.py` | — |
| 2 | **PQ23-C** dependency | `pqv2_dependency.py`, `tests/test_pqv2_dependency.py`, `tests/fixtures/pqv2/dependency/**` | A, B |
| 3 | **PQ23-H** evaluation | `pqv2_evaluation.py`, `tests/test_pqv2_evaluation.py`, `tests/fixtures/pqv2/evaluation/**`, `tests/test_pqv2_grammar_crosscheck.py`, **new** `tests/test_pqv2_event_crosscheck.py` | A, B, C, G |
| 4 | planner: I1–I15 (v2.3), then PQ2-ADM (v2.3) | evidence documents only | all |

- **Forbidden to all:** D (`pqv2_assign_simple.py`), E (`pqv2_assign_sandwich.py`), F
  (`pqv2_envelope.py`), B-v2 (`pqv2_sources.py`, `pqv2_labels.py`), every pinned file,
  `spec/*`, `campaign*`, `edgar_*`, the docs.
- **If any of these must change → STOP.**

### PQ23-A — core
- **`AUTHORITY_V2_3`:**
  - `methodology_relative` = the v2.3 document; `base_methodology_*` = the v2.2
    amendment (`ab12886c…`);
  - `verify_authority(AUTHORITY_V2_3)` also runs `verify_authority(AUTHORITY_V2_2)`
    (the chain), so v2.1 is checked transitively.
- **Admitted authorities:** `ADMITTED_AUTHORITIES` = (V2_1, V2_2, V2_3).
  `CURRENT_AUTHORITY` stays V2_1.
- **Causes:** add the M1b cause constants and the precedence order (E§4 / M1b §2).
  `CauseSet` ordering follows the full M1b order.
- **Mutations:**
  - A23-M1: the chain is not verified;
  - A23-M2: CURRENT switched;
  - A23-M3: an M1b precedence pair swapped;
  - every existing mutation stays killed.

### PQ23-B — events
- **`load_event_layer(root, *, acq_id, ev_id)`:**
  - verifies `derived/edgar-<acq_id>/manifest.json` has `ev_id`;
  - reads `events.parquet` and `identity_map.parquet` (development only);
  - **refuses** any `reserve_sealed` path or any row with K ≥ 2023-07-03.
- **The cause sources per key (M1b):**
  - UNMAPPED (AMBIGUOUS / NO_FILINGS_IN_INTERVAL / NO_CANDIDATE);
  - CONTRADICTED (`audit_verdict`);
  - SOURCE_EXCLUDED (FOREIGN_FILER);
  - TIME_UNAVAILABLE: in-scope `EA_8K202` with null K. In-scope = filed ≥ 2015-01-02
    and within the key's interval, using the event's `acceptance_json_raw` date only as
    a *filing-date* proxy for scope, never as a time.
- **The builders:** `build_event_inputs(layer, tri_frame, sessions, *, scenario)` →
  `{semantic_id: TrustedInput}` for the requested roles, exactly per §2.
- **Tests:**
  - the bridge-lag alignment, with a frame whose labels are offset from the dates;
  - observability at K+1 = date(f − 1) vs K+1 > date(f − 1);
  - lookback edge 504 / 505;
  - B_e at 15:44:59 / 15:45:00 / 15:59:59 / 16:00:00;
  - DST;
  - K1 only for B_e;
  - each cause;
  - the firewall refusal;
  - determinism.
- **Mutations:**
  - B23-M1: the row label is read as the TRI date (bridge lag ignored);
  - B23-M2: an event is observed at K instead of K+1;
  - B23-M3: B_e is symmetric ±15 min;
  - B23-M4: FPI keys are given events;
  - B23-M5: a null-time event is silently skipped;
  - B23-M6: reserve rows are read.

### PQ23-G — oracle (independent; no `smart_beta` import)
- **Event worlds:** per-key event calendars and an oracle-side event-primitive
  evaluator using the same §2 conventions, implemented independently.
- **Classifier:** an EVENT_G classifier, plus a seeded generator
  (`PCG64(20261009)`, ≥ 2,000 EVENT_G members, depth ≤ 4).
- **The E§7 adversarial cases:**
  - an event inside vs outside the suspect window;
  - the lookback edge;
  - a pre-close B_e event;
  - a 16:05 acceptance;
  - each cause;
  - an FPI key;
  - a null-time event between e* and f;
  - an event primitive as a divisor (NOT_CERTIFIED);
  - an event primitive inside `standardize` (NOT_CERTIFIED).
- **Brute force:** membership ⊆, L ≥ min and turnover ≤, over correction grids
  touching the event window, per scenario.
- **Mutations:** G23-M1 … G23-M4, mirroring B23-M1, B23-M2, B23-M3 and the classifier
  divisor rule.

### PQ23-C — dependency
- **`certify(spec, *, authority)`:** for `AUTHORITY_V2_3`, recognize EVENT_G (E§3).
  - **Event roles** are the §2 semantic ids; every other role is the frozen D7c table.
  - **Constraints:**
    - at least one event primitive;
    - no event primitive as a divisor, under `lag` or inside a cross-sectional node;
    - SIMPLE_G terms under the v2.2 rules.
  - **Class** `EVENT_SIMPLE`.
- **Event-anchored windows:** `event_windows(layer, key, formation, scenario)` → the
  algebraic window (K(e*)−1, K(e*)+1] for `ear` (none for `days_since_ea` /
  `recent_ea`).
- **`input_uncertainty`:** accepts per-key event windows; reach uses the unchanged
  §8A algebraic rule on those windows. The M1b cause sources from B enter the cell cause
  set.
- **Authority behaviour:** v2.1 and v2.2 are unchanged; an event role under v2.1/v2.2
  → NOT_CERTIFIED.
- **Mutations:**
  - C23-M1: an event primitive is admitted as a divisor;
  - C23-M2: the event window is offset-uniform (not per key);
  - C23-M3: an M1b cause is dropped;
  - C23-M4: EVENT_G is accepted under the v2.2 authority.

### PQ23-H — evaluation
- **Declarations** resolve `AUTHORITY_V2_3`.
- **For EVENT_SIMPLE:**
  - build the K0 and K1 event inputs (B);
  - evaluate the factor panel **per scenario**; the B (D7b observed) and L statistics
    are computed per scenario;
  - **classification:** PASS requires PASS in **all** {T0, TS} × {K0, K1} combinations;
    otherwise per the existing precedence;
  - **diagnostics** record per-scenario cells and the cause sets.
  - When no B_e event affects any cell, K1 ≡ K0. This is recorded, not skipped.
- **Unchanged:** the v2.1/v2.2 paths (C1 golden equivalence; the PQ22 cross-check).
- **Cross-check** (`test_pqv2_event_crosscheck.py`, two tiers, PQ22-E2 pattern):
  - default tier: the first 50 EVENT_G members plus the adversarial set;
  - `PQV2_FULL_CROSSCHECK=1`: ≥ 2,000;
  - checks: production vs oracle class, membership ⊆, L ≥ min and turnover ≤, per
    scenario.
- **Mutations:**
  - H23-M1: a K1-only or K0-only pass is accepted;
  - H23-M2: the scenario dimension is collapsed;
  - H23-M3: an event cause is dropped from the diagnostics.

## 4. Barrier (planner; full I1–I15 against v2.3) and ADM

- **I1:** the chain identity (v2.3 → v2.2 → v2.1).
- **I3:** the EVENT_G census (full tier).
- **I11:** a rebuild includes one EVENT_G member under K0/K1.
- **I12:** the audit includes the v1.3 paths. 0 `reserve_sealed/edgar-*` reads.
- **I15:** the full suite is under the 900 s Phase-5A bound (the default tiers only).
- **PQ2-ADM (v2.3)** on real development data:
  - the event structural census: event-cell counts by cause, the B_e share of e*
    events, and the K1 ≠ K0 cell counts;
  - certification of the positive-control spec (`ear` held one month);
  - **no factor values or outcomes.**

## 5. Stop conditions (plus the base plans)

- a pinned or sealed file must change;
- D/E/F/B-v2 must change;
- the bridge-lag convention is ambiguous for a concrete case;
- a cross-check counterexample appears (a methodology defect: STOP; do not filter);
- the default-tier runtime would push the full suite past the Phase-5A bound.
