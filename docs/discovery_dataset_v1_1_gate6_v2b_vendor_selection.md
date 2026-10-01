# Discovery Dataset v1.1 — Gate-6 v2B Vendor Selection Research (DOCUMENTATION ONLY; DATA-FREE)

- **Status:** documentation-stage capability research against the frozen v2B
  requirements. It was done under the user's "G6-v2B VENDOR SELECTION RESEARCH —
  DOCUMENTATION ONLY" authorization. Research date (all accesses): 2026-09-30.
  Master at start: `c165f6e`.
- **Frozen authority (verified before research):**
  - evidence protocol `docs/discovery_dataset_v1_1_gate6_v2b_evidence_protocol.md`,
    SHA-256 `f04d2253747e2b9fdebad3681ac93832b3d452d5463c009d17c5a16607b36c27`;
  - addendum `docs/discovery_dataset_v1_1_gate6_v2b_protocol_addendum.md`, SHA-256
    `f88404d9a2553f37d5bc348762303ed9e78a369801facc0351f8948a0172b54f`.
  - Gate-6 v1: INCONCLUSIVE. Dataset v1 admission: HOLD. The same sealed 180
    specimens remain authoritative.
- **Firewall (addendum A5):**
  - only public official documentation, product pages, API schemas and official
    SDK documentation were read;
  - no specimen security, ticker, date, price, corporate action or availability
    was queried or searched;
  - no vendor API or data query, no account, credential, trial, purchase or
    contact.
- **The governing question** (addendum A6): does a source satisfy every frozen MUST
  requirement? This document **does not choose a vendor**. Cost is recorded only
  where public, and is not a qualification criterion.

## 1. Candidate products (exact product, not company)

| Column | Exact product evaluated | Field families used |
|---|---|---|
| **CRSP-CIZ** | CRSP US Stock Database (daily), Flat File Format 2.0 (CIZ), Center for Research in Security Prices (University of Chicago Booth affiliate; documentation now hosted by Morningstar Indexes) | price: `DlyPrc` / `DlyClose` / `DlyPrcFlg` / `DlyVol`; CA: `StkDistributions` (`DisType`, `DisExDt`, `DisDivAmt`, `DisFacPr`); identity: `PERMNO`, `PERMCO`, security-info history |
| **LSEG-DSS** | LSEG (Refinitiv) DataScope Select: Price History / Single Price History reports, Corporate Actions reports, Terms and Conditions and RIC maintenance, via REST API | price: Price History unadjusted fields, including `Universal Close Price`; CA: Corporate Actions (Capital Changes, Dividends) and Price History adjustment-value fields; identity: RIC, CUSIP, ISIN, SEDOL |
| **FACTSET-GP** | FactSet Global Prices API v1 (API version 1.12.0 per the official SDK): Prices and Corporate Actions endpoints | price: `price` (closing price) with `adjust=UNSPLIT`, `volume`, `tradeCount`; CA: corporate-action events with split terms and unadjusted cash amounts; identity: FactSet Permanent Identifier (`-R` regional, `-L` listing) |
| **BBG-DL** | Bloomberg Data License (Per Security and Bulk datasets): Exchange & Contributed Pricing, Corporate Actions, Reference Data content | per public product material only. Field-level documentation was not publicly accessible |

No further candidate was added. No other official documentation found during this
research gave a credible reason to expect a source to satisfy the MUST set better
than these four.

## 2. Cross-cutting finding: the incumbent's lineage is undisclosed

IND-A (parent §7; addendum A4) requires that a candidate's record for a field family
be **documented as not inheriting from Tiingo's acquisition, transformation,
redistribution or correction lineage**. Tiingo's public documentation does not name
its upstream sources:
- "Each feed is made up of at least 3 data sources on average" (Tiingo EOD product
  page).
- "Our meta information comes from a variety of sources" (Tiingo EOD
  documentation).
- Tiingo applies a "proprietary data cleaning framework" with "manual override",
  and follows CRSP's adjustment *methodology* for adjusted prices. That is a shared
  method, not a shared data lineage.

**Consequence:** no candidate's price or CA family can be graded IND-A from public
documentation alone, because the comparison lineage (Tiingo's) is itself
undocumented. Every candidate's M4 and M5 is therefore at best
REQUIRES_COMMERCIAL_CONFIRMATION, and closing it needs written lineage information
from **both** the candidate and Tiingo (§10, questions T1–T4).
- If Tiingo's lineage cannot be obtained, every candidate remains IND-U, which is
  treated as IND-B. The frozen outcome would then be NO QUALIFYING SOURCE.
- This is an application of the frozen fail-closed rule, not an ambiguity. **No
  protocol change is needed or proposed.**

## 3. M1–M10 matrix

Statuses: PASS / FAIL / NOT_DOCUMENTED (ND) / REQUIRES_COMMERCIAL_CONFIRMATION (RCC).

| MUST | CRSP-CIZ | LSEG-DSS | FACTSET-GP | BBG-DL |
|---|---|---|---|---|
| M1 raw official close 2014-12 to 2023-07, incl. inactive/renamed | **FAIL** | RCC | RCC | RCC |
| M2 separate adjustment info + CA records (split, cash) | PASS | RCC | PASS | RCC |
| M3 dated symbol history, instrument, type, class, persistent ID | PASS | RCC | RCC | ND |
| M4 price family IND-A | RCC | RCC | RCC | RCC |
| M5 CA family IND-A | RCC | RCC | RCC | RCC |
| M6 currency, per-share unit, precision | RCC | RCC | RCC | ND |
| M7 reproducible, retainable, hashable retrieval | PASS | PASS | PASS | PASS |
| M8 licensing for sealed local retention | RCC | RCC | RCC | RCC |
| M9 finite-cap retrieval feasible | PASS | PASS | PASS | RCC |
| M10 retrieval without disclosing outcomes or interpretation | PASS | PASS | PASS | PASS |
| **Qualification state** | **DISQUALIFIED** | **CONDITIONALLY QUALIFIABLE** | **CONDITIONALLY QUALIFIABLE** | **NOT CURRENTLY QUALIFIED** |

### Cell evidence (bibliography keys in §11)

**CRSP-CIZ**
- **M1 FAIL** [C1 "Price or Bid/Ask Average"; C2 StkDlySecurityData]:
  - The daily price is "the last trading price for that day on the exchange on
    which the security last traded" within the composite pricing network ("the
    closing price on that day represents the closing price on the PACX, not the
    NYSE"). `DlyClose` is "Last price at which a stock traded during the regular
    trading day".
  - This is a **composite last-trade construct, not the listing-venue official
    close** that the frozen truth object requires (parent §1). No official-close
    field is documented.
  - The bid/ask-average fallback is separable (`DlyPrcFlg` BA vs TR), and "All
    prices are raw prices", but neither change the close definition.
  - Coverage of 2014–2023, inactive securities and the five exchanges is
    documented [C1, C3].
- **M2 PASS** [C2 StkDistributions; flag types AS/AT]: `DisType` (CD cash
  dividend, FRS forward/reverse split), `DisExDt`, `DisDivAmt`, `DisFacPr`,
  `DisFacShr`, and separate `DlyFacPrc`.
- **M3 PASS** [C2]: `PERMNO`, which is "fixed for the life of a security"
  [C3]; security-information rows with `SecInfoStartDt`/`SecInfoEndDt`;
  `Ticker`/`TradingSymbol`; `SecurityType` (EQTY / FUND / UNIT), `ShareType` (NS /
  AD ADR / UG units), `ShareClass`; and `PrimaryExch`.
- **M4 RCC** [C1 "Data Sources"]: NYSE / NYSE MKT data come from "Interactive Data
  Corporation" (1999–present) and Arca also from Interactive Data (2006–present).
  NASDAQ comes from the NASD/NASDAQ (1982–present), with Interactive Data as a
  secondary source since 2004. Whether Tiingo shares this lineage is unknown (§2).
- **M5 RCC:** the modern distribution-data source is not named in the documents
  read. `DisAmountSourceType` exists [C2], but its source lineage is not described.
- **M6 RCC** [C1 decimal note; C2 `DisOrigCurType`]: decimal pricing with
  "phantom precision" from double storage. The USD convention is indicated, but
  rounding/tick precision is not specified.
- **M7 PASS** [C2]: delivered as versioned flat files.
- **M8 RCC** [C4 "See your subscription agreement"]: retention terms are
  contractual.
- **M9 PASS:** bulk files.
- **M10 PASS:** bulk retrieval discloses nothing.

**LSEG-DSS**
- **M1 RCC:**
  - Price History "from as far back as 1980" [L1 p. "Pricing"]. Unadjusted prices
    are the default ("When this option is cleared, unadjusted prices are retrieved
    instead (default)") [L1 Price History options]. Historical and delisted RICs
    are supported ("Allow Import of Historical Instruments … (e.g. Delisted RICs)")
    [L1 Preferences].
  - Close semantics: for equities, the `Universal Close Price` "is mapped to a price
    fact that the corresponding exchange deems to be the official closing price
    (for example, Official Close, Bid, Ask, etc.). In cases where the exchange does
    not provide an official Closing Price, Refinitiv determines the Closing Price"
    [L1 Price History Extractions]. **That is a documented fallback construct**, and
    the per-exchange mapping sits in the non-public *Data Content Guide*.
  - Compatibility therefore requires confirmation that, for US primary listings,
    the mapped fact is the listing-venue official close; that bid/ask or
    vendor-determined substitutions are flagged or separable; and that no stale
    carry-forward occurs in Price History.
- **M2 RCC:**
  - Corporate Actions cover Capital Changes (splits) and Dividends, retrievable by
    Ex Date [L1 "Corporate Actions"].
  - Price History adjustment applies to capital changes and special dividends,
    "regular cash distributions only for" certain non-US markets [L1 Price History
    options]. Separate "Adjustment Value - Capital Change" fields exist [L3].
  - The historical depth and retention of CA records for inactive issuers over
    2014–2023 is not stated.
- **M3 RCC** [L1]:
  - identifiers RIC, CUSIP, ISIN, SEDOL, among others;
  - RIC maintenance types RENM / DLST / RLST / SPLT, but the maintenance report
    covers only "the past ten days";
  - a "Last Entity" Price History option indicates awareness of RIC reuse.
  - A dated historical symbol-to-instrument history, and instrument type and class
    for inactive instruments, are not publicly documented.
- **M4 RCC** [L1 "Pricing"]: EOD prices "for all exchange-traded instruments from
  the real-time network". A US consolidated vs primary-exchange quote choice exists
  [L1 Preferences]. Upstream acquisition is not detailed, and Tiingo's is unknown
  (§2).
- **M5 RCC:** the CA acquisition lineage is not described publicly.
- **M6 RCC** [L1]: the user guide defers field semantics and the "Scaled Currency
  Field" column to the non-public *Data Content Guide*. Precision and rounding are
  not public.
- **M7 PASS** [L1, L4]: REST/SOAP/FTP extractions that return files.
- **M8 RCC:** licensing is contractual. The documents read carry only the general
  notice that redistribution is prohibited without written consent [L1, L2].
- **M9 PASS** [L2 "Limits"]: documented concurrent-request and instrument limits
  (for example 50 concurrent requests per report template, "Price History: 10,000
  instruments", and a poll limit of 200 per 60 seconds).
- **M10 PASS:** requests consist of instrument lists, dates and fields only [L1,
  L4].

**FACTSET-GP**
- **M1 RCC:**
  - "daily unadjusted price data back to 2006", with "listing and composite level
    prices" [F1].
  - `adjust=UNSPLIT` means "No Adjustments", but the default is `SPLIT` (split
    adjusted) [F2 Adjust]. Retrieval must set `UNSPLIT` explicitly.
  - `price` is defined only as "Closing Price" [F2 PricesFields]. Official
    listing-venue close vs last trade, fallback behaviour, and coverage of inactive
    securities' *prices* are not documented. The universe is stated to exclude
    warrants ("Structured Product (not including Warrants)") [F1].
- **M2 PASS** [F2 FieldsCorporateActions / CorporateAction / SplitCalendar]:
  - event codes include FSP/RSP/SPL (splits) and DVC… (cash dividends);
  - `effectiveDate` is the "date when security is traded ex-dividend";
  - split terms `distOldTerm`/`distNewTerm`, `adjFactor`, `adjFactorCombined`;
  - cash amounts `amtGrossDecUnadj` ("not adjusted for splits");
  - coverage of active and inactive companies back to 2006 [F1].
- **M3 RCC** [F2 Price/CorporateAction]: the FactSet Permanent Identifier (`-R`
  regional, `-L` listing) is documented. Dated ticker history, instrument-type and
  share-class fields are not part of this API's documented schema (they would
  require a separate symbology product, not evaluated). Warrants are excluded.
- **M4 / M5 RCC:** acquisition lineage is not documented in the API material read.
  §2 applies.
- **M6 RCC** [F2]: `currency` (ISO; default LOCAL) is documented. `precision`
  controls output decimals only (default 16), not source precision or tick
  rounding.
- **M7 PASS** [F2]: an OpenAPI REST interface with batch processing.
- **M8 RCC:** licensing is contractual.
- **M9 PASS** [F2]: documented per-request ID limits (`IdsMax…`, batch maximum
  2,000).
- **M10 PASS:** requests consist of identifiers, date ranges and fields [F2].

**BBG-DL**
- **M1 RCC** [B1, B2]: "end-of-day, intraday, snapshot and historical pricing
  options"; equity pricing "covers all equity exchanges globally and composites";
  bulk "20+ years of history". Close semantics, raw vs adjusted fields and inactive
  coverage are not publicly documented.
- **M2 RCC** [B3]: Corporate Actions with "more than 900 elements", and "daily
  Global Equity Corporate Actions … available going back to 2007". Adjustment-factor
  semantics are not public.
- **M3 ND** [B4 searched]: the Reference Data page states only generic
  identifier and security-master capability. No dated symbol history,
  instrument-type or share-class documentation was found.
- **M4 / M5 RCC** [B2, B3]: "managed by a team of experts" (pricing); CA "verified
  and validated … by our Global Data support and research team". This indicates
  own processing, but lineage is not documented. §2 applies.
- **M6 ND:** no public documentation of currency, unit or precision conventions
  for Data License fields (B1–B4 searched).
- **M7 PASS** [B1]: "REST API, SFTP, Web Services or natively in all major cloud
  providers".
- **M8 RCC:** licensing is contractual.
- **M9 RCC:** no public request or usage limits.
- **M10 PASS** [B1]: Per Security requests specify the securities and content
  selected.

## 4. SHOULD / NICE coverage (these never compensate for a failed MUST)

| Requirement | CRSP-CIZ | LSEG-DSS | FACTSET-GP | BBG-DL |
|---|---|---|---|---|
| S: volume / stale indicator | Yes: `DlyVol`; `DlyPrcFlg` TR/BA; "no price data from one trading day applied to the next" [C1, C2] | Yes: volume; null codes for non-trading days in Price History [L1, L3]. EOD universal close can carry forward [L1] | Partial: `volume`, `tradeCount` [F2]; zero/missing semantics not documented | ND |
| S: revision history | Partial: monthly / annual correction cuts; no per-record history [C1] | ND | ND | ND |
| S: listing venue | Yes: `PrimaryExch` [C1, C2] | Yes: RIC exchange; primary vs consolidated choice [L1] | Yes: `-L` listing IDs [F2] | Indicated ("all equity exchanges … and composites") [B2] |
| S: official-close definition | Documented, but incompatible (composite last trade) [C1] | Mapping documented with fallback; per-exchange detail non-public [L1] | ND | ND |
| S: cross-identifiers | Yes: CUSIP, ticker, PERMCO [C2] | Yes: CUSIP, ISIN, SEDOL, RIC [L1] | ND in this API | ND |
| S: bulk snapshot | Yes: flat files [C2] | ND (extraction platform) | Partial: batch endpoint [F2] | Yes: bulk datasets [B1] |
| N: exchange-direct close | Partial: NASDAQ direct; NYSE via Interactive Data [C1] | ND | ND | ND |
| N: PIT vintages | ND | ND | ND | ND for these products |
| N: per-record CA provenance | Partial: `DisAmountSourceType` [C2] | ND | ND | ND |
| N: intraday | No | Yes: Intraday Pricing (Tick History is a separate product) [L1] | Not in this product | Indicated [B2] |

## 5. Source-independence analysis

| Family | CRSP-CIZ | LSEG-DSS | FACTSET-GP | BBG-DL |
|---|---|---|---|---|
| Price: documented lineage | Interactive Data (NYSE / NYSE MKT / Arca); NASDAQ direct | "real-time network" | not documented | "managed by a team of experts"; exchanges and composites |
| Price: grade at documentation stage | **IND-U** | **IND-U** | **IND-U** | **IND-U** |
| CA: documented lineage | not named for the modern period | not documented | not documented | own validation team |
| CA: grade at documentation stage | **IND-U** | **IND-U** | **IND-U** | **IND-U** |

- **Why every grade is IND-U:** the candidates' lineages are only partly
  documented, and Tiingo's upstream is undisclosed (§2).
- **Brand, institutional status and popularity were not used.**
- **Shared official exchange closes are not counted as dependence** (addendum A4).
  The open question is whether any candidate's record and Tiingo's inherit from a
  common intermediate (for example a shared redistributor, or the same data vendor
  as one of Tiingo's "at least 3" sources).

## 6. Raw / official-close analysis

- **CRSP-CIZ:** raw. Composite last trade with a separable bid/ask fallback. No
  listing-venue official close, so it is **incompatible** with the frozen truth
  object.
- **LSEG-DSS:** raw by default. `Universal Close Price` maps to the exchange's
  official close where one is provided, but may substitute bid/ask, and the vendor
  determines a close where none exists. It is compatible only if the US mapping and
  the separability of substitutions are confirmed. An "Alternate Close Price"
  field also exists, with semantics in the non-public guide.
- **FACTSET-GP:** raw is available (`UNSPLIT`; the default is split-adjusted).
  "Closing Price" is undefined. Listing-level IDs exist. Official vs last trade and
  fallback behaviour need confirmation.
- **BBG-DL:** not publicly documented.
- **Corrections:** only CRSP documents correction cycles. The others are not
  documented.

## 7. Corporate-action analysis

| Need | CRSP-CIZ | LSEG-DSS | FACTSET-GP | BBG-DL |
|---|---|---|---|---|
| event type | Yes | Yes | Yes | Indicated |
| ex / effective date | Yes | Yes (by Ex Date) | Yes | Indicated |
| split ratio | Yes (`DisFacPr`/`DisFacShr`) | Factor fields | Yes (old/new terms) | ND |
| cash amount | Yes | Indicated | Yes (unadjusted amounts) | ND |
| adjustment factor | Yes | Yes (adjustment-value fields) | Yes | ND |
| history / retention 2014–2023 incl. inactive | Yes | ND | Yes (since 2006, active + inactive) | Since 2007 (fact sheet) |

An adjusted-price series alone was never counted toward M2.

## 8. Identity analysis

- **CRSP-CIZ:** strongest documented historical identity.
  - Permanent `PERMNO`/`PERMCO`, and dated security-information rows (ticker,
    trading symbol, security / share type, class, primary exchange).
  - Distinguishes common, ADR and units. Warrants and rights are not described as
    covered.
- **LSEG-DSS:** multiple identifiers and RIC maintenance events.
  - Historical dated mapping is not publicly documented.
  - Reused RICs need care ("Last Entity" option).
- **FACTSET-GP:** permanent listing and regional IDs.
  - No documented dated ticker history or type / class fields in this API.
  - Warrants are excluded from the price universe.
- **BBG-DL:** not documented publicly.
- **Rules applied:** current ticker metadata was not treated as historical identity
  anywhere. Any instrument-class coverage gap is handled per specimen by the frozen
  fail-closed identity states. It was not assessed against the specimen.

## 9. Reproducibility, retention and parameter readiness

- **Retention and licensing (M8):** RCC for every candidate. No public terms
  establish sealed local retention, retention after entitlement ends, or retention
  of derived audit artifacts. None of these rights is inferred.
- **Cost (recorded only; not a criterion):** no public list prices were found for
  any candidate. All four are institutional or academic subscription products. No
  trials were started.

Readiness of the documentation-dependent parameters (parent §4) for each candidate
that is not disqualified:

| Parameter | LSEG-DSS | FACTSET-GP | BBG-DL |
|---|---|---|---|
| τ_rel / tick tolerance | INSUFFICIENT_DOCUMENTATION | INSUFFICIENT_DOCUMENTATION | INSUFFICIENT_DOCUMENTATION |
| τ_ratio | INSUFFICIENT_DOCUMENTATION | INSUFFICIENT_DOCUMENTATION | INSUFFICIENT_DOCUMENTATION |
| τ_adj | INSUFFICIENT_DOCUMENTATION | INSUFFICIENT_DOCUMENTATION | INSUFFICIENT_DOCUMENTATION |
| μ_align | INSUFFICIENT_DOCUMENTATION | INSUFFICIENT_DOCUMENTATION | INSUFFICIENT_DOCUMENTATION |
| stale field / rule | INSUFFICIENT_DOCUMENTATION (null-code semantics partly documented) | INSUFFICIENT_DOCUMENTATION | INSUFFICIENT_DOCUMENTATION |

No parameter values were frozen.

## 10. Commercial-confirmation questions (prepared only; no contact made)

These are capability and provenance questions. None mentions specimen securities,
dates, v1 results or Gate-6 counts. Contacting any party needs separate
authorization.

**T — the incumbent (Tiingo); required for M4 and M5 of every candidate (§2):**
- T1. Which upstream sources supply Tiingo's historical US end-of-day raw close for
  2014–2023, per exchange and period?
- T2. Which upstream sources supply Tiingo's split and cash-distribution records,
  and its split factors, for the same period?
- T3. Does any named data vendor, or that vendor's redistributor, supply Tiingo
  with either field family?
- T4. What is Tiingo's close definition (official listing-venue close vs composite
  last trade), and what is its correction / override process?

**LSEG-DSS:**
- L-Q1. For US primary-listed equities 2014–2023, which price fact does `Universal
  Close Price` map to? Is it the listing venue's official close?
- L-Q2. Can bid, ask or vendor-determined closes appear in historical Price History
  for these instruments? If so, how is each substitution flagged?
- L-Q3. Does Price History ever carry a prior session's price forward? How are
  non-trading days represented?
- L-Q4. What is the acquisition lineage of historical US equity closes: direct
  exchange feeds, consolidated tape, or a third-party redistributor?
- L-Q5. What is the acquisition lineage of US corporate-action records (splits,
  cash distributions)? What is the correction policy, and is history retained?
- L-Q6. Are delisted, renamed and reassigned US securities fully available in Price
  History and Corporate Actions for 2014–2023? Is a dated historical
  RIC-to-instrument (and CUSIP) mapping with instrument type and share class
  available?
- L-Q7. What are the documented price precision and rounding conventions, and the
  currency / unit fields, for US equities?
- L-Q8. May retrieved records and raw extraction files be retained locally as sealed
  research evidence, including after entitlement ends, together with derived audit
  artifacts?
- L-Q9. Can the Data Content Guide's close-field and adjustment definitions be
  provided for the record?

**FACTSET-GP:**
- F-Q1. Is `price` at listing level the listing venue's official close or the last
  trade? Can fallback or substituted values appear?
- F-Q2. Does the price endpoint cover inactive and delisted US securities for
  2014–2023?
- F-Q3. What is the acquisition lineage of historical closes and of
  corporate-action records? What is the correction policy?
- F-Q4. Which FactSet product provides dated historical ticker-to-ID history with
  instrument type and share class? Can it be used with this API?
- F-Q5. Which instrument classes are excluded (warrants are stated; are rights and
  units excluded too)?
- F-Q6. What are the source price precision and rounding conventions?
- F-Q7. What are the retention rights for raw responses and derived audit
  artifacts, including after entitlement ends?
- F-Q8. What request-rate limits apply?

## 11. Bibliography (all accessed 2026-09-30)

- **T1** Tiingo, "End of Day Stock Price Data API" product page —
  https://www.tiingo.com/products/end-of-day-stock-price-data (sections "data
  sources", "error checking").
- **T2** Tiingo, End-of-Day API documentation —
  https://www.tiingo.com/documentation/end-of-day (field definitions; adjustment
  methodology; metadata sources).
- **C1** CRSP, *Data Descriptions Guide for CRSPAccess (FIZ) US Stock & Index
  Databases*, PDF modified 2025-05-09 (126 pp.). The original URL
  https://www.crsp.org/wp-content/uploads/guides/CRSP_US_Stock_&_Indexes_Database_Data_Descriptions_Guide.pdf
  now returns 404 after the move to Morningstar hosting. It was read from the
  Internet Archive capture of that official URL (20250618). Sections: "Data
  Sources"; "Price or Bid/Ask Average"; "Primary Exchange"; release notes on
  corrections.
- **C2** CRSP, *US Stock & Indexes Database Guide, Flat File Format 2.0*, PDF
  modified 2025-05-13 (31 pp.), and *Metadata Guide FFF 2.0 (CIZ)*, PDF modified
  2025-11-20. The original crsp.org URLs return 404. They were read from Internet
  Archive captures (20250519, 20260124). Sections: StkDistributions;
  StkDlySecurityPrimaryData / StkDlySecurityData; flag types AS, AT, PC, S2–S4, SH;
  SIZ→CIZ mapping.
- **C3** Morningstar Indexes, "CRSP US Stock Databases" product page —
  https://indexes.morningstar.com/research-data-products/crsp-us-stock-databases
- **C4** CRSP, *US Stock & Indexes Databases for SAS, ASCII & R*, modified
  2021-02-24 — https://www.crsp.org/wp-content/uploads/2023/10/crsp_us_stock.pdf
  (disclaimer; double-precision / decimal note).
- **L1** Refinitiv, *DataScope Select Release 14.5 User Guide*, v1.1 (209 pp.) —
  https://developers.lseg.com/content/dam/devportal/api-families/datascope-select/datascope-select-rest-api/documentation/overview-and-concepts/dss_14_5_user_guide.pdf
  (Pricing; Corporate Actions; Preferences; Price History options; Universal Close
  Price; RIC Maintenance).
- **L2** Refinitiv, *DataScope Select & Tick History Best Practices & Fair Usage
  Policy*, April 2021 —
  https://developers.lseg.com/content/dam/devportal/api-families/datascope-select/datascope-select-rest-api/documentation/overview-and-concepts/ds_th_best_practices_and_fair_usage_policy_apr2021.pdf
  (ch. 3 "Limits").
- **L3** Refinitiv, *DSS 13.2 Release Notes* v1.1 (Nov 2019) and *DSS 14.0 Release
  Notes* v1.1 (May 2020) —
  https://developers.lseg.com/content/dam/devportal/api-families/datascope-select/datascope-select-rest-api/documentation/release-notes/dss_13_2_release_notes_nov2019_5.pdf ;
  …/dss_14_0_release_notes_may2020_3.pdf (adjustment-value fields; null codes).
- **L4** LSEG Developers, DSS REST API Tutorial 4 (On Demand Price History) —
  https://developers.lseg.com/en/api-catalog/datascope-select/datascope-select-rest-api/tutorials/rest-api-tutorials/rest-api-tutorial-4--on-demand-price-history-extraction
- **F1** FactSet, "FactSet Global Prices API" catalog pages —
  https://developer.factset.com/api-catalog/factset-global-prices-api and
  https://www.factset.com/marketplace/catalog/product/factset-global-prices-api
  (coverage statement as indexed by the search engine. The pages render
  client-side, so the text was taken from the official page's indexed content.)
- **F2** FactSet, official SDK documentation generated from the Global Prices
  OpenAPI spec (API v1.12.0), repository `factset/enterprise-sdk`,
  `code/python/FactSetGlobalPrices/v1/docs/` (Price.md, PricesFields.md, Adjust.md,
  Precision.md, CorporateAction.md, FieldsCorporateActions.md, SplitCalendar.md,
  CorporateActionsApi.md, PricesApi.md).
- **B1** Bloomberg, "Data License" product page —
  https://professional.bloomberg.com/products/data/data-management/data-license/
- **B2** Bloomberg, "Pricing Data Solutions" —
  https://professional.bloomberg.com/products/data/enterprise-catalog/pricing/
- **B3** Bloomberg, "Bloomberg's Corporate Actions solution" fact sheet (PDF,
  2019) — https://data.bloomberglp.com/professional/sites/10/Corporate-Actions-Fact-Sheet.pdf
- **B4** Bloomberg, "Reference Data" —
  https://professional.bloomberg.com/products/data/enterprise-catalog/reference/

Search engines were used only to locate official documents. No third-party source
supports any PASS.

## 12. Result

- **QUALIFIED candidates:** none.
- **CONDITIONALLY QUALIFIABLE:** LSEG-DSS and FACTSET-GP. Every non-PASS MUST cell
  is RCC.
  - For both, M4 and M5 additionally depend on the incumbent-lineage questions
    T1–T4.
  - If those cannot be answered, both remain IND-U and the frozen outcome is NO
    QUALIFYING SOURCE.
- **NOT CURRENTLY QUALIFIED:** BBG-DL (M3 and M6 not documented).
- **DISQUALIFIED:** CRSP-CIZ (M1 FAIL: composite last-trade close; no official
  listing-venue close).
- **Selection:** no winner is selected, and the protocol is unchanged.

## Access record

- Network access: documentation only.
- Specimen-specific queries: 0. Vendor API or data queries: 0. Vendor contacts: 0.
- Provider credentials used: 0. Reserve reads: 0.
- New specimen evidence accessed: NO. Specimen changed: NO. v1 outcome changed: NO.
- Protocol changed: NO. Knowledge-PIT append: NO.
- E3 scratch provenance retained, unmodified.
