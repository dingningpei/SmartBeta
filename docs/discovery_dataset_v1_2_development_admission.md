# Discovery Dataset v1.2 — Development Admission Review (DATA-FREE)

- **Decision:** **ADMITTED_FOR_DEVELOPMENT**, under the frozen P-Q **SAFE RESEARCH
  CONSUMPTION** framework.
- **Authorization:** the user's "DATASET v1.2 DEVELOPMENT ADMISSION REVIEW
  AUTHORIZED", on master `06e4a82`.
- **Question decided (narrowly):** given that Gate-6 v1 remains INCONCLUSIVE and Gate-6
  data quality remains NOT_ASSESSED, has Dataset v1.2 implemented and validated
  sufficient fail-closed quarantine semantics to permit **development** factor
  research on the safe-research-consumption basis? This review does **not** ask
  whether Dataset v1.2 is data-quality certified, and does not require the suspect
  observations to be proven correct.
- **Method:** an evidence review only. No real factor evaluation, performance metric,
  screen, ranking or alpha estimate was computed. The bound evidence was verified
  read-only. The overlay was **not** rebuilt.

## 1. Authority bindings

| Authority | Commit | SHA-256 |
|---|---|---|
| Dataset-v1.2 authority (adopted P-Q amendment) `docs/discovery_dataset_v1_2_pq_amendment.md` | ffcc8da | `9261a55b9b82a5708dded65bfd3083d5a960cfa4375681b1b23390da62fb3ed0` |
| P-Q rules `docs/discovery_dataset_v1_2_pq_quarantine_rules.md` (= `quarantine_rule_hash`) | ffcc8da | `df5bec69103eca5ad9196b3342e5013fab30a3b85cdb3a5332b033dcac5db886` |
| v1 contract `docs/discovery_dataset_v1_contract.md` | 7348a31 | `d03f35549777e5a94046f06dd0621acfc3a1dedb7a4b40adc55fdae911fa80e3` |
| v1.1 classification amendment | b6225b6 | `f89a7128bb8113c6978fc6bca8bcaf6048cd0c7db1fe5ac2aaffa1c7d519c623` |
| Gate-6 clarification / addendum | cab82e0 / 4695544 | `0b529dca209b4fa9f54f9662bb3d9205f07b0056232532b2a529a3a513f43789` / `9960d3e19f2966526c82767cfb15fd7e4a50383b2c42f83dcc9de41b130dcfa6` |
| Gate-6 v1 audit result (INCONCLUSIVE) | a91628e | `8d91f58ebac8cf0483fa9bee473f032541c631d51f16946c0aef09fb8d5c2995` |
| P-Q implementation plan / PQA + E2 + E3 / PQB / PQC + E1 | bab2296; 15ab4e1; c347a69; 28b5068 | `a196d4f3…a0a1` / `92e175f5…3f44`, `d440ac09…ee9d`, `66322fbe…74ff` / `d49110ee…1cb7` / `820f8d45…dfef`, `c5ab26b9…475d` (full hashes in the integration record) |
| **P-Q Integration Barrier evidence** `docs/discovery_dataset_v1_2_pq_integration_barrier.md` | 06e4a82 | `296437bbe3e09187869d9320dab6b7b289cd2ae6ab1e417501398c5b493609cd` |

**Admission conditions** (amendment §6), all satisfied (§§2–4 below):
1. every other required §16 gate passes;
2. the implemented P-Q layer passes its own frozen barriers;
3. this explicit review approves the safe-research-consumption basis.

## 2. Inherited gate matrix (contract §16; evidence already bound; not re-run)

| Gate | Status for Dataset-v1.2 development | Bound evidence |
|---|---|---|
| 1 Manifest completeness | **PASS** | D5 `q-5d1028655f8c82e1` (`qc_hash` `3d63e51b983096f0ae579cd1e382c613d9329d239c3f848cc785dee2d61cdfc3`): expected = summary = status = 10,903 |
| 2 Coverage | **PASS** | D5: non-OK 42 of 10,903 (0.385%, ≤ 1%; all listed) |
| 3 Identity | **PASS** | D5 / D4 `i-a21f1e46621dee94`: INTERVAL_ASSIGNED 10,353; ambiguous, unreachable and other states listed and excluded |
| 4 Rows vs calendar | **PASS** | D5: 16,619,088 observations; 4 off-calendar rows flagged |
| 5 OHLC consistency | **PASS** | D5: 0 non-positive prices, 0 duplicate dates; 14 OHLC-inconsistent rows flagged |
| **6 Corporate actions** | **v1 specimen audit = INCONCLUSIVE; data quality = NOT_ASSESSED** (unchanged). Research treatment: **EXPLICITLY QUARANTINED UNDER P-Q** | D5 delivered the malformed counts (0) and the counted suspect list (17,469 development). D5's internal "gate_6" status covers only those sub-items. The required specimen audit is INCONCLUSIVE (a91628e), and the Gate-6 clarification §8 mapping applies. See §4 |
| 7 Volume | **PASS** | D5: 0 vendor-null rows; zero-volume distribution recorded (685,963) |
| 8 Listings | **PASS** | D5: 10,353 interval-assigned; 4 interval-mismatch rows, 5 termination-mismatch rows flagged |
| 9 Integrity | **PASS** | D5: 10,893 raw files, 7 D3 files and 2 D4 files hash-verified |
| 10 Reproducibility | **PASS** | D5: rebuild of `n-f874d3ecb61374d4` byte-identical (7 files, 0 mismatches) |
| 11 Universe | **PASS (development)**; reserve part DEFERRED / NOT ASSESSED | D6 `u-6150bc6ac2866b7f` (`universe_hash` `6e8539b7…ed87`) manifest `gate_11.development = PASS` |
| 12 Reserve firewall | **PASS** | D8 development accessor and firewall, merged 4636831; tests in the passing full suite; `reserve_firewall.py` `4b8a74b5…3e15` pinned by D7c and PQA |
| 13 Timing | **PASS** | D7c TrustedInput bridge, merged 4c273fd (`ti-aa23fabd3db4e595`, `ti_hash` `30ee0c51…c2ae6`): one-session lag and gate-13 future-data invariance |
| 14 Delisting scenarios | **PASS** | D7b bridge, merged d2fd75d (software and structural): T0/TS deterministic, no affected observation dropped, TS leg rule, §9.6 classification. D7b is pinned by PQB `125893be…2efc` |

- **Currency of the evidence:** the final aggregate review at 4c273fd found gate 6's
  specimen audit to be the sole development blocker, with every other gate PASS.
- No inherited artifact has changed since. The P-Q barrier (I2, I19) re-verified the
  manifest-file, manifest-field and data-file hashes of D4, D5, D6, D7a and D7c, and
  the identity-bound D3 and D-SC, on the current master.

## 3. Dataset-v1.2 P-Q integration status

**Integration Barrier I1–I20: PASS.** See the integration record (`296437bb…09cd`).

| Item | Value (verified read-only in this review) |
|---|---|
| Canonical identity | `discovery_dataset_v1_2` (parent `discovery_dataset_v1_1`), bound to amendment `9261a55b…3ed0` |
| Overlay (integration evidence artifact) | `pq-59c3bd7abe08eef5`; `verify_overlay` on the files gives `pq_hash` `baa920ddfc52805c70445abfb8650e2969d341ae9383e1b1a0c6a788fcc95f2c` |
| `quarantine_rule_hash` | `df5bec69103eca5ad9196b3342e5013fab30a3b85cdb3a5332b033dcac5db886` |
| R3 identity | `verify_dataset_v1_2_identity` gives `a065493d295883f425d499816a98ac55a03cd67a1086bd7b64249b90fe76b9da`, equal to the overlay manifest. D8 genesis `984d7bec…4eea` inherited, not re-registered. K-log read only and unchanged |
| Deterministic rebuild | byte-identical, including under `TZ=Asia/Tokyo LC_ALL=C` (barrier I6) |
| Suspect population / member paths / top-500 labels | 17,469 / 110 / 1,700 of 2,772,000, each reconciled exactly with history (I7, I8) |
| Factor-input calculus | NaN-shadow = syntax-tree oracle, cell by cell, on the real grid (I12) |
| Tests | scoped 371 passed; full suite 4,923 passed, 1 skipped (credential-scrubbed, network-denied) |

**Safe-consumption invariants:** each is supported by the barrier evidence and the
frozen rules. **None is unsupported.**
- original factual observations and the original D6 universe are preserved;
- A/B factor values and grouping are shared;
- no member deletion, weight renormalization, implicit cash or zero-return
  substitution;
- no IC pair deletion and no re-ranking;
- quarantine at portfolio-period and IC-period level;
- conservative TRI propagation;
- gap-aware HAC (no compressed-time NW; NOT_ASSESSED where it cannot be computed);
- path metrics fail closed across gaps;
- CS-3 (CS-1 decision basis; complement diagnostic only);
- κ = 2, with QUARANTINE_SENSITIVE and NOT_ASSESSED blocking advancement;
- DELISTING_SENSITIVE independently binding in both estimands.

## 4. Gate-6 exception treatment

    Gate-6 v1 audit:              INCONCLUSIVE (a91628e; permanent)
    Gate-6 data quality:          NOT_ASSESSED
    research treatment:           EXPLICITLY QUARANTINED UNDER P-Q
    development admission basis:  SAFE RESEARCH CONSUMPTION, NOT DATA-QUALITY CERTIFICATION
    G6-v2B:                       OPEN — OPERATIONALLY PAUSED
    +C:                           DEFERRED / NOT AUTHORIZED

- **Admission rests on this exception:** unresolved Gate-6 corporate-action uncertainty
  is explicitly quarantined at the Dataset-v1.2 research-consumption boundary.
- **Underlying data quality is unchanged.** Dataset v1.2 changes only the
  research-consumption treatment of these observations.
- **Not a precedent:** the exception is specific to this frozen Gate-6
  CORP_ACTION_SUSPECT uncertainty. It is not a precedent for bypassing any other failed
  or inconclusive gate.
- **Never stated:** Gate 6 is not described as passed, conditionally or effectively
  passed, remediated or certified.

## 5. Recorded observations

- **O1 — `residual_cs_cells`: KNOWN REPORTING DEFECT; NON-ADMISSION-BLOCKING;
  CAMPAIGN REPORTING BLOCKER.**
  - The diagnostic currently also counts floating-point changes from time-series
    rolling ops that are not caused by cross-sectional ops. Example: `rolling_mean`,
    97,691 cells, maximum relative difference about 5.7e-13.
  - It does not alter quarantine states, A/B admissibility, QUARANTINE_SENSITIVE or
    advancement.
  - **It must be corrected and tested before any Campaign-1 reporting is final or
    authoritative.**
  - Until then, the diagnostic must be neither silently omitted nor interpreted as
    genuine residual cross-sectional influence.
- **O2 — Phase-6 rolling-variance numerics: KNOWN NUMERICAL LIMITATION / NON-CLAIM;
  NON-ADMISSION-BLOCKING.**
  - For windows whose theoretical variance is about zero, the frozen Phase-6 rolling
    implementation can retain floating-point residue from its accumulator. The maximum
    absolute discrepancy observed is about 6.5e-7.
  - P-Q structural propagation remains exact, and A and B share the same numerical
    factor computation.
  - **Not claimed:** exact real-arithmetic rolling variance, exact zero at
    theoretical-zero windows, or bitwise equivalence to ideal symbolic arithmetic.
  - **Frozen escalation rule:** if later Campaign evidence shows this behaviour can
    materially change ranks, group assignment, screen outcomes or A/B comparison
    semantics, STOP that affected analysis and open a separately authorized numerical
    remediation review.
  - No Phase-6 change is authorized or prejudged now.

## 6. Scope and claim boundary

**Development only.** Development formations run 2016-01-04 … 2023-06-30 (contract §22),
with:
- acquisition and warm-up history from 2015-01-02;
- labels crossing the reserve start purged, and a maximum forward horizon of 63
  sessions;
- the D6 top-200/500/1000 universes as built.

The reserve (2023-07-03 … C) remains outside development authority.

**Not authorized:**
- reserve access, confirmation evaluation or Phase-10 confirmation;
- final-holdout consumption;
- production use.

Reserve readiness is **not** inferred from this admission.

**Permitted conclusion:**

> Dataset v1.2 is admitted for DEVELOPMENT factor research under the frozen P-Q SAFE
> RESEARCH CONSUMPTION framework. This admission does not certify the underlying Gate-6
> suspect observations. Gate-6 v1 remains INCONCLUSIVE and Gate-6 data quality remains
> NOT_ASSESSED. Reserve and confirmation use remain unauthorized.

**Not claimed:**
- production, confirmation or investment readiness;
- alpha or factor validity;
- full data-quality certification;
- any change to existing §18 nonclaims or the identity, delisting, PIT and vintage
  boundaries.
- O2 is a non-claim (§5).

## 7. Campaign-1 consequence and Knowledge-PIT

- **Campaign-1:** NOT STARTED.
  - Dataset admission is recorded separately from Campaign authorization.
  - The next step is a separately authorized Campaign-1 precommitment/design task.
  - O1 must be resolved before Campaign-1 reporting is final.
  - No Campaign declaration may be registered without the R3-bound identity already
    certified.
- **Knowledge-PIT:** no append.
  - The authority requires no Knowledge-PIT record for development artifacts or for
    this admission (amendment §§6 and 8; the D7c clarification §6 precedent; Gate-6
    clarification §9).
  - The R3 identity rule states that v1.2 records are `NONE_AUTHORIZED`.
  - Any future registration follows the separately governed Phase-10 rules.

## 8. Final decision

**ADMITTED_FOR_DEVELOPMENT**, on the SAFE RESEARCH CONSUMPTION basis, with the claim
limitations in §§4–6.

**Review access record:**
- real factor evaluations: 0; performance metrics: 0; screen verdicts: 0;
- reserve reads: 0; final-holdout consumption: 0; provider / network calls: 0;
- Knowledge-PIT appends: 0.
- Production code changed: NO. Overlay rebuilt: NO.
