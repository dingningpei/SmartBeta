# Campaign-3 P4 — generator integration design (FROZEN design 2026-10-09; implementation spec not yet written)

- **Status:** planner draft, 2026-10-09 (user: "先起草设计").
- **Scope:** connect the sealed Phase-9 research loop to the certified P-Q v2.3 grammars
  and the admitted data (Dataset v1.2 + v1.3), for a budgeted, fully registered
  discovery run.
- **Not in scope:** the Campaign-3 precommitment itself (P5); the reserve path (P7).

## 1. What exists (Phase 9, sealed) and what is missing

| Exists (`smart_beta/research/`) | Missing |
|---|---|
| `ResearchLoop`, `GenerationEvent` write-ahead registry, `RawArtifact`, deterministic normalization to a FactorSpec, novelty/duplicate checks, stopping policy, `ResearchPolicy` (expression operators, budgets), holdout firewall | (a) an **LLM generator adapter** (the loop takes an injected generator); (b) a **grammar bridge**: the policy's operator set and inputs must express SIMPLE_G ∪ STD_X_RANK_G ∪ EVENT_G, and every normalized candidate must pass `pqv2_dependency.certify(…, authority=AUTHORITY_V2_3)` as a **non-empirical** admission check; (c) an **evaluation bridge**: candidate → P-Q v2.3 diagnostics on the development folds; (d) **information-flow tiers** (§3) |

## 2. Components (new modules; no sealed code changes; STOP if a sealed module must change)

1. **`research/llm_generator.py` (new):** an adapter implementing the Phase-9 generator
   interface.
   - **Records:** model id, provider, prompt-template hash, the generator-visible history
     snapshot hash, seed and settings; the raw response stored verbatim as a
     `RawArtifact`.
   - **Credentials:** read only in-process from the provider variable. It is never
     logged; the existing secret scan on raw artifacts applies.
   - **Output format:** the prompt asks for factor expressions in the FactorSpec JSON
     grammar, using only the §2 roles (`tri`, `ret`, `dv`, `event_ear_k_kp1`,
     `event_days_since_ea`, `event_recent_ea_w<W>`).
   - **Provider/model (decision D4):** DeepSeek `deepseek-v4-flash` or Claude. This
     affects cost and billing, and is your choice.
2. **`campaign/campaign3_generation.py` (new):** the grammar bridge.
   - The Phase-9 normalization produces a FactorSpec, then `certify(v2.3)`.
   - A NOT_CERTIFIED candidate is **recorded and counted** in the budget, and rejected
     non-empirically. That is allowed by the Phase-9 §3 trust invariant, because no
     result was observed.
   - Novelty: exact canonical-key duplicates collapse to one slot (Phase-9 §10c).
3. **`campaign/campaign3_eval.py` (new):** the evaluation bridge.
   - It evaluates certified candidates with `build_pqv2_diagnostics` under
     `AUTHORITY_V2_3`, on the TOP_1000 development universe.
   - It persists diagnostics write-ahead (E6 pattern) before any feedback is formed.

## 3. Information-flow tiers (the central design decision)

| Tier | Folds | Visible to the generator? | Used for |
|---|---|---|---|
| **T-A search** | IS (2016-01-29 … 2020-07-31, 54 formations) | **yes**: per-candidate B and L summaries (mean, t, assessability) | generation feedback, ranking |
| **T-B validation** | OOS (18) + DEVELOPMENT_SEALED (18) = 36 formations | **never** | the development screen of the top-k IS candidates |
| **T-C confirmation** | reserve 2023-07-03 … C (about 37) | **never**; not even built until P7 executes | one-shot confirmation (m = 1) |

**Why:** once the generator sees IS results, IS statistics are search-inflated and
cannot screen, even after Holm. So:
- the **screen runs on T-B only**, over the k candidates selected **by IS rank alone**
  (k precommitted, for example 5);
- Holm runs over **k**, not over N, because only k hypotheses touch T-B. Selection used
  only T-A;
- the best T-B survivor (by a precommitted rule) is forwarded to T-C.

**Firewall:** the generator-visible history is built only from T-A artifacts, and the
loop's holdout firewall enforces it. Any T-B/T-C path in a history snapshot means STOP.

The development sealed fold is used for the first time here, as validation. That use is
consumed and must be registered.

**Power implication** (for P5, recorded now): Holm over k = 5 on 36 months (one-sided
α = 0.05/5 → z ≈ 2.33) needs an annual Sharpe of about 1.3 for 50% power at T-B. The
reserve confirmation is separate (Campaign-3 plan §5, to be recomputed with EWC).

## 4. Budget and accounting

- **N** (decision D1; recommended 100) counts every recorded proposal, including
  NOT_CERTIFIED ones and duplicates (as events).
- **K-PIT:** every proposal gets a GENERATOR-channel record before evaluation. The
  HUMAN-channel positive control (`event_ear_k_kp1`) is registered separately and
  **counts in T-B's k only if it ranks top-k on IS like any other candidate**.
  Alternatively, it could be a fixed extra slot (decision for P5).
- **Stopping:** the budget is exhausted, or the Phase-9 stopping policy fires (repeated
  signals, generator failure).

## 5. Tests and evidence (for the eventual implementation spec)

- **Synthetic loop end-to-end:**
  - a stub generator emitting fixed raw artifacts;
  - certification and rejection accounting;
  - write-ahead crash recovery (no hidden retries);
  - the firewall: a T-B or reserve path in a snapshot → refusal.
- **LLM adapter:** an injected fake transport; the secret scan; a malformed response →
  a recorded rejection, never a silent retry.
- **No live model calls in tests.** A live dry run with N = 3 is a separate,
  user-authorized step.

## 6. Decisions needed

| ID | Decision | Recommendation |
|---|---|---|
| D1 | budget N | 100 |
| D4 | generator provider/model | your choice (cost) |
| D6 | k (IS top-k forwarded to the T-B screen) | 5 |
| D7 | positive control: compete for top-k, or a fixed extra T-B slot | **fixed extra slot** (so the control always reaches T-B; Holm over k+1) |

## Freeze decisions (user, 2026-10-09: "按照你的建议 D4用claude")

| ID | Decision |
|---|---|
| D1 | budget **N = 100** recorded proposals (NOT_CERTIFIED proposals and duplicate events included) |
| D4 | generator provider **Claude** (Anthropic API). The exact model id is frozen in the P4 implementation spec; the planner proposal is `claude-opus-5-5` |
| D6 | **k = 5** IS-ranked candidates forwarded to the T-B screen |
| D7 | positive control (`event_ear_k_kp1`): a **fixed extra T-B slot**. Holm over k + 1 = 6 at T-B |

**Live model calls:** a live dry run (N = 3) and the real generation each need separate
authorization. They incur API cost.
