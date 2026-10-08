# Dataset v2 — longer-history feasibility (research record 2026-10-08; not a contract; no data acquired)

- **Status:** planner draft. Nothing here authorizes data acquisition, vendor contact,
  purchase, network probes or any change to Dataset v1.2.
- **Motivation:** the frozen-screen power analysis (2026-10-08, pure math, no data). The
  IS power is governed by calendar time (t ≈ Sharpe × √years). Dataset v1.2 gives 4.5 IS
  years, so detecting an effect with 50% power needs an annual net Sharpe of about 1.2
  (m = 7). Realistic US large-cap price-anomaly Sharpes (about 0–0.5) are undetectable,
  and a null result is uninformative.

## 1. How much history is needed (pure math, frozen screen shape, IS = 60% of history)

| Target true annual net Sharpe | IS years for 50% power, m = 7 | IS years for 50% power, m = 1 | IS years for 80% power, m = 1 |
|---|---|---|---|
| 0.5 | ~26 | ~14 | ~29 |
| 0.4 | ~40 | ~21 | ~45 |
| 0.3 | ~72 | ~38 | ~81 |

**Implication:** longer history alone is not enough. Detecting realistic effects needs
**both** about 15–30 years of IS history **and** few, strong, precommitted hypotheses
(m = 1–3). Practically, a 25–30-year dataset (about 1995/2000 onward) with m ≤ 3 makes a
Sharpe of 0.5 detectable with about 50–80% power.

## 2. Why Dataset v1 starts in 2015 (contract §2)

- **The trusted calendar** starts in 2015. This is an engineering constraint and can be
  extended.
- **Recorded Tiingo delisting coverage** rises sharply only from 2015–16 (Gate 0B). This
  is the **substantive** constraint, because survivorship bias biases price-momentum
  factors upward.
- **Reused tickers:** Tiingo serves only the current security's history for a reused
  ticker, so the old security's history is missing (a confirmed hole). The hole grows
  with lookback.
- **The D4 identity build already shows** 330 HISTORY_UNREACHABLE and 504
  IDENTITY_AMBIGUOUS keys in the 2015+ window.

## 3. Options

| Option | History | Survivorship / delisted coverage | Identity continuity | Cost / effort | Main risk |
|---|---|---|---|---|---|
| **A. Tiingo backfill** (extend calendar + D1–D8 to about 2005) | prices exist (Gate 0: ≥ 10 y) | **thin before 2015–16**; reused-ticker hole grows | weak (ticker-keyed) | low money (one Power month), high engineering (re-run the D1–D8 chain) | survivorship bias could *manufacture* momentum alpha; must be measured first |
| **B. Survivorship-free retail vendor** (e.g. Norgate Data, Sharadar/Nasdaq Data Link, EODHD with delisted) | about 1990s+ (to verify per vendor) | designed to include delisted securities (to verify) | vendor permaticker / IDs (to verify) | moderate subscription; new vendor adapter plus licensing review | licensing / redistribution terms; lineage certification (the earlier vendor research found lineage blockers) |
| **C. CRSP (WRDS) / institutional** (LSEG, FactSet) | 1926+ (CRSP) | gold standard (delisting returns included) | PERMNO | high cost / institutional access | access and licensing likely out of reach; the earlier research rated LSEG / FactSet "conditional" |

**Facts marked "to verify"** (history start, delisted coverage, IDs, licensing) need a
vendor research step before any choice. This draft does **not** assert them as
certified.

## 4. Recommended next steps (each needs its own authorization)

1. **Option-A measurement probe (cheapest, decisive).** Using only Tiingo metadata already
   inside the licensed scope (supported-tickers start/end dates), count securities
   delisted per year, 1995–2016. Compare against a published delisting-rate benchmark to
   estimate the missing fraction per year. No factor or return outcomes are involved.
   - *Note:* the existing snapshot lives under `raw/`, which the planner may not open
     without authorization.
2. **Option-B/C vendor research:** documentation and licence review only, with a
   shortlist and a cost/coverage/licensing table. This extends `8bcc151`.
3. **Decision gate:** choose A, B or C, based on (1) and (2) together with the §1
   requirement. Only then plan a Dataset v2 contract.

## 5. Non-claims

- No vendor fact here is certified.
- No data was acquired or read.
- The power numbers are approximations: i.i.d. monthly periods, the four cells treated as
  identical, and an independence assumption across hypotheses that overstates
  family-level power.

## 6. Findings — step 1 (Tiingo metadata probe) and step 2 (vendor research), 2026-10-08

### 6.1 Tiingo survivorship probe (metadata only)

- **Method:** the supported-tickers snapshot, 108,847 rows, at
  `data_store/discovery_dataset_v1/smoke_starter_live/snapshot/supported_tickers.csv`.
  It was read without returns or prices, filtered to US exchanges / Stock / USD (16,193
  rows), and counted securities alive at mid-year and securities ending per year.

| Year | Tiingo alive at mid-year | Tiingo delistings recorded (plain tickers) |
|---|---|---|
| 1996 | 1,829 | 0 |
| 2001 | 2,732 | 0 |
| 2006 | 3,481 | 1 |
| 2009 | 4,148 | 41 |
| 2012 | 4,653 | 55 |
| 2015 | 5,598 | 294 |
| 2018 | 5,940 | 502 |

- **External benchmark:** US exchange listings peaked at about **8,025 in 1996** (Doidge,
  Karolyi and Stulz, *The U.S. listing gap*, JFE 2017; updated in the 2025 *Financial
  Review* paper), then roughly halved by 2012.
- **Read:** Tiingo holds about 23% of the 1996 listed population, and its count rises
  where the true count fell. It records essentially **no** delistings before 2009, and
  only about 300–500 per year from 2015. Pre-2009 Tiingo history is close to
  survivors-only.
- **Ruling: Option A is not viable.** Even 2009–2014 under-records delistings. This
  confirms the contract's 2015 start.

### 6.2 Vendor research (documentation only; prices and terms as published, to verify before purchase)

| Vendor | US history | Delisted | Fundamentals | Price (published) | Licence | Delivery |
|---|---|---|---|---|---|---|
| Norgate Data, Platinum | 1990+ (Diamond tier 1950+) | yes; more than 50,000 US listed + delisted | some "extras & fundamentals" | USD 630 / 12 months (Platinum) | personal subscription; terms to read | local client, not a REST API. **Windows-only updater (to verify on macOS)** |
| Sharadar via Nasdaq Data Link | prices 1998+, fundamentals 1990+ | yes; more than 21,000 tickers, "99% survivorship-bias free" | yes, point-in-time oriented | not shown publicly | non-professional vs professional tiers; **no redistribution** | REST API (cross-platform) |
| CRSP via WRDS | 1926+ | gold standard (delisting returns) | via Compustat | institutional | institutional | — likely inaccessible |

**Preliminary recommendation:** Sharadar.
- It delivers survivorship-free prices from 1998 (about 25 years: IS ≈ 15 years at 60%).
- It **also opens fundamentals**, which are the larger lever for genuinely new hypotheses.
- Its API fits the existing adapter pattern.
- Its no-redistribution licence matches the project's existing data policy.

**Before any purchase:**
- verify the non-professional licence fits this use;
- verify delisting-return availability (the analogue of the D7b terminal semantics);
- verify the point-in-time vintage semantics for fundamentals;
- re-run the earlier vendor-lineage criteria (`8bcc151`).

Norgate is the fallback if its macOS delivery works.

**Sources:**
- https://www.nber.org/papers/w21181
- https://onlinelibrary.wiley.com/doi/full/10.1111/fire.12439
- https://norgatedata.com/
- https://norgatedata.com/stockmarketpackages.php
- https://data.nasdaq.com/databases/SEP
- https://www.quantrocket.com/pricing/data/sharadar/
