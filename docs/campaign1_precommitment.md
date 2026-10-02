# Campaign-1 Precommitment — Dataset v1.2 development factor research (FROZEN; EXECUTION NOT AUTHORIZED)

- **Status:** FROZEN DESIGN. Authorized by the user's "CAMPAIGN-1 DESIGN +
  PRECOMMITMENT" and "CAMPAIGN-1 HOLD RESOLUTION (D-1 a, D-2 i, D-3 asymptotic-normal)",
  on master `7318956`.
- **Empirical execution is NOT authorized.** It requires every prerequisite in §13
  and a separate authorization.
- **No outcome has been observed.** This design was fixed without computing any real
  factor value, return, IC, metric or screen.

## 0. Bindings

| Item | Value |
|---|---|
| Dataset | `discovery_dataset_v1_2`, ADMITTED_FOR_DEVELOPMENT (SAFE RESEARCH CONSUMPTION) |
| Admission document | `docs/discovery_dataset_v1_2_development_admission.md` (7318956), `8c720c29938497aca98716dc317b77d0b68ea8126db33b35459fdf90132129ee` |
| P-Q overlay | `pq-59c3bd7abe08eef5`, `pq_hash` `baa920ddfc52805c70445abfb8650e2969d341ae9383e1b1a0c6a788fcc95f2c` |
| `quarantine_rule_hash` | `df5bec69103eca5ad9196b3342e5013fab30a3b85cdb3a5332b033dcac5db886` |
| R3 identity | `a065493d295883f425d499816a98ac55a03cd67a1086bd7b64249b90fe76b9da` |
| Dataset-v1.2 authority | `9261a55b9b82a5708dded65bfd3083d5a960cfa4375681b1b23390da62fb3ed0` |
| FactorSpec declaration | `docs/campaign1_factorspecs.json`, `f93a218d1e8a5bc07a7e64b52e1ce66be09b6708479d5c76a5f9a2973d6d532c` |
| O1 repair task spec | `worker_tasks/campaign1/task-c1-o1-structural-residual.md`, `7907aa87057c609c0640a7bb9118ebbb413f59213a4c35ac69ff1e0904c8cb6c` |
| Development screen policy | canonical JSON in §8.2, `b7414db54add862dc12711564cdf7e0f4dec1ec07a0eb5b2bff507b81d9e592e` |
| SESOI justification text | §4, `2565ce442a0edc4d4916d2dcb8621a7d9260cb6742bbcf2f6b0f4e5203844493` |

**Status carried forward:**
- Gate-6 v1 = INCONCLUSIVE; Gate-6 data quality = NOT_ASSESSED; research treatment =
  EXPLICITLY QUARANTINED UNDER P-Q.
- Reserve and confirmation: NOT AUTHORIZED.
- G6-v2B = OPEN — OPERATIONALLY PAUSED.

## 1. Scientific objective and claim boundary

**Objective:** test whether a small, precommitted family of economically motivated
price/volume hypotheses carries persistent, economically meaningful cross-sectional
predictive information in the admitted development data. The test is net of costs,
with the expected direction fixed in advance.

**This is exploratory development research.** Campaign-1 can at most establish
**development evidence**. The five levels are kept distinct:
1. **governance-valid:** the attempt was correctly registered, accounted for and
   evaluated;
2. **`DEVELOPMENT_SCREEN_PASS`:** the §8 development screen was satisfied. This is the
   strongest Campaign-1 outcome;
3. **scientifically interesting:** a human judgment, recorded only after Campaign-1
   closes;
4. **confirmation-authorized:** never granted by Campaign-1. It requires Phase-10
   preregistration and a separate authorization;
5. **production-ready:** never claimed.

**Not claimed:**
- confirmation, alpha validity, investability or production readiness;
- the data quality of Gate-6 suspect observations;
- exact rolling-variance arithmetic (O2).

## 2. Architecture (D-1 option a)

- **Bridge only:** every Campaign-1 evaluation runs on the existing development-only
  **D7b/PQB bridge** (`smart_beta/dataset_v1/pq_evaluation.py`, which uses
  `delisting_bridge.py` unmodified).
- **No Phase-7 engine:** Campaign-1 never calls Phase-7 `engine.evaluate`, never
  consumes a Phase-7 final-holdout token, never fabricates an `EvaluationRecord` or
  `DecisionRecord`, and never emits a Phase-8 ACCEPT.
- **Campaign ledger and executor (§12, implementation prerequisite):** a narrow layer
  that mechanically reproduces the applicable frozen Phase-8/9 governance semantics in
  a **distinct identity domain** (Campaign attempts are never registered as Phase-8
  experiments):
  - fixed family, `family_budget_m`, `family_alpha` and fixed-m Bonferroni;
  - first-attempt locking; replay consuming no slot;
  - non-substitutable generation, proposal, experiment and statistical budgets;
  - an append-only ledger; typed, deterministic stop reasons.

## 3. Temporal design (mechanical; frozen)

**Sample-allocation rule:** fixed before any outcome, by formation count only.
- The 90 admitted development formations (monthly last trusted sessions 2016-01-29 …
  2023-06-30; D6 `formations.parquet`, partition DEVELOPMENT) are split in time order
  as **60% / 20% / 20%**: 54 / 18 / 18 formations.
- No cut uses any factor or return information.

| Segment | Formations (inclusive) | n | Phase-7 container (half-open `[start, end)`) | Campaign-1 access |
|---|---|---|---|---|
| **Campaign IS** | 2016-01-29 … 2020-06-30 | 54 | fold role `is`, `[2016-01-29, 2020-07-31)` | read for the screen's IS rule |
| **Campaign OOS** | 2020-07-31 … 2021-12-31 | 18 | fold role `oos`, `[2020-07-31, 2022-01-31)` | read for the screen's OOS directional rule |
| **DEVELOPMENT_SEALED_FOLD** | 2022-01-31 … 2023-06-30 | 18 | fold role `holdout`, `[2022-01-31, 2023-07-03)` | **never read by Campaign-1** |

- **Partition:** `split_rule = "campaign1-is-oos-sealed-v1"`, trusted NYSE calendar.
  `partition_id` = `b4b56dbfc82377930b8bf87a1d8039a69ccbf7d42d5faf234d3bb8cbbcb936fb`
  (Phase-7 `Partition` with the three folds below, no calendar; verified at freeze).
- **Why the `holdout` fold role:** it is used **only as the structural container** for
  the DEVELOPMENT_SEALED_FOLD. D7b and PQB require a holdout fold to exist and refuse
  to evaluate it or any fold overlapping it. No Phase-7 holdout token is ever consumed,
  because `engine.evaluate` is never called.
- **Terminology is not interchangeable:**
  - Campaign IS;
  - Campaign OOS;
  - DEVELOPMENT_SEALED_FOLD (Campaign-internal, untouched);
  - the Phase-7 final holdout (a Phase-7 concept, not used);
  - the Dataset reserve (2023-07-03 … C; sealed, outside development).
- **Labels:** h = 21 forward labels. Labels whose realization window crosses a fold
  boundary are purged (P7-B §8.1 rule, applied by D7b). Labels crossing 2023-07-03 are
  purged (Dataset v1.2).
- **History sufficiency:** the trusted inputs start 2015-01-02, which gives 270 sessions
  before the first formation. That is at least the deepest structural requirement
  (253 sessions, C1-H1/H4/H6). Lookback never mechanically truncates the IS fold.
- **No cut may move after outcomes are seen.**

## 4. Primary estimand (D-2 option i): one EstimandPolicy for the program

| Field | Frozen value |
|---|---|
| `program_id` | `"campaign1:" +` SHA-256 of this file's committed bytes (computed at registration) |
| `estimand_kind` | `MEAN_NET_LONG_SHORT` |
| `horizon` | `21` trusted sessions |
| `construction.n_groups` | `5` (quintiles; long = group 5, short = group 1, under sign +1) |
| `construction.cost_bps` | `10.0`: the Phase-7 cost per unit of membership turnover (`cost_t = 10/10,000 × turnover_t`; Pilot-1A precedent) |
| `construction.winsorization` | `0.01` (per-date 1% / 99% percentile clip; Pilot-1A precedent) |
| `construction.factor_missing_policy` | `"PROPAGATE"` |
| `sesoi` | `0.0025` (net long-short return per 21-session period) |
| `sesoi_justification_hash` | SHA-256 of the exact text below: `2565ce442a0edc4d4916d2dcb8621a7d9260cb6742bbcf2f6b0f4e5203844493` |

SESOI justification text (exact bytes, UTF-8, single line):

> C1 SESOI: 0.0025 net long-short return per 21-session holding period (about 3.0% annualized), the smallest net quintile long-short spread on the D6 top-500 equal-weighted universe, after 10 bps Phase-7 transaction cost per unit of membership turnover, that Campaign-1 treats as economically meaningful; fixed before any Campaign-1 empirical evaluation.

**Fixed evaluation construction (every primary evaluation):**
- universe `top_500`; weighting `EQUAL`;
- `periods_per_year = 12` (monthly formations);
- primary metric `TURNOVER_COST_ADJUSTED`, basis `NET`;
- `FactorSpec.sign = +1` for every hypothesis. The expected economic direction is
  positive after construction.
- **Reversal** is encoded inside the expression by multiplying by the literal −1, which
  is legal Phase-6 semantics. Evaluator sign = −1 is **never** used.

## 5. Hypotheses: 4 economic families, 7 hypotheses (one statistical family)

**Exact semantics** (bridge timing, contract §11.1 and Gate 13):
- Factor row f uses primitives dated ≤ f−1. `lag(x, k)` at row f refers to the
  primitive dated session f−1−k.
- `tri` = `total_return_index` (segment-1 TRI, D7c); `ret` = `daily_total_return`;
  `dv` = `dollar_volume`.
- All arithmetic is Phase-6 semantics: division by zero gives NaN, and a missing input
  propagates.

| ID | Family | Exact FactorSpec expression | Economic meaning (exact window) | Mechanism | Expected direction |
|---|---|---|---|---|---|
| **C1-H1** | F1 momentum | `lag(tri,21) / lag(tri,252)` | compound total return over sessions (f−253, f−22] (231 sessions; skips the latest 21 sessions plus the one-session bridge lag) | intermediate-horizon continuation (underreaction; Jegadeesh–Titman) | + |
| **C1-H2** | F1 momentum | `lag(tri,21) / lag(tri,126)` | compound total return over (f−127, f−22] (105 sessions) | the same mechanism, at a half-year scale | + |
| **C1-H3** | F2 reversal | `(-1) * (tri / lag(tri,21))` | minus the compound total return over (f−22, f−1] (21 sessions) | short-horizon reversal (liquidity provision / overreaction; Jegadeesh 1990, Lehmann 1990) | + (after the −1 encoding) |
| **C1-H4** | F3 volume-confirmed momentum | `standardize(lag(tri,21) / lag(tri,252)) * rank(rolling_mean(dv,21) / rolling_mean(dv,252))` | the centred C1-H1 signal, scaled by the cross-sectional rank (≥ 1) of recent-to-annual mean dollar volume (21 vs 252 sessions to f−1) | trends accompanied by rising trading activity reflect more information flow, so continuation is stronger for high-activity winners and losers. Interaction: sign from momentum, magnitude from activity | + |
| **C1-H5** | F3 volume-confirmed momentum | the same with `lag(tri,126)` | the centred C1-H2 signal × the same activity rank | as C1-H4, at a half-year scale | + |
| **C1-H6** | F4 volatility-scaled momentum | `(lag(tri,21) / lag(tri,252) - 1) / lag(rolling_std(ret,231), 21)` | the C1-H1 return divided by the standard deviation of the 231 daily total returns dated f−252 … f−22 (the same window) | risk-adjusted continuation: the return per unit of the stock's own volatility separates persistent trends from high-variance noise | + |
| **C1-H7** | F4 volatility-scaled momentum | `(lag(tri,21) / lag(tri,126) - 1) / lag(rolling_std(ret,105), 21)` | the C1-H2 return / std of the 105 daily returns in the same window | as C1-H6, at a half-year scale | + |

- **Canonical FactorSpec JSONs and their SHA-256s** are in
  `docs/campaign1_factorspecs.json`:
  - C1-H1 `9b27eb88…037c`; C1-H2 `85a7166b…54fc`; C1-H3 `78a6d454…e99f`;
  - C1-H4 `54b7edf3…1a6f`; C1-H5 `ee5b8ea8…4f26`; C1-H6 `42605a13…16a1`;
  - C1-H7 `32678dfd…66b0`.
  - Every spec: frequency DAILY, missing policy PROPAGATE, sign +1, declared input
    lookback 0.
- **Parameter grid (finite and frozen):**
  - **Momentum formation length** ∈ {252, 126} sessions (one year, half year). These
    are the two canonical momentum time scales.
  - **Skip** = 21 sessions (one month, the classic short-term-reversal exclusion), the
    same for all momentum-based hypotheses.
  - **Reversal window** = 21 sessions (one month), matching the monthly holding period.
  - **Volume activity ratio** = 21-session over 252-session mean dollar volume (recent
    month vs trailing year), one setting only.
  - **Volatility window** = exactly the momentum window. This is not a free parameter.
- **Forbidden variants** (outside Campaign-1; consuming no budget, not runnable):
  - any other lookback, skip, window or ratio;
  - evaluator sign −1, or flipping the expected sign;
  - additive or other interaction forms; other volume transforms;
  - any lagged-holding or overlapping-horizon variant;
  - any hypothesis added after execution begins.
- **Observed opposite sign** is evidence **against** the stated hypothesis, never a
  reason to reinterpret it.
- **Out of scope, as future hypotheses** (not expressible without changing the
  language, which is not allowed): `OUT_OF_SCOPE_FUTURE_HYPOTHESIS`:
  - turnover or share-based measures (shares outstanding are not an admitted input);
  - intraday or overnight decomposition;
  - fundamentals;
  - idiosyncratic volatility relative to a market model;
  - 52-week-high price proximity (needs raw close levels, which are not an admitted
    input).

## 6. Universes

- **Primary decision universe:** D6 **top-500** only.
- **Robustness universes:** top-200 and top-1000. They are **descriptive diagnostics**:
  - they are run for every hypothesis, pass or fail, on IS and OOS, with all four P-Q
    cells;
  - they never produce a screen verdict or disposition;
  - they never rescue a failed top-500 screen, never become alternate primary
    estimands, and consume no statistical slot.
- **Multiplicity basis:** under Phase-8 semantics, a statistical attempt is the first
  *admissible judgment* of a new experiment identity (plan §7.1). Diagnostics are never
  judged, so this is not a loophole. They have their own non-substitutable diagnostic
  evaluation budget (§10).

## 7. Metrics

- **Primary (one per hypothesis, identical for all):** net long-short mean
  (`TURNOVER_COST_ADJUSTED`), via PQB.
  - **A cells:** the mean and t-statistic come from PQC `gap_aware_mean_tstat` (original
    formation index, gaps not compressed).
  - **B cells:** the unmodified D7b / Phase-7 values.
- **Secondary diagnostics** (reported, never substituting for the primary):
  - gross long-short mean;
  - mean IC and rank-IC (per-period, with gap-aware t in A);
  - turnover and cost series; Sharpe (arithmetic over available periods);
  - support counts; the CS-3 complement statistics.
- **Maximum drawdown** follows Q9.4: NOT_ASSESSED across gaps.

## 8. Development screen and multiplicity (D-3)

### 8.1 Statistical rule

- **Statistical family:** one family containing all 7 hypotheses.
  - `family_budget_m = 7`; `family_alpha = 0.05`, declared here because Phase-8 freezes
    no global value and requires per-family predeclaration;
  - procedure `FIXED_M_BONFERRONI`; `budget_exhaustion = GOVERNANCE_FAILURE`.
- **Per-test level:** αᵢ = 0.05 / 7 = 0.0071428571…
- **Critical value:** c = Φ⁻¹(1 − αᵢ) = **2.44999766060273** (`scipy.stats.norm.ppf`,
  float64). The comparison is **one-sided** in the expected positive direction, using
  the **asymptotic standard normal**.
- **This is not a finite-sample p-value** and does not use Student-t. An asymptotic
  one-sided normal p-value may be reported, but the critical-value comparison is
  authoritative.

### 8.2 Screen policy (canonical JSON; SHA-256 `b7414db5…592e`)

```json
{"basis":"NET","cells":["ORIGINAL-T0","ORIGINAL-TS","QUARANTINED-T0","QUARANTINED-TS"],"direction":"POSITIVE","estimand":"MEAN_NET_LONG_SHORT","is_rule":{"comparison":">=","critical_value_formula":"Phi^-1(1 - family_alpha/family_budget_m)","distribution":"ASYMPTOTIC_STANDARD_NORMAL_ONE_SIDED","family_alpha":0.05,"family_budget_m":7,"minimum_available_periods":24,"statistic":"economic HAC t of the net long-short mean (A cells: PQC gap_aware_mean_tstat; B cells: unmodified D7b/Phase-7 NW t)"},"metric_key":"turnover_cost_adjusted","not_assessed":"any required statistic NOT_ASSESSED or below minimum periods -> screen verdict None (NOT_ASSESSED); DEVELOPMENT_SCREEN_PASS impossible","oos_rule":{"comparison":">","minimum_available_periods":12,"statistic":"arithmetic mean of available net long-short periods","threshold":0.0},"pass_requires":"IS rule in all four cells AND OOS rule in all four cells AND both fold PQ records not NOT_ASSESSED, not QUARANTINE_SENSITIVE, DELISTING NOT_SENSITIVE in A and B AND O2 guard not triggered","screen_id":"C1-DEV-SCREEN-v1"}
```

**Per-cell screen verdicts**, handed to PQB `classify_pq` (fold-specific
`PQScreenOutcome`s bound to `screen_id` and `screen_policy_hash`):
- **IS run:** `passed = (t_econ ≥ c)` when the cell's primary t is assessed and the
  available periods are ≥ 24; otherwise `passed = None`.
- **OOS run:** `passed = (mean_econ > 0)` when there are ≥ 12 available periods;
  otherwise `None`.
- The minimums use the Phase-8 `minimum_n_obs` concept, set here at 24 (two years of
  monthly observations, ≥ 4 × the fixed NW lag L = 6) and 12 (one year).

**`DEVELOPMENT_SCREEN_PASS` iff all of these hold:**
1. the IS rule passes in all four cells;
2. the OOS rule passes in all four cells;
3. the IS and OOS PQB records are not NOT_ASSESSED and not QUARANTINE_SENSITIVE
   (κ = 2, rules Q11);
4. DELISTING is NOT_SENSITIVE in A and in B (§9.6, both folds);
5. the O2 guard (§11) is not triggered.

**OOS** is a directional validation condition only. It never tunes parameters,
horizon, universe, sign, metric, costs or grouping, and no OOS significance is
required.

### 8.3 Development dispositions (no Phase-8 ACCEPT exists in Campaign-1)

| Disposition | Condition (first match wins) |
|---|---|
| `NOT_ASSESSED(<reason>)` | binding or identity failure; PQB `NOT_ASSESSED`; any required statistic NOT_ASSESSED; support below the minimum; O2 guard; a screen verdict `None` |
| `DEVELOPMENT_SCREEN_FAIL(<reason>)` | `QUARANTINE_SENSITIVE`, or `DELISTING_SENSITIVE`, or `IS_THRESHOLD_NOT_MET`, or `OOS_DIRECTION_NOT_MET` (all applicable reasons recorded) |
| `DEVELOPMENT_SCREEN_PASS` | §8.2 satisfied |

A pass means **only** that the precommitted development screen was satisfied. It does
not mean scientifically confirmed, holdout-validated, confirmation-authorized,
production-ready or investable.

## 9. P-Q and delisting requirements

- **Every primary evaluation** produces the four PQB cells: ORIGINAL × {T0, TS} and
  QUARANTINED × {T0, TS}. This applies to the IS run and to the OOS run.
- **Rules applied:**
  - the frozen PQB and D7b semantics (portfolio-period and IC-period quarantine);
  - shared factor values and grouping;
  - no deletion, renormalization, implicit cash, zero substitution, pair deletion or
    re-ranking;
  - gap-aware HAC; CS-3 (CS-1 is the decision basis);
  - κ = 2; QUARANTINE_SENSITIVE and NOT_ASSESSED block a pass.
- **DELISTING_SENSITIVE** is independently binding.
- **ORIGINAL is not preferred** over QUARANTINED. Both must pass.

## 10. Budgets (non-substitutable; hard finite)

| Budget | Value | Meaning |
|---|---|---|
| Generation | **0** LLM generation events | the hypotheses are human-precommitted (generation method `HUMAN_PRECOMMITTED`); no generator is invoked |
| Proposal | **7** | exactly C1-H1 … C1-H7; no further proposal is admissible |
| Experiment (judged) | **7** | one primary evaluation design per hypothesis: top-500, IS run + OOS run under one Campaign design |
| Diagnostic evaluation (not judged) | **14** | 7 hypotheses × {top-200, top-1000}, each IS + OOS, descriptive only |
| Statistical | **m = 7**, α = 0.05 | one slot per first admissible judgment of a primary attempt |

- Unused budget in one category never expands another.
- Replay of an identical attempt consumes nothing.
- Exceeding any budget is a governance failure (stop).

## 11. Stop rules (typed; first match stops the affected scope)

**Campaign-level stops:**
- `PROPOSAL_BUDGET_EXHAUSTED`;
- `EXPERIMENT_BUDGET_EXHAUSTED`;
- `STATISTICAL_BUDGET_EXHAUSTED`;
- `ALL_PRECOMMITTED_HYPOTHESES_EVALUATED` (the normal close);
- `INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE` (any binding, identity, overlay, pin or
  determinism failure);
- `GOVERNANCE_CONFLICT`;
- `RESERVE_OR_FIREWALL_VIOLATION`;
- `SEALED_FOLD_ACCESS_ATTEMPT`.

**Hypothesis-level stops (disposition NOT_ASSESSED):**
- `PQ_NOT_ASSESSED`;
- `O2_ESCALATION`.

**O2 guard (mechanical, structural, precommitted):** for C1-H6 and C1-H7, before any
return is evaluated, the executor counts (formation f in IS ∪ OOS, member of the
top-500 U_f) cells whose denominator `lag(rolling_std(ret, w), 21)` is finite and
below 1e-6.
- If the count is > 0, that hypothesis is `NOT_ASSESSED(O2_ESCALATION)` and its
  analysis stops.
- A separately authorized numerical remediation review is required.
- No outcome-dependent workaround is permitted.
- **Escalation rule (admitted, frozen):** if Campaign evidence otherwise indicates that
  O2 materially changes ranks, grouping, screens or A/B semantics, stop the affected
  analysis and require separately authorized remediation.

**No "try a few more because results are close."** Campaign-1 ends at
`ALL_PRECOMMITTED_HYPOTHESES_EVALUATED` or at the first Campaign-level stop.

## 12. Implementation prerequisite: Campaign development ledger and executor (to be specified as a worker task under separate authorization)

**Scope:** only what is missing between the PQB evaluations and Phase-8/9-equivalent
development governance. It reuses existing value objects where possible:
- Phase-8 `SearchPolicy` (`FIXED_M_BONFERRONI`, `family_budget_m = 7`,
  `family_alpha = 0.05`, `budget_exhaustion = GOVERNANCE_FAILURE`, `replay_rule`);
- Phase-8 `hypothesis_id_for`;
- PQB `PQEvaluationDeclaration`, `classify_pq` and `PQScreenOutcome`;
- the Phase-7 `Partition`.

**Required behaviour:**
1. **Campaign identity:** `campaign1_id` = SHA-256 of the canonical JSON of:
   - the dataset id; the admission SHA-256; the overlay `pq_id` and `pq_hash`;
   - `quarantine_rule_hash`; the R3 identity hash;
   - this file's SHA-256; the FactorSpec declaration SHA-256;
   - the screen-policy SHA-256; the O1 task-spec SHA-256 and the merged O1 code SHA-256;
   - `partition_id`; the universes; horizon 21; the EstimandPolicy construction;
   - the budgets; the `SearchPolicy` content.
2. **Hypothesis identity:** `hypothesis_id = hypothesis_id_for(factor_provenance_hash)`,
   where the provenance is the Phase-6 `EngineResult.content_hash` of the factor panel
   evaluated on the D7c TrustedInputs. Factor values are hashed, never inspected.
3. **Attempt identity:** `campaign_attempt_id` = SHA-256 of {`campaign1_id`,
   `hypothesis_id`, `campaign_design_hash`, the domain
   `"campaign1-development-attempt"`}. `campaign_design_hash` covers the base PQB
   declaration minus `fold_key` (top-500). It mirrors the Phase-8 `experiment_id`
   semantics **in a distinct domain**, and is never presented as a Phase-8
   `experiment_id`.
4. **Append-only attempt ledger:** canonical JSONL, hash-chained. Each record holds the
   attempt id, slot index, the bound declaration hashes, the PQB diagnostics hashes
   (IS, OOS), the screen verdicts, the classify records, the disposition, the reasons
   and the stop reason.
   - Replay of an identical attempt consumes no slot.
   - A different result for the same attempt id fails closed.
5. **Locking:** the family fields are locked at the first attempt. Any later change is
   `GOVERNANCE_CONFLICT`.
6. **Budget counters** for every budget in §10, separately and non-substitutably.
7. **Screen computation** exactly per §8.2, including the critical value c computed
   from the frozen formula.
8. **Disposition** exactly per §8.3. **No ACCEPT and no `EvaluationRecord` exist** in
   this layer, and no Phase-7 holdout token is consumed.
9. **Sealed-fold and reserve firewalls:** the executor refuses any declaration whose
   fold is the DEVELOPMENT_SEALED_FOLD (PQB / D7b already refuse it). The reserve is
   never read.
10. **Reporting** per §15. The O1-repaired structural residual is used.
11. **Determinism:** the ledger and reports are byte-identical under
    `TZ=Asia/Tokyo LC_ALL=C`.

The executor must be barrier-tested on synthetic fixtures, with mutations, before
execution.

## 13. Execution prerequisites (all required before a separate execution authorization)

1. This precommitment is frozen (this commit).
2. **O1 repaired and tested**: task `worker_tasks/campaign1/task-c1-o1-structural-residual.md`
   merged and barrier-tested, `quarantine.py` byte-unchanged.
3. **Campaign ledger and executor** (§12): implemented, merged and barrier-tested.
4. **EstimandPolicy registered** in Knowledge-PIT (`HUMAN_DECISION`, `decision_kind =
   ESTIMAND_POLICY`, the §4 fields) under a separate authorization.
5. **`PROGRAM_FREEZE` registered** (`HUMAN_DECISION`, `consulted_all_prior = true`)
   after the ESTIMAND_POLICY record.
6. **`HYPOTHESIS_FREEZE` ordering prepared and validated.** The factor panels are
   computed (hashed only), then 7 `HYPOTHESIS_FREEZE` records are written with payload
   `hypothesis_id`, `factor_spec_hash` and `proposal_id = null`, and with
   `refs.influenced_by` = {ESTIMAND_POLICY, PROGRAM_FREEZE}. All of this happens
   **before** the first Campaign evaluation.
7. **Bindings verified:** Dataset v1.2, overlay, admission, R3 and Campaign identity.

**Knowledge-PIT in this task:** no append.
- Phase-10 records are written by P10-I ingestion or by a separately authorized
  registration step.
- §7.1 requires the ESTIMAND_POLICY "frozen before exploration" and recorded before the
  program's first `HYPOTHESIS_FREEZE`. That is satisfied by items 4–6 occurring before
  execution.
- The design freeze itself is not a Knowledge-PIT event under any existing rule.

## 14. No result-driven adaptation

- **No changes after execution begins:** no hypothesis, parameter, window, universe,
  horizon, sign, metric, cost, grouping, screen, threshold, fold or budget may be added
  or altered after Campaign-1 execution begins.
- **Interesting failures** may seed **Campaign-2** hypotheses. They are recorded only
  after Campaign-1 closes (`ALL_PRECOMMITTED_HYPOTHESES_EVALUATED` or a Campaign-level
  stop).

## 15. Reporting schema (frozen before outcomes)

**Every one of the 7 hypotheses is reported**, whatever its disposition. Each report
includes:
- **identity and bindings:**
  - the hypothesis id and label; economic rationale; expected direction;
  - the FactorSpec SHA-256; the Campaign design hash (the PQB base declaration hash);
  - the dataset identity; the overlay `pq_id` and `pq_hash`; `campaign1_id`;
- **design:**
  - universe; partition and fold boundaries; horizon 21;
  - costs (10 bps; gross and net); statistical allocation (slot index, αᵢ, c);
- **results**, for **each fold run (IS, OOS)** and **each cell** (ORIGINAL-T0,
  ORIGINAL-TS, QUARANTINED-T0, QUARANTINED-TS):
  - the primary value, t or status, n_obs, n_index;
  - the support counts and support fractions;
- **quarantine and sensitivity:**
  - quarantine exposure (Q_B, Q_S, Q_L); the structural residual cross-sectional cells
    (O1-repaired definition);
  - liquidity-window exposure; CS-3 complement diagnostics;
  - delisting sensitivity in A and B; quarantine sensitivity (clauses);
- **outcome:**
  - secondary diagnostics; the screen verdicts; the multiplicity result;
  - the final development disposition with reasons;
  - the O2 guard count (C1-H6, C1-H7).

**Robustness diagnostics** are reported for all hypotheses, labelled descriptive. The
close report lists any stop reason. Reporting only successes is forbidden.

## 16. Independent pre-freeze review (planner)

| Risk | Finding |
|---|---|
| Hidden parameter tuning | None. The grid is {252, 126} × skip 21; reversal 21; volume 21/252; the volatility window equals the momentum window. Fixed before any outcome |
| Excessive hypothesis count | 7 hypotheses in 4 families, within the ~8 target |
| Arbitrary grids | Each value has a time-scale rationale (year, half-year, month) |
| Sign flexibility | Sign +1 everywhere. Reversal is encoded in the expression. Opposite sign is evidence against |
| Horizon flexibility | One horizon, h = 21 (one estimand per program) |
| Universe flexibility | Primary top-500 only. Robustness is descriptive, unjudged, rescue-proof |
| Metric substitution | One primary (net long-short). Secondaries cannot substitute |
| Robustness-as-search | Robustness is run for every hypothesis, never judged, with a fixed budget of 14 |
| Multiplicity loopholes | One statistical family with m = 7. Diagnostics are not judged. The OOS rule is directional, not a second test. A/B and T0/TS are cells of one attempt, not attempts |
| Budget substitution | Five separate counters, non-substitutable |
| Post-selection degrees of freedom | None remaining. Folds, thresholds, minimum supports, c, construction and dispositions are all frozen |
| Reserve leakage | Development formations only; reserve purge; the reserve is never read |
| Sealed-fold leakage | DEVELOPMENT_SEALED_FOLD is never read. It sits in the D7b-refused holdout role |
| P-Q bypass | All four cells are required; QUARANTINED is co-equal with ORIGINAL |
| Delisting bypass | §9.6 is binding in A and B, for both folds |
| O1 ambiguity | The repair is an execution prerequisite, with a structural (numeric-free) definition frozen now |
| O2 overclaim | Non-claim kept. A structural pre-evaluation guard and the escalation rule apply. No workaround |

**Unresolved methodological degrees of freedom: NONE.**

## Access record

- Empirical factor evaluations: 0. Factor returns: 0. IC / rank-IC: 0. Performance
  metrics: 0. Screen verdicts: 0.
- The FactorSpecs were constructed and validated only. They were not evaluated.
- Reserve reads: 0. Final-holdout consumption: 0. Provider / network calls: 0.
- Knowledge-PIT append: NO.
- Campaign-1 execution: NOT STARTED.
