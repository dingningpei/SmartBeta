# Task P5B-CA1-T1 — Contract-A Basic Capability Probe

**STATUS: FROZEN SPEC (spec/governance only).** This document freezes the
corrected, minimized CA1-T1 capability probe for **later worker execution
only**. It authorizes **zero** live calls now, does **not** execute the
probe, does **not** create an execution branch/worktree, does **not** access
either credential, and does **not** implement production code. No live calls
were made to produce this document.

Base master: `12bde9824259dcf263789da245a7f224d311c99e`
Original CA1 spec: `worker_tasks/phase5b/task-p5b-ca1-contract-a-capability-probe.md`
Original CA1 evidence branch: `phase5b/task-p5b-ca1-contract-a-capability-probe`
Original CA1 evidence commit: `57fc53a7255405b8a253c713e23449aa4e2e0bc4`
Supersedes: `worker_tasks/phase5b/task-p5b-ca1-t1-http-transport-amendment.md`
(commit `6bcbdeb5f9b4567463f4ec220d54c8ee75b6cf3e`) — its 5-call matrix is
replaced by the 3-call matrix below after the provider manual resolved
endpoint-name ambiguity.

## 1. Historical context (preserved, never rewritten)

The original CA1 execution made exactly one live call: the credential-free
HTTPS/TLS pre-flight to
`https://datahubco.com/app-api/openapi/v1/tushare/stock-basic`. It received
**no HTTP response** (connection timeout). Per the original spec's
deterministic early-stop and TLS hard stop, `CREDENTIAL-TRANSPORT-RISK`
triggered: **no credential was read or transmitted**, and original Calls 2–6
were not attempted.

**Original CA1 disposition (final):** 1 live call, `TLS-UNREACHABLE`,
`CREDENTIAL-TRANSPORT-RISK` hard stop, `income`/`fina_indicator` =
`NOT-DETERMINABLE`. This result remains permanently on branch
`phase5b/task-p5b-ca1-contract-a-capability-probe` at commit
`57fc53a7255405b8a253c713e23449aa4e2e0bc4` and is **not** rewritten,
amended, squashed, merged away, or reinterpreted as success.

## 2. New authoritative provider evidence (customer API manuals)

The provider has supplied two customer API manuals, which materially clarify
the transport and endpoint contracts. Contract A and Contract B are **not**
the same acquisition route.

**Contract A / Basic manual (`15000积分基础功能.txt`), provider-documented:**

- public domain: `datahubco.com`
- customer route: `GET /app-api/openapi/v1/tushare/{api}`
- current `BASE_URL`: `http://datahubco.com`
- authentication: `X-API-Key`
- server currently provides **HTTP port 80**; **HTTPS is not yet configured**
- the provider warns against transmitting the credential over untrusted
  networks until HTTPS is configured
- API names use **lowercase underscore** spelling
- `income` is documented
- `fina_indicator` is documented
- `code=0` with `items=[]` means a successful query with no matching data
- response data must be interpreted using `data.fields`, never guessed
  fixed column positions

**Contract B / ProMax manual (`promax.txt`), provider-documented (already
used):**

- base: `https://pcd.mobcvb.cn/tushare/pro`
- HTTPS, `X-API-Key`
- `income` and `fina_indicator` documented

## 3. User transport-risk decision (explicit project-level exception)

The user has explicitly reviewed and accepted the risk of transmitting the
provider-issued Contract-A credential over the provider-documented plaintext
HTTP route for this narrowly bounded capability experiment.

This is an explicit, project-level transport exception. It does **not** mean
plaintext HTTP is generally secure. It applies **only** to the P5B-CA1-T1
capability probe, using `TUSHARE_BASIC_PROXY_TOKEN` against
`http://datahubco.com/app-api/openapi/v1/tushare/{api}`.

It does **not** authorize plaintext HTTP for: `TUSHARE_PROXY_TOKEN`,
Contract B, other credentials, other providers, production acquisition,
future experiments, or the four B2 blocker identities.

## 4. Credential boundary

- Contract A / Basic credential: `TUSHARE_BASIC_PROXY_TOKEN` (only).
- Contract B / ProMax credential: `TUSHARE_PROXY_TOKEN` (distinct).
- Under no circumstance may `TUSHARE_PROXY_TOKEN` be sent to Contract A.
- Do not print, persist, hash, log, commit, or expose either credential.
- `TUSHARE_BASIC_PROXY_TOKEN` travels only in the `X-API-Key` header, never
  in a URL, body, log, evidence file, or commit.

## 5. Why the original CA1 matrix must change

The provider manual now resolves endpoint-name ambiguity. The documented
Contract-A financial endpoint is **`fina_indicator`** (lowercase underscore).
Therefore the speculative probes for `fina-indicator` (hyphen variant) and
`stock_basic` (exploratory underscore disambiguation) are **removed**. The
capability experiment is minimized to the three load-bearing calls below.

## 6. Frozen CA1-T1 experiment

**Experiment identity:** `P5B-CA1-T1`

**Purpose:** determine whether the newly documented Contract-A / Basic
gateway is a functioning, materially distinct candidate acquisition path
for the financial endpoint classes needed by Barrier 4a recovery.

**Maximum future live calls: 3.** Exactly one attempt per call. No retries,
no replacement calls, no endpoint guessing, no alternate parameter shapes,
no B2 requests.

## 7. Frozen CA1-T1 request matrix (exact order)

| Call | endpoint | query params | credential | purpose |
|---|---|---|---|---|
| T1-1 | `stock-basic` | `limit=3` | `X-API-Key` = `TUSHARE_BASIC_PROXY_TOKEN` | basic gateway / auth baseline |
| T1-2 | `income` | `ts_code=000001.SZ&period=20250930` | present | income positive control |
| T1-3 | `fina_indicator` | `ts_code=000001.SZ&period=20250930` | present | fina_indicator positive control |

All three are `GET http://datahubco.com/app-api/openapi/v1/tushare/{api}`
with query-string parameters and the `X-API-Key` header.

**T1-1 — basic gateway / auth baseline.** Establishes that the Contract-A
HTTP gateway and Contract-A credential are usable. Uses the
provider-documented minimal baseline shape with `limit=3` (exactly as the
provider documents). If this call cannot establish usable authenticated
Contract-A service: **HARD STOP.** Do not execute T1-2 or T1-3. No retry,
no auth guessing.

**T1-2 — income positive control.** Establishes whether Contract A serves
`income` using the financial request shape required by the project
(`ts_code=000001.SZ`, `period=20250930`).

**T1-3 — fina_indicator positive control.** Establishes whether Contract A
serves `fina_indicator` using the financial request shape required by the
project (`ts_code=000001.SZ`, `period=20250930`).

## 8. Positive control

Financial positive-control identity: `000001.SZ`, `period=20250930`. This
is intentionally **not** one of the unresolved B2 identities. Do **not**
substitute another stock or period if a call is empty or failed.

## 9. B2 hard boundary

The following identities remain **STRICTLY FORBIDDEN** in CA1-T1:

```
601318.SH::fina_indicator::20260630
000858.SZ::fina_indicator::20250930
000858.SZ::income::20251231
000858.SZ::fina_indicator::20260331
```

No request for any of these identities is authorized. CA1-T1 is capability
evidence only.

## 10. Classification (unchanged taxonomy)

Each attempted request must be classified using the existing evidence
discipline. At minimum preserve: `SERVES-DATA`, `SERVES-EMPTY`,
`ENDPOINT-MISSING`, `SHAPE-MISMATCH`, `ENTITLEMENT-DENIED`,
`TRANSPORT-FAILURE`.

- HTTP success alone is insufficient.
- Provider `code=0` plus `items=[]` is **`SERVES-EMPTY`**, not a transport
  failure.
- Do not infer entitlement denial solely from HTTP status without examining
  the sanitized provider response.
- Do not upgrade `SERVES-EMPTY` to `SERVES-DATA`.
- Interpret returned rows by `data.fields`; do not guess fixed column
  positions.

## 11. Evidence requirements (for future execution)

The later worker execution must record sanitized evidence sufficient to
establish: experiment ID, call identity, endpoint, non-secret request
parameters, HTTP status, provider code, sanitized provider message, response
envelope shape, `data.fields`, row count, classification, and whether later
calls were skipped due to a hard stop. Where available, record non-secret
provider/cache provenance headers (e.g. data-source/cache status), but do
**not** treat cache provenance as semantic certification.

Never record: the credential value, the `X-API-Key` value, or any
authorization material.

## 12. Anti-upgrade rules

Even if all three calls succeed, CA1-T1:

- does **not** establish that the four B2 identities exist;
- does **not** certify PIT/vintage semantics;
- does **not** certify `profit_dedt` semantics;
- does **not** certify complete historical coverage;
- does **not** certify Contract A for production;
- does **not** automatically reopen Barrier 4a;
- does **not** authorize P5B-7.

CA1-T1 establishes only bounded empirical capability of a distinct candidate
acquisition route.

## 13. Post-probe governance

If T1-1, T1-2, and T1-3 establish usable Contract-A capability: **STOP**. A
separate governance review must determine whether the combination of a
distinct base URL, distinct credential, distinct gateway/service contract,
provider documentation, and successful independent capability evidence
qualifies Contract A as a material exogenous change / legitimate B2 recovery
path under D4A governance. Only that later review may authorize requests for
the four B2 blocker identities.

## 14. Production boundary

Do **not** modify: `ProxyTushareClient`, production provider routing, PIT
selection logic, factor code, B2 capture artifacts, existing P5B-6 evidence,
ST evidence, or Barrier-4a disposition evidence. No production integration
is authorized.

## 15. Governance status after this freeze

```
Barrier 4a:                       NOT PASSED
D4A:                              HOLD
P5B-7:                            BLOCKED
B2 identities authorized:         NO
CA1-T1 live calls (frozen, later worker only): 3 maximum
Live calls in this planning task: 0
```
