# Discovery Dataset v1.1 — P-Q Open-Question Resolution

> **DESIGN RESOLUTION — P-Q STILL NOT ADOPTED.**
> **No implementation authorized.** No frozen contract or clarification is
> edited, and no dataset version is created.

- **Requested by:** the user's "P-Q OPEN-QUESTION RESOLUTION ADDENDUM — DESIGN
  ONLY" instruction of 2026-09-30, on master `3c8d542`.
- **Parent draft:** `docs/discovery_dataset_v1_1_pq_contract_amendment_draft.md`,
  SHA-256 `786c9f142f41fb2221e52aeab24fc3d5b0971e5a1888f3a50f8d8cbee5e1e543`.
  Where this document and the draft differ, **this document governs the proposal**.
  In particular, §3 below supersedes draft §6.2 (member-level typed-NaN
  inadmissibility), §6.4 and §6.5.
- **Engine facts used.** They come from the unchanged Phase-7 code, read only to
  define semantics; nothing was run:
  - `smart_beta/evaluation/metrics.py`:
    - `long_short_mean_tstat` and the IC aggregation drop non-finite periods,
      then apply Newey-West to the **compressed** series;
    - `sharpe_ratio` uses non-NaN periods;
    - `max_drawdown` compounds `(1 + r)` over non-NaN periods in time order.
  - `smart_beta/evaluation/portfolio.py`:
    - turnover_t = changed(t) / |U| over the **membership sequence alone** ("the
      forward return is never consulted");
    - cost_t = cost_rate × turnover_t;
    - net_t = gross_t − cost_t is the single cost-application point.

## 1. Final proposed resolutions U1–U8

| # | Resolution |
|---|---|
| U1 | **Two-endpoint conservative quarantine accepted.** Q(s) = {close(prev(s)), close(s), CA-state(prev(s) → s)}, with the dependency semantics of draft §§4.1 and 5 unchanged |
| U2 | **Conservative TRI propagation accepted.** No symbolic or algebraic dependency optimization |
| U3 | **No third pre-ranking-mask estimand.** Common factor values and the common grouping are kept. Residual cross-sectional influence is reported (draft §6.4, retained as reporting only) |
| U4 | **κ = 2. Clause (d) removed.** QUARANTINE_SENSITIVE ⇔ (a) economic-statistic sign difference, **or** (b) screen-verdict difference in either direction, **or** (c) Q_S > 2·Q_B or Q_L > 2·Q_B (or Q_S > 0 or Q_L > 0 when Q_B = 0). No effect-size threshold is introduced |
| U5 | **Dataset version transition required.** No exception to §19 / §22. See §6 |
| U6 | **P-Q is development-only.** It authorizes no reserve access and no Phase-10 confirmation. Reserve and confirmation treatment stays **NOT AUTHORIZED**, pending a separately frozen specification |
| U7 | **Resolved below** (§§2–5) |
| U8 | **Liquidity-window exposure is report-only.** Historical universe membership is never reconstructed because of P-Q |

## 2. Governing accounting principle (U7)

**Forbidden in the QUARANTINED estimand (A):**
- silently dropping quarantined member returns;
- renormalizing surviving weights;
- implicit cash substitution, or zero-return substitution;
- forward or back fill;
- regrouping, or changing quantile membership.

**Principle (adopted as proposed by the user, made exact here).** The unit of
quarantine in A is the **portfolio-period**, never the member. A portfolio-period
value in A is **`QUARANTINE_PROPAGATED`, and unavailable in A**, if computing it
under its pre-existing frozen composition requires at least one constituent
contribution that is quarantined. A constituent contribution is quarantined when
either:
- **label:** the member's (f, h) forward label is `QUARANTINE_PROPAGATED`
  (draft §5); or
- **input:** the member's factor observation at f is `QUARANTINE_PROPAGATED`
  (draft §4). Its presence in that group is itself determined by suspect-dependent
  data, so the composition is contaminated.

**No partial-return portfolio may stand in for the original portfolio.**

**Precise consequence: the A-available values are exactly the B values.** On every
portfolio-period that is available in A, the composition, weights and constituent
returns equal those of B. So the A value equals the B value exactly. Estimand A is
**the same series as B, restricted to quarantine-free portfolio-periods**. This is
what makes A interpretable, and it drives the common-support analysis in §5.

**Other typed NaNs are unchanged.** Existing D7b typed NaNs (for example
`TERMINAL_OBSERVED_PATH_INCOMPLETE`) keep their unchanged D7b treatment, which is
identical in A and B. P-Q adds no new member-level NaN.

## 3. Exact propagation rules

Notation: at evaluation period t (formation f, horizon h), G_t(g) is the frozen
membership of group g. The economic legs L_t and S_t follow D7b §5. For each
quantity, A-available means the quantity is defined in A for that period.

| Quantity | Rule in A |
|---|---|
| **group return** R_t(g) | A-available iff no member of G_t(g) has a quarantined label **or** a quarantined input |
| **long-leg return** | A-available iff the economic-long group is A-available |
| **short-leg return** | A-available iff the economic-short group is A-available |
| **long-short (gross) return** | A-available iff **both** legs are A-available |
| **benchmark-relative return** (where admitted; never under sign = −1, per D7b §5) | A-available iff the portfolio series value **and** the benchmark value are A-available. A universe benchmark built from member returns is A-available iff no constituent of it carries a quarantined label or input |
| **turnover_t** | **a factual quantity, always computed and identical in A and B**. It depends on membership only (engine fact), and membership is frozen and common. In A it carries a composition flag: `COMPOSITION_CLEAN` iff no member of U_t or U_{t−1} has a quarantined input, otherwise `COMPOSITION_QUARANTINED` |
| **cost_t** | a factual quantity, identical in A and B (cost_rate × turnover_t), carrying the same composition flag as turnover_t |
| **net long-short return** | A-available iff the gross long-short return is A-available **and** cost_t is `COMPOSITION_CLEAN`. Return and cost therefore always refer to the same effective portfolio. A net value is never formed from a quarantined gross, and never from a clean gross with a composition-quarantined cost |
| **IC / rank-IC per period** | see §4 |
| **mean, Newey-West t-statistic** (long-short, net, IC) | computed by the unchanged engine on the **ordered sequence of A-available periods**. This is an explicit frozen missingness treatment, identical to how the engine treats any non-finite period, and it is declared as the A estimator: "mean / NW t-stat over quarantine-free periods". It is **not** a gap-aware HAC estimator (R2) |
| **Sharpe** | the unchanged engine on the A-available periods (a non-path statistic). The same declared treatment applies |
| **maximum drawdown** | **NOT_ASSESSED in A if any period within the evaluated span is unavailable in A.** The engine's compounding over non-NaN values would bridge gaps, which is path surgery. It is computed only when A has no gap |
| **screen metrics** | the owning Campaign / Phase-8 screen is applied **unchanged** to the A statistics. If a statistic the screen requires is NOT_ASSESSED, or has fewer valid periods than the screen's own rules need, then A's screen verdict is `NOT_ASSESSED`, and the candidate cannot advance (draft §8) |
| **Q_B, Q_S, Q_L** (§8 clause (c)) | unchanged member-level exposure fractions (draft §8). They count members with a quarantined label or input |

**Under ORIGINAL (B):** every quantity is computed exactly as Dataset v1.1 / D7b
already specifies. P-Q changes nothing in B.

## 4. IC / rank-IC rule

- **What IC is:** a per-period cross-sectional correlation over the members of U_t
  that have a factor value and an admissible label. Factor values are unchanged and
  never recomputed.
- **Candidate 1, rejected: drop quarantined pairs and recompute the per-period
  correlation.**
  - For rank-IC, removing members re-ranks the remaining ones. That is the
    correlation analogue of regrouping.
  - For IC, it changes the cross-sectional sample through outcome-dependent
    exclusion (label quarantine conditions on post-formation moves).
  - It would also make the A and B per-period values differ for reasons of
    composition, not contamination.
- **Candidate 2, adopted: period-level quarantine.**
  - IC_t and rank-IC_t are **unavailable in A** iff any member of U_t that has a
    factor value carries a quarantined label or input.
  - Otherwise they equal the B values exactly.
  - Aggregation follows §3: mean and NW over A-available periods.
  - It is coherent with §2 (the period is the unit), with "no regrouping, no
    factor recomputation", and with the A ≡ B-on-support property.
- **Consequence:** IC periods are quarantined more often than leg-based portfolio
  periods, because the IC cross-section is the whole universe.
  - With the trailing-window exposure recorded in draft §2, roughly 3–4
    input-quarantined top-500 members per formation for a ~1-year lookback, a
    large share of IC periods may be unavailable in A for long-lookback
    FactorSpecs.
  - The rule is fail-closed. A sparse A support makes NOT_ASSESSED, and so
    non-advancement, more likely. It never manufactures a pass (R1).

## 5. A/B common-support rule

Under §2, the A-available values equal the B values. The candidate support rules
are:

| Rule | Estimand compared | Advantages | Failure modes | A/B comparable? |
|---|---|---|---|---|
| **CS-1 native supports:** A on its quarantine-free periods; B on all periods | "Does the conclusion change when suspect-dependent periods are excluded?" | Directly measures the dependence of the result on suspect-touched periods. Uses the frozen engine unchanged. No re-weighting | It mixes contamination dependence with **sample-size or power loss**: a smaller A support can lower a t-statistic or flip a screen even if the excluded periods are typical. That is a conservative false positive (blocks advancement) | yes: the same series, full vs. subset |
| **CS-2 common support:** A and B both on A's periods | — | removes power differences | **Degenerate.** Since A ≡ B on A's periods, the two estimates are identical, so the comparison carries no information about contamination | no (identical by construction) |
| **CS-3 native supports plus decomposition (proposed):** decide on CS-1, and additionally report B on A's support (= A), B on the quarantined complement only, and the support fractions | as CS-1, with a transparent decomposition | Separates "what the excluded periods look like" (complement statistic) from "what remains" (A). Support fractions show how much of the change is sample loss. Nothing is selected post hoc | The complement statistic is descriptive and may be noisy. It is never used to rescue a sensitive result | yes |

**Proposed rule: CS-3**, chosen on semantics alone.
- **The decision basis:** QUARANTINE_SENSITIVE clauses (a) and (b) are evaluated on
  CS-1 native supports.
- **Reported only, never decision-relevant:** the complement statistic and the
  support fractions.
- **Why CS-1 is the decision basis:** CS-2 is mathematically empty under the
  period-level principle. CS-1's known bias (power loss) runs only in the
  conservative direction, toward non-advancement.
- **No Campaign data was inspected** to choose it.

## 6. Version transition (U5)

**The authority, traced:**
- Contract §19: "Changing any item in the §22 table creates a **new dataset
  version**. Dataset v1 is never mutated silently."
- §19 identities: `dataset_id` = `discovery_dataset_v1`, bound to `contract_hash`.
- P-Q changes two §22 rows ("Corporate actions" and "Advancement rule"), so a new
  version is required. No exception is proposed.

**The naming convention is mechanical, from existing authority.** The contract text
defines no successor naming. The only precedent is the frozen v1.1 classification
amendment: "Dataset v1.1 is Dataset v1 with this amendment … Dataset id:
`discovery_dataset_v1_1`, bound to the SHA-256 of this file's committed bytes …
recorded outside the file, in the commit message".
- The current dataset is **v1.1** (D-SC, D6, D7a–c and D8 are all v1.1 artifacts).
- **Next version:** **Dataset v1.2**, `dataset_id = discovery_dataset_v1_2`. It is
  bound to the SHA-256 of the *adopted* P-Q amendment file, recorded in the
  adoption commit message, never inside the file.

**What v1.2 inherits byte-for-byte.** Nothing is rebuilt:
- the v1 contract (`d03f3554…80e3`), the v1.1 classification amendment (`f89a7128…c623`)
  and every v1.1 clarification and addendum;
- the acquisition batch `b-c21b48190918f21f`, D1, D3 `n-f874d3ecb61374d4`, D4
  `i-a21f1e46621dee94`, D5 `q-5d1028655f8c82e1`, D-SC
  `c-d386c342dcd1f4b4`, D6 `u-6150bc6ac2866b7f`, D7a `r-34565f074d64ccda`, D7c
  `ti-aa23fabd3db4e595`, the D7b software, and the D8 reserve artifact and
  registration as already recorded;
- the Gate-6 v1 audit (`g6-2264dbcac2a09a3d`, a91628e) and the G6-v2B lineage.

**What belongs to v1.2 only:**
- the adopted P-Q amendment text, which defines `quarantine_rule_hash`;
- a new derived **quarantine overlay** artifact: the Q(s) source sets, primitive
  states, lagged input-state panels, label states and member types. It is bound to
  `quarantine_rule_hash` and to the exact input artifact ids and hashes above;
- the evaluation extension: Campaign-declaration fields for both estimands, the
  §§2–5 accounting, and the QUARANTINE_SENSITIVE computation;
- a v1.2 admission record carrying the draft §10 wording.

**Provenance binding:** the overlay manifest records `parent_dataset_id =
discovery_dataset_v1_1`, the parent artifact ids and hashes, and
`quarantine_rule_hash`. A v1.2 evaluation record binds `dataset_id =
discovery_dataset_v1_2`, the overlay id and the estimand label.

**Not retroactive:**
- v1 and v1.1 artifacts, statuses and hashes are unchanged.
- **Dataset v1.1's development admission remains HOLD**, and its gate 6 remains
  NOT_ASSESSED, blocking.
- Only v1.2, under P-Q, could carry the separate admission basis.
- New research-consumption semantics are **not** a modification of Dataset v1 or
  v1.1.

## 7. Claim boundary (unchanged)

- **Preserved:** Gate-6 v1 = INCONCLUSIVE; Gate-6 data quality = NOT_ASSESSED;
  v2B = OPEN; +C = DEFERRED.
- **P-Q may support only:** **development safe-research-consumption** (draft §15
  claim, restricted to development).
- **It may not support:** Gate-6 PASS, price correctness, CA completeness, reserve
  certification, confirmation eligibility, alpha, or production readiness.

## 8. Remaining questions

None blocks an adoption decision, but each should be accepted explicitly:
- **R1 — A-support sparsity (a consequence, not a gap).**
  - Period-level input quarantine can leave few A-available periods for
    long-lookback FactorSpecs, especially for IC.
  - The rule is fail-closed (NOT_ASSESSED, so no advancement).
  - Accept as is, or decide before adoption on a different unit. Any alternative
    would need to be justified on semantics, not on support size.
- **R2 — t-statistics on a compressed series.**
  - A uses the engine's existing treatment: NW over available periods, adjacent
    after compression. With overlapping horizons, this can misstate autocorrelation
    across gaps.
  - Accept it as the declared A estimator, or require a gap-aware HAC estimator.
    That would be a separate Phase-7 engine amendment, outside P-Q.
- **R3 — Knowledge-PIT / D8 binding for v1.2.**
  - A Campaign under `discovery_dataset_v1_2` will need its dataset identity bound
    in whatever records Campaign-1 governance requires.
  - That is decided in the adoption and implementation step, not here. No append
    is made.

**Readiness:** with U1–U8 resolved as above, and R1–R3 accepted or answered, the
P-Q proposal is **sufficiently specified for an adoption decision**. Adoption itself,
the v1.2 transition and any implementation spec each need separate explicit
authorization.

## Access record

- Implementation: NO. Original contract modified: NO. Dataset version changed: NO.
- Campaign data accessed: NO. Reserve reads: 0. Network and provider calls: 0.
- v1 changed: NO. v2B preserved: YES.
- Knowledge-PIT append: NO.
- E3 scratch provenance retained, unmodified.
