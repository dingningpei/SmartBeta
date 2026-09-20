# P5B-4 Run-2 resume — staged migration + bounded re-probe (BLOCKED)

**Status: BLOCKED.** Run 2 was performed as one bounded pass; 27/36 identities
are `SUCCESS/ACCESSIBLE`, 9/36 remain `UPSTREAM/TRANSPORT FAILURE`, 0 are
explicit entitlement denials, 0 are `OTHER UNRESOLVED`. Per the authorized
instruction, **no Run 3 was performed**; this evidence is committed as-is.

This report supersedes only the *probe status* half of `README.md`; the
Run-1 selection and evidence remain intact and are not rewritten.

## Authorization and merge (step 1)

* Run-2 authorized by the planner as a resume of P5B-4.
* `master` (`7db905142ddbc89cacecf693b1de49618b99d394`, P5B-5 merge) was
  merged into this branch with an **ordinary merge** (not rebase):
  `b7ff440`. Run-1's commit `d245beb1387802e3420abb1f1fd189fb4a971e24` is an
  ancestor of `b7ff440` and was neither rewritten nor discarded.
* The merge brought in `scripts/staged_capture.py` and
  `tests/test_staged_capture.py`; neither was modified by P5B-4.

## Step 2 — migration (zero live calls)

`scripts/resume_phase5b_pilot_universe.py` constructs a `StagedCaptureSet`
over the same 36 `(ts_code, endpoint)` identities and stages the 13 Run-1
successes through `StagedCaptureSet.ingest_body()` using:

* their **exact original raw bodies**, parsed from
  `docs/phase5b/pilot_universe/raw/<ts_code>__<endpoint>.json`; and
* the **exact Run-1 `retrieved_at` timestamp**,
  `2026-09-20T08:05:44Z`, taken verbatim from the Run-1 ledger
  (`_provenance.retrieved_at_utc`). Run 1 recorded a single run-level
  timestamp and no distinct per-call timestamp; using that one recorded value
  verbatim is the only non-fabricating choice, and it is applied identically
  to all 13.

Verified deterministically, with **zero live calls**:

* exactly **13** identities are staged-valid, and they are exactly Run-1's
  13 successes;
* the missing set is exactly the **23** identities Run 1 left unresolved;
* for every staged record, `body ==` the exact parsed Run-1 raw body,
  `retrieved_at_utc == 2026-09-20T08:05:44Z`, and
  `content_sha256 == staged_capture.content_sha256(raw body)`;
* the 13 Run-1 staged records are byte/provenance-unchanged after Run 2.

If any of those had failed, the runner would have stopped before any live
call. None did.

## Step 3 — Run 2 (exactly one live attempt per missing identity)

`ProxyTushareClient(max_attempts=1)` + `record(samples=1)` — the same
transport/endpoint semantics as Run 1: exactly one transport call per
identity, **no retry/backoff/sleep/substitution**. Exactly the 23 missing
identities were attempted, once each, in deterministic order; the 13
already-staged identities were never re-requested. Every attempt was
appended (with `fsync`) to `resume/run2_attempts.jsonl` and every raw body
was preserved under `resume/raw_run2/`. Results: **14 succeeded, 9 failed**.

Note on framework semantics: `StagedCaptureSet.run()` deliberately stops at
the first non-success, which cannot make one attempt per each of 23
identities. Run 2 therefore drove the framework's staging/provenance through
its public `ingest_body()` and recorded outcomes in an append-only ledger,
which is the requested "one live attempt per missing identity" pass. No
framework file was modified.

## Step 4 — combined 4-bucket classification (all 36)

Buckets are assigned from **actual captured status/body evidence** (Run 1 +
Run 2), with precedence `SUCCESS/ACCESSIBLE > EXPLICIT ENTITLEMENT DENIAL >
UPSTREAM/TRANSPORT FAILURE > OTHER UNRESOLVED`. A transport failure was never
converted into an entitlement denial, or vice versa. No identity produced an
explicit entitlement denial; every failure is HTTP 503
`upstream_pool_exhausted`.

| # | identity | Run-1 (status/error) | Run-2 (status/error) | bucket |
| --- | --- | --- | --- | --- |
| 1 | `000001.SZ::daily` | 503 upstream_pool_exhausted | 200 | SUCCESS/ACCESSIBLE |
| 2 | `000001.SZ::daily_basic` | 503 upstream_pool_exhausted | 200 | SUCCESS/ACCESSIBLE |
| 3 | `000001.SZ::stk_limit` | 503 upstream_pool_exhausted | 503 upstream_pool_exhausted | UPSTREAM/TRANSPORT FAILURE |
| 4 | `000001.SZ::suspend_d` | 503 upstream_pool_exhausted | 200 | SUCCESS/ACCESSIBLE |
| 5 | `000001.SZ::dividend` | 200 | (not attempted; Run-1 success) | SUCCESS/ACCESSIBLE |
| 6 | `000001.SZ::stock_basic` | 503 upstream_pool_exhausted | 503 upstream_pool_exhausted | UPSTREAM/TRANSPORT FAILURE |
| 7 | `000001.SZ::income` | 200 | (not attempted; Run-1 success) | SUCCESS/ACCESSIBLE |
| 8 | `000001.SZ::balancesheet` | 200 | (not attempted; Run-1 success) | SUCCESS/ACCESSIBLE |
| 9 | `000001.SZ::fina_indicator` | 200 | (not attempted; Run-1 success) | SUCCESS/ACCESSIBLE |
| 10 | `600519.SH::daily` | 200 | (not attempted; Run-1 success) | SUCCESS/ACCESSIBLE |
| 11 | `600519.SH::daily_basic` | 200 | (not attempted; Run-1 success) | SUCCESS/ACCESSIBLE |
| 12 | `600519.SH::stk_limit` | 200 | (not attempted; Run-1 success) | SUCCESS/ACCESSIBLE |
| 13 | `600519.SH::suspend_d` | 503 upstream_pool_exhausted | 200 | SUCCESS/ACCESSIBLE |
| 14 | `600519.SH::dividend` | 200 | (not attempted; Run-1 success) | SUCCESS/ACCESSIBLE |
| 15 | `600519.SH::stock_basic` | 503 upstream_pool_exhausted | 200 | SUCCESS/ACCESSIBLE |
| 16 | `600519.SH::income` | 503 upstream_pool_exhausted | 503 upstream_pool_exhausted | UPSTREAM/TRANSPORT FAILURE |
| 17 | `600519.SH::balancesheet` | 200 | (not attempted; Run-1 success) | SUCCESS/ACCESSIBLE |
| 18 | `600519.SH::fina_indicator` | 503 upstream_pool_exhausted | 200 | SUCCESS/ACCESSIBLE |
| 19 | `601318.SH::daily` | 200 | (not attempted; Run-1 success) | SUCCESS/ACCESSIBLE |
| 20 | `601318.SH::daily_basic` | 503 upstream_pool_exhausted | 200 | SUCCESS/ACCESSIBLE |
| 21 | `601318.SH::stk_limit` | 503 upstream_pool_exhausted | 200 | SUCCESS/ACCESSIBLE |
| 22 | `601318.SH::suspend_d` | 503 upstream_pool_exhausted | 200 | SUCCESS/ACCESSIBLE |
| 23 | `601318.SH::dividend` | 200 | (not attempted; Run-1 success) | SUCCESS/ACCESSIBLE |
| 24 | `601318.SH::stock_basic` | 503 upstream_pool_exhausted | 503 upstream_pool_exhausted | UPSTREAM/TRANSPORT FAILURE |
| 25 | `601318.SH::income` | 503 upstream_pool_exhausted | 200 | SUCCESS/ACCESSIBLE |
| 26 | `601318.SH::balancesheet` | 503 upstream_pool_exhausted | 200 | SUCCESS/ACCESSIBLE |
| 27 | `601318.SH::fina_indicator` | 503 upstream_pool_exhausted | 503 upstream_pool_exhausted | UPSTREAM/TRANSPORT FAILURE |
| 28 | `000858.SZ::daily` | 503 upstream_pool_exhausted | 503 upstream_pool_exhausted | UPSTREAM/TRANSPORT FAILURE |
| 29 | `000858.SZ::daily_basic` | 503 upstream_pool_exhausted | 503 upstream_pool_exhausted | UPSTREAM/TRANSPORT FAILURE |
| 30 | `000858.SZ::stk_limit` | 503 upstream_pool_exhausted | 503 upstream_pool_exhausted | UPSTREAM/TRANSPORT FAILURE |
| 31 | `000858.SZ::suspend_d` | 503 upstream_pool_exhausted | 200 | SUCCESS/ACCESSIBLE |
| 32 | `000858.SZ::dividend` | 503 upstream_pool_exhausted | 503 upstream_pool_exhausted | UPSTREAM/TRANSPORT FAILURE |
| 33 | `000858.SZ::stock_basic` | 200 | (not attempted; Run-1 success) | SUCCESS/ACCESSIBLE |
| 34 | `000858.SZ::income` | 503 upstream_pool_exhausted | 200 | SUCCESS/ACCESSIBLE |
| 35 | `000858.SZ::balancesheet` | 503 upstream_pool_exhausted | 200 | SUCCESS/ACCESSIBLE |
| 36 | `000858.SZ::fina_indicator` | 200 | (not attempted; Run-1 success) | SUCCESS/ACCESSIBLE |

**Totals:** SUCCESS/ACCESSIBLE = **27**; EXPLICIT ENTITLEMENT DENIAL = **0**;
UPSTREAM/TRANSPORT FAILURE = **9**; OTHER UNRESOLVED = **0**.

## Remaining identity list (9, all `UPSTREAM/TRANSPORT FAILURE`)

```
000001.SZ::stk_limit
000001.SZ::stock_basic
600519.SH::income
601318.SH::stock_basic
601318.SH::fina_indicator
000858.SZ::daily
000858.SZ::daily_basic
000858.SZ::stk_limit
000858.SZ::dividend
```

Every one failed with HTTP 503 `{"error": "upstream_pool_exhausted"}` in
both Run 1 and Run 2. This is proxy-pool contention, not an entitlement
finding.

## Every live call made in Run 2 (23)

| # | identity | HTTP | bucket |
| --- | --- | --- | --- |
| 1 | `000001.SZ::daily` | 200 | SUCCESS/ACCESSIBLE |
| 2 | `000001.SZ::daily_basic` | 200 | SUCCESS/ACCESSIBLE |
| 3 | `000001.SZ::stk_limit` | 503 | UPSTREAM/TRANSPORT FAILURE |
| 4 | `000001.SZ::suspend_d` | 200 | SUCCESS/ACCESSIBLE |
| 5 | `000001.SZ::stock_basic` | 503 | UPSTREAM/TRANSPORT FAILURE |
| 6 | `600519.SH::suspend_d` | 200 | SUCCESS/ACCESSIBLE |
| 7 | `600519.SH::stock_basic` | 200 | SUCCESS/ACCESSIBLE |
| 8 | `600519.SH::income` | 503 | UPSTREAM/TRANSPORT FAILURE |
| 9 | `600519.SH::fina_indicator` | 200 | SUCCESS/ACCESSIBLE |
| 10 | `601318.SH::daily_basic` | 200 | SUCCESS/ACCESSIBLE |
| 11 | `601318.SH::stk_limit` | 200 | SUCCESS/ACCESSIBLE |
| 12 | `601318.SH::suspend_d` | 200 | SUCCESS/ACCESSIBLE |
| 13 | `601318.SH::stock_basic` | 503 | UPSTREAM/TRANSPORT FAILURE |
| 14 | `601318.SH::income` | 200 | SUCCESS/ACCESSIBLE |
| 15 | `601318.SH::balancesheet` | 200 | SUCCESS/ACCESSIBLE |
| 16 | `601318.SH::fina_indicator` | 503 | UPSTREAM/TRANSPORT FAILURE |
| 17 | `000858.SZ::daily` | 503 | UPSTREAM/TRANSPORT FAILURE |
| 18 | `000858.SZ::daily_basic` | 503 | UPSTREAM/TRANSPORT FAILURE |
| 19 | `000858.SZ::stk_limit` | 503 | UPSTREAM/TRANSPORT FAILURE |
| 20 | `000858.SZ::suspend_d` | 200 | SUCCESS/ACCESSIBLE |
| 21 | `000858.SZ::dividend` | 503 | UPSTREAM/TRANSPORT FAILURE |
| 22 | `000858.SZ::income` | 200 | SUCCESS/ACCESSIBLE |
| 23 | `000858.SZ::balancesheet` | 200 | SUCCESS/ACCESSIBLE |

No other live network calls were made in Run 2 (the migration step made
zero); no identity was attempted more than once; no Run 3 was performed.

## Preserved dispositions (verbatim, not upgraded)

* `PROXY DETERMINISM = NOT CERTIFIED`
* `PROXY SUFFICIENT FOR PRODUCTION = NO`

This is proxy-observed evidence from `pcd.mobcvb.cn/tushare/pro`, not direct
official-Tushare behavior. It is not a canonical set, not a production data
source, and it does not clear Barrier 3 while 9 identities remain unresolved.

## Evidence files

| File | Contents |
| --- | --- |
| `resume/staging/` | 27 staged records (13 Run-1 + 14 Run-2) with body, provenance, canonical hash |
| `resume/run2_attempts.jsonl` | append-only Run-2 attempt ledger (23 lines, one per attempt, fsync'd) |
| `resume/raw_run2/` | 23 verbatim Run-2 raw response bodies (including the 9 error bodies) |
| `resume/classification.json` | combined per-identity 4-bucket classification + evidence pointers |
| `resume/run2_run.log` | the human-readable Run-2 run log |
