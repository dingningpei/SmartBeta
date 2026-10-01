# Discovery Dataset v1.1 — P-Q Research-Layer Quarantine: Proposed Contract Amendment

> **DRAFT — NOT ADOPTED.**
> **NO IMPLEMENTATION AUTHORIZED.**
> This document proposes language only. The frozen contract
> `docs/discovery_dataset_v1_contract.md` (SHA-256
> `d03f35549777e5a94046f06dd0621acfc3a1dedb7a4b40adc55fdae911fa80e3`) is
> **unchanged**, and so is every artifact.

- **Requested by:** the user's "DISCOVERY DATASET v1 — P-Q CONTRACT AMENDMENT
  DRAFT" instruction of 2026-09-30 (decisions D-1 NO, D-2 YES, D-3 NO FOR NOW,
  D-4 YES), on master `ea0a763`.
- **Authority chain:**
  - path comparison `docs/discovery_dataset_v1_1_gate6_path_comparison.md`
    (`9006f0a7…8c84`);
  - Gate-6 audit result (a91628e);
  - Gate-6 clarification (cab82e0) and addendum (4695544);
  - D7b clarification (§§2–7) and D7c clarification (§§1–5), which are the
    precedents this draft follows;
  - G6-v2B protocol (`f04d2253…`) and addendum (`f88404d9…`); vendor research
    (8bcc151, 663e445).

## 1. Scope

- **What it applies to:** P-Q is a **research-consumption layer** above the
  immutable Dataset-v1 factual artifacts. It applies only to the development
  CORP_ACTION_SUSPECT condition of contract §8: 17,469 development `day_flags`
  rows over 2,674 securities (QC `q-5d1028655f8c82e1`).
- **What it does:** it produces explicit research-eligibility (quarantine) states
  and a second, precommitted evaluation estimand.
- **What it never does:** certify, repair, relabel or delete any observation.
- **Not covered:** any other gate, flag or nonclaim. TERMINATION_MISMATCH (also
  UNCERTIFIED_SEMANTICS) is **not** covered.
- **Reserve:** reserve suspect days stay DEFERRED (Gate-6 clarification §2).
  Confirmation-stage use needs its own preregistered treatment (§12).

## 2. Historical disclosure (binding text)

1. **Gate-6 v1 = INCONCLUSIVE, permanently** (a91628e). It is never to be
   restated as PASS, PASS WITH EXCLUSIONS, CONDITIONALLY PASSED, "effectively
   passed" or an immaterial failure. The 180 specimens, the sealed evidence and
   all v1 artifacts are unchanged.
2. **P-Q is a post-v1 methodological adaptation.** It was designed after Gate-6 v1
   was observed to be INCONCLUSIVE. It was not preregistered before Gate-6 v1.
3. **The research-overlap analysis was performed after v1.** The analysis
   motivating P-Q (path comparison §2) was computed **after** observing the v1
   INCONCLUSIVE result. Its figures, recorded here as **exposure diagnostics only,
   not as evidence of harmlessness**:
   - suspect rows: 17,469 over 2,674 securities;
   - on securities ever in the research universe: 480 over 226 securities (2.7%);
   - inside member daily-return paths: 110 over 72 securities;
   - affected top-500 forward labels: 1,700 of 2,772,000 (0.061%; 20
     securities), under the narrower in-path definition s ∈ (f, endpoint];
   - top-500 member-formations with a suspect day in the trailing 365 days:
     0.75%.

   These counts do not show that the suspect observations are correct, nor that
   their effect is immaterial for any factor.

## 3. Quarantine state model

### 3.1 The suspect source observation

A CORP_ACTION_SUSPECT flag on session s says that **the move from close(prev(s))
to close(s) is unexplained**. Any of these may be defective:
- close(prev(s));
- close(s);
- the corporate-action state between them.

**Proposed (fail-closed):** the quarantined **source set** for flag s is

    Q(s) = { close(prev(s)), close(s), CA-state(prev(s) → s) }

where prev and next are trusted NYSE sessions within the key's D4 interval.
Design question U1 (§14) records the narrower alternative.

### 3.2 State vocabulary (machine-readable, per cell)

| State | Meaning |
|---|---|
| `CLEAN` | a value exists and its computation consumes no element of any Q(s) |
| `QUARANTINED_CA_SUSPECT` | a **primitive** (§4.1) whose own computation directly consumes an element of some Q(s) |
| `QUARANTINE_PROPAGATED` | a **derived** value (TRI cell, factor value, label, member evaluation record) that consumes at least one `QUARANTINED_CA_SUSPECT` primitive through the dependency rules below |
| `NOT_APPLICABLE` | no value exists for an independent, already-typed reason (§10 missingness, D7c mask, identity exclusion, reserve purge, POST_TERMINAL, and so on). The existing type is kept, and quarantine never overwrites it |

**Rules:**
- **Fail-closed:** if dependency cannot be established mechanically, the cell is
  `QUARANTINE_PROPAGATED`.
- **No downgrade:** quarantine states are never downgraded to `CLEAN` by any later
  operation.
- **Separate column:** states are carried in a separate typed column or panel.
  **No numeric value is changed.**

### 3.3 Member evaluation types (adds to the D7b §3 typing)

| Type | Estimand A (QUARANTINED) | Estimand B (ORIGINAL) |
|---|---|---|
| `CA_QUARANTINED_INPUT` | the member's factor observation at f is `QUARANTINE_PROPAGATED`; return-inadmissible (typed NaN) | unchanged treatment |
| `CA_QUARANTINED_LABEL` | the member's (f, h) label is `QUARANTINE_PROPAGATED`; return-inadmissible (typed NaN) | unchanged treatment |

These types are independent of `TERMINAL_OBSERVED_PATH_INCOMPLETE`,
`OBSERVED_PATH_INCOMPLETE`, terminal affectedness and reserve purge. A member may
carry several; each is recorded and counted separately. CA quarantine is never
expressed as missingness, terminal evidence or a purge.

## 4. Factor-input propagation

### 4.1 Primitives (dated by their own session; before the §11.1 lag)

| Primitive (D7c §4) | Consumes | `QUARANTINED_CA_SUSPECT` iff |
|---|---|---|
| `daily_total_return` dated u | close(prev(u)), close(u), CA-state(prev(u) → u) | u ∈ {prev(s), s, next(s)} for some flag s. r(prev(s)) consumes close(prev(s)); r(s) consumes the whole pair and its CA state; r(next(s)) consumes close(s) |
| `dollar_volume` dated u | close(u), volume(u) | u ∈ {prev(s), s} for some flag s |
| `total_return_index` dated u | every `daily_total_return` from its segment anchor through u | the computation includes any quarantined `daily_total_return` (that is, TRI(u) for u ≥ the first quarantined return within the admitted segment). Such cells are `QUARANTINE_PROPAGATED` (§4.3) |

### 4.2 The lag and Gate 13

- **Same lag as the values:** state panels pass through the **same** §11.1
  bridge lag as the values. Row d carries the state of the primitive dated prev(d).
- **No new information channel:** no state reaches a factor row from a session
  later than its value would.
- **Look-ahead check:** the flag on s is computable at close(s). A state reaching
  factor row f derives from primitives dated ≤ f−1, plus, under the Q(s)
  endpoint rule, flags on sessions s ≤ f.
  - A flag at s = f marks close(f−1) and therefore r(f−1) on row f. That flag is
    known at close(f), which is the formation time, so no post-formation
    information is used for input quarantine.
  - It is information beyond the close(f−1) factor cutoff. It is used **only for
    eligibility typing, never as a factor value**. U1 records this.

### 4.3 Propagation through FactorSpec operations (structural dependency)

A boolean dependency panel is evaluated **alongside** the FactorSpec expression,
on the same grid:

| Operation | State propagation |
|---|---|
| input reference | the input's lagged state panel |
| `lag(x, N)` | shift the state by N rows (the same shift as the values) |
| rolling `mean/sum/std/min/max(x, N)` | quarantined if any cell in the N-row window is quarantined (`min_periods == window` already) |
| element-wise arithmetic and functions | quarantined if any operand cell is quarantined |
| cross-sectional `rank`, `winsorize`, `standardize` | a cell keeps **its own** state. Other securities' quarantined cells do **not** taint it (see §6.4 and U3 for the residual cross-sectional influence) |
| nesting | compose the rules above |

**Not permitted under P-Q:** shortening a lookback, skipping the suspect day,
substituting an observation, forward-filling or back-filling, recomputing with a
different window, or setting a factor value to NaN. Factor **values** are
identical in estimands A and B. Only the states differ.

**Dependency-exactness:**
- A factor value is quarantined only if its own computation reaches a
  quarantined primitive.
- A security's unrelated suspect day outside the expression's effective window
  never quarantines it.
- TRI is a stated exception, and conservative: because TRI(u) structurally
  contains every earlier return in its segment, a TRI-based expression is
  quarantined from the first quarantined return onward, even where algebra
  (a ratio of TRI cells) would cancel it. This over-quarantine is fail-closed and
  recorded as U2.

### 4.4 Universe membership

- **Membership is unchanged in both estimands.** D6/D7a formation membership is
  immutable, and the liquidity rule's use of raw close × volume is not altered.
- **Reported only:** the count of member-formations whose 63-session liquidity
  window contains a `QUARANTINED_CA_SUSPECT` `dollar_volume`. It is never used
  to re-form the universe (U8).

## 5. Forward-label propagation

For formation f, horizon h, member s with D7a endpoint e and observed segment
(f, e′], where e′ = min(e, `listed_end`):
- **The label consumes:** close(f) and every VALID `daily_total_return` dated in
  (f, e′], hence close(f) … close(e′) and the CA states between them.
- **The label is `QUARANTINE_PROPAGATED`** iff the path consumes an element of
  some Q(s). Equivalently, a flag s with s ∈ (f, e′] **or** prev(s) ∈ [f, e′].
  The second condition includes a flag on next(e′), which marks close(e′), the
  endpoint price.
- **Never:**
  - the suspect day is never removed with the return compounded around it;
  - the label is never shortened;
  - it is never reinterpreted as terminal, delisting or missingness;
  - the P7-B value is never altered.
- **Typing:** the quarantined label is typed `CA_QUARANTINED_LABEL` (§3.3). A label
  already `NOT_APPLICABLE` (reserve-crossing purge, empty-path identity, and so on)
  keeps its type.
- **Not an investable return:** label quarantine conditions on information
  realized **after** formation (suspect moves inside the holding path). Estimand A
  is therefore a **data-contamination sensitivity estimand, not an investable
  return estimand**. It can exclude genuine extreme outcomes. That is why it is
  never reported or used alone (§7, §8).

## 6. Portfolio, group and evaluation propagation

1. **One grouping (the D7b §4 precedent):** for each formation, universe,
   FactorSpec and `n_groups`, there is exactly **one** group assignment.
   - It comes from `portfolio_sort.assign_groups` on the winsorized factor values
     of the complete membership.
   - Those values are identical in A and B (§4.3).
   - The same assignment owns quantiles, legs, economic orientation, spreads,
     turnover and cost, affected-leg accounting and the delisting T0/TS
     machinery, in **both** estimands.
   - **No quarantined member is removed before grouping, and no quantile is
     re-formed from survivors.**
2. **Estimand A** marks `CA_QUARANTINED_INPUT` and `CA_QUARANTINED_LABEL`
   members return-inadmissible, using the existing typed-NaN `forward_return`
   representation.
   - It is permitted **only** because each such NaN is predeclared, typed,
     provenance-bound and reconciled downstream (D7b §4 standard).
   - Untyped NaN or implicit sample selection fails closed.
3. **Estimand B** applies the original Dataset-v1 / D7b treatment, unchanged.
4. **Group and portfolio returns, the long-short spread and IC / rank-IC** are
   computed by the unchanged Phase-7 engine on each estimand's admissible sample
   under the same assignment.
   - IC and rank-IC pair factor values with admissible forward returns. The
     factor values are the same in A and B, and only pair admissibility differs.
   - **Residual cross-sectional influence:** a quarantined member's factor value
     still participates in the cross-sectional rank and winsorization that place
     *other* members. P-Q does not remove this, and it is reported as the count of
     formations with at least one quarantined factor cell in the universe (U3).
5. **Costs and turnover:** computed from the single assignment. They are identical
   in A and B, except where the engine's weight treatment of NaN-return members
   differs. Any such difference must be reconciled and reported, never silently
   absorbed.
6. **Delisting interaction:** T0 and TS are evaluated **within each** estimand.
   There are four cells (A-T0, A-TS, B-T0, B-TS), all under the same assignment.
   - §9.6 DELISTING-SENSITIVE is evaluated in both A and B.
   - QUARANTINE_SENSITIVE (§8) is evaluated in both T0 and TS.
7. **Robustness metrics** (universe variants 200 and 1000, other predeclared
   robustness): each robustness run carries both estimands under the same rules.
8. **Evaluation records:** every record binds `quarantine_rule_hash` and the
   estimand label (`QUARANTINED` or `ORIGINAL`). Records missing either fail closed.

## 7. Dual-estimand requirement

Every Campaign-1 evaluation on Dataset v1 must precommit, in its hash-bound
`CampaignEvaluationDeclaration` (D7b §2), **both**:
- **A. QUARANTINED:** P-Q rules applied (§§3–6).
- **B. ORIGINAL:** the unmodified Dataset-v1 treatment.

Both use the **same** FactorSpec, sign, universe variant, formation schedule,
horizons, grouping, `n_groups`, winsorization, cost assumptions, EvaluationSpec,
temporal partitions and fold, statistical screen and its threshold, and holdout
policy.
- Neither estimand is tuned separately.
- Neither is chosen as "preferred" after outcomes are seen.
- A declaration missing either estimand gives `NOT_ASSESSED`, and the candidate
  cannot advance.

## 8. QUARANTINE_SENSITIVE rule

This rule is modelled on the frozen §9.6 rule and is evaluated for the declared
horizon, universe and fold, within each delisting scenario.

**Definitions:**
- **Q_B:** the mean over evaluated formations of the fraction of U_f that is
  `CA_QUARANTINED_*` (the universe base rate).
- **Q_S, Q_L:** the same fraction within the economic short and long legs (D7b §5
  orientation; return-inadmissible members included, as in E_S and E_L).

A result is **QUARANTINE_SENSITIVE** if **any** of these holds:
- (a) sgn(economic_A) ≠ sgn(economic_B) for the declared primary statistic
  (sgn ∈ {−1, 0, +1});
- (b) the declared development screen's verdict differs between A and B, in
  **either** direction;
- (c) Q_S > κ·Q_B or Q_L > κ·Q_B, or Q_S > 0 or Q_L > 0 when Q_B = 0. **κ is to be
  frozen before Campaign-1.** The draft proposes κ = 2, mirroring the frozen §9.6
  multiplier. Both legs are used because, unlike delisting, no genuine bound
  covers either side;
- (d) a material change in effect magnitude between A and B. **No threshold is
  proposed.** Whether to include (d), and its threshold, must be frozen separately
  before Campaign-1 and never chosen from Campaign results (U4).

**Advancement consequence (conservative):**
- A QUARANTINE_SENSITIVE result, in **any** of the four cells, **may not advance
  to confirmation under Dataset v1**. The same applies to a `NOT_ASSESSED` result.
- A not-sensitive result does **not** authorize holdout use, and does not
  establish that the suspect observations are correct or immaterial.
- Advancement additionally requires not DELISTING-SENSITIVE in both A and B.

## 9. Reporting (mandatory, side by side)

Every Campaign result reports, for the declared configuration:
- **exposure counts:**
  - suspect source observations (flags) in the evaluated domain;
  - affected securities;
  - `QUARANTINED_CA_SUSPECT` primitives by type;
  - `QUARANTINE_PROPAGATED` factor observations;
  - affected labels;
  - affected member-formations by group and leg;
  - Q_B, Q_S and Q_L;
  - formations with residual cross-sectional influence (§6.4);
  - liquidity-window exposure (§4.4);
- **every precommitted metric** for QUARANTINED and ORIGINAL, under T0 and TS;
- **flags:** QUARANTINE_SENSITIVE and DELISTING-SENSITIVE, with the triggering
  clause.

Reporting only one estimand, or only the better one, is forbidden.

## 10. Gate-6 status and admission semantics (proposed wording)

**Gate-6 status, post-amendment** (it is not a PASS):

    Gate 6:
      v1 specimen audit       = INCONCLUSIVE (a91628e; permanent)
      data-quality status     = NOT_ASSESSED (CORP_ACTION_SUSPECT data quality uncertified)
      research treatment      = CORP_ACTION_SUSPECT observations EXPLICITLY QUARANTINED
                                under P-Q (quarantine_rule_hash = <frozen at adoption>)
      G6-v2B                  = OPEN (parallel route to a genuine Gate-6 PASS)
      +C                      = DEFERRED / NOT AUTHORIZED

**Development admission wording** (only if every other §16 gate passes):

> Dataset v1 is admitted for Discovery Campaign 1 **development use only under the
> P-Q research-consumption rules**. Exception: unresolved Gate-6 data-quality
> uncertainty for CORP_ACTION_SUSPECT observations is quarantined at the
> research-consumption boundary. Gate 6 has **not** passed. This admission rests on
> **safe research consumption**, not on **data-quality certification**.

**Limits of the exception:**
- It applies only to the defined Gate-6 CORP_ACTION_SUSPECT condition.
- It is **not** a precedent for any other failed, inconclusive or not-assessed
  gate. Any such case needs its own frozen amendment.

## 11. Coexistence with G6-v2B and with +C

- **v2B is preserved.** P-Q does not cancel, alter or reinterpret the G6-v2B
  lineage: its protocol, addendum, specimens, thresholds or vendor requirements.
- **Transition rule:** a future genuine Gate-6 PASS may remove the *need* for
  quarantine **for new research runs only**, through a separately frozen transition
  rule.
- **Historical runs are kept:** Campaign evaluations run under P-Q stay
  reproducible and reportable under their original dual-estimand treatment, and
  are never re-scored retroactively.
- **+C:** DEFERRED / NOT AUTHORIZED. No new audit sampling, SEC access or
  specimen-level audit is part of this amendment.

## 12. Anti-post-selection safeguards

1. Gate-6 v1 stays permanently INCONCLUSIVE.
2. All suspect observations stay stored, counted, provenance-bearing, queryable and
   visible in QC.
3. No specimen or suspect observation is reclassified to obtain admission.
4. Quarantine is stricter than accepting the uncertain observations: it removes
   their influence on the QUARANTINED estimand and never treats them as valid.
5. Both estimands are preregistered in the hash-bound Campaign declaration.
6. Both are always reported (§9).
7. QUARANTINE_SENSITIVE blocks advancement (§8).
8. P-Q applies mechanically to **every** matching suspect observation (all 17,469
   development flags), not only to those known to affect current factors.
9. No P-Q parameter (κ, clause (d), the U1 rule, any reporting choice) may be
   tuned from Campaign performance. All are frozen before Campaign-1 data access.
10. No FactorSpec or Campaign may opt out of P-Q, for example because quarantine
    hurts its result.
11. The amendment is frozen and committed before any implementation or Campaign
    access. The confirmation stage (Phase 10, reserve) must preregister its own P-Q
    treatment. Reserve suspect days stay DEFERRED.

## 13. Contract change map (proposed; the original contract is NOT edited)

| Section | Existing authority | Proposed language (additive unless stated) | Reason | Certification claim affected |
|---|---|---|---|---|
| §8 Corporate actions | "Flagged days are retained, counted and specimen-audited, never silently dropped." | Add: "Under amendment P-Q, CORP_ACTION_SUSPECT observations are additionally subject to the research-consumption quarantine (P-Q §§3–9). Quarantine never deletes, repairs or relabels an observation." | defines the new layer while keeping retention and visibility | none weakened. The specimen-audit obligation remains, with its status recorded as INCONCLUSIVE |
| new §8A Quarantine | — | the full state model, propagation, dual estimand and reporting (P-Q §§3–9) | the normative home for P-Q | adds a research-consumption claim |
| §9.6 / §9.7 | DELISTING-SENSITIVE advancement rule | Add a parallel QUARANTINE_SENSITIVE rule (P-Q §8). Advancement requires neither flag in any cell. Phase-10 preregistration must include P-Q | conservative advancement | adds an advancement restriction |
| §16 gate 6 | "CORP_ACTION_SUSPECT list with a specimen audit"; "All of these must PASS" | Replacement for gate 6 only: "Gate 6 status is recorded as in P-Q §10. Development admission may proceed with gate 6 NOT_ASSESSED **only** under P-Q, with every other gate PASS. This exception covers no other gate." | the separate admission basis | the admission basis changes from "all gates PASS" to "all other gates PASS + P-Q" |
| §17 Bounded claim | "If every §16 gate passes, Dataset v1 supports this claim: …" | Add an alternative claim: "If every §16 gate except gate 6 passes and P-Q is in force, Dataset v1 supports the §17 claim **excluding** any statement implying CORP_ACTION_SUSPECT data quality is certified, plus: 'CORP_ACTION_SUSPECT observations are quarantined at the research-consumption boundary and every Campaign-1 result is reported under QUARANTINED and ORIGINAL estimands with a QUARANTINE_SENSITIVE advancement rule.'" | the claim must not overstate | narrowed |
| §18 Nonclaims | complex CA "bounded only by … specimen-level certification" | Add: "CORP_ACTION_SUSPECT data quality: NOT CERTIFIED (Gate-6 v1 INCONCLUSIVE). The QUARANTINED estimand is a data-contamination sensitivity estimand and is not investable. Not-sensitive does not establish correctness or immateriality. Residual cross-sectional influence is not removed." | explicit nonclaims | adds nonclaims |
| §19 Versioning | "Changing any item in the §22 table creates a new dataset version." | Add a `quarantine_rule_hash` covering P-Q §§3–9. **Versioning consequence: see U5** | P-Q changes §22 rows | identity and versioning |
| §22 Frozen decisions | "Corporate actions: trusted reconstruction only; suspect days retained and flagged"; "Advancement rule: DELISTING-SENSITIVE …" | Corporate actions: "… retained, flagged, and quarantined under P-Q". Advancement: "… and QUARANTINE_SENSITIVE (P-Q §8)" | consistency | as §8 and §9.6 |
| D7b clarification §§2–7 | the Campaign declaration fixes the T0/TS evaluation | Add the estimand dimension to the declaration, `CA_QUARANTINED_*` typing, Q_B/Q_S/Q_L, and the four-cell evaluation | evaluation plumbing | none weakened |
| D7c clarification §4 | "CORP_ACTION_SUSPECT … → usable" | Unchanged for values. Add the state panel with the same lag (P-Q §4) | values immutable | none |
| Gate-6 clarification §8 | INCONCLUSIVE → NOT_ASSESSED, still blocking | Add: "blocking for data-quality certification. Development admission may proceed only under P-Q (contract §16 as amended)" | reconcile the admission basis | the gate-6 mapping is unchanged as to data quality |

## 14. Unresolved design questions (each must be decided before adoption)

- **U1 — Which closes are quarantined.** The draft uses both endpoints
  (close(prev(s)) and close(s)), which is fail-closed. The narrower alternative is
  close(s) only. The endpoint rule also uses a flag at s = f (known at close(f),
  after the close(f−1) cutoff) for eligibility typing.
- **U2 — TRI over-quarantine.** Structural dependency quarantines TRI-based values
  even when a ratio cancels earlier returns. The alternative is an expression-algebra
  analysis that recognizes ratio windows (complex, and not general).
- **U3 — Residual cross-sectional influence.** Quarantined members' factor values
  still shape others' ranks and groups. The draft keeps the single grouping and
  reports the residual. The alternative is a third estimand that masks quarantined
  factor values before grouping (it changes composition, so it would need explicit
  precommitment as a separate estimand).
- **U4 — κ and clause (d).** Confirm κ = 2 or set another value, and decide whether
  to include a magnitude clause and its threshold. All of this must be frozen before
  Campaign-1.
- **U5 — Versioning.** Under §19, changing §22 rows creates a new dataset version.
  Decide whether P-Q is (i) a new dataset version label with the factual artifacts
  hash-identical, or (ii) a research-consumption overlay bound by
  `quarantine_rule_hash` with an explicit §19 exception.
- **U6 — Confirmation stage.** How Phase-10 preregistration incorporates P-Q, and
  how reserve suspect days (DEFERRED) are treated.
- **U7 — The engine's NaN weight treatment.** Confirm that the Phase-7 engine's
  handling of typed-NaN members for turnover and cost matches between estimands, or
  define the reconciliation.
- **U8 — Liquidity-window exposure.** Confirm that it is report-only (as drafted).

## 15. Certification and non-certification claims

**If adopted and implemented, P-Q could support at most:**

> Campaign-1 research on Dataset v1 is reported under a preregistered QUARANTINED
> estimand that excludes every evaluation observation depending on a
> CORP_ACTION_SUSPECT source observation, alongside the ORIGINAL estimand, with a
> conservative QUARANTINE_SENSITIVE advancement rule.

**It does not claim:**
- Gate-6 PASS, or any data-quality certification of suspect observations;
- that suspect observations are correct, or that their effect is harmless or
  immaterial;
- that the QUARANTINED estimand is an investable or unbiased return estimand;
- removal of residual cross-sectional influence;
- coverage of errors outside the suspect list;
- any change to PIT, identity, delisting, complex-corporate-action or other §18
  nonclaims;
- any change to the v1 audit, the v2B lineage, or reserve and confirmation status.

## Access record

- Implementation: NO. Original contract modified: NO.
- v1 Gate-6 outcome changed: NO. v2B preserved: YES. +C: DEFERRED.
- Campaign data accessed: NO. No factor values, returns, IC, Sharpe, t-statistics
  or screens were computed.
- Reserve reads: 0. Provider and network calls: 0.
- Knowledge-PIT append: NO.
- E3 scratch provenance retained, unmodified.
