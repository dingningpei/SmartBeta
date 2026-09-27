# Discovery Dataset v1.1 — decisions record and evidence plan (PLANNING ONLY)

- **Status:** the user approved these decisions on 2026-09-27.
- **The v1.1 classification amendment is NOT frozen.** It is on HOLD pending
  measured SEC evidence coverage.
- **This file is not the contract.** The Dataset-v1 contract
  (`docs/discovery_dataset_v1_contract.md`, SHA-256 `d03f3554…fa80e3`) stays
  unchanged as history. The v1.1 amendment will be a separate document once
  frozen.
- **Scope of v1.1:** security classification only, plus the Q-D6-1
  clarification below. No other v1 methodology changes.

## 1. Eligible-class policy (decided; applied only through positive evidence)

**ELIGIBLE**, only when positive, interval-dated evidence identifies the
listed instrument as one of:
- common stock;
- ordinary shares;
- classed common shares (Class A, B, …);
- ADR/ADS representing common or ordinary equity;
- REIT common shares;
- **SPAC common or Class A common shares**, only when positive evidence
  explicitly identifies the listed instrument as common equity.

**NOT ELIGIBLE:**
- preferred, depositary preferred;
- warrant, unit, right;
- note or debt;
- ETF, mutual fund, closed-end fund, BDC;
- LP/MLP partnership units, SBI or trust units;
- tracking stock;
- SPAC units, warrants and rights;
- exchange test issues.

**Rules:**
- **The absence of NON_COMMON evidence never establishes COMMON_STOCK.**
- A ticker suffix convention and Tiingo `assetType=Stock` are supporting or
  exclusion evidence only. **Neither can promote a key to COMMON_STOCK.**
- Current directories and current Tiingo metadata are never projected
  backward. **No further Tiingo metadata requests are authorized.**
- **Outcomes:** `COMMON_STOCK`, `NON_COMMON(reason)` or `CLASS_UNRESOLVED`.
  `CLASS_UNRESOLVED` fails closed and is D6-ineligible.
- Evidence binds to a `security_key` only when its date falls inside that
  key's listed interval and its symbol and exchange match.

**Clarification 2026-09-27 — REIT / Shares of Beneficial Interest (user
decision 1):**
- **A.** Interval-dated positive evidence establishes that the listed
  instrument is the issuer's common equity, and the issuer or security is a
  REIT: `COMMON_STOCK`, subtype `REIT_COMMON`. Eligible.
- **B.** "Shares of Beneficial Interest" appears, but the evidence does not
  establish eligible REIT or common equity: `CLASS_UNRESOLVED`.
- **C.** Positive evidence establishes a non-common trust, fund or unit
  interest: `NON_COMMON`, with the matching reason.
- SBI or beneficial-interest wording alone is **neither** NON_COMMON **nor**
  COMMON_STOCK evidence.
- The 42 Wayback keys provisionally measured as SBI/trust are **not** carried
  into final classification. They are re-evaluated under the frozen vocabulary
  when D-SC is implemented.

**Wayback evidence status:** preserved as evidence, not certified
classification. Its provisional name heuristics are **not frozen**:
- a common-looking name is not automatically `COMMON_STOCK`;
- an SBI- or trust-looking name is not automatically `NON_COMMON`.

The final vocabulary must operate on positive evidence rules. The Nasdaq Daily
List purchase remains not authorized.

## 2. Q-D6-1 — missingness eligibility is point-in-time (decided)

**Problem.** Contract §3 rule 6 ("not excluded under §10") combined with §10
("> 5% VENDOR_MISSING or VENDOR_NULL across its in-window listed sessions")
leaks future information if read over the whole window. It also contradicts
§3's "what eligibility never uses: … future …". The literal whole-window
reading is **rejected** for formation-date eligibility.

**Frozen interpretation (D6 applies it; D5 only supplies session records):**
at formation session f, for an `INTERVAL_ASSIGNED` key:

- `S_f` = the trusted NYSE sessions in `[max(listed_start, 2015-01-02),
  min(listed_end, f−1)]`.
- A **usable row** is present, on-calendar, not CORRUPT and not
  INTERVAL_MISMATCH, as defined in `task-d5-qc.md`.
- `first_f` = the first session in `S_f` with a usable row. Sessions in `S_f`
  before `first_f` are NOT_YET_LISTED and are excluded from the denominator.
- **Denominator** `D_f` = the sessions in `S_f` from `first_f` through f−1.
- **Numerator** `M_f` = the sessions in `D_f` with no usable row, which
  covers VENDOR_MISSING and CORRUPT days, plus sessions whose row is
  VENDOR_NULL.
- **Clarification 2026-09-27 (user decision 2).** The numerator counts
  **trusted listed sessions that should have usable data but do not**. It is
  not a count of corrupt rows. For each trusted listed session in `D_f`:

  | Session state | Denominator | Numerator |
  |---|---|---|
  | usable observation | +1 | +0 |
  | no observation | +1 | +1 |
  | observation exists but is unusable/corrupt | +1 | +1 |
  | vendor-null observation | +1 | +1 |

  An `OFF_CALENDAR_ROW` is not a trusted session, so it enters **neither**
  count, and no missing session is manufactured from it. Its frozen D5
  disposition is unchanged: CORRUPT security-day, retained, flagged, and
  excluded from session computations.
- The key fails §10 at f when `M_f / D_f > 0.05`, or when `D_f` is empty.
- The expanding window has no trailing cap, because the contract defines none.
  It runs alongside the unchanged §3 rule 4 (listing age ≥ 252 sessions) and
  §5 (≥ 50 valid sessions in f−63…f−1).
- **Invariant:** no observation, flag or listing fact dated after f−1 can
  change eligibility at f. `listed_end` enters only through `min(listed_end,
  f−1)`.

  One residual is disclosed rather than hidden: the snapshot's `listed_end`
  for a delisted security is a vendor fact recorded at the snapshot date. It
  is used here only to cap `S_f` at f−1, never to prefer survivors (§3 "use
  of vendor end dates").

- **Whole-window missingness** remains a D5 diagnostic only.
- **v1.1 textual delta:** §10's exclusion sentence becomes "at formation
  session f, a security whose VENDOR_MISSING / VENDOR_NULL / unusable sessions
  exceed 5% of its listed sessions from its first usable row through f−1 is
  ineligible at f". §3 rule 6 cross-references this PIT form.

## 3. Evidence plan (approved; SEC network access blocked on contact address)

**A. Internet Archive, auxiliary (authorized).** The archive holds historical
captures of Nasdaq Trader `nasdaqlisted.txt`, `otherlisted.txt` and
`nasdaqtraded.txt`, which carry the security name, test-issue flag, ETF flag
and file creation time. A capture dated inside a key's interval with a
matching symbol is interval-dated positive or negative evidence. The survey
results are recorded in the planner report and stored outside Git
(`dsc_evidence/wayback-20260927/`).

**B. SEC EDGAR bounded coverage probe (approved, but NOT RUN).** No approved
project contact address exists for the SEC-required declared User-Agent. The
probe stops before any network access until the user supplies an address
approved for this purpose.

**Predeclared sample.** Selection used only `identity_assignment` fields
(`state`, `exchange`, `listed_end`) and SHA-256 ordering of
`"dsv1.1-sec-probe-v1:" + security_key`. No returns or outcomes were used.
Strata are exchange × cohort: `ENDED_PRE_2019` (listed_end < 2019-01-01),
`ENDED_2019_2021`, `ENDED_2022_2026` (< C), and `ACTIVE` (listed_end ≥ C).
Five keys per stratum, fewer where the stratum is smaller.

| Stratum (INTERVAL_ASSIGNED population) | Sampled keys |
|---|---|
| NASDAQ / pre-2019 (585) | NEWS:2006-12-14, HMIN:2006-10-26, PFED:1996-08-12, BRCD:1999-05-25, BEAV:1990-04-25 |
| NASDAQ / 2019–2021 (609) | ISNS:1995-05-05, ESIO:1990-03-26, OFSSL:2018-04-20, GFNSL:2014-09-24, BMTC:1985-08-08 |
| NASDAQ / 2022–2026 (1,779) | MOBV:2022-09-26, JMACU:2021-10-08, GIACU:2021-10-12, MUDS:2021-01-29, ESPR:2013-06-26 |
| NASDAQ / active (3,876) | VLOSU:2024-08-01, NXPI:2010-08-06, USAR:2023-07-17, MVST:2019-03-27, TWIN:1987-12-31 |
| NYSE / pre-2019 (395) | FNFV:2014-06-23, GEH:2013-02-01, AF:1993-11-18, RRMS:2011-12-08, IL:2016-01-04 |
| NYSE / 2019–2021 (318) | ISF:2007-06-13, PRSP:2018-06-01, WUBA:2013-10-31, PIY:2003-10-09, TPGH:2017-08-24 |
| NYSE / 2022–2026 (478) | AAQC:2021-05-10, APE:2022-08-22, HIGA:2020-12-11, SJR:1998-07-01, HGH:2012-04-25 |
| NYSE / active (2,025) | AXS:2003-07-01, RDN:1992-10-30, CDRE:2021-11-04, NTEST-G:2019-07-17, T:1984-07-19 |
| AMEX+NYSE MKT / pre-2019 (1) | OSGB:2014-10-08 |
| AMEX+NYSE MKT / 2019–2021 (15) | GDP:2016-12-08, GPR:2013-08-21, BWL-A:1984-09-07, NSU:2002-12-19, CRHM:2015-09-03 |
| AMEX+NYSE MKT / 2022–2026 (53) | SATX:2022-10-28, AINC:2014-11-13, EMAN:2000-01-21, SOQ:2017-09-22, ID:2001-01-02 |
| AMEX+NYSE MKT / active (219) | CCEL:2011-02-23, EVI:1990-03-27, TOPS:2004-08-02, GENC:2003-07-15, LGPS:2025-03-25 |

The key prefix `TIINGO:` is omitted in the table. There are 56 stratified keys.

**Named specimen types (16).** These were selected by instrument type, using
current-directory names for ACTIVE keys only as a sampling aid, then SHA-256
order. The current names are never used as evidence.

| Type | Keys |
|---|---|
| Ordinary common controls | A:1999-11-18, AAPL:1980-12-12 |
| ADR/ADS | YMT:2025-08-19, YPF:1993-06-29 |
| REIT common | RYN:1994-02-17, FVR:2024-10-02 |
| SPAC Class A common | OACC:2024-12-16, EVAC:2025-08-22 |
| Known non-common | AACBR:2025-04-07, AACBU:2025-02-13, AACIW:2021-11-11, ACGLO:2017-08-15, ARGD-1:2002-12-10 |
| Reused tickers (binding tests) | AAAP:2015-11-11, ACI:1988-08-12, ACI:2020-06-26 |

**Method (public SEC endpoints only; efficient mechanisms first):**
1. Resolve the CIK. Use SEC `company_tickers_exchange.json` (current), which
   is **only a hint**. For historical or reused symbols, use EDGAR full-text
   search for the trading symbol within the key's interval dates. No CIK is
   inferred from issuer identity alone.
2. For each resolved CIK, read the submissions JSON to get the filing list
   with dates and forms.
3. **Structured target (A):** for 10-K, 10-Q, 20-F and 8-K filings dated
   inside the interval (2019 onward), read the cover-page inline-XBRL facts
   `dei:Security12bTitle`, `dei:TradingSymbol` and
   `dei:SecurityExchangeName`, and match symbol and exchange.
4. **Historical target (B):** check Form 8-A12B and Form 25 availability and
   the extractability of the registered class title and exchange within the
   interval.
5. **Record per key:** security_key, exchange, cohort, source, filing date,
   form, symbol match, exchange match, class-title availability, class
   outcome, evidence quality, resolved or unresolved. No prices, returns or
   factor data.
6. **Coverage report by exchange, cohort and specimen type**, separating
   positive COMMON, positive NON_COMMON, conflicting and no usable evidence.
   Coverage is not extrapolated into certified full-pool coverage.

**Request ceiling and rate:**
- at most **600** SEC requests in total, including retries;
- at most **2 requests per second**, well below SEC's published 10/s;
- declared User-Agent `smart_beta dataset-v1.1 coverage probe (<approved
  contact>)`;
- stop on any HTTP 403 or 429, or when the ceiling is reached.

**C. Nasdaq Daily List (licensed): not authorized.** It will be reconsidered
only after the SEC coverage results.

## 4. What stays reusable

**Acquisition, D1, D3 and D4 all remain valid.** Classification is a new
artifact (D-SC) between D4 and D6, bound to the future v1.1 amendment hash,
the snapshot hash, `identity_id` and the evidence-file hashes. Nothing is
regenerated.
