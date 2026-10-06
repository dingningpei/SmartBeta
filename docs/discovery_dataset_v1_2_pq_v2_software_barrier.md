# Dataset v1.2 — P-Q v2 software integration barrier evidence (PQ2-I1 … PQ2-I15)

- **Status:** planner barrier evidence. Authorized by "P-Q v2 FINAL I1-I15 INTEGRATION
  BARRIER — AUTHORIZED".
- **Master:** before = `ea644286cc8880fa8f871182f363be611e40744c`; this document is the
  only file committed by the barrier.
- **Authority chain:**
  - methodology `docs/discovery_dataset_v1_2_pq_v2_methodology.md` (`a31ed9c4…a56d`);
  - plan `docs/discovery_dataset_v1_2_pq_v2_implementation_plan.md`;
  - barrier spec `docs/discovery_dataset_v1_2_pq_v2_integration_barrier.md`;
  - errata `worker_tasks/pq_v2/task-pq2-g-oracle-erratum-e1.md` (`cf841768…d535`;
    parent `cf034f41…c784c7`) and `worker_tasks/pq_v2/task-pq2-d-simple-assignment-erratum-e1.md`
    (`3a11e46c…1f62`; parent `9bb96668a6db…e4d7c1`);
  - repair PQ2-D-R1 (`5f768ea`, an implementation repair; no spec change).
- **Merged tasks (worker commits):**

  | Task | Worker commit(s) |
  |---|---|
  | A | `e2a1a24` |
  | G | `366c789`, `ac4d56f`, G-E1 `b70d9d0` |
  | B | `a14b8c3` |
  | C | `1dc6fc7` plus corrective `5cd0dcc` |
  | D | `f3c36ed`, R1 `5f768ea` |
  | E | `485f85b` |
  | F | `504ead3`, `9a5177a`, `ebc3288` |
  | H | `5cdc0b5` |

- **Scope of this certificate:** **SOFTWARE IMPLEMENTATION ONLY** (see Non-claims).

## Identities (recomputed)

| Identity | Value | Result |
|---|---|---|
| Methodology | `a31ed9c49cd3a03208ae28539a8b5f5531916e31fac32bbd60e2af07746fa56d` | unchanged |
| CS-ID | `432de09746bbc4c16c332d662c24d6d7369787d55e8b704e3a521494b73bdae2` | unchanged |
| ED-ID | `e979924ee4efc365155d8a79089fb940a28ebede7099f67373f0d654bd479aeb` | unchanged |
| CE-ID | `605fec93812d4165b6bf455a9fb453cd7c0c6c4a9f5a2a0fc418b53b0d0dbaaf` | unchanged |

CS/ED/CE were recomputed twice: by `pqv2_core.recompute_identity_hashes()`, and by an
independent planner extraction of the first fenced block after each identity header.

## Environment and tooling disclosure

- **Every barrier process:**
  - ran under the canonical interpreter (`main/.venv/bin/python`);
  - scrubbed the eight provider credentials process-locally (`env -u …`);
  - ran under a network guard (`sitecustomize.py`): every non-AF_UNIX `connect`,
    `create_connection`, `getaddrinfo` and `gethostbyname` raises. Effectiveness was
    **proven before use** (two outbound connection attempts were blocked, and no
    credential was visible inside the process).
- **Disclosure: the earlier guard was missing.** At the start of this barrier the planner
  scratch guard directories (`scratchpad/netguard`, `scratchpad/pqbarrier/guard`) and
  the planner mutation runner `scratchpad/pq_mutrun.py` had been deleted by a periodic
  temp-directory cleanup.
  - The guard was therefore **not active** in the planner's own re-runs since it
    vanished, during the Wave 1–5 reviews.
  - The statements "network blocked" in those wave reports were **not enforced** for the
    planner re-runs. (The tests are synthetic and make no network calls by design, but
    that was not mechanically guaranteed.)
  - **All results below were re-derived under the rebuilt, verified guard.**
- **Recreated tooling** (scratchpad; SHA-256):
  - `pq_mutrun.py` `1abd45144e8b838e0d6fef7aabaa5815a93e91aee01c238a951d74af853bbfb3`:
    the same CLI; runs each mutation in an isolated temp copy; an edit's `old` text must
    match exactly once, else `APPLY_FAILED`.
    - It reproduced the prior oracle (7/7) and dependency (11/11) results exactly.
  - guard `sitecustomize.py` `6697069a5d5b5cba1c9da59d236efcde428e9b680e9dba1f76073d96fac38368`:
    blocks networking; optional `PQ_AUDIT_LOG` file-open audit hook.
  - `i11_replay.py` `b6c3fb6bf6861b4b94300e12eeea3ae9a88f89f7128526f97a7f3bf196297031`:
    the I11 driver.

## Barrier results

**I1 — Frozen identity: PASS**
- doc SHA-256 = constant; independent CS/ED/CE = constants = frozen literals;
  `verify_authority()` passes on master; 11/11 pinned code hashes match.
- **Tamper tests:** in a temp copy, one byte flipped in the methodology, and separately in
  a pinned file (`diagnostics_store.py`), → `PQv2BindingError`; the untampered copy
  passes.
- **Errata bindings:** both parent specs are unchanged, and each erratum records its
  parent hash.
- **Entry tests:** the 11 authority/identity/tamper tests across core, F and H pass.

**I2 — P-Q v1 non-regression: PASS**
- `git diff 0777a10..HEAD` touches **0** files among the P-Q v1 modules,
  `gap_hac.py`, D5/D7a/D7b/D7c, `smart_beta/{campaign,spec,evaluation,engines}`. The only
  changes are `pqv2_*` files, docs and worker tasks.
- The P-Q v1 / D7 / Campaign-1 test modules pass (594 tests).
- `quarantine.verify_overlay` passes on the admitted overlay (hash verification only), and
  its rule hash equals the Campaign-1 expectation. The file-open audit shows 0 opens
  under `reserve_sealed/`, `raw/` or `dsc_evidence`.

**I3 — Dependency correctness: PASS**
- `test_pqv2_dependency` passes (26).
- **Frozen table:** `frozen_table.json` was checked by the planner against M§10 entry by
  entry:
  - H1 (−253, −22]; H2 (−127, −22]; H3 (−22, −1];
  - H4/H5 algebraic as H1/H2, with dv short (−22, −1] and dv long (−253, −1],
    cross-sectional;
  - H6/H7 numerical over (−253, −22] and (−127, −22].
- `test_frozen_table_matches_dependency_windows` passes.
- **Certification:** H1–H3 and H6–H7 → SIMPLE; H4–H5 → STD_X_RANK. Sign −1, a perturbed
  window and an extra node → NOT_CERTIFIED; no structural-similarity fallback; Prop. 2
  prefix cancellation is tested.

**I4 — Correction set: PASS**
- **S2 / Q_s:**

  | m | S2 | Q_s |
  |---|---|---|
  | 0.2 | [0.2, 1.5] | [1, 7.5] |
  | 0.5 | [0.5, 1.5] | [1, 3] |
  | 1.0 | [0.5, 1.5] | [0.5, 1.5] |
  | 1.5 | [0.5, 1.5] | [1/3, 1] |
  | 4.0 | [0.5, 4] | [0.125, 1] |

  Q_s contains 1 in every case; S2(4) contains the partial value 2.0; non-positive and
  non-finite m are rejected.
- **Wave-2 repair:** an exhaustive scan of all 24 oracle worlds found 684 flag-states,
  41 of them misdated, and **0** misdated states with branch ≠ NONE or λ ≠ 1.
- G-M7 KILLED; C-M8 KILLED.

**I5 — Label envelope: PASS**
- `test_pqv2_labels` and `test_pqv2_sources` pass (40).
- `test_oracle_equality_required_m16_cases` runs the analytic `label_envelope` against the
  oracle on candidate_e_single_suspect, multiple_suspects, formation_endpoint,
  ending_endpoint, interior_close_cancellation, misdated_paired_ca and
  tri_prefix_cancellation (exact equality), and empty_label (containment).
- Plus the union regressions (the four E1 configurations, a genuine suspect with
  m_s = 2.0 and φ_d = 0.9), s = f / next(e*) Q⁻¹, terminal COMPLETE, INCOMPLETE → NaN,
  and the reserve-boundary UNKNOWN.

**I6 — Simple assignment coverage: PASS**
- `test_pqv2_assign_simple` passes (54).
- **Full Prop. 4 coverage (5 qualifying worlds, all preconditions satisfied):**
  `d_single_suspect`, `d_multiple_suspects`, `d_nan_member`, `d_neg_short`,
  `d_small_group_count` (q = 0.05, n = 19–20; 2–4 distinct reachable top and bottom
  sets; 9 reachable assignments with two suspects). The anti-vacuity guard (≥ 4) passes.
- **Excluded under `PQV2_WINSOR_BLOCK`:** `simple_assignment_movement`, `ties`, `nan`,
  `turnover_upper_bound`, `full_window_simple` (q = 0.2 with n ≤ 10, so
  ⌊q(n−1)⌋+2 = 3 already reaches s₁).
- **Recorded limitation (not upgraded):**
  - the original G simple census contains **no** world inside the Prop. 4 certified
    domain;
  - theorem-level containment evidence comes from five D-built adversarial worlds built
    with G's public API;
  - **no H6/H7-style `volscaled` expression is directly exercised at D's theorem layer.**
    C certifies the H6/H7 expressions and maps them to the uncertain-member
    representation; D's semantics are conditional on that representation.
  - This is **not** direct H1–H3/H6–H7 oracle coverage at D's layer.

**I7 — H4/H5 sandwich coverage: PASS**
- `test_pqv2_assign_sandwich` passes (38).
- Oracle containment on the five E worlds (clean-clean crossing via μ, suspect boundary
  crossing, volume-rank movement, coupled M/V, full window):
  - reachable assignments within C ⊆ T ⊆ P;
  - reachable scores within [g^lo, g^hi];
  - oracle `ls_min` ≥ the analytic bound.
- **Full cross-section:** stocks outside the universe change μ (E-M3) and a volume rank
  (E-M6).
- σ = 0 fails closed; average-rank and final-factor ties; winsorization block pass, fail
  and cap ties are covered.
- **F-layer non-vacuous coverage:** three block-passing suspect worlds
  (`sandwich_suspect_g2`, `_g3`, `_two_formation`) reach 2–3 top sets; T0, TS and
  turnover coverage hold; the anti-vacuity guard passes.
- **Exact equality is not claimed:** `rule = SANDWICH_CONSERVATIVE_SUPERSET`.

**I8 — Turnover / cost coverage: PASS**
- The envelope turnover/cost tests pass. They cover:
  - simple D_t = min(|U|, G·|A_t|), taken from D;
  - sandwich ambiguous-band D_t, taken from E;
  - first-period TO_hi = 0, mirroring `evaluation/portfolio._turnover_series`, with
    changed^B reconciled exactly;
  - TO_hi = min(1, (changed^B + D_t + D_{t−1}) / |U_{t−1} ∪ U_t|);
  - cost = (bps/10⁴)·TO_hi, subtracted only for the NET metric, adverse direction;
  - oracle `turnover_max` ≤ TO_hi;
  - mutations F-M1/F-M2/F-M3/F-M4/V2-M16 killed.

**I9 — Oracle census and independence: PASS**
- All 18 M§16 cases exist in `pqv2_oracle.CASES` (24 total). Each is exercised by a test
  module that uses both the oracle and an analytic module: label/TRI cases in
  `test_pqv2_labels`; assignment cases in `test_pqv2_assign_simple`,
  `test_pqv2_assign_sandwich` and `test_pqv2_envelope`.
- G self-tests pass (27).
- **Structural independence:**
  - an AST scan of `tests/pqv2_oracle/*.py` finds 0 `pqv2_*` or `smart_beta` imports;
  - importing the oracle in a fresh process loads 0 `smart_beta` modules;
  - 0 production files reference `pqv2_oracle`.

**I10 — Mutation score: PASS**

| Family | Mutations killed | Notes |
|---|---|---|
| core | 8 | |
| oracle | 7 | |
| dependency | 11 | |
| labels/sources | 12 | killed by the labels test file; the source mutations are also killed by the sources file |
| simple | 11 | |
| sandwich | 11 | |
| envelope | 22 | |
| evaluation | 22 | plus H-M13 and H-M14 below |

- Every CONTROL passes.
- **H-M13** (duplicate-period guard removed): **equivalent, revalidated on master**. With
  the guard disabled, the inputs L = [d1, d2, d2] vs B = [d1, d2, d3], [d1, d1] vs
  [d1, d2], [d1, d2, d2] vs [d1, d2], and [d2, d2] vs [d2] are all rejected by the
  following B/L identity/order check (`b_lookup` is a `Mapping`, so its keys are unique).
- **H-M14** (typed reason collapsed): a **mutation-fixture ambiguity / tooling defect**,
  **not** an equivalent mutation and **not** a production survivor.
  - The fixture `old` text occurs twice, so the canonical runner reports APPLY_FAILED.
  - Manual application against the full H test file killed every variant: both
    occurrences (19 failed), first only (18 failed), second only (15 failed).
  - The frozen barrier spec does not authorize a fixture repair, so the ambiguity is
    preserved and recorded here.

**I11 — Deterministic rebuild: PASS**
- The synthetic end-to-end `build_pqv2_diagnostics` (C1-H1 simple and C1-H4 sandwich)
  ran in two fresh processes with different `PYTHONHASHSEED` and working directories,
  and produced byte-identical output:
  - C1-H1 `80bd59b1691b0310367957bc18eddb0d77ed888c5b291fe85e1805defb0b55c3`;
  - C1-H4 `4d13053b821b5e2c3f692efad57427954820695e10326af118d9226f0a8526e6`.
- The persisted bytes through the unchanged `diagnostics_store.persist` hash to the same
  values, and `persist` returns them.
- The four identities are bound in the payload; no temp path or cwd leaks into it.

**I12 — Reserve firewall: PASS**
- The full P-Q v2 suite (317 tests) ran under the `PQ_AUDIT_LOG` file-open audit: 9,716
  opens, with **0** under `reserve_sealed/`, `raw/`, `dsc_evidence`, `knowledge_pit` or
  `data_store`.
- 15 boundary tests pass:
  - sources: firewall-adjacent flags → UNKNOWN; `next_beyond_development`; file-open
    audit;
  - labels: e′ = DEVELOPMENT_END → UNKNOWN;
  - C: UNKNOWN flags affecting a cell or a dv cell → UNKNOWN;
  - D and E: fail closed, E over the full cross-section;
  - F: possible-set vs middle-group distinction, F-M5/F-M17 kills;
  - H: typed reason preserved.

**I13 — K-PIT no-write: PASS**
- `knowledge_pit/knowledge.jsonl` SHA-256
  `190d53f09240acbd86e6256d6725d082c72ea19928843b5ec7b4cc11ad18f6a2` is identical before
  and after the barrier.
- No Campaign attempt, no slot, no K write.

**I14 — Campaign historical non-mutation: PASS**
- `data_store/campaign1/ddd152dc…/`: ledger `85bde1e7f59b4694…`, `report.json`
  `370826532016a6b2…`, `report.md` `2079ec6fd2bf86bb…`, byte-identical before and after.
- No `diagnostics/` directory exists. Campaign-1 remains permanently 7 NOT_ASSESSED; it
  was not recomputed.

**I15 — Full suite: PASS**
- The P-Q v2 suite: 317 passed.
- The repository suite: **5403 passed, 1 skipped, 0 failed** (the skip count is unchanged
  from all previous waves), under the guard and with credentials scrubbed.

## Cross-cutting verification

- **Same-period invariant: PASS.**
  - `_estimate_l` requires B and L period identities and order to be identical (duplicate
    or missing periods raise).
  - T_B = B's finite periods; every t ∈ T_B needs an assessed, finite L_t, else
    `PQV2_L_PERIOD_NOT_ASSESSED`.
  - The Phase-7 `long_short_mean_tstat` runs on the full series.
  - **F/H contain no cross-period** `nanmean`, `dropna`, `fillna`, `ffill`/`bfill` or
    `gap_hac` (F's `_nanmean` is the within-period NaN-denominator leg mean used only
    for B's observed LS in the L ≤ B check).
  - Tests: first/middle/last missing L, NaN L, B-numeric/L-NOT_ASSESSED, partial-period
    estimator rejected, `gap_hac` not imported, plus mutations H-M1/M2/M17–M20.
- **T0/TS separation: PASS.** They are computed in F, classified in H and persisted as
  separate cells; no combination construct exists; H-M21 is killed.
- **Vocabulary boundary: PASS.**
  - The class vocabulary (`SIMPLE` / `STD_X_RANK`) is used only for certified-class
    values.
  - The assignment rule is exactly `AssignmentRule.SIMPLE_EXACT` (D) or
    `SANDWICH_CONSERVATIVE_SUPERSET` (E); F maps each class to one rule with a strict `!=`.
  - The legacy `SIMPLE` rule tag fails closed (`PQV2_EXPRESSION_NOT_CERTIFIED`), in both
    F and H tests; D-M11 and F-M19 are killed; no other leak was found.
- **B FAIL / L PASS: semantic note.**
  - The frozen typed status `INVARIANT_VIOLATION_FAIL_CLOSED` is unchanged. It is
    returned (never raised), with `advancement_permitted = False`, and `accept` and
    `confirmation` are always False.
  - **Its name does not denote a proven mathematical violation.** Periodwise
    L_t ≤ LS^B_t does not imply monotonicity of the HAC t-statistic or of any screen, so
    this outcome can occur legitimately (for example, lower variance of L). The frozen
    consequence is fail-closed. H-M4/H-M5 are killed.
- **Diagnostics sufficiency: PASS.** `PQv2Diagnostics` carries:
  - the four identities and `pqv2_code_sha256`;
  - `certified_class`, `certified_hypothesis`, `assignment_rule`, `relaxations`;
  - separate `b_periods_t0/ts` and `l_periods_t0/ts`;
  - per-cell scenario, estimand, estimator, ordered period identities with per-period
    statuses, values and typed reasons, support, n_obs, status, reason, value and t;
  - cost/turnover inputs (`cost_bps`, `cost_rate`, per-period records);
  - upstream hashes (`b_diagnostics_hash`, `factor_panel_sha256`,
    `dataset_identity_hash`, `overlay_pq_hash`, `declaration_hash`);
  - `non_claims`;
  - `screen_id`/`screen_policy_hash` are carried from the D7b base declaration only.

  It is content-addressed with no timestamps and round-trips through the unchanged
  `diagnostics_store`.

## Non-claims

- This barrier certifies that the integrated A–H software implements the frozen P-Q v2
  methodology correctly, conservatively and fail-closed **on synthetic evidence**.
- It does **not** certify: real-data admission (PQ2-ADM, not run), empirical usefulness,
  data quality, alpha, Campaign advancement, confirmation eligibility, reserve use or
  production readiness.
- P-Q v2 is a declared correction-sensitivity model, not truth containment. Gate-6 remains
  INCONCLUSIVE; security identity is not sensitivity-tested; one misdating at a time; the
  H4/H5 sandwich is a conservative superset; only the seven CE-certified expressions,
  sign +1, NET/GROSS long-short.
- **No real factor values, real L_t, real P-Q v2 diagnostics or real screens were
  computed.** No reserve or final holdout was accessed, no K-PIT write was made, and no
  network call came from barrier tests.

## Result

**P-Q v2 SOFTWARE IMPLEMENTED — PQ2-I1…I15 PASS**
