# Dataset v1.2 — P-Q v2 Methodology (frozen; document-only; NOT implemented)

- **Status:** FROZEN METHODOLOGY AUTHORITY. Authorized by "P-Q v2 DOCUMENT-ONLY
  METHODOLOGY FREEZE", on master `372573b`.
- **Not authorized here:** no implementation, Dataset v1.3, overlay, Campaign or K-PIT
  record. Each requires a separate authorization.
- **Identity:** P-Q v2 is a **new methodology identity**. It does **not** rewrite P-Q
  v1 (`docs/discovery_dataset_v1_2_pq_quarantine_rules.md`,
  `df5bec69103eca5ad9196b3342e5013fab30a3b85cdb3a5332b033dcac5db886`). Historical
  Campaign-1 (`ddd152dc…430d`) stays governed by P-Q v1 and is permanently
  **7 NOT_ASSESSED**. No historical ledger, report or artifact is rewritten.
- **Sources:** the planner's post-Campaign-1 F-1 review, the P-Q v2 mathematical
  review, the Candidate-E verification, the Gate-6 ρ-bound review, the final
  consolidation review and the H4/H5 exact-assignment review, all checked against the
  frozen D5 (`qc.py`), D7a (`returns.py`), D7b (`delisting_bridge.py`), D7c
  (`trusted_inputs.py`), Phase-6 (`spec/transforms.py`), Phase-7 (`portfolio.py`,
  `forward_returns.py`, `engines/portfolio_sort.py`) and P-Q v1 (`quarantine.py`)
  authorities.

**Tags used throughout:** **[T]** = theorem / derived from frozen semantics;
**[A]** = declared sensitivity assumption; **[N]** = non-claim / out of scope.

---

## 1. Scientific interpretation (Decision 1)

- **[A]** P-Q v2 is a **PREDECLARED CORRECTION-SENSITIVITY MODEL**, not a
  truth-containment model. Its correction set Ω is a frozen sensitivity model.
- **[N]** P-Q v2 does **not** claim that every possible true correction lies in Ω.
- **The final proposition (§13)** holds for correction states **inside Ω only**.

## 2. Definitions

- **Formation and universe:** formation f; universe U_f (D6, e.g. TOP_500); G = 5
  groups.
- **Grouping:** `engines/portfolio_sort.assign_groups`: over the n non-NaN
  (winsorized) factor values, `rank(method="first")`, bucket = ⌊(rank−1)·G/n⌋ + 1.
  Bucket sizes s_g are a function of n.
- **Economic legs:** for sign +1, long = bucket G and short = bucket 1. For sign −1
  they swap (D7b `_scenario_panels` leg mapping).
- **Leg return:** R_g = the mean of scenario values over members with a non-NaN value
  (the existing NaN denominator rule, `group_return_stats`).
- **Turnover:** `portfolio._turnover_series`: TO_t = changed_t / |U_{t−1} ∪ U_t|,
  counted over all groups; cost_t = c·TO_t with c = bps/10⁴.
- **Economic long-short:** LS^X_t = R^X_{econLong,t} − R^X_{econShort,t} − cost_t, for
  X ∈ {T0, TS}.
- **Transition multiplier:** for trusted session s of key K,
  m_s = close_s / close_{prev(s)} · φ_s = 1 + r_s, where φ_s is the visible corporate-
  action factor (`compute_adjusted_returns`). For a VALID session, m_s > 0
  (D7a enforces r_s > −1).
- **Flag:** a D5 `CORP_ACTION_SUSPECT` session s. Its source set is
  Q(s) = {close(prev s), close(s), φ_s}.

## 3. Correction-sensitivity set S2 (Decision 2)

**[A]** For a flagged transition with observed multiplier m_obs > 0:

  **Ω_S(m_obs) = [min(0.5, m_obs), max(1.5, m_obs)]**

- **Interpretation:** the corrected transition lies anywhere between the observed
  suspect-sized transition and the frozen non-suspect region.
- **The constants 0.5 and 1.5** are anchored to the D5 L1 suspect threshold
  (|m − 1| > 0.5) and the Gate-6 mechanical-explanation window (a ∈ [0.5, 1.5]). **They
  are not empirically calibrated error bounds.** There is no global K.
- **Relative correction set:** Q_s = Ω_S(m_s)/m_s = [min(0.5, m_s)/m_s, max(1.5, m_s)/m_s].
  It always contains 1: if m_s > 1.5 it is [0.5/m_s, 1]; if m_s < 0.5 it is
  [1, 1.5/m_s].

**Correction-set identity (CS-ID)** = SHA-256 of the exact canonical line:

```
{"correction_set":"pq-v2-S2","form":"[min(lo,m_obs),max(hi,m_obs)]","hi":1.5,"lo":0.5,"model":"PREDECLARED_CORRECTION_SENSITIVITY"}
```

## 4. Identity premise (Decision 3)

- **[A]/[N]** D4 security identity is a premise shared by B and P-Q v2. **P-Q v2 does
  not sensitivity-test the correctness of security identity.**
- **No ρ is used to model identity mismatch.**

## 5. Exact-dependency invariant (Decision 4)

**[T-rule]** Uncertainty propagates only through **exact mathematical dependency**
wherever it can be derived from frozen production semantics.
- **Cancellation:** algebraic cancellation **must** be recognized where provable.
- **Fallback:** conservative structural propagation is used only where exact
  dependency cannot be derived.
- **Supersedes P-Q v1** lineage absorption (Q3.1 TRI segment propagation) wherever an
  exact dependency is available.

## 6. Labels and Candidate E (Decision 5)

- **Construction [T].** D7a stores daily returns only. D7b / P7-B compound them as
  `(1 + window).prod(skipna=False) − 1` over the h D7a sessions strictly after f,
  ending at e (or at the observed segment end e′ for terminal labels).
- **Proposition 1 [T] (label identity).** For a COMPLETE label with VALID sessions in
  (f, e′]:
  - 1 + R = ∏_{d∈(f,e′]} m_d = (close_{e′} / close_f) · ∏_{d∈(f,e′]} φ_d;
  - **interior close errors cancel**;
  - the label depends on Q(s) **iff s ∈ [f, next(e′)]**.

  *Proof sketch:* m_d = close_d / close_{prev d} · φ_d telescopes. close(x) appears
  only for x ∈ {f, e′}, and φ_d only for d ∈ (f, e′]. Hence Q(s) intersects the
  dependency iff prev(s) ∈ {f, e′}, s ∈ {f, e′}, or s ∈ (f, e′].
- **Correction of P-Q v1 false negative:** **s = f is included** (close_f ∈ Q(f)).
- **EMPTY labels** (n_obs = 0, R ≡ 0) depend on no primitive and are **not** quarantined.

## 7. MISDATED corporate actions (Decision 6)

**[A]** For flagged s, the paired candidates are **only** already-recorded split or
cash-distribution events of the same D4-bound security, inside its D4 interval, at
trusted session d ≠ s with |pos(d) − pos(s)| ≤ 5.
- **Not uncertain:** other sessions in the window.
- **Hypothesis:** each candidate is a discrete hypothesis that "the event belongs at s,
  not d".
- **Effect on a dependency window W:**
  - W contains exactly one of {s, d}: recorded factor φ_d^{+1} (s only) or φ_d^{−1}
    (d only);
  - W contains both: cancellation (factor 1);
  - W contains neither: no effect.
- **Same-day events** use the existing combined factor.
- **[N]** Candidates are mutually exclusive: one misdating at a time.

## 8. Label return envelope (Decisions 9, 23)

**Proposition 3 [T under A].** For each flagged s on which a label depends, the
label factor set is:
- **s ∈ (f, e′]:** Λ_s = Q_s (the CA branch; the endpoint-close cases at s = next(f)
  and s = e′ also give factors in Q_s);
- **s = f:** Λ_s = Q_s⁻¹;
- **s = next(e′):** Λ_s = Q_s⁻¹.

Each MISDATED candidate adds an alternative factor (§7), so Λ_s^tot = Λ_s ∪ {the
candidates' factors}. **Every factor is positive, and every set contains 1.**

- **Corrected label:** 1 + R_corr = (1 + R_obs) · ∏_s λ_s with λ_s ∈ Λ_s^tot, so:
  - **R_lo = (1 + R_obs) · ∏_s min Λ_s^tot − 1;**
  - **R_hi = (1 + R_obs) · ∏_s max Λ_s^tot − 1.**
- *Proof sketch:* a product of positive factors is monotone in each factor.
- **Firewall [T-rule]:** if e′ = 2023-06-30 (`DEVELOPMENT_END`), next(e′) lies in the
  reserve and its flag state is unknown. **The label is UNKNOWN**, and the affected
  economic portfolio-period is **NOT_ASSESSED**. Reserve data is never read and never
  assumed clean.

## 9. Typed missingness and T0/TS (Decisions 10, 11)

- **[T]** D7b typed missingness (INCOMPLETE → NaN) stays authoritative. P-Q never
  converts a NaN into a bounded value.
- **[T] Composition order:** correct the observed component R_obs under §8 first, then
  apply the frozen D7b scenario v^X = (1 + R)(1 + τ^X_leg) − 1, where τ^{T0} = 0 and
  τ^{TS} = −1 (long leg) or +1 (short leg) or 0 (otherwise).
  - 1 + τ ≥ 0, so v^X is monotone non-decreasing in R, and the bounds pass through.
  - Nothing is counted twice.
- **The matrix is {B, L} × {T0, TS}** where assessable.

## 10. Exact TRI and factor dependency (Decisions 7, 8)

- **Proposition 2 [T] (TRI prefix cancellation).** D7c TRI is segment-1 only:
  TRI(anchor) = 1 and TRI(u) = ∏_{j∈(anchor,u]} m_j over one uninterrupted
  valid-return run. For y < x in the same segment:
  **TRI(x)/TRI(y) = ∏_{j∈(y,x]} m_j = (close_x / close_y) · ∏_{(y,x]} φ.** The shared
  history before y cancels, and interior close errors cancel.
- **Rule:** P-Q v2 **must not** propagate TRI quarantine from the first suspect return
  to the end of the segment when an expression depends only on a later TRI ratio
  window.
- **Indexing [T]:** the D7c bridge lag holds data dated prev(row). So at row f, a field
  is dated f−1, `lag(x,k)` is dated f−1−k, and a rolling op over w rows ending at row
  r covers dates (r−1−w, r−1].

**Campaign-1 dependency table [T]**:

| Hypothesis | Expression | Exact dependency at row f |
|---|---|---|
| C1-H1 | `lag(tri,21)/lag(tri,252)` | algebraic: m_j for j ∈ **(f−253, f−22]**; flags s ∈ [f−253, next(f−22)] (Prop. 1 form, y = f−253, x = f−22) |
| C1-H2 | `lag(tri,21)/lag(tri,126)` | algebraic: **(f−127, f−22]** |
| C1-H3 | `−tri/lag(tri,21)` | algebraic: **(f−22, f−1]** |
| C1-H4 / C1-H5 | `standardize(mom) × rank(rolling_mean(dv,21)/rolling_mean(dv,252))` | momentum: algebraic, as H1 / H2. Volume: numerical: dv on **(f−22, f−1]** (21-row mean) and **(f−253, f−1]** (252-row mean). **Cross-sectional:** standardize (μ, σ) and rank depend on the whole valid D7c row |
| C1-H6 | `(ratio−1) / lag(rolling_std(ret,231),21)` | numerator: algebraic over (f−253, f−22]. Denominator: **numerical** over every return in **(f−253, f−22]**, with **no cancellation** (variance changes when an interior close moves r_s and r_{next s} in opposite directions) |
| C1-H7 | `(ratio−1) / lag(rolling_std(ret,105),21)` | numerator: algebraic over (f−127, f−22]. Denominator: numerical over **(f−127, f−22]** |

**Certified generic rules (Decision 8)** — this is **not** a general symbolic engine:

| Node | Rule | Kind |
|---|---|---|
| products and quotients of TRI lags (one security, one segment) | log-linear: log TRI(u) = Σ_{j≤u} log m_j; shared prefixes cancel; dependency = the support of the nonzero coefficients | algebraic |
| `lag` | shift | — |
| `rolling` mean / sum / std / min / max | the window (union) | numerical; no cancellation |
| ordinary arithmetic | conservative union unless a separately certified simplification applies (multiplying or subtracting a constant keeps the dependency) | — |
| `rank` / `winsorize` / `standardize` | dependency on the whole row | cross-sectional |

- **Primitive dependency [T]:** `daily_total_return(u)` depends on Q(s) iff
  u ∈ {prev s, s, next s}; `dollar_volume(u)` iff u ∈ {prev s, s}. These match the
  P-Q v1 primitive rules.
- **DV non-claim [A]/[N]:** dollar_volume = raw_close × raw_volume (D7c).
  - Under the frozen model, CA-only correction branches do **not** change raw dollar
    volume.
  - DV uncertainty comes **only** from the price-error branch: dv(u)^corr =
    dv(u)^obs · κ, with κ in the Q_s-derived set for that position.
  - This is a semantic statement under D7c definitions, not a general market-data
    claim.

## 11. Assignment

**Shared deterministic precondition [T] (Decision 18).** D7b `winsorize_panel` clips the
final F per formation at its q- and (1−q)-quantiles (pandas linear interpolation), then
`assign_groups` ranks with `rank(method="first")`.
- **Why it can matter:** clipping is monotone non-decreasing, so it can only change
  membership through the new ties it creates. Those are the values that become equal to
  a cap, and they are then ordered by row position instead of by value.
- **Block condition:** membership equals bucketing by the order of unwinsorized F
  whenever, in every correction state, the low-cap tied block {i : F_i ≤ cap_lo} fits
  inside bucket 1 and the high-cap block {i : F_i ≥ cap_hi} fits inside bucket G.
- **Deterministic sufficient check:** let h = q·(n−1). In any state,
  cap_lo ≤ the (⌊h⌋+2)-th smallest value.
  - Let κ_lo be the (⌊h⌋+2)-th smallest upper score bound and κ_hi the (⌊h⌋+2)-th
    largest lower score bound.
  - The check passes if #{i : v_i^lo ≤ κ_lo} ≤ s_1 and #{i : v_i^hi ≥ κ_hi} ≤ s_G.
  - Here [v^lo, v^hi] is the member's certified ranking-score interval: g for H4/H5,
    the value interval for the simple class.
- **If the check fails: L_t = NOT_ASSESSED.** The old A-W assumption is **not** used.

### 11.1 Simple class (Decision 12): C1-H1–H3, C1-H6–H7

- **Condition [T]:** the factor is an own-cell, monotone-composable function of the
  stock's own dependency (no cross-sectional node), so **clean relative order is
  invariant**.
- **Notation:** A_t is the set of members whose factor value depends (§10) on a
  flagged primitive with a non-trivial Λ (input-uncertain).
- **Proposition 4 [T] (simple coverage).** Every reachable long leg has the form
  TopClean(s_L − j) ∪ S, with S ⊆ A_t, |S| = j, 0 ≤ j ≤ min(|A_t|, s_L). The short leg
  is symmetric, with BottomClean.
  - *Proof sketch:* clean values and their order are fixed. Placing uncertain values
    anywhere is a superset of their Ω-reachable values.
- **Turnover:** D_t ≤ min(|U_t|, G·|A_t|). *Proof sketch:* at most |A_t| clean
  members cross each of the G−1 boundaries, plus the movers themselves.

### 11.2 H4/H5 class (Decisions 13–19): F = standardize(M) × rank(V)

**Certified only when all of these hold:**
- M is a product or quotient of TRI lags of one security in one valid segment;
- V is the frozen ratio of rolling means of dv;
- standardize has ddof = 0 over the full valid D7c cross-section (non-NaN M);
- rank(V) uses `method="average"`, NaN kept, over the full valid cross-section;
- D7b then restricts to U_f, winsorizes the final F, and assigns groups with
  `rank(method="first")`.

**Standardization (Decision 14) [T].** Z_i = (M_i − μ)/σ and F_i = Z_i·R_i.
- **When σ > 0:** F_i > F_j ⟺ g_i > g_j, with **g_i = R_i(M_i − μ)**. The common
  factor 1/σ > 0 drops out.
- **Precondition:** at least two distinct clean M values (so σ > 0 in every
  correction state). If it fails: **L_t = NOT_ASSESSED** (fail-closed / typed).

**Score intervals (Decision 15) [T].**
- **Momentum M_s:** for each suspect, M_s ∈ [M_s^obs · ∏ min Λ, M_s^obs · ∏ max Λ],
  over its algebraic dependency (Prop. 2 / Prop. 3 factors). Clean M values are fixed.
- **Mean μ:** μ ∈ 𝓜 = (Σ_clean M + Σ_suspect [M_s^lo, M_s^hi]) / n_M. This is one
  interval, or a finite union under MISDATED alternatives.
- **Volume V_s:** V_s ∈ [V_s^lo, V_s^hi]. V is linear-fractional in its uncertain dv
  values, so its extremes are at the box vertices.
- **Volume rank (Decision 19):**
  - clean V order is fixed;
  - for clean i, R_i ∈ [c_i + n_i^min, c_i + n_i^max], with c_i its clean average
    rank, n^min the number of suspects whose V interval lies entirely below V_i, and
    n^max the number whose interval reaches below V_i (+½ conventions for ties under
    `average`);
  - a suspect's R_s lies between its insertion positions at its interval ends;
  - the exact insertion-vector set is not enumerated in production.
- **Score interval:** g_i^lo = min and g_i^hi = max of R(M − μ) over μ ∈ 𝓜, R in its
  range, and M fixed (clean) or in its interval (suspect). The extremes are at endpoint
  combinations (bilinear).
- **Proposition 5 [T] (score coverage).** Every Ω-reachable score of i lies in
  [g_i^lo, g_i^hi].
  - *Proof sketch:* the true joint state of (M, μ, R) lies in the relaxed product set.
  - **Disclosed relaxations:**
    - (a) the box relaxation of R ignores that insertion counts are monotone;
    - (b) the coupling of suspect M values through μ is ignored;
    - (c) **M/V coupling is relaxed by a Cartesian superset** (a single correction
      picks one branch: CA affects M, price error affects M at endpoints and dv).

**Certain / possible sandwich (Decision 16) [T].** For a top leg of size s_L:
- **C_L** = {i : #{j ≠ i : g_j^hi ≥ g_i^lo} < s_L};
- **P_L** = {i : #{j : g_j^lo > g_i^hi} < s_L};
- **𝒯_L** = {T : C_L ⊆ T ⊆ P_L, |T| = s_L}.

The bottom leg is symmetric: C_B = {i : #{j ≠ i : g_j^lo ≤ g_i^hi} < s_B},
P_B = {i : #{j : g_j^hi < g_i^lo} < s_B}.
- **Proposition 6 [T] (sandwich coverage).** Every Ω-reachable top or bottom assignment
  lies in the corresponding sandwich. It is a **provable superset, not exact.**
  - *Proof sketch:* if i is top in some state, every j with g_j^lo > g_i^hi ranks above
    it in every state, so fewer than s_L such j exist and i ∈ P_L. If i ∈ C_L, at most
    s_L − 1 stocks can reach its minimum, so it is top in every state. Ties are handled
    conservatively through ≥ / ≤.

**Turnover (Decision 20) [T].** D_t = the number of stocks for which a group other than
B's group is possible under the score intervals (the ambiguous band, applied at every
boundary). The simple class keeps its tighter §11.1 rule.

## 12. Leg envelope and turnover bound (Decisions 17, 20)

**Proposition 7 [T] (leg envelopes).**
- **Economic long leg:** R^lo = min over admissible memberships of the leg mean of
  worst-case values. A member's worst-case value is its observed v^X, or v^X(R_lo) if
  it is label-flagged (§8, §9).
  - **Simple class:** enumerate j and the number of valid-value candidates (the NaN
    denominator rule), taking the lowest.
  - **H4/H5:** C_L plus the (s_L − |C_L|) lowest-valued members of P_L ∖ C_L, under the
    same NaN rule.
- **Economic short leg:** R^hi is symmetric (highest values, v^X(R_hi)).
- **No dropping, regrouping, renormalization or substitution** beyond these semantics.
- *Proof sketch:* the membership set ⊇ reachable, and each value is its extreme over Ω.

**Proposition 8 [T] (turnover).** Taking D_t from §11.1 or §11.2:
**TO^hi_t = min(1, (changed^B_t + D_t + D_{t−1}) / |U_{t−1} ∪ U_t|)** and
cost^hi_t = c·TO^hi_t.
- *Proof sketch:* a member's changed status flips only if its group at t or t−1
  differs from B, and |U| is fixed because flagged values are present.

## 13. Lower envelope and the final proposition (Decision 21)

- **Formula:** **L^X_t = R^{lo,X}_{econLong,t} − R^{hi,X}_{econShort,t} − cost^hi_t.**
- **Sign transformation [T]:**
  - **sign +1:** econLong = bucket G, econShort = bucket 1 (D7b `_scenario_panels`).
    L is exactly the formula above.
  - **sign −1:** the D7b economic leg mapping swaps (econLong = bucket 1,
    econShort = bucket G). D7b admits sign −1 only for `SIGN_MINUS_ONE_ADMITTED` = {IC,
    RANK_IC, LONG_SHORT, SHARPE}, with `orientation_rule` = NEGATED: it computes the
    raw metric and negates value and t-statistic (`economic = −value`).
  - **Sign −1 is not certified [T-rule, fail-closed]:** this freeze does not certify
    that the economic-leg L above equals the lower bound of the negated raw D7b metric,
    in particular how cost behaves under negation. For sign −1, **L_t = NOT_ASSESSED**
    until a separately frozen sign −1 rule exists.
  - **No Campaign-1 effect:** all certified Campaign-1 classes (C1-H1–H7) are sign +1.
- **Proposition 9 [T conditional on Ω].** For every correction state ω **inside the
  frozen P-Q v2 sensitivity model Ω** (S2, the MISDATED alternatives, exact-dependency
  propagation, D4 identity), and every X ∈ {T0, TS}:

  **LS^X_economic(ω) ≥ L^X_t**

  for sign +1, provided the deterministic preconditions hold (winsorization block condition,
  σ-support for H4/H5, no UNKNOWN reserve dependency, at least one valid value in every
  admissible leg configuration).
  - *Proof sketch:* Props. 4/6 give membership ⊇ reachable; Props. 3 and 7 give values
    at their extremes; the joint relaxation min(long) − max(short) is valid because the
    joint set ⊆ the product of marginals; Prop. 8 bounds the cost.
- **[N]** The statement "LS_true ≥ L" is **not** made outside Ω.

**When L_t is defined:** all preconditions above hold. Otherwise **L_t = NOT_ASSESSED**.

## 14. Evaluation and classification (Decision 22)

- **B** is the unchanged observed-data estimator. **L** is the conservative result
  under Ω. The same frozen screen is applied to both. Multiplicity is unchanged.

| B screen | L screen | Classification |
|---|---|---|
| PASS | PASS | correction-sensitivity screen PASS |
| PASS | FAIL | QUARANTINE_SENSITIVE; no advancement |
| FAIL | PASS | invariant violation; FAIL CLOSED |
| FAIL | FAIL | development screen FAIL |
| a required B or L statistic unavailable | — | NOT_ASSESSED |

- **[N]** No classification here is an ACCEPT or a confirmation.

## 15. Certification boundary (Decision 24)

| Expression class | Certified rule |
|---|---|
| sign +1 only (§13). C1-H1, C1-H2, C1-H3, C1-H6, C1-H7 (own-cell, no cross-sectional node, exact or numerical dependency per §10) | simple exact assignment (§11.1) |
| sign +1 only. standardize(M) × rank(V) under §11.2 semantics (C1-H4, C1-H5) | conservative sandwich (§11.2) |
| anything else | **needs a separately frozen dependency and assignment rule, or L_t = NOT_ASSESSED.** No silent generalization |

**Exact-dependency identity (ED-ID)** = SHA-256 of the exact canonical line:

```
{"rules":["tri_log_linear_prefix_cancellation","lag_shift","rolling_window_union_numerical","arithmetic_conservative_union","cross_sectional_whole_row","label_candidate_e_[f,next(e)]","dtr_{prev,s,next}","dv_{prev,s}","misdated_recorded_pm5","reserve_unknown_not_assessed"],"version":"pq-v2-ed-1"}
```

**Certified-expression identity (CE-ID)** = SHA-256 of the exact canonical line:

```
{"classes":{"simple_exact":["C1-H1","C1-H2","C1-H3","C1-H6","C1-H7"],"std_x_rank_sandwich":["C1-H4","C1-H5"]},"other":"NOT_ASSESSED_UNLESS_SEPARATELY_FROZEN","version":"pq-v2-ce-1"}
```

**Methodology identity** = the SHA-256 of this document as committed.

## 16. Oracle requirements (for future implementation)

The oracle is independent and brute force. On synthetic universes it enumerates finite
correction grids and **recomputes production-equivalent semantics from scratch**
(compounding, CA factors, TRI, standardize ddof = 0, average rank, multiply, universe
restriction, linear-quantile winsorize, `rank(method="first")` bucketing, NaN means,
T0/TS, turnover). It asserts:
- membership ⊆ the analytic set;
- the minimum true long-short ≥ L;
- turnover ≤ TO^hi;
- and it reports the tightness gap.

**Required cases:**
- Candidate-E: one suspect and multiple suspects;
- the formation endpoint (s = f) and the ending endpoint (s = e, next(e));
- interior close cancellation;
- MISDATED paired corporate action;
- EMPTY label;
- TRI prefix cancellation;
- simple-class assignment movement;
- H4/H5: clean-clean crossing, a suspect crossing a group boundary, volume-rank
  movement, coupled M/V uncertainty;
- ties; σ = 0; NaN;
- winsorization block-condition failure;
- the turnover upper bound.

The production bound must not share the oracle's algorithm.

## 17. Migration and versioning

- **New methodology:** P-Q v2 is new. P-Q v1 and Campaign-1 are unchanged; Campaign-1
  is permanently 7 NOT_ASSESSED.
- **Required bindings for any future use:** the methodology identity (this document's
  SHA-256), CS-ID, ED-ID and CE-ID.
- **Not created here:** no Campaign or K-PIT record.

## 18. Summary of non-claims [N]

- **The model:** it is not truth containment, and robustness to every possible
  correction is not claimed.
- **Data quality:** P-Q v2 doesn't bound the true return, doesn't certify data quality,
  and doesn't imply Gate-6 passed (Gate-6 stays INCONCLUSIVE).
- **Identity:** security identity is not sensitivity-tested.
- **MISDATED:** one misdating at a time.
- **DV:** the DV uncertainty model is a D7c-semantic statement.
- **H4/H5:** the sandwich is a superset, not exact.
- **Outcomes:** no confirmation, holdout or reserve validation.
- **Required disclosure:** every P-Q v2 robustness claim must state that **the correction
  model is declared, not empirically certified containment.**
  - Allowed wording: "survives the frozen P-Q v2 correction-sensitivity model."
  - Prohibited wording includes: "robust to every possible correction", "bounds the true
    return", "data quality certified", "Gate-6 passed", "identity errors tested",
    "confirmed", "holdout validated", "reserve validated".
