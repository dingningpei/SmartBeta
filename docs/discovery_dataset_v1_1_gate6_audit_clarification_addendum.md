# Discovery Dataset v1.1 — Gate-6 Specimen Audit Clarification Addendum: Conflict Domain and Form Allowlist (NORMATIVE)

- **Status:** normative wording repair, decided by the user on 2026-09-30
  (G6-SW HOLD resolution: A1 = (b), A2 = (a)).
- **It amends:**
  - `worker_tasks/dataset_v1/task-g6-specimen-audit.md` §§5 and 8 (05ef922) —
    `1dc4c7e0f8a09ebe4e6322d98d42c17aa95301b3aed8bf7321ad1bf6e637e7e2`;
  - `docs/discovery_dataset_v1_1_gate6_audit_clarification.md` (cab82e0) —
    `0b529dca209b4fa9f54f9662bb3d9205f07b0056232532b2a529a3a513f43789`.
- **What it does not change:** every other element of the audit design.

## A1. Where evidence conflicts are looked for (spec §8 step 2)

**Only facts dated in (p, t] count.** Conflict detection for `SPLIT` and
`CASH_DISTRIBUTION` facts considers only facts whose **own** event date lies
in (p, t]:
- the event date of a `SPLIT` fact is its `market_effective_date`;
- the event date of a `CASH_DISTRIBUTION` fact is its `ex_date`.

A fact dated outside (p, t], such as an unrelated historical split or
distribution, is irrelevant to conflict detection for that specimen.

**What counts as a conflict inside (p, t]:**
- Two `SPLIT` facts whose f values differ beyond a relative 1e-6 give
  `UNRESOLVED(EVIDENCE_CONFLICT)`.
- Two `CASH_DISTRIBUTION` facts whose amounts differ beyond a relative 1e-6
  give `UNRESOLVED(EVIDENCE_CONFLICT)`.

**Order:** conflict detection runs **before** the candidate and magnitude
test. A fact does not have to pass the a ∈ [0.5, 1.5] test (or the amount <
close_p test) to take part in conflict detection.

**Unchanged:** the rule that candidates from more than one of (a), (b) and
(c) give `UNRESOLVED(EVIDENCE_CONFLICT)`.

## A2. The form priority table is a closed allowlist (spec §5)

- **Only listed forms are eligible.** Only filings whose exact index Form Type
  appears in the §5 priority table can enter the fetch plan.
- **Unlisted forms are excluded entirely.** An unlisted form (for example
  Form 4, SC 13G, or any other form not in the table) is never ranked, never
  fills an unused slot, never enters the fetch plan and never affects the
  fetch-plan hash.
- **No listed filing in the window:** if the event window
  [t − 60 days, min(t + 30 days, 2023-06-30)] contains no filing of a listed
  form, the specimen is `UNRESOLVED(NO_FILINGS_IN_WINDOW)`.
- **The list is frozen.** It is not broadened to improve audit resolution.
