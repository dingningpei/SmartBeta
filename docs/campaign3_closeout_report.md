# Campaign-3 (option b) closeout report

- **Status:** CLOSED. The final disposition is **FORWARDED; T-C NOT RUN (reserve preserved)**.
- **Date:** 2026-10-13.
- **Governing documents:**
  - `docs/campaign3_p5_precommitment.md` (bound SHA-256 `95093b99…1288`, incl. E1);
  - `docs/campaign3_p5_erratum_e2.md` (P5-E2, SHA-256 `a8629869…71cc`);
  - `docs/campaign3_p6_implementation_spec.md` (P6 + E1–E6);
  - `docs/campaign3_p4_amendment_a2_deepseek.md` (A2 + E1/E2);
  - `docs/campaign3_kpit_dataset_reference_note.md`.

## 1. Disposition

| Item | Value |
|---|---|
| Active attempt | 4 (CAMPAIGN record `9e0fedf0ada67f35f279dea3215e43cd435a452a40eb6b206f72cfdfeed2b5ad`, K-PIT seq 46) |
| Generation | 100 proposals used (1 duplicate), 99 unique; 99 certified; 99 evaluated (T-A/IS); 12 DeepSeek calls, 0 failed; 642,029 tokens; stop reason `proposal_budget_exhausted` |
| TB_SELECTION | K-PIT seq 159, `d08c82aab29e37eb505e16fd24311ce5c60956ccd9fbb7ff0c144702f47c95e9` |
| D9 ranking table hash | `1916b9c3473f6dcd199a930ea044985805c218223abac1d64b759d2a6dd2ec4a`; recomputed by C3-F; **equal** to the TB_SELECTION hash |
| FORWARDING | K-PIT seq 160, `37c493d8af891a6340a78094384a41716f452b9c53f631361513d3b53b300e69` (rule: P5-E2, rank 1 of the D9 IS ranking) |
| Forwarded hypothesis | `ed358470c727eb113b802704dba638a1944ceaf1a5c1eafaf995cfbe669f1c69` (`days_since_ea_x_reversal_10_63_neg`) |
| T-C (reserve) | **NOT RUN.** The user declined to authorize it on 2026-10-13 ("不跑 T-C"). Reserve opens: 0. No ACCESS record. |
| Confirmatory claim | **None.** No reserve-tier evidence exists for any Campaign-3 hypothesis. |
| K-PIT canonical log | 161 records, SHA-256 `29f0cabcfa587f18cc5667ffa006371bc7285dccb663ffefd9f4b993693a35fe` (laptop) |

## 2. IS evidence (descriptive only; IS is the generator-visible tier)

- **Eligibility:** 73 of 99 candidates are eligible under the D9 rule. The 26 ineligible
  candidates have an L-envelope scenario that is INVALID: 17 are `SCENARIO_INVALID:L-T0`
  and 9 are `SCENARIO_INVALID:L-T0-K0`.
- **Minimum-scenario L-envelope EWC t, in the declared sign:**
  - rank 1: **0.194**;
  - rank 2: −0.351;
  - rank 5: −0.818;
  - rank 62: −4.10.
  - Only rank 1 is positive.
- **Rank 1 by scenario (HAC t over 54 IS periods):**

  | Scenario | B | L |
  |---|---|---|
  | T0 | 1.575 | 1.331 |
  | TS | 0.397 | 0.186 |

  K0 and K1 are essentially identical.
- **Interpretation, limited to what the evidence supports:**
  - The IS top-1 is not significant even inside the generator-visible IS tier.
  - Under the TS scenarios, almost every candidate's L-envelope t is negative in its
    declared sign.
  - This is IS evidence after a 99-candidate search. It is search-inflated and **not**
    evidence of a factor.
- **Reasons not to spend the reserve:**
  - T-C requires B and L under all T0/TS × K0/K1 combinations at one-sided α = 0.05
    (P7 decision 4).
  - Rank 1's IS minimum t of 0.19 implies a pass probability only slightly above α.
  - The reserve is single-use. The user declined T-C, so the reserve remains untouched.
  - Not testing, decided after seeing IS-only evidence, does not inflate any
    confirmatory error rate.
- **Full table:** the forwarding report
  (`data_store/campaign3/d6c34dbc…/forwarding/campaign3_forwarding_report.{md,json}`;
  SHA-256 md `c82a6cab…f51d`, json `f0df5235…1ca7`).

## 3. T-B infeasibility (planner design defect; P5-E2)

- **The holdout fold cannot be evaluated:**
  - The P4/P5 T-B design was OOS (18 periods) + DEVELOPMENT_SEALED (18 periods).
  - The DEVELOPMENT_SEALED fold is the frozen partition's (`b4b56dbf`) **holdout** fold.
  - Sealed D7b `delisting_bridge._domain_reason` returns
    `NOT_ASSESSED_FINAL_HOLDOUT_OVERLAP` for every fold with role `holdout`.
- **OOS alone cannot be tested:** 18 periods is below the admitted `EWC_T23_ONE_SIDED`
  minimum of T ≥ 24.
- **No usable result was produced:** T-B was abandoned after TB_SELECTION. Its partial OOS
  diagnostics for the first selected candidate were not used for any decision.
- **D8 and D12:**
  - P5-E2 dropped T-B and the D8 floor;
  - D12 break-even cost is **unavailable**, because it requires T-B.
- **Missed check:** P4 never checked how D7b treats `holdout` folds.

## 4. Attempt history

| Attempt | CAMPAIGN record (K-PIT seq) | Outcome |
|---|---|---|
| 1 | `523763100f9d…41e8` (11) | RUN_INCOMPLETE: P-Q realization invariant (STD_X_RANK double lag; fixed by erratum PQ-SX) |
| 2 | `eb058bfee1e1…31d3` (16) | RUN_INCOMPLETE: EVENT_SIMPLE grid mismatch (fixed by erratum PQ-EV) |
| 3 | `bc061d8419bc…7b4b` (23) | RUN_INCOMPLETE: early generation stop (transport errors; bounded retry added) |
| 4 | `9e0fedf0ada6…b5ad` (46) | Generation, T-A, ranking and TB_SELECTION complete; T-B infeasible; FORWARDED; T-C not run |

No candidate from attempts 1–3 enters any ranking or decision.

## 5. Errata and amendments issued during Campaign-3

- **P4-A2 (+E1, E2):** DeepSeek generator.
- **P5-E1:** call/spec accounting.
- **P5-E2:** T-B dropped; IS top-1 forwarded.
- **P6-E1 … P6-E6:**
  - P6-E2: panel_of reuse;
  - P6-E3: probe correction;
  - P6-E5: cold resume;
  - P6-E6: forwarding step C3-F.
- **Dataset v1.2 P-Q errata:** PQ-SX (STD_X_RANK lag, V=0 positivity, clean-box
  tolerance; recertified) and PQ-EV (event grid).
- **K-PIT governance:** hash-only dataset reference (no literal dataset id in any record).

## 6. Engineering findings for future campaigns (not defects of the disposition)

1. **Single-core forwarding:** `campaign3_forward` builds the assembly with
   `screen_workers=1`, so the cold path rebuilds 99 panels single-core (≈ 50 min). It
   should expose `--screen-workers`.
2. **Repeated rebuilds:** cold resume and forwarding re-derive panels to verify claims.
   Persisting panel and claim hashes with the exports at T-A time would remove the
   rebuild.
3. **Artifact location:**
   - The attempt-4 generation snapshot names `<attempt>/diagnostics`, but the launcher
     persisted diagnostics and exports under the base identity directory (`76a2c212…`).
   - Artifacts are content-addressed and C3-F verified each one by hash. The resolved
     directory is recorded in the FORWARDING payload.
   - Artifacts are **left in place**, because moving them would break the recorded
     path.
4. **Vectorization:** `build_event_inputs` should be vectorized.
5. **Desktop runs:** full desktop runs need a cross-architecture equality test first.
6. **Suite timing:** the full suite (~15 min) is near the Phase-5A 900 s bound.
7. **Process lessons:**
   - Errata to a hash-bound document must be separate documents. Appending P5-E2 to the
     bound P5 file broke its hash, and `verify_precommitment` correctly refused.
   - Every frozen-design dependency on a sealed module must be checked against that
     module before freeze. That check would have caught the T-B defect.
8. **Generator feedback:** the near-universal TS-scenario failure is the main
   substantive input for the next campaign's generator feedback and design.

## 7. Evidence categories (CLAUDE.md §8)

- **IMPLEMENTED and independently reviewed:**
  - all Campaign-3 code (C3-D, C3-X, C3-R, C3-S, C3-L R1–R8, C3-F R0–R2);
  - each merge records an exact reviewed SHA.
- **EXECUTED (LIVE-RECORDED):**
  - attempt-4 generation (live DeepSeek calls, network-guarded to the DeepSeek
    allowlist);
  - T-A IS evaluation, ranking, TB_SELECTION and FORWARDING.
- **NOT RUN:** T-B (infeasible) and T-C (not authorized).
- **NO CONFIRMATORY CERTIFICATION** of any factor. "Forwarded" means only that a
  hypothesis met the P5-E2 selection rule. It is not evidence that the factor works.
- **Unchanged boundaries:** all pre-existing NOT CERTIFIED boundaries are unchanged.

## 8. Barrier regression at closeout

- Master `165ea1e` + this document.
- Full suite: **6787 passed, 1 skipped** in 805 s (exit 0); credentials scrubbed; no network.
