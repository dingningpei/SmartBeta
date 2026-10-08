# C2-LX — Campaign-2 ledger and executor (P-Q v2.1) (FROZEN 2026-10-08; user-approved option (a))

- **Authority:**
  - Campaign-2 precommitment `docs/campaign2_precommitment.md`
   ;
  - the C1-LX spec `worker_tasks/campaign1/task-c1-ledger-executor.md` and its errata
    E1–E6.1, applied **mutatis mutandis** except where this spec differs;
  - P-Q v2.1 H API (`pqv2_evaluation.build_pqv2_diagnostics`, `classify_pqv2`,
    `PQv2EvaluationDeclaration`).
- **Owned (new files only):**
  - `smart_beta/campaign/campaign2.py`;
  - `smart_beta/campaign/runner2.py`;
  - `tests/test_campaign2_executor.py`;
  - `tests/fixtures/campaign2/**` (including `mutations.json`).
- **Forbidden:**
  - `campaign1.py`, `runner.py`, `ledger.py` and `diagnostics_store.py`. These are
    **reused by import, unchanged**;
  - every `pqv2_*` module, D7a/b/c, P-Q v1 and docs.

  Campaign-1 stays permanently P-Q v1. If a reused module must change, STOP.

## 1. Differences from C1-LX (everything else follows C1-LX + E1–E6.1)

1. **Identity:**
   - `campaign2_id` = SHA-256 of the canonical Campaign-2 input manifest: the
     precommitment SHA, the four P-Q v2.1 identities, the Dataset/overlay/D7c/D7a
     identities, the factorspecs SHA, the partition, and the screen id;
   - hypothesis ids `campaign2_hypothesis_id = H("campaign2", definition_hash)`, using
     the E4 definition hashes.
2. **Evaluation:** per hypothesis and fold, call `build_pqv2_diagnostics` with a
   `PQv2EvaluationDeclaration` bound to the v2.1 identities (core constants). There are
   no PQB / P-Q v1 calls and no O2 guard.
3. **Screens:** `C2-DEV-SCREEN-v1` exactly as precommitment §4, computed from the
   persisted diagnostics cells (B-T0, B-TS, L-T0, L-TS: value, t_stat, n_obs, status).
   `passed = None` on any unavailable statistic or short support.
4. **Classification and disposition:** `classify_pqv2` per fold and scenario; the
   disposition by precommitment §5. Reasons collected, with the full cause sets copied
   from the diagnostics.
5. **Ledger:** the same append-only ledger (`ledger.py`, E3 terminal semantics). Record
   kinds:
   - `PRIMARY_ATTEMPT` per hypothesis: identities, the diagnostics hashes for both
     folds, screen verdicts, classifications, disposition, reasons, cause sets;
   - `CAMPAIGN_COMPLETE` / `CAMPAIGN_STOP`.

   Diagnostics are persisted **before** the referencing record (E6 write-ahead), through
   the unchanged `diagnostics_store`.
6. **Report:** a deterministic `report.json` / `report.md` with the precommitted
   operational endpoints (§6): the assessability counts, the per-classification cause
   sets, and the re-use disclosure verbatim. There are no robustness diagnostics.
7. **K-PIT:** a read-only preflight that requires the 9 Campaign-2 registration records
   (ESTIMAND_POLICY → PROGRAM_FREEZE → 7 HYPOTHESIS_FREEZE) to exist. The executor
   **never writes K-PIT**; registration is a separate, authorized transaction.
8. **Output:** `data_store/campaign2/<campaign2_id>/`. Campaign-1's directory is never
   touched.

## 2. Tests (synthetic only; the H synthetic dataset fixture)

- **Identity:** determinism; precommitment tamper → binding error; P-Q v2.1 identity
  bound and v2 rejected.
- **Screen rules** for every cell type, including `None` on NOT_ASSESSED L or short
  support.
- **Disposition precedence**, over all combinations: T0 PASS / TS FAIL → FAIL; any
  NOT_ASSESSED → NOT_ASSESSED; PASS only when all four pass.
- **Cause sets** carried into the ledger and report verbatim.
- **Write-ahead:** a crash between persist and append leaves no dangling reference.
- **Replay:** idempotent, with no extra slot; the terminal ledger is honoured.
- **K-PIT:** preflight failure → no evaluation and no ledger record; the executor never
  writes K-PIT.
- **Isolation:** the Campaign-1 directory and its modules are unchanged (hash
  assertions); 0 reserve or holdout reads (file-open audit).

## 3. Mutations (each must be killed)

| ID | Mutation |
|---|---|
| C2-M1 | T0-only (or TS-only) pass accepted |
| C2-M2 | L cells omitted from the screen |
| C2-M3 | NOT_ASSESSED L treated as a FAIL or PASS (not `None`) |
| C2-M4 | cause sets dropped from the ledger or report |
| C2-M5 | persist after append (write-ahead broken) |
| C2-M6 | executor writes K-PIT |
| C2-M7 | Campaign-1 directory written |
| C2-M8 | v2 identity accepted |
| C2-M9 | disposition precedence changed (FAIL before NOT_ASSESSED) |
| C2-M10 | re-use disclosure omitted from the report |
