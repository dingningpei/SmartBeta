# P5B-4 Final Disposition (completes the bounded entitlement-probe task)

This document is the final, authoritative disposition of P5B-4. It
supersedes no prior evidence and rewrites nothing: `entitlement_probe_
ledger.json` (Run 1), `resume/run2_attempts.jsonl` (Run 2), and
`PROXY_DIAGNOSTIC.md` remain exactly as committed. This document only
states, precisely, what P5B-4 as a whole does and does not certify,
correcting an earlier report's looser "ENTITLEMENT DENIAL EVIDENCE: NO"
phrasing to the exact bounded claim below.

## Corrected certification language

**Do not read as:** "entitlement denial has been conclusively proven
absent," or as a claim about the proxy or vendor in general.

**The supported bounded claim is exactly:**

> NO ENTITLEMENT DENIAL OBSERVED IN THE BOUNDED P5B-4 PROBE.

Evidence for this specific, bounded claim:
- 59 total live calls across two independent bounded invocations (Run 1:
  36, Run 2: 23);
- zero observed `400`/`401`/`403` responses across all 59 calls;
- every one of the 9 required endpoint categories produced at least one
  valid (`200`) response;
- every one of the 4 selected stocks produced at least one valid
  response;
- 14 of Run 1's 23 unresolved identities later returned `200` in Run 2;
- **9 identities remain unresolved**, all `HTTP 503
  upstream_pool_exhausted` in both independent attempts.

Those 9 are recorded as **unresolved transport/access observations**.
They are **not** classified as accessible (no valid response was ever
obtained for them) and **not** classified as entitlement-denied (no
denial-shaped response was ever observed for them either). They remain
exactly what the evidence shows: unresolved.

**Additional frozen disposition, new this document:**

> SINGLE-PASS COMPLETE-CAPTURE RELIABILITY = NOT CERTIFIED.

P5B-4 does not certify that any single bounded capture pass (this
probe's or a future one) reliably completes a full stock x endpoint
matrix. The 60.9% Run-1-to-Run-2 recovery rate is reported as an
observed historical fact only; it is **not** treated as a probability
model, and no future success rate is estimated or projected from it.

`PROXY DETERMINISM = NOT CERTIFIED` and `PROXY SUFFICIENT FOR
PRODUCTION = NO` are unchanged and are not upgraded by any evidence in
this task.

## The two questions, split and frozen

**P5B-4 (this task) — entitlement probe.** Purpose: detect bounded
evidence of endpoint/plan entitlement denial and select a defensible
pilot universe. Disposition: `NO ENTITLEMENT DENIAL OBSERVED IN BOUNDED
PROBE`, with 9/36 identities unresolved for complete raw capture. This
is P5B-4's complete and final scope — it does not attempt, and is not
required to attempt, full raw-data acquisition.

**P5B-6 (future task) — raw-data acquisition.** Purpose: obtain the
actual, complete bounded dataset the empirical CH3/CH4 pilot needs.
Requirement, frozen here for that task to inherit: use the staged/
resumable capture architecture (P5B-5); fail closed if the required
capture set is incomplete according to its own dataset contract; retain
exactly-one-attempt-per-missing-identity semantics per invocation;
preserve staging state and STOP at its own capture barrier rather than
running unlimited automatic resume rounds; never silently shrink the
universe; never fabricate or fall back to substitute data; never
reinterpret a transport failure as an entitlement denial. **P5B-6 must
not infer that an identity is available merely because its endpoint
succeeded for a different stock** — actual required stock x endpoint x
time-range evidence must be captured for each one it needs.

## P5B-4 completion criterion (all satisfied)

- [x] Pilot universe selection is frozen and documented (`README.md`,
  Run 1).
- [x] All required endpoint categories were exercised (9/9).
- [x] Every endpoint category has at least one successful bounded
  observation (confirmed in `PROXY_DIAGNOSTIC.md`'s endpoint summary).
- [x] No explicit entitlement-denial response was observed (0/59).
- [x] Unresolved 503 identities remain explicitly recorded (9, named in
  `RESUME_RUN2.md` and `PROXY_DIAGNOSTIC.md`).
- [x] Incomplete raw coverage is not hidden (stated plainly in three
  separate committed documents).
- [x] Proxy reliability is not certified (stated above and in
  `PROXY_DIAGNOSTIC.md`).
- [x] Complete empirical acquisition is explicitly delegated to P5B-6
  (this document, above).
- [x] All evidence/provenance remains preserved (Run 1 and Run 2 raw
  bodies, ledgers, and timestamps are unmodified; independently verified
  byte-for-byte in the Barrier-3 resume review).

**This is not a certification that all 36 identities are accessible.**
It is a certification that the bounded entitlement-probe purpose is
satisfied and that the remaining incompleteness is explicitly recorded
and handed to the correctly-scoped future task.
