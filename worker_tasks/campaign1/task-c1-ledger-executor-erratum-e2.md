# C1-LX-E2 — Erratum to the C1-LX task spec: PQB status encoding, registry-ban scope, binding-failure trigger (frozen; standalone)

- **Amends:** `worker_tasks/campaign1/task-c1-ledger-executor.md` (d7e87a8,
  `118bcf414fa7015f66d2ec5ffdec3580e7e97123694314d56ad6a585c4f636fd`), together with
  C1-LX-E1 (bea64b3,
  `6cc544bac82ee7544f7407639c0b5a182263db6c250956fc0396c08a21f5e9ca`). Neither document
  is modified.
- **Effective authority:** the original task + E1 + E2.
- **Status:** FROZEN. Approved by the user ("C1-LX-E2 APPROVED"), on master `bea64b3`.
- **Reported by:** the C1-LX worker as a blocker before any code was written. The
  planner verified it independently.
- **Bound authority:**
  - PQB `smart_beta/dataset_v1/pq_evaluation.py`
    (`322570ec810516c6b16493c6a060fe26d1479078477f14b4e05d6b8ea0b8aca3`):
    `_estimate_a`, `PQCell`, `classify_pq`;
  - Phase-8 `smart_beta/experiment/registry.py` (identity helpers),
    `smart_beta/experiment/search.py`, and `worker_tasks/phase8/phase8-plan.md`
    §7.1/§7.4;
  - Campaign precommitment `docs/campaign1_precommitment.md`
    (`15f700606929889d4b9b64799e49603f0f5e84081df5557519d815cc556619d7`) §8.3 and §11.

## E2.1 — A-cell "assessed" predicate (C1-LX §5.2): decisive

**Defect:** §5.2 says an A cell is assessed iff `status == "OK"`. The frozen PQB never
writes `"OK"`:
- `_estimate_a` (`pq_evaluation.py` ~L896–913) stores `PQCell.status = None` when the
  PQC `GapAwareHACResult.status == "OK"`, and stores the failure string otherwise.
- `classify_pq` (~L1436) itself treats an A cell as assessed iff `status is None`.

The literal clause would make every A cell not assessed, every attempt
`NOT_ASSESSED`, and `DEVELOPMENT_SCREEN_PASS` impossible. That contradicts precommitment
§8.2 and §9.

**Correction:** a required cell (A or B) is *successfully evaluated (assessed)* iff
`PQCell.status is None`, uniformly for A and B. For A cells
this is exactly PQB's encoding of `GapAwareHACResult.status == "OK"`; any non-`None` A
status (a PQC failure status) is not successfully evaluated and follows the frozen typed
`NOT_ASSESSED` semantics. PQB is not modified, and there is no Campaign-specific
reinterpretation of `PQCell.status`. The intended semantics are unchanged.
This only corrects the interface encoding to match the frozen PQB, and is identical to
`classify_pq`'s own predicate.

**Added test:** a stubbed PQB A cell with `status=None` and a finite t is assessed;
`status="INSUFFICIENT_OBSERVATIONS"` (or any non-`None` string) gives `passed=None`.

**Added mutation:**
- LX-E2-M1: `status == "OK"` reintroduced, or `status is not None` treated as assessed.

## E2.2 — Scope of the "Phase-8 registry" ban (C1-LX §5)

**Defect:** §5 bans importing the "Phase-8 registry", while §2.3 requires
`hypothesis_id_for` and E1 requires `registry.content_hash`. Both live in
`smart_beta/experiment/registry.py`.

**Correction:** the ban covers the mutable registration and record surface, not the
pure identity helpers.

**Permitted imports** from `smart_beta.experiment.registry`:
- in the Campaign modules: exactly `content_hash`, `canonical_json` and
  `hypothesis_id_for`;
- in tests only (for the domain-separation test): `experiment_id_for`.

**Forbidden** in `smart_beta/campaign/**` (import, attribute reference or call; checked
by the AST test):
- `ExperimentRegistry`, `ExperimentEntry`, `DecisionEntry`, `RegistrySnapshot`;
- `experiment_id_for`, `hypothesis_id_for_record`, `experiment_id_for_record`;
- `smart_beta.experiment.search.SearchLedger`, `adjudicate_search_attempt`;
- the Phase-8 judge and decision API;
- `EvaluationRecord`, `DecisionRecord`;
- `smart_beta.evaluation.engine` (including `evaluate`) and holdout tokens.

`smart_beta.experiment.policy` is permitted only for `SearchPolicy` and its enums.

**Added mutation:**
- LX-E2-M2: the Campaign module imports `ExperimentRegistry` or `experiment_id_for`.
  The AST test must kill it.

## E2.3 — Trigger for `BINDING_OR_IDENTITY_FAILURE` (C1-LX §5.4 / §8)

**Defect:** the reason is listed with no trigger. Precommitment §8.3 makes a binding or
identity failure a `NOT_ASSESSED` condition, and §11 makes it a Campaign-level stop.
Both must hold.

**Correction (deterministic).** There are three stages. The boundary between Stage A and
Stage B is whether a valid, immutable Campaign + hypothesis + attempt identity has
already been established.

**Attempt identity is established** once all of the following hold:
1. `CAMPAIGN_OPEN` has been durably appended, so `campaign1_id`, `SEARCH_POLICY` /
   `family_id` and the dataset, overlay and authority bindings are validated;
2. the hypothesis's `factor_provenance_hash` and `hypothesis_id` have been computed and
   validated (spec hash equals the frozen value);
3. `campaign_design_hash` and `campaign_attempt_id` have been computed;
4. the C1-LX §7 budget pre-check has passed.

From that point the attempt is established, and its slot is consumed by the
`PRIMARY_ATTEMPT` record that C1-LX §7 requires.

| Stage | Condition | Ledger action | Slot | Campaign |
|---|---|---|---|---|
| **A** (open / pre-attempt) | a failure that prevents trustworthy establishment of any item 1–3 above: a Campaign identity / `SearchPolicy` / `family_id` / dataset / overlay / authority binding failure, a K-PIT preflight failure, or a failure while computing or validating the hypothesis or attempt identity | **no attempt record**. An attempt identity is never fabricated just to record the failure | none | `CAMPAIGN_STOP` `INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE` (`GOVERNANCE_CONFLICT` for a K-PIT ordering failure, as in C1-LX §9) |
| **B** (established attempt) | after establishment, `C1BindingError`, `D7bBindingError` or `PQBindingError` raised while processing that primary attempt | append that attempt's `PRIMARY_ATTEMPT` record with disposition `NOT_ASSESSED(BINDING_OR_IDENTITY_FAILURE)`. The attempt stays in the experiment accounting, the statistical accounting and the hypothesis history | consumed (proposal, experiment, statistical) | then `CAMPAIGN_STOP` `INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE` |
| **C** (PQB typed not-assessed) | bindings and identities are valid, and PQB *returns* its ordinary typed `not_assessed` diagnostics or classify record instead of raising | `PRIMARY_ATTEMPT` with `NOT_ASSESSED(PQ_NOT_ASSESSED)`. This is never collapsed into `BINDING_OR_IDENTITY_FAILURE` | consumed | **no** Campaign stop; the Campaign continues |

**Other cases:**
- **Scope of Stage B:** Stage B covers only the three typed binding/identity errors
  named above. Any other untyped exception keeps the existing C1-LX §8 behaviour
  unchanged (`CAMPAIGN_STOP INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE`, then re-raise).
  E2 neither widens nor narrows that rule.
- A binding failure during a **diagnostic** evaluation appends no disposition (C1-LX
  §5.5). It appends a `DIAGNOSTIC_EVALUATION` record marked `BINDING_FAILURE`, then the
  same Campaign stop.
- **Replay after a fatal attempt failure (the frozen replay rule, not changed):** a
  later run that would produce a different record for a durably recorded Stage-B
  attempt fails closed (`C1ReplayMismatchError`). Historical attempts are never
  rewritten into another result. C1-LX has **no** retry escape hatch; recovery requires
  separately authorized new Campaign lineage or ledger semantics.

**Added tests:**
- **Stage A:** a binding failure at open, and a failure while computing the hypothesis
  or attempt identity, each give zero attempt records, zero slots and
  `CAMPAIGN_STOP INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE`.
- **Stage B:** a binding error raised after establishment gives a `PRIMARY_ATTEMPT`
  with `NOT_ASSESSED(BINDING_OR_IDENTITY_FAILURE)`; proposal, experiment and statistical
  counters each +1; the attempt in the hypothesis history; then
  `CAMPAIGN_STOP INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE`.
- **Stage C:** a returned PQB `not_assessed` gives `PQ_NOT_ASSESSED`, a consumed slot,
  no stop, and the next hypothesis is processed.
- **Replay after a Stage-B failure:** re-running with the cause removed gives
  `C1ReplayMismatchError` (fail closed), and the ledger is unchanged.

**Added mutation:**
- LX-E2-M3: Stage-A/B accounting is broken. There are two variants, and **both** must
  be killed: (a) a Stage-B failure stops without appending the attempt record, so the
  attempt escapes accounting; (b) a Stage-A identity failure fabricates an attempt
  record.

## Scope statement

| Item | Change |
|---|---|
| Methodology | NONE |
| Hypotheses | NONE |
| Family membership | NONE |
| m | NONE |
| α | NONE |
| Screen | NONE |
| Critical value | NONE |
| Budgets | NONE |
| Empirical information used | NONE |
| Mutations | the 17 original + 6 E1 mutations remain in force; E2 adds 3 (M3 has two required variants). Frozen total: **26** |
