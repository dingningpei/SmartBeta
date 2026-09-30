# Discovery Dataset v1.1 — Gate-6 v2 Diagnostic and Candidate Designs (DATA-FREE)

- **Status:** a retrospective diagnostic plus design options. It **chooses and
  executes nothing**, and no new evidence was accessed. It was written under the
  user's "G6-v2 DIAGNOSTIC / DESIGN ONLY" instruction of 2026-09-30, on master
  `a91628e`.
- **Sealed v1 audit (unchanged):**
  - `g6_id` `g6-2264dbcac2a09a3d`;
  - population `d2153329…2783` (17,469 / 2,674);
  - specimen `c0c15d20…3b1e` (180);
  - fetch plan `98aa5f81…ca66`;
  - evidence set `c3d62453…e2c6`;
  - result document `a91628e`.
- **V1 outcome and decision (unchanged):** 31 GENUINE_MOVE_DOCUMENTED, 149
  UNRESOLVED, 0 data errors; S1 resolved 30 of 120. Decision: **INCONCLUSIVE**.
- **Inputs used:** only the local v1 artifacts (`outcomes.parquet`,
  `specimen.parquet` and the two sealed fact files in the audit directory), the
  frozen engine predicates, and committed authority.
- **What this document contains:** aggregate counts only. No specimen keys,
  dates, prices, ratios or filing text.
- **Retained provenance:** the E3 scratch directories `worktrees/g6e3-worker-out`
  and `worktrees/g6e3-shared` are kept unmodified.

## D1 — The per-band threshold (15 vs 16)

**Frozen authority:** spec `worker_tasks/dataset_v1/task-g6-specimen-audit.md`,
commit `05ef922` (SHA-256 `1dc4c7e0…e7e2`), §9 item 2:

> for any S1 band, r_b < min(16, ⌈n_b/2⌉), because below 16 it loses 80%
> detection of a 10%-prevalence mechanism within that band (1 − 0.9¹⁶ = 0.815).

- **Operator:** strict `<`.
- **Operative value:** with n_b = 30 (every S1 band of the sealed specimen),
  min(16, 15) = **15**. So INCONCLUSIVE if any band has fewer than 15 resolved.
- **Implementation:** `gate6_audit.decide_gate6` evaluates
  `resolved < min(16, math.ceil(n_b / 2))`, exactly the frozen formula.
- **Tests:**
  - `test_49` exercises the 15/16 boundary with bands larger than 32;
  - `test_50` asserts PASS at exactly 15 resolved in three bands of 30.
- **E3 result document:** it prints the formula and the value 15. That is
  correct.
- **Where "16" came from:** paraphrases, not frozen authority. The G6-SW and G6-E3
  authorization messages said "fewer than 16 resolved in any S1 band", and the
  planner's own freeze summary said the same.
- **A text defect inside the frozen spec:** its power rationale cites 16, but for
  the frozen full-band size of 30 the ⌈n_b/2⌉ term always binds, so 16 is never
  operative. At 15 the stated detection is 1 − 0.9¹⁵ = 0.794, not 0.815. This
  does not make the rule ambiguous (the formula is explicit), but a v2 must
  restate the threshold and its rationale explicitly.

**Finding: REPORTING_ONLY_DISCREPANCY.** The implementation matches the frozen
formula (operative 15, strict `<`). The "16" appeared only in paraphrases. The
v1 decision is INCONCLUSIVE under either value (band counts 8, 10, 7 and 5;
S1 30 < 59).

## D2 — Decomposition of all 149 UNRESOLVED

### Diagnostic vocabulary for REVIEWER_DISAGREEMENT

The vocabulary was frozen before any case was inspected. File SHA-256:
`f387a377a21dc0b660b270b034c305693acfce18e0b0d09360a33db24a62e867`, frozen
2026-09-30T23:45:54Z. It was applied mechanically, in this order:

| Category | Mechanical rule |
|---|---|
| SECURITY_IDENTITY_DISAGREEMENT | one side PRICE_OR_IDENTITY_ERROR(IDENTITY_MISMATCH) |
| VALUE_OR_MAGNITUDE_DISAGREEMENT | one side PRICE_OR_IDENTITY_ERROR(PRICE_CONTRADICTED), or one side a corporate-action outcome and the other not |
| SAME_EVENT_DIFFERENT_TYPE_LABEL | the resolved side's deciding MATERIAL_EVENT and one of the other side's share an accession or event date, both are in W, and their types differ. Sub-variants: BOTH_LISTED; LISTED_VS_INELIGIBLE (the other side's type is unlisted or direction-disallowed) |
| EVENT_DATE_DISAGREEMENT | same document and type, but the other side's fact is not in W |
| MULTIPLE_DIFFERENCES | same document, but type differs and the fact is not in W |
| DIFFERENT_EVENT_SELECTED | both GENUINE, different types, no shared document or date |
| EVENT_EXISTENCE_DISAGREEMENT | one side GENUINE; the other has no MATERIAL_EVENT from the deciding document or date |
| DOCUMENT_INTERPRETATION_DISAGREEMENT | both UNRESOLVED with different reasons |
| OTHER_UNCLASSIFIABLE | anything else |

### Full decomposition (180 specimens)

| Bucket | All | S1 | S2 | S3 |
|---|---|---|---|---|
| RESOLVED (GENUINE_MOVE_DOCUMENTED) | 31 | 30 | 1 | 0 |
| NO_EXPLANATORY_FILING | 79 | 51 | 15 | 13 |
| RD: SAME_EVENT_DIFFERENT_TYPE_LABEL | 14 | 12 | 1 | 1 |
| RD: EVENT_EXISTENCE_DISAGREEMENT | 13 | 13 | 0 | 0 |
| RD: EVENT_DATE_DISAGREEMENT | 2 | 1 | 1 | 0 |
| RD: MULTIPLE_DIFFERENCES | 2 | 2 | 0 | 0 |
| RD: VALUE_OR_MAGNITUDE_DISAGREEMENT | 1 | 0 | 1 | 0 |
| RD: SECURITY_IDENTITY / DIFFERENT_EVENT / DOCUMENT_INTERPRETATION / OTHER | 0 | 0 | 0 | 0 |
| NO_ISSUER_NAME | 13 | 0 | 13 | 0 |
| NO_ISSUER_MAPPING | 13 | 0 | 7 | 6 |
| NO_FILINGS_IN_WINDOW | 7 | 6 | 1 | 0 |
| AMBIGUOUS_ISSUER_MAPPING | 5 | 5 | 0 | 0 |
| **Total** | **180** | **120** | **40** | **20** |

The 32 REVIEWER_DISAGREEMENT cases break down as 14 same event with a different
type label (11 BOTH_LISTED, 3 LISTED_VS_INELIGIBLE), 13 event existence, 2 event
date, 2 multiple differences and 1 value or magnitude.

**Checking the "~11" observation:** exactly 11 disagreements are GENUINE vs
GENUINE, and all 11 fall in SAME_EVENT_DIFFERENT_TYPE_LABEL / BOTH_LISTED. With
the three LISTED_VS_INELIGIBLE cases, the same-event label class totals **14**.

**Note on the single VALUE_OR_MAGNITUDE case (S2):** the mechanical rule places
it here. Separately, the planner's extraction notes record that the price it
cites belongs to a different listed instrument of the same issuer. That is a
reviewer observation, not mechanical proof, and it changes no category or
outcome.

None of the above alters any v1 outcome.

## D3 — S1 failure map and required additional resolutions

| S1 band | Total | Resolved | Unresolved | NO_EXPL. FILING | RD: label | RD: existence | RD: date / multiple | NO_FILINGS_IN_WINDOW | AMBIGUOUS_MAPPING |
|---|---|---|---|---|---|---|---|---|---|
| B1 | 30 | 8 | 22 | 7 | 7 | 2 | 0 / 1 | 4 | 1 |
| B2 | 30 | 10 | 20 | 8 | 0 | 9 | 1 / 0 | 0 | 2 |
| B3 | 30 | 7 | 23 | 17 | 3 | 1 | 0 / 0 | 1 | 1 |
| B4 | 30 | 5 | 25 | 19 | 2 | 1 | 0 / 1 | 1 | 1 |
| **S1** | **120** | **30** | **90** | **51** | **12** | **13** | **1 / 2** | **6** | **5** |

S1 has no NO_ISSUER_NAME or NO_ISSUER_MAPPING cases. All 26 of those are in S2
and S3.

**Arithmetic under the authoritative rule (operative band threshold 15):**
- Overall: 59 − 30 = **29** more S1 resolutions needed.
- Per band: B1 +7, B2 +5, B3 +8, B4 +10, for a sum of **30**.
- Both together: at least **30** additional S1 resolutions, distributed at least
  7 / 5 / 8 / 10 by band (giving 60 ≥ 59).
- Even then, PASS additionally requires zero DATA_ERROR outcomes in any stratum.
- For reference only, the paraphrased value 16 would require 8 / 6 / 9 / 11 = 34.

This is arithmetic only. It does not select which specimens should resolve.

## D4 — Which evidence could identify the truth

Evidence classes:
- **A:** SEC-positive filings (what v1 already uses).
- **B:** an independent historical price source.
- **C:** an independent corporate-action source.
- **D:** better historical issuer or security identity evidence.
- **E:** a canonical event ontology and precedence rule.
- **F:** nothing reasonable; the case stays unresolvable.

| Bucket (all / S1) | Could identify the truth | Notes |
|---|---|---|
| NO_EXPLANATORY_FILING (79 / 51) | B, C; partly A with a wider filing window or more forms (a v1 design choice that cannot change for this lineage without a new version) | v1 cannot tell a real undisclosed move, a bad print and an unrecorded split apart. B addresses bad prints. B alone **cannot** rule out an unrecorded split, because raw prices from any vendor show the same raw jump; that needs C, or B's own split-adjusted series with known semantics. Many small-cap moves have no filed cause, so some cases are F under A alone. |
| RD: same-event type label (14 / 12) | E | Purely semantic. Both extractors cite the same document. A frozen type ontology with precedence, or an outcome that does not depend on type, removes the disagreement mechanically. |
| RD: event existence (13 / 13) | A plus an extraction-protocol change (an enumerated per-document checklist, or a pre-frozen adjudication rule); B or C as an alternative path | One extractor did not record an event the other found. This is extraction completeness, not missing evidence. |
| RD: event date / multiple (4 / 3) | E (a canonical rule for event date and timing), A | Disagreement on dating or timing relative to W. |
| RD: value / magnitude (1 / 0) | D (instrument-level identity), B | The instrument identity of a stated price. |
| NO_FILINGS_IN_WINDOW (7 / 6) | B, C, and A with a wider window | The issuer maps, but no allowlisted filing falls in the window. |
| NO_ISSUER_NAME / NO_ISSUER_MAPPING / AMBIGUOUS_ISSUER_MAPPING (31 / 5) | D for any SEC path; B and C bypass issuer mapping but need their own security-identity resolution | See D8. |

More evidence does not equal resolution. For every bucket, some cases may remain
F. B and C resolve a case only if the v2 outcome vocabulary defines what
confirmation or contradiction means (D6, D7).

## D5 — The canonical event-type issue

- **Same event, different type label:** 14 overall; 12 in S1 (B1 7, B2 0, B3 3,
  B4 2), 1 in S2, 1 in S3.
- **Upper bound if every one resolved:** S1 = 30 + 12 = **42** < 59. Bands would
  be B1 15 (meets 15), B2 10, B3 10 and B4 7 (all short).
- **An ontology alone cannot satisfy the Gate-6 thresholds**, even in the best
  case.
- **Mechanism:** the frozen engine's reason for GENUINE is the *first* qualifying
  MATERIAL_EVENT in fact-list order. So two extractors that find the same set of
  events can still disagree through ordering or labelling. A v2 ontology must
  make the reason independent of list order.

## D6 — Candidate independent price-source architecture (no vendor chosen or accessed)

**Required source properties** (to be established before any specimen value is
queried):
1. Independent of Tiingo's upstream feed, as far as can be documented. A shared
   upstream vendor (for example, the same consolidated-tape redistributor) must be
   disclosed as a dependence risk.
2. Historical daily **raw** (unadjusted) closes, plus a **separately documented**
   split-adjusted series or corporate-action file with known adjustment
   semantics.
3. Security identity: symbol plus exchange plus date validity, with a documented
   procedure for reused tickers and for warrants, units and rights.
4. Coverage over the specimen dates 2015–2023, including delisted securities.
5. Reproducible retrieval: a fixed query protocol, stored raw responses with
   hashes, vendor version or as-of date, and no discretionary manual steps.

**Comparison outcomes** (semantics only; tolerances left open):
- **CORROBORATED_RAW_MOVE:** the independent raw closes at p and t reproduce the
  Tiingo move.
- **CONTRADICTED_RAW_MOVE:** the independent raw closes show no such move (a
  candidate bad print or wrong series).
- **SPLIT_SIGNATURE_IN_INDEPENDENT_ADJUSTED:** the raw move agrees but the
  independent adjusted series shows no move, which implies a corporate action.
  This needs C or the v1 SEC path to classify.
- **INDEPENDENT_UNAVAILABLE:** no data for the security or date. The case stays
  unresolved and is reported.
- **IDENTITY_MISMATCH:** the independent symbol maps to a different instrument or
  issuer.
- **ADJUSTMENT_SEMANTIC_MISMATCH:** adjustment conventions cannot be reconciled.
- **COMMON_UPSTREAM_SUSPECTED:** the provenance suggests both vendors derive from
  one feed, so corroboration is non-independent and must be flagged.

**Decisions to freeze before any access:**
- the vendor and the exact product;
- the identity mapping procedure;
- raw versus adjusted fields;
- numeric tolerances (a relative close tolerance and a split-ratio match
  tolerance);
- how each comparison outcome maps into the v2 outcome vocabulary, and whether
  CORROBORATED_RAW_MOVE counts as "resolved" on its own or only together with
  evidence of no split;
- how contradiction interacts with DATA_ERROR and HOLD;
- request caps, retries and caching;
- the handling of unavailability;
- the dependence-risk disclosure.

## D7 — Candidate independent corporate-action architecture

- **What it can address:** unrecorded or misdated splits and cash distributions,
  and spin-offs or reorganizations (whether the event exists, its date, its
  ratio). This matters most in the extreme bands (B1, B4), where an omitted split
  is the main alternative to a genuine move.
- **What it cannot address:** whether a price move with no corporate action was
  real or a bad print (that needs B); purely semantic label disagreements (that
  needs E); and issuer-mapping failures, unless its own identity keys are
  resolved (D8).
- **Required properties:** independent of Tiingo's splitFactor and divCash
  fields; ex-date and ratio fields with documented semantics; coverage including
  delisted issuers; a reproducible query protocol.
- **Its value is mainly negative evidence:** "a credible CA source records no
  action" is what upgrades a CORROBORATED_RAW_MOVE to "genuine, not an omitted
  split".
- It remains separate from D6, so its marginal value can be judged
  independently.

## D8 — Identity-evidence option

- **Scope:** NO_ISSUER_NAME (13), NO_ISSUER_MAPPING (13) and
  AMBIGUOUS_ISSUER_MAPPING (5). Only 5 of these (all AMBIGUOUS) are in S1.
- **Evidence required:** point-in-time identity evidence dated within the
  specimen's own listing interval. Examples: historical exchange directory
  captures that name the issuer for that symbol and interval; SEC filings from
  that interval stating the trading symbol; historical CUSIP-to-issuer records
  for that interval.
- **What must not happen:**
  - current identity (today's ticker-to-CIK tables) cannot certify historical
    identity;
  - an improved mapping cannot upgrade Dataset-v1's D4 identity certification or
    the NOT CERTIFIED issuer-identity knowledge timing;
  - a mapping produced for the audit is audit evidence only.
- **Ambiguity resolution:** it must be a pre-frozen deterministic rule (for
  example, a unique CIK whose filings in the interval state the symbol). A case
  with no unique answer stays AMBIGUOUS.

## D9 — Candidate v2 architectures (not ranked; no PASS probability claimed)

All three operate on the **same 180 specimens, strata and bands**.

| | V2-A: ontology and extraction repair | V2-B: independent price evidence | V2-C: combined (A + B + targeted C / D) |
|---|---|---|---|
| Unresolved reasons addressed | RD label (14), RD date/multiple (4), RD existence (13) via checklist or adjudication | NO_EXPLANATORY_FILING, NO_FILINGS_IN_WINDOW, AMBIGUOUS mapping via symbol-level identity; potentially any unresolved case if price corroboration is a resolution path | all buckets |
| S1 cases potentially addressable | ≤ 28 (label 12 + existence 13 + date/multiple 3) | ≤ 62 (51 + 6 + 5); ≤ 90 if price resolution overrides label disagreement | ≤ 90 |
| Per band (B1 / B2 / B3 / B4) | 10 / 10 / 4 / 4 | 12 / 10 / 19 / 21 (NEF + NFW + AMB) | 22 / 20 / 23 / 25 |
| Maximum theoretical resolved S1 | 58, bands 18 / 20 / 11 / 9 | 92, bands 20 / 20 / 26 / 26 (NEF only: 81, bands 15 / 18 / 24 / 24) | 120 |
| Threshold mathematically reachable? | **No** (58 < 59; B3 and B4 fall short even at the maximum) | **Yes**, arithmetically | **Yes**, arithmetically |
| New evidence dependencies | none; the sealed evidence is re-extracted | a new price vendor (entitlement, cost, licensing) | price vendor, CA source, identity sources |
| Independence risks | re-extraction by the same actors after seeing v1 | a common upstream feed with Tiingo | as B, plus multiple sources |
| PIT implications | none new; the audit stays retrospective | retrospective; must not upgrade PIT or vendor-revision nonclaims | as B; identity evidence must not upgrade D4 claims |
| Reproducibility | high (sealed evidence) | depends on vendor versioning and stored raw responses | lowest (most sources) |
| Complexity | low to moderate (ontology, checklist, adjudication rule, engine change) | moderate (new fetcher, identity mapping, comparison semantics) | high |
| Scientific risks | ontology choices made after seeing v1 labels; extraction completeness is still judgement-based | raw corroboration does not exclude unrecorded splits unless adjusted or CA evidence is included; possible non-independence | largest degrees of freedom; highest risk of outcome-driven design |

## D10 — Safeguards against post-outcome adaptation

v2 is being designed **after** observing v1. It cannot be preregistered free of
that knowledge. The goal is to make the adaptation explicit and to remove further
outcome-driven degrees of freedom:
1. The same 180 specimens, the same S1/S2/S3 and the same bands. No replacement,
   no exclusion; the 38 E1 mapping cases stay in.
2. v1 is retained permanently. v2 results are reported alongside v1 and never
   overwrite it.
3. The whole v2 protocol is frozen and committed before any new evidence is
   accessed: sources, products, identity procedure, comparison semantics,
   tolerances, outcome vocabulary, resolution definition and decision rule.
4. The evidence-source choice is frozen before any specimen-level value is
   queried. No source shopping after seeing disagreements, and no per-specimen
   source selection: every source is queried for every in-scope specimen under
   one protocol.
5. Unavailable, failed or contradictory evidence stays visible as its own
   outcome.
6. Any change to the engine or ontology is frozen and tested before being
   applied to the sealed v1 facts or to new facts, and applies to all specimens
   uniformly.
7. If v2 re-extracts facts, it must be a blind double extraction again. The v1
   fact files are not reused as drafts.
8. A disclosure section states that the design was informed by v1's failure
   decomposition (this document), including the specific v1 observations it
   responds to.

## D11 — Threshold policy

- **Keep the operative v1 decision rule unchanged:**
  - HOLD if there is any data error;
  - INCONCLUSIVE if r₁ < min(59, ⌈n₁/2⌉) or any r_b < min(16, ⌈n_b/2⌉), which is
    operatively 15 for bands of 30;
  - otherwise PASS.
- **Why it stays:** the thresholds were justified by detection power over
  *resolved* draws. That justification does not depend on how a specimen gets
  resolved, so adding an evidence architecture does not by itself require a new
  threshold.
- **What v2 does change:** the definition of "resolved". If v2 adds
  resolution-by-price-corroboration, it must state explicitly whether a
  corroborated but causally unexplained move counts as resolved. That is a
  semantic decision justified by what evidence can support (D6), not by the
  30/120 result.
- **Any threshold change** must be justified independently of the observed
  result, and must not be more permissive. The only issue seen here is the D1
  rationale-text defect. Correcting it would at most raise the band threshold to
  16 (stricter), and it must be decided explicitly, not silently.

## D12 — What "zero confirmed errors" means

- **Observed:** 0 confirmed data errors; 149 of 180 unresolved; S1 resolved 30 of
  120.
- **Zero confirmed errors is not evidence of zero errors.** A data error can only
  be confirmed on a specimen whose evidence resolves it, and 149 did not resolve.
  Unresolved specimens may contain errors (bad prints, unrecorded splits,
  identity problems) that v1 could not detect: 79 had no explanatory filing, and
  v1 had no second price source.
- **Resolution is not random:** the 31 resolved specimens are those with filed,
  dated events. That is a selected subset (disproportionately clinical and
  regulatory news), not a random sample of suspect days.
- **The one conditional bound supported:** among the 31 resolved specimens,
  observing 0 data errors gives a Clopper–Pearson two-sided 95% upper bound of
  **11.2%** (one-sided 95%: 9.2%). For the 30 resolved S1 specimens: 11.6% and
  9.5%.
- **What even that bound is not:** it concerns the resolvable subset only, under
  v1's evidence. It does not bound errors among the unresolved specimens, the
  17,469 population, or the reserve.
- **What GENUINE_MOVE_DOCUMENTED means:** a documented event explains the move.
  It does not verify Tiingo's price level.
- **Scope:** nothing here certifies overall price correctness, corporate-action
  completeness or identity continuity. Gate 6 remains NOT_ASSESSED /
  INCONCLUSIVE, and Dataset-v1 development admission remains on HOLD.

## Access record

- Network calls, provider calls and reserve reads: 0.
- New evidence accessed: NO. Specimen changed: NO. v1 outcome changed: NO.
- Knowledge-PIT append: NO.
- Scratch provenance retained: YES.
