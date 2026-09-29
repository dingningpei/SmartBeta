# Discovery Dataset v1.1 — D8 Clarification Addendum (NORMATIVE)

- **Status:** normative addendum to
  `docs/discovery_dataset_v1_1_d8_clarification.md` (2245cbb;
  `9a3f218d67c7e7456b2a5c9439a2d1fffe202e5beca3299afb31439b919b24c0`) and to
  `worker_tasks/dataset_v1/task-d8-reserve-firewall.md` (d577b43;
  `33455cc6bb5e185c9ea0075ac5e67cad267b89d9bd65fc960527738d94caf169`). It was
  decided by the user on 2026-09-29 (D8 IMPLEMENTATION HOLD resolution).
- **It changes exactly two things:**
  - it adds one row to the §7 historical disclosure;
  - it replaces the single-lock wording of spec §6 with the public-API
    procedure below.
- Everything else in the clarification and the spec is unchanged. No
  completed stage, no sealed phase and no Phase-10 file changes.

## A1. New historical interaction (added to clarification §7, verbatim)

| Stage | Interaction |
|---|---|
| D8 (implementation development) | an implementation-worker `grep` recursively scanned the bytes of `data_store` `*.json` files while searching for a bound hash. It did **not** scan `reserve_sealed` parquet files or `raw/*.json.gz`. It **did** scan JSON files including `qc_report.json`, which contains reserve-derived aggregate counts. The only `data_store` content returned by a matching result to the worker model was D3 `build.json`, which contains already-bound reserve structural row counts and file hashes. No reserve economic outcome or value row was returned to the worker model by this operation. Absence of additional matches is not proof of historical non-exposure outside this recorded event. |

- **Evidence:** the worker's Pi session log for the D8 worktree (the command
  `grep -rn "256ae777" <smart_beta parent> --include=*.py --include=*.md
  --include=*.json` and its returned output). The worker disclosed it in its
  completion report.
- **The distinction that matters:** bytes scanned by a process are not the
  same as matching contents surfaced to the model. D8 does **not** claim
  `qc_report.json` was unread.
- **Effect:** none on the evidence role. The footprint stays UNDETERMINABLE,
  overlap stays UNDETERMINABLE, and the role stays `UNKNOWN_EXPOSURE`. There
  is no ACCESS backfill and no HUMAN `EXPOSED` or `NOT_EXPOSED` declaration.
  The event neither establishes nor disproves human exposure.

## A2. Registration concurrency (replaces the single-lock wording of spec §6)

**The limitation.** The frozen public Phase-10 `KnowledgeLog.append` takes its
own non-reentrant `flock`, so one lock cannot span the read, the decision and
the append.
- **D8 does not implement an atomic compare-and-append** of "the log is empty"
  together with "append the genesis ARTIFACT".
- D8 uses only the public API. There is no private `_append_line`, no second
  append implementation and no Phase-10 change.

**The procedure: PRECHECK → PUBLIC APPEND → POSTCONDITION.**
1. **PRECHECK,** under `KnowledgeLog.locked(blocking=False)` for the read and
   decision only:
   - the target is the exact configured production path (in PRODUCTION mode);
   - the log is absent, or empty and valid (`read_records` verifies; a
     `TruncatedTail` or integrity error is raised and never repaired);
   - the D8 payload, footprint and bindings are rebuilt and match the frozen
     bindings;
   - the expected registration is genesis (`seq 0`, zero `prev_hash`,
     `program_id None`).
   - **Idempotence:** if record `seq 0` is an `ARTIFACT` whose `source_label`,
     `packaging_hash`, payload and footprint are identical, with
     `program_id None` and zero `prev_hash`, the result is
     **`ALREADY_REGISTERED`**, with no append.
     - Any other record carrying the D8 `source_label` gives `D8ConflictError`.
     - A non-empty log without that exact genesis match gives
       **`D8HoldError`** ("D8 PRODUCTION APPEND HOLD"), with no append.
     - An arbitrary `seq 0` record is never read as D8 success.
2. **PUBLIC APPEND:** exactly one call to the public `KnowledgeLog.append`,
   without an outer lock and without bypassing its own lock.
3. **POSTCONDITION:** re-read and verify the log (`read_records`). It must hold
   **exactly one** record, and that record must be:
   - at `seq 0` with zero `prev_hash` and `program_id None`;
   - kind `ARTIFACT`;
   - carrying exactly the expected payload, `packaging_hash`, footprint and
     disclosure/map/calendar bindings.

   Only then are `REGISTERED` and the `record_hash` reported.

**Failure semantics.**
- If the postcondition fails (for example, a concurrent writer raced the
  append), the result is **`D8ProductionConcurrencyError`** ("D8 PRODUCTION
  REGISTRATION CONCURRENCY FAILURE").
- There is no rollback, deletion, rewrite, chain repair or automatic retry.
  The log is preserved exactly for forensic review.

**Operational precondition (governance, not a code guarantee).** A production
genesis registration requires a **single operator and no concurrent
Knowledge-PIT writer**. The code does not claim to prevent every concurrent
writer.

**Authorization.** The production append remains a separate explicit
authorization.
