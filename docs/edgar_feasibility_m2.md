# M2 — SEC EDGAR feasibility probe (research record 2026-10-08; not a contract; no dataset built)

- **Authorization:** the user approved M2 of `docs/discovery_loop_plan_draft.md`, plus
  about 200 Tiingo metadata requests (issuer names) and an SEC User-Agent contact
  (2026-10-08). The contact was supplied per process only; it is not recorded here.
- **Scope:** CIK ↔ D4 security-key mapping coverage, and filing-date coverage. **No
  prices, returns, factor values, reserve data or final-holdout data were read.** Issuer
  names and raw responses were held in memory only; only aggregate counts were written.
- **Probe:** a scratchpad script (SHA-256 `5cbfe85d…3533`) produced result `915aeace…7202`.
  Reads:
  - D4 identity and D7a TOP_1000 membership (development);
  - SEC `company_tickers.json`, `cik-lookup-data.txt` and `submissions/CIK*.json`;
  - Tiingo `/tiingo/daily/<ticker>` metadata (the `name` field).

## Sample

- **Population:** D4 keys that were ever in TOP_1000 during development, 1,921 keys.
  - **Active:** 1,450 keys, listed through 2026-06 or later.
  - **Ended:** 368 keys, whose listing ended between 2016-01-01 and 2023-06-30.
- **Sample:** a seeded stratified random sample (seed 20261008) of 100 active and 100
  ended keys.

## Method

- **Active keys:** current ticker → CIK, via `company_tickers.json`.
- **Ended keys,** and active keys with no ticker match: the Tiingo issuer name →
  normalized name → CIK candidates, from the SEC historical name table, which includes
  former names.
- **Verification:** a candidate counts only if it filed at least one periodic report
  (10-K/Q, 20-F, 40-F, including amendments) inside the key's listed interval, clipped to
  development. A mapping counts only if **exactly one** candidate passed.

## Results

| Stratum | Uniquely mapped and verified | Ambiguous (more than one verified CIK) | Candidates, but no filings in interval | No candidate | Coverage (95% Wilson interval) |
|---|---|---|---|---|---|
| Active | 99 (98 by ticker, 1 by name) | 0 | 1 | 0 | **99%** [94.6, 99.8] |
| Ended | 90 (by name) | 3 | 2 | 5 | **90%** [82.6, 94.5] |

- **Periodic-filing density** among mapped keys: about 3.8 per listed year (active) and
  3.9 per listed year (ended). The expected value is about 4 (three 10-Qs + one 10-K).
- **Foreign private issuers** (20-F / 6-K, no quarterly 10-Q): 10 active and 5 ended.
- **Filings in the sample** (2016-01 to 2023-06), by form:

  | Form | Count |
  |---|---|
  | 8-K | 11,755 |
  | 10-Q | 2,692 |
  | 10-K | 964 |
  | 6-K | 1,950 |
  | 20-F | 40 |

  The by-year counts decline for ended keys as they exit (1,485 in 2016 → 23 in 2023), as
  expected.

## Limitations (binding on any follow-up)

1. **"Verified" is not identity certification.** It shows only that a single candidate
   CIK filed periodic reports during the key's interval. A same-name different issuer, or
   a ticker reused across issuers (312 reused tickers in D4), could still pass. Precision
   was **not measured**; a hand-audited precision sample is required before M3.
2. **The 10% unmapped ended keys are not random.** Ended keys are where survivorship and
   delisting effects live, so non-coverage may be outcome-correlated. M3 must record
   unmapped keys explicitly (fail-closed), never drop them.
3. **The date field used was `filingDate`.** A point-in-time event dataset must use
   `acceptanceDateTime` and map after-close acceptances to the next session. That field
   was not probed.
4. **8-K items not distinguished.** Earnings releases are 8-K Item 2.02, which is
   available in submissions `items`. Its coverage was not measured.
5. **XBRL company-facts coverage was not probed.**
6. **Sample size:** 100 per stratum. The intervals above are the uncertainty.

## Disposition

**CONDITIONAL GO for M3 design.** EDGAR filing dates are available for about 99% of
active and about 90% of ended development-era TOP_1000 keys. The conditions are:

- a precision audit of the mapping;
- the `acceptanceDateTime` timing rule;
- Item 2.02 coverage;
- an explicit fail-closed treatment of unmapped keys.

This is **MECHANICALLY AVAILABLE**, not **EMPIRICALLY COVERED** or certified.
