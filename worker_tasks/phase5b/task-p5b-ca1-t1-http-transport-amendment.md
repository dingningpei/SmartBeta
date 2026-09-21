# Task P5B-CA1-T1 — Contract-A Plaintext-HTTP Transport Amendment

**STATUS: FROZEN AMENDMENT (spec/documentation only).** This document
freezes a narrowly-scoped transport exception for a **future** CA1-T1
capability execution. It authorizes **zero** live calls now, does **not**
execute the amended probe, does **not** create an execution branch or
worktree, and does **not** merge the original CA1 evidence branch. No live
calls were made to produce this document.

Base master: `12bde9824259dcf263789da245a7f224d311c99e`
Original CA1 spec: `worker_tasks/phase5b/task-p5b-ca1-contract-a-capability-probe.md`
Original CA1 evidence branch: `phase5b/task-p5b-ca1-contract-a-capability-probe`
Original CA1 evidence commit: `57fc53a7255405b8a253c713e23449aa4e2e0bc4`

## 1. Historical context (preserved, never rewritten)

The original CA1 execution made exactly one live call: the credential-free
HTTPS/TLS pre-flight to
`https://datahubco.com/app-api/openapi/v1/tushare/stock-basic`. It received
**no HTTP response** — connection timeout (`TimeoutError`, raw body
`timed out`). Per the original frozen spec's deterministic early-stop and
TLS hard stop, this triggered `CREDENTIAL-TRANSPORT-RISK`: **no credential
was transmitted**, and original Calls 2–6 were not attempted.

**Original CA1 disposition (final, not rewritten, not reinterpreted as
success):** `TLS-UNREACHABLE`, 1 live call, `CREDENTIAL-TRANSPORT-RISK`
hard stop, `income` and `fina_indicator` capabilities `NOT-DETERMINABLE`.
This result remains permanently on branch
`phase5b/task-p5b-ca1-contract-a-capability-probe` at commit
`57fc53a7255405b8a253c713e23449aa4e2e0bc4`.

## 2. User transport-risk decision (explicit project-level exception)

The user has explicitly reviewed and accepted the transport risk of sending
the Contract-A provider-issued API credential over the provider-documented
**plaintext HTTP** route for this narrowly bounded capability experiment.

This is:

- an explicit, project-level exception, recorded here;
- scoped to the bounded CA1-T1 capability probe only;
- **not** evidence that plaintext HTTP is generally safe;
- **not** authorization to use any other credential over plaintext HTTP;
- **not** authorization for production code, normal research acquisition,
  B2 acquisition, other providers, other credentials, or future
  experiments.

## 3. Credential boundary

- **Only** `TUSHARE_BASIC_PROXY_TOKEN` may be used under this exception,
  and only for the CA1-T1 request matrix in §6.
- `TUSHARE_PROXY_TOKEN` remains the Contract B / ProMax credential and must
  **never** be sent over Contract A or over plaintext HTTP.
- Do not print, log, hash, persist, commit, or expose the value of
  `TUSHARE_BASIC_PROXY_TOKEN`. Do not record `X-API-Key` header contents.
  The credential is read at call time and travels only in the `X-API-Key`
  header, never in evidence, logs, URLs, or files.

## 4. Amended transport contract (CA1-T1 only)

For the future CA1-T1 capability execution, the provider-documented
Contract-A base is authorized:

```
GET http://datahubco.com/app-api/openapi/v1/tushare/{endpoint}
arguments as URL-encoded query parameters
X-API-Key header = TUSHARE_BASIC_PROXY_TOKEN
```

This plaintext-HTTP permission applies **only** to the bounded CA1-T1
capability probe defined below. It does not generalize to production code,
normal research acquisition, B2 acquisition, other providers, other
credentials, or future experiments.

**Relationship to the original CA1 spec:** the original spec remains in
force except where this amendment explicitly overrides it for CA1-T1. The
override is narrow and limited to: (a) the transport scheme (plaintext
`http://` instead of `https://`), and (b) removal of the HTTPS/TLS
pre-flight (original Call 1), which is **not repeated**. Every other frozen
rule carries forward unchanged: the one-attempt-per-call rule, no
retry/backoff/sleep, the deterministic early stop, the positive control,
the B2-identity prohibition, the classification taxonomy, evidence
sanitization, and the governance boundary.

## 5. CA1-T1 is a NEW, separately-identified experiment

The original CA1's single live call (the credential-free HTTPS pre-flight)
remains part of the original CA1 evidence and is **not** re-counted or
pretended away. CA1-T1 is a distinct experiment with its own budget and its
own evidence under experiment ID **`P5B-CA1-T1`**.

**CA1-T1 frozen budget: maximum 5 credentialed HTTP calls** (T1-1 through
T1-5 below), exactly one attempt each. No retries, no replacement calls, no
additional endpoint names, no additional parameter shapes, no HTTPS
pre-flight retry.

## 6. Frozen CA1-T1 request matrix (exact future order)

| Call | endpoint | query params | X-API-Key | purpose |
|---|---|---|---|---|
| T1-1 | `stock-basic` | (none) | present | provider-documented baseline |
| T1-2 | `income` | `ts_code=000001.SZ&period=20250930` | present | B2-critical positive control |
| T1-3 | `fina_indicator` | `ts_code=000001.SZ&period=20250930` | present | B2-critical positive control |
| T1-4 | `fina-indicator` | `ts_code=000001.SZ&period=20250930` | present | hyphen spelling variant |
| T1-5 | `stock_basic` | `ts_code=000001.SZ` | present | underscore naming disambiguation |

**Deterministic early stop (frozen before execution):**

- **T1-1 (`stock-basic` baseline):** if authentication/basic Contract-A
  request semantics are unusable → **HARD STOP**. T1-2 through T1-5 are
  forbidden. No auth guessing, no parameter change, no retry.
- If T1-1 is usable → continue to T1-2 through T1-5 in the exact frozen
  order.
- No endpoint-name exploration, no parameter exploration, no additional
  spelling variants, no HTTPS pre-flight retry, and no calls beyond the
  five above.

## 7. Positive control / B2 boundary

- Fundamentals positive control: `000001.SZ`, `period=20250930` (unchanged).
- The four B2 blocker identities remain **STRICTLY FORBIDDEN** in CA1-T1:

```
601318.SH::fina_indicator::20260630
000858.SZ::fina_indicator::20250930
000858.SZ::income::20251231
000858.SZ::fina_indicator::20260331
```

CA1-T1 does **not** authorize B2 acquisition.

## 8. Classification (unchanged taxonomy)

`SERVES-DATA`, `SERVES-EMPTY`, `ENDPOINT-MISSING`, `SHAPE-MISMATCH`,
`ENTITLEMENT-DENIED`, `TRANSPORT-FAILURE`. Do not infer entitlement denial
solely from HTTP status; classify from response semantics. A
bad-parameter/request-shape response is `SHAPE-MISMATCH` and does **not**
authorize parameter exploration.

## 9. Evidence requirements

Record sufficient sanitized evidence to establish: experiment ID
`P5B-CA1-T1`, request endpoint, non-secret request parameters, HTTP status,
provider response code/message, response envelope, fields, row count, and
classification. Do **not** preserve the credential value, the `X-API-Key`
header value, or any authorization material. If evidence tooling fails
after a request has already occurred, do **not** repeat the request merely
to repair evidence; preserve what was obtained and report the tooling
defect separately.

## 10. No production authorization

CA1-T1 does **not** authorize modification of production Contract-A support,
integration of Contract A into generic provider code, modification of
`ProxyTushareClient`, or modification of PIT semantics. This is capability
evidence only.

## 11. Governance after success

Even if CA1-T1 establishes `SERVES-DATA` for both `income` and
`fina_indicator`:

```
Barrier 4a:  NOT PASSED
D4A:         HOLD
P5B-7:       BLOCKED
```

The four B2 identities remain unauthorized. A CA1-T1 success would
establish only that Contract A is a functioning, materially distinct
candidate fundamentals acquisition path; a separate governance decision
must determine whether that evidence qualifies Contract A as a material
exogenous change / legitimate B2 recovery path. Do **not** automatically
proceed to B2.

## 12. Scope of this amendment (spec only)

This document freezes the amendment. It authorizes **zero** live calls now
and does **not** execute CA1-T1. The original CA1 spec and evidence remain
historically intact and are not rewritten. This amendment is committed to
master as a spec; the CA1 evidence branch is **not** merged to freeze this
amendment.
