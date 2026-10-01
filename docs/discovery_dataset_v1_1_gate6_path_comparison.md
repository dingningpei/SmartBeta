# Discovery Dataset v1.1 — Gate-6 Path Comparison: v2B Independent Evidence vs. Research-Layer Quarantine (DATA-FREE; DESIGN ONLY)

- **Status:** a comparison and evaluation document, requested by the user on
  2026-09-30. Master at start: `663e445`. **Nothing here is adopted.**
  - No contract, protocol or artifact is changed, and no audit is re-run.
  - Any change to contract §8 or §16 is a methodology-freeze barrier that needs the
    user's explicit authorization.
- **Current state:** Gate-6 v1 is INCONCLUSIVE (a91628e), which maps to gate 6
  NOT_ASSESSED and still blocking (clarification §8). Dataset v1 development
  admission: HOLD.
  - v2B protocol: f04d2253… plus addendum f88404d9….
  - Vendor research (8bcc151 plus 663e445): no QUALIFIED source. LSEG-DSS and
    FACTSET-GP are conditionally qualifiable, pending Tiingo lineage (T1–T4; the
    inquiry was sent by the user and is unanswered).
- **Inputs:**
  - committed authority;
  - aggregate counts computed read-only from the sealed **development** derived
    artifacts: QC `q-5d1028655f8c82e1`, universe `u-6150bc6ac2866b7f`, returns
    `r-34565f074d64ccda`.
  - No raw file, no reserve file, and no horizon crossing the reserve was used. No
    factor or performance statistic was computed (contract §16).

## 1. Authority that constrains both paths

- **Contract §8:** "A day with |raw return| > 50% and no split or dividend event is
  flagged CORP_ACTION_SUSPECT. Flagged days are retained, counted and
  specimen-audited, never silently dropped."
- **Contract §16:** gate 6 is the "CORP_ACTION_SUSPECT list with a specimen audit".
  "All of these must PASS before Dataset v1 is admitted for Discovery Campaign 1."
- **Clarification §8:** INCONCLUSIVE gives NOT_ASSESSED, still blocking. Any
  follow-up audit is a new, separately frozen version. No resampling.

**Consequence:** under the current contract, the only route to admission is a
Gate-6 PASS. Any route that admits Dataset v1 without one, including a quarantine,
is a **contract amendment**, not an interpretation.

## 2. New aggregate finding: where the suspect days sit

| Quantity (development only) | Count |
|---|---|
| CORP_ACTION_SUSPECT rows (whole development population, the Gate-6 v1 sampling frame) | 17,469 over 2,674 securities |
| securities ever in the research universe (top-1000 members across 89 formations) | 1,920 |
| suspect rows on those member securities (any date) | **480** over 226 securities (2.7% of the population) |
| suspect rows inside member daily-return paths (`daily_returns`, 2016-02 to 2023-06) | **110** over 72 securities (0.63%) |

**Exposure in forward labels** (formation f, horizon h = 1…63, labels whose window
(f, endpoint] contains at least one suspect day; reserve-crossing horizons
excluded):

| Tier | Labels | Affected | Share | Distinct affected securities | Formations with ≥1 affected member (h = 63) |
|---|---|---|---|---|---|
| top-200 | 1,108,800 | 621 | 0.056% | 7 of 455 | 17 of 89 |
| top-500 | 2,772,000 | 1,700 | 0.061% | 20 of 1,033 | 33 of 89 |
| top-1000 | 5,544,000 | 7,766 | 0.140% | 72 of 1,920 | 75 of 89 |

- **Exposure in trailing inputs:** member-formations with a suspect day in the 365
  calendar days before f. Top-500: 339 of 45,000 (0.75%). Top-1000: 1,037 of
  90,000 (1.15%).
- **Interpretation (descriptive, not a certification):**
  - The Gate-6 sampling frame is dominated by securities that never enter the
    research universe. About 97% of suspect rows sit on non-member securities,
    which are illiquid and excluded by the dollar-volume rule.
  - The suspect days that *can* touch Campaign-1 research are a small, enumerable
    set (480 rows; 110 in member return paths).
  - **Disclosure:** this mismatch is a property of the frozen design that existed
    before v1 ran. It is reported only now, after v1. Any design that uses it is a
    post-v1 adaptation and must say so (§6).

## 3. The candidate paths

### P-HOLD: stay at HOLD
Do nothing beyond waiting for Tiingo. Admission stays blocked indefinitely unless
P-v2B succeeds.

### P-v2B: independent-price-evidence audit (as frozen)
The same 180 specimens, frozen semantics, and the same thresholds (S1 ≥ 59, each
band ≥ 15). Unconfirmed error signatures block PASS.

### P-Q: research-layer quarantine plus an explicit Gate-6 nonclaim (needs a contract amendment)
**The candidate amendment shape** (illustrative; would be frozen separately):
1. Suspect days stay **retained, counted and visible** in every artifact. Nothing
   is deleted.
2. In the research layer, a return session flagged CORP_ACTION_SUSPECT is treated
   as **invalid for return use**:
   - a label whose window contains one gets a new explicit state (for example
     `SUSPECT_SESSION_IN_PATH`), never silently dropped (consistent with §9.2's
     no-silent-disappearance principle);
   - a factor input whose window contains one is flagged the same way.
3. Gate 6 for development admission is recorded as **"v1 INCONCLUSIVE; suspect
   sessions quarantined in research use"**, with an explicit, bounded nonclaim. It
   is **never** recorded as PASS.
4. **Mandatory pre-registered sensitivity:** every Campaign-1 result is reported
   both with quarantine and with the suspect sessions included (certified path).
   The quarantine choice cannot be made after seeing results.

**Optional add-on P-Q+C: a universe-scoped census audit.** A new, separately frozen
audit version over **all** suspect rows on member securities (480), or those in
member paths (110). It is a census rather than a sample, using the same SEC
evidence engine. It is a new lineage. It does not replace or overwrite v1.

## 4. Comparison

| Dimension | P-HOLD | P-v2B | P-Q (± C) |
|---|---|---|---|
| Contract change needed | none | none (a new audit version is already permitted) | **yes:** §8 wording ("specimen-audited") and §16 "all must PASS" for gate 6 |
| What it would support | nothing new | a Gate-6 PASS under the frozen rule, if attained | admission with gate 6 **explicitly not passed**, and suspect sessions excluded from research returns, with sensitivity |
| Detects data errors among suspect days? | no | yes, for the 180-specimen sample (within protocol limits) | no for the quarantine itself; it removes their *influence*. +C enumerates the universe-relevant set |
| Protects research from suspect-day errors? | n/a (blocked) | indirectly (sample-based inference over all 17,469) | **directly:** affected labels and inputs are flagged and excluded (≈0.06% of top-500 labels; ≈0.75% of top-500 trailing inputs) |
| Errors outside the suspect list (\|ret\| ≤ 50%, or omitted events with an event recorded elsewhere) | not addressed | not addressed (outside Gate-6 scope) | not addressed (same) |
| External dependencies | Tiingo reply | Tiingo T1–T4; a vendor's lineage confirmation; an institutional licence (10×-individual or enterprise terms; no public price); a retention licence | none for P-Q. +C: none new (sealed SEC method) |
| Feasibility risk | — | high: may end in NO QUALIFYING SOURCE (IND-U fail-closed); cost may be prohibitive for a personal project | low: an internal amendment plus implementation |
| Time | indefinite | long (several confirmation rounds, licensing, implementation, retrieval) | short to medium |
| Bias risk | — | low (sample unchanged; frozen semantics) | **selection risk:** quarantine removes extreme-move sessions, which are disproportionately genuine biotech or event moves. Small in magnitude (counts above), and mitigated by the mandatory sensitivity |
| Post-outcome adaptation risk | none | disclosed (designed after v1); frozen before evidence | **higher:** proposed after an INCONCLUSIVE result, so it looks like moving the goalposts. Mitigated only by (a) never calling it a PASS, (b) a stricter research treatment (exclusion, not acceptance), (c) the frozen sensitivity, and (d) explicit disclosure |
| Effect on v1 | retained | retained; v2B alongside | retained; never overwritten |
| PIT / knowledge claims | unchanged | unchanged (retrospective; no PIT upgrade) | unchanged |

## 5. What each path can never claim

- **P-v2B:** even a PASS supports only the clarification §10 claim, about a
  specimen of the full population. It does not claim every suspect observation is
  correct, and it does not certify Tiingo's price level.
- **P-Q:**
  - It never claims Gate 6 passed, and never that suspect days are correct.
  - It claims only that Campaign-1 research returns do not use suspect sessions
    (with the sensitivity report showing what changes if they are used).
  - It leaves the §18 complex-corporate-action nonclaim and every other boundary
    unchanged.
- **P-Q+C:** a census result applies to the universe-relevant suspect set only, and
  under the same SEC-evidence limits that made v1 INCONCLUSIVE. It may itself
  resolve only partially.

## 6. Safeguards any P-Q amendment would need

1. A user-authorized contract amendment, frozen and committed before any
   implementation or Campaign-1 access.
2. Gate 6 recorded as v1 INCONCLUSIVE (NOT_ASSESSED) with the quarantine nonclaim,
   **never** PASS. Admission language must state the exception.
3. Quarantine applied mechanically to every flagged session. No per-security or
   per-result discretion.
4. Sensitivity (quarantined vs. included) mandatory, pre-registered, and reported
   for every Campaign-1 statistic.
5. A disclosure that the amendment was proposed after Gate-6 v1 was INCONCLUSIVE,
   citing this document and the §2 aggregate finding.
6. v1, the v2 diagnostic, the v2B protocol and the vendor research stay
   permanently. P-v2B remains available as a later, independent path to an actual
   Gate-6 PASS.
7. The reserve is untouched. Reserve suspect days stay DEFERRED (clarification
   §2). Any confirmation-stage use needs its own treatment.

## 7. Decision points for the user (not decided here)

- D-1: Remain at HOLD and continue only P-v2B (Tiingo, then vendor confirmation)?
- D-2: Authorize drafting a P-Q contract amendment (spec freeze only, no
  implementation) for review?
- D-3: If P-Q, include the +C universe-scoped census audit as a separate new audit
  version?
- D-4: Keep P-v2B open in parallel regardless (the Tiingo reply may still arrive)?

## Access record

- Network calls: 0. Provider calls: 0. Reserve reads: 0. Raw reads: 0.
- Derived development artifacts were read for aggregate counts only.
- Contract changed: NO. Protocol changed: NO. v1 outcome changed: NO.
- Knowledge-PIT append: NO.
- E3 scratch provenance retained, unmodified.
