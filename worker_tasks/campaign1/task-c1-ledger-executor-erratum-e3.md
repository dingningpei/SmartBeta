# C1-LX-E3 — Erratum to the C1-LX task spec: pre-open failures and terminal ledgers (frozen; standalone)

- **Status:** FROZEN. Approved by the user ("同意你的建议，定位E3"), on master `ef7c4d1`.
- **Amends:** `worker_tasks/campaign1/task-c1-ledger-executor.md` (d7e87a8,
  `118bcf414fa7015f66d2ec5ffdec3580e7e97123694314d56ad6a585c4f636fd`),
  together with:
  - C1-LX-E1 (bea64b3, `6cc544bac82ee7544f7407639c0b5a182263db6c250956fc0396c08a21f5e9ca`);
  - C1-LX-E2 (ef7c4d1, `90251a1da34c3bc2c43db3bc995ccc746178d15a2480fb3cad99669ccb78778c`).

  None of these documents is modified.
- **Effective authority:** the original task + E1 + E2 + E3.
- **Found by:** the planner's independent review of worker commit `ecd7b2b`, using
  adversarial scenarios S1 / S3 / S4.

## E3.1 — Defect: the E2.3 Stage-A row conflicts with C1-LX §6

**C1-LX §6** requires `CAMPAIGN_OPEN` to be **exactly one and first**, and
`CAMPAIGN_STOP` to be **exactly one and last**. **E2.3 Stage A** said a failure "at
`CAMPAIGN_OPEN`" emits `CAMPAIGN_STOP`. For a failure *before* `CAMPAIGN_OPEN` exists,
that would make `CAMPAIGN_STOP` the first record, which §6 forbids. The worker's code
showed both readings: S1 wrote the stop as the first record, while S4 wrote nothing.

**Correction.** A Stage-A failure is classified by **ledger state**:

| Ledger state when the Stage-A failure occurs | Ledger action | Result |
|---|---|---|
| **Pre-open:** the ledger contains no `CAMPAIGN_OPEN` (for example a provenance or identity computation failure, a K-PIT preflight failure, or a Campaign identity / `SearchPolicy` / `family_id` / binding validation failure before `CAMPAIGN_OPEN` is appended) | **no record of any kind is written.** The ledger stays byte-identical, including staying empty | `CampaignRunResult.stop_reason` carries the typed stop: `INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE`, or `GOVERNANCE_CONFLICT` for a K-PIT ordering failure (C1-LX §9) |
| **Post-open:** `CAMPAIGN_OPEN` is already durably recorded, but no attempt identity has been established (E2.3 boundary) | append `CAMPAIGN_STOP` with the typed stop. **No** attempt record | same typed stop |

E2.3 Stages B and C are unchanged.

## E3.2 — A ledger containing `CAMPAIGN_STOP` is terminal

This restates C1-LX §6 and E2.3's "no retry escape hatch" as an explicit executable
rule:
- If the ledger already contains a `CAMPAIGN_STOP`, `run()` **appends nothing**. That
  means no `PRIMARY_ATTEMPT`, `DIAGNOSTIC_EVALUATION`, `O2_GUARD` or second
  `CAMPAIGN_STOP`.
- `run()` may verify the chain and return the recorded stop reason, or refuse with a
  typed error. Either is acceptable, as long as **zero bytes are appended**.
- Any further work requires a separately authorized new Campaign ledger.

(A ledger that has `CAMPAIGN_OPEN` but no `CAMPAIGN_STOP`, for example after a hard
kill, continues to follow the existing replay and resume semantics in C1-LX §6 and §8.)

## E3.3 — Required tests (added; nothing removed)

1. **S1:** provenance failure on a fresh ledger → the ledger file is absent or empty,
   with zero records, and the result is `INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE`.
2. **S4:** K-PIT preflight failure → zero records, and the result is
   `GOVERNANCE_CONFLICT`.
3. **Post-open Stage A:** a hypothesis-identity failure after `CAMPAIGN_OPEN` → exactly
   one `CAMPAIGN_STOP` as the last record, zero attempt records for that hypothesis,
   and no consumed slot.
4. **S3:** a run ending in a recorded `CAMPAIGN_STOP` (untyped exception), then a re-run
   with the cause removed → ledger bytes are identical before and after the re-run.
   The same check applies after a Stage-B stop and after a normal
   `ALL_PRECOMMITTED_HYPOTHESES_EVALUATED` close.
5. **Invariant**, asserted at the end of every executor test: `CAMPAIGN_OPEN` is the
   first record if any record exists, at most one `CAMPAIGN_OPEN`, at most one
   `CAMPAIGN_STOP`, and `CAMPAIGN_STOP` (if present) is last.

**Additional mutations.** All prior mutations stay in force: 17 + 6 + 3, with E2-M3 as
variants a and b. E3 adds 3, for a frozen total of **29**:
- LX-E3-M1: the terminal-ledger check is removed (a stopped ledger accepts new
  records);
- LX-E3-M2: a pre-open failure appends `CAMPAIGN_STOP`;
- LX-E3-M3: a post-open Stage-A failure returns without appending `CAMPAIGN_STOP`.

## Scope statement

| Item | Change |
|---|---|
| Methodology | NONE |
| Hypotheses | NONE |
| Family membership | NONE |
| m | NONE |
| α | NONE |
| Screen | NONE |
| Critical value | NONE |
| Budgets | NONE |
| Replay semantics for non-terminal ledgers | NONE |
| Empirical information used | NONE |
