# Discovery Dataset v1.1 — Gate-6 v2B Independent-Price Evidence Protocol (DATA-FREE; VENDOR NOT SELECTED)

- **Status:** this document freezes the evidence *semantics* for a possible
  Gate-6 v2B audit. It was written under the user's "G6-v2B EVIDENCE PROTOCOL
  DESIGN — NO NEW DATA" instruction of 2026-09-30, on master `0f29168`.
- **Nothing is selected, accessed or executed.** No vendor was searched for,
  named or queried. No specimen value from any second source exists.
- **Authorities:**
  - spec `worker_tasks/dataset_v1/task-g6-specimen-audit.md` (05ef922,
    `1dc4c7e0…e7e2`);
  - clarification (cab82e0, `0b529dca…3789`);
  - addendum (4695544, `9960d3e1…cfa6`);
  - manifest (cb7797f, `b4dbfda0…cc45`);
  - v1 result (a91628e);
  - v2 diagnostic (0f29168,
    `8a8e9f638797d92bb5804aa769dee5f8ade38b28853817a74696f91bf8c86874`).
- **Sealed v1 lineage:**
  - `g6_id` `g6-2264dbcac2a09a3d`;
  - population `d2153329…2783`;
  - specimen `c0c15d20…3b1e` (180);
  - fetch plan `98aa5f81…ca66`;
  - evidence set `c3d62453…e2c6`.
  - v1 decision: **INCONCLUSIVE**.
- **v2B is an explicit post-v1 adaptation.** It was designed after observing v1
  and the v2 diagnostic, and it is reported alongside v1, never over it.

## 0. Fixed lineage and threshold

1. **Fixed for v2B:** the same 180 specimens, S1/S2/S3 strata, four bands, v1
   outcomes and v1 sealed SEC evidence and facts. No resampling, no replacement,
   no exclusion.
2. **v1 outcomes stay unchanged.** v2B produces a separate per-specimen outcome
   column (`v2b_outcome`, `v2b_reason`, `v2b_basis`) and a separate decision.
3. **Frozen threshold:**
   - S1 needs resolved ≥ 59, because the rule is INCONCLUSIVE if
     r₁ < min(59, ⌈n₁/2⌉) with n₁ = 120.
   - Each S1 band (n_b = 30) needs resolved ≥ **15**, because the rule is
     INCONCLUSIVE if r_b < min(16, ⌈n_b/2⌉) = 15.
   - This is **not** "16 required".
4. **Separately recorded defect:** the spec's power-rationale wording
   (1 − 0.9¹⁶ = 0.815) describes a value that never applies at n_b = 30. At 15 the
   stated detection is 1 − 0.9¹⁵ = 0.794. The threshold is not altered.

## 1. The truth object and what independent confirmation means (P1)

**The truth object.** For a security and a session d, it is the **official
closing price on the listing venue for d**, expressed per share, in USD, for the
exact instrument of the sealed `security_key`. A second source is evidence about
this object. It is not a new definition of it.

**What v2B tests.** Gate 6 exists to detect two classes of data error on suspect
days:
- corporate-action errors: an omitted or misdated split or cash distribution;
- price or identity errors: a bad print, the wrong series, or a date offset.

A specimen is **resolved** in v2B when the evidence *tests both error classes*
and finds neither, or confirms one. It is not resolved merely because a cause is
narrated. This is the property the frozen detection-power thresholds are
defined over: they count draws on which an error would have been detected. The
rationale is independent of the v1 counts.

**INDEPENDENT PRICE CONFIRMATION** of a specimen holds when all of the following
hold, each mechanically:

| # | Condition | Defined in |
|---|---|---|
| C1 | identity is `IDENTITY_CONFIRMED` for the sealed instrument on every session used | §6 |
| C2 | the source's price-field and CA-field independence grades are both `IND-A` | §7 |
| C3 | raw closes agree at p and at t (`RAW_AGREE`, both dates), and the ratio agrees (`RATIO_AGREE`) | §4 |
| C4 | the independent adjustment factor over (p, t] is neutral (`ADJ_NEUTRAL`), and the independent CA records have no event in the CA window | §2, §5 |
| C5 | alignment is `ALIGNED` (best lag 0) and neither p nor t is `OBS_STALE` | §4, §5 |
| C6 | the unit is `UNIT_OK`, with no `REVISION_AFFECTS_PT` | §4, §8 |
| C7 | the v1 sealed SEC facts do not contradict: neither extractor's frozen-engine outcome for the specimen is a DATA_ERROR or COMPLEX outcome, and no v1 fact for the specimen is a SPLIT, CASH_DISTRIBUTION or COMPLEX_ACTION dated in (p, t] | §3 |

**Distinguished states.** Every one is a separate, recorded state:

| Situation | State |
|---|---|
| same raw-price move | `RAW_AGREE` at p and t, and `RATIO_AGREE` |
| same adjusted-return move | `ADJ_NEUTRAL` (independent adjusted ratio equals independent raw ratio) |
| disagreement in raw prices | `RAW_CONTRADICT` (outside the frozen contradiction band) or `RAW_AMBIGUOUS` (between the agreement tolerance and the contradiction band) |
| disagreement caused by split adjustment | `ADJ_SPLIT_SIGNATURE` |
| disagreement caused by dividend adjustment | `ADJ_CASH_SIGNATURE` |
| an unexplained adjustment factor | `ADJ_UNEXPLAINED` |
| missing second-source observation | `OBS_MISSING_SECURITY` / `OBS_MISSING_DATE` |
| stale or non-trading observation | `OBS_STALE` |
| date offset between sources | `DATE_OFFSET(ℓ)` |
| ticker or security identity mismatch | the §6 identity states |
| currency or unit mismatch | `UNIT_MISMATCH` |
| source correction or revision | `REVISION_AFFECTS_PT` / `REVISION_ELSEWHERE` |
| common-upstream dependence | the source grade `IND-B` / `IND-U` (§7) |

A rule like "prices look similar" is never used. Every state is a mechanical
function of the §4 quantities.

## 2. Roles of raw and adjusted evidence (P2)

| Evidence | Role | What it cannot establish |
|---|---|---|
| Independent raw close | tests the **price and identity** class: is Tiingo's raw close the official close? | whether the raw jump is a split. Two vendors can represent a split identically in raw prices |
| Independent adjusted close / total-return series | tests the **corporate-action** class: the adjustment factor F = (adjusted ratio) / (raw ratio) over (p, t] shows whether the source applies an action in (p, t] | the cause of a move with no action |
| Independent split factors | name and date a split, and check a = ρ·f in [0.5, 1.5] (the v1 frozen explanation band) | that the split is real (the records are secondary) |
| Independent cash-distribution factors | the same for distributions: a = ρ·close_p/(close_p − amount) in [0.5, 1.5] | the same |

**Rule R-RAW.** Both raw series showing about a 2× move is **never sufficient
alone**. Classifying the move as a genuine price move additionally requires
`ADJ_NEUTRAL` **and** no independent CA record in the CA window (C4), **and** no
v1 SEC corporate-action fact in (p, t] (C7). If the independent source's
adjustment explains the move (`ADJ_SPLIT_SIGNATURE` or `ADJ_CASH_SIGNATURE`), the
specimen carries a **corporate-action error signature** instead. Every
population day is one where Tiingo's certified fields do *not* explain the move,
so such a signature is exactly the error Gate 6 looks for.

A source that cannot supply both raw and adjustment information with documented
semantics cannot satisfy C4. This is judged at source level (§10), never per
specimen.

## 3. Evidence triangulation matrix (P3)

The inputs are:
- **T:** the sealed Tiingo/D3 raw observation (context; never independent);
- **B-raw:** the independent raw state;
- **B-adj:** the independent adjustment/CA state;
- **S:** the v1 sealed SEC evidence, meaning the per-extractor frozen-engine
  outcomes and v1 facts. S is re-used, never re-extracted.

Rows are evaluated in order, and the first matching row decides. Identity (§6),
unit, independence (§7) and availability (§10) failures are resolved *before*
this matrix; they give `UNRESOLVED` with their own reason.

| Row | v1 final outcome | B-raw | B-adj / CA | v2B outcome (reason; basis) |
|---|---|---|---|---|
| M1 | DATA_ERROR (not in v1; never occurred) | — | — | the v1 outcome is carried (it is already primary evidence) |
| M2 | COMPLEX_CA_OUTSIDE_CERTIFIED_SCOPE (none in v1) | — | — | carried |
| M3 | any | `RAW_CONTRADICT` at p or t, or `DATE_OFFSET(ℓ≠0)` | any | `UNRESOLVED(ERROR_SIGNATURE_PRICE)`; **blocks PASS** (§9, §12) |
| M4 | any | `RAW_AGREE` | `ADJ_SPLIT_SIGNATURE` or `ADJ_CASH_SIGNATURE` explaining ρ in the v1 band | `UNRESOLVED(ERROR_SIGNATURE_CA)`; **blocks PASS** |
| M5 | any | `RAW_AGREE` | `ADJ_UNEXPLAINED`, or a CA record in the window that does not explain ρ | `UNRESOLVED(ADJUSTMENT_UNEXPLAINED)` |
| M6 | GENUINE_MOVE_DOCUMENTED | `RAW_AGREE`, `RATIO_AGREE`, `ALIGNED` | `ADJ_NEUTRAL`, no CA record | `GENUINE_MOVE_DOCUMENTED`(v1 type; basis `SEC_DOCUMENTED+PRICE_CORROBORATED`) |
| M7 | GENUINE_MOVE_DOCUMENTED | unavailable or ambiguous (any §10 state, `RAW_AMBIGUOUS`, `OBS_STALE`) | any non-signature state | `GENUINE_MOVE_DOCUMENTED`(v1 type; basis `SEC_DOCUMENTED_ONLY`). v1 primary resolution is not removed by missing secondary data |
| M8 | UNRESOLVED(NO_EXPLANATORY_FILING / NO_FILINGS_IN_WINDOW / a mapping reason) | all of C1–C7 | all of C1–C7 | `GENUINE_MOVE_DOCUMENTED(INDEPENDENT_PRICE_CORROBORATED)`; basis `PRICE_CORROBORATED` (`…_NO_PRIMARY` when the v1 state was a mapping reason, since no SEC evidence exists) |
| M9 | UNRESOLVED(REVIEWER_DISAGREEMENT), where both extractors' engine outcomes are in {GENUINE_MOVE_DOCUMENTED, UNRESOLVED} | all of C1–C7 | all of C1–C7 | `GENUINE_MOVE_DOCUMENTED(INDEPENDENT_PRICE_CORROBORATED)`; basis `PRICE_CORROBORATED`, with the v1 disagreement preserved in the record |
| M10 | UNRESOLVED(REVIEWER_DISAGREEMENT), where either extractor's engine outcome is a DATA_ERROR or COMPLEX outcome | any | any | `UNRESOLVED(REVIEWER_DISAGREEMENT)`. A price source never adjudicates a primary-evidence error dispute; B states are reported descriptively only |
| M11 | anything else | any | any | `UNRESOLVED(<first failing condition>)` |

**Why M8 and M9 are not a count-inflating category.** They require that both
error classes be tested and neither found: the price class via independent raw
agreement and alignment; the CA class via a neutral independent adjustment, no
independent CA record, and no SEC CA fact. That is the resolution property the
thresholds measure (§1).
- A v1 disagreement *among non-error outcomes* concerns only the causal label.
  It does not bear on either error class. Hence M9, which is decided on that
  principle and not on the count it yields.
- The reason code `INDEPENDENT_PRICE_CORROBORATED` and the `v2b_basis` column
  keep price-corroborated resolutions **separately visible**. Every report must
  show resolved counts by basis.

## 4. Comparison quantities and tolerances (P4)

**Quantities.** All are computed on the independent source's *raw* series unless
stated. Log ratios are written ℓr(x, y) = |ln(x/y)|.

| Quantity | Definition |
|---|---|
| per-date raw agreement | ℓr(c_B(d), c_T(d)) for d ∈ {p, t}, plus every session in the §5 window, for the descriptive path |
| ratio agreement | ℓr(ρ_B, ρ_T), where ρ_B = c_B(t) / c_B(p) and ρ_T is the sealed ρ |
| adjustment factor | F_B = (adj_B(t) / adj_B(p)) / ρ_B; `ADJ_NEUTRAL` if \|ln F_B\| ≤ τ_adj |
| CA explanation | for an independent split record f dated in (p, t]: a = ρ_T·f ∈ [0.5, 1.5] (the v1 frozen band). For cash: a = ρ_T·close_p/(close_p − amount) ∈ [0.5, 1.5] |
| alignment lag | for lags ℓ ∈ {−5, …, +5} trusted sessions, D(ℓ) = the median of ℓr over the §5 window. `ALIGNED` if argmin D = 0; `DATE_OFFSET(ℓ)` if argmin D = ℓ ≠ 0 and D(ℓ) + μ_align < D(0); `ALIGNMENT_AMBIGUOUS` otherwise |
| event date | the independent CA record date relative to W, using the v1 timing rules |
| persistence (descriptive only) | c_B(t+k) / c_B(p) for k = 1…5. Reported, never decisive: a genuine one-day spike in the official close is still genuine |

**Three-zone rule for raw comparison.** The zone form is frozen now:
- **`RAW_CONTRADICT`:** the ratio c_B / c_T lies outside **[0.8, 1.25]**. This
  band is frozen now; it reuses the v1 frozen `STATED_CLOSE` contradiction band
  (spec §8 step 1), so "contradiction" means the same thing in v1 and v2B.
- **`RAW_AGREE`:** ℓr ≤ τ_agree(price), where
  τ_agree(price) = max(τ_rel, n_tick·tick/price). There is a **hard ceiling**:
  τ_agree ≤ ln(1.05).
  - A date whose documented precision cannot achieve the ceiling at that price
    level is `RAW_AMBIGUOUS` (fail closed).
- **`RAW_AMBIGUOUS`:** anything between the two zones. It is never agreement and
  never contradiction.
- **The ratio zones follow the same form:** `RATIO_AGREE` if ℓr(ρ_B, ρ_T) ≤
  τ_ratio (ceiling ln(1.05)); `RATIO_CONTRADICT` outside [0.8, 1.25].

**Parameters still unfrozen.** They are frozen after vendor documentation review
but **before any specimen retrieval**, from documented precision conventions only
and never from observed specimen differences:
- τ_rel, n_tick and the tick schedule;
- τ_ratio;
- τ_adj;
- the alignment margin μ_align;
- the stale-detection field and rule (§5).

Each has the ceiling or form stated above.

## 5. Temporal window (P5)

The windows are frozen, relative to the frozen trusted NYSE session calendar
(`gate6_audit.default_calendar`):
- **Price window:** trusted sessions from **p − 10 to t + 10**. This covers the
  ±5-session alignment search with context on both sides, the v1 ±5-session
  misdated-split horizon, and 5-session descriptive persistence.
- **CA window:** calendar dates **[p − 30 days, t + 30 days]**. This matches the
  v1 corporate-action fact window.
- **What the windows separate:**
  - a **one-day bad print** (`RAW_CONTRADICT` at t, or at p, with agreement on
    neighbours);
  - **persistent repricing** (agreement throughout);
  - a **split or adjustment discontinuity** (`ADJ_*_SIGNATURE`);
  - a **stale quote** (`OBS_STALE`);
  - a **date offset** (`DATE_OFFSET(ℓ)`).
- **`OBS_STALE`:** the source marks the observation as carried forward or
  non-traded, or its documented volume/trade-count field is zero, on p or t. The
  field choice is frozen from documentation (§4).
- **Clipping:** windows are clipped at 2023-06-30 and at the sealed key's
  D4-assigned interval. Clipping is recorded.

## 6. Identity (P6)

**Mapping rule.** Mapping is mechanical and fails closed. The sealed input is
`(security_key, ticker, D4-assigned interval, p, t)`. The instrument type comes
from the sealed Dataset-v1 security metadata; where it is not recorded, it is
`UNKNOWN`.

`IDENTITY_CONFIRMED` requires all of:
1. exactly one independent-source instrument whose **dated symbol history**
   includes the sealed ticker on **every** session of the price window used for
   C3 and C5;
2. its listing interval overlaps the sealed interval on those sessions;
3. its instrument type equals the sealed type, from {common/ordinary, ADR/ADS,
   preferred, warrant, right, unit, other}, and its share class (a class suffix)
   matches;
4. a type of `UNKNOWN` on either side gives `INSTRUMENT_TYPE_UNVERIFIABLE`.

A ticker match alone never establishes continuity. Current identity never
certifies historical identity.

**Fail-closed identity states.** Each gives `UNRESOLVED(<state>)` and is never an
error.

| State | Trigger |
|---|---|
| `IDENTITY_NOT_FOUND` | no instrument carries the ticker on those sessions |
| `IDENTITY_AMBIGUOUS` | more than one instrument qualifies (multiple securities, class shares) |
| `IDENTITY_RENAMED_UNVERIFIED` | the ticker belongs to the instrument on only some sessions (a rename or reassignment inside the window) |
| `INSTRUMENT_TYPE_MISMATCH` | the type or class differs (ADR vs common; class A vs B; warrant, right or unit vs the underlying) |
| `INSTRUMENT_TYPE_UNVERIFIABLE` | either side's type is unknown |
| `IDENTITY_INTERVAL_MISMATCH` | there is no overlap with the sealed interval |

**The CHEKZ-style case.** For a sealed warrant, right or unit instrument, only the
identical warrant, right or unit instrument may be compared. An observation of the
same issuer's common shares, or any other instrument, is `INSTRUMENT_TYPE_MISMATCH`
and is never compared. Where the v1 specimen's final outcome is
`UNRESOLVED(REVIEWER_DISAGREEMENT)` with an extractor-side DATA_ERROR outcome,
matrix row M10 keeps it UNRESOLVED regardless. Its v1 result is unchanged.

**Units.** The source must be documented as USD and per share of that instrument.
Anything else, including ADR-ratio changes presented as price changes, gives
`UNIT_MISMATCH` and is unresolved.

## 7. Source independence (P7)

Grades are assessed **per source and per field family** (price vs corporate
action) from provenance documentation. They are assessed *before* any specimen
retrieval and applied uniformly to all specimens.

| Grade | Meaning | Effect |
|---|---|---|
| `IND-A` | the field is collected from exchange-direct or consolidated-tape official closes (price), or from primary announcements (CA), and processed by a pipeline documented as not sourced from Tiingo or from Tiingo's own upstream redistributor | may corroborate (C2) |
| `IND-B` | shares a documented common vendor aggregator or redistributor with Tiingo for that field | **cannot corroborate**. States are reported descriptively only; the field counts as unavailable for resolution |
| `IND-U` | provenance undocumented, or insufficient to exclude common redistribution | treated as `IND-B` |

**Notes:**
- **Shared exchange closes are not common dependence.** Both sources carrying the
  same exchange official close is the truth object itself (§1). Independence
  concerns vendor *processing* and redistribution, which is where the audited
  errors arise.
- **Perfect independence cannot always be established.** The grade records what
  documentation supports, and uncertainty resolves to `IND-U`. No resolution
  flows from an uncertain grade.
- **Source-level gate:** if the price or CA field family is not `IND-A`, v2B has
  no resolving power. The run must stop **before** specimen retrieval (§10).

## 8. Vendor revision and vintage (P8)

- **What v2B can certify:** only that, as of the recorded retrieval time R and
  the recorded dataset version, the independent source's *current* historical
  record agrees or disagrees with the sealed Tiingo/D3 record under this
  protocol. It is **retrospective data-quality evidence** about historical truth.
- **What v2B cannot certify:**
  - that either record was knowable at any formation date;
  - point-in-time or vintage properties of either vendor;
  - that the second source's history is unrevised;
  - any Dataset-v1 PIT, vintage or identity-continuity claim.
- **No upgrade:** Dataset-v1 PIT, knowledge-date and NOT CERTIFIED boundaries are
  unchanged by any v2B result.
- **Revision handling:** if the source exposes revision or correction flags,
  record them.
  - A flag touching p or t gives `REVISION_AFFECTS_PT`, which is unresolved.
  - A flag touching other window dates gives `REVISION_ELSEWHERE`, which is
    descriptive only.
- **Retrieval sealing:** raw responses are stored verbatim with SHA-256, R, the
  dataset version and the request parameters, as in the v1 E2 sealing. They are
  never committed to Git.

## 9. Error semantics (P9)

**A vendor contradiction alone never means Tiingo is wrong.** A second source
can be the wrong one.

| v2B outcome | Required evidence |
|---|---|
| `GENUINE_MOVE_DOCUMENTED` | matrix rows M6 or M7 (v1 SEC basis), or M8 or M9 (all of C1–C7) |
| `CERTIFIED_FIELD_OMISSION` | **primary** confirmation. Either it is carried from a v1 engine outcome, or a pre-frozen primary-evidence step, separately authorized and applied uniformly to every `ERROR_SIGNATURE_CA` specimen, confirms the action (ratio in the v1 band, date in (p, t]). An independent CA record alone is not enough |
| `PRICE_OR_IDENTITY_ERROR` | **two mutually independent confirmations** against Tiingo. The first is `RAW_CONTRADICT` or `DATE_OFFSET` from an `IND-A` source. The second is either primary evidence (a v1 `STATED_CLOSE` or `TICKER_REASSIGNMENT` fact, already an engine outcome), or a pre-frozen second independent source applied uniformly to every `ERROR_SIGNATURE_PRICE` specimen. One source's contradiction alone is not enough |
| `UNRESOLVED` | everything else, including the error signatures |

Because **one DATA_ERROR gives HOLD — REMEDIATION REQUIRED**, error outcomes
require primary or doubly independent confirmation. Unconfirmed error signatures
are **not** silently unresolved: they block PASS (§12).

## 10. Missing second-source data (P10)

**Source-level failures** abort v2B before any specimen retrieval, with no
fallback vendor:
- no raw prices;
- undocumented adjustment semantics;
- price or CA independence below `IND-A`;
- licensing that forbids sealed local retention;
- no dated identity metadata.

**Specimen-level failures:**

| Situation | v2B result |
|---|---|
| security absent (`OBS_MISSING_SECURITY`) | UNRESOLVED(`OBS_MISSING_SECURITY`); v1 GENUINE is retained (M7) |
| date absent (`OBS_MISSING_DATE`) at p or t | UNRESOLVED(`OBS_MISSING_DATE`); v1 GENUINE is retained |
| any §6 identity failure | UNRESOLVED(<identity state>); v1 GENUINE is retained |
| no raw value for that observation | UNRESOLVED(`OBS_MISSING_DATE`) |
| adjustment unavailable for the specimen | C4 fails, so UNRESOLVED(`ADJUSTMENT_UNAVAILABLE`) |
| `RAW_AMBIGUOUS`, `ALIGNMENT_AMBIGUOUS`, `OBS_STALE`, `UNIT_MISMATCH`, `REVISION_AFFECTS_PT` | UNRESOLVED(<state>); v1 GENUINE is retained |

- **One source only:** a failed specimen is **never** routed to another source.
  The only additional sources allowed are the pre-frozen, uniformly applied
  confirmation steps of §9. They are used only for error confirmation, never to
  turn an unavailable case into a resolved one.
- **Missing data stays visible:** every missing or ambiguous state is counted and
  reported.
- **v1 primary evidence is never demoted:** a v1 GENUINE stays GENUINE when
  secondary data are missing, but not when they carry an error signature (M3,
  M4).

## 11. Blindness and completeness (P11)

1. **Vendor selection** uses only the §13 checklist and documentation. There are
   no trial queries on any specimen security or date.
2. **Before retrieval:** the full protocol, parameters (§4), identity procedure,
   independence grades, retrieval plan (all 180 specimens, a fixed order, the
   request cap) and a hash of the retrieval plan are committed.
3. **The comparison engine** is mechanical code, frozen and tested on synthetic
   fixtures before any retrieval. Identity mapping is mechanical as well.
4. **Any residual human or agent step** (for example, documentation-derived
   instrument-type review) sees one specimen at a time. It sees only that
   specimen's identity inputs. It never sees ρ, the v1 or v2B outcome, other
   specimens, aggregate counts, the resolved total, or "how many are needed".
5. **Complete processing:** all 180 specimens are retrieved and classified
   regardless of interim results. The decision function refuses to run unless
   all 180 have a terminal v2B state. There is no early stopping at 59 or at
   any other count.
6. **Determinism:** the downstream steps are re-run under a different TZ/locale
   with byte-identical output, as in v1 E3.

## 12. v2B decision (P12)

The original hierarchy and thresholds are retained:
1. Any v2B DATA_ERROR outcome gives **HOLD — REMEDIATION REQUIRED**.
2. Otherwise, S1 resolved < 59, or any S1 band resolved < 15, gives
   **INCONCLUSIVE**.
3. Otherwise, **any `ERROR_SIGNATURE_PRICE` or `ERROR_SIGNATURE_CA` specimen
   (any stratum)** gives **INCONCLUSIVE**.
4. Otherwise, **PASS**.

- **Why step 3 exists:** v2B's evidence is asymmetric. It can confirm genuine
  moves with secondary evidence but, by §9, cannot confirm errors without primary
  or doubly independent evidence. Without step 3, an audit could reach PASS while
  unconfirmed error signatures sit among the unresolved. The guard is **stricter**
  than v1, and it follows from the new evidence class, not from v1's counts.
- **No other change:** thresholds are not changed to compensate for v1.
- **Reporting:** the v2B decision is reported next to the v1 decision, with
  resolved counts split by basis.

## 13. Vendor requirements checklist (P13; governs later vendor selection)

**MUST HAVE** (any failure disqualifies before retrieval):
1. Daily raw (unadjusted) official closes for US-listed securities over
   2014-12 to 2023-07, **including delisted, renamed and reassigned** securities.
2. A separate adjustment series (adjusted or total-return close) **and**
   corporate-action records (split ratio and ex-date; cash amount and ex-date),
   with documented semantics.
3. A dated symbol history and instrument identity: instrument type, share class,
   listing interval, and a persistent identifier independent of ticker.
4. Provenance documentation sufficient to grade price and CA fields `IND-A`
   relative to Tiingo (§7).
5. Documented currency and unit (USD, per share) and documented price precision
   or rounding, needed to freeze τ.
6. Reproducible programmatic or bulk retrieval, with raw responses preservable
   verbatim and hashable, and a recordable dataset version or retrieval
   timestamp.
7. Licensing that permits research use and sealed local retention of raw
   responses for audit reproduction, without redistribution.
8. Access within a pre-frozen request cap and throttle, with no need for
   discretionary per-specimen manual steps.
9. Queries that do not require disclosing aggregate audit results.

**SHOULD HAVE:**
1. Volume or trade-count, or an explicit stale / non-traded flag.
2. A revision or correction history, or versioned historical datasets.
3. The listing venue per session, and a documented official-close definition
   (including busted-trade handling).
4. Cross-reference identifiers (for example CUSIP or FIGI-type) with validity
   dates.
5. Bulk historical download (a stable snapshot).

**NICE TO HAVE:**
1. Exchange-direct official closes, rather than consolidated-feed closes.
2. Point-in-time or vintage history.
3. Per-record provenance on corporate-action entries.
4. Intraday data to diagnose one-day prints (descriptive only).

## 14. Feasibility bound (P14; arithmetic only)

The inputs are the v2 diagnostic's aggregate counts only.

- **Current S1:** 30 resolved of 120. The minimum is 59 overall and 15 per band.
  At least **30 more** are required: B1 +7, B2 +5, B3 +8, B4 +10.
- **Addressable in principle:** the unresolved S1 cases whose failure mode
  independent market-price evidence could address under matrix rows M8 and M9:
  - NO_EXPLANATORY_FILING 51;
  - NO_FILINGS_IN_WINDOW 6;
  - AMBIGUOUS_ISSUER_MAPPING 5 (via direct identity, `…_NO_PRIMARY` basis);
  - REVIEWER_DISAGREEMENT among non-error outcomes 28 (same-event label 12,
    existence 13, date 1, multiple 2).
  - None of S1's disagreements involves an extractor-side DATA_ERROR outcome.
    The only such case in the specimen is in S2 (M10).
- **Upper bound:** **90** addressable S1 cases, by band B1 22, B2 20, B3 23 and
  B4 25. The maximum S1 resolved is 120, with bands at 30 each.
- **Excluding disagreement cases:** 62 addressable (B1 12, B2 10, B3 19, B4 21);
  maximum 92, bands 20 / 20 / 26 / 26.
- **Other strata:** S2 has 39 unresolved, of which 38 are addressable (the M10
  case excluded). S3 has 20 unresolved, all addressable.

**Arithmetic feasibility is not expected success.** Every addressable case can
still fail:
- identity (§6), coverage of delisted small-cap securities, stale data, or
  ambiguity;
- an error signature, which also blocks PASS under §12 step 3;
- a source-level independence gate (§7), which would stop v2B entirely.

No probability of passing is claimed.

## Access record

- Network calls, provider calls and reserve reads: 0.
- Vendor searched, named or selected: NO. New evidence accessed: NO.
- Specimen changed: NO. v1 outcome changed: NO.
- Knowledge-PIT append: NO.
- E3 scratch provenance (`worktrees/g6e3-worker-out`, `worktrees/g6e3-shared`)
  retained, unmodified.
