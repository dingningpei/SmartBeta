# Dataset v1.2 — P-Q v2 integration and admission barriers (SPECIFICATION FROZEN)

- **Status:** specification only. Authorized by "P-Q v2 IMPLEMENTATION-SPEC DESIGN", on
  master `0777a10`.
- **Companion documents:**
  - plan: `docs/discovery_dataset_v1_2_pq_v2_implementation_plan.md`;
  - methodology: `docs/discovery_dataset_v1_2_pq_v2_methodology.md` (`a31ed9c4…a56d`).
- **Who runs it:** the planner, after PQ2-A…H are all merged at reviewed exact SHAs.
- **Environment:**
  - all runs scrub the eight provider credentials process-locally;
  - the netguard is active (`scratchpad/netguard`);
  - the file-open audit (`PQ_AUDIT_LOG`) runs for I12 and ADM;
  - no reserve, holdout, `raw/`, `reserve_sealed/` or `dsc_evidence` access;
  - no K-PIT write.

## Part 1 — Software integration barrier (PQ2-I1 … PQ2-I15)

| # | Barrier | Evidence (re-derived by the planner, never taken from worker summaries) | PASS condition |
|---|---|---|---|
| I1 | Frozen identity | Recompute the document SHA-256 and the CS/ED/CE line hashes from the methodology document; compare with `pqv2_core` constants; run `verify_authority` on master; mutate one byte of a copy of the document → `PQv2BindingError` | all equal; the tamper is rejected |
| I2 | P-Q v1 non-regression | `git diff 0777a10..HEAD --stat` touches no v1 file (`quarantine.py`, `pq_evaluation.py`, `pq_reporting.py`, `gap_hac.py`, D7a/b/c, the Campaign modules); all v1, PQ, Campaign-1 test modules pass; v1 overlay `verify_overlay` passes on the admitted overlay (metadata/hash verification only) | no v1 file changed; tests green |
| I3 | Dependency correctness | `test_pqv2_dependency` passes; the frozen M§10 table fixture equals `dependency_windows` output for all 7 expressions; uncertified expressions → `PQV2_EXPRESSION_NOT_CERTIFIED` | exact equality |
| I4 | Correction set | S2 constants; Q_s for m < 0.5, 0.5 ≤ m ≤ 1.5 and m > 1.5; `contains` at the interior and at the boundaries; non-positive or non-finite m raises | all pass |
| I5 | Label envelope | Oracle-equality tests: single suspect, multiple suspects, formation endpoint, ending endpoint, interior cancellation, MISDATED, EMPTY, INCOMPLETE, reserve UNKNOWN, T0/TS | analytic = oracle extremes (equality where the envelope is exact; containment otherwise, as specified) |
| I6 | Simple assignment coverage | Oracle containment: every oracle-reachable top/bottom set is in the simple set; enumerated min LS ≥ L; max TO ≤ TO^hi | 0 violations |
| I7 | H4/H5 sandwich coverage | Oracle containment over the E case list (crossings, boundary crossing, volume rank, coupled M/V, ties, σ = 0, NaN) | 0 violations; tightness gap reported |
| I8 | Turnover coverage | Oracle max turnover ≤ TO^hi for both classes; first-period convention = 0; changed^B reconciles with `evaluation/portfolio` | 0 violations; exact reconciliation |
| I9 | Oracle case census | The 18 M§16 cases each exist as a named test that invokes `pqv2_oracle` and an analytic module; G's self-tests (semantic cross-checks against `transforms`, `portfolio_sort`, D7a-style compounding, D7c-style TRI) pass; `pqv2_oracle` imports no `smart_beta.dataset_v1.pqv2_*` (AST check) | 18/18 present; independence AST check passes |
| I10 | Mutation score | The planner re-runs every `tests/fixtures/pqv2/*/mutations.json` with `scratchpad/pq_mutrun.py` (CONTROL included) | CONTROL passes; every non-equivalent mutation is killed; each equivalent one has an approved justification |
| I11 | Deterministic rebuild | Synthetic end-to-end `build_pqv2_diagnostics` run twice in fresh processes → identical `diagnostics_hash` and identical persisted bytes through `diagnostics_store.persist` to a temp dir | identical |
| I12 | Reserve firewall | Synthetic fixtures with post-`DEVELOPMENT_END` rows → `PQv2InputError`; e′ = DEVELOPMENT_END → UNKNOWN → period `PQV2_RESERVE_UNKNOWN`; MISDATED within 5 sessions of `DEVELOPMENT_END` → UNKNOWN; the file-open audit during the v2 test modules shows no path under `reserve_sealed/`, `raw/` or `dsc_evidence` | all pass; 0 forbidden opens |
| I13 | K-PIT no-write | `knowledge_pit/knowledge.jsonl` SHA-256 = `190d53f09240acbd86e6256d6725d082c72ea19928843b5ec7b4cc11ad18f6a2` before and after the full barrier | unchanged |
| I14 | Campaign historical non-mutation | `data_store/campaign1/ddd152dc…/` ledger `85bde1e7…`, `report.json` `37082653…`, `report.md` `2079ec6f…` unchanged; no `diagnostics/` created there | unchanged |
| I15 | Full suite | Full `pytest` under scrub and netguard | 0 failures (the existing skip count is unchanged and explained) |

**PASS statement**, recorded in `docs/discovery_dataset_v1_2_pq_v2_software_barrier.md`
(created by the planner at barrier time, not now):

> **P-Q v2 SOFTWARE IMPLEMENTED — PQ2-I1…I15 PASS**

This statement certifies the **software only**. It is not empirical certification, it
does not certify data quality, and it is **not** admission for use.

## Part 2 — Admission barrier PQ2-ADM (needs its own authorization, after Part 1)

**Purpose:** bind the software to the real admitted Dataset-v1.2 artifacts **without
producing any factor outcome or statistic**.

| # | Check | PASS condition |
|---|---|---|
| ADM-1 | `verify_authority` + pinned code on master | pass |
| ADM-2 | Real `load_source_set` over the development period. Report: number of flags, m_s range, MISDATED candidates by action type, number of UNKNOWN flags. **No factor values, labels envelopes, L or B statistics are computed** | it completes; counts are deterministic across 2 runs; the D7a `ca_event_count` cross-check passes |
| ADM-3 | Firewall audit on the ADM-2 run (`PQ_AUDIT_LOG`) | 0 forbidden opens; no read ≥ `RESERVE_START` |
| ADM-4 | Certification table: `certify` on the seven `campaign1_factorspecs.json` specs gives the M§15 classes; one perturbed spec gives NOT_CERTIFIED | exact |
| ADM-5 | Resource bound: wall time and peak RSS of ADM-2, plus synthetic full-size sub-expression realization timing (no real factor realization) | recorded; within the stated bound or STOP |
| ADM-6 | I13/I14 re-check | unchanged |

**PASS statement:**

> **P-Q v2 ADMITTED FOR PROSPECTIVE DEVELOPMENT USE**

It carries these explicit non-claims:
- P-Q v2 is a declared correction-sensitivity model, not truth containment;
- it does not bound the true return;
- it certifies no data quality;
- Gate-6 remains INCONCLUSIVE;
- security identity is not sensitivity-tested;
- one misdating at a time; the H4/H5 sandwich is a conservative superset;
- only the seven CE-certified expressions; sign +1 only; metrics NET/GROSS long-short
  only;
- **no reserve, holdout or confirmation use**;
- Campaign-1 stays P-Q v1 and permanently 7 NOT_ASSESSED;
- no Campaign is created by admission.

**Factor and L outcomes may first be produced only inside a separately precommitted and
authorized future Campaign.**
