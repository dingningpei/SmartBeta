# C1-LX-E6.1 — Parent-directory durability for first creation of `diagnostics/` (frozen; standalone)

- **Status:** FROZEN. Authorized by "F-2 E6.1 PARENT-DIRECTORY DURABILITY ERRATUM",
  on master `90742f6`.
- **Amends:** `worker_tasks/campaign1/task-c1-ledger-executor-erratum-e6.md` (commit
  `90742f6`, SHA-256
  `689659c5f8ff78d1e43f4bb217a9ab223b4079df67d33f21449ae9a4831e76a4`), §E6.3 only.
  E6 is not modified or redesigned.
- **Origin:** the E6 implementation review (worker `e747c1f`) disclosed a gap.
  `persist()` creates `diagnostics/` and fsyncs that directory, but never fsyncs the
  **parent** Campaign directory that holds the new `diagnostics` entry. After a power
  loss or kernel crash, a ledger record could durably reference hash X while the newly
  created `diagnostics` directory entry is not durable.
- **Nature:** a narrow durability fix. **Not** an evaluation, reporting-schema,
  ledger-schema, P-Q or accounting change.

## E6.1 Required invariant

When the diagnostics directory is **newly created**, the implementation must durably
persist its directory entry in its parent **before** any artifact beneath it can be
published, and therefore before any ledger record can reference such an artifact.

## E6.1 Frozen ordering (replaces the first part of E6.3 step 3; the rest is unchanged)

1. **The Campaign directory** (`<ledger dir>`) exists. It is created by the existing
   runner and ledger architecture, and its own durability is out of scope here.
2. **Create `diagnostics/` non-recursively and record whether this call created it:**
   `os.mkdir(path)`. `FileExistsError` means "already existed".
   - If the parent does not exist, raise `C1DiagnosticsError`, which is fail-closed.
   - **No `parents=True`:** the parent is the existing Campaign directory.
3. **If this call newly created `diagnostics/`:** fsync the parent Campaign directory
   (`os.open(parent, O_RDONLY)` + `os.fsync`) **before** step 4. If the directory
   already existed, there is no parent fsync. A concurrent creator that wins the race
   is treated as "already existed".
4. Write the payload to the uniquely named temp file `.<hash>.<pid>.<random>.tmp`
   (unchanged).
5. `flush` + `fsync` the temp file (unchanged).
6. `os.link(tmp, final)`, which publishes atomically. If the final file exists, verify
   it (unchanged).
7. `unlink(tmp)`; **fsync `diagnostics/`** (unchanged).
8. Only then return. The executor appends the referencing ledger record afterwards
   (the E6.4 write-ahead rule, unchanged).

**Unchanged:**
- the existing-artifact path (verify, never overwrite);
- replay idempotence;
- canonicalization and the hash assertion;
- the report, the ledger, evaluation, disposition and accounting.

## E6.1 Tests and mutation (added; all E6 tests and mutations remain)

**Tests:**
- **CASE A** (`diagnostics/` already exists): no parent fsync; the diagnostics-dir
  fsync is still performed.
- **CASE B** (newly created): the parent fsync is performed **before** the temp-file
  write and before the link (checked by an ordered spy on `os.fsync`, `os.open` and
  `os.link`).
- **Executor:** on a fresh Campaign directory, the parent fsync precedes the first
  ledger append that references an artifact. A spy on the ledger append confirms the
  ordering.
- **A missing parent:** `C1DiagnosticsError`; nothing is created.
- **Unchanged behaviour:** exact replay is still idempotent (no new fsync of the parent
  on replay); an existing correct artifact is accepted; a corrupted one is rejected
  without overwrite; a persistence failure still prevents the referencing ledger
  append.

**Mutation:**
- E6.1-M1: the parent-directory fsync on first creation is removed or bypassed. It
  must be killed.

## Scope

- **Production change:** `smart_beta/campaign/diagnostics_store.py` only. Tests and
  fixtures may change.
- **Wider changes:** anything wider is a STOP.
- **Firewall:** no reconstruction, no re-run, no factor computation, no historical
  artifact change, no K-PIT, no slots, no reserve or holdout, no network.
