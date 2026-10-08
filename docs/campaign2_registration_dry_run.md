# Campaign-2 — synthetic K-PIT registration dry run (option (a) terminal evidence)

- **Status:** planner evidence, 2026-10-08. The user chose option (a): implement, then
  run a synthetic registration dry run, then STOP.
- **Not done:** **no production K-PIT write and no Campaign-2 execution.**
- **Software:**
  - master `6d55dd0`;
  - C2-LX `1b923c3` (merged `3097502`);
  - C2-LX-R1 `35eed8e` (merged `6d55dd0`).
- **Errata:** C2-E1 (SESOI, `17f10a91…b8b8`) and C2-E2 (bound IDs + payloads,
  `110ae61d…a594`).
- **Tool:** planner scratchpad transaction tool `ac87c983…1289`, not committed. It
  implements the registration plan §4–§7 mutatis mutandis, using:
  - the production `KnowledgeLog.append`, `read_records` and `estimand_policy_from_record`;
  - the merged C2-LX `hypothesis_identities` (bound path) and `verify_kpit_ordering`.

  Result file `d9bc001d…3e3e`.
- **Environment:** run under the network guard `6697069a…8368`, with the eight
  credentials scrubbed.
- **Regression:** the full suite at `6d55dd0` gives 5571 passed, 1 skipped, 0 failed.

## Pre-write values (A phase, computed by merged code from frozen inputs)

| Item | Value |
|---|---|
| Append base | K-PIT seq 9 `13d04ef4…d262`; file `190d53f09240acbd86e6256d6725d082c72ea19928843b5ec7b4cc11ad18f6a2` |
| `program_id` | `campaign2:0815259b60479670fe56d6dda110c46be38d719c2c0cb90da6f4902c61d80bc5` |
| `campaign2_id` | `3a24ab7df8339a0587b3ac37d1dafae1b415bf359d87e05a63c7e9b4df4f4379` (equals C2-E2 §2) |
| Hypothesis IDs | the 7 C2-E2 §2 values, reproduced through the **bound** path (no bypass) |
| Payloads seq 10–18 | byte-equal to C2-E2 §3 |

## Synthetic tests (disposable copies of the production log; deleted afterwards)

| Test | Result |
|---|---|
| T1 full transaction | seq 10–18 appended. All nine are HUMAN; the topology is exact; the EstimandPolicy round-trips; the C2-LX preflight is OK |
| T2 interrupted after 4 records, then resumed | only the remaining suffix was appended; preflight OK |
| T3 re-run on a complete log | 0 bytes appended (verification only) |
| T4 foreign record interleaved | STOP; file unchanged |
| T5 tampered prefix (SYSTEM-channel ESTIMAND_POLICY) | STOP; file unchanged |

- The terminal hashes and file SHA-256s of the synthetic runs are append-time values.
  They do **not** predict production.
- The production log was **byte-identical** before and after (`190d53f0…f6a2`).
- 0 factor evaluations, 0 return or label reads.

## History disclosure

- A first dry run on master `3097502` exposed the C2-LX defect: the empty
  `EXPECTED_HYPOTHESIS_IDS` and the `or None` bypass. The planner had missed it in the
  pre-merge review.
- That run computed the IDs through an unbound internal path only to finish the
  transaction tests. It is **superseded** by this bound-path run.

## Disposition

**CAMPAIGN-2 OPTION (a) COMPLETE — SYNTHETIC REGISTRATION DRY RUN PASS.**

Not authorized and not done:
- the production append of the 9 records;
- any Campaign-2 execution;
- any use of the reserve or the final holdout.
