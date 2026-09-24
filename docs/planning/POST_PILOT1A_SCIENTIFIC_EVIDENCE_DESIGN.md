# Post-Pilot-1A — Scientific Evidence Framework (RESEARCH / ARCHITECTURE DESIGN)

**Status:** research and design only. No code, tests, sealed authority, Pilot
evidence, holdout, credential or model was touched.

- Baseline: `pilot1a-complete` → `1d1f70ae41ab91676002373a52be3815436549bb`
  (tag object `c83e8d72…`).
- Sources read: Pilot-1A evidence (`pilot_evidence/pilot1a-real-deepseek-v1/`),
  `worker_tasks/phase7/phase7-plan.md` §7–9, `worker_tasks/phase8/phase8-plan.md`
  §5, §7, §10–11, `worker_tasks/phase9/phase9-plan.md`,
  `docs/phase9_research_loop_certification.md` §9, `worker_tasks/pilot1/pilot1-plan.md`
  §26b–26g, and the implementations in `smart_beta/evaluation/`,
  `smart_beta/experiment/judge.py` and `smart_beta/research/history.py`.

**Claim labels used throughout:**
- **[E]** established statistical result;
- **[M]** methodological recommendation from the literature;
- **[D]** design choice (ours);
- **[I]** our inference or judgment.

---

## 1. Executive conclusion

1. **[I] The binding constraint is data, not statistics.** On the only data the
   system can use today — 26 names, a single 52-trading-day holdout, and a
   window already inspected in Phase 5A — **no** test procedure can yield a
   scientifically defensible SUPPORTED or NOT_SUPPORTED conclusion for any
   plausible factor effect.
   - The minimum detectable mean rank-IC is ≈0.08–0.09. Plausible cross-sectional
     factor ICs are ≈0.02–0.05.
   - The next phase must therefore *build the judgment machinery* **and** make the
     system *refuse* confirmatory conclusions when the design is inadequate or the
     data are contaminated. It must not produce false precision.

2. **[I] Phase 8's ACCEPT/REJECT/DEFER is a governance vocabulary and should stay
   one.** Scientific conclusions need a separate, multi-axis record: governance
   validity, evidence role, statistical, economic, robustness, replication and
   production are independent axes. Collapsing them was the root of the
   "ACCEPT with negative holdout" confusion.

3. **[M/I] The defensible design for an autonomous LLM search is
   *separate exploration from confirmation*.**
   - The LLM loop explores freely on development data. That produces candidates,
     not conclusions.
   - A small, pre-registered set of candidates is then promoted by a frozen rule
     and tested **once** on data that played no role in generation or selection.
     Multiplicity is corrected over the *confirmatory* family only.
   - This is sample splitting. Its validity does not depend on modeling the LLM's
     adaptive search [E], provided the confirmation data are genuinely untouched.
     Every "correct the whole adaptive history" alternative (Bonferroni over a
     guessed m, online FDR, SPA over the history) either needs assumptions an
     LLM-driven search violates or has very low power.

4. **[I] Fixed-m Bonferroni (Phase 8) is adequate as a *search-budget governor*
   and as a valid FWER bound for a small pre-registered confirmatory family.**
   It is **not** adequate as a scientific correction over an adaptive exploration
   history: m is unknowable ex ante, it counts syntactic rather than
   decision-relevant tests, and it ignores dependence.

5. **[I] In the current design, "OOS" is not out-of-sample for any generated
   hypothesis after the first.** The generator sees OOS metrics as feedback
   (P9-B channels `OOS_METRICS`), so from H2 onward the OOS window is
   development data. Only data never exposed to the generator, the researchers,
   or earlier study designs can confirm.

6. **[D] Minimum defensible next phase (Phase 10 — Scientific Evidence
   Protocol):**
   - a multi-axis scientific assessment record;
   - pre-registered confirmatory studies (endpoint, direction, smallest effect of
     interest, α, analysis plan, candidate set) with a mandatory power/design
     adequacy gate;
   - an evidence-exposure (contamination) ledger that persists across runs;
   - rule-based canonicalization for endpoint-invariant transforms (this would
     have merged `standardize(ret)` and `rank(ret)`);
   - fold-level HAC and block-bootstrap inference on per-date series;
   - acquisition of a genuinely untouched confirmation dataset, which is the
     only thing that can make SUPPORTED reachable.

---

## 2. What Pilot 1A exposed

These points are from the evidence record and the code.

| Observation | Evidence | Consequence |
|---|---|---|
| ACCEPT ≠ support | Exp 1 `standardize(ret)` ACCEPT; holdout IC −0.0020, rank-IC −0.0463, net long-short −0.0014/day, Sharpe −1.80 | The Phase-8 judge applies no performance criterion (`judge.py` scope note). The system has no scientific judgment at all. |
| Only pooled inference exists | `primary_metrics` t-stats pool IS+OOS+holdout (227 dates, one Newey-West lag = 6); `FoldResult.metrics` carry `value` and `n_obs` only, no SE | No holdout-specific test statistic exists in the record. The pooled statistic mixes development and confirmatory data. |
| Holdout underpowered | 26 names × 52 holdout days | Minimum detectable rank-IC ≈ 0.08–0.09; Sharpe SE ≈ 2.2 (annualized) |
| Holdout contaminated | Phase 5A computed and committed market-factor and per-name returns over the window | Not scientifically untouched, independent of the Phase-8 mechanics |
| OOS used as feedback | P9-B feedback channels expose IS/OOS metrics to the generator | OOS is development data for H2 and H3 |
| Semantic redundancy | Exp 1 ≡ Exp 2 for every rank-based endpoint (identical rank-IC, long-short, Sharpe and drawdown in every fold) | Two statistical slots were consumed by one decision-relevant hypothesis |
| Costs dominate | Daily turnover ≈0.65–0.8 at 10 bps ⇒ ≈7 bps/day cost vs ≈3 bps/day gross spread (Exp 1 IS) | Gross-return "significance" is economically irrelevant here; the economic endpoint must be net of cost. |
| Single-consumer holdout | Exp 2 and 3 DEFER `holdout_previously_consumed`; their holdout metrics exist but are ungoverned | One holdout supports one experiment; a promotion *set* cannot be confirmed together. |
| LLM prior knowledge | Documented training cutoff Jun 2026 covers IS/OOS | Hypotheses may encode memorized in-sample patterns (a known LLM alpha-mining concern) |

---

## 3. Scientific question and terminology

**Question.** What evidence must exist before the system may say a factor
hypothesis is scientifically *supported*, *not supported*, *inconclusive*, or
that the evidence is *invalid* for judgment?

**Terms [D]:**
- **Signal** — a deterministic cross-sectional score (a Phase-6 `FactorSpec`
  evaluated on PIT inputs).
- **Hypothesis** — a directional claim about the population relationship
  between a *signal equivalence class* and future returns on a declared
  universe and horizon, measured by a declared endpoint, with a declared
  smallest effect of scientific interest (SESOI). A formula alone is not a
  hypothesis.
- **Experiment** — one evaluation of one signal under one EvaluationSpec
  (Phase-8 `experiment_id`; unchanged).
- **Study** — a pre-registered, frozen analysis plan that tests a *set* of
  hypotheses on a declared data window with a declared inference procedure.
  Studies are **exploratory** or **confirmatory**.
- **Evidence role** of a data window with respect to a hypothesis:
  - **development** — the hypothesis or its selection may have depended on it;
  - **confirmatory** — provably not used for generation or selection;
  - **contaminated** — exposed by some channel (generator, human, earlier
    study, pretraining), so it cannot be confirmatory.
- **Search attempt** (budget unit) vs **statistical test** (inference unit):
  distinct concepts (§6).

---

## 4. Literature review

For each method: the problem it solves, its assumptions, its fit to an
adaptive LLM search, the inputs it needs, and the failure modes that remain.

### 4.1 Multiple testing and the factor zoo

**Harvey, Liu & Zhu (2016), "…and the Cross-Section of Expected Returns", RFS 29(1):5–68.**
- **Problem:** hundreds of published factors imply high false-discovery rates
  at t = 2.
- **Method:** Bonferroni, Holm and BHY applied to the historical factor record,
  with correlation and publication-bias adjustments. It recommends new factors
  clear **t > 3.0**. [E for their sample and model; M as a hurdle.]
- **Assumptions:** an estimate of the number of tests actually tried, including
  unpublished ones, which they model.
- **Fit:** a *static* hurdle calibrated to the academic literature's history. It
  is not calibrated to our search process. A fixed t hurdle inherits no
  guarantee under adaptive LLM search, because our effective number of tests
  differs.
- **Remaining failure:** a hurdle is not a procedure. It ignores power; with
  N = 26, t > 3 is practically unreachable for real effects.

**Harvey & Liu (2020), "False (and Missed) Discoveries in Financial Economics", JF 75(5).**
- Double-bootstrap estimation of FDR and power trade-offs. It emphasizes that
  raising hurdles raises *missed* discoveries. [M]
- **Fit:** it motivates reporting the error trade-off rather than one threshold.
  It needs a large population of tested strategies to bootstrap.

**Chordia, Goyal & Saretto (2020), "Anomalies and False Rejections", RFS 33(5).**
- About 2 million data-mined accounting strategies under multiple-hypothesis
  control; few survive. [E for their sample]
- **Fit:** this is the closest analogue to autonomous search. It shows the
  *scale* problem: mechanical search needs corrections scaled to the search
  space, not to the number of "published" candidates.

**Chen, Lopez-Lira & Zimmermann, "Does Peer-Reviewed Research Help Predict Stock Returns?" (arXiv 2212.10317).**
- Mining about 29,000 accounting ratios for t > 2 produces post-sample
  predictability similar to peer-reviewed predictors; about 50% remains
  post-sample. [E for their sample]
- **Relevance [I]:** machine-generated signals are not a priori worse than
  "theory". But post-sample decay of about half is the norm, so in-sample
  magnitudes must be shrunk. The LLM's "economic rationale" field carries no
  evidential weight by itself.

**Hou, Xue & Zhang (2020), "Replicating Anomalies", RFS 33(5):2019–2133.**
- With NYSE breakpoints and value weighting, 65% of 452 anomalies fail
  |t| ≥ 1.96, and 82% fail the 2.78 multiple-test hurdle. [E]
- **Relevance:** construction choices (microcaps, weighting) move conclusions.
  Robustness to construction is a first-class axis.

**Jensen, Kelly & Pedersen (2023), "Is There a Replication Crisis in Finance?", JF 78(5):2465–2518.**
- A hierarchical Bayesian model: most factors replicate, cluster into 13
  themes, and work out-of-sample across 93 countries. Evidence is *strengthened*
  by the number of related factors through shrinkage. [E for their data and
  model]
- **Relevance [I]:** replication is about themes (clusters), not formulas.
  Shrinkage across related signals is the principled answer to "many similar
  tests". It needs many factors and long, broad data, which we do not have.

**McLean & Pontiff (2016), "Does Academic Research Destroy Stock Return Predictability?", JF 71(1):5–32.**
- Returns are 26% lower out-of-sample and 58% lower post-publication. [E]
- **Relevance:** an upper bound on data-mining shrinkage around 26%. Any
  in-sample effect size should be discounted before power and SESOI planning.

**Giglio, Liao & Xiu (2021), "Thousands of Alpha Tests", RFS 34(7):3456–3496.**
- FDR control for many alpha tests in linear factor models, robust to omitted
  factors and missing data. It is asymptotic in both the number of tests and
  T. [E]
- **Fit:** relevant when we test many alphas against a factor model. It
  requires a large test population and panels, and it is asymptotic. It is
  inappropriate for 3 candidates on 26 names.

**Feng, Giglio & Xiu (2020), "Taming the Factor Zoo", JF 75(3).**
- Double-selection LASSO tests a new factor's *marginal* contribution given the
  zoo. [M]
- **Relevance:** "redundancy" should be tested as marginal pricing
  contribution, not only pairwise correlation. It needs a benchmark factor set.

### 4.2 Data snooping and "best-of-many"

**White (2000), "A Reality Check for Data Snooping", Econometrica 68(5).**
- Tests whether the *best* of K strategies beats a benchmark, accounting for
  the search, via a stationary bootstrap of the full K-strategy return matrix.
  [E, asymptotic]
- **Assumptions:** the K strategies are specified in advance; stationarity;
  their joint return series are available.

**Hansen (2005), "A Test for Superior Predictive Ability", JBES 23(4).**
- A studentized, recentered improvement on the Reality Check that is less
  sensitive to poor, irrelevant strategies. [E]

**Romano & Wolf (2005), "Stepwise Multiple Testing as Formalized Data Snooping", Econometrica 73(4).**
- A stepdown bootstrap that identifies *which* strategies beat the benchmark,
  with FWER control and dependence exploited. [E]

**Fit for the whole family [I]:** these are the *correct* tools when the
question is "is the best of the strategies we tried real?" and the full set of
tried strategies is known and re-evaluable on common data. Our registry makes
the tried set knowable, which is a real advantage. But:
- **(a)** the tried set is *adaptively grown*; the Reality Check assumes a fixed
  universe of candidates, so adding strategies after seeing results changes the
  null distribution;
- **(b)** they need the per-period return series of **every** tried strategy on
  the **same** window;
- **(c)** the power is low with short T.

Recommended use: *reporting* on exploration history, not a confirmatory gate.

### 4.3 Backtest overfitting

**Bailey & López de Prado (2014), "The Deflated Sharpe Ratio", JPM 40(5):94–107.**
- Adjusts the observed Sharpe for the number of trials, the variance of trial
  Sharpes, non-normality (skew/kurtosis) and sample length. [E, under a
  Gaussian-extreme-value approximation]
- **Needs:** the number of *independent* trials, and the cross-trial Sharpe
  variance. Both are ill-defined under dependent, adaptive trials.
- **Fit:** useful as a skeptical *report* on exploration winners. It is not a
  confirmatory test, and the "effective number of independent trials" is not
  estimable reliably from a few dozen correlated LLM trials.

**Bailey, Borwein, López de Prado & Zhu (2017), "The Probability of Backtest Overfitting", J. Comp. Finance 20(4):39–70 (CSCV).**
- Estimates how often the in-sample-best configuration underperforms the median
  out-of-sample, using combinatorially symmetric cross-validation over a
  strategy × time matrix. [E as a procedure]
- **Needs:** many candidate configurations evaluated on the same partitioned
  panel.
- **Fit:** a diagnostic of *search-process* overfitting. It suits a
  "family-level report" once exploration produces dozens of candidates on a
  common window. It is not a per-hypothesis verdict.

**Lo (2002), "The Statistics of Sharpe Ratios", FAJ 58(4)**, and **Ledoit & Wolf (2008), J. Empirical Finance 15(5).**
- Sharpe standard errors under serial correlation, and HAC/bootstrap tests for
  Sharpe differences. [E]
- **Relevance:** the Sharpe SE with 52 daily observations is huge (≈2.2 in
  annualized units at SR ≈ 0). A holdout Sharpe of −1.80 is statistically
  uninformative.

### 4.4 Classical FWER and FDR

**Holm (1979), Scand. J. Stat.** — uniformly more powerful than Bonferroni and
FWER-valid under arbitrary dependence. [E] **Fit:** strictly better than
Bonferroni for a pre-registered confirmatory family. Adopt.

**Benjamini & Hochberg (1995), JRSS-B; Benjamini & Yekutieli (2001), Ann. Stat.**
- BH controls FDR under independence or PRDS. BY controls it under arbitrary
  dependence at a log(m) cost. [E]
- **Fit:** appropriate for *screening* many exploratory candidates, where some
  false discoveries are tolerable. Not appropriate for a "SUPPORTED" claim
  about a specific hypothesis, where FWER semantics are clearer.

### 4.5 Adaptive and online inference

**Foster & Stine (2008), α-investing, JRSS-B 70(2); Javanmard & Montanari (2018), Ann. Stat. 46(2), LORD; Ramdas et al. (2017–2018), LORD++/SAFFRON.**
- Online FDR for a *stream* of hypotheses tested in sequence, with an α-wealth
  budget. [E; several versions are proven only under independence or specific
  dependence]
- **Fit [I]:** conceptually the closest match to "H1 → H2 → H3 …". But the
  guarantees need each p-value to be valid *conditional on the past*. When H2
  is chosen after seeing H1's results *on the same data*, H2's p-value on that
  data is not conditionally valid. Online FDR fixes the *counting* problem, not
  the *reuse-of-data* problem. It is valid only if each test uses fresh data
  (or data independent of the selection information).

**Vovk & Wang (2021), Ann. Stat. 49(3); Wang & Ramdas (2022), "False Discovery Rate Control with E-values", JRSS-B 84(3):822–852 (e-BH); Ramdas, Grünwald, Vovk & Shafer (2023), Stat. Sci. (safe anytime-valid inference).**
- E-values and test martingales support optional stopping and continuation;
  e-BH controls FDR under **arbitrary dependence**. [E]
- **Fit [I]:** the best-matched tool for **prospective** confirmation, where
  new data arrive over time and are monitored with a pre-registered
  martingale. Anytime validity makes "keep watching until evidence is
  decisive" legitimate. It does **not** rescue inference on data already used
  for selection. It needs data that *arrive after* the hypothesis is frozen.

**Dwork, Feldman, Hardt, Pitassi, Reingold & Roth (2015), "The Reusable Holdout", Science 349(6248):636–638.**
- Differential-privacy-style noise on holdout answers allows many adaptive
  queries while bounding overfitting. [E, i.i.d.-sample theory]
- **Fit [I]:** theoretically attractive for letting a generator query a
  confirmation set a controlled number of times. But its guarantees assume
  i.i.d. samples. Financial time series are neither independent nor
  identically distributed, and the budget of queries is small for short
  samples. A research direction, not a component to build now.

**Selective inference** (Fithian, Sun & Taylor 2014; Taylor & Tibshirani 2015, PNAS).
- Valid inference conditional on a selection event, when the selection rule is
  known and mathematically tractable (e.g. lasso). [E]
- **Fit:** an LLM's selection rule is neither known nor tractable. It is not
  applicable, except through its simplest instance, *data splitting*, which is
  exactly the exploration/confirmation design.

**Gelman & Loken (2014), "The garden of forking paths", American Scientist.**
- Undisclosed analytic flexibility invalidates nominal p-values even without
  conscious fishing. [M]
- **Relevance:** every evaluation-parameter choice made after seeing data is a
  fork. Pre-registration must freeze the analysis plan, not only the formula.

### 4.6 Out-of-sample, holdout and protocol design

**Arnott, Harvey & Markowitz (2019), "A Backtesting Protocol in the Era of Machine Learning", JFDS 1(1).**
- A research-protocol checklist: pre-specify, count trials, respect true OOS,
  mind costs and economic mechanism. [M]

**López de Prado (2018), *Advances in Financial Machine Learning*.**
- Purging and embargo for overlapping labels; combinatorial purged
  cross-validation. [M] **Relevance:** Phase 7 already purges cross-boundary
  labels (§8.1). The embargo concept matters once horizons exceed 1.

**Nosek et al. (2018), "The Preregistration Revolution", PNAS 115(11).**
- Pre-registration distinguishes confirmatory from exploratory evidence; it
  does not forbid exploration. [M]

### 4.7 Economic significance and costs

**Novy-Marx & Velikov (2016), "A Taxonomy of Anomalies and Their Trading Costs", RFS 29(1).**
- Many anomalies' profits disappear after realistic costs, especially
  high-turnover ones. [E]
- **Relevance:** Pilot 1A's 1-day return signals have turnover ≈70%/day.
  Costs, not t-stats, decide their economic status.

### 4.8 LLM-driven factor mining

AlphaAgent (arXiv 2502.16789), Chain-of-Alpha (arXiv 2508.06312), QuantaAlpha
(arXiv 2602.07085), and LLM evolutionary factor search (arXiv 2507.17211)
**[I from reading their framing]**:
- The field recognizes that backtest-feedback loops overfit ("alpha decay", "LLM
  p-hacking"), and that pretrained models may have memorized market history.
- Mitigations reported are mostly regularized exploration and later-dated
  test windows, i.e. process design rather than new inferential theory.
- None provides a validity guarantee for adaptive LLM search. This supports
  treating the LLM loop as *exploration*.

### 4.9 Summary: which method for which job [I]

| Job | Suitable | Unsuitable / why |
|---|---|---|
| Search-budget governance (stop runaway search) | fixed-m budget (Phase 8), α-wealth accounting | treating the budget as inference |
| Confirmatory significance on untouched data | pre-registered endpoint + Holm (or Bonferroni) over the small promoted family; HAC or block-bootstrap SE | BH (wrong error semantics for a specific claim); DSR/PBO (not tests) |
| Reporting skepticism about exploration winners | DSR, Romano-Wolf/SPA on the tried set, PBO when enough candidates | as gates (unstable with short T and dependent trials) |
| Prospective / sequential confirmation | e-values / test martingales, e-BH | fixed-sample tests with repeated looks |
| Replication across themes and markets | hierarchical shrinkage (JKP-style) once data exist | single-formula replication |
| Redundancy / marginal value | spanning regressions or double-selection vs a benchmark set | pairwise correlation alone |

---

## 5. Proposed scientific evidence model [D]

```
            EXPLORATION (autonomous, adaptive)          CONFIRMATION (frozen, once)
  generator ⇄ development data (IS, "OOS")      →  pre-registered study on untouched data
  output: candidates + exploration report           output: scientific assessment
  claims: none about truth                          claims: bounded, per axis
```

1. **Exploration may use any development data and any adaptivity.** Its outputs
   are *candidates*, with full lineage and every tested variant recorded (Phase
   8/9 already do this). Statistics computed here are *selection statistics*.
   They are reported, never read as evidence for truth.
2. **Promotion** is a frozen, pre-declared rule applied to exploration outputs.
   Example: "at most k = 3 candidates, distinct canonical classes, ranked by
   development rank-IC, with a net-of-cost development spread > 0".
   Promotion creates a **confirmatory study** object *before* confirmation data
   are touched.
3. **Confirmation** evaluates the promoted set once, on a window whose exposure
   ledger shows no generator, human or pretraining exposure (to the extent
   knowable). Inference follows the pre-registered plan, with FWER over the
   promoted family.
4. **Scientific assessment** is a multi-axis record (§11), derived
   mechanically from the confirmatory result plus the declared robustness and
   replication evidence.
5. **Exploration history is never re-used as confirmatory evidence.** A
   hypothesis whose confirmation window becomes contaminated loses its
   confirmatory role permanently.

**Why this, rather than correcting the whole adaptive history [I]:**
- sample splitting is valid under arbitrary, unmodeled selection by the LLM,
  because confirmation data are independent of selection [E];
- it needs no estimate of the "effective number of tests" in exploration;
- it makes the generator firewall a *scientific* requirement (it protects
  independence) rather than only a governance one.

**The cost:** data. Confirmation consumes data that exploration cannot use.

---

## 6. Hypothesis, experiment and family ontology [D]

**Canonical signal class.** Two signals are *endpoint-equivalent* for endpoint
E if E is invariant to the transformation between them.
- Rank-based endpoints (rank-IC, quantile-sort portfolios) are invariant to any
  strictly increasing per-date transform: `rank`, affine `standardize` with
  positive scale, positive scaling, adding a per-date constant.
- Pearson IC is invariant only to per-date positive affine maps.

A **rule-based canonicalizer** (a declared rewrite set per endpoint, not
learned semantic inference) maps each FactorSpec to a canonical form for the
study's endpoint. `standardize(ret)` and `rank(ret)` canonicalize to the same
class under rank-IC. Signals built from different inputs, lags or windows do
not. This is deliberately **incomplete**: it detects declared invariances only,
and the general semantic-equivalence nonclaim stays.

**When is a change a new hypothesis vs a robustness test?**

| Change | Classification |
|---|---|
| sign | Same class, opposite direction. The directional hypothesis must be declared *before* confirmation. A sign flip after seeing development results is a new exploratory candidate, not a free re-test. |
| lag, window, transform (non-invariant), input field | New signal class ⇒ new hypothesis |
| strictly monotone transform under a rank endpoint | Same class ⇒ same hypothesis (no new test) |
| universe, transaction cost, portfolio construction, horizon | Robustness dimensions of the *same* hypothesis **if pre-declared** as a robustness grid. Changed after seeing results ⇒ a new analysis (forking path) that must be counted. |
| EvaluationSpec (endpoint, partition, horizon as primary) | A different study design. The primary endpoint and horizon are part of the hypothesis. |

**Units [D]:**
- The **search-budget unit** stays Phase-8's `experiment_id`. It is a
  governance cost, and it is fine that it overcounts.
- The **statistical test unit** is *(canonical class, direction, endpoint,
  universe, horizon)* inside a **confirmatory study**. Only confirmatory tests
  enter the scientific multiplicity family.
- These units are intentionally **not** identical.

**Statistical family [D]:** the set of hypotheses tested in *one confirmatory
study* (pre-registered). Cross-study familywise control is handled by
pre-registered study-level α allocation (e.g. a fixed α per confirmation
window), not by pooling unrelated studies.

---

## 7. Primary endpoint design

**[I] No endpoint is universally right.** Each hypothesis should pre-register
**one** primary statistical endpoint and **one** economic endpoint, with
direction and a SESOI.

| Candidate | For | Against |
|---|---|---|
| Pearson IC | uses magnitudes | outlier-sensitive; scale-dependent; weak economic meaning |
| **Rank-IC (mean of per-date Spearman)** | robust; uses the full cross-section; construction-free; invariant to monotone transforms | economic magnitude unclear; noisy at small N |
| Long-short (quantile) return | economic; portfolio-level | depends on n_groups, weighting and breakpoints; lower power |
| Sharpe of long-short | scale-free | very large SE at short T; non-normal |
| Alpha vs factor model | measures marginal contribution | needs a certified benchmark factor set (US CAPM only today; FF/CH factors not certified for this universe) |

**[D] Recommended default:**
- **Primary statistical endpoint: mean rank-IC** at the declared horizon.
  Directional (one-sided in the declared direction), inferred by
  Newey-West/HAC with a lag rule declared ex ante, plus a stationary block
  bootstrap as a robustness check on the SE.
- **Primary economic endpoint: net-of-cost long-short return** (declared
  construction, cost model and turnover), tested one-sided against a declared
  economic SESOI. It is a *separate axis*, not a co-primary on the same α.

**Required analysis properties:**
- directionality declared;
- the SESOI declared in endpoint units, justified from prior evidence and
  discounted about 25–50% for expected decay (McLean-Pontiff; Chen et al.);
- autocorrelation handled by a HAC lag ≥ horizon − 1 and an ex-ante rule;
- cross-sectional dependence acknowledged: the per-date IC SE exceeds
  1/√(N−3) under factor structure, so the time-series-of-ICs approach is
  preferred;
- non-normality handled by the bootstrap check;
- multiple horizons or constructions either (a) pre-declared with one primary,
  the rest as robustness, or (b) all primary with Holm across them.

---

## 8. Multiple-testing and adaptive-search framework

1. **Search governance (keep Phase 8).** Fixed-m budgets bound exploration cost
   and prevent unbounded search. **[I]** Their semantics are "budget", not
   "inferential guarantee over the adaptive history".
2. **Confirmatory significance (new).** Holm over the pre-registered
   confirmatory family at α_study. This is valid FWER under arbitrary
   dependence [E], and because confirmation data are untouched it is valid
   regardless of how exploration chose the candidates.
3. **Exploration reporting (new, report-only).** For the family's exploration
   history, report:
   - the canonical-class count;
   - Romano-Wolf/SPA p-values of the best candidate *where the tried set is
     re-evaluable on a common window*;
   - the DSR of the best candidate with a declared trial count;
   - PBO when ≥ about 16 candidates exist.

   Labeled **exploratory**, never a gate.
4. **Prospective confirmation (stronger future).** E-value test martingales on
   data arriving after freeze, with e-BH across simultaneously monitored
   hypotheses (valid under arbitrary dependence).

**[I] What does not survive LLM adaptivity:**
- any p-value computed on data the generator saw, or that informed selection;
- BH or online-FDR over exploration p-values (not conditionally valid);
- Bonferroni with m = "experiments so far" (a data-dependent m);
- DSR as a gate (unknown effective trial count).

---

## 9. Holdout and evidence-contamination model

**Exposure ledger [D].** A persistent, append-only record of every
(data window × universe × target) and every *channel* that has seen it:
- generator feedback;
- human inspection (e.g. Phase 5A artifacts);
- a prior study's confirmation;
- pretraining, by the documented model cutoff;
- aggregates leaked through robustness tables (the §26b lesson).

A window is eligible as confirmatory for a hypothesis only if the ledger shows
no exposure channel that could inform that hypothesis's generation or
selection. Contamination is **permanent**.

**Role of each fold [D]:**

| Fold | Role |
|---|---|
| IS | exploration fitting and selection |
| "OOS" / validation | exploration model checking; development data once shown to the generator |
| Walk-forward | exploration stability evidence |
| Confirmation window | one-time, pre-registered, study-level; consumed by a *promotion set*, not a single experiment |
| Prospective window | data dated after freeze; the strongest confirmation |

**[I] Design recommendation:**
- abandon "one final holdout consumed by the first experiment" as the
  scientific design;
- keep Phase-8 single-use holdout governance as the *mechanical* guard, but
  make the consuming entity a **confirmatory study** (a batch);
- prefer **sequential forward windows**: each new calendar block, once
  accrued, becomes a new confirmation window for the studies frozen before it.

The cross-run holdout ledger (a deferred item) becomes mandatory.

---

## 10. Robustness vs replication

**[I] Robustness** is the same data, varied analysis: subperiods, universe
policy variants, parameter grid, cost levels, construction. It detects
fragility and does **not** add independent evidence (the samples overlap
heavily).

**Replication** requires new *observations*, in rough order of evidential
value:
1. **Prospective later period** — genuinely new data; strongest; subject to
   regime change.
2. **Different market or universe with low overlap** (e.g. China A-shares vs
   US), when PIT-certified.
3. **Independently sourced or reconstructed data** for the same period —
   guards against vendor artifacts, not sampling error.
4. **An independent research process** reproducing the claim from its
   specification.

Alternative portfolio construction on the same data is robustness, not
replication. Because asset returns share common factors, "different" universes
in the same period are positively correlated tests. Replication evidence must
be discounted for overlap, or modeled hierarchically (JKP-style) when enough
studies exist.

---

## 11. Scientific decision-state machine [D]

A **ScientificAssessment** has independent axes. It is never collapsed into
one score.

| Axis | States | Moves by |
|---|---|---|
| governance_validity | VALID / INVALID | Phase-8 record and provenance integrity |
| evidence_role | EXPLORATORY / CONFIRMATORY / CONTAMINATED | exposure ledger + study pre-registration |
| design_adequacy | ADEQUATE / UNDERPOWERED | pre-registered power check: minimum detectable effect ≤ SESOI at α_study, 80% power |
| statistical | SUPPORTED / NOT_SUPPORTED / INCONCLUSIVE / NOT_ASSESSED | confirmatory result, below |
| economic | MATERIAL / NOT_MATERIAL / INCONCLUSIVE / NOT_ASSESSED | net-of-cost endpoint vs economic SESOI |
| robustness | CONSISTENT / FRAGILE / NOT_ASSESSED | pre-declared robustness grid (sign agreement, no single-slice dependence) |
| replication | REPLICATED / NOT_REPLICATED / NOT_ATTEMPTED | a separate confirmatory study on new observations |
| production_readiness | NOT_CERTIFIED | always, in this system's scope |

**Statistical-axis rule** (only if `evidence_role = CONFIRMATORY` and
`design_adequacy = ADEQUATE`; otherwise NOT_ASSESSED):
- **SUPPORTED:** the Holm-adjusted one-sided test rejects "effect ≤ 0" **and**
  the point estimate is in the declared direction.
- **NOT_SUPPORTED:** an equivalence (TOST-style) test shows the effect < SESOI.
  Absence of significance alone is never NOT_SUPPORTED.
- **INCONCLUSIVE:** otherwise (the interval spans both 0 and the SESOI).

**Composite labels** are derived strings for humans. For example,
"supported, economically material, robust, unreplicated" requires every
component. SUPPORTED on the statistical axis alone never implies economic
materiality.

---

## 12. Pilot-1A worked example

This applies the framework to the observed evidence. No re-test and no new
data. [I] The framework's rules were fixed above, independent of these
numbers.

| | `standardize(ret)` | `rank(ret)` | `rank(rolling_mean(ret,5))` |
|---|---|---|---|
| governance_validity | VALID | VALID | VALID |
| canonical class (rank endpoint) | C1 | **C1** (same class) | C2 |
| evidence_role of holdout | CONTAMINATED (Phase-5A exposure) | CONTAMINATED; also ungoverned (DEFER) | CONTAMINATED; ungoverned (DEFER) |
| evidence_role of IS/OOS | EXPLORATORY | EXPLORATORY | EXPLORATORY (generated after IS/OOS feedback) |
| design_adequacy | UNDERPOWERED (minimum detectable rank-IC ≈ 0.08–0.09 vs a plausible SESOI of ≤ 0.03) | same | same |
| statistical | **NOT_ASSESSED** | **NOT_ASSESSED** | **NOT_ASSESSED** |
| economic (exploratory report) | unfavorable: net spread IS −0.0003/day, OOS ≈ 0, holdout −0.0020/day; turnover ≈70%/day | identical portfolios ⇒ same | mixed: IS negative, OOS positive (development only) |
| robustness | NOT_ASSESSED (single construction; the subperiod table is exploratory) | — | — |
| replication | NOT_ATTEMPTED | NOT_ATTEMPTED | NOT_ATTEMPTED |
| production | NOT_CERTIFIED | NOT_CERTIFIED | NOT_CERTIFIED |

- **None** is scientifically supported, and **none** is "not supported". The
  evidence is structurally incapable of either verdict. The correct system
  output is *"no confirmatory conclusion available: contaminated and
  underpowered confirmation data"*.
- **Redundancy:** Exp 2 would not be a separate statistical test. It is C1
  again. Its Phase-8 slot stays consumed (a historical governance fact, not
  rewritten), but the scientific layer counts one test for C1 and records a
  **search-efficiency finding**. Going forward, canonicalization before
  evaluation would have skipped it.
- **Phase-5A exposure:** this alone removes the confirmatory role of the
  2026-07-01…09-15 window, whatever the Phase-8 mechanics say. It would even
  if the power were adequate.
- **Exp 3's OOS Sharpe 2.73** is precisely the kind of adaptively selected
  development statistic the framework refuses to treat as evidence. It is the
  winner of a small adaptive search on a window the generator had seen through
  earlier feedback.

---

## 13. Required changes to the current architecture

**Must stay intact (sealed guarantees):**
- Phase 6: admission and trust boundary; no provider fields; PIT
  vintage rules.
- Phase 7: purge rules; single computation authority for metrics.
- Phase 8: append-only registry; identities; single-use exact holdout
  mechanics; budget locks.
- Phase 9: write-ahead generation; proposal-before-evidence; structural
  firewall; family-escape prevention.
- The Pilot-1A harness temporal firewall (§26b TF).
- **The Phase-8 DecisionRecord keeps its meaning (governance). It is not
  repurposed as a scientific verdict.**

**Placement [D]:**
1. **New layer after Phase 8: "Scientific Evidence Protocol" (Phase 10).** It
   owns the confirmatory study, pre-registration, canonicalization,
   exposure-ledger reading, design adequacy, confirmatory inference and the
   ScientificAssessment. It consumes Phase-7 records and Phase-8 governance
   **read-only**.
2. **Phase 7 (versioned extension, a new phase-scoped change):**
   - export per-date series per fold (IC, rank-IC, gross and net spread,
     turnover);
   - fold-level HAC SEs with an ex-ante lag rule;
   - optional block bootstrap;
   - allow **development-only partitions** (no holdout fold) for exploration.
     Sealed Phase 7 requires a holdout fold in every evaluation, which forces
     exploration to "consume" holdouts it should never touch.

   This is a sealed-authority change and needs its own authorization.
3. **Phase 8 (versioned extension):**
   - holdout consumption by a **study** (batch) identity;
   - the persistent **cross-run** holdout/exposure ledger (merging deferred
     item 7).
4. **Phase 9:**
   - reclassify OOS feedback explicitly as development evidence (a
     semantics/doc change);
   - add promotion as a governed step, with the generator never seeing
     confirmation data;
   - apply FIX B (temporal provenance) as a prerequisite.
5. **Data:** a genuinely untouched confirmation dataset (see §17), a
   prerequisite for any SUPPORTED state.

---

## 14. Data and metadata requirements

| Requirement | Status |
|---|---|
| Full hypothesis and experiment history, lineage | **available** (Phase 8/9 registries, journals) |
| Canonical signal classes | **missing** (needs a rule-based canonicalizer) |
| Per-date IC / rank-IC series per fold | **missing** (only aggregates recorded) |
| Per-date net long-short and turnover | **partially available** (`portfolio_accounting`, `cost_adjusted_series`; primary construction only) |
| Fold-level SEs (HAC / bootstrap) | **missing** (only pooled t-stats) |
| Subperiod / universe / parameter sensitivity | **partially available** (subperiod; parameter sensitivity removed in Pilot 1A for leakage; universe variants inert) |
| Transaction-cost sensitivity | **partially available** (single cost; the grid mechanism exists) |
| Dependence among candidates (return-series correlations) | **partially available** (redundancy mechanism exists; not populated) |
| Benchmark factor-model residuals | **missing** (US CAPM only; no certified FF/CH for this universe) |
| Effective number of independent tests | **fundamentally difficult** (not reliably estimable from few, dependent, adaptive trials) |
| Exposure / contamination history (human, generator, pretraining) | **missing**; pretraining exposure is **fundamentally difficult** (cutoff dates are coarse; memorization is unobservable) |
| Holdout exposure history across runs | **missing** (per-run in-memory only) |
| Untouched confirmation data | **missing** (Gate-B window contaminated) |
| Replication datasets (other market, later period) | **missing** (China fundamentals path blocked in 5B; prospective data only after 2026-09-15) |
| Power inputs (cross-sectional IC dispersion, autocorrelation) | **partially available** (derivable from exploration series once exported) |

---

## 15. Failure modes and adversarial cases

1. **Selection leaking into confirmation** through the promotion rule (e.g.
   promoting on a statistic computed on the confirmation window). Guard:
   promotion reads only development evidence, enforced like the TF firewall.
2. **Pre-registration theater:** an analysis plan edited after peeking. Guard:
   hash-frozen before the confirmation window is readable; a ledger check.
3. **Contaminated "untouched" data:** vendor restatements, or humans viewing
   dashboards. Guard: exposure-ledger discipline; this can never be fully
   proven [I].
4. **SESOI gaming:** choosing a tiny SESOI to reach SUPPORTED, or a huge one to
   reach NOT_SUPPORTED. Guard: justified ex ante from external evidence,
   bounded by policy.
5. **Canonicalizer incompleteness** lets non-obvious equivalents through.
   Accepted; documented nonclaim; exploration counts stay honest.
6. **Regime change** makes prospective confirmation "fail" a real effect.
   Report as NOT_REPLICATED with date context; do not overwrite.
7. **Underpowered studies repeatedly rerun until significant.** Guard: the
   design-adequacy gate plus study-level α allocation per window.
8. **LLM memorization** creating in-sample fits. Guard: confirmation windows
   dated after the model's training cutoff.
9. **Dependence across the confirmatory family.** Holm stays valid; power
   loss is accepted.
10. **Cost-model optimism.** The economic axis must use declared,
    conservative costs; a sensitivity grid should be pre-declared.

---

## 16. Explicit nonclaims

This framework does not claim:
- general semantic-equivalence detection;
- valid inference on data used for selection;
- a reliable effective-number-of-tests estimate;
- elimination of pretraining leakage;
- production readiness;
- causal mechanisms;
- that rank-IC is the right endpoint for every hypothesis;
- that SUPPORTED means profitable after costs;
- that any Pilot-1A factor is supported or refuted.

---

## 17. Open questions

1. What confirmation data can be obtained? Options: a longer and broader US
   history (licensing and survivorship-safe membership needed), prospective
   accrual (slow: 3 months ≈ 60 days, still underpowered for N = 26), or a
   larger universe (the most effective lever for power).
2. How should α be allocated across sequential confirmation windows (fixed per
   window vs α-spending vs e-value wealth)?
3. What is the right default SESOI in rank-IC units for daily horizons on large
   universes? Literature-informed but disputable.
4. Should the generator see *any* development metrics, or only governance
   status? More feedback means better exploration but more development data
   consumed.
5. How should replication across overlapping universes be discounted in
   practice with few studies?
6. Is a batch holdout acceptable to Phase-8 governance semantics, or is a new
   study-level governance object cleaner?

---

## 18. Recommended next phase

**RECOMMENDED SCIENTIFIC ARCHITECTURE.** An exploration/confirmation split:
- governance (Phase 8) unchanged in meaning;
- a new Scientific Evidence Protocol layer owning pre-registered confirmatory
  studies, canonical classes, the exposure ledger, design adequacy,
  confirmatory inference (Holm, HAC, bootstrap) and the multi-axis
  ScientificAssessment;
- Phase 7 extended to export per-date fold series with fold-level SEs and to
  allow development-only partitions;
- Phase 8 extended with study-level holdout consumption and a cross-run
  exposure ledger;
- FIX B as a prerequisite.

**MINIMUM DEFENSIBLE VERSION.**
1. The ScientificAssessment multi-axis record, where `statistical =
   NOT_ASSESSED` unless both confirmatory and adequate.
2. A confirmatory-study pre-registration object: endpoint, direction, SESOI,
   α, analysis plan, candidate set, window, hash-frozen.
3. A design-adequacy (power) gate.
4. An exposure ledger with at least static declarations: the Phase-5A window,
   generator-visible windows, the model cutoff, and holdout consumption across
   runs.
5. A rule-based canonicalizer for rank- and affine-invariant transforms.
6. Fold-level HAC inference on exported per-date series.
7. Holm over the confirmatory family.

This version will correctly output "NOT_ASSESSED / UNDERPOWERED /
CONTAMINATED" on today's data. That is its value: it prevents unjustified
claims.

**STRONGER FUTURE VERSION.**
- Prospective sequential confirmation with e-value martingales and e-BH.
- Exploration reports with Romano-Wolf/SPA, DSR and PBO.
- Marginal-contribution tests vs a certified benchmark factor set.
- Hierarchical (JKP-style) shrinkage across signal themes and markets.
- Cross-market replication once PIT-certified non-US data exist.
- A reusable-holdout-style controlled query interface, researched for time
  series first.

**WHAT SHOULD NOT BE BUILT YET.**
- LLM- or learned semantic-equivalence inference.
- A single composite "factor score".
- DSR, PBO or SPA as decision gates.
- Online FDR or α-investing over exploration p-values.
- A reusable holdout on financial time series.
- Hierarchical Bayesian machinery (too few factors and markets).
- Any mechanism that would let the current Gate-B data produce SUPPORTED.
- Semantic-equivalence work beyond the declared invariance rules.

**Recommended next phase content (Phase 10 — Scientific Evidence
Protocol):**
- **(a)** freeze this ontology, state machine and pre-registration contract
  (design barrier);
- **(b)** a versioned Phase-7 extension for per-date fold series,
  fold-level HAC and development-only partitions;
- **(c)** a Phase-8 extension for study-level holdout consumption and the
  cross-run exposure ledger, merged with FIX B;
- **(d)** the minimum-version scientific layer;
- **(e)** a separate data-acquisition track scoped to obtaining a genuinely
  untouched, adequately powered confirmation dataset (a larger universe
  and/or longer history), without which no future SUPPORTED claim is
  reachable;
- **(f)** P1A-SV (the full-package secret-value sweep) before any further
  real-model run.

---

## Sources

- Harvey, Liu & Zhu (2016) — https://academic.oup.com/rfs/article/29/1/5/1843824
- Hou, Xue & Zhang (2020) — https://academic.oup.com/rfs/article-abstract/33/5/2019/5236964
- Jensen, Kelly & Pedersen (2023) — https://onlinelibrary.wiley.com/doi/full/10.1111/jofi.13249
- Chen, Lopez-Lira & Zimmermann — https://arxiv.org/abs/2212.10317
- McLean & Pontiff (2016) — https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.12365
- Giglio, Liao & Xiu (2021) — https://academic.oup.com/rfs/article-abstract/34/7/3456/5911131
- Dwork et al. (2015) — https://www.science.org/doi/10.1126/science.aaa9375
- Bailey & López de Prado (2014), DSR — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551
- Bailey, Borwein, López de Prado & Zhu (2017), PBO — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253
- Wang & Ramdas (2022), e-BH — https://academic.oup.com/jrsssb/article/84/3/822/7056146
- Online FDR (LORD) reference implementation — https://academic.oup.com/bioinformatics/article/35/20/4196/5380770
- LLM alpha mining: AlphaAgent https://arxiv.org/abs/2502.16789 ; Chain-of-Alpha https://arxiv.org/abs/2508.06312 ; QuantaAlpha https://arxiv.org/html/2602.07085 ; LLM evolutionary factor search https://arxiv.org/abs/2507.17211
- Classical references cited from the literature (White 2000; Hansen 2005;
  Romano & Wolf 2005; Holm 1979; Benjamini & Hochberg 1995; Benjamini &
  Yekutieli 2001; Foster & Stine 2008; Javanmard & Montanari 2018; Vovk & Wang
  2021; Ramdas et al. 2023; Lo 2002; Ledoit & Wolf 2008; Feng, Giglio & Xiu
  2020; Chordia, Goyal & Saretto 2020; Harvey & Liu 2020; Novy-Marx & Velikov
  2016; Arnott, Harvey & Markowitz 2019; Gelman & Loken 2014; Nosek et al.
  2018; López de Prado 2018) are cited from their published versions and
  were not re-fetched in this review.
