# Discovery Dataset v1.2 — P-Q Research-Layer Quarantine Amendment (ADOPTED; Dataset v1.2 Authority)

- **Status:** ADOPTED and FROZEN. The user authorized adoption on 2026-09-30 ("P-Q
  ADOPTION — FREEZE DATASET v1.2 CONTRACT"), on master `ed193a3`.
  **Implementation is NOT started and NOT authorized.**
- **History is unchanged:** the v1 contract and every v1.1 document stay unchanged
  as history.
  - Dataset v1.2 is **Dataset v1.1 with this amendment and its normative rules
    file. No other methodology changes.**
- **Dataset id:** **`discovery_dataset_v1_2`**, bound to the SHA-256 of this
  file's committed bytes.
  - That hash is recorded outside the file, in the adoption commit message.
  - This follows the v1.1 precedent
    (`docs/discovery_dataset_v1_1_classification_amendment.md`: "Dataset id:
    `discovery_dataset_v1_1`, bound to the SHA-256 of this file's committed
    bytes").
- **Normative rule text:** `docs/discovery_dataset_v1_2_pq_quarantine_rules.md`.
  - **`quarantine_rule_hash`** =
    `df5bec69103eca5ad9196b3342e5013fab30a3b85cdb3a5332b033dcac5db886`.
  - Where this summary and the rules file differ, **the rules file governs**.

## 1. Parent authority (exact bindings)

| Authority | Commit | SHA-256 |
|---|---|---|
| v1 contract `docs/discovery_dataset_v1_contract.md` | 7348a31 | `d03f35549777e5a94046f06dd0621acfc3a1dedb7a4b40adc55fdae911fa80e3` |
| v1.1 classification amendment (defines `discovery_dataset_v1_1`) | b6225b6 | `f89a7128bb8113c6978fc6bca8bcaf6048cd0c7db1fe5ac2aaffa1c7d519c623` |
| v1.1 identity-binding clarification | b9d9e88 | `57da1603ff0c77f19460293802d34f2b2383800e0528746dcb8fbb6b9ceedfa9` |
| v1.1 D6 clarification | 5238303 | `e3cb52a892f533c41c5c0d1d927cd54d7e200ef2a28e56e76efdf01227aa17ea` |
| v1.1 D6 duplicate-key clarification | c9de712 | `7d34c6037040f8998966221fd1a68db1e9f2f9e74004d746081464035ebb22a0` |
| v1.1 D7 clarification | 8efbd96 | `bc8e6920d31dd429beb62030123a721a7293558ba57fe8e3d4d6cd6368a96946` |
| v1.1 D7b clarification | 0a72a02 | `7cf2f702982bf24179010b6adcdc37f867d0c70803afe4fc7809898f22324bad` |
| v1.1 D7c clarification / addendum | 839d4b0 / 1556167 | `508d5f1ac3da3c68233c72b4b31ea75d0ec1d88be9241e6dd018cd54467b4a89` / `75806604cfb10bc9790126c5e833255497c5883c4370490ca593039595aaf05d` |
| v1.1 D8 clarification / addendum | 2245cbb / 6b8acdb | `9a3f218d67c7e7456b2a5c9439a2d1fffe202e5beca3299afb31439b919b24c0` / `d46b9ef6a59a059dc17f2134835da884e8753cc7f7089c434e7ec725fcfddbcf` |
| Gate-6 clarification / addendum | cab82e0 / 4695544 | `0b529dca209b4fa9f54f9662bb3d9205f07b0056232532b2a529a3a513f43789` / `9960d3e19f2966526c82767cfb15fd7e4a50383b2c42f83dcc9de41b130dcfa6` |
| Gate-6 v1 audit result (INCONCLUSIVE) | a91628e | `8d91f58ebac8cf0483fa9bee473f032541c631d51f16946c0aef09fb8d5c2995` |
| G6-v2B protocol / addendum | c8705c6 / c165f6e | `f04d2253747e2b9fdebad3681ac93832b3d452d5463c009d17c5a16607b36c27` / `f88404d9a2553f37d5bc348762303ed9e78a369801facc0351f8948a0172b54f` |
| P-Q path comparison | ea0a763 | `9006f0a74fc8223ca03f1ce13440fce4848347e7f860a5f308d3363e84568c84` |
| P-Q draft | 3c8d542 | `786c9f142f41fb2221e52aeab24fc3d5b0971e5a1888f3a50f8d8cbee5e1e543` |
| P-Q open-question resolution | ed193a3 | `a6abdc054eafcd8d81a1c32b0ef31d08ea6b698c6a0986d680f2ab4bc7321667` |

**Correction of record.** The open-question resolution §6 cites D-SC as
`c-d386c342dcd1f4b4`. The production D-SC artifact consumed by D6 and D7c is
**`c-27b6dabd0db0cb15`** (`dsc_hash` below), as both manifests record. The
`c-d386…` build is a superseded, defective build and is **not** inherited.

## 2. Inherited factual artifacts (byte-for-byte; not rebuilt or renamed)

| Stage | Artifact id | Recorded hash |
|---|---|---|
| Acquisition batch | `b-c21b48190918f21f` | (batch manifest, unchanged) |
| D3 normalized | `n-f874d3ecb61374d4` | `normalized_hash` `f93093a8b8fa07521191d687560c2c26ab16630ebeed35d68aa12867def47fb3` |
| D4 identity | `i-a21f1e46621dee94` | (as recorded by D4) |
| D5 QC | `q-5d1028655f8c82e1` | `qc_hash` `3d63e51b983096f0ae579cd1e382c613d9329d239c3f848cc785dee2d61cdfc3` |
| D-SC classification | `c-27b6dabd0db0cb15` | `dsc_hash` `2ee4940c14a85048fddd9e3e675c3e9a6f40a11c8887192a5e61939adfc62b60` |
| D6 universe | `u-6150bc6ac2866b7f` | `universe_hash` `6e8539b7acb4ed4559846234b0b26e6f1d4e6d34f7e0a5d1e2290134d563ed87` |
| D7a returns | `r-34565f074d64ccda` | `returns_hash` `4c487e47fd613f3f272109d14b43a27e061f9cc31e5b6d6bcefd4abc97994861` |
| D7b software | merged at d2fd75d | (software; unchanged) |
| D7c TrustedInput | `ti-aa23fabd3db4e595` | `ti_hash` `30ee0c51602cf792e8cb7654c2501f52c7d664675dbf78845c903df6683c2ae6` |
| D8 reserve firewall and ARTIFACT | software 4636831; K-log genesis seq 0 | `record_hash` `984d7bec4fbb159b3eba3385b2a0af24a54c2c707478af91b5ba461684da4eea` |
| Gate-6 v1 audit | `g6-2264dbcac2a09a3d` | as bound in the a91628e result |

The hashes above were read from the artifacts' own committed manifests and records.
No raw or reserve file was read.

## 3. Adopted decisions

| # | Adopted |
|---|---|
| U1 | Two-endpoint conservative quarantine: Q(s) = {close(prev(s)), close(s), CA-state} (rules Q1) |
| U2 | Conservative structural TRI propagation, no algebraic optimization (rules Q3.1) |
| U3 | No third pre-ranking-mask estimand. Common factor values and grouping; residual cross-sectional influence reported (rules Q3.3, Q12) |
| U4 | κ = 2. No effect-size clause (rules Q11) |
| U5 | Dataset version transition to **v1.2**. No §19 / §22 exception (§4 below) |
| U6 | Development-only. Reserve, confirmation and Phase 10 NOT AUTHORIZED (§7) |
| U7 | Portfolio-period quarantine; IC-period quarantine; factual turnover and cost with a composition flag; net requires clean gross and clean composition (rules Q6–Q8) |
| U8 | Liquidity-window exposure report-only; no universe reconstruction (rules Q3.5) |
| R1 | **Accepted.** Portfolio-period and IC-period fail-closed granularity is kept regardless of support size. Low support and NOT_ASSESSED are legitimate outcomes |
| R2 | **Compressed-time HAC rejected.** Gap-aware semantics are frozen: HAC lags use original formation-index distance, and there is no bridging, fallback or substitute estimator. A HAC statistics are NOT_ASSESSED until a narrow gap-aware capability is implemented (rules Q9.3) |
| R3 | **Frozen as an implementation prerequisite** (§8). Not resolved by invention here |

**CS-3 adopted** (rules Q10): CS-1 native supports are the decision basis. The
complement and support fractions are diagnostic only. CS-2 is never the decision
basis.

## 4. Version transition (contract §19 / §22)

- **Why a new version:** §19 says "Changing any item in the §22 table creates a new
  dataset version." This amendment changes two §22 rows (below), so it creates
  Dataset v1.2. No exception is made.
- **§22 rows amended for v1.2** (the v1 file itself is not edited):
  - **Corporate actions:** "trusted reconstruction only; suspect days retained and
    flagged" becomes "trusted reconstruction only; suspect days retained, flagged
    and quarantined in research consumption under P-Q (rules
    `df5bec69…b886`)".
  - **Advancement rule:** append "… and QUARANTINE_SENSITIVE or NOT_ASSESSED under
    P-Q rules Q11 (κ = 2)".
- **§19 identities added for v1.2:**
  - `dataset_id = discovery_dataset_v1_2`, bound to this file's SHA-256 (adoption
    commit message);
  - `quarantine_rule_hash = df5bec69103eca5ad9196b3342e5013fab30a3b85cdb3a5332b033dcac5db886`.
- **New in v1.2 (to be produced only under a future implementation
  authorization):**
  - a derived quarantine-overlay artifact (rules Q1–Q4 states), bound to
    `quarantine_rule_hash` and to the §2 parent ids and hashes, with
    `parent_dataset_id = discovery_dataset_v1_1`;
  - the dual-estimand evaluation extension (rules Q5–Q12);
  - the narrow gap-aware HAC capability (rules Q9.3);
  - a Dataset-v1.2 admission record.
- **Not retroactive:**
  - Dataset v1 and v1.1 documents, artifacts, statuses and hashes are unchanged.
  - Dataset v1.1 development admission remains **HOLD**.
  - New research-consumption semantics are not a modification of Dataset v1 or
    v1.1.

## 5. Contract change map, as adopted for v1.2

| v1 section | Existing authority | v1.2 rule | Claim effect |
|---|---|---|---|
| §8 | "Flagged days are retained, counted and specimen-audited, never silently dropped." | Retained. Additionally, CORP_ACTION_SUSPECT observations are quarantined in research consumption per rules Q1–Q4. Quarantine never deletes, repairs or relabels | the specimen audit stays, with status INCONCLUSIVE |
| new §8A | — | the rules file (Q1–Q13) | adds a research-consumption claim |
| §9.6 / §9.7 | DELISTING-SENSITIVE rule | plus QUARANTINE_SENSITIVE / NOT_ASSESSED (rules Q11). Both are independently binding. Phase-10 use is not authorized (§7) | adds an advancement restriction |
| §16 gate 6 | "CORP_ACTION_SUSPECT list with a specimen audit"; "All of these must PASS" | For v1.2 development admission only: gate 6 recorded as in §6. Admission may proceed with gate 6 NOT_ASSESSED **only** on the §6 basis, with every other gate PASS. This covers no other gate | the admission basis is separate from data-quality certification |
| §17 | "If every §16 gate passes …" | v1.2 alternative claim: the §17 claim minus any implication that CORP_ACTION_SUSPECT data quality is certified, plus the rules Q5/Q11/Q12 statement (§9 below) | narrowed |
| §18 | complex-CA nonclaim | plus the §9 nonclaims | adds nonclaims |
| §19 / §22 | versioning; frozen table | §4 above | new version |
| D7b clarification §§2–7 | the Campaign declaration fixes the T0/TS evaluation | the estimand dimension, rules Q5–Q12, and the four-cell evaluation | none weakened |
| D7c clarification §4 | CORP_ACTION_SUSPECT usable | values unchanged; state panels with the same lag (rules Q3.2) | none |
| Gate-6 clarification §8 | INCONCLUSIVE → NOT_ASSESSED, blocking | still blocking for data-quality certification and for v1.1. For v1.2, development admission may proceed only on the §6 basis | unchanged as to data quality |

## 6. Gate-6 status and Dataset-v1.2 admission basis

**Required status language:**

    Gate-6 v1 audit:              INCONCLUSIVE
    Gate-6 data quality:          NOT_ASSESSED
    research treatment:           EXPLICITLY QUARANTINED UNDER P-Q
    development admission basis:  SAFE RESEARCH CONSUMPTION,
                                  NOT DATA-QUALITY CERTIFICATION
    G6-v2B:                       OPEN
    +C:                           DEFERRED / NOT AUTHORIZED

- **Never written:** "Gate 6 PASS", "conditional Gate-6 PASS", "effectively passed",
  or any equivalent.
- **Data-quality certification ≠ safe research consumption.** P-Q can at most
  support the latter.

**Dataset v1.2 may later be admitted for development use only if all of these
hold:**
1. every other required §16 gate passes;
2. the implemented P-Q layer passes its own frozen barriers (§8);
3. an explicit Dataset-v1.2 admission review approves the safe-research-consumption
   basis.

**Exception wording for the admission record:**

> unresolved Gate-6 data-quality uncertainty is quarantined at the
> research-consumption boundary.

This exception applies only to the Gate-6 CORP_ACTION_SUSPECT condition. It is not a
precedent for any other gate.

## 7. Development only

Dataset-v1.2 P-Q authority applies **only** to development research. It does not
authorize:
- reserve access;
- confirmation evaluation, or Phase-10 confirmation;
- final-holdout consumption;
- production use.

Reserve suspect days stay DEFERRED (Gate-6 clarification §2). Reserve and
confirmation treatment needs a separately frozen future specification.

## 8. Implementation prerequisites (binding)

**No Dataset-v1.2 Campaign declaration may be registered, and no Campaign-1 run may
start, until a separately authorized and frozen implementation specification has
defined and tested all of the following:**
1. **R3, identity:**
   - Dataset-v1.2 canonical identity (`discovery_dataset_v1_2` with this file's hash);
   - the parent Dataset-v1 / v1.1 binding;
   - the adopted-amendment hash binding;
   - the `quarantine_rule_hash` binding;
   - inherited D8 artifact identity semantics (the genesis record above is inherited,
     not re-registered, unless the specification proves otherwise);
   - Knowledge-PIT dataset and version identity.
2. **The quarantine overlay:** rules Q1–Q4, deterministic and byte-identical on
   rebuild, with tests for every Q3.1 / Q4.1 boundary (u ∈ {prev(s), s, next(s)};
   s = f; prev(s) = e′; segment anchors; the bridge lag).
3. **Dual-estimand evaluation:** rules Q5–Q8 and Q10–Q12, including the forbidden
   behaviours as negative tests (no renormalization, regrouping, pair-drop recompute
   or compressed HAC).
4. **The gap-aware HAC capability:** rules Q9.3, narrow, using original
   formation-index lags. Until frozen and tested, A HAC statistics are NOT_ASSESSED.
5. **Path-metric classification:** rules Q9.4.
6. **The P-Q barriers** and the Dataset-v1.2 admission review (§6).

**Remaining implementation tasks, each needing separate authorization:**
- the implementation specification freeze;
- the overlay build;
- the evaluation extension;
- the gap-aware HAC;
- the barriers;
- the v1.2 admission review;
- only after all of those, any Campaign-1 declaration.

## 9. Certification and non-certification claims

**If implemented, barrier-tested and admitted, Dataset v1.2 can support at most:**

> Development research on Dataset v1.2 is reported under a preregistered QUARANTINED
> estimand that excludes every portfolio-period and IC-period depending on a
> CORP_ACTION_SUSPECT source observation, alongside the ORIGINAL estimand, with a
> conservative QUARANTINE_SENSITIVE / NOT_ASSESSED advancement rule.

**It does not claim:**
- Gate-6 PASS, or the data quality of any suspect observation;
- price correctness, or corporate-action completeness;
- that suspect effects are harmless;
- that the QUARANTINED estimand is investable or unbiased;
- removal of residual cross-sectional influence;
- coverage of errors outside the suspect list;
- reserve certification, or confirmation eligibility;
- alpha, or production readiness;
- any change to existing §18 nonclaims, PIT, identity or delisting boundaries.

## 10. Coexistence

- **G6-v2B remains OPEN:** the protocol, addendum, vendor research, and the Tiingo
  correspondence awaiting reply.
  - A future genuine Gate-6 PASS may remove the *need* for quarantine **for new
    runs only**, through a separately frozen transition rule.
  - Runs made under P-Q stay reproducible under their original treatment.
- **+C:** DEFERRED / NOT AUTHORIZED.

## Adoption record

- Implementation: NO. Dataset-v1.2 admission review: NOT RUN.
- Campaign-1: NOT STARTED.
- Reserve reads: 0. Provider and network calls: 0.
- Knowledge-PIT append: NO.
- Historical v1 / v1.1 modified: NO.
