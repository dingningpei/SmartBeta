# Campaign-4 — factor-method program on Dataset v1.2 (DRAFT for review; nothing frozen or authorized)

- **Status:** planner draft, 2026-10-14. It supersedes the S1 framing in
  `docs/campaign4_program_plan_draft.md`.
- **User direction (2026-10-14):** "重点是以现有的数据构建有效的因子挖掘和构建方法". The data
  is fixed at Dataset v1.2. The work is the discovery and construction method.
  - Path B (new data) is closed: `docs/dataset_v2_vendor_verification.md` §9.
- **Data-length disclosure (stated once):** the reserve's power limit still applies to the
  final confirmation (§5). It is a constraint on what can be *confirmed*, not an
  explanation of Campaign-3's result.

## 0. Why the method is the first suspect

**Campaign-3 IS evidence** (generator-visible tier, from the forwarding report):

| Scenario | n | min | q1 | median | q3 | max | positive |
|---|---|---|---|---|---|---|---|
| L, T0-K0 (no terminal stress) | 32 | −2.84 | −1.17 | **−0.81** | −0.39 | 1.41 | 5 |
| L, TS-K0 | 32 | −4.41 | −3.60 | −2.02 | −1.58 | 0.19 | 1 |

- **Power explains noise, not bias.** Short data makes estimates noisy around their
  true value. It does not push 27 of 32 candidates negative in their declared direction.
  Something systematic is wrong.
- **Facts found while drafting** (planner read of the frozen code and prompt):
  - F1: the engine's long-short is **high minus low** (`portfolio_sort.long_short_return`;
    the top group is long).
  - F2: the frozen generator prompt (`research/prompts/campaign3_v1.txt`) fixes
    `sign = 1` and says "negation is expressed inside the expression". It **never states
    that a high factor value is held long**. The generator had to guess the direction
    convention.
  - F3: all 99 candidates carry sign = 1. So direction errors cannot be seen in the
    `sign` column; they can only be found by reading each expression's stated hypothesis
    against its algebra.
  - F4: the search space is narrow (and a level of `dv`, i.e. a size or liquidity proxy, is not even expressible: `PQV2_EXPRESSION_NOT_CERTIFIED`, planner pre-check 2026-10-14):
    - inputs: TRI, daily return, dollar volume, and earnings-date events;
    - cross-sectional operators: `rank` and `standardize` only;
    - no market or beta term, no residualization, no signal combination;
    - every candidate is one expression evaluated alone.

## 1. Phase M0 — method diagnostics (IS only; no new data exposure)

- **What they use:** the persisted Campaign-3 IS exports and the IS fold
  (`[2016-01-29, 2020-07-31)`), which is already generator-visible.
- **What they never touch:** OOS, DEVELOPMENT_SEALED or the reserve.
- **Outputs are descriptive.**

| ID | Diagnostic | Question it answers | Reading |
|---|---|---|---|
| M0-1 | **Direction audit.** For each of the 99 candidates, compare the generator's stated hypothesis text with the algebraic direction of its expression under F1 | did the generator invert direction systematically? | a high inversion rate → a prompt-contract defect (fix in M1-a) |
| M0-2 | **Known-factor replication.** Run textbook signals through the identical pipeline (universe, h = 21, 5 groups, costs, T0/TS): 12-1 momentum, 1-month reversal, 12-month low volatility, EAR drift (the C3 positive control), (an illiquidity/size proxy is not expressible in the certified grammar; see F4) | does the pipeline reproduce the literature's sign and rough size on this universe and period? | textbook factors wrong-signed or degenerate → a pipeline/convention defect, which **blocks** everything else. Plausible → the pipeline is sound, and the problem is generator or search space |
| M0-3 | **Common exposure.** The correlation matrix and the first principal components of the 99 candidates' IS long-short series, plus each candidate's correlation with the M0-2 textbook factors | are the 99 candidates really a few repeated bets? | a dominant first component → the generator is not exploring; diversity constraints are needed |
| M0-4 | **TS decomposition.** Per period and per leg: INCOMPLETE terminal legs, their TS penalty, and the share of the T0–TS gap they explain | is the TS failure a property of the factors (they hold delisting-prone names), or of the stress design? | input to M1-d. **Any change to the gating rule is decided on methodological grounds and disclosed** |
| M0-5 | **The C3 positive control at IS.** EAR drift was planned as a T-B pipeline check, which never ran | did the one literature-backed signal behave? | combined with M0-2 |

- **Implementation:**
  - one read-only diagnostic script per item, under a new `smart_beta/diagnostics/` package;
  - frozen spec, then Pi via Herdr;
  - no pinned or C3-bound file is touched.
- **Ordering:** M0-2 runs **first**. If it fails, the remaining items wait for a pipeline
  fix.

## 2. Phase M1 — method redesign (chosen after M0; candidates listed now)

| ID | Change | Motivated by | Cost |
|---|---|---|---|
| M1-a | **Generator contract.** State the long direction explicitly; require an economic rationale with the predicted direction; require a self-check field ("high value → long because …"); give per-scenario IS feedback; add diversity constraints (family quotas; reject candidates too correlated with ones already proposed) | M0-1, M0-3 | prompt + C3-G-style executor changes; no P-Q change |
| M1-b | **Search space within v1.2 data.** Market beta and residual (idiosyncratic) volatility versus the equal-weight universe return; residual momentum; signal-to-volatility scaling | F4; the literature (residual momentum and low-idiosyncratic-volatility effects are stronger in large caps than raw versions) | **new operators need a P-Q grammar amendment and certification.** This is the largest cost; scope it narrowly |
| M1-c | **Construction, not only selection.** Combine signals (rank-average or IS-fit linear composite of a few vetted signals); neutralize against beta and size proxy (dollar volume); test holding periods longer than one month to cut turnover cost | single-expression search is the weakest possible construction | composite construction must be specified before data; its fit uses IS only |
| M1-d | **Stress gating review.** Keep T0/TS. Decide, from M0-4 and on methodological grounds, whether TS belongs in *ranking* (selection) or only in *confirmation* | M0-4 | document-only decision; disclosed as informed by C3 IS evidence |
| M1-e | **The method is the hypothesis.** Register the *procedure* (generator contract + search space + construction + selection rule), not a single expression. Validate it **walk-forward inside IS** (the method sees data up to t and is evaluated after t) before spending any clean fold | the C3 design tested one IS-selected expression, so a weak method was invisible until the end | new harness; IS-only |

**Order:**
- M1-a and M1-e are cheap and come first.
- M1-c next.
- M1-b only if M0 shows the search space itself is the binding limit.

## 3. Clean-data budget (unchanged; spent only on a method that passed M0/M1 on IS)

| Tier | Status | Planned use |
|---|---|---|
| IS, 54 formations | generator-visible, heavily used | all method development and walk-forward validation |
| OOS + DEVELOPMENT_SEALED, about 35 formations under the R1 repartition (`campaign4_program_plan_draft.md` §2) | OOS was read by Campaign-1; DEVELOPMENT_SEALED is clean | **one** method-level validation (T-B), with the R1 verification list completed **before** any freeze |
| Reserve, 37 formations | clean, 0 opens | the final m = 1 confirmation |
| Post-C forward months | accruing | optional forward tier F |

## 4. What this program does not do

- **No new data:** no vendor, no Dataset v2.
- **No loosened confirmation rule** to obtain a pass.
- **No change to sealed or pinned code.** New operators go through a P-Q amendment.
- **No clean-tier read** until a method has passed IS walk-forward.

## 5. Decisions needed

| ID | Decision | Recommendation |
|---|---|---|
| K1 | adopt this program in place of A'/F | yes |
| K2 | authorize the M0 diagnostic **spec** (document-only; then a separate launch authorization) | yes, M0-2 first |
| K3 | forward tier F (register `ed358470` now for post-C testing) | optional, cheap; can run alongside |

## Decisions (user, 2026-10-14: "全部是可以")

| ID | Decision |
|---|---|
| K1 | **Adopted.** This program replaces A'/F as the Campaign-4 direction |
| K2 | **The M0 diagnostic spec is authorized** (document-only, M0-2 first). Pi launch needs a separate authorization after the freeze |
| K3 | **The forward tier F is adopted.** The planner drafts its design: the K-PIT registration of `ed358470` and the post-C data path. The K-PIT append and any Tiingo acquisition each need their own authorization |
