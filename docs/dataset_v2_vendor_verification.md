# Dataset v2 — vendor verification (documentation only; S2, authorized 2026-10-14)

- **Status:** planner research record, 2026-10-14. It extends
  `docs/dataset_v2_history_feasibility.md` §6.2.
  - **No account, API key, purchase, download or vendor API call** was made.
  - Every fact below comes from **public vendor pages read on 2026-10-14** by a
    planner-delegated research agent, with the URLs given.
  - Facts are labelled **V** (verified on the vendor page) or **U** (unknown or not
    documented). Nothing here is certified (CLAUDE.md §8). Terms can change before
    purchase.
- **Scope:** survivorship-free US daily equity prices from about 1998 or earlier, plus
  point-in-time fundamentals, for a single private researcher.
- **Candidates:**
  - Sharadar, sold direct at sharadar.com (new since the feasibility note) and also via
    Nasdaq Data Link;
  - Norgate Data, Platinum;
  - EODHD, briefly;
  - CRSP/WRDS has no individual access route and is excluded.

## 1. Findings

| | Sharadar (direct) | Norgate Platinum | EODHD |
|---|---|---|---|
| Daily price start | **Dec 1997** (V) [S1] | 1990; Diamond 1950 (V) [N1] | delisted mostly from 2000 (V) [E1] |
| Delisted coverage | "99% survivorship bias free" (V) [S1, S3] | 25,222 delisted 1950–Sep 2022, no completeness claim, OTC-only excluded (V) [N2] | 26,000+ (V); EOD-only (no splits/dividends) before 2018 [E1] |
| Delisting date / reason | date, reason and acquirer (`contraticker`) in ACTIONS (V) [S4]; reason codes **U** | year-month symbol suffix, `last_quoted_date` (V) [N3]; reason **U** | **U** |
| **Delisting return / final value** | **U** (not documented) | **U** | **U** |
| Total-return inputs | `closeadj` (splits, dividends, spinoffs), `closeunadj`, ACTIONS (V) [S1, S4] | TOTALRETURN adjustment, dividend column (V) [N3] | only for delistings from 2018 (V) |
| Persistent ID | `permaticker`; a reused ticker gets a suffix on the delisted company (V) [S2, S3] | `assetid` (V) [N3] | **U** |
| Point-in-time fundamentals | ARQ/ARY/ART exclude restatements, indexed to the SEC filing date (V) [S5] | **U** | not researched |
| Licence holder | natural person, personal use incl. research and backtesting (V) [S6, S3] | one natural person, non-commercial (V) [N4] | non-professional, private (V) [E2] |
| Redistribution | prohibited, incl. derivative data that allows reverse engineering (V) | prohibited (V) | private use only (V) |
| **Retention after the subscription ends** | **delete all copies within 30 days**; research outputs that "cannot reproduce" the data may be kept (V) [S6] | **must cease use and delete exports and backups**; "Derived Data" may be kept (V) [N4] | **U** |
| Third-party / LLM processing | not addressed (**U**) | third-party processing "on the Licensee's behalf" explicitly allowed (cl. 2(iv)); LLMs not named (V) [N4] | not addressed |
| Delivery | REST API + bulk zip, any OS (V) [S1] | **Windows-only updater**; Mac needs a Windows VM, which counts toward the 2-install limit (V) [N5] | API, any OS |
| Price (published) | prices, full history **$299/yr**; prices + fundamentals + actions **$499/yr** (V) [S7] | Platinum **$630/yr** (V) [N1] | $199/yr (V) [E3] |

**EODHD is excluded.** Its delisted coverage starts mostly in 2000, and before 2018 it has
no splits or dividends for delisted names. That fails the total-return requirement for
the target history.

## 2. Mapping to the project's M-criteria (`8bcc151`, adapted to a discovery dataset)

**Note:** the M-set was written for the v1.1 Gate-6 *raw official close lineage*
question. Below, only the criteria that carry over are applied.

| Criterion | Sharadar | Norgate | Consequence for a Dataset v2 contract |
|---|---|---|---|
| M2 separate adjustment info + CA records | PASS (doc) | PARTIAL (adjustment modes + dividend column; CA records **U**) | — |
| M3 dated symbol history, persistent ID | PASS (doc) | PASS (doc; symbol history **U**) | — |
| **Delisting terminal semantics** (the D7b analogue) | **FAIL / U**: date and reason exist; no return or cash-out value | **FAIL / U** | **No vendor gives a DLRET.** v2 must treat terminal returns exactly as v1 does: INCOMPLETE, bounded by the P-Q T0/TS scenarios. Sharadar's reason + acquirer could *classify* terminals (acquisition vs failure). That is a **new design input** for the TS question (Campaign-4 draft §3.2), never a silent upgrade |
| **M7 reproducible, retainable, hashable retrieval** | **CONDITIONAL**: retainable only **while subscribed** | **CONDITIONAL**: same | K-PIT and certification records bind content hashes. Re-verifying them needs the raw data, so **the subscription must be kept for as long as re-verification is wanted** |
| **M8 licensing for sealed local retention** | **CONDITIONAL** (as M7) | **CONDITIONAL** | The sealed reserve can only be held while subscribed. This is acceptable only if the subscription is treated as a standing project cost |
| M10 retrieval without disclosing outcomes | PASS (bulk zip, no query on outcomes) | PASS | — |
| **LLM / agent exposure** (new criterion, from this project's workflow) | **U** | allowed as third-party processing (LLM not named) | The project's agents (Claude planner, Pi/DeepSeek workers, the DeepSeek generator) must **never receive raw vendor rows**. Only code, schemas and aggregate statistics may reach them. That rule must be written into the v2 contract either way. Whether Sharadar's terms permit even that needs a written answer (Q2) |

## 3. Planner assessment

- **Sharadar remains the preferred candidate:**
  - its history starts in 1997;
  - `permaticker` survives ticker reuse;
  - delisting reasons and acquirers are recorded;
  - it has point-in-time fundamentals;
  - it works on any OS through the API;
  - it is the cheapest qualifying option ($299–499/yr).
- **Norgate is the fallback:**
  - it is stronger on the licence (third-party processing is explicit) and has longer
    history;
  - but it needs a Windows VM, its point-in-time fundamentals are unknown, and it costs
    more.
- **Neither is qualified yet.** Three open items decide the choice:
  1. **The LLM-exposure question (Q2).** If Sharadar forbids any processing that touches
     an AI service, the project workflow conflicts with the licence. The answer must be
     in writing.
  2. **Terminal semantics (Q1).** What the reason codes and the final `closeunadj`
     mean. This decides whether v2 improves on v1's TS treatment or only matches it.
  3. **Retention.** Is the standing subscription cost (about $300–500/yr, indefinitely)
     acceptable to you?

## 4. Questions to send to the vendor (draft; **you** send them; the planner contacts no vendor)

To Sharadar (support contact on sharadar.com):

1. **Q1 (terminal semantics).** For delisted securities in SEP and ACTIONS:
   - (a) the full list of delist-reason codes and the meaning of the `value` field;
   - (b) for cash or stock acquisitions, is the consideration per share recorded
     anywhere?
   - (c) is the last `closeunadj` the last exchange trade, or can it be an OTC price
     after delisting?
2. **Q2 (processing).** Under the personal-use licence, may I:
   - (a) process the data with my own scripts on my own computers and on a cloud VM I
     control;
   - (b) use AI coding assistants that see my code and aggregate statistics computed
     from the data, but never raw data rows?
3. **Q3 (retention).** After cancelling, may I keep content hashes of the files I
   downloaded, plus aggregate results (portfolio return series), for audit purposes?
4. **Q4 (channels).** Do the sharadar.com direct licence and the Nasdaq Data Link
   licence differ for a personal user?
5. **Q5 (terms date).** What is the effective date and version of the current terms?

**Norgate** (only if Sharadar fails Q2): whether delisting reasons or merger
consideration are available; whether Gold/Platinum fundamentals are point-in-time;
whether Windows-on-ARM VMs on Apple Silicon are supported.

## 5. Decision gate S1-B (after the answers)

Choose Sharadar, Norgate, or no Dataset v2 (fall back to Path A on v1.2). Only after
that choice does the planner draft a Dataset v2 contract. That contract must keep
2023-07-03 … C sealed as the v1 reserve (Campaign-4 draft §1). **No purchase happens
before your explicit authorization.**

## 6. Sources (read 2026-10-14)

| Key | URL | Note |
|---|---|---|
| S1 | https://sharadar.com/docs/stocks | — |
| S2 | https://sharadar.com/docs/tickers | — |
| S3 | https://sharadar.com/docs/faqs | — |
| S4 | https://sharadar.com/docs/actions | — |
| S5 | https://sharadar.com/docs/fundamentals | — |
| S6 | https://sharadar.com/terms | no effective date shown |
| S7 | https://sharadar.com/pricing | — |
| N1 | https://norgatedata.com/stockmarketpackages.php | — |
| N2 | https://www.norgatedata.com/data-content-tables.php | — |
| N3 | https://pypi.org/project/norgatedata/ | v1.0.77 |
| N4 | https://norgatedata.com/subscribe/eula.php | EULA 20261002 |
| N5 | Norgate FAQ / system requirements | NDU Windows-only |
| E1 | https://eodhd.com/financial-academy/financial-faq/historical-stock-prices-for-delisted-companies | — |
| E2 | https://eodhd.com/financial-apis/terms-conditions | — |
| E3 | https://eodhd.com/pricing | — |

**Nasdaq Data Link** pricing and licence pages render through JavaScript and could not
be read (**U**).

## 7. Budget constraint and low-cost / China-accessible sources (2026-10-14)

- **User budget:** **≤ ¥300 per year** (about USD 42). This excludes Sharadar ($299–499/yr),
  Norgate ($630/yr), Tushare US daily (¥2,000, DISQUALIFIED in Supplement A anyway),
  Massive Advanced (about $1,910/yr) and FMP Premium (about $588/yr).
- **Second research pass:** documentation only; same firewall; read 2026-10-14.

| Source | Delisted US price history | Total-return inputs | Identity | Fits ≤ ¥300/yr |
|---|---|---|---|---|
| 米筐 RQData | no US equities (V) | — | — | — |
| AKShare (新浪 / 东方财富) | not documented; universe = current listings (V) | qfq factor only | ticker | free, but R1 fails |
| 富途 / 长桥 OpenAPI | not documented; 长桥 daily from 2010-06 (V) | QFQ | ticker | free with a brokerage account, but R1 not shown |
| 聚宽, 掘金, BaoStock, 天勤, efinance | no US coverage documented | — | — | — |
| Wind / iFinD / Choice | U | U | U | no (institutional contracts) |
| MarketParquet | **yes, from 2000** (V), about 8,500 delisted | **split-only; no dividends, no CA files** (V) | ticker + `-DELISTED` suffix | $79 once (about ¥570); fails R2; lineage undisclosed |
| FMP / Alpha Vantage | delisted *lists* yes; delisted *prices* U | adjusted (claimed) | ticker + dates | no |
| Massive (Polygon) | yes (V); dividends only from 2008 | yes from 2008 | FIGI | no |
| yfinance / Stooq | no / U | — | ticker | — |

**Result:** **no source meets the requirements within ¥300/yr.** The requirements are:
- R1: delisted US price history;
- R2: dividend-inclusive total returns;
- a stable identity.

The only budget-adjacent options are compromises:
- **(i) MarketParquet:** it lacks dividends, so momentum/value total returns would be
  biased. Its lineage is undisclosed.
- **(ii) A single Sharadar month ($39):** the licence requires deleting the raw data
  within 30 days after the subscription ends. Every Dataset v2 build, campaign run and
  re-verification would then have to fit inside paid months, which the project's
  multi-week cadence does not.

**Planner recommendation:** do not build Dataset v2 under the current budget. Return to
the S1 choice with Path B closed (decision pending).
