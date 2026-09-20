# Phase 5B pilot universe — selection and bounded entitlement probe (P5B-4)

**Task:** P5B-4 (Phase 5B Wave 3). **Owned artifacts:** this directory and
`scripts/probe_phase5b_pilot_universe.py`. **No production code was changed.**

**Dispositions (stated plainly, not smoothed):**

| Item | Disposition |
| --- | --- |
| Pilot universe selection (per the frozen Phase 5B procedure) | **COMPLETE** — 4 names, frozen below |
| Bounded Tushare entitlement probe | **PROBE_BLOCKED** — 13/36 identities HTTP 200; 23/36 HTTP 503 `upstream_pool_exhausted` (transient proxy-pool contention) |
| Entitlement denial of any probed identity | **NONE OBSERVED** — zero HTTP 400/403, zero plan-tier/permission bodies |
| Universe **entitlement-cleared** (Barrier 3 evidence) | **NO** — not cleared by this run |

A blocked probe is not a cleared universe. Nothing below may be read as
entitlement certification, as direct official-Tushare behavior, or as a
freeze of Barrier 3.

Provenance classes used below are kept distinct: **proxy-observed live
evidence** (this run, or the committed Phase 4D-B live fixtures, explicitly
named), **public knowledge not re-probed by this run**, and **direct
official-Tushare behavior** (never exercised or certified here).

---

## 1. Frozen pilot universe

Selected per the Phase 5B plan's "Pilot universe" procedure, before any
live probe call:

| # | `ts_code` | Name | Exchange / board | Listing evidence (class) |
| --- | --- | --- | --- | --- |
| 1 | `000001.SZ` | 平安银行 Ping An Bank | SZSE main | `list_date=19910403`, `list_status="L"`, no `delist_date` — **proxy-observed live** (committed Phase 4D-B fixture `tests/fixtures/tushare/listing/stock_basic_000001.SZ.json`) |
| 2 | `600519.SH` | 贵州茅台 Kweichow Moutai | SSE main | long-listed; **not re-probed this run** (its `stock_basic` call was among the HTTP 503s) |
| 3 | `601318.SH` | 中国平安 Ping An Insurance | SSE main | long-listed; **not re-probed this run** (its `stock_basic` call was among the HTTP 503s) |
| 4 | `000858.SZ` | 五粮液 Wuliangye | SZSE main | `list_date=19980427`, `list_status="L"`, no `delist_date` — **proxy-observed live** (`raw/000858.SZ__stock_basic.json`, this run) |

### How the frozen procedure is satisfied

* **Size (3–5):** 4 real, currently-listed, large-cap A-shares.
* **Preferred Phase 4D-B specimen:** `000001.SZ` is included. It is
  Phase 4D-B's clean, still-listed specimen (items 1, 3, 7, 8, 9, 11 of
  `docs/phase4d_b_tushare_certification.md`), and the pilot needs at least
  one fully clean hand-verification specimen.
* **Exchange coverage:** two `.SH` names and two `.SZ` names, so both the
  SSE and SZSE sides of the exchange calendar are exercised.
* **History requirement:** both mechanically confirmed names were listed in
  the 1990s and remain `list_status="L"` with no `delist_date`. That covers
  the whole plausible pilot window with ample margin for a full 250-trading-
  day turnover lookback and multiple fiscal-report cycles. `600519.SH`
  (listed 2001) and `601318.SH` (listed 2007) are likewise far older than any
  plausible pilot window — **stated as public knowledge, not as a live
  observation of this run.**
* **Exclusion rule — no name whose only in-window fiscal year is known to
  raise an unresolved `TushareConflictingVintageError`:** Phase 4D-B's one
  known conflicting-vintage specimen is `002450.SZ` FY2015 (item 3), which is
  **delisted** (`list_status="D"`, `delist_date=20210531`, item 11) and is
  therefore excluded by the *currently-listed* requirement regardless. None
  of the four selected names has any known conflicting-vintage finding in
  Phase 4D-B. The other Phase 4D-B-characterized names are not selected:
  `600518.SH` (suppressed `ni_ex_nonrecurring`, condition 2), `002069.SZ`
  (vintage-coverage counterexample, item 4; also not large-cap), and the
  `000024.SZ`/`001914.SZ` restructuring pair (identifier-continuity finding,
  item 10).
* **Representativeness — explicitly not claimed.** Canonical sentence: *"The
  Phase 5B pilot does not demonstrate representative China A-share market
  coverage, scalability, a China market factor, CH3/CH4 replication, economic
  significance, or statistical significance."*

---

## 2. Bounded entitlement probe design

`scripts/probe_phase5b_pilot_universe.py` makes **exactly one** live call per
`(ts_code, endpoint)` identity through `ProxyTushareClient.record(samples=1)`
— one transport call, no retry, no backoff, no sleep loop. A non-200 or a
malformed envelope is captured verbatim and drives a fail-closed
`PROBE_BLOCKED` disposition; no name or endpoint is substituted, skipped, or
retried. Raw response bodies (including error bodies) are preserved under
`raw/`.

**Identities:** 4 names × 9 endpoints = **36**. The endpoints are the ones the
P5B-2/P5B-3 PIT-native CH3/CH4 path actually fetches:

| Endpoint | Parameter shape | Why the pilot needs it |
| --- | --- | --- |
| `daily` | `ts_code,start_date,end_date` | raw returns, `vol` (B2), `close`/`pre_close`, `adj` inputs |
| `daily_basic` | `ts_code,start_date,end_date` | `total_mcap` (canonical), `total_share` (B2) |
| `stk_limit` | `ts_code,start_date,end_date` | limit-up/limit-down tradability check |
| `suspend_d` | `ts_code` | suspension tradability check |
| `dividend` | `ts_code` | corporate actions → adjusted returns |
| `stock_basic` | `ts_code,fields` | listing/delisting (`list_date`,`delist_date`,`list_status`) |
| `income` | `ts_code,period` | CH3 `ni_ex_nonrecurring` vintage assembly |
| `balancesheet` | `ts_code,period` | CH3 `ni_ex_nonrecurring` vintage assembly |
| `fina_indicator` | `ts_code,period` | CH3 `ni_ex_nonrecurring` (`profit_dedt`) |

`bak_basic` is deliberately **not** probed: the assembled
`TushareAShareSource` never fetches it, so it is not an entitlement the pilot
depends on. (Phase 4D-B already records same-day ST name-source completeness
as `NOT CERTIFIED`; that pre-existing boundary is unchanged here.)

**Bounded probe parameters** (chosen before the live call, recorded in the
ledger): date-ranged endpoints use `2026-08-03..2026-08-31` (one calendar
month, far inside the proxy's documented ≤366-day cap); fundamentals use the
single bounded period `20251231` (the most recent annual report a late-2026
pilot resolves E/P against). These are **entitlement-probe** parameters, not
a pilot-window freeze.

**Day-count note:** the China risk-free leg (B1) is a single national PBOC
series independent of these names and is probed separately, once, against
PBOC's own published source. That live probe already exists as P5B-1's
committed, live-recorded fixture `tests/fixtures/phase5b/china_rf/`; it is
**not** re-probed here (no unnecessary live call).

---

## 3. Probe result — `PROBE_BLOCKED`

Run once, live, with `TUSHARE_PROXY_TOKEN` read from the environment (never
logged, hashed, or written). Full raw evidence:
`entitlement_probe_ledger.json`, `raw/*.json`, `probe_run.log`.

```
n_identities = 36
n_ok         = 13   (HTTP 200, well-formed envelope)
n_blocked    = 23   (HTTP 503, {"ok": false, "error": "upstream_pool_exhausted"})
```

**Every failure was the same transient condition:** HTTP 503
`upstream_pool_exhausted`, the proxy's documented cold/contended-pool
response (the same condition `ProxyTushareClient.fetch` normally absorbs with
a bounded 2/4/8 s retry — deliberately disabled here by the frozen one-call
rule). **No** 400/403, **no** `code != 0` plan-tier/permission body, and no
`date_range_too_large` rejection was observed.

Resolved (HTTP 200) identities by name:

* `000001.SZ`: `dividend` (71), `income` (1), `balancesheet` (2),
  `fina_indicator` (1)
* `600519.SH`: `daily` (21), `daily_basic` (21), `stk_limit` (21),
  `dividend` (116), `balancesheet` (1)
* `601318.SH`: `daily` (21), `dividend` (84)
* `000858.SZ`: `stock_basic` (3), `fina_indicator` (1)

Unresolved (HTTP 503) identities: all remaining `(name, endpoint)` pairs —
including every `suspend_d` call, three of four `stock_basic` calls, and most
`income`/`fina_indicator`/`daily_basic`/`stk_limit` calls. The ledger records
each one with its status and error code.

### Interpretation (fail closed — no routing around)

* The probe **did not** discover an entitlement problem: no identity was
  denied on entitlement grounds.
* The probe **did not clear** the universe either: 23 identities have no
  successful response, so Barrier 3's required "one bounded entitlement probe
  per name, covering every endpoint" evidence is incomplete.
* Under the frozen one-call/no-retry rule, a single pass against a contended
  proxy pool cannot be completed by re-calling; re-probing is a **retry** and
  is therefore not performed here. Escalation is a planner/user decision (for
  example: authorize the proxy client's own bounded retry/backoff for a
  re-probe, pace the probe, or switch the probe to the documented retry
  policy), not something this task may decide unilaterally.
* This is reported as a blocker, exactly as the plan's fail-closed discipline
  requires; nothing was substituted inline and no universe was downgraded.

---

## 4. What this evidence is and is not

* It **is** proxy-observed live evidence from
  `pcd.mobcvb.cn/tushare/pro` with an `X-API-Key` header.
* It is **not** direct official-Tushare behavior and certifies nothing about
  the paid official API at any tier.
* It is **not** an entitlement clearance of the frozen universe (see §3).
* It is **not** a pilot-window freeze, a CH3/CH4 construction, or a
  profitability/statistical claim.
* No credential value appears anywhere in this directory; the token is read
  from the environment only.

## 5. Files

| File | Contents |
| --- | --- |
| `README.md` | this document |
| `entitlement_probe_ledger.json` | every request identity, HTTP status, proxy code, error code, item count, fields, body SHA-256, and raw-file pointer |
| `raw/<ts_code>__<endpoint>.json` | every raw response body, verbatim, including the HTTP 503 error bodies |
| `probe_run.log` | the human-readable run log |

## 6. Reproduction / re-probe

```bash
# network-free self-test of classification + fail-closed/overwrite logic
.venv/bin/python scripts/probe_phase5b_pilot_universe.py --self-test

# one bounded live call per identity (refuses if a ledger already exists)
.venv/bin/python scripts/probe_phase5b_pilot_universe.py --token-file ~/.secrets
```
