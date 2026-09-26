# Phase 10 — Knowledge-PIT & Confirmatory Evidence Protocol: certification record

**Artifact under certification:** the Phase-10 science layer
`smart_beta/science/{contracts,knowledge,footprint,roles,preregistration,inference,assessment,adapters,study}.py`,
the authorized additive Phase-7 extension (`smart_beta/evaluation/inferential_series.py`
and the fold-trace hook in `smart_beta/evaluation/engine.py`, P10-S), and the
P10-V pilot-harness secret sweep (`smart_beta/pilot/{runner,artifacts}.py`).

**Frozen plan:** `worker_tasks/phase10/phase10-plan.md`. It is authoritative
wherever this record and the plan disagree.

**Final Barrier master:** `31cc356b2273b046c65568ca2f436cc217346c94` (clean).
This record is committed as a docs-only child of that commit.

**Seal:** NOT YET. There is no `phase10-complete` tag and no push; the
seal, tag, push and cleanup each need separate authorization.

**Production code modified by this record:** none.

**§21 statement hash:**
`sha256` of the plan's §21 section is
`42549301d61beb4aed221052639a58154212d1e8ee4fc6f47f69ad108e63eb1b`. It was
unchanged throughout Phase 10 and was not strengthened at the Final Barrier.

Planning Claude wrote this record at the Final Barrier, as §17 requires.
Evidence classes are stated per CLAUDE.md §8: **MECHANICAL PROOF** means
tests that ran, and **REVIEWER JUDGMENT** means code inspection or mutation
analysis.

---

## 1. Integration history (all verified reachable from `31cc356`)

| Commit | Role |
|---|---|
| `610fd0bca979f0c2eb04b7f95be4e9de3651f9d9` | Science design SD v3.1 frozen; the sealed-file digest baseline |
| `dea829c2745dc07a0fb7bcb8cf268c3e89dd00da` | Phase-10 plan frozen (Barrier 0) |
| `b402bcfa0a47dd3b40d2f6576e0e425360f2c15b` | P10-I merged (Barrier 3) |
| `9a4e7f86ea84e1763845876013f7aa8ee6c556ac` | P10-E-R merged (pre-Wave-5 ACCESS/registry repair) |
| `1525269825475b92c05d1586f1bfb2af1b78f9c6` | §11.3 material-change amendment |
| `d12eeab22c8a3a1cbdfb79b7172231a7b753e7ec` | P10-G merged (Barrier 4) |
| `05a723ed776a7d81bf4f171f50c5e70dd31a3867`, `5dda4a232f841152fa1c9f9906b5662ea13bc38e`, `61bd075846c6a2c047a47e91209bfeb7ca10970d`, `3b7aa5c7f750608d069a653113c9ab4bae172278` | P10-H pre-launch amendments (§13.2a, §13.3a, §13.3b, §13.3c) |
| `cf08c48f08649063d57ecaeb461fedc59a4bcb07` | §5.4 rule-1a temporal amendment |
| `38c213a06012650290b45a7f02572770c95546de` → `2421b9fa59021fd96cdb64b910b79a73cd7cbebe` | P10-D-R2 rule-1a repair; merge |
| `337fcb5a6d29ab6632f91dc4ae6ddec4586f1e77` | §13.2a item 7a EvaluationSpec / CostModel.mode amendment |
| `ac6f9a7988a2c6883849761b7c5ea7aac787defb` → `a9259b9d68a0111b124491cb81e0fc10731a8c34` → `42145ebc35f6c8db714d09ffbd440e2b8950b91f` → `f52d0562a5a64fcac755d64b88b438dd0bdc91b1` | P10-H reviewed ancestry and merge (Barrier 5) |
| `c3715d5550d8fb43020c61b6431c0ddce51ea1e4` → `ccc30a6dd1330ec6b637ccbdefa977d41d4ecb37` → `31cc356b2273b046c65568ca2f436cc217346c94` | P10-Z reviewed ancestry and merge |

The earlier pre-rebase commit `a6cf010` is **not** a certification SHA;
`ac6f9a7` is its content-identical rebased form.

## 2. What was run at the Final Barrier (MECHANICAL PROOF, on `31cc356`)

Every pytest invocation removed `TIINGO_API_KEY`, `TUSHARE_PROXY_TOKEN`,
`TUSHARE_BASIC_PROXY_TOKEN`, `TUSHARE_API_TOKEN`, `DEEPSEEK_API_KEY`,
`ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` and `OPENAI_API_KEY` for that
process only, using `env -u`. No credential was globally unset or printed.
The P10-Z autouse guard blocks socket connections. There were no provider
or live calls and no real confirmation evidence.

| Surface | Result |
|---|---|
| P10-Z `tests/test_phase10_certification.py` (all) | 81 passed |
| — §21 clause tests | 12 passed |
| — §18 row tests (65 rows + Pilot-1A row) | 66 passed |
| — guards: sealed digest vs `610fd0bc`, Pilot-1A SHA256SUMS, empty production registry | passed |
| Phase-10 cross-module (13 science / P10-S files) | 555 passed |
| P10-V secret sweep | 10 passed |
| Full repository suite | **3833 passed, 1 skipped** |

- **Only skip:** `tests/test_tiingo_certification.py:697`, "TIINGO_API_KEY
  not set; live ranged-query verification not attempted". This is the one
  skip §17 permits.
- **Production inference registry:** `production=True`, **0** admitted
  procedures.
- **Pilot evidence:** `pilot_evidence/pilot1a-real-deepseek-v1` passes
  SHA256SUMS **25/25**.
- **Sealed paths:** since `610fd0bc` the only sealed-path changes are the
  §2.3 exceptions (`evaluation/engine.py` modified, `evaluation/inferential_series.py`
  added, `pilot/runner.py` and `pilot/artifacts.py` modified). No
  pre-existing test file was modified.
- **Working tree:** clean. The only ignored residue is `pilot_runs/`, Pilot-1A
  runtime output dated 2026-09-23, which predates Phase 10 and is not
  tracked.

## 3. §21 clause-by-clause mapping (§17 Final requirement)

| §21 clause | Certification test (`tests/test_phase10_certification.py`) |
|---|---|
| 1 Append-only Knowledge-PIT provenance | `test_clause_01_append_only_knowledge_pit_provenance` |
| 2 Deterministic, hypothesis-relative evidence roles | `test_clause_02_deterministic_hypothesis_relative_evidence_roles` |
| 3 Fail-closed unknown exposure | `test_clause_03_fail_closed_unknown_exposure` |
| 4 Preregistration before confirmation observation | `test_clause_04_preregistration_before_confirmation_observation` |
| 5 EvidenceFootprint-based one-use governance | `test_clause_05_evidence_footprint_one_use_governance` |
| 6 One primary estimand/test contract | `test_clause_06_one_primary_estimand_test_contract` |
| 7 Execution of a preregistered AND admitted procedure | `test_clause_07_deterministic_execution_of_admitted_procedure` |
| 8 Holm over the frozen confirmatory family | `test_clause_08_holm_over_the_frozen_confirmatory_family` |
| 9 Hypothesis-local NOT_SUPPORTED | `test_clause_09_hypothesis_local_not_supported` |
| 10 Separation from governance | `test_clause_10_separation_from_governance` |
| 11 Generator firewall | `test_clause_11_generator_firewall` |
| 12 Deterministic replay | `test_clause_12_deterministic_replay` |

**§18 coverage.** There is one test per row, named `test_<table>_row_*`.

| §18 table | Rows = tests |
|---|---|
| Knowledge-PIT | 13 |
| Preregistration | 5 |
| Inference | 14 |
| R-1 | 5 |
| Holm | 5 |
| EvidenceFootprint | 15 |
| Firewall | 4 |
| Replay/integrity | 4 |
| Pilot-1A (§19) | 1 |

## 4. Mutation evidence for the certification suite (REVIEWER JUDGMENT)

In the independent P10-Z review of `ccc30a6`, these production mutants were
each caught by the suite, and each was removed afterwards:
- the rule-1a seq filter reverted;
- Holm without the step-down multiplier;
- EFFECT_BELOW_SESOI mapped to NOT_SUPPORTED;
- the firewall overlap condition disabled;
- P10-H's pre-read one-use check skipped;
- the empirical reader invoked before the durable ACCESS;
- a late HUMAN NOT_EXPOSED declaration counting at the actual G3 gate
  (`roles._covering_declarations`).

**Audit clarification.** The first P10-Z review cited a late-NOT_EXPOSED
mutant in `roles._declaration_is_active`. That is not the G3 gate, and that
mutant is **not** evidence for the original finding. The original test-gap
finding stands on the fixture design alone: the old fixtures could not
isolate declaration timing. A mutant placed correctly at the G3 gate
**survives** the old `c3715d5` suite and is **caught** by the corrected
`ccc30a6` suite.

**Non-blocking survivor.** Disabling the K canonical-form check survives the
suite. §5.1, §21 clause 1 and the §18 replay rows require hash/chain tamper
detection, which the hash chain provides; no frozen requirement calls for
independent canonical-form sensitivity.

## 5. The bounded certification claim (verbatim §21; hash above)

> **Phase 10 certifies, mechanically and deterministically, on synthetic
> fixtures and preserved historical evidence, that:**
>
> 1. **Append-only Knowledge-PIT provenance.** Every record the protocol
>    uses is appended to a hash-chained, content-addressed log K whose
>    references point only to earlier records. Any edit, deletion,
>    reordering or insertion is detected, and every derived result names the
>    KnowledgeSnapshot it was computed from.
> 2. **Deterministic, hypothesis-relative evidence roles.** EvidenceRole(E,
>    H, P, K) is computed only by the frozen rule order of the plan (§5.4)
>    from K, and is never stored as a mutable field or assigned manually.
>    - A recorded pre-freeze influence path from evidence whose
>      source-observation footprint overlaps E's yields DEVELOPMENT.
>    - Later records can only downgrade a role.
>    - Exposure declarations are immutable, bound to the K prefix they attest
>      against, and count toward a confirmation role only if recorded before
>      the preregistration freeze.
> 3. **Fail-closed unknown exposure.** Missing, unverifiable or
>    undeterminable provenance or footprint overlap yields UNKNOWN_EXPOSURE
>    or NOT_ASSESSED, never a confirmation role.
> 4. **Preregistration before confirmation observation.** Every
>    confirmatory assessment references a preregistration whose record
>    precedes, in K order, the consumption and every read of its
>    confirmation data. A preregistration cannot reference later records, so
>    retrospective preregistration is impossible.
> 5. **EvidenceFootprint-based one-use governance.**
>    - Confirmation freshness is keyed to the canonical source-observation
>      footprint, to which derived quantities are expanded by frozen
>      derivation rules. It is not keyed to derived cells, files, artifact
>      hashes, vendors, dataset ids or run ids.
>    - A footprint that shares any source observation with, or cannot be
>      shown disjoint from, any previously consumed confirmation footprint is
>      refused.
>    - Consumption is recorded write-ahead and is never released.
> 6. **One primary estimand/test contract.** Each member has exactly one
>    primary estimand (fixed by a program estimand policy recorded before the
>    program's first hypothesis freeze), direction, null, SESOI, estimator,
>    inference-procedure reference (id, version, contract hash), parameters,
>    missingness policy and bound level, all frozen at preregistration.
> 7. **Deterministic execution of a preregistered AND Phase-10-admitted
>    inference procedure.**
>    - An inferential result is produced only when the member's
>      preregistration references a procedure version admitted in K before
>      the preregistration freeze, not revoked, and whose executing
>      implementation matches the admitted source hash and contract.
>    - That procedure alone is executed, on the Phase-7-bound per-date series
>      of the confirmation fold, under the preregistered missingness policy.
>    - There is no endpoint switch, fallback procedure or post-data repair.
>    - Missing inputs, unsupported missingness patterns or unadmitted
>      procedures yield NOT_ASSESSED.
>    - Neither preregistration nor admission is certified to make a procedure
>      statistically valid. Validity rests on the admitted procedure's
>      separately documented validation and stated assumptions.
> 8. **Holm over the frozen confirmatory family.** SUPPORTED is emitted only
>    for a Holm-adjusted rejection of the null boundary in the declared
>    direction, over exactly the m members frozen at preregistration. This
>    provides family-wise error control conditional on the validity (of the
>    declared type) of the individual p-values. Every SUPPORTED record also
>    carries a deterministic effect-size qualification relative to the SESOI
>    (`EFFECT_BELOW_SESOI` or `SESOI_NOT_EXCLUDED`) and never asserts that the
>    SESOI is reached.
> 9. **Hypothesis-local NOT_SUPPORTED.** NOT_SUPPORTED is emitted only when
>    the null is not rejected and the member's predeclared one-sided bound
>    excludes its SESOI. It is recorded as hypothesis-local, with no
>    family-wise claim. INCONCLUSIVE means neither determination holds.
>    NOT_ASSESSED means inadmissible.
> 10. **Separation from governance.** ScientificAssessment is a distinct
>     record. Phase-8 governance ACCEPT, development evidence and
>     unknown-exposure evidence are never converted into scientific support,
>     and production readiness is constantly NOT_CERTIFIED.
> 11. **Generator firewall against confirmation evidence.** Confirmation
>     evidence and anything derived from it (including SUPPORTED or
>     NOT_SUPPORTED bits) are structurally absent from the sealed
>     generator-visible history. Any recorded generator input that touches
>     consumed confirmation observations or their derivatives is detected as
>     a firewall violation.
> 12. **Deterministic replay.** The same K prefix, preregistration, admitted
>     procedure implementation and persisted evidence reproduce
>     byte-identical roles, inference results, Holm results and assessments.

## 6. Scope observations and residual limitations (recorded; not defects in the bounded claim)

- **Latent rule-1a defect.** P10-H integration found a latent rule-1a
  defect: a study's own CONSUMPTION forced ROBUSTNESS. The prior barriers had
  not exercised the own-CONSUMPTION case, and this record does not claim
  they failed. The defect was repaired by the §5.4 amendment and P10-D-R2,
  then re-certified by the Barrier-3/4 regression and Barrier 5.
- **No production procedure.** Zero production inference procedures are
  admitted. Phase 10 certifies the admission and execution machinery, not
  any production statistical procedure. Until Track P's PA barrier passes,
  every real study is NOT_ASSESSED.
- **CostModel.mode label.** Its semantics are not certified. In the current
  sealed Phase 7 it is declarative; the applied cost is `transaction_cost_bps`
  applied one-way once (§13.2a item 7a).
- **EvaluationSpec scope.** Certification covers only the frozen
  primary-estimand path. Reader-supplied fields outside that path are not
  certified.
- **P10-E freeze-time race.** The preregistration-freeze race in P10-E
  (check versus append of prior CONSUMPTION) stays outside the certified
  scope. P10-H's execution-time arbitration (§13.3c) is the protection.
- **K canonical-form check.** It is covered only through hash-chain
  integrity (§4 above).

## 7. NOT CERTIFIED — §22 nonclaims (verbatim)

Phase 10 does **not** claim:
- that any factor is true, or that alpha or a premium exists;
- production readiness or tradeability;
- complete reconstruction of human knowledge or exposure;
- complete reconstruction of LLM pretraining knowledge. G1 and G2 do
  **not** prove absence from pretraining; pretraining is a disclosed
  residual;
- the truth of declarations. **G3 depends on declarations**, as do vendor
  `available_from` dates, model cutoffs and the system clock behind
  `recorded_at`;
- universal statistical validity. **Scientific support is conditional on
  the preregistered and admitted procedure's validity assumptions and on its
  separately documented validation**, which are recorded, not certified by
  Phase 10;
- that preregistration or admission makes a procedure valid. Admission
  certifies process and implementation identity only;
- that any production inference procedure is admitted at the Phase-10 seal.
  Until Track P's PA barrier passes, every real study is NOT_ASSESSED;
- that SUPPORTED means the effect reaches the SESOI. It asserts only
  θ′ > 0 at the Holm level, with an explicit effect-size qualification;
- that complete panels are scientifically necessary. `COMPLETE_REQUIRED` is a
  v1 implementation limitation;
- the authenticity of declarant identities (no cryptographic signatures);
- complete reconstruction of historical access. **Absence of an ACCESS
  record is not proof that no machine or human read ever occurred.** G2/G3
  express separation relative to the recorded Knowledge-PIT and declaration
  evidence governed by the protocol. Reads outside the mechanically ingested
  Phase-8 registry remain declaration-dependent;
- family-wise error control for NOT_SUPPORTED;
- that Holm corrects the adaptive exploration history, validates individual
  p-values, or controls error across studies;
- general semantic or cross-vendor data equivalence beyond the
  source-observation canonicalization and derivation rules. **Unlinked
  identifier continuity is NOT CERTIFIED**, and unmapped observations are
  refused, never assumed fresh;
- reusable confirmation data;
- generalization outside the confirmation study. **One confirmation period
  does not prove persistence**;
- repair of the Phase 7 + 9 temporal composition (FIX B remains NOT
  CERTIFIED).

Carried forward as explicit statements:
- **Historical evidence is not automatically invalid.**
- **Prospective evidence has the strongest mechanically auditable
  separation.**
- **Robustness is not replication.**
- **Replication is not production readiness.**

## 8. Disposition

**PHASE-10-FINAL-PASS** at master `31cc356b2273b046c65568ca2f436cc217346c94`
for exactly the §21 statement above, subject to §6 and §7. The `phase10-complete`
seal/tag, push, branch/worktree/Herdr cleanup, Track P and Phase 11 each
require separate explicit authorization.
