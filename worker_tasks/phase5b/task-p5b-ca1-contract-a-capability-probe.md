# Task P5B-CA1 — Contract-A Capability Probe (datahubco.com BASIC)

**STATUS: FROZEN SPEC.** This document freezes the probe's scope, request
matrix, budget, classification taxonomy, and evidence format. Freezing it
does **not** authorize execution, a branch, a worktree, a Pi worker, any
live call, or the supply of a credential. Execution requires separate,
explicit authorization plus the user-supplied Contract-A credential value.
No live calls were made to produce this spec.

Base master: `86dc9a6e9d350070c0f2096b97360519d7462f72`
Branch (execution only): `phase5b/task-p5b-ca1-contract-a-capability-probe`
Discovered by: P5B-PROXY-CONTRACT-AUDIT (audit conclusion: P5B-6 used
Contract B / PROMAX `pcd.mobcvb.cn/tushare/pro` exclusively and exhausted
it for the four unresolved B2 identities; Contract A `datahubco.com` is the
only acquisition path named in the provider-supplied contract that is
absent from this repository and therefore genuinely untested).

## 1. Purpose

A bounded, live-call **capability discovery** of Contract A (BASIC), to
answer exactly two questions with recorded evidence:

1. Does Contract A serve `income` and `fina_indicator` — the two endpoints
   behind the four unresolved P5B-6 B2 identities — under the B2-required
   request shape (or a spelling variant)?
2. What is Contract A's request/response contract (scheme/TLS, `X-API-Key`
   behavior, endpoint-name spelling convention, parameter encoding,
   response envelope, and any date-range/parameter rejection), to the
   extent this bounded probe can establish it?

This task is **explicitly NOT**:

- acquisition of the four unresolved identities
  (`601318.SH::fina_indicator::20260630`,
  `000858.SZ::fina_indicator::20250930`,
  `000858.SZ::income::20251231`,
  `000858.SZ::fina_indicator::20260331`);
- a CH3/CH4 methodology change or any factor computation;
- a Barrier 4a reopen, a P5B-6 Resume-4, a Stage 3/4 authorization, or a
  P5B-7 authorization;
- a production-code change of any kind;
- a claim that Contract A is (or is not) a "material exogenous change"
  under the D4A-HOLD disposition. A capability finding is only one *input*
  to that later, separately-authorized decision.

## 2. Provider-supplied contract under test (the only source of truth)

The following is the provider-supplied description of Contract A and is the
**only** authority for Contract A's surface in this repository. No
`datahubco.com` reference exists anywhere in the repo, so nothing below is
repo-verified; the probe's job is to verify or contradict it.

```
CONTRACT A — BASIC
GET http://datahubco.com/app-api/openapi/v1/tushare/{endpoint}
Authentication: X-API-Key
Example endpoint: stock-basic
Provider description: "普通基础功能" (ordinary basic functionality)
```

For contrast (already established and repo-verified — do not re-litigate):

```
CONTRACT B — PROMAX (what P5B-6 actually used)
GET https://pcd.mobcvb.cn/tushare/pro/{api_name}
Authentication: X-API-Key; args as query params; example: daily
```

Contract A uses a **different** API key from Contract B. The provider's
"basic" tier description and the single documented example (`stock-basic`)
must **not** be read as evidence that `income`/`fina_indicator` exist —
that is exactly what this probe tests. Do not infer capability from the
provider statement.

## 3. Frozen request matrix (deterministic, fixed order)

The probe base URL template is frozen as
`https://datahubco.com/app-api/openapi/v1/tushare/{endpoint}` with
arguments URL-encoded into the query string and `X-API-Key` in the header.
**The documented scheme is plaintext `http://`; this probe freezes `https://`
for every credential-carrying request** (see §4 TLS hard stop).

Exactly the following calls, in this order, one attempt each, no retry, no
backoff, no sleep, no resume within this task:

| Call | endpoint | query params | X-API-Key | purpose |
|---|---|---|---|---|
| 1 | `stock-basic` | (none) | **absent** | unauthenticated HTTPS/TLS pre-flight (no credential, no `X-API-Key` header) |
| 2 | `stock-basic` | (none) | present | credentialed baseline: auth + envelope + hyphen naming convention |
| 3 | `income` | `ts_code=000001.SZ&period=20250930` | present | B2-critical: `income` with the B2-required shape |
| 4 | `fina_indicator` | `ts_code=000001.SZ&period=20250930` | present | B2-critical: `fina_indicator` (underscore) |
| 5 | `fina-indicator` | `ts_code=000001.SZ&period=20250930` | present | B2-critical: `fina-indicator` (hyphen spelling variant) |
| 6 | `stock_basic` | `ts_code=000001.SZ` | present | naming-convention disambiguation (underscore variant) |

The probe identity `ts_code=000001.SZ, period=20250930` is the
**positive control** and the **only** fundamentals probe identity. It is a
Contract-B-proven-success identity (both `income` and `fina_indicator`
returned real `fields`/`items` for it on PROMAX), so a Contract-A failure
for it cannot be explained away as "this stock/period has no data" — it
would be a genuine capability/contract difference.

**The four unresolved B2 identities are STRICTLY FORBIDDEN in CA1.** This
probe must never request `601318.SH::fina_indicator::20260630`,
`000858.SZ::fina_indicator::20250930`, `000858.SZ::income::20251231`, or
`000858.SZ::fina_indicator::20260331`. They remain outside CA1
authorization.

**Frozen budget: maximum 6 live calls** (Call 1 pre-flight + Calls 2–6
credentialed), one attempt each, `samples=1`-equivalent. No intra-invocation
retry, no backoff, no sleep, no automatic Resume. Fail-closed: if any
identity is left `NOT-DETERMINABLE`, that is the recorded outcome; a resume
requires a new, separately-authorized spec/budget, never an automatic
continuation.

### 3.1 Deterministic early-stop decision tree (frozen before execution)

The following decision tree is frozen and is evaluated in call order with
no deviation, no guessing, and no exploration.

**Call 1 — unauthenticated HTTPS/TLS pre-flight (no credential, no
`X-API-Key` header).**

- If `https://datahubco.com/app-api/openapi/v1/tushare/stock-basic` cannot
  be safely reached over HTTPS/TLS: **STOP.** Calls 2–6 are FORBIDDEN.
  Classify `CREDENTIAL-TRANSPORT-RISK` (or the appropriate transport
  classification defined in §5). A 401/403 or any other HTTP response
  actually reached over HTTPS establishes HTTPS reachability and allows
  continuation; it does **not** by itself establish authentication
  success, `stock-basic` capability, Contract-A usability, `income`
  capability, or `fina_indicator` capability.
- If HTTPS/TLS is reachable: continue to Call 2.

**Call 2 — credentialed `stock-basic` baseline
(`TUSHARE_BASIC_PROXY_TOKEN` sent only as the `X-API-Key` header).**

- If authentication/basic Contract-A request semantics are unusable:
  **STOP.** Calls 3–6 are FORBIDDEN. Record the precise predeclared
  classification (§5). Do **not** guess another authentication scheme, do
  **not** change parameters, do **not** retry.
- If Contract A is usable: continue to Calls 3–6 in the frozen order.

**Calls 3–6 — frozen matrix, preserved order** (`income` →
`fina_indicator` → `fina-indicator` → `stock_basic`), each with the frozen
positive-control identity and spelling variant. No additional endpoints, no
additional parameter shapes, no additional naming variants, no exploration
after a failure.

**No param-guessing.** If a probe identity returns a "bad parameter" style
4xx, record the exact body verbatim and classify `SHAPE-MISMATCH` (§5).
Do **not** iterate on parameter guesses — that is unconstrained acquisition
and is out of scope. The param-convention question is answered only by what
the frozen matrix returns, not by exploration.

## 4. Credential and TLS governance (binding)

- **Environment variable (frozen, exact name):** `TUSHARE_BASIC_PROXY_TOKEN`.
  This is the Contract-A-specific variable. `TUSHARE_PROXY_TOKEN` remains
  reserved for Contract B / ProMax and must never be used for Contract A.
  The value is supplied by the user at execution time (environment or a
  `--token-file`); the spec does not assume the value is known.
- **Must be a different value** from the Contract-B token. The two
  credentials must never be mixed or reused across the two contracts.
- **Read at call time, never at import/construction, never stored on an
  object, never logged, hashed, printed, or written to any file.** The
  recorder references only the variable *name*.
- **Sent only in the `X-API-Key` header, only over HTTPS.**
- **TLS hard stop (do not route around):** before Call 2, Call 1 (an
  unauthenticated `https://` request carrying **no** `X-API-Key` header)
  must establish that the host answers over HTTPS. If
  `https://datahubco.com` is unreachable (DNS, TLS handshake, connection
  refused) and the endpoint is plaintext-only (`http://`), **STOP and
  report a `CREDENTIAL-TRANSPORT-RISK` blocker**. Do not transmit
  `TUSHARE_BASIC_PROXY_TOKEN` over plaintext HTTP under any circumstance,
  and do not downgrade HTTPS → HTTP while carrying a credential. This is a
  hard stop (CLAUDE.md §5 "any secret/credential risk"), not a decision a
  worker may make locally.
- If Call 1 returns HTTP 200 with data (no auth enforced), record that as
  an observation (auth weaker than documented) but still send the
  credential on the remaining calls unless it would be sent over
  plaintext, in which case the hard stop above applies.
- **NO_PROXY (process-local only):** the provider's example included
  `NO_PROXY='*'`. Do **not** make a persistent/global user environment
  modification merely because it appeared in the example. If the frozen CA1
  recorder requires `NO_PROXY` behavior, it must be set **process-local to
  the CA1 probe** (e.g. on the recorder's own subprocess environment) and
  the reason must be recorded in the evidence. Do not alter shell startup
  files and do not alter global networking configuration.

## 5. Classification taxonomy (buckets are never conflated)

For each credentialed identity, classify from HTTP status + raw body only:

- `SERVES-DATA` — HTTP 200 with a usable, non-empty data payload. The
  envelope shape is whatever Contract A actually returns; it is recorded
  verbatim and must not be assumed to match Contract B's
  `{"code":0,"data":{"fields":[...],"items":[...]}}`.
- `SERVES-EMPTY` — HTTP 200 with a recognized data payload that is empty
  for this identity (endpoint exists and accepted the request; zero rows).
- `ENDPOINT-MISSING` — HTTP 404, or an explicit "not found"/"no such
  api"/"endpoint does not exist" marker.
- `SHAPE-MISMATCH` — HTTP 400/422, or an explicit "invalid parameter"/
  "missing parameter"/"bad request" marker. (Endpoint exists but rejects
  this exact request shape — a capability finding, not an entitlement or
  transport failure.)
- `ENTITLEMENT-DENIED` — HTTP 401/403, or an explicit entitlement marker
  (`unauthorized`, `forbidden`, `not entitled`, `订阅`, `权限`, `无权`,
  `plan tier`, etc.).
- `TRANSPORT-FAILURE` — HTTP 5xx, connection reset/timeout, DNS, TLS
  failure.
- `CREDENTIAL-TRANSPORT-RISK` — the Call-1 pre-flight established that
  HTTPS/TLS cannot safely reach the endpoint and the service is available
  only over plaintext HTTP, so no credential may be transmitted. This is a
  hard stop; Calls 2–6 are forbidden.
- `OTHER` — anything else not above.

A 4xx response must **not** be classified as entitlement denial merely
because it is a 4xx: classify from the response semantics. A
bad-parameter/request-shape response is `SHAPE-MISMATCH`, not
`ENTITLEMENT-DENIED`, and it does not authorize parameter exploration.

Call 1 (unauthenticated pre-flight) is classified separately as
`TLS-OK` / `TLS-UNREACHABLE` / `HTTP-200-NO-AUTH` / `HTTP-401-AUTH-REQUIRED`
etc., and never conflated with the credentialed buckets.

## 6. Capability conclusion (computed strictly from the evidence)

For `income` and for `fina_indicator` (the B2-critical endpoints), the
report must state one of:

- `PROVED (serves data)` — at least one `SERVES-DATA` for that endpoint
  name under any probed spelling, naming the exact spelling that worked.
- `PROVED (endpoint exists, empty for probe identity)` — no `SERVES-DATA`
  but at least one `SERVES-EMPTY`.
- `CONTRADICTED` — all probed spellings are `ENDPOINT-MISSING` or
  `ENTITLEMENT-DENIED`.
- `NOT-DETERMINABLE` — all attempts `TRANSPORT-FAILURE`, or the probe was
  stopped by the §4 TLS/credential hard stop before any credentialed
  attempt.

Mixed outcomes resolve to the most-informative non-failure bucket (e.g.
one spelling `ENDPOINT-MISSING` and another `SERVES-DATA` ⇒ `PROVED` for
the `SERVES-DATA` spelling). The report must state exactly which spelling
and which classification produced the conclusion, quoting the raw body
verbatim (no credential material is ever in a body).

`SERVES-DATA`/`SERVES-EMPTY` for the *probe identity* proves Contract A
serves the endpoint; it does **not** prove the four specific unresolved
identities would return rows — that is the follow-on acquisition task's
question, not this probe's.

## 7. Evidence preservation (immutable, append-only)

New directory `docs/phase5b/contract_a_probe/` on the execution branch,
containing:

- `probe_contract_a.py` — task-owned recorder (evidence helper only, never
  imported by `smart_beta`, never modifies `ProxyTushareClient`).
- `attempts.jsonl` — append-only, one line per call, with `label`,
  `endpoint`, `params`, `attempted_at_utc`, `http_status`, `error_code`,
  `classification`, and the raw-body filename.
- `raw/` — every response body verbatim, exactly as received.
- `classification.json` — the classified matrix + the §6 conclusions.
- `CAPABILITY_REPORT.md` — the §10 report block.

The recorder must implement a **raw HTTPS GET** (query-string params +
`X-API-Key` header, one attempt, no retry) against the frozen Contract-A
URL template. It must **not** reuse `ProxyTushareClient`, which hard-codes
the Contract-B base URL, assumes Contract-B's envelope, and performs retry
— all three are wrong for this probe. It must not import or modify any
production module.

The credential value must appear in **zero** committed files. The recorder
reads `TUSHARE_BASIC_PROXY_TOKEN` at call time and writes only the variable
name, never its value.

## 8. Scope / boundary — no overclaim

Regardless of outcome — including a future `SERVES-DATA` result for
`income` and/or `fina_indicator`:

- The four B2 identities remain unresolved unless and until a
  **separately-authorized** bounded acquisition task actually acquires
  them and their PIT/vintage semantics are independently verified.
- A successful CA1 result does **not** itself authorize: B2 acquisition,
  retrying the four missing identities, a Barrier 4a reopen, Stage 4,
  P5B-7, a new pilot universe, new pilot dates, factor computation, or
  production integration.
- `PROVED` capability here does **not** by itself reopen Barrier 4a and
  does **not** establish Contract A as a qualifying "material exogenous
  change" (that requires independent verification of a materially
  different provider/path *before* any new attempt, per D4A-HOLD). A
  successful CA1 result would only become evidence for a later,
  separately-authorized determination of whether Contract A constitutes a
  materially distinct acquisition path / material exogenous change.

```
P5B-6 Stage 4:  NOT AUTHORIZED
Barrier 4a:     NOT PASSED
P5B-7:          BLOCKED
```

These three lines are unchanged by this task's result, including a
`PROVED` result.

## 9. Branch / ownership

This task owns only: `docs/phase5b/contract_a_probe/` (new) and nothing
else. It must not touch any production module, any existing test, any
sealed phase file (Phase 4D-B / Phase 5A certifications), any P5B-6
evidence-branch file, or the frozen `phase5b-plan.md`. Execution runs on
its own dedicated branch, preserved as evidence (not merged as a completed
implementation), exactly like P5B-ST2/P5B-B2R.

## 10. Required completion report format

Return exactly this report, then stop:

```
PHASE 5B P5B-CA1 — CONTRACT-A CAPABILITY PROBE REPORT

BASE MASTER:
BRANCH:
CONTRACT-A URL TEMPLATE USED (scheme/host/path):
ENVIRONMENT VARIABLE: TUSHARE_BASIC_PROXY_TOKEN
TLS PRE-FLIGHT (Call 1): TLS-OK / TLS-UNREACHABLE / HTTP-200-NO-AUTH / HTTP-401-AUTH-REQUIRED
CREDENTIAL-TRANSPORT-RISK BLOCKER: NONE / TRIGGERED (hard stop)
N LIVE CALLS (max 6):
  Call 1 (pre-flight, unauthenticated): ...
  Call 2 stock-basic (credentialed): CLASS
  Call 3 income (ts_code=000001.SZ period=20250930): CLASS
  Call 4 fina_indicator (underscore): CLASS
  Call 5 fina-indicator (hyphen): CLASS
  Call 6 stock_basic (underscore): CLASS

CONTRACT-A INCOME CAPABILITY: PROVED (serves data) / PROVED (exists, empty) / CONTRADICTED / NOT-DETERMINABLE
CONTRACT-A FINA_INDICATOR CAPABILITY: PROVED (serves data) / PROVED (exists, empty) / CONTRADICTED / NOT-DETERMINABLE
EXACT SPELLING THAT SERVED (income): ...
EXACT SPELLING THAT SERVED (fina_indicator): ...
RESPONSE ENVELOPE OBSERVED (verbatim, credential-free): ...
NAMING CONVENTION: HYPHEN / UNDERSCORE / BOTH / NOT-DETERMINABLE
ENDPOINT-NAME CONTRADICTION WITH REPO (if any): ...

FOUR B2 IDENTITIES ACQUIRED BY THIS TASK: NO (out of scope)
CONTRACT A IS A DISTINCT UNTESTED PATH FOR B2: NOT YET ESTABLISHED (capability only)
BARRIER 4A REOPEN: NO
P5B-6 STAGE 4: NOT AUTHORIZED
P5B-7: BLOCKED
CREDENTIAL VALUE EXPOSED: NO
LIVE CALLS: <n>
FILES MODIFIED OUTSIDE OWNED DIR: 0
EXACT WORKER COMMIT:
INDEPENDENT REVIEW:
MERGE AUTHORIZED: NO
NEXT ACQUISITION (B2 VIA CONTRACT-A) AUTHORIZED: NO
NEXT WAVE AUTHORIZED: NO
```
