# Discovery Dataset v1.1 — Gate-6 v2B Commercial Confirmation Round 1: Tiingo Lineage (DATA-FREE)

- **Status:** a record of the Round-1 incumbent-lineage inquiry and Tiingo's reply.
  It was done under the user's "G6-v2B COMMERCIAL CONFIRMATION — ROUND 1, TIINGO
  LINEAGE ONLY" authorization.
- **This document qualifies no candidate**, changes no M4/M5 cell, and selects no
  vendor. It establishes only the Tiingo side of a future independence comparison.
- **Frozen authority (unchanged):**
  - evidence protocol `f04d2253747e2b9fdebad3681ac93832b3d452d5463c009d17c5a16607b36c27`;
  - addendum `f88404d9a2553f37d5bc348762303ed9e78a369801facc0351f8948a0172b54f`;
  - vendor selection `625bd220795cb364120110cb817af0458f4669a49d1d748480632b19f2c6a78b`;
  - Tushare supplement `7d52d4af9e45f4c4797262847d056c8b9003df8a8bd830df200ea611c0a70a2a`.

## 1. Contact provenance

| Item | Record |
|---|---|
| Channel | email to `support@tiingo.com`, Tiingo's official support address (Tiingo KB "Contact Support") |
| Sent by | the user, manually. This session cannot send email, so the prepared text was handed to the user |
| Sent | 2026-09-30, about 22:36 PDT, as reported by the user |
| Inquiry text | T1–T4, prepared in-session. SHA-256 of the exact prepared text: `5a6177612e78a1c55873808ce60bd8882948a47b8884edcb1014ca03037b08f7` |
| Inquiry disclosed | no specimen ticker, date, price or corporate action; no Gate-6 outcome, count, threshold or admission status; no candidate vendor; no local artifact. The purpose was described generically (research / audit provenance and methodology) |
| Reply received | by the user; pasted into the session on 2026-10-01 (about 09:57 PDT) |
| Sender role | Tiingo **Account Executive** (named individual; the signature is not reproduced here) |
| Message / thread identifier | not supplied |
| Original correspondence | kept in the user's mailbox (authoritative). A verbatim provenance copy is held outside Git, SHA-256 `fa4fe4e12616f7e2539b1259ac809b8f9d3f876e325b65c0b34e715e44196960` |
| Follow-up sent | **none.** A follow-up needs separate authorization |

## 2. Substance of the reply (close paraphrase; key phrases quoted)

**T1, historical price sources:**
- "each Tiingo End-of-Day (EOD) data point is constructed by combining,
  cross-checking, and error-filtering across 3 to 7 institutional enterprise market
  data providers, direct exchange feeds, and consolidated tape (SIP) inputs."
- "Specific commercial provider contracts are confidential."
- "[the] core multi-source cross-validation framework has been maintained
  continuously since 2014, with redundancy layers and additional institutional
  feeds added over time".
- The reply points to a Tiingo engineering blog post on validation methodology. It
  was **not** read in this round.

**T2, corporate-action sources:**
- Ingested "through a combination of direct primary regulatory filings (SEC
  filings/EDGAR), direct exchange distribution notices, and specialized
  institutional corporate action feeds".
- "Multi-vendor verification" cross-checks rates, dates and split ratios "against
  primary issuer disclosures".
- The reply does not specifically address whether this sourcing changed over
  2014–2023.

**T3, close definition:**
- Raw close = "the official primary-exchange closing auction prints disseminated
  across the consolidated tape (CTA/UTP) for the regular trading session". Volume is
  consolidated cross-exchange volume.
- Fallback: "When a primary exchange closing auction does not occur or is halted,
  standard consolidated tape (SIP) fallback rules apply (typically using the final
  consolidated regular-way trade executed prior to 4:00 PM ET)."
- "We do not fabricate artificial closing trades or substitute bid/ask quotes into
  the raw EOD close field."
- **Not answered:** whether fallback observations are flagged. Carry-forward of a
  prior session's price is not explicitly addressed beyond the no-fabrication
  statement.

**T4, corrections and transformations:**
- "extensive proprietary outlier filtering, cross-source reconciliation, and
  corporate action adjustment logic before values are published".
- Adjusted fields are recalculated historically, while "raw OHLCV fields remain
  strictly unadjusted".
- Nightly reconciliation captures busts, late prints and corrections through about
  8:00 PM ET.
- "Our EOD endpoints return today's currently corrected historical series."
- Internal revision snapshots exist, but "an auditable point-in-time correction
  vintage trail … is not exposed via the API today" (stated as on a late-Q4
  roadmap).
- Ticker / security-mapping corrections are **not addressed**.

## 3. T1–T4 documentation states

| Question | State | Basis |
|---|---|---|
| T1 price lineage | **PARTIALLY_DOCUMENTED** | source *categories* given (3–7 institutional commercial providers, direct exchange feeds, SIP), with framework continuity since 2014. **Providers confidential**; which inputs feed which observation is not disclosed |
| T2 CA lineage | **PARTIALLY_DOCUMENTED** | categories given (SEC/EDGAR, exchange notices, institutional CA feeds, multi-vendor verification). Providers unnamed; 2014–2023 change not addressed |
| T3 close definition | **PARTIALLY_DOCUMENTED** | definition and fallback documented (official primary-exchange closing auction print; SIP last regular-way trade fallback; no bid/ask substitution). Fallback flagging not answered |
| T4 corrections / transformations | **PARTIALLY_DOCUMENTED** | correction, reconciliation and revision behaviour documented, and the lack of an exposed vintage trail stated. Mapping corrections not addressed |

These states do **not** establish candidate IND-A (addendum A4).

## 4. Implications for later rounds (observations, not determinations)

1. **The price family remains IND-U on the Tiingo side.**
   - Tiingo's observations combine 3–7 confidential "institutional enterprise market
     data providers".
   - Without the provider identities, no candidate that is itself an institutional
     market-data provider can be shown **not** to be one of Tiingo's inputs.
   - Under addendum A4 (IND-U treated as IND-B), the frozen protocol cannot reach
     IND-A for any such candidate's price family on the current disclosure.
   - Tiingo's multi-source cross-checking may dilute dependence on any single input,
     but the frozen protocol has no rule crediting dilution. Inferring independence
     from it would weaken A4, which is forbidden.
2. **The CA family remains IND-U on the Tiingo side** for the same reason
   (unnamed institutional CA feeds). Tiingo's use of SEC/EDGAR primary filings is
   use of the **underlying primary truth**, not a shared intermediate record (A4).
   It does not affect the independence of the Gate-6 v1 SEC evidence. The v1 result
   is unchanged.
3. **Close semantics:** Tiingo's stated close (official primary-exchange auction
   print, with a SIP last-trade fallback) is broadly aligned with the v2B truth
   object (parent §1, the official listing-venue close).
   - Fallback days are not flagged, so on those days the definitions may differ.
   - This is relevant only to future tolerance design, and is not decided here.
4. **Vintage:** Tiingo serves a currently corrected history, with no exposed
   point-in-time revision trail. This is consistent with the existing Dataset-v1
   nonclaim "vendor revision history (raw values are as served at C)". No Dataset-v1
   claim changes.
5. **Consequence for v2B:**
   - Unless a later, separately authorized round obtains disclosure of provider
     identities (from Tiingo, or by a candidate demonstrating it is not a Tiingo
     input), M4 and M5 cannot move to PASS for LSEG-DSS or FACTSET-GP.
   - The frozen fail-closed outcome would then be **NO QUALIFYING SOURCE**.
   - No such follow-up is sent or proposed as authorized here.

## 5. Unchanged state

- **Vendor matrix:**
  - QUALIFIED: none.
  - CONDITIONALLY QUALIFIABLE: LSEG-DSS, FACTSET-GP.
  - NOT CURRENTLY QUALIFIED: BBG-DL.
  - DISQUALIFIED: CRSP-CIZ, TUSHARE-OFFICIAL, TUSHARE-PROXY.
- **G6-v2B:** OPEN. Gate-6 v1: INCONCLUSIVE. Gate-6 data quality: NOT_ASSESSED.
- **Datasets:** Dataset v1.2 (P-Q) is frozen, with implementation not started.
  Dataset v1.1 admission: HOLD.

## Access record

- Contacts: one inquiry (sent by the user), one reply received. Follow-ups: 0.
- Provider or market-data queries: 0. Second-source evidence accessed: NO.
- Network calls in this step: 0. The linked blog post was not read.
- Specimen information disclosed: NO. Candidate vendors disclosed: NO.
- Protocol, addendum and vendor matrix changed: NO.
- Knowledge-PIT append: NO.
- E3 scratch provenance retained.
