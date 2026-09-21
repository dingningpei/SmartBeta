# D4A Contract-A Recovery Eligibility Review

**OFFLINE GOVERNANCE REVIEW.** Live provider calls made to produce this
document: **0**. No credential was accessed, no B2 identity was requested,
and no production code was modified.

- Master before this review: `a994a8bd463432b6ace08c541534ce446a6a92c0`
- CA1-T1 evidence reviewed: `97e082e7973e64bd421343880b3d878748a086da`
  (branch `phase5b/task-p5b-ca1-t1-basic-capability-probe`, reviewed
  directly — not merged)
- Original CA1 evidence: `57fc53a7255405b8a253c713e23449aa4e2e0bc4`
  (branch `phase5b/task-p5b-ca1-contract-a-capability-probe`, preserved)

## 1. Question decided

Whether the newly documented and independently probed Contract A / Basic
service qualifies under the existing D4A-HOLD governance standard as **a
material exogenous change and/or a materially distinct legitimate
acquisition path** sufficient to authorize a narrowly bounded B2 recovery
experiment.

This is **eligibility only**. It is not a review of whether Barrier 4a has
passed, and it does not authorize B2 execution.

## 2. Historical blocker

P5B-6 used Contract B / ProMax (`https://pcd.mobcvb.cn/tushare/pro/{api}`,
credential class `TUSHARE_PROXY_TOKEN`). It successfully acquired many
`income`/`fina_indicator` identities, but four exact B2 identities remained
unresolved after the frozen Policy C capture budget was exhausted:

```
601318.SH::fina_indicator::20260630
000858.SZ::fina_indicator::20250930
000858.SZ::income::20251231
000858.SZ::fina_indicator::20260331
```

Their failures were transport/service failures (`HTTP 503
upstream_pool_exhausted` and connection drops), **not** certified
entitlement denials and **not** proof that the records do not exist. No
fourth ProMax retry was authorized, and a fourth ProMax retry would be the
explicitly non-qualifying "try again / same proxy / same endpoint / same
parameters / same identity / reset Policy C" pattern.

## 3. Existing D4A standard (applied verbatim)

From `docs/phase5b/BARRIER_4A_DISPOSITION.md` (D4A-HOLD):

> **Qualifying material exogenous changes** (any one, independently
> verified *before* any new attempt, never inferred): a materially
> different data provider; an independently confirmed upgraded official
> Tushare entitlement; a newly available qualifying institutional/licensed
> source with explicit semantic equivalence established first (field,
> report period, vintage identity, knowledge date, PIT/revision behavior);
> an independently observable change to the proxy/provider's own
> infrastructure; a newly certified batch/range acquisition contract that
> changes the failed identity's request shape itself; an authoritative
> filing source with independently established PIT semantics.
>
> **Explicitly non-qualifying:** elapsed time; desire to continue; "try
> again"; the same proxy; the same endpoint; the same parameters; the same
> missing identity; simply resetting the Policy C invocation counter.

This standard is applied without weakening it merely because CA1-T1
succeeded.

## 4. Evidence reviewed

1. **`docs/phase5b/BARRIER_4A_DISPOSITION.md` (master)** — the D4A-HOLD
   disposition and its qualifying/non-qualifying lists (§3 above).
2. **P5B-6 acquisition history** (`phase5b/task-p5b-6-ch3-pilot`, terminal
   `24b7d08…`) — 80/84 final coverage, Policy C `3/3` exhausted, four B2
   gaps all `UPSTREAM/TRANSPORT FAILURE`, 0 entitlement denials across 134
   live calls.
3. **Original CA1 evidence** (`57fc53a…`) — one credential-free HTTPS
   pre-flight, `TLS-UNREACHABLE` timeout, `CREDENTIAL-TRANSPORT-RISK` hard
   stop, no credential read or transmitted, Calls 2–6 not attempted.
   Preserved and not reinterpreted.
4. **Frozen CA1-T1 spec**
   (`worker_tasks/phase5b/task-p5b-ca1-t1-basic-capability-probe.md` at
   `a994a8b…`) — 3-call matrix, user-accepted plaintext-HTTP transport
   exception, B2 identities strictly forbidden.
5. **CA1-T1 evidence** (`97e082e…`) — exactly 3 of 3 frozen calls, one
   attempt each, no retries. T1-1 `stock-basic?limit=3` → HTTP 200,
   `code=0`, `SERVES-DATA`, 3 rows (`X-Cache: HIT`, `X-Data-Source:
   redis`). T1-2 `income` → HTTP 200, `code=0`, `SERVES-DATA`, 1 row
   (`X-Cache: MISS`, `X-Data-Source: upstream`). T1-3 `fina_indicator` →
   HTTP 200, `code=0`, `SERVES-DATA`, 1 row, fields include `profit_dedt`
   (`X-Cache: MISS`, `X-Data-Source: upstream`). No B2 identity requested;
   credential value absent from all committed bytes.
6. **Provider-contract facts** (transcribed in the frozen CA1-T1 spec) —
   Contract A: `http://datahubco.com/app-api/openapi/v1/tushare/{api}`,
   `X-API-Key`, plaintext HTTP (HTTPS not configured), lowercase-underscore
   API names, `income` and `fina_indicator` documented.

## 5. Material-distinction analysis

Contract A is distinct from the exhausted Contract B / ProMax route in at
least every load-bearing dimension:

| Dimension | Contract B (exhausted) | Contract A (candidate) | Distinct? |
|---|---|---|---|
| Base URL / hostname | `https://pcd.mobcvb.cn` | `http://datahubco.com` | YES |
| Gateway path | `/tushare/pro/{api_name}` | `/app-api/openapi/v1/tushare/{api}` | YES |
| Credential class | `TUSHARE_PROXY_TOKEN` | `TUSHARE_BASIC_PROXY_TOKEN` | YES |
| Transport contract | HTTPS | plaintext HTTP (HTTPS not configured) | YES |
| Documented service contract | ProMax manual | Basic manual | YES |
| Interface catalog / naming | (repo-established set) | lowercase-underscore, `income` + `fina_indicator` documented | YES |

`TUSHARE_PROXY_TOKEN` may never be sent to Contract A; the two routes are
independent providers with independent credentials and independent
service contracts.

## 6. Empirical capability (independently verified, not inferred)

The provider statement alone would be insufficient; the CA1-T1 probe
independently confirmed, with real HTTP 200 + `code=0` + populated
`data.items`:

- **`income` capability: PROVED (`SERVES-DATA`)** — positive control
  `000001.SZ` / `period=20250930`, 1 row, 85 fields.
- **`fina_indicator` capability: PROVED (`SERVES-DATA`)** — positive control
  `000001.SZ` / `period=20250930`, 1 row, 108 fields (including
  `profit_dedt`; semantics NOT certified).
- **`stock-basic` baseline: `SERVES-DATA`** — 3 rows, authenticated gateway
  usable.

`X-Data-Source` / `X-Cache` headers are recorded as provenance observations
only and are **not** treated as semantic certification.

## 7. Would a Contract-A B2 experiment be a prohibited Contract-B retry?

**NO.** The exhausted Policy C budget was scoped to the Contract B / ProMax
proxy (`pcd.mobcvb.cn`) and its credential. Contract A is a different
provider (different hostname, different gateway path, different credential,
different service contract). Requesting the four B2 identities through
Contract A is therefore not "the same proxy + the same endpoint + the same
parameters + the same identity" in the non-qualifying sense — the
**acquisition route** is materially different even though the abstract data
identity (stock/period/endpoint class) is the same. The "same proxy"
non-qualifier targets the provider/route, not the logical data identity.

## 8. Anti-upgrade — what remains NOT CERTIFIED

A positive eligibility decision means only that Contract A is sufficiently
materially distinct to justify a new, separately bounded recovery
experiment. The following remain NOT CERTIFIED / NOT ESTABLISHED:

- the four B2 identities exist on Contract A;
- complete historical coverage;
- PIT/vintage semantics;
- `profit_dedt` semantic equivalence;
- Contract A production readiness;
- HTTP transport security (plaintext; user-accepted exception only);
- Barrier 4a;
- P5B-7 eligibility.

## 9. Verdict

**`D4A-CA-ELIGIBLE`**

Contract A satisfies the first D4A qualifying criterion — **a materially
different data provider** — and that distinction is independently verified
by the CA1-T1 empirical probe (real `SERVES-DATA` for both `income` and
`fina_indicator`), not inferred from the provider statement alone.

## 10. Permitted scope ceiling (for a subsequent frozen recovery spec)

Because the verdict is ELIGIBLE, a subsequent recovery spec **may** be
created with maximum scope no broader than:

- exactly the four unresolved B2 identities;
- one Contract-A attempt per identity;
- no substitute stocks, no substitute periods;
- no universe shrink;
- no Contract-B retry;
- no automatic retry;
- no production integration.

This review does **not** itself create or authorize execution of that spec;
it only establishes that such a spec is permitted to be created.

## 11. What this review does NOT authorize

- B2 live execution (a separate frozen recovery spec + separate execution
  authorization are required);
- Barrier 4a reopen;
- P5B-7;
- production integration;
- merging either CA1 evidence branch;
- push or tag.

## 12. Repository action

This governance review is committed to `master`. The CA1-T1 evidence branch
(`97e082e…`) and the original CA1 evidence branch (`57fc53a…`) are **not**
merged. No push, no tag, no production-code change.
