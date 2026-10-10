# Campaign-3 P7 — reserve-fold data path design (FROZEN design 2026-10-09; implementation spec not yet written)

- **Status:** planner draft, 2026-10-09 (user: "先起草设计").
- **Purpose:** produce, exactly once and only inside an authorized confirmation
  execution, the reserve-period inputs a Campaign-3 confirmation needs. The inputs are
  the net long-short series for the forwarded hypothesis (m = 1) plus its P-Q v2.3
  sensitivity treatment.

## 1. What the reserve currently holds (sealed; not read by this design)

| Path | Content |
|---|---|
| `reserve_sealed/n-f874d3ecb61374d4/` | D3 reserve observations, obs_flags, ca_events (registered by D8 as an ARTIFACT) |
| `reserve_sealed/q-5d1028655f8c82e1/` | D5 reserve QC day flags |
| `reserve_sealed/edgar-f0d0ee1b…/` | v1.3 reserve events (K ≥ 2023-07-03) |

**Missing** for a confirmation:
- reserve-period D4 identity continuation;
- the D6 universe (TOP_1000) at reserve formations;
- D7a returns and labels;
- D7b observed evaluation;
- D7c trusted inputs (factor inputs with lookback crossing the boundary);
- the P-Q overlay / sources for reserve flags.

## 2. Principles

1. **One access.** The real reserve derivation runs **once**, inside the authorized
   confirmation execution. It is preceded by a K-PIT `ACCESS` record (Phase-10
   §5.2) naming the hypothesis, the procedure and the exact code hashes. No
   exploratory or "check it works" reads of real reserve data are allowed.
2. **Frozen code, built and tested beforehand.** Every reserve derivation reuses the
   frozen development D-chain code, parameterized by fold.
   - It is certified on **synthetic reserve-shaped fixtures** and on a
     **development-period rehearsal**: running the reserve path on the
     development sealed fold, as if it were the reserve, must reproduce the
     development D7a/D7c outputs byte-for-byte for those dates.
3. **Lookback across the boundary.** Factor inputs at reserve formations need up to 504
   sessions of history, which mostly comes from development data.
   - That is allowed: development data is not sealed for this purpose.
   - Every input row dated ≥ 2023-07-03 comes only from the reserve package.
4. **P-Q on the reserve.** The same P-Q v2.3 machinery runs on the reserve fold:
   - reserve D5 flags are classified with the §8A source rules;
   - UNKNOWN_RESERVE as a *firewall* cause no longer applies inside the authorized
     confirmation;
   - the confirmation uses the T0/TS × K0/K1 rule;
   - the L-lower-envelope requirement for confirmation must be precommitted in P5:
     B only, or B and L.
5. **Output minimality.** The confirmation consumes **only** the forwarded hypothesis's
   per-period series. No other hypothesis, statistic or diagnostic is computed on the
   reserve.

## 3. Components (new; the implementation spec comes after the freeze)

| Component | Work |
|---|---|
| `dataset_v1/reserve_chain.py` | fold-parameterized wrappers over the frozen D4/D6/D7a/D7c builders. Development → reserve continuation. Hash-bound to the D8 reserve ARTIFACT record; refuses unless an authorized `ACCESS` record exists in the supplied K-PIT prefix |
| P-Q reserve sources | the reserve-fold source-set loader (the `pqv2_sources` pattern) and the reserve event-layer loader (`reserve_sealed/edgar-*`), unlocked only by the same `ACCESS` gate |
| Rehearsal harness | the development-sealed-fold rehearsal (§2.2) as a certification test |
| Confirmation executor | ACCESS → derive → evaluate → `run_inference` (the admitted EWC procedure) → assessment, all in a single process, with write-ahead persistence |

## 4. Open questions for the freeze

1. **D7a reserve labels:** the reserve end C must leave h = 21 sessions for the last
   formation's label. The last reserve formation is therefore ≤ C − 21 sessions. This
   is a binding precommitted rule.
2. **Universe continuity:** the TOP_1000 membership at reserve formations uses the same
   D6 rule on reserve liquidity data (the D3 reserve observations).
3. **Gate re-use:** v1.3 reserve events passed the same E-3 build. Should their
   identity/time causes simply carry over? (Proposed: yes; the same M1b causes apply.)
4. **Confirmation estimand:** B (observed) net long-short only, or B and the P-Q L
   envelope both required? (Proposed: both, consistent with development, so a
   data-sensitivity flip blocks confirmation.)
5. **Timing:** P7 is implemented and rehearsed **before** the Campaign-3 P5 freeze, so
   the precommitment can bind exact code hashes.

## Freeze decisions (user, 2026-10-09: "按照你的建议 D4用claude")

1. **The last reserve formation:** ≤ C − 21 trusted sessions, so every reserve label is
   complete.
2. **Universe continuity:** the D6 TOP_1000 rule is applied unchanged to the reserve D3
   liquidity data.
3. **v1.3 reserve events:** the same M1b causes carry over.
4. **Confirmation estimand:** **both** B (observed net long-short) and the P-Q L lower
   envelope must pass, under all T0/TS × K0/K1 combinations, using the admitted
   `EWC_T23_ONE_SIDED`.
5. **Timing:** P7 is implemented and rehearsed (the dev-sealed-fold byte-equality
   rehearsal) **before** the Campaign-3 P5 freeze.
