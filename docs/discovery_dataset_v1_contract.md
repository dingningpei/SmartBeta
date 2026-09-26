# Discovery Dataset v1 — Frozen Contract

**Status:** FROZEN (D0). Approved after independent review of the Contract
Freeze proposal, its correction pass (B1/B2/N1/N2/N3) and the final
delisting correction. This document is self-contained and is the only
authoritative text of the contract.

**Base commit at freeze:** `a7a717b53a7b8581053e5f41b1c59804e04d04d5`
(`phase10-complete`). **Contract hash:** SHA-256 of this file's exact
committed bytes (§19). The hash is recorded outside this file, in the D0
commit message, so that no bytes are hashed self-referentially.

**Evidence basis:** Gates 0, 0B and 0C (Tiingo entitlement, PIT
universe-construction feasibility, cross-vendor capability). Evidence classes
follow `CLAUDE.md` §8. No factor-performance information was used to make
any decision in this contract.

---

## 1. Purpose

Dataset v1 exists to support the **first serious alpha-discovery campaign**,
aimed at finding persistent, economically meaningful, tradable factor
candidates while minimizing false discovery and self-deception.

**Supports:**
- broad cross-sectional US equity research;
- a point-in-time (PIT), liquidity-ranked, monthly historical universe;
- about 7.5 years of development history;
- price/volume factor families: momentum/reversal, volatility/lottery,
  volume/liquidity/attention and price-volume interactions;
- a sealed historical confirmation reserve, plus prospective accrual.

**Does not support:**
- fundamental factors;
- certified market-cap or size factors (dollar volume is a liquidity measure,
  not a size measure);
- perfect survivorship reconstruction or identifier continuity;
- certified delisting returns;
- production trading, capacity or market-impact modeling.

## 2. Provider and historical window

- **Primary provider:** Tiingo EOD (`/tiingo/daily/{ticker}/prices` through
  the existing `smart_beta.vendors.tiingo` client). **Tushare and the Tushare
  proxy are not Dataset-v1 dependencies** (Gate 0C).
- **Acquisition start:** 2015-01-02, the first NYSE session in the trusted
  calendar (`smart_beta/vendors/tiingo/calendar_source.py`, which covers
  2015–2027). This gives a full 252-session warmup.
- **Research start:** 2016-01-04, the first NYSE session of 2016.
- **End rule:** C is the last NYSE session of the last complete calendar
  month before the acquisition batch starts. C is recorded in the manifest.
- **Rationale** (no factor outcomes involved):
  - the trusted calendar authority begins in 2015;
  - recorded vendor delisting coverage rises sharply from 2015–16 (Gate 0B);
  - a 2016 research start leaves about 7.5 development years spanning
    several market regimes.

  A 2012 or 2014 start would require extending a certified calendar and would
  inherit thinner early delisting coverage. A 2018 start would give too short
  a development period.

## 3. Eligibility rule

A security is **eligible at formation session f** if and only if all of the
following hold:

1. **Exchange:** NYSE, NASDAQ, NYSE MKT or AMEX.
2. **Currency and type:** priceCurrency = USD and assetType = Stock
   (supported-tickers fields).
3. **Suffix exclusions:** the ticker has none of these suffix segments:
   - `-P…` (preferreds);
   - `-WS…` or `-W` (warrants);
   - `-U` (units);
   - `-R`, `-RT` or `-R-W` (rights);
   - `-WI` (when-issued).

   Share-class suffixes (for example `-A` and `-B`) are **kept**, and each
   class is a separate security. ADRs are included because they cannot be
   identified from the vendor fields.
4. **Listing age:** at least **252 NYSE sessions** from the security's listed
   start date through f−1. The listed start is the supported-tickers
   `startDate`, or 2015-01-02 if that is later. Age is counted on the trusted
   calendar **regardless of vendor observation completeness**, so missing
   vendor observations never make a security younger.
5. **Liquidity computability:** it meets the §5 observation rule.
6. **Missingness QC:** it is not excluded under §10.
7. **Identity:** it is not IDENTITY_AMBIGUOUS (§4).

**Why the age rule exists** (independent of vendor cost):
- the full maximum factor lookback (252 sessions) and the full liquidity
  window are available;
- the IPO-seasoning period is excluded.

**What eligibility never uses:** current survival, current liquidity, current
market cap, current index membership, future listing duration, future
delisting outcome or future returns.

**Use of vendor end dates:** the vendor endDate is used only to reconstruct
retrospectively whether a security existed, never to prefer survivors.

**Acquisition pool:** rows of the supported-tickers snapshot (file hash
recorded) that pass rules 1–3, whose listed interval overlaps [2015-01-02, C],
and that can reach listing age 252 within [2016-01-04, C]. The expected pool
is about 11,000–12,000 symbols.

## 4. Identity policy (fail-closed)

- **Canonical key:**
  `security_key = "TIINGO:" + ticker + ":" + listed_startDate`, one per
  supported-tickers row. A ticker alone is never treated as a permanent
  security identity.
- **Reused or duplicated tickers:** each ticker is fetched once.
  - Its returned rows are assigned to a listed interval only if their first
    and last dates fall inside exactly one of that ticker's listed intervals
    (±5 sessions).
  - Otherwise every interval of that ticker is **IDENTITY_AMBIGUOUS** and is
    excluded from the universe. Exclusion counts are reported.
- **Overlapping listed intervals** under one ticker are IDENTITY_AMBIGUOUS.
- **Renames:** a renamed security's history lives under its current ticker,
  and its key uses that ticker. Historical ticker strings are not
  reconstructed.
- **Data outside the listed interval** receives the QC flag
  **INTERVAL_MISMATCH**.
- **Propagation:** identity status propagates into QC and into the universe.
- **`IDENTIFIER_CONTINUITY_NOT_CERTIFIED`** is retained.

## 5. Liquidity rule

- **Daily dollar volume:** DV(i,d) = raw close(i,d) × raw volume(i,d).
- **Formation sessions f:** the last NYSE session of each calendar month
  (monthly rebalance).
- **Ranking statistic:** the **median** of DV over the **63 NYSE sessions
  f−63 … f−1**.
- **Validity requirement:** at least **50** valid sessions within that window.
  - A valid session has a row with close > 0 and non-null volume.
  - A **zero-volume row is valid**, with DV = 0.
  - A missing row on a listed NYSE session counts as missing, never as zero.
- **Ranking:** descending median DV among eligible securities. Ties are broken
  by `security_key` in lexicographic order.
- **Timing:** every universe input is at or before close(f−1). The universe
  U_f applies to portfolios formed at close(f).

**Rationale** (frozen universe-design choices):
- **Median** reflects persistent tradability and is robust to one-off spikes,
  such as index-reconstitution days, news or earnings bursts and block
  trades, which a sum or mean would reward.
- **63 sessions** is about one quarter: long enough to smooth episodic
  activity, short enough to reflect current tradability. It is a conventional
  average-daily-volume horizon.
- **At least 50 valid sessions** (about 80%) keeps the median based on most of
  the window while tolerating brief vendor gaps or trading halts.

These parameters **may not be changed because factor results look better
under another liquidity definition**. Any change creates a new dataset version
with a stated non-performance reason.

## 6. Universe size

- **Primary N = 500.**
- **Rationale:**
  - it gives a broad cross-section across sectors and the large/mid-cap
    range, where a one-way basis-point cost model is realistic;
  - it is available in every year, since roughly 5,500–6,900 core-exchange
    names are alive on any date;
  - it supports quintile sorts with about 100 names per leg;
  - computation is tractable.

  The size supports cross-sectional information content. It is **not** a
  statistical guarantee: cross-sectional observations are not independent.
- **Predeclared robustness variants:** top-200 and top-1000, built from the
  same data. They never replace the primary universe.

## 7. Raw fields

| Field | Use | Semantic status |
|---|---|---|
| date | REQUIRED | CERTIFIED (checked against the trusted calendar) |
| close (raw) | REQUIRED | CERTIFIED via the Phase-4B return path (specimen level) |
| volume (raw) | REQUIRED | LIVE_RECORDED / NOT_CERTIFIED |
| divCash | REQUIRED | CERTIFIED (dividend semantics) |
| splitFactor | REQUIRED | CERTIFIED (AAPL specimen only) |
| open, high, low | OPTIONAL: stored in the raw response, used for QC only in v1 | LIVE_RECORDED / NOT_CERTIFIED |
| adjOpen, adjHigh, adjLow, adjClose, adjVolume | NOT USED except as a QC cross-check | OBSERVED / NOT_CERTIFIED (the vendor's current reconstruction) |

**Semantic inputs admitted to FactorSpec in v1:**
- `daily_total_return` (the trusted reconstruction, §8);
- `total_return_index` (the trusted cumulative product of
  `daily_total_return`);
- `dollar_volume` (DV from §5; volume semantics NOT_CERTIFIED and disclosed).

All three reach Phase 6 **only** through the D7 bridge, which applies the §11
one-session information lag.

## 8. Corporate actions

- **Total returns come from one path only:** raw close-to-close returns plus
  the corporate-action table mapped from divCash and splitFactor by the
  existing Phase-4B mappers, passed through
  `smart_beta.pit.corporate_actions.compute_adjusted_returns`. This is the
  certified path.
- **Vendor-adjusted series are never trusted.**
- **Dollar volume** uses raw fields and needs no adjustment.
- **Complex corporate actions** (spin-offs, share-class issuances, mergers,
  rights) are **NOT CERTIFIED**. A day with |raw return| > 50% and no split
  or dividend event is flagged **CORP_ACTION_SUSPECT**. Flagged days are
  retained, counted and specimen-audited, never silently dropped.

## 9. Delistings

### 9.1 Observation

A series ends at its last EOD row. The date of that row, `final_session`, is
recorded. Terminal proceeds are **unknown** and remain unknown. No synthetic
rows are ever stored.

### 9.2 No silent disappearance

A security in U_f whose series ends inside its holding window (f, f+h] is an
**affected observation**. It is never dropped from any portfolio or
cross-sectional statistic. Its holding-period return is:

    R = (1 + R_obs) · (1 + τ) − 1

- **R_obs** is the observed return from close(f) to the close of
  `final_session`.
- **τ** is the unknown terminal return, from the last close to the proceeds.

### 9.3 Mathematical facts

- **Long side:** τ ≥ −100%, so **−100% is a genuine lower bound** for a long
  position's terminal return.
- **Short side:** τ has no finite upper bound, so **no finite worst-case
  terminal-return bound exists for short positions**. None is claimed.
- **Pearson IC:** affected forward returns are unbounded above, so **no finite
  IC bound is claimed**.

### 9.4 Frozen scenarios

The D7 bridge derives the scenario panels, labelled `DELISTING_SCENARIO_*`,
for evaluation only. They are never stored as data, and they are fed to the
unmodified Phase-7 engine.

| Scenario | τ for affected observations | Status |
|---|---|---|
| **T0 — last-close reference** | τ = 0 for every affected observation | Reference only. It is **not** an assumption about proceeds. |
| **TS — delisting stress scenario** | **Long leg** (predicted-high portfolio quantile): τ = −100%, the genuine lower bound for a long terminal return. **Short leg** (predicted-low portfolio quantile): τ = +100%, a **declared stress parameter, not a mathematical bound**. **Middle quantiles:** τ = 0. | A stress scenario. It is **not** a worst case and **not** a robustness guarantee. |

### 9.5 IC and rank-IC

IC and rank-IC use **the same leg-based τ assignment as TS**. Leg quantiles
come from the evaluation's frozen portfolio construction. This is a stress
scenario, not a bound.

Every IC and rank-IC report contains, per formation session and in aggregate:
- the affected-observation count and fraction, by quantile;
- the T0 value, as the reference;
- the TS value, as the stress result;
- the DELISTING-SENSITIVE status under §9.6.

### 9.6 Deterministic advancement rule

This rule was frozen before any factor outcome was observed.

**Definitions:**
- **B:** the mean over formation sessions of the fraction of U_f names that
  are affected observations (the universe base rate).
- **E_S:** the mean over formation sessions of affected short-leg weight, as a
  fraction of short-leg weight.
- **E_L:** the same quantity for the long leg. It is reported only, because
  the long side is already covered by the genuine lower bound in TS.

A result is **DELISTING-SENSITIVE** if **any** of the following holds:
- (a) the sign of the primary statistic differs between T0 and TS;
- (b) the primary development screen passes under T0 but fails under TS;
- (c) E_S > 2·B, meaning the short leg is concentrated in unknown-outcome
  delistings beyond twice the universe base rate; or E_S > 0 when B = 0.

**A DELISTING-SENSITIVE result may not advance to confirmation under
Dataset v1.**

Passing TS does **not** establish that delisting risk is bounded, that
proceeds are known, or that the result is robust to every possible delisting
outcome.

### 9.7 Phase-10 compatibility

A future Phase-10 confirmation study must preregister T0 and TS reporting and
the §9.6 rule. Dataset v1 does not modify Phase 10.
**`DELISTING_RETURN_NOT_CERTIFIED`** is retained.

## 10. Missingness

The trusted NYSE calendar is the authority for which dates are sessions.

| Class | Meaning |
|---|---|
| NOT_YET_LISTED | a session before the security's first row |
| ENDED | a session after its last row |
| (holiday) | not a session |
| LEGIT_ZERO_VOLUME | a row is present and volume is 0 |
| VENDOR_MISSING | no row on a listed session |
| VENDOR_NULL | a row is present but close or volume is null |
| MALFORMED_CA | splitFactor ≤ 0 or NaN, or divCash < 0; that security-day is excluded |

**Rules:**
- A return is computed only between consecutive sessions that are both
  present. A return spanning a VENDOR_MISSING session is invalid.
- **No forward fill** of any field.
- A security with more than 5% VENDOR_MISSING or VENDOR_NULL across its
  in-window listed sessions is excluded (fail-closed and counted).
- Data that stops more than 5 sessions before the listed endDate, or before C
  for an active listing, is flagged **TERMINATION_MISMATCH**.
- Missingness never changes listing age (§3).

## 11. Temporal contract and partitions

### 11.1 Temporal contract (per formation session f)

| Element | Rule |
|---|---|
| Information cutoff | **close(f−1)** |
| Universe inputs | sessions ≤ f−1 (§5) |
| Factor inputs | every TrustedInput value on factor row f comes from a session ≤ f−1 |
| Formation | **close(f)** |
| Realized returns | **strictly after f**, i.e. r(f+1) onward |

- **Enforcement of the factor-input rule:** the D7 bridge applies a mandatory
  one-session information lag. The row dated d carries the observation from
  session prev(d). Any FactorSpec `lag` is applied in addition to this lag.
- **Realized returns:** P7-B compounds only returns dated strictly after the
  formation date (`smart_beta/evaluation/forward_returns.py`).
- **No same-close execution:** a value first known at close(f) is never traded
  at close(f).
- **Why the bridge enforces the lag:** the Phase-6 evaluator performs no as-of
  alignment, and the existing Pilot-1A input convention dates a return at its
  own trading date. That convention implies same-close use, so Dataset v1
  imposes the lag explicitly in its bridge.

### 11.2 Partitions

| Partition | Dates | Access |
|---|---|---|
| Warmup | 2015-01-02 … 2015-12-31 | conditioning only: lookbacks and liquidity windows |
| **DEVELOPMENT** | formation sessions f from 2016-01-04 through 2023-06-30 | research-accessible |
| **RESERVE** (sealed historical) | 2023-07-03 … C | sealed (§12) |
| **PROSPECTIVE** | sessions after C | not acquired now; future accrual eligible for G1 |

**Boundary rules:**
- **Purge:** any development label whose realization window crosses
  2023-07-03 is purged, following the P7-B §8.1 rule.
- **Maximum forward horizon:** 63 sessions.
- **Conditioning across the boundary:** the lagged inputs for the first
  reserve formation come from development sessions. That is legitimate
  conditioning (Phase-10 §6.3), not reserve leakage.
- **Embargo:** none beyond the purge.
- **Internal development splits** are a Campaign-1 decision and are not a
  dataset change.

## 12. Confirmation reserve

- **Span:** a fixed start of 2023-07-03, running to C.
- **Prospective growth:** observations after C accrue as PROSPECTIVE evidence.
  They are acquired only under a later authorization.
- **Data possession vs research access:**
  - The reserve data is **physically possessed** in `reserve_sealed/` (§14).
  - It is registered as a sealed ARTIFACT in the Knowledge-PIT log.
  - **Research access goes through a firewall:** the development accessor
    rejects any session ≥ 2023-07-03.
  - Only a separately authorized Phase-10 confirmation study may read the
    reserve.
- **Dataset v1 is not Phase-10 confirmation.** The historical reserve is at
  best G2/G3 evidence, with LLM-pretraining exposure disclosed. Prospective
  data is the G1 path.

## 13. Provenance and manifest

One manifest record is written per request, containing:
- provider, endpoint, ticker, the `security_key`s and the requested range;
- the UTC retrieval timestamp, HTTP status, row count, first and last
  observation dates, and the SHA-256 of the raw body;
- the schema version, the acquisition-tool code hash and the adapter code
  hash;
- the attempt count, the terminal status, the `batch_id`, the supported-tickers
  snapshot hash and C.

The design supports idempotent resume, partial failure, retry, corruption
detection, reproducibility and audit. **Credentials are never stored.**

## 14. Storage

- **Location:** `~/Developer/personal/smart_beta/data_store/discovery_dataset_v1/`,
  **outside every Git worktree.**

| Directory | Contents |
|---|---|
| `raw/<batch_id>/<ticker>.json.gz` | verbatim provider bodies, immutable |
| `normalized/` | Parquet |
| `derived/` | universe and dollar-volume series |
| `reserve_sealed/` | the reserve-period normalized partition |
| `manifest/` | JSONL records plus a manifest hash |

- **Estimated size:** about 5 GB of raw JSON (about 1 GB gzipped) and about
  0.5 GB normalized.
- **What may be committed:** only this contract and a data-free manifest
  summary (hashes, counts, statuses). Bulk raw, normalized or derived data is
  never committed. No redistribution or licensing claim is made beyond
  existing evidence.

## 15. Acquisition failure handling

| Condition | Behaviour |
|---|---|
| HTTP 429, 5xx, timeout | at most 3 attempts with exponential backoff, all recorded |
| 4xx entitlement failure | ENTITLEMENT_ERROR; no retry |
| Empty response | EMPTY |
| Malformed, partial or truncated response | MALFORMED |
| Monthly or daily quota exhausted | clean batch stop; resumable |
| Interrupted run | resume skips tickers whose status is OK and whose hash verifies |
| Hash mismatch on re-fetch | both copies kept; NONDETERMINISTIC flag |

Every ticker ends with a visible terminal status. There are no silent gaps,
and failures stay visible until they are resolved or explicitly excluded.

## 16. Quality-control gates

All of these must PASS before Dataset v1 is admitted for Discovery Campaign 1.

1. **Manifest completeness:** the expected pool, the requested set and the
   terminal statuses agree.
2. **Coverage:** returned vs requested symbols. Non-OK outcomes are at most 1%
   and all are listed.
3. **Identity:** duplicate, overlap and ambiguity counts, with exclusions
   listed.
4. **Rows vs calendar:** row and date coverage against the trusted calendar,
   plus missingness distributions.
5. **OHLC consistency:** low ≤ open, close ≤ high; prices > 0.
6. **Corporate actions:** malformed counts, and the CORP_ACTION_SUSPECT list
   with a specimen audit.
7. **Volume:** completeness and the zero-volume distribution.
8. **Listings:** listing-interval and termination consistency.
9. **Integrity:** hash verification of every raw file.
10. **Reproducibility:** rebuilding the normalized dataset from raw is
    byte-identical.
11. **Universe:** reconstruction is deterministic (identical top-N hashes on
    two runs), and N = 500 is attained at every formation session.
12. **Reserve firewall:** development-accessor tests.
13. **Timing:**
    - an input built from session f's own data cannot reach factor row f;
    - every row-f input's source session is ≤ f−1.
14. **Delisting scenarios:**
    - the T0 and TS panels are deterministic;
    - no affected observation is dropped;
    - TS τ follows the frozen leg rule (−100% long leg, +100% short-leg stress
      parameter, 0 for middle quantiles);
    - affected counts per formation session and quantile, B, E_S and E_L are
      reproducible;
    - the §9.6 classification is deterministic on seeded fixtures.

**No factor-performance statistic is part of QC**, and none is used to set any
threshold.

## 17. Bounded certification claim

If every §16 gate passes, Dataset v1 supports this claim:

> Discovery Dataset v1 is a reproducible, hash-manifested panel of daily raw
> close, volume and dividend/split inputs acquired from Tiingo EOD for
> US-exchange USD common-stock listings (per the frozen eligibility rule) over
> 2015-01-02…C. Total returns are reconstructed only through the certified
> Phase-4B/Phase-3 path. A monthly PIT universe of the top 500 by 63-session
> median dollar volume is deterministically reconstructible, with information
> cutoff close(f−1), formation at close(f) and returns strictly after f,
> enforced by a one-session lag in the input bridge. Delisting-truncated
> positions are **retained** and evaluated under a last-close reference (T0)
> and a frozen **delisting stress scenario** (TS: −100% on the long leg, which
> is a genuine lower bound for longs, and a declared +100% stress parameter on
> the short leg, which is **not** a bound), with a deterministic
> DELISTING-SENSITIVE rule, so series termination cannot silently remove a
> position from evaluation. The development/reserve partition is enforced by
> an access firewall. All of this is subject to the recorded identity,
> delisting, corporate-action, volume and vendor-coverage limitations.

## 18. Nonclaims

- **Identifier continuity:** NOT CERTIFIED.
- **Survivorship completeness:** NOT CERTIFIED. Early-period vendor delisting
  coverage is thin, and reused tickers leave holes.
- **Delisting returns:** NOT CERTIFIED.
  - T0 is a reference and TS is a stress scenario; **neither estimates
    proceeds**.
  - For short positions **no finite worst-case terminal-return bound
    exists**, and the +100% value is a declared stress parameter.
  - No finite Pearson-IC bound is claimed.
  - Passing TS does not establish robustness to all delisting outcomes.
- **Complex corporate actions:** bounded only by the existing specimen-level
  certification.
- **Volume semantics:** bounded by current evidence (NOT_CERTIFIED).
- **Listing age:** the vendor coverage start may understate the true listing
  date.
- **Not certified at all:** fundamentals, market cap or size, ADR/common
  classification, and vendor revision history (raw values are as served at C).
- **Not claimed at all:** production readiness, factor alpha, or universal
  statistical validity.

## 19. Versioning and hashes

| Identity | Definition |
|---|---|
| `contract_hash` | SHA-256 of this file's exact committed bytes. Recorded in the D0 commit message, never inside this file. |
| `dataset_id` | `discovery_dataset_v1`, bound to `contract_hash` |
| `batch_id` | one acquisition run |
| `manifest_hash` | hash of the canonical manifest JSONL |
| `normalized_hash` | hash of the normalized build |
| `universe_rule_hash` | covers §3, §5 and §6 |
| `partition_hash` | covers §11 and §12 |
| `delisting_scenario_hash` | covers the §9.4 scenario definitions (T0, TS) and the §9.6 DELISTING-SENSITIVE rule; also covered by `contract_hash` |

**What creates what:**
- Resuming a batch keeps the same `batch_id`.
- Re-acquiring under this contract creates a new batch.
- Changing normalizer code under this contract creates a new normalized build.
- Changing any item in the §22 table creates a **new dataset version**.
  Dataset v1 is never mutated silently.

## 20. Implementation DAG (design)

| Task | Scope | Depends on | Acceptance | Provider | Offline-testable |
|---|---|---|---|---|---|
| D0 | Freeze this contract | approval | committed; `contract_hash` recorded | No | — |
| D1 | Candidate-pool builder | D0 | deterministic pool with snapshot hash; §3 tests | 1 public snapshot download at run time | Yes |
| D2 | Resumable acquisition tool + manifest + §15 handling | D0 | every §15 case tested under a fake transport; resume and idempotence | live only at run time | Yes |
| D3 | Normalizer + §10 classes + corporate-action table (Phase-4B mappers) | D2 | byte-reproducible; §10 tests | No | Yes |
| D4 | Identity resolver (§4) | D1, D3 | ambiguity fixtures | No | Yes |
| D5 | QC suite (§16) | D3, D4 | every gate implemented; seeded-corruption tests | No | Yes |
| D6 | PIT universe constructor (§5, §6) | D3, D4 | deterministic top-N; look-ahead adversarial tests | No | Yes |
| D7 | TrustedInput bridge (see below) | D3, D6 | gate-13 timing tests; gate-14 scenario and classification tests; Phase-6 admission | No | Yes |
| D8 | Reserve-access firewall + sealed-ARTIFACT registration | D3 | accessor rejects reserve sessions; ARTIFACT recorded | No | Yes |

**D7 scope:**
- the TrustedInput bridge with the mandatory one-session information lag;
- construction of the T0 reference and TS stress scenario panels, with
  leg-based τ for both portfolio and IC evaluation;
- computation of affected counts, B, E_S and E_L;
- the §9.6 classification.

**Order:** D1 and D2 → Power barrier (§21) → live acquisition → D3–D8.

No sealed Phase-6–10 code change is required. Extending the trusted calendar
beyond 2027 will be needed before prospective accrual passes that year, under
a separate authorization.

## 21. Power-purchase readiness barrier

A one-month Tiingo Power plan may be purchased only after **all** of the
following hold:

1. This contract is approved, frozen and committed.
2. D1 is complete and the expected acquisition pool has been computed.
3. D2 is complete, reviewed and merged, with offline tests green for every
   §15 case, manifest generation, resume and retry.
4. A Starter-tier live smoke test of at most 20 symbols passes end to end,
   including one delisted specimen and one reused-ticker specimen.
5. Storage exists, and corruption-detecting QC (hashes, row counts, manifest
   completeness) runs on the smoke-test output.
6. No known software blocker would require live debugging.

The Power month is used for acquisition, not software development.

## 22. Frozen decisions

| Decision | Frozen value |
|---|---|
| Primary provider | Tiingo EOD; Tushare and the Tushare proxy are not dependencies |
| Historical start | acquisition 2015-01-02; research 2016-01-04 |
| End rule | C = last NYSE session of the last complete month before the batch |
| Exchanges / currency / type | NYSE, NASDAQ, NYSE MKT, AMEX / USD / Stock |
| Listing age | ≥ 252 NYSE sessions since the listed start, through f−1, independent of vendor observation completeness |
| Suffix exclusions | `-P…`, `-WS…`, `-W`, `-U`, `-R`, `-RT`, `-R-W`, `-WI`; share classes kept |
| Identity | ticker + listed-start key; fail-closed exclusion of ambiguous identities |
| Liquidity formula / window | median(raw close × raw volume) over sessions f−63…f−1, with ≥ 50 valid; no results-driven change |
| Information cutoff | close(f−1) for universe and factor inputs; one-session lag enforced in the bridge |
| Formation / returns | close(f) / strictly after f |
| Rebalance | monthly, on the last NYSE session of each month |
| Primary N | 500 (robustness variants 200 and 1000, predeclared) |
| Raw fields | date, close, volume, divCash and splitFactor required; open/high/low for QC only; adj* fields unused |
| Corporate actions | trusted reconstruction only; suspect days retained and flagged |
| Delisting | affected observations retained; R = R_obs ∘ τ. **T0:** τ = 0 (reference). **TS delisting stress scenario:** long leg −100% (genuine long lower bound), short leg +100% (declared stress parameter; no finite short bound exists), middle quantiles 0 |
| Delisting IC / rank-IC | same leg-based TS assignment; T0 and TS values plus affected counts reported; no IC bound claimed |
| Advancement rule | DELISTING-SENSITIVE if sign(T0) ≠ sign(TS), or the screen passes at T0 but fails at TS, or E_S > 2·B (or E_S > 0 when B = 0). Sensitive results cannot advance to confirmation. |
| Missingness | §10 classes; no forward fill; > 5% exclusion; independent of listing age |
| Development | formation sessions 2016-01-04 … 2023-06-30 |
| Reserve | 2023-07-03 … C, sealed; prospective accrual after C |
| Purge / embargo | purge labels crossing the reserve start; maximum forward horizon 63 sessions; no additional embargo |
| Storage | outside Git: raw / normalized / derived / reserve_sealed / manifest |
| Certification boundary | the §17 claim and the §18 nonclaims |
