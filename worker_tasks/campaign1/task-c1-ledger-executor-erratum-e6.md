# C1-LX-E6 — F-2 reporting/provenance repair: write-once content-addressed diagnostics artifacts (frozen)

- **Status:** FROZEN. Authorized by "F-2 REPORTING REPAIR — SPEC + IMPLEMENTATION
  AUTHORIZED", on master `181cf63`. Merging needs a separate authorization.
- **Origin:** post-Campaign-1 finding **F-2**. Campaign evaluation computes complete
  diagnostics objects, but the durable artifacts keep only their hashes, so the
  precommitment §15 report cannot be rebuilt without recomputation.
- **Nature:** **reporting/provenance defect only.** It is **not**:
  - a P-Q methodology change;
  - a statistical change;
  - a disposition change;
  - a data-quality change;
  - a factor change;
  - a reconstruction authorization.
- **Effective authority:** C1-LX task (`118bcf41…`) + E1–E5 + **E6**; precommitment §15
  (`15f70060…`).
- **Owned files:**
  - `smart_beta/campaign/diagnostics_store.py` (new);
  - `smart_beta/campaign/campaign1.py` (persistence calls in the executor + report
    generation only);
  - `tests/test_campaign1_diagnostics_store.py` (new);
  - `tests/test_campaign1_executor.py` (E6 adjustments only);
  - `tests/fixtures/campaign1/diagnostics/**` (new, including `mutations.json`).
- **Must not modify:**
  - `pq_evaluation.py`, `delisting_bridge.py`, `quarantine.py`, `pq_reporting.py`,
    `gap_hac.py`;
  - `runner.py`, unless strictly needed (then STOP and report);
  - Phase-6/7/8/9/10 code; documents; dataset artifacts;
  - the historical Campaign-1 artifacts.

## E6.1 What is persisted (derived from repository authority; nothing invented)

For every evaluation the executor already builds, persist exactly the two existing
diagnostics objects:

| Object | Content address (already bound) | Canonical payload |
|---|---|---|
| PQB `PQDiagnostics` (one per fold run) | `diag.diagnostics_hash`, already recorded in the ledger (`diagnostics.is` / `diagnostics.oos`) | `delisting_bridge.canonical_json(diag.to_dict())` |
| D7b `DevelopmentDiagnostics` attached as `diag.b_diagnostics` | `diag.b_diagnostics_hash` (inside the PQDiagnostics payload) = `diag.b_diagnostics.diagnostics_hash` | `delisting_bridge.canonical_json(diag.b_diagnostics.to_dict())` |

**Coverage:** every primary attempt (TOP_500 IS and OOS) **and** every diagnostic
evaluation (TOP_200 and TOP_1000, IS and OOS). That is up to 6 PQDiagnostics and 6
D7b objects per hypothesis.

**Contents:**
- `PQDiagnostics` carries:
  - the four cells (B-T0, B-TS, A-T0, A-TS), each with value, raw_value, t_stat,
    status, n_obs, n_index and availability_mask_hash;
  - support counts; cs3 and cs3_status;
  - quarantine exposure (`q_b`, `q_s`, `q_l`, `n_flagged_formations`);
  - `residual_cs_cells` (the O1 structural count), `liquidity_exposed`,
    `n_interior_b_missing`;
  - delisting exposure (`e_s`, `b`); not_assessed; the formation index.
- `DevelopmentDiagnostics` carries the B T0/TS values, t-statistics and n_obs, the
  e_s/e_l/b counts, and the formation accounting and purged cells.
- Together with the ledger's screen verdicts, classification records, dispositions,
  slot/α/critical-value and O2 counts, this covers precommitment §15.

**Out of scope (disclosed, not F-2):** precommitment §7 "secondary diagnostics"
(gross long-short, IC/rank-IC, Sharpe) are **not computed** by the frozen executor.
That is an evaluation-scope gap, not persistence. E6 neither computes nor invents them.

**No P-Q module is modified.** `b_diagnostics` is already attached by PQB and only
read.

## E6.2 Canonicalization and content address

- **Bytes:** `payload = delisting_bridge.canonical_json(obj.to_dict()).encode("utf-8")`.
  This is the existing serializer: sorted keys, compact separators, ASCII, Python
  round-trip float repr, dates as ISO strings, non-finite floats as `null`, ints as
  ints, enums and objects via `to_dict`. No new serializer is introduced.
- **Address:** `sha256(payload)` **must equal** `obj.diagnostics_hash` before any
  write. On inequality, raise `C1DiagnosticsError`.
- **Location:** `<campaign_dir>/diagnostics/<diagnostics_hash>.json`, where
  `campaign_dir` is the ledger file's parent directory.
- **Display:** pretty or display formatting never touches artifacts or identity.

## E6.3 Atomic write-once (`diagnostics_store.persist(obj, directory) -> hash`)

1. Compute the payload and hash, and assert the hash equals `obj.diagnostics_hash`.
2. If `<hash>.json` exists, read it and require `sha256(bytes) == hash`. On success
   this is idempotent: return. On failure raise `C1DiagnosticsError`; **never
   overwrite or repair**.
3. Otherwise:
   1. write the payload to a uniquely named temp file `.<hash>.<pid>.<random>.tmp` in
      the same directory;
   2. `flush` and `fsync`;
   3. `os.link(tmp, final)`. This is atomic and fails if the final file already
      exists; in that case go to step 2's verification;
   4. `unlink(tmp)`;
   5. `fsync` the directory.

**Consequences:**
- A crash can leave only `*.tmp` files. These never match the content-address
  pattern, are ignored by readers, and may be safely deleted.
- A final file is always complete and fsynced before it becomes visible.
- Two different payloads can never share a name: the name is the hash, and existing
  files are verified, never replaced.

## E6.4 Ordering, crash and replay (write-ahead)

**Ordering:**
- In the executor, **all artifacts an attempt or diagnostic record will reference are
  persisted, and the directory fsynced, BEFORE that record is appended to the
  ledger.**
- Therefore a ledger record never references a diagnostics hash whose artifact is
  not durable.
- An artifact without a ledger record (a crash between persisting and appending) is
  an orphan. It is harmless, never read as evidence, and the existing replay
  semantics decide what happens next.

**Failure handling:**
- A `C1DiagnosticsError` (persistence or verification failure) is **not** a new
  disposition. It follows the existing C1-LX §8 untyped-failure rule unchanged:
  `CAMPAIGN_STOP INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE`, then re-raise.
- The ledger becomes terminal (E3.2).
- No attempt record is appended for the unpersisted evaluation, so no slot is
  consumed for it.

**Replay:**
- A replay of an identical attempt recomputes in memory exactly as today. Persisting
  finds the existing artifacts, verifies them and returns. There is no new write, no
  new record and no slot consumption.
- The existing replay byte-comparison is unchanged.
- A stopped ledger stays terminal (E3.2). No evaluation happens, so nothing is
  persisted.

**Unchanged:** evaluation, screen, classification, disposition, accounting, stop
semantics and the ledger record schema are **unchanged**. Persistence only adds side
artifacts.

## E6.5 Report generation

- **Signature:** `render_report(ledger_path, *, diagnostics_dir=None)`.
  `diagnostics_dir` defaults to `<ledger dir>/diagnostics`.
- **Reading artifacts:** for every non-null diagnostics hash referenced by
  `PRIMARY_ATTEMPT` and `DIAGNOSTIC_EVALUATION` records, plus each referenced
  `b_diagnostics_hash` inside those payloads, the report loads the artifact and
  verifies that `sha256(bytes) == hash`, that the bytes are canonical
  (`canonical_json(json.loads(bytes)) == bytes`), and that the hash equals the
  reference.
- **Fail closed:** **any missing or failing artifact raises `C1DiagnosticsError`.**
  There is no silent omission and no legacy bypass. Historical Campaign-1, whose
  artifacts do not exist, therefore cannot be re-rendered. It is not re-rendered.
- **Content:** in addition to the existing content, the report includes, per attempt,
  per fold run and per cell:
  - value, raw_value, t_stat, status, n_obs, n_index;
  - support counts;
  - quarantine exposure (`q_b`, `q_s`, `q_l`, `n_flagged_formations`),
    `residual_cs_cells`, `liquidity_exposed`, `n_interior_b_missing`, cs3 and
    cs3_status;
  - delisting exposure (`e_s`, `b`, `e_l`, the n_*_formations counts, purged-cell
    count);
  - the D7b B T0/TS t-statistics and n_obs;
  - the diagnostics identity hashes.
- **Same for diagnostics:** the TOP_200/TOP_1000 diagnostic evaluations carry the
  same fields, labelled DESCRIPTIVE — NOT JUDGED.
- **Determinism:** values are rendered from the verified payload with no
  recomputation. Output stays byte-deterministic.

## E6.6 Required tests (synthetic only)

1. Canonical payload determinism; `sha256(payload) == obj.diagnostics_hash`, for both
   object types.
2. Filename-to-content binding.
3. First write creates the file; no `.tmp` remains.
4. Exact replay is idempotent (the mtime and inode do not change).
5. A pre-existing correct artifact is accepted.
6. A corrupted, truncated or conflicting payload at the hash path is rejected and the
   file is left untouched.
7. An interrupted temp write (only `.tmp` present) is ignored, and persist then
   succeeds.
8. Report: a missing artifact is refused; a wrong-hash or non-canonical artifact is
   refused.
9. The report reconstructs the per-cell statistics, exposures and D7b fields from
   artifacts.
10. Primary, TOP_200 and TOP_1000 artifacts are all persisted (executor run with
    stubs).
11. The ledger and disposition are byte-identical with versus without persistence.
    The ledger records are unchanged, and the persistence only adds files.
12. Replay consumes no extra slot and creates no new artifact.
13. Write-ahead ordering: no ledger record references a non-existent artifact,
    checked by a crash injected between persist and append.
14. No K-PIT access or write; no real data; no reserve or holdout paths.

**Mutations (new set; the existing 39 + 12 remain in force and must stay
killed):**
- E6-M1: hash verification is skipped on an existing artifact;
- E6-M2: overwrite instead of fail-closed;
- E6-M3: robustness diagnostics are not persisted;
- E6-M4: D7b `b_diagnostics` is not persisted;
- E6-M5: the report accepts a missing artifact (silent omission);
- E6-M6: the report accepts a wrong-hash artifact;
- E6-M7: the report bypasses verification;
- E6-M8: a non-atomic write (direct write to the final name);
- E6-M9: persistence happens after the ledger append (write-ahead broken);
- E6-M10: the payload-hash vs `diagnostics_hash` assertion is removed.

Every non-equivalent mutation must be killed.

## E6.7 Firewall and history

**Firewall:**
- no provider or network calls; credentials scrubbed;
- no real factor evaluation or Campaign execution;
- no reserve or final holdout.

**History:**
- The historical Campaign-1 directory
  `data_store/campaign1/ddd152dc…/` is **not touched**: no backfill, no re-render, no
  ledger change. Historical F-2 remains disclosed.
- Reconstruction requires a separate authorization.

**P-Q freeze:** no F-1 finding is implemented (no B/L, no bounds, no propagation
change).
