# C1-LX — Campaign-1 development ledger and executor — FROZEN BEFORE IMPLEMENTATION

- **Status:** FROZEN SPEC. Implementation requires a separate authorization.
- **This is an execution prerequisite** (Campaign-1 precommitment §12–§13, item 3).
- **Authority, in order:**
  1. `docs/campaign1_precommitment.md` (e708f97,
     `15f700606929889d4b9b64799e49603f0f5e84081df5557519d815cc556619d7`). Every value
     below is copied from it; if they conflict, the precommitment wins and the conflict
     is a **blocker**.
  2. `docs/campaign1_factorspecs.json`
     (`f93a218d1e8a5bc07a7e64b52e1ce66be09b6708479d5c76a5f9a2973d6d532c`).
  3. Dataset v1.2 / P-Q authority (`9261a55b…3ed0`, rules `df5bec69…b886`) and the
     admission document (`8c720c29…29ee`).
  4. The existing Phase-8 / Phase-9 / Phase-10 code, which is reused and not modified.
- **Dependency:** C1-O1 merged into master. This task branches from the C1-O1 merge
  commit because it binds the post-O1 `pq_evaluation.py` and `pq_reporting.py` hashes.
- **Owned files (all new):**
  - `smart_beta/campaign/__init__.py`;
  - `smart_beta/campaign/ledger.py`;
  - `smart_beta/campaign/campaign1.py`;
  - `tests/test_campaign1_ledger.py`;
  - `tests/test_campaign1_executor.py`;
  - `tests/fixtures/campaign1/ledger/**` (including `mutations.json`).
- **Must not modify:** any existing file. That includes PQA, PQB, PQC, D7b, D7c, D6,
  Phase-6/7/8/9/10 code and every document.
- **Real-data scope in this task:** NONE. There is no real factor evaluation, no real
  PQB build and no K-log write. Tests are synthetic. The single real-data touch allowed
  is the barrier's binding-only preflight (§11), which hashes files and reads nothing
  that holds outcome values.
- **Provider and network calls:** 0. **Reserve reads:** 0.

## 1. Frozen constants (`campaign1.py`; module-level and immutable)

| Constant | Value |
|---|---|
| `PRECOMMITMENT_SHA256` | `15f700606929889d4b9b64799e49603f0f5e84081df5557519d815cc556619d7` |
| `FACTORSPECS_SHA256` | `f93a218d1e8a5bc07a7e64b52e1ce66be09b6708479d5c76a5f9a2973d6d532c` |
| `O1_TASK_SPEC_SHA256` | `7907aa87057c609c0640a7bb9118ebbb413f59213a4c35ac69ff1e0904c8cb6c` |
| `PROGRAM_ID` | `"campaign1:" + PRECOMMITMENT_SHA256` |
| `SCREEN_ID` / `SCREEN_POLICY_SHA256` | `"C1-DEV-SCREEN-v1"` / `b7414db54add862dc12711564cdf7e0f4dec1ec07a0eb5b2bff507b81d9e592e`. The code re-derives the hash from the exact canonical JSON in precommitment §8.2 and asserts equality |
| `DIAG_SCREEN_ID` / `DIAG_SCREEN_POLICY_SHA256` | `"C1-ROBUSTNESS-DESCRIPTIVE-v1"` / SHA-256 of `{"judged":false,"role":"ROBUSTNESS_DESCRIPTIVE_DIAGNOSTIC","screen_id":"C1-ROBUSTNESS-DESCRIPTIVE-v1","verdict":"NONE"}` = `e6ed8b27390d399e2ecd5c0fd7dcb9d4f71095eedf1bdf44cfcd75c6d17ca7c7` |
| Partition | Phase-7 `Partition(folds=(is [2016-01-29, 2020-07-31), oos [2020-07-31, 2022-01-31), holdout [2022-01-31, 2023-07-03)), split_rule="campaign1-is-oos-sealed-v1")`. `partition_id` is asserted to equal `b4b56dbfc82377930b8bf87a1d8039a69ccbf7d42d5faf234d3bb8cbbcb936fb` |
| Evaluation construction | `horizon=21`, `n_groups=5`, `winsorization=0.01`, `transaction_cost_bps=10.0`, `periods_per_year=12`, `weighting="EQUAL"`, `primary_metric=MetricKey.TURNOVER_COST_ADJUSTED`, `basis="NET"` |
| Universes | primary `"TOP_500"`; diagnostics `("TOP_200", "TOP_1000")` (D7b `_UNIVERSE_FLAG` keys) |
| `SEARCH_POLICY` | Phase-8 `SearchPolicy(family_id="campaign1-family-1", family_budget_m=7, family_alpha=0.05, trial_unit=TrialUnit.EXPERIMENT_ID, procedure=SearchProcedure.FIXED_M_BONFERRONI, budget_exhaustion=BudgetExhaustion.GOVERNANCE_FAILURE, replay_rule=ReplayRule.DETERMINISTIC_REPLAY)`. This reuses the existing value object; nothing is copied |
| `CRITICAL_VALUE` | `float(scipy.stats.norm.ppf(1 - SEARCH_POLICY.family_alpha / SEARCH_POLICY.family_budget_m))`. A test asserts `abs(c - 2.44999766060273) < 1e-12` |
| `MIN_PERIODS_IS` / `MIN_PERIODS_OOS` | `24` / `12` |
| Budgets | generation 0, proposal 7, experiment 7, diagnostic 14, statistical 7 |
| `O2_THRESHOLD` | `1e-6` |
| Cell mapping | PQB `B-T0` = ORIGINAL-T0, `B-TS` = ORIGINAL-TS, `A-T0` = QUARANTINED-T0, `A-TS` = QUARANTINED-TS (B = ORIGINAL, A = QUARANTINED, per Dataset v1.2) |
| `EstimandPolicy` | `smart_beta.science.preregistration.EstimandPolicy(program_id=PROGRAM_ID, estimand_kind=EstimandKind.MEAN_NET_LONG_SHORT, horizon=21, construction={"n_groups":5,"cost_bps":10.0,"winsorization":0.01,"factor_missing_policy":"PROPAGATE"}, sesoi=0.0025, sesoi_justification_hash="2565ce442a0edc4d4916d2dcb8621a7d9260cb6742bbcf2f6b0f4e5203844493")`. The code re-derives the justification hash from the exact text in precommitment §4 |

**Hypotheses:** load them from `docs/campaign1_factorspecs.json` with
`smart_beta.spec.factor_spec.from_dict`.
- Assert, for every spec: `factor_spec_hash(spec) == spec.version ==` the frozen hash;
  `sign == +1`; labels exactly C1-H1 … C1-H7, in that order.
- Assert the file's SHA-256 equals `FACTORSPECS_SHA256`.
- At import or run start, assert the precommitment file's SHA-256 equals
  `PRECOMMITMENT_SHA256`.
- Any mismatch raises `C1BindingError`, which stops the Campaign with
  `INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE`.

## 2. Identities

1. **`campaign1_id`** = SHA-256 of the canonical JSON (sorted keys, compact, ASCII) of:
   - `dataset_id`, `dataset_identity_hash`, `amendment_sha256`;
   - the admission document SHA-256;
   - `pq_id`, `pq_hash`, `quarantine_rule_hash`, the R3 identity hash;
   - `PRECOMMITMENT_SHA256`, `FACTORSPECS_SHA256`, `SCREEN_POLICY_SHA256`,
     `DIAG_SCREEN_POLICY_SHA256`, `O1_TASK_SPEC_SHA256`;
   - the SHA-256 of the merged `smart_beta/dataset_v1/pq_reporting.py` and
     `smart_beta/dataset_v1/pq_evaluation.py`, read at run time and recorded;
   - `partition_id`; the universes; the evaluation construction;
   - `SEARCH_POLICY` (its existing canonical content); the budgets; `CRITICAL_VALUE`
     (as `repr`).
2. **Factor provenance:** `evaluate_factor(spec, aliased D7c inputs)` (the Phase-6 path
   PQA uses). Use `EngineResult.content_hash` as `factor_provenance_hash`.
   **Factor values are never logged, printed or reported**; only hashes and counts.
3. **`hypothesis_id`** = Phase-8 `hypothesis_id_for(factor_provenance_hash)`. No parent.
4. **`campaign_design_hash(hypothesis, universe)`** = SHA-256 of the canonical JSON of
   the D7b `CampaignEvaluationDeclaration` content **excluding** `fold_key` and
   `factor_panel_sha256`, plus `pq_id`, `pq_hash` and `campaign1_id`.
5. **`campaign_attempt_id`** = SHA-256 of the canonical JSON of
   `{"domain":"campaign1-development-attempt","campaign1_id":…,"hypothesis_id":…,"campaign_design_hash":…}`.
   - It is never called, stored as or passed where a Phase-8 `experiment_id` is
     expected.
   - Diagnostic designs use domain `"campaign1-diagnostic-evaluation"`.

## 3. Fold factor panels and declarations

For each (hypothesis, universe, fold ∈ {IS, OOS}):
- **Panel:** one row per (formation f in the fold, member of that universe at f, from
  D6), with columns `date`, `stock_id`, `value`. `value` is the Phase-6 wide value at
  row f, or NaN. The member key is whatever D7b `_validate_factor_panel` expects. The
  panel must pass PQB `_factor_panel_matches`.
- **`factor_panel_sha256`:** `delisting_bridge._canonical_factor_hash(panel)`, a
  read-only use of the existing function.
- **Declaration:** `CampaignEvaluationDeclaration(precommitment_ref="docs/campaign1_precommitment.md@"+PRECOMMITMENT_SHA256, factor_spec_version=spec.version, factor_panel_sha256=…, partition_id=…, fold_key=delisting_bridge.fold_key(fold), universe=…, …construction…, screen_id/screen_policy_hash = primary or diagnostic constant)`.
  It is wrapped in `PQEvaluationDeclaration` with the admitted dataset, overlay and rule
  bindings.
- **Firewall:** the executor **never constructs** a declaration for the holdout-role
  fold (DEVELOPMENT_SEALED_FOLD). Requesting one raises `C1SealedFoldError`, which
  stops the Campaign with `SEALED_FOLD_ACCESS_ATTEMPT`. This is independent of
  D7b/PQB's own refusal.

## 4. O2 guard (C1-H6 and C1-H7 only; before any PQB build)

- **Count:** for each of C1-H6/H7, evaluate the denominator sub-spec
  `lag(rolling_std(ret, w), 21)`, with w = 231 or 105 (a FactorSpec built from the
  frozen expression's right operand, sign +1, PROPAGATE).
- Count the cells (f in IS ∪ OOS formations, f-member of TOP_500) whose value is finite
  and `< O2_THRESHOLD`. Only the integer count is recorded.
- **If the count is > 0:** the attempt's disposition is
  `NOT_ASSESSED(O2_ESCALATION)`. No PQB build is run for that hypothesis, in any
  universe or fold.
- The guard runs once per hypothesis and is recorded in the ledger.

## 5. Evaluation, screen, classification and disposition

**Per primary attempt (TOP_500):**
1. `build_pq_diagnostics` for IS, then for OOS. Use the default bindings and the
   admitted overlay. Use the O1-repaired residual reporting.
2. **Screen outcomes:** one `PQScreenOutcome` per cell, bound to `SCREEN_ID`,
   `SCREEN_POLICY_SHA256` and the cell and declaration hashes.
   - **IS:** `passed = (cell.t_stat >= CRITICAL_VALUE)` if the cell is
     *assessed*, `cell.t_stat` is finite and `cell.n_obs >= 24`; otherwise
     `None`.
   - **OOS:** `passed = (cell.value > 0)` if `cell.value` is finite and
     `cell.n_obs >= 12`; otherwise `None`.
   - A cell is *assessed* iff (B cell and `status is None`) or (A cell and
     `status == "OK"`, the PQC `GapAwareHACResult` status). Any other A status is
     not assessed.
   - With sign +1, `value` and `t_stat` are already in the economic orientation. Assert
     `orientation_rule` is the identity.
   - **No other field may be used.**
3. **Classification:** `classify_pq(diag_IS, screens_IS)` and
   `classify_pq(diag_OOS, screens_OOS)`.
4. **Disposition:** the first matching row wins; all applicable reasons are recorded,
   in canonical order.
   - **`NOT_ASSESSED`** reasons: `BINDING_OR_IDENTITY_FAILURE`, `O2_ESCALATION`,
     `PQ_NOT_ASSESSED` (either fold's diagnostics or classify record
     NOT_ASSESSED), `SCREEN_VERDICT_NONE` (any `passed is None`).
   - **`DEVELOPMENT_SCREEN_FAIL`** reasons (all collected): `QUARANTINE_SENSITIVE`
     (either fold), `DELISTING_SENSITIVE` (`delisting_a` or `delisting_b` not
     NOT_SENSITIVE, either fold), `IS_THRESHOLD_NOT_MET` (any IS cell False),
     `OOS_DIRECTION_NOT_MET` (any OOS cell False).
   - **`DEVELOPMENT_SCREEN_PASS`:** none of the above.
5. **Diagnostic designs (TOP_200 / TOP_1000):** `build_pq_diagnostics` for IS and OOS
   only.
   - There are no screen outcomes, no `classify_pq` and no disposition. The record
     type is `DIAGNOSTIC_EVALUATION`.
   - They are run for every hypothesis that is not stopped by O2 or by a Campaign-level
     stop, **after** that hypothesis's primary attempt, whatever its disposition.

The vocabulary contains **no** `ACCEPT`, `REJECT`, `DEFER`, `EvaluationRecord` or
`DecisionRecord`. A test greps the module and the emitted ledger to confirm it. No
Phase-7 `engine.evaluate`, `holdout_tokens` or Phase-8 registry is imported or called;
an AST import test checks this.

## 6. Append-only ledger (`ledger.py`)

- **Format:** JSONL with UTF-8 canonical JSON lines. Each line is
  `{"seq":n, "prev_hash":…, "record":{…}, "record_hash":sha256(canonical(record) + prev_hash)}`.
  The genesis `prev_hash` is 64 zeros.
- **Durability:** opened append-only (`O_APPEND`). Each append is fsynced. On open the
  whole chain is verified; a truncated tail or hash mismatch raises
  `C1LedgerIntegrityError`. There is never a repair, rewrite or truncate.
- **Record types:**
  - `CAMPAIGN_OPEN`: `campaign1_id`, all §2.1 inputs, `SEARCH_POLICY` canonical content,
    budgets, `CRITICAL_VALUE`, and the K-PIT preflight result hashes. **Exactly one,
    first.**
  - `O2_GUARD`;
  - `PRIMARY_ATTEMPT`: attempt id, `hypothesis_id`, label, design hash, slot index,
    αᵢ, c, IS/OOS declaration hashes, IS/OOS diagnostics hashes, the 8 screen-outcome
    dicts, the 2 classify records, disposition, reasons, `quarantine_sensitive`,
    delisting statuses, and the O1 structural residual totals;
  - `DIAGNOSTIC_EVALUATION`;
  - `CAMPAIGN_STOP`: the typed stop reason and budget counters. **Exactly one, last.**
- **Replay:** re-running an attempt with the same `campaign_attempt_id` must produce a
  byte-identical record payload.
  - If it does, nothing is appended and no slot is consumed. The return value is
    `REPLAY_IDENTICAL`.
  - If it does not, raise `C1ReplayMismatchError`, which stops the Campaign with
    `INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE`.
- **Locking:** the family fields (`SEARCH_POLICY`, `CRITICAL_VALUE`, budgets) are
  locked at `CAMPAIGN_OPEN`. Opening an existing ledger with different values raises
  `C1GovernanceConflict`, which stops with `GOVERNANCE_CONFLICT`.

## 7. Budgets and slots (non-substitutable)

- **Separate counters:** `generation_used` (always 0; any generator call is
  `GOVERNANCE_CONFLICT`), `proposal_used`, `experiment_used`, `diagnostic_used`,
  `statistical_slots_used`.
- **Primary attempt:** a new primary attempt consumes 1 proposal, 1 experiment and 1
  statistical slot **at the moment its first `PRIMARY_ATTEMPT` record is appended**,
  whatever its disposition. That includes `NOT_ASSESSED`; the slot is consumed
  conservatively. m is fixed at 7, so αᵢ never changes.
- **Diagnostic design:** a new diagnostic design consumes 1 diagnostic evaluation and
  nothing else.
- **Over-budget:** a request that would exceed any counter is refused before any
  computation, with the corresponding typed stop.
- A proposal that is not one of C1-H1 … H7 (spec hash not in the frozen set) is
  refused (`GOVERNANCE_CONFLICT`).

## 8. Stop reasons (typed; the precommitment §11 vocabulary)

- **`C1StopReason` values:** `PROPOSAL_BUDGET_EXHAUSTED`, `EXPERIMENT_BUDGET_EXHAUSTED`,
  `STATISTICAL_BUDGET_EXHAUSTED`, `ALL_PRECOMMITTED_HYPOTHESES_EVALUATED`,
  `INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE`, `GOVERNANCE_CONFLICT`,
  `RESERVE_OR_FIREWALL_VIOLATION`, `SEALED_FOLD_ACCESS_ATTEMPT`.
- **Mapping to Phase-9:** where a value has identical meaning to a Phase-9
  `smart_beta.research.policy.StopReason` value (`PROPOSAL_BUDGET_EXHAUSTED`,
  `STATISTICAL_BUDGET_EXHAUSTED`, `GOVERNANCE_CONFLICT`; and
  `HOLDOUT_FIREWALL_VIOLATION` → `SEALED_FOLD_ACCESS_ATTEMPT`), the module exposes an
  explicit mapping table, and a test checks that the mapped Phase-9 members exist.
- **Precedence:** first match. Any exception that is not typed becomes
  `INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE`, after which a `CAMPAIGN_STOP` is appended
  and the error is re-raised.
- **Reserve:** reserve access (`ReserveFirewallError` from D7b/PQA, or any path under
  `reserve_sealed/`, `raw/` or `dsc_evidence`) gives `RESERVE_OR_FIREWALL_VIOLATION`.
- **Order of execution:** C1-H1 … C1-H7. For each hypothesis: O2 guard (H6, H7), then
  primary IS+OOS, then diagnostics TOP_200 IS/OOS and TOP_1000 IS/OOS. The normal close
  is `ALL_PRECOMMITTED_HYPOTHESES_EVALUATED`.
- **No early stop on outcomes:** a FAIL or PASS never stops or reorders the Campaign.

## 9. Knowledge-PIT preflight (read-only) and registration helpers (no append)

- **`estimand_policy()`** returns the §1 `EstimandPolicy` value.
- **`hypothesis_identities(root)`** returns, for C1-H1 … H7: `label`,
  `factor_spec_hash`, `factor_provenance_hash` and `hypothesis_id`. It is computed from
  real inputs **only when called by the separately authorized registration step**.
  Values are never emitted.
- **`verify_kpit_ordering(knowledge_log_path)`** uses `read_records` (read-only) and
  requires, in log order:
  1. exactly one `HUMAN_DECISION` with `decision_kind == "ESTIMAND_POLICY"`,
     `program_id == PROGRAM_ID`, whose content equals `estimand_policy()` (checked via
     `estimand_policy_from_record`);
  2. then exactly one `HUMAN_DECISION` `PROGRAM_FREEZE` with
     `program_id == PROGRAM_ID`;
  3. then exactly 7 `HYPOTHESIS_FREEZE` records with `program_id == PROGRAM_ID`, whose
     (`hypothesis_id`, `factor_spec_hash`) pairs equal `hypothesis_identities` exactly,
     and whose `refs.influenced_by` each contain both record hashes from 1 and 2;
  4. no record with `program_id == PROGRAM_ID` before item 1.
- **On failure** it returns a typed failure, and the executor refuses to open the
  Campaign (`GOVERNANCE_CONFLICT`).
- **On success** the hashes of these 9 records go into `CAMPAIGN_OPEN`.
- **This task never calls** `append_estimand_policy`, `record_program_freeze`,
  `record_hypothesis_freeze` or `KnowledgeLog.append`. An AST test checks this.

## 10. Report (deterministic)

- **Function:** `render_report(ledger_path) -> (json_bytes, markdown_bytes)`, derived
  only from the ledger.
- **Content:** exactly the precommitment §15 schema, for all 7 hypotheses whatever
  their disposition, plus the diagnostic section labelled `DESCRIPTIVE — NOT JUDGED`
  and the stop reason.
- **Required text:** a fixed claim-boundary paragraph stating that
  `DEVELOPMENT_SCREEN_PASS` is not confirmation, holdout validation, confirmation
  authorization, production readiness or investability.
- **Determinism:** byte-identical under `TZ=Asia/Tokyo LC_ALL=C`.

## 11. Required tests (synthetic) and barrier

**Tests:**
1. Constants and hashes (§1), including the critical value, the partition id and the
   screen JSON re-derivation.
2. FactorSpec loading and hash round-trip.
3. Identity determinism and domain separation: an attempt id is never equal to a
   Phase-8 `experiment_id_for` of the same inputs.
4. Ledger:
   - chain verification; refusal on tamper or truncation;
   - identical replay consumes no slot; a mismatched replay fails closed;
   - lock conflict.
5. Budgets: each counter independently exhausts with the right typed stop; there is no
   cross-substitution.
6. Screen: the boundary `t == c` passes and `t = nextafter(c, -inf)` fails; NaN t or
   short n gives `None`, which gives NOT_ASSESSED; `OOS value == 0` fails.
7. Disposition precedence and reason collection, including QUARANTINE_SENSITIVE and
   DELISTING_SENSITIVE individually blocking an otherwise passing attempt.
8. O2 guard: count > 0 gives NOT_ASSESSED and no PQB call (spy).
9. Sealed fold: requesting the holdout fold gives `SEALED_FOLD_ACCESS_ATTEMPT`, and no
   D7b/PQB call happens.
10. Diagnostics are never screened or classified and consume only the diagnostic
    budget.
11. K-PIT preflight against synthetic temporary K-logs: every ordering violation in §9
    is refused; the valid order is accepted.
12. Forbidden-vocabulary grep and the AST import/call bans (§5, §9).
13. Report determinism and claim-boundary text.
14. End-to-end on a small synthetic dataset with a stubbed PQB (monkeypatched
    `build_pq_diagnostics` / `classify_pq` returning fixed synthetic objects).

**Mutations (planner re-executes all, plus a no-op control):**
- LX-M1 critical value uses Student-t or a two-sided quantile;
- LX-M2 m read as 6 or as the number of attempts so far;
- LX-M3 replay consumes a slot;
- LX-M4 mismatched replay accepted;
- LX-M5 diagnostics consume statistical slots;
- LX-M6 a diagnostic gets a disposition;
- LX-M7 `>=` changed to `>` in the IS rule;
- LX-M8 OOS uses `>= 0`;
- LX-M9 a `None` verdict treated as False (FAIL instead of NOT_ASSESSED);
- LX-M10 DELISTING_SENSITIVE ignored;
- LX-M11 QUARANTINE_SENSITIVE ignored;
- LX-M12 the sealed-fold check removed;
- LX-M13 K-PIT ordering check accepts HYPOTHESIS_FREEZE before PROGRAM_FREEZE;
- LX-M14 ledger chain check skipped;
- LX-M15 a budget lock is not enforced;
- LX-M16 the O2 guard is bypassed;
- LX-M17 the hash check on the precommitment file is skipped.

**Barrier (planner):**
- review;
- all tests, plus all 17 mutations re-executed;
- a credential-scrubbed, network-denied full suite;
- a binding-only real preflight: constants, file hashes, `partition_id`, and
  `pq_evaluation.py` / `pq_reporting.py` hashes are recorded. No factor, PQB, label or
  return computation, and no K-log write.

## Report

- files changed;
- test and mutation counts, with the kill map;
- full-suite result;
- the exact completion commit SHA;
- the line `existing files modified: NONE`.
