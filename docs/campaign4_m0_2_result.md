# Campaign-4 M0-2 — known-factor replication: result (IS only; descriptive)

- **Authority:** `worker_tasks/campaign4_m0/campaign4-m0-plan.md` (frozen 2026-10-14).
  The §4 reading rules were precommitted before any result existed.
- **Code:** master `ace8d8d`, which merges C4M0-2:
  - `6e385aa`, then `06ac03f` (SR annualization fixed: monthly 12, not the C3
    declaration's 252);
  - then `2d25b61` (the script's policy identity is a real SHA-256).
  - Each merge was independently reviewed, with the tests re-run.
- **Run:** by the planner, `scrubrun.sh` (credentials scrubbed), 2026-10-14, wall time
  1,293 s.
  - **K-PIT:** a **disposable copy** was used, plus an empty scratch run root. The
    production K-PIT SHA-256 was `29f0cabc…3a35fe` before and after, so it is unchanged.
  - **Folds:** IS only (`[2016-01-29, 2020-07-31)`, 54 formations). No other fold was
    opened.
- **Report:** `known_factors.json`, content SHA-256
  `01ee520a620e4be54f7b46ba87aa48b8d0e4931b56767950bbf41d2ed4a1ab74`. It is held in the
  planner scratchpad, not in the repository data tiers.
- **Barrier at `ace8d8d`:** full suite 6800 passed, 1 skipped, 822 s solo (exit 0).
- **Evidence category:**
  - EXECUTED on real IS data, descriptive;
  - no claim;
  - IS is the generator-visible tier.

## 1. Results (B = observed estimand; L = P-Q lower envelope; net of 10 bps; TOP_1000, 5 groups, h = 21)

| Factor | B-T0 EWC t | B-T0 ann. SR | L-T0 EWC t | B-TS EWC t | L-TS EWC t | Group monotonicity (T0) | One-way turnover (B) |
|---|---|---|---|---|---|---|---|
| KF-MOM12 (12-1 momentum) | 0.04 | 0.02 | −0.26 | −1.09 | −1.35 | 0.3 | 0.40 |
| KF-MOM6 (6-1 momentum) | −0.23 | −0.08 | −0.59 | −1.95 | −2.17 | −0.4 | 0.51 |
| KF-REV1 (1-month reversal) | **1.43** | 0.35 | 1.04 | 0.14 | −0.16 | **1.0** | 0.78 |
| KF-VSMOM (volatility-scaled momentum) | −0.03 | −0.01 | −0.27 | −1.07 | −1.28 | 0.6 | 0.42 |
| KF-LVOL (low volatility) | −1.42 | −0.49 | −1.75 | −2.52 | −2.83 | **−1.0** | 0.15 |
| KF-EAR (C3 positive control), K0 | −0.27 | −0.12 | −1.70 | −1.23 | −2.63 | −0.3 | 0.34 |

- **Coverage:** 54/54 periods assessed in every cell.
- **K1 cells:** KF-EAR's K1 cells are within 0.02 of K0.

## 2. Reference comparison R (Ken French Data Library; IS holding months 2016-02 … 2020-07, n = 54)

| Our series (B-T0) | Reference | Pearson | Our mean / month | Reference mean / month |
|---|---|---|---|---|
| KF-MOM12 | Mom | **0.92** | 0.02% | 0.09% |
| KF-REV1 | ST_Rev | **0.87** | 0.43% | 0.52% |

- **Alignment:** formation month + 1 is the holding month.
- **Universe difference:** the reference is all-CRSP, value-weighted 2×3; ours is
  TOP_1000, equal-weight quintiles, net of cost.
- **Firewall:** only IS months were read from the reference files (see their
  `PROVENANCE.md`).
- **Evidence category:** ECONOMIC-DEFINITION REPLICATION (CLAUDE.md §8), not exact-source
  replication.

## 3. Reading (frozen §4 rules)

| Condition | Value | Triggered |
|---|---|---|
| Direction-oracle test | passes (synthetic; t > 10 / < −10) | no |
| ≥ 3 of {MOM12, MOM6, REV1, LVOL} with B-T0 EWC t < −1.65 | 0 of 4 | no |
| Reference correlation < 0.30 | 0.92 and 0.87 | no |

**Reading: PIPELINE_PLAUSIBLE.**
- The pipeline reproduces the published momentum and reversal factors month by month.
- The weak or negative textbook results are **the period's own behaviour**:
  - the reference momentum averaged only 0.09% per month over these months;
  - the low-volatility profile is monotone **against** the literature in 2016–2020;
  - neither reflects a pipeline or convention defect.

## 4. What this changes in the Campaign-3 diagnosis (planner interpretation; to be tested by M0-1/3/4)

1. **The C3 "systematically negative" pattern is largely explained without a generator
   defect.**
   - *Comparison:* the textbook factors' L-T0 EWC t values have median about −0.4, and
     C3's 32 comparable candidates have median −0.81. They are in the same region.
   - *Two forces push both sets down:*
     - (a) the period: low-volatility/defensive tilts lost, and momentum was flat;
     - (b) the L envelope, which lowers t by about 0.3–1.4 relative to B.
   - *The earlier hypothesis* that "the generator inverted directions" is now a minor
     candidate, not the leading one. **M0-1 stays scheduled** but is lower priority.
2. **TS dominates every ranking.** Moving from T0 to TS costs every factor about
   0.4–1.7 in t, including momentum, whose long leg rarely delists. Under the C3 ranking
   statistic (the minimum over scenarios of the L t), even the one factor that works in
   this period (KF-REV1, B-T0 t 1.43) scores −0.16. **M0-4 (TS decomposition) becomes the
   top priority.** A stress that sinks a replicated factor needs explaining before it can
   gate selection.
3. **The positive control failed in IS.** EAR drift, B-T0 t −0.27, is consistent with the
   literature's finding that post-earnings drift has decayed in large caps. It is a
   period/universe fact, not a pipeline fault, because the pipeline itself is replicated
   (R). A future positive control should be a factor with a known **in-period** effect;
   KF-REV1 is the candidate.
4. **A latent C3 configuration defect, with no effect.** `C3_PERIODS_PER_YEAR = 252` for
   a monthly series. C3's primary metric (`turnover_cost_adjusted` = the net long-short
   mean and its t) and all its ranking and forwarding statistics (EWC t) do not use it,
   so the C3 disposition is unaffected. Any future annualized metric must use 12.

## 5. Next (proposed; each needs its own spec freeze)

1. **M0-4 TS decomposition**, next: per period and leg, the INCOMPLETE terminal legs, their
   τ = ∓1 penalty, and the share of the T0–TS gap they explain. Run on the six known
   factors and the C3 IS candidates.
2. **M0-3 common exposure:** the C3 candidates' correlation with KF-LVOL / KF-MOM12 /
   KF-REV1. This tests explanation 4.1(a) directly.
3. **M0-1 direction audit:** lower priority.
