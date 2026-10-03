# Campaign-1 identity architecture correction — hypothesis definition vs factor realization (frozen)

- **Status:** FROZEN. Approved by the user ("批准两份草稿，冻结并提交"), on master
  `3d5c3cd`. Decision D-A (user): HypothesisDefinition carries **no** Campaign-level
  fields.
- **Nature:** an intentional, prospective **architecture correction**. It is not an
  implementation optimization. It amends `docs/campaign1_precommitment.md`
  (`15f700606929889d4b9b64799e49603f0f5e84081df5557519d815cc556619d7`) §12 item 2 and
  §13 item 6, and records the Phase-10 interpretation needed for Campaign-1. The
  precommitment file is **not modified**: the old rule stays visible and is superseded
  here.
- **Scope:** Campaign-1 identity domain only. Phase-8, Phase-9 and Phase-10 code and
  rules are unchanged.

## 1. OLD rule (superseded for Campaign-1; preserved verbatim by reference)

Precommitment §12 item 2: "`hypothesis_id` = `hypothesis_id_for(factor_provenance_hash)`,
where the provenance is the Phase-6 `EngineResult.content_hash` of the factor panel
evaluated on the D7c TrustedInputs."

Precommitment §13 item 6: "the factor panels are computed (hashed only), then 7
`HYPOTHESIS_FREEZE` records are written …"

In effect, `hypothesis_id` bound the **realized** Phase-6 factor panel.

## 2. Why it is corrected

The old rule conflates two concepts:
- **hypothesis definition identity:** what was preregistered;
- **factor realization identity:** what a particular execution produced.

A definition must exist **before** execution, but the old rule could only be satisfied
by materializing the full 2138 × 10353 factor panel first (about 11.5 min per factor on
the reference path). That made preregistration depend on an execution artifact.

## 3. NEW rule

**HypothesisDefinition (schema `campaign1-hypothesis-definition-v1`):**

```
{"schema": "campaign1-hypothesis-definition-v1",
 "factor_spec_hash": <Phase-6 factor_spec_hash of the frozen spec>,
 "trusted_inputs": {"ti_id": "ti-aa23fabd3db4e595",
                    "ti_hash": "30ee0c51602cf792e8cb7654c2501f52c7d664675dbf78845c903df6683c2ae6",
                    "manifest_sha256": "d8d40099ba978604727a7d316274a2d3e7f8210baf8cb35e4ca687435fd8f8f0"},
 "engine_semantics": {<repo path>: <SHA-256>, … for the 10 files below}}
```

| Field | Existing authoritative identity reused | What it binds |
|---|---|---|
| `factor_spec_hash` | Phase-6 `factor_spec_hash` (= `FactorSpec.version`), from the frozen declaration `f93a218d…532c` | expression, inputs, sign, missing policy, frequency |
| `ti_id`, `ti_hash` | D7c's own content identity: `ti_hash = sha256(canonical_json(files_sha256))` | the exact factor-input bytes |
| `manifest_sha256` | the SHA-256 of the D7c `trusted_inputs.json` | the temporal semantics `knowledge_date_rule` ("row d holds the end-of-day observation of prev(d)") and the upstream stage bindings. This is required because `ti_hash` covers the parquet files only |
| `engine_semantics` | the repository's pinned-code-hash convention (PQB `PINNED_CODE`, D7b `Bindings`). Phase-6 has no semantic version constant | `smart_beta/__init__.py` `a21ef162…339e`; `smart_beta/data/__init__.py` `b9b2ed7a…cefec`; `smart_beta/data/schema.py` `28d3ac32…50981`; `smart_beta/spec/__init__.py` `be94f95e…11f32`; `spec/engine.py` `eea3bc30…3b46a`; `spec/evaluator.py` `aecdeba6…5bcce`; `spec/expression.py` `9b027c5d…6d6f4`; `spec/factor_spec.py` `ab6c65d3…58c56`; `spec/requirements.py` `e0b99194…c57dc`; `spec/transforms.py` `15708149…f5a66`. These are the import closure of `evaluate_factor`, unchanged since `phase6-complete`. Full hashes are in §6 |

- **Excluded (D-A):** program, Campaign, estimand, universe and screen context. These
  are bound by `campaign1_id` and `campaign_attempt_id`, which both include
  `hypothesis_id`. A definition re-tested in a later program keeps its `hypothesis_id`,
  so hypothesis-scoped exposure declarations carry over conservatively.
- **Never included:** factor values or any empirical outcome.

**Derivation (frozen, versioned):**

```
hypothesis_definition_hash = smart_beta.experiment.registry.content_hash(HypothesisDefinition)
hypothesis_id              = smart_beta.experiment.registry.content_hash(
                               {"domain": "campaign1-hypothesis-id-v1",
                                "hypothesis_definition_hash": hypothesis_definition_hash})
```

`hypothesis_id_for(...)` is **not** used. Its argument means the factor provenance, so
passing a definition hash would reintroduce the conflation. The domain tag separates
Campaign-1 IDs from the Phase-8 identity domain.

**Factor realization identity:** `factor_realization_hash` = the Phase-6
`EngineResult.content_hash` of the factor panel evaluated on the D7c inputs. This is
the former `factor_provenance_hash`, with unchanged computation.
- It is produced **by execution** and bound into execution provenance (C1-LX-E4).
- **Same `hypothesis_id` with a different `factor_realization_hash` = REALIZATION /
  REPRODUCIBILITY FAILURE, not a different hypothesis.** It fails closed under the
  existing precommitment §11 "determinism failure" stop
  (`INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE`).
- The two-deterministic-runs requirement moves to the execution-readiness barrier,
  which computes `factor_realization_hash` twice independently per hypothesis and
  requires equality before any evaluation.

## 4. Campaign-1 identities under the NEW rule (computed from metadata only; no factor evaluation)

| Label | factor_spec_hash | hypothesis_definition_hash | hypothesis_id |
|---|---|---|---|
| C1-H1 | `9b27eb881a7b31bfe123866f82996638315be6257c333fcf04cfcae3a02f037c` | `ce26a34da8a62912f10a50ad9c25447b62ecb1b56e2d5a031913691785420e14` | `b5e2ced5365c5963e7ca52ec839f9a356a629ee3d41ecbaa9ded3f5572826b6e` |
| C1-H2 | `85a7166bf2953fd9240037dc5189cb63dd3ae93690b2574c2f6d0567136d54fc` | `e2503e5240cce5b38071af96d19d11e118ec78dd84ae4152415a6ed6ce70fce9` | `39f6ded371924f6c7d7d9d25c59950123b673a3837422d36fb0e15172762374f` |
| C1-H3 | `78a6d4547aecee3615841408be1df040fdb33037fe465b790bcd75c5d76fe99f` | `6bd890da620263418709623fc23963cb82e258b2b2bfc8a01f600e132be15da2` | `4f6a7ddfdc67c3be4f6ddedb2f42c8ba1c52165bf01ede7ea84ff3f2cdcb53f9` |
| C1-H4 | `54b7edf3c0f00e5c53c8d87eee7a9c9bfd26287e15b04dc8ac38f57d3e4f1a6f` | `095b126b5227c180bd49568daa02222c3e77ee460917b5e2f4d263ab1e2cc1d0` | `b7dce1008472217507d3140e1ea9dd351ee11c532e9540a541f83e2f911b7a2c` |
| C1-H5 | `ee5b8ea8f57262945828d545910584b6b49546af344c9a2bfbd2164380564f26` | `e859f7aa604b3fca1a19b2ea16e87ec5d056064a6e15c4206ebba2727a8b176a` | `a6de29a5a21930c249b010978f837557e287900ad0391b7d9bd9788584920e72` |
| C1-H6 | `42605a13a41cf0c9a04b614424b4bee1643bd565b1ec5b67c58a4b6d6ed816a1` | `ba7714468f9b3641e249764e1b076491b32531b16a2bfd5da51e2c29b6dad7ac` | `e34ecaf78791d9387a4e1b06f0d28e816e08f0dc2d19ebb5a3ceac9d5e39ed22` |
| C1-H7 | `32678dfda88647a666994e62697742955f7903b141431a628b3df98895be66b0` | `efd345b3c280ca81905b7a6e655e2e30ff4c4e8b01173fa949fea4dddfaba8d3` | `983189fe6c5c188c0e488c26d284b34853deadac8c4cba1e1e1eb5f33ccf3539` |

The 7 IDs are unique. There is no collision with the legacy C1-H1 ID and no collision
with Phase-8-domain `hypothesis_id_for` outputs. The implementation (C1-LX-E4) must
reproduce exactly these values.

**Disclosed legacy evidence (not an identity):** under the OLD rule, a single
reference-path run produced C1-H1 `factor_provenance_hash`
`c1dfedb7e77758b11cda2547f20ed3c012d3ffc48d26d0ca627322ff0041c714` and legacy
`hypothesis_id` `09bb4ecffce522163709df816ef2a8bdbe8bbb2414a6c27c1d8baa9b80b038b1`
(688 s; hashes only; factor inputs only). The legacy ID is **not** the new C1-H1 ID.
The provenance value is disclosed reference evidence only. The execution-readiness
barrier's two independent realization runs remain authoritative.

## 5. Phase-10 interpretation (Campaign-1 only; no Phase-10 change)

Phase-10 plan §5.2 annotates `HYPOTHESIS_FREEZE.hypothesis_id` as "(Phase 8)".
Campaign-1 `hypothesis_id` values are **Campaign-1 definition-domain IDs**, not Phase-8
registry IDs, because Campaign-1 never registers Phase-8 experiments (D-1(a)).

This is compatible with Phase-10:
- `knowledge.py` validates `hypothesis_id` only as a non-empty string;
- MemberContract, roles and assessment use it as an opaque key;
- confirmation-time Stage-B binding (`study.py` L936–L944) already binds a member by
  `factor_spec_hash` (the definition) and checks `factor_provenance_hash ==
  EngineResult.content_hash` (the realization) separately.

Phase-8-registered experiments keep their Phase-8 IDs and rules unchanged.

## 6. Why the correction is permissible now

- Campaign-1 empirical execution has not started.
- Campaign slots consumed: 0.
- Campaign-1 production K-PIT writes: 0 (log `cf6b9aa8…`, 1 record).
- No empirical outcome was observed.
- The final holdout is untouched, and so is the Dataset reserve.
- The only OLD-rule artifact is the disclosed C1-H1 hash above.

**Full engine-semantics hashes:**

```
smart_beta/__init__.py          a21ef162d78080927c5d8adb2cd8bd15f72aace0546e81669f3abb486407339e
smart_beta/data/__init__.py     b9b2ed7a1bbc8281d10076a7a029bcf69d99be2cd15cb4fb132e156a327cefec
smart_beta/data/schema.py       28d3ac32e8c46856b166a1271b2cdf5bcbd6bf9d3bd51b11c660bd1859b50981
smart_beta/spec/__init__.py     be94f95e4ca9ff33c5a51e1fda0fc7b52c3fe720a1ea143600182c3f25011f32
smart_beta/spec/engine.py       eea3bc30dd50121865009d0bab4999dc76068c1e2fbf9f7c965413c2a443b46a
smart_beta/spec/evaluator.py    aecdeba642ca23f3285b8f55565afb6edaf255322783345e22a50c20c405bcce
smart_beta/spec/expression.py   9b027c5da83f0778d72c236327c09967172a5314d60c47bd54ea6337fe46d6f4
smart_beta/spec/factor_spec.py  ab6c65d312d6fe1e421c489e6ad8045a50e265c8f73dbe6a07738e1bf5458c56
smart_beta/spec/requirements.py e0b99194cbdb8f1c907a03958ca2b0c1ba7b877790ef562570c83b52324c57dc
smart_beta/spec/transforms.py   15708149406017a6c970d42aebef7b0e2ea9ed6b0be48182f7769006a9ff5a66
```

## 7. Explicit non-changes

| Item | Change |
|---|---|
| Hypotheses / FactorSpecs | NONE |
| `family_id` | NONE (built from factor_spec_hashes) |
| m, α | NONE (m = 7, α = 0.05) |
| Screen | NONE |
| Critical value | NONE |
| Budgets | NONE |
| Folds | NONE |
| EstimandPolicy | NONE |
| Dataset-v1.2 | NONE |
| Phase-8 code and rules | NONE |
| Phase-9 code and rules | NONE |
| Phase-10 code and rules | NONE |
| Empirical information used | NONE |
