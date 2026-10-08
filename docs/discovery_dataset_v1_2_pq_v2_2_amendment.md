# P-Q v2.2 — parameterized expression-class certification (FROZEN 2026-10-08; document-only; NOT implemented)

- **Status:** FROZEN by user approval ("按建议冻结", 2026-10-08), roadmap M1.
- **Methodology identity:** P-Q v2.2 = the SHA-256 of **this document as committed**, always bound together with its base, P-Q v2.1 `66772bb1b12a134ca35a3ff5e3d475e00c998b95d3cdcb1e3223fd5f012d5d1e`. It amends the frozen P-Q v2.1 methodology
  (`66772bb1…5d1e`) **only** in §10 (the dependency node table), §15 (the certification
  boundary) and §16 (the oracle), and adds §15A.
- **Unchanged:**
  - every other section;
  - S2 / CS-ID;
  - the §8A cause model;
  - Props. 1–8 as stated.
- **Scope split** (the roadmap called M1 "+ the fundamentals / event-date sensitivity
  model"): that model is deferred to **M1b**, designed together with M3. Its semantics
  depend on the Dataset v1.3 contract, which does not exist yet.

## 1. Problem

- **The CE-ID is enumerative.** `certify()` admits exactly 7 canonical keys (C1-H1…H7);
  every other expression gets `PQV2_EXPRESSION_NOT_CERTIFIED`.
- **So the generator loop (M4) cannot produce assessable candidates.** A perturbed C1-H1
  (lag 252 → 251) is NOT_ASSESSED although its proof is identical (ADM-4).
- **The analyzer is already generic.** The dependency analyzer
  (`pqv2_dependency._analyze`) is a generic tree walker over the certified M§10 rules.
  What blocks generalization is the enumerated certification step, plus the evidence:
  the oracle and the proofs were exercised only on the 7 points.

## 2. Principle

- **Certification by grammar.** An expression is certified iff it parses into one of two
  frozen grammars (§15A). Its class is then determined by the grammar, not by a list.
- **Proof scope.** Each proposition's proof must already hold for **every** member of the
  grammar, not only for the C1 instances.
- **What changes in v2.2.** v2.2 does not add a new proposition. It:
  - states each existing proof's actual preconditions;
  - restricts the grammar to exactly those preconditions;
  - requires oracle evidence over randomly generated grammar members.
- **Backward compatibility.** The 7 C1 expressions remain certified in the same class as
  in v2.1.

## 3. Precondition audit of the existing proofs (planner reading; to be confirmed at review)

| Proposition | What its proof actually requires | Consequence for the grammar |
|---|---|---|
| **Prop. 2** (TRI prefix cancellation) | a log-linear TRI monomial with **zero net exponent** (a ratio form), inside one valid segment | the existing `_tri_support` total-exponent-zero check is the exact guard; an unbounded level → NOT_CERTIFIED |
| **Prop. 4** (simple coverage) | (i) own-cell: no cross-sectional node; (ii) the dependency set is a **superset** of the true dependency, so A_t is complete; (iii) the ranked member set and the bucket sizes are invariant under Ω, i.e. no finite ↔ non-finite change of the factor value. **Monotonicity is not used:** uncertain values are placed anywhere | any own-cell composition is admissible **if** (ii) and (iii) hold; (iii) needs denominator guards (§15A.1 D-rules) |
| **Winsorization block check** (simple class) | uncertain members get (−∞, +∞) score intervals (as already implemented) | no value-interval rule is needed for the simple class |
| **Props. 5–6** (STD_X_RANK sandwich) | M is a **pure** TRI ratio, so M_s scales multiplicatively by ∏Λ (an additive constant inside M breaks the multiplicative interval). V is linear-fractional in dv: a ratio of rolling dv means, short window ⊂ long window, both ending at f−1, so its extremes are at box vertices. Full-row standardize (ddof = 0) and average rank | M = `lag(tri,a)/lag(tri,b)` only; V = `rolling_mean(dv,w1)/rolling_mean(dv,w2)` only; the parameters are free within bounds |
| **Props. 7–8** | class-agnostic, given the assignment sets | unchanged |

**Finding (a latent constraint, not a v2.1 defect).** The v2.1 analyzer accepts
`standardize(ratio − c)`, because `Sub` with a constant returns the left dependency, but
Prop. 5's multiplicative M interval is wrong for `ratio − c`. v2.1 never reaches this
case, because only the enumerated C1-H4/H5 are certified, and their M is a pure ratio.
Under v2.2 the STD_X_RANK grammar must therefore **forbid** any non-ratio M. This is
mandatory.

## 15A. Certified grammars (new)

**Common rules:**
- the inputs are the D7c fields `tri`, `ret`, `dv` only;
- `sign = +1` (negation is expressed inside the expression);
- the maximum dependency lookback is **≤ 504 sessions**;
- every `lag(k)` has k ≥ 0, and every rolling window w satisfies **2 ≤ w ≤ 504**;
- the literal constants are finite and nonzero where they multiply or divide.

### 15A.1 SIMPLE_G (own-cell; Prop. 4)

```
E   := T | N | c·E | E ± c | E × E | E / D | E + E | E − E | lag(E, k)
T   := lag(tri,a) / lag(tri,b)        (0 ≤ a < b; a = 0 means tri)   — or any TRI monomial with net exponent 0
N   := rolling_{mean,std}(ret, w) | lag(N, k)
      | rolling_mean(dv,w1) / rolling_mean(dv,w2) | lag(…, k)
D   := T | rolling_std(ret, w) | rolling_mean(dv, w) | lag(D, k)   (denominator rules)
```

- **No cross-sectional node** anywhere.
- **Dependency rules:** the existing M§10 rules, with **one addition**. Non-constant
  `E + E` and `E − E` use the **conservative support union** of the two operands' supports.
  They never merge log-linear exponents, because a sum is not log-linear. The
  implementation must keep a support-only representation for these nodes.
- **Denominator rules (precondition (iii)).** A divisor must be one of:
  - a TRI ratio (strictly positive);
  - `rolling_mean(dv, w)`: corrected dv = dv·κ with κ > 0, so its sign and its zero-ness
    are invariant;
  - `rolling_std(ret, w)` with w ≥ 2. Its zero-ness under Ω is excluded by the inherited
    v2.1 assumption **[A-STD]**, now made explicit: a correction never makes a rolling std
    exactly zero. This is the same assumption that underlies C1-H6/H7 in v2.1, disclosed
    here and not newly introduced.

  Any other divisor (for example `rolling_mean(ret)` or a sum) → NOT_CERTIFIED.
- **Excluded:** `rolling_{sum,min,max}` and rolling over `tri` or `dv` other than the
  dv-mean forms. Their dependency rules exist in M§10, but they are not admitted until a
  later amendment adds oracle evidence for them.

### 15A.2 STD_X_RANK_G (Props. 5–6)

```
F := standardize( lag(tri,a) / lag(tri,b) ) × rank( rolling_mean(dv,w1) / rolling_mean(dv,w2) )
     with 0 ≤ a < b ≤ 504, 2 ≤ w1 < w2 ≤ 504
```

- The semantics are exactly as in §11.2: standardize with ddof = 0 over the full valid row;
  rank with `average`, NaN kept; D7b restriction, winsorization and `rank(first)`.
- Nothing else may wrap or combine F.

### 15A.3 Anything else

- `PQV2_EXPRESSION_NOT_CERTIFIED`, unchanged.
- No silent generalization: in particular, no other cross-sectional node, no `winsorize`
  inside the expression, and no event or fundamental inputs (those are M1b).

## 15. Identities (changes)

- **CE-ID v2.2:** the SHA-256 of the canonical line

  ```
  {"classes":{"simple_exact":"GRAMMAR_SIMPLE_G_v1","std_x_rank_sandwich":"GRAMMAR_STD_X_RANK_G_v1"},"bounds":{"lookback_max":504,"window_min":2,"window_max":504},"denominators":["tri_ratio","rolling_mean_dv","rolling_std_ret_assumption_A_STD"],"other":"NOT_ASSESSED_UNLESS_SEPARATELY_FROZEN","version":"pq-v2.2-ce-2"}
  ```

  - The grammar text of §15A is bound by the methodology hash.
- **ED-ID v2.2:** the v2.1 rule list plus the token `add_sub_support_union`; version
  `pq-v2.2-ed-3`.
- **CS-ID:** unchanged.
- **Historical identities are preserved:** the v2.1 document, its identities, its I1–I15
  evidence (`13062ce`) and its ADM evidence (`0bde51e`) remain unchanged.

## 16. Oracle requirements (additions)

- **Random grammar members.** The oracle generates random grammar members, with a frozen
  generator seed and **N ≥ 2,000 expressions per grammar**. It covers every production
  rule and the parameter bounds, including the extremes (a = 0; w = 2; w = 504; lookback
  = 504).
- **Per expression,** the existing brute-force checks on synthetic universes:
  - membership ⊆ the analytic set;
  - the minimum true long-short ≥ L;
  - turnover ≤ TO^hi.
- **Required adversarial cases:**
  - an `Add`/`Sub` support union where the two operands' supports cancel under exponent
    merging; the test fails if the exponents were merged;
  - a divisor near zero for each permitted denominator;
  - a forbidden divisor, which must be NOT_CERTIFIED;
  - `standardize(ratio − c)`, which must be NOT_CERTIFIED;
  - a lookback of 505 and a window of 1, both NOT_CERTIFIED;
  - the 7 C1 expressions, which must certify in the v2.1 class with byte-identical
    diagnostics.
- **Independence:** the oracle must not import or share the production analyzer.

## 17. Implementation outline (after freeze; separate implementation spec)

| Task | Work |
|---|---|
| PQ22-A | authority v2.2 constants and identities; v2.1 preserved |
| PQ22-C | grammar recognizer + Add/Sub support union + denominator rules in `pqv2_dependency` |
| PQ22-G | oracle random-grammar generator + adversarial cases |
| PQ22-H | evaluation binds the v2.2 identities; C1 replay is byte-identical |

- **Then:** a full I1–I15 recertification and a restarted PQ2-ADM.
- **Expected not to change:** D, E and F (their inputs are dependency windows and
  intervals, which are already parameter-driven). The implementation spec must verify
  this; it must not assume it.

## 18. Non-claims

- **What the grammar is.** It is a certification of *assessability* (a correction-
  sensitivity bound), not of usefulness or alpha.
- **The data model is unchanged.** UNKNOWN causes still carry no numeric bound;
  [A-STD] is an assumption, not a proven property.
- **The search space is larger, so multiplicity must be accounted for.** A larger
  certified space increases the multiplicity burden. Every generated candidate must
  be counted by the Phase-9/10 budget accounting (M4); certification does not reduce
  that obligation.

## Freeze decisions (user-approved recommendations)

1. **Bounds:** ACCEPTED, 504 sessions. Early long-lookback IS formations are NaN under PROPAGATE.
2. **Add/Sub support union:** ADMITTED in SIMPLE_G now.
3. **Oracle sample size:** N ≥ 2,000 per grammar, ACCEPTED as the minimum.
