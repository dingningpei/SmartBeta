# Campaign-4 M1-d — role of terminal stress (TS) in selection and confirmation: design draft (nothing frozen)

- **Status:** planner draft, 2026-10-14. Requested by the user, who asked for the three
  directions with pros, cons and literature.
- **Inputs:**
  - `docs/campaign4_m0_2_result.md`: the pipeline is plausible;
  - `docs/campaign4_m0_4_result.md`: TS is structural, with a near factor-independent
    drag of about 0.5–0.8% per month;
  - the frozen rule being reviewed: P7 decision 4 (`docs/campaign3_p7_reserve_path_design.md`)
    plus P5 D9. Confirmation requires B **and** L under **all** T0/TS × K0/K1, and IS
    ranking uses the minimum over those scenarios.
- **Disclosure, carried by any decision taken from this draft:**
  - this review was triggered by Campaign-3 and M0 IS evidence;
  - the reserve has never been opened, and every Campaign-4 rule will be frozen before
    any reserve or forward read;
  - so confirmatory error control is unaffected. What changes is only *which*
    hypotheses can pass.

## 0. What TS is, and why it fails everything

- **Current TS** (frozen D7b): every member whose listing ends inside its holding window
  gets τ = −1 if it is in the long leg (a total loss) and τ = +1 if it is in the short
  leg (about +100% against the short). This applies **whatever the reason for the
  termination**.
- **M0-4 measured:**
  - about 0.34% of TOP_1000 terminates per holding month;
  - legs hold that share regardless of the factor;
  - so TS subtracts about `a_long + a_short` ≈ 0.5–0.8% per month from any long-short;
  - the best replicated in-period factor (1-month reversal) earns +0.43% per month net
    under T0.
- **What we cannot observe:** Dataset v1 (Tiingo) records *that* a listing ended, not
  *why* (acquisition, going private, exchange move, failure) nor the post-delisting
  value.

## 1. Literature

| Source | Finding | Verification status |
|---|---|---|
| Shumway (1997), *The Delisting Bias in CRSP Data*, JF 52(1) 327–340 | CRSP is missing correct delisting returns for most stocks delisted for **negative (performance) reasons**. Using OTC prices, the omitted returns are **large**. Such delistings are generally surprises | abstract (IDEAS/RePEc) |
| Shumway & Warther (1999), *The Delisting Bias in CRSP's Nasdaq Data and Its Implications for the Size Effect*, JF 54(6) 2361–2379 | missing performance-delisting returns on Nasdaq are large and negative; **a corrected return of −55%** removes the bias; after correction, there is no evidence that a Nasdaq size effect ever existed | abstract |
| Standard practice, as documented by the SAFE Research Datacenter, *Information on the preprocessed CRSP data set* (2025) | when the CRSP delisting return is missing: impute **−30% for NYSE/Amex** and **−55% for Nasdaq** for the relevant (performance-related) delisting codes, per Shumway (1997) and Shumway & Warther (1999). Delisting returns **can be positive**, for example in an acquisition at a premium | secondary documentation (read); the −30% value was not read in the 1997 paper itself |
| Hou, Xue & Zhang (2020), *Replicating Anomalies*, RFS | lists delisting bias among the replication issues for anomaly research | bibliographic only; the exact procedure was not read |

**Two points from the literature for our case:**
1. **No paper treats every termination as a −100% loss.** Performance delistings get
   −30% / −55%, and merger delistings are typically about 0 or positive.
2. **The direction matters.** The bias the literature corrects is *upward* bias in long
   positions in distress-prone stocks, such as small or illiquid names. That is exactly
   the survivorship risk our TS was meant to guard against.

## 2. The three directions

### A. Rank on T0 (and L); TS reported, not gated

- **Rule:**
  - IS ranking = the minimum over T0 × K0/K1 of the L-envelope EWC t;
  - confirmation = B and L under T0 × K0/K1;
  - TS and its gap are always reported, never gated.
- **Pros:**
  - the simplest change, with no new code in the evaluation path;
  - it uses only data we actually observe;
  - it maximizes power.
- **Cons:**
  - T0 implicitly assumes a **0% post-delisting return** for every termination;
  - that is exactly the assumption Shumway shows is biased for performance delistings;
  - a factor that systematically longs failing firms would be **overstated**, and nothing
    would stop it;
  - it gives up the protection TS was built for.
- **Literature fit:** it contradicts the standard correction practice.

### B. Calibrated terminal value in place of ±100%

- **Rule:** replace τ = ∓1 with a literature value d for affected members. Two variants:
  - **B1, adversarial bound (recommended form):**
    - τ_long = d and τ_short = −d, with d = −0.55 (the Nasdaq value, applied to all
      exchanges as the conservative choice, because the termination type and the
      exchange-at-termination are not certified in v1);
    - expected drag ≈ 0.55 · (a_long + a_short) ≈ **0.3–0.4% per month** at the M0-4
      rates;
    - an alternative d = −0.30 is NYSE/Amex-only and less conservative.
  - **B2, economic point estimate:**
    - the same d for the stock in either leg, so a short leg *gains* when a short-held
      firm fails;
    - the drag depends on `a_long − a_short`, so it penalizes only factors that
      **tilt toward** terminating names.
- **Pros:**
  - grounded in the standard delisting-bias correction;
  - B1 keeps a worst-case flavour while staying survivable: KF-REV1's +0.43% per month
    would keep roughly +0.2% under B1 (d = −0.55), by M0-4 arithmetic;
  - B2 targets exactly the bias the literature identifies.
- **Cons:**
  - **The calibration is borrowed.** The −30%/−55% values were estimated for
    *performance* delistings in CRSP-era data, not for TOP_1000 terminations of
    2016–2026, which are mostly not performance-related.
  - Applying them to every termination is conservative for acquisitions, but it is an
    assumption, not a measurement.
  - **Implementation cost.**
    - D7b and P-Q are pinned. The new scenario needs a **new module** that recomputes
      leg values from the D7b scenario records. M0-4 proved this decomposition is exact.
    - The **L envelope under a new τ** needs a P-Q amendment. The P-Q v2.1 §9 monotonicity
      argument (1 + τ ≥ 0) carries over unchanged, but it must be implemented and tested
      to reproduce T0/TS exactly at τ ∈ {0, ∓1}.
    - The confirmation executor (`reserve_chain/confirm.py`) is a C3-bound file, so
      Campaign-4 needs its own confirmation wrapper.
- **Literature fit:** direct.

### C. Keep TS, but only at confirmation

- **Rule:** rank on T0/L; at confirmation, require B and L under T0 **and** TS (the P7
  rule as frozen).
- **Pros:**
  - no rule is relaxed at the confirmatory step;
  - no new code is needed.
- **Cons:**
  - M0-4 shows TS subtracts more than any replicated premium, so **confirmation is
    near-certain to fail** for any realistic factor;
  - it moves the impossibility to the last step and spends the single reserve use on a
    test with near-zero power.
  - **power arithmetic:** at the reserve's 37 periods, a factor needs a TS-net Sharpe of
    about 1 for 50% power. With a 0.6% per month drag, that means a T0 premium of about
    1.2–1.5% per month — far above any replicated factor.
- **Literature fit:** none. The ±100% stress has no literature counterpart.

## 3. Planner recommendation

**B1 with d = −0.55**, used consistently in **both** ranking and confirmation:

- **Gate:** min over {T0, B1-stress} × K0/K1 of the L t (ranking) and B ∧ L under
  {T0, B1} × K0/K1 (confirmation).
- **Reported only, never gated:** TS (±100%) and B2.
- **Why not A:** it removes the survivorship protection the project has deliberately kept
  since Gate 0B, and the literature says that protection matters.
- **Why not C:** a gate that no realistic factor can pass is not a test.
- **Why B1 over B2:** it is adversarial in the same spirit as TS, so it does not let a
  factor *benefit* from an assumed termination value. The literature value bounds the
  severity without assuming the termination type.

## 4. Consequences and costs of the recommendation

1. **P-Q amendment (`v2.5`, scope: a parameterized terminal scenario τ_d):**
   - design, then proof carry-over, then an implementation as new modules;
   - **an equivalence barrier:** at d = −1 it must reproduce TS byte for byte, and at
     d = 0 it must reproduce T0;
   - this is the main cost (about 3–4 Pi tasks).
2. **A Campaign-4 ranking and confirmation wrapper:** new code. C3-bound files are not
   touched.
3. **The frozen P7 decision 4 is superseded for Campaign-4 only.** Campaign-3's records
   (TB_SELECTION, FORWARDING `ed358470`) stay as recorded, under their own rules.
4. **The power statement is recomputed for the new gate** and carried verbatim.
5. **Re-diagnosis:** M0-2's six factors and C3's IS ranking can be re-reported under B1
   (IS, descriptive) once v2.5 exists, so the effect of the rule is visible before any
   Campaign-4 hypothesis is frozen.

## 5. Decisions needed

| ID | Question | Recommendation |
|---|---|---|
| D-M1d-1 | direction A / B1 / B2 / C | **B1** |
| D-M1d-2 | d value | **−0.55** (conservative, all exchanges); alternative −0.30 |
| D-M1d-3 | proceed to the P-Q v2.5 amendment design (document-only first) | yes |

## Sources

- Shumway (1997), JF: https://ideas.repec.org/a/bla/jfinan/v52y1997i1p327-40.html
- Shumway & Warther (1999), JF: https://ideas.repec.org/a/bla/jfinan/v54y1999i6p2361-2379.html
- SAFE Research Datacenter, *Information on the preprocessed CRSP data set* (2025):
  https://datacenter.safe-frankfurt.de/documents/Data_Description_CRSP_Shumway_20250317.pdf
- Hou, Xue & Zhang (2020), *Replicating Anomalies*, RFS; NBER w23394:
  https://www.nber.org/papers/w23394.pdf
