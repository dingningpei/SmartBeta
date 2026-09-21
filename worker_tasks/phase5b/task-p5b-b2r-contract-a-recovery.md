# Task P5B-B2R-CA — Contract-A Four-Identity Recovery (Spec Freeze)

**STATUS: FROZEN SPEC.** This document freezes one narrowly bounded
recovery experiment for **later isolated-worker execution only**. It
authorizes **zero** live calls now, does **not** access credentials, does
**not** modify production code, does **not** merge any CA1 evidence branch,
and does **not** push or tag.

Base master: `cbc36ca766cf7884ac0604f6883839939f24d84a`
D4A Contract-A governance verdict: `D4A-CA-ELIGIBLE`
(governance commit `cbc36ca766cf7884ac0604f6883839939f24d84a`)
CA1-T1 capability evidence: `97e082e7973e64bd421343880b3d878748a086da`
(branch `phase5b/task-p5b-ca1-t1-basic-capability-probe`)

## 1. Purpose

Freeze one narrowly bounded recovery experiment for exactly the four P5B-6
B2 identities that remained unresolved after the Contract-B / ProMax Policy
C capture budget was exhausted. This experiment exists only because D4A
governance independently determined Contract A to be a materially distinct
legitimate acquisition path.

This is **not** a reset of Contract-B Policy C, **not** a fourth ProMax
attempt, and **not** a substitution of any kind.

## 2. Contract and credential

- Contract A / Basic base: `http://datahubco.com/app-api/openapi/v1/tushare/{api}`
- Method: `GET`, arguments as query parameters, `X-API-Key` header.
- Credential: `TUSHARE_BASIC_PROXY_TOKEN` **only**.
- Contract B / ProMax (`TUSHARE_PROXY_TOKEN`) is **not** authorized for this
  recovery and must never be read, used, or sent.

## 3. Exact authorized identities (frozen)

Exactly these four requests, in this order:

| ID | endpoint | ts_code | period |
|---|---|---|---|
| B2-CA-1 | `fina_indicator` | `601318.SH` | `20260630` |
| B2-CA-2 | `fina_indicator` | `000858.SZ` | `20250930` |
| B2-CA-3 | `income` | `000858.SZ` | `20251231` |
| B2-CA-4 | `fina_indicator` | `000858.SZ` | `20260331` |

No other stock, no other period, no other endpoint, no other endpoint
spelling, no query broadening, and no batch/range substitution is
authorized.

## 4. Call budget and progression

- **Maximum live calls in later execution: 4** (one per frozen identity).
- Exactly **one attempt per identity**. No retries, no replacement calls,
  no alternate stock/period, no Contract-B fallback, no ProMax call, no
  automatic continuation beyond these four calls.
- **Unlike CA1-T1,** a failed identity does **not** authorize substitution.
  Unless a security/credential boundary is violated, the worker classifies
  the attempt and **proceeds to the next already-frozen identity**, so the
  bounded four-identity experiment measures each identity separately.
- The only permitted early stop is a security/credential boundary violation
  (e.g. the credential is absent, or the credential would be sent to an
  unexpected host, or evidence tooling would leak credential material). In
  that case, stop immediately and record the blocker; do not continue to
  the remaining identities.

## 5. Transport exception (extended, narrowly)

The existing user-accepted plaintext-HTTP exception is **extended only** to
this explicitly frozen four-call Contract-A recovery experiment, and only
for `TUSHARE_BASIC_PROXY_TOKEN` against
`http://datahubco.com/app-api/openapi/v1/tushare/{api}`.

This does **not** authorize plaintext transport for: `TUSHARE_PROXY_TOKEN`,
Contract B / ProMax, other credentials, other endpoints or experiments,
production integration, or future acquisition.

## 6. Response handling

Interpret returned rows by `data.fields`, never by positional assumptions.
The expected envelope is `{"request_id": …, "code": …, "data": {"fields":
[…], "items": […]}, …}`.

For every request, record sanitized evidence:

- experiment identity (`P5B-B2R-CA`);
- logical B2 identity and call ID;
- endpoint, `ts_code`, `period`;
- HTTP status;
- provider code;
- sanitized provider message;
- response `data.fields`;
- row count;
- classification;
- non-secret cache/data-source headers if present (recorded as provenance
  observation only, never as semantic certification).

Never persist the `X-API-Key` value, the credential value, or any
secret-derived material.

## 7. Classification

Preserve the existing taxonomy: `SERVES-DATA`, `SERVES-EMPTY`,
`ENDPOINT-MISSING`, `SHAPE-MISMATCH`, `ENTITLEMENT-DENIED`,
`TRANSPORT-FAILURE`.

- Do **not** equate HTTP 200 with usable B2 recovery.
- Do **not** equate `code=0` + `items=[]` with `SERVES-DATA`; that is
  `SERVES-EMPTY`.
- Do not infer entitlement denial solely from HTTP status without examining
  the sanitized provider response.

## 8. Positive-vintage / semantic requirements

Retrieval success and semantic usability are **separate decisions**. For
each `SERVES-DATA` result, preserve all fields necessary for a later
offline review of positive-vintage identity, including at least:

- `ts_code`;
- `end_date` (reporting period);
- `ann_date`;
- `f_ann_date` (if present);
- `report_type` (if present);
- `update_flag` (if present);
- `profit_dedt` for `fina_indicator`;
- any other fields required to establish positive vintage identity.

Do **not** certify PIT semantics during capture merely because these
columns exist. Do **not** certify `profit_dedt` semantics merely because
the field exists. **Stock + period alone is not positive-vintage identity.**

## 9. Evidence preservation

The later execution must preserve sanitized raw response evidence for each
attempted identity, sufficient for a subsequent **offline** reviewer to
determine: (1) whether the exact identity was served; (2) whether a
positive vintage identity can be established; (3) whether the recovered
record can be joined into the frozen fundamental reconstruction without
silent substitution.

Do **not** modify historical P5B-6 evidence and do **not** overwrite
Contract-B failures. Contract-A evidence must remain distinguishable by
provider/path (Contract A = `datahubco.com`, distinct from the Contract-B
`pcd.mobcvb.cn` provenance).

## 10. Anti-upgrade

Even if all four calls return `SERVES-DATA`:

- do **not** declare Barrier 4a passed;
- do **not** declare B2 solved merely from transport success;
- do **not** declare PIT certified;
- do **not** declare `profit_dedt` certified;
- do **not** declare production readiness;
- do **not** authorize P5B-7.

The next mandatory step after execution is an **offline B2 semantic /
vintage review**. Only after that review may the project decide whether
Stage 3 should be rerun.

## 11. Execution ownership

This planning task executes **zero** live calls. The later execution must
be performed by an isolated worker branch, may use only
`TUSHARE_BASIC_PROXY_TOKEN`, and must not read or use `TUSHARE_PROXY_TOKEN`.

## 12. Status after this freeze

```
D4A Contract-A recovery eligibility:  PASS (D4A-CA-ELIGIBLE)
B2 live execution:                    AUTHORIZED ONLY BY THIS FROZEN SPEC, AFTER THIS FREEZE
Barrier 4a:                           NOT PASSED
P5B-7:                                BLOCKED
Production integration:               NOT AUTHORIZED
CA1 evidence branches merged:         NO
```
