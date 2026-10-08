# Campaign-2 precommitment — P-Q v2.1 pipeline validation (FROZEN 2026-10-08, user-approved option (a); not registered)

- **Status:** FROZEN (user approval 2026-10-08). Its SHA-256 is bound by
  `campaign2_id`. Nothing here authorizes K-PIT writes or execution.
- **Bound authorities:**
  - P-Q v2.1 (methodology `66772bb1…5d1e`, CS `432de097…`, ED `65bd7d13…`, CE
    `605fec93…`);
  - software evidence `docs/discovery_dataset_v1_2_pq_v2_1_software_barrier.md`
    (`7b28231f…`);
  - admission evidence `docs/discovery_dataset_v1_2_pq_v2_1_admission.md`
    (`57360355…`);
  - Dataset v1.2 `a065493d…`, overlay `pq-59c3bd7abe08eef5`, D7c `ti-aa23fabd3db4e595`,
    D7a `r-34565f074d64ccda`.

## 1. Purpose (precommitted before any outcome)

- **Primary purpose: operational validation.** It answers whether the certified P-Q v2.1
  pipeline yields **assessable** development dispositions on the admitted real data.
- **Not an alpha test.** By the precommitted power analysis (pure math; frozen screen;
  54 IS periods; c = 2.45), the screen has about 50% power only at a true annual net
  Sharpe of about 1.2.
  - **A FAIL or null is declared uninterpretable as evidence of no alpha.**
  - A PASS is a development-screen result only. It is never a confirmation and grants no
    reserve or holdout use.

## 2. Data re-use disclosure (binding)

- The 7 hypotheses and the IS/OOS folds are the same as Campaign-1's.
- **Planner audit, 2026-10-08, structural only, no values printed:**
  - Campaign-1's `report.json` and `report.md` expose no outcome statistics; the only
    number is the precommitted critical value;
  - but its `ledger.jsonl` contains IS classification floats (`economic_b_t0`,
    `economic_b_ts`, `economic_a_*`). Those B-cell values exist in machine-readable
    form, and it cannot be certified that they were never viewed.
- **Therefore Campaign-2 is not an independent test** of these hypotheses. Its results
  must always be reported together with this disclosure.

## 3. Design

- **Hypotheses:** C1-H1…H7 definitions, unchanged. These are the only CE-certified
  expressions. Identities use a new namespace:
  `campaign2_hypothesis_id = H("campaign2", definition_hash)`.
- **Family:** m = 7, family α = 0.05, c = Φ⁻¹(1 − 0.05/7) = 2.44999766060273.
- **Construction:** TOP_500, h = 21, 5 groups, 10 bps cost, 1% winsorization, sign +1,
  metric `turnover_cost_adjusted` (NET).
- **Folds:**
  - IS `[2016-01-29, 2020-07-31)` (54 formations);
  - OOS `[2020-07-31, 2022-01-31)` (18 formations);
  - DEVELOPMENT_SEALED_FOLD **sealed**; reserve and final holdout untouched.
- **Estimands per fold and scenario X ∈ {T0, TS}:** B-X (the D7b/Phase-7 observed
  estimator) and L-X (the P-Q v2.1 lower envelope; same-period invariant).

## 4. Screen (`C2-DEV-SCREEN-v1`)

- **IS rule per cell (B-T0, B-TS, L-T0, L-TS):** the economic HAC t ≥ c with ≥ 24
  available periods. The B cells use the D7b/Phase-7 NW t; the L cells use the P-Q v2.1 L
  statistic (the same Phase-7 estimator on B's period set).
  - L NOT_ASSESSED, or fewer periods → `passed = None`.
- **OOS rule per cell:** the arithmetic mean of the net long-short over the available
  periods > 0, with ≥ 12 periods. For the L cells the mean is over B's periods; any
  NOT_ASSESSED period → `passed = None`.
- **Classification:** `classify_pqv2` per fold and per scenario.

## 5. Disposition per hypothesis (mirrors Campaign-1's precedence)

The required set is {IS, OOS} × {T0, TS}: four classifications.

1. **`NOT_ASSESSED`** if any required classification is NOT_ASSESSED, or any required
   screen is `None`. Reasons are collected, with the full P-Q v2.1 cause sets.
2. Else **`DEVELOPMENT_SCREEN_FAIL`** if any required classification is
   DEVELOPMENT_SCREEN_FAIL, QUARANTINE_SENSITIVE or INVARIANT_VIOLATION_FAIL_CLOSED.
   Reasons are collected; INVARIANT_VIOLATION is reported as fail-closed and is not an
   error.
3. Else **`DEVELOPMENT_SCREEN_PASS`.** This requires CORRECTION_SENSITIVITY_SCREEN_PASS
   in **both T0 and TS** for **both IS and OOS** (user decision, 2026-10-08: T0 and TS
   must both pass).

**Not carried over:** Campaign-1's O2 guard, a P-Q v1 structural-residual construct. P-Q
v2.1 exact dependency handles the H6/H7 numerical denominator directly.

## 6. Precommitted operational endpoints (the primary results)

- **Assessability rate:** the number of the 28 required classifications (7 × 2 × 2) that
  are not NOT_ASSESSED, and the number of the 7 hypotheses with a non-NOT_ASSESSED
  disposition.
- **Attribution:** for every NOT_ASSESSED classification, the complete persisted cause
  set.
- **Integrity:** deterministic re-render; diagnostics write-ahead and round-trip; 0
  reserve or holdout reads; 0 network calls.

## 7. Budget and stop

- **Slots:** one primary attempt per hypothesis, in canonical order (H1…H7). No
  re-attempts.
- **Stop vocabulary:** as in Campaign-1 (C1-LX §8, E3). An infrastructure or invariant
  failure → CAMPAIGN_STOP, and the ledger becomes terminal.
- **Not included:** no TOP_200 / TOP_1000 robustness diagnostics (validation scope).
