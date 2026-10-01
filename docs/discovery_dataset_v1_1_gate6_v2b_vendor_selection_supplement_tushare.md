# Discovery Dataset v1.1 — Gate-6 v2B Vendor Selection: Supplement A, Tushare (DOCUMENTATION ONLY; DATA-FREE)

- **Status:** supplementary documentation-stage evaluation, requested by the user
  on 2026-09-30 after the main research. It does **not** modify the main document
  `docs/discovery_dataset_v1_1_gate6_v2b_vendor_selection.md` (8bcc151, SHA-256
  `625bd220795cb364120110cb817af0458f4669a49d1d748480632b19f2c6a78b`). It adds two
  columns judged by the same rules, vocabulary and qualification logic.
- **Frozen authority:**
  - evidence protocol `f04d2253747e2b9fdebad3681ac93832b3d452d5463c009d17c5a16607b36c27`;
  - addendum `f88404d9a2553f37d5bc348762303ed9e78a369801facc0351f8948a0172b54f`.
  - Neither is changed.
- **Research date:** 2026-09-30. Master at start: `8bcc151`.
- **Firewall (addendum A5):** only Tushare's public official documentation pages
  and this repository's own committed prior findings were read.
  - **No** Tushare interface was called, through the official API or the proxy.
  - **No** token or credential was used.
  - No specimen security, ticker, date, price or corporate action was queried or
    searched. No contact was made.
- **Why it was added:** the user noted that the Tushare proxy also carries US
  equities. Under addendum A6, adding a candidate is permitted when official
  documentation gives a credible reason to evaluate it. The question remains only
  whether the source satisfies the frozen MUST set.

## 1. Two separate candidates

The repository's standing rule is that the official Tushare account and the
third-party proxy are separate access paths and are never conflated. Phase 4D-B
certification `docs/phase4d_b_tushare_certification.md` (de5b1c1, SHA-256
`dc7621d9…1115`) froze these items:
- item 12 `PROXY DETERMINISM = NOT CERTIFIED`;
- item 13 `PROXY SUFFICIENT FOR PHASE 4D-B POC = YES`;
- item 14 `PROXY SUFFICIENT FOR PRODUCTION = NO`.

The two columns are therefore:

| Column | Exact product |
|---|---|
| **TUSHARE-OFFICIAL** | Tushare Pro US-equity interfaces through the official API: `us_basic` (doc 252), `us_tradecal` (doc 253), `us_daily` (doc 254), `us_daily_adj` (doc 338), `us_adjfactor` (doc 402). The US daily-price permission is separate. |
| **TUSHARE-PROXY** | the same interfaces as fronted by the third-party relay `pcd.mobcvb.cn/tushare/pro`. Proxy characteristics are taken only from previously recorded, committed, PROXY-OBSERVED findings (Phase 4D-A.2 / 4D-B). No new proxy observation was made. |

## 2. What the official documentation states (all accessed 2026-09-30)

- **`us_daily` (doc 254):**
  - "获取美股行情（未复权），包括全部股票全历史行情" (unadjusted US quotes, full
    history for all stocks).
  - Fields: `ts_code, trade_date, close, open, high, low, pre_close, change,
    pct_change, vol, amount, vwap, turnover_ratio, total_mv, pe, pb`.
  - Maximum 6,000 rows per request.
  - `close` has no further definition.
- **`us_daily_adj` (doc 338):**
  - "价格 * 复权因子 = 复权价格" (price × adjustment factor = adjusted price).
  - Fields add `adj_factor`, `free_share`, `total_share`, `exchange`
    (NAS / NYS / OTC). Maximum 8,000 rows per request.
- **`us_adjfactor` (doc 402):** the daily `cum_adjfactor` (cumulative adjustment
  factor) and `close_price`. **No** event type, ex-date, split ratio or cash amount
  is documented.
- **The US catalogue (doc 2):** basic info, trade calendar, daily, adjusted daily,
  adjustment factor, and four financial-statement interfaces. **There is no US
  dividend, split or corporate-action event interface.**
- **`us_basic` (doc 252):**
  - Fields `ts_code, name, enname, classify (ADR/GDR/EQ/PF), list_date,
    delist_date`; `list_status` L/D/P.
  - Explicitly: "美股代码存在代码复用，变更，多市场交易等" (US codes are reused,
    changed and traded on multiple markets).
  - No dated ticker history and no persistent non-ticker identifier.
- **Permissions (doc 290):** "美股日线 … 单独2000元 … 每分钟500次，每次8000行 …
  全历史". Institutional pricing is 10× the individual rate.
- **Provenance:** no document read names an upstream source for US prices or
  adjustment factors. The general FAQ describes Tushare collecting and processing
  data on its own platform without naming sources.
- **Usage terms:** attribution ("Tushare数据") is required when data are used
  publicly. Retention and evidence-sealing rights for this purpose are not
  addressed.

## 3. M1–M10 matrix

Statuses: PASS / FAIL / NOT_DOCUMENTED (ND) / REQUIRES_COMMERCIAL_CONFIRMATION (RCC).

| MUST | TUSHARE-OFFICIAL | TUSHARE-PROXY |
|---|---|---|
| M1 raw official close, 2014-12 to 2023-07, incl. inactive/renamed | RCC | RCC |
| M2 separate adjustment info **and** CA records (split, cash) | **FAIL** | **FAIL** |
| M3 dated symbol history, instrument, type, class, persistent ID | **FAIL** | **FAIL** |
| M4 price family IND-A | ND | ND |
| M5 CA family IND-A | ND | ND |
| M6 currency, per-share unit, precision | ND | ND |
| M7 reproducible, retainable, hashable retrieval | PASS | RCC |
| M8 licensing for sealed local retention | RCC | ND |
| M9 finite-cap retrieval feasible | PASS | RCC |
| M10 retrieval without disclosing outcomes | PASS | PASS |
| **Qualification state** | **DISQUALIFIED** | **DISQUALIFIED** |

**Cell evidence:**
- **M1 RCC (both):** unadjusted, full history, and delisted names are listable
  (`list_status=D`). But `close` is undefined: official listing-venue close vs last
  trade vs fallback is not stated.
- **M2 FAIL (both):** the documentation affirmatively shows only a cumulative
  adjustment factor and adjusted prices. The US catalogue has no corporate-action
  record interface (no event type, ex-date, split ratio or cash amount). Parent §2
  requires *both* adjustment information and CA records, and an adjusted series or
  factor alone does not satisfy M2. A factor discontinuity cannot distinguish a
  split from a distribution.
- **M3 FAIL (both):** identity is ticker-based (`ts_code`, for example AAPL), and
  the documentation itself warns that codes are reused and changed. No dated symbol
  history and no persistent non-ticker identifier are documented. Instrument type
  is limited to ADR / GDR / EQ / PF, with no warrant, right, unit or share-class
  distinction. Ticker-only identity is insufficient under M3.
- **M4 / M5 ND (both):** no official material names or characterizes the upstream
  sources. Unlike the main candidates, nothing even indicates an own acquisition
  network, so the status is ND rather than RCC. For the proxy, the relay layer adds
  a further undocumented transformation and transport step. Tiingo-side lineage
  (questions T1–T4) is also unresolved.
- **M6 ND (both):** no currency, unit or precision documentation for US fields.
- **M7:**
  - Official: PASS. A documented API with parameterized requests.
  - Proxy: RCC. The recorded PROXY-OBSERVED behaviour includes a proxy-imposed
    ≤366-day window, intermittent 503/429 responses, and identical requests
    returning an empty result on some attempts and data on others (Phase 4D-A.2).
    Phase 4D-B item 12 `PROXY DETERMINISM = NOT CERTIFIED` stands.
- **M8:**
  - Official: RCC. Attribution is documented; sealed-retention rights are not.
  - Proxy: ND. The repository's Tushare licensing disposition is scoped to direct
    official access. No terms from the third-party proxy operator establish any
    retention right, and Phase 4D-B item 14 records that a third-party proxy's
    terms are not a production dependency contract.
- **M9:**
  - Official: PASS. Documented 500 requests per minute and 8,000 rows per request
    for the paid permission.
  - Proxy: RCC. Recorded throttling and pool exhaustion; no published limits.
- **M10 PASS (both):** requests consist of ticker and date parameters only.

## 4. SHOULD / NICE (these never compensate for a failed MUST)

| Requirement | TUSHARE-OFFICIAL | TUSHARE-PROXY |
|---|---|---|
| S: volume / stale indicator | Partial: `vol`, `amount`; stale semantics ND | same, PROXY-OBSERVED |
| S: revision history | ND | ND |
| S: listing venue | Partial: `exchange` NAS / NYS / OTC (`us_daily_adj`) | same |
| S: official-close definition | ND | ND |
| S: cross-identifiers | ND | ND |
| S: bulk snapshot | ND (per-request pagination) | ND |
| N: exchange-direct, PIT vintages, per-record CA provenance, intraday | ND | ND |

## 5. Result

- **TUSHARE-OFFICIAL: DISQUALIFIED.**
  - M2 FAIL: no corporate-action records, only a cumulative factor.
  - M3 FAIL: ticker-only identity, with documented code reuse.
- **TUSHARE-PROXY: DISQUALIFIED** on the same M2 and M3 failures. It additionally
  carries the frozen Phase 4D-B dispositions `PROXY DETERMINISM = NOT CERTIFIED`
  and `PROXY SUFFICIENT FOR PRODUCTION = NO`.
- **Ruled out:**
  - The failures are affirmative documentation facts. Commercial confirmation
    cannot close them without the protocol being weakened, which addendum A6
    forbids.
  - Tushare could serve only as non-resolving descriptive context, and only
    under a separate future protocol decision. This supplement proposes no such
    use.
- **The main research's state is unchanged:**
  - QUALIFIED: none.
  - CONDITIONALLY QUALIFIABLE: LSEG-DSS and FACTSET-GP, both still dependent on
    Tiingo T1–T4.
  - NOT CURRENTLY QUALIFIED: BBG-DL.
  - DISQUALIFIED: CRSP-CIZ, plus the two Tushare columns from this supplement.

## 6. Bibliography (accessed 2026-09-30)

- **TS1** Tushare, 美股日线行情 `us_daily` — https://tushare.pro/document/2?doc_id=254
- **TS2** Tushare, 美股复权行情 `us_daily_adj` — https://tushare.pro/document/2?doc_id=338
- **TS3** Tushare, 美股复权因子 `us_adjfactor` — https://tushare.pro/document/2?doc_id=402
- **TS4** Tushare, 美股基础信息 `us_basic` — https://tushare.pro/document/2?doc_id=252
- **TS5** Tushare, data catalogue (US section) — https://tushare.pro/document/2
- **TS6** Tushare, 积分与频次权限对应表 — https://tushare.pro/document/1?doc_id=290
- **TS7** Tushare, 常见问题整理 (FAQ) — https://tushare.pro/document/1?doc_id=122
- **R1** this repository, `docs/phase4d_b_tushare_certification.md` (de5b1c1),
  items 12–14. Phase 4D-A.2 proxy characterization: recorded PROXY-OBSERVED
  findings, with no new observation made.

## Access record

- Network access: documentation only.
- Tushare / proxy API calls: 0. Credentials or tokens used: 0.
- Specimen-specific queries: 0. Vendor contacts: 0. Reserve reads: 0.
- New specimen evidence accessed: NO. Specimen changed: NO. v1 outcome changed: NO.
- Protocol changed: NO. Main vendor-selection document changed: NO.
- Knowledge-PIT append: NO.
- E3 scratch provenance retained, unmodified.
