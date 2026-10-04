# Campaign-1 execution-readiness realization evidence (evidence only; EXECUTION NOT AUTHORIZED)

- **Status:** EVIDENCE RECORD of the "CAMPAIGN-1 EXECUTION-READINESS REALIZATION
  BARRIER" authorization. Run 2026-10-03, 16:58–19:36 local time.
- **Authority:** none. This document changes no scientific, statistical or identity
  authority.
- **Use:** it supplies the agreed `factor_realization_hash` values that C1-LX-E4 §2
  requires as the executor's `expected_realizations` input, to be bound into
  `CAMPAIGN_OPEN` at a future, separately authorized execution.
- **Contents:** hashes and metadata only. No factor values, summaries or distributions.

## Baseline (verified before computation)

| Item | Value |
|---|---|
| master | `82ac6be` (clean) |
| Production K-log | SHA-256 `190d53f09240acbd86e6256d6725d082c72ea19928843b5ec7b4cc11ad18f6a2`; terminal seq 9, `13d04ef4ee32de16771371a2610c7320a42dbe980dde4f45f32bff94969bd262` |
| Authorities | identity correction `50f9929f…34b8`; C1-LX-E4 `1c5a001c…2ade`; registration plan `e04505cf…73cf` |
| Registered identities | the 7 registered (`hypothesis_id`, `factor_spec_hash`) pairs equal the definition-only `hypothesis_identities(root)` output |

## Realization path and frozen identities

- **Path:** `smart_beta.campaign.campaign1.factor_realization_hash(spec, load_trusted_inputs(root))`
  = Phase-6 `EngineResult.content_hash` (merged code `b73e0bf`).
- **Isolation:** each run was a separate process (strictly sequential; max concurrency
  1; 60-minute cap). Runs had network denial, file-open audit and process-local
  credential scrub.

**Frozen inputs, identical in all 14 runs and equal to the HypothesisDefinition:**
- D7c `ti_id` `ti-aa23fabd3db4e595`, `ti_hash` `30ee0c51602cf792e8cb7654c2501f52c7d664675dbf78845c903df6683c2ae6`;
- manifest SHA-256 `d8d40099ba978604727a7d316274a2d3e7f8210baf8cb35e4ca687435fd8f8f0`;
- the 10 engine-semantics file hashes as in the identity correction §6.

## Results (A = run 1, B = run 2; all A == B)

| Hypothesis | Registered hypothesis_id | factor_spec_hash | factor_realization_hash (A = B) | A s / B s |
|---|---|---|---|---|
| C1-H1 | `b5e2ced5365c5963e7ca52ec839f9a356a629ee3d41ecbaa9ded3f5572826b6e` | `9b27eb881a7b31bfe123866f82996638315be6257c333fcf04cfcae3a02f037c` | `c1dfedb7e77758b11cda2547f20ed3c012d3ffc48d26d0ca627322ff0041c714` | 760 / 546 |
| C1-H2 | `39f6ded371924f6c7d7d9d25c59950123b673a3837422d36fb0e15172762374f` | `85a7166bf2953fd9240037dc5189cb63dd3ae93690b2574c2f6d0567136d54fc` | `e8d94efb31e5b4063869aa2e1de5eedd5d244fe5812eb9abb5aa2b0106c8243f` | 539 / 561 |
| C1-H3 | `4f6a7ddfdc67c3be4f6ddedb2f42c8ba1c52165bf01ede7ea84ff3f2cdcb53f9` | `78a6d4547aecee3615841408be1df040fdb33037fe465b790bcd75c5d76fe99f` | `72d9f8538bcc8fa9631dfc4c0f4a13301189fd2cf60d4e5960ebcda0fe9f52cf` | 491 / 573 |
| C1-H4 | `b7dce1008472217507d3140e1ea9dd351ee11c532e9540a541f83e2f911b7a2c` | `54b7edf3c0f00e5c53c8d87eee7a9c9bfd26287e15b04dc8ac38f57d3e4f1a6f` | `5f490ac4df78c213b3e12c3433c1fcbf740563c3b3ed52fbda5b318a83146c25` | 535 / 516 |
| C1-H5 | `a6de29a5a21930c249b010978f837557e287900ad0391b7d9bd9788584920e72` | `ee5b8ea8f57262945828d545910584b6b49546af344c9a2bfbd2164380564f26` | `201fed58d42c2e9c247c914cad1abc8c6fc39644ebc66e811a2dd923807c49e9` | 508 / 1531 |
| C1-H6 | `e34ecaf78791d9387a4e1b06f0d28e816e08f0dc2d19ebb5a3ceac9d5e39ed22` | `42605a13a41cf0c9a04b614424b4bee1643bd565b1ec5b67c58a4b6d6ed816a1` | `93cf20645ca8389e0be21bd24000a5f3d1328166f84e9741299f9a0b4ea2d280` | 705 / 614 |
| C1-H7 | `983189fe6c5c188c0e488c26d284b34853deadac8c4cba1e1e1eb5f33ccf3539` | `32678dfda88647a666994e62697742955f7903b141431a628b3df98895be66b0` | `529fdf7b2b8ca69200b8d64a3123de565ca1906c157eaaf8d722f9b79bd7657d` | 521 / 1018 |

**Legacy C1-H1 reference (diagnostic only):** the pre-correction reference realization
`c1dfedb7e77758b11cda2547f20ed3c012d3ffc48d26d0ca627322ff0041c714` **equals** C1-H1 A
and B. This is consistent with an unchanged realization computation. The legacy
`hypothesis_id` is not reinterpreted and is not used.

**Resources:**
- peak RSS per run: about 5.7–9.3 GB;
- system swap in use: about 8.5–9.7 GB throughout, with no thrashing and no failures;
- H5-B and H7-B ran slower (1531 s, 1018 s) under memory pressure but completed.

## Firewall and accounting

| Item | Value |
|---|---|
| Files opened (all 14 runs) | only `derived/trusted_inputs/ti-aa23fabd3db4e595/{daily_total_return, dollar_volume, segments, total_return_index}.parquet` and `trusted_inputs.json`, plus a Python bytecode cache of imported code |
| Outcome files (D7a labels, forward returns) | NONE |
| DEVELOPMENT_SEALED_FOLD factor inputs | YES. The frozen D7c grid spans 2015-01-02 … 2023-06-30, including the 2022–2023 sealed-fold period, as the frozen realization definition requires |
| DEVELOPMENT_SEALED_FOLD outcomes, final holdout, reserve | NO |
| Network calls | 0 |
| Phase-7/8 evaluation, Campaign evaluator | not called |
| K-PIT writes | 0 (log SHA-256 unchanged before and after: `190d53f0…f6a2`) |
| Campaign slots consumed | 0 (no ledger and no `PRIMARY_ATTEMPT`) |

## Independent read-only verification

The following all PASS:
- 14 evidence records (7 × 2);
- A == B for all 7;
- registered `hypothesis_id` / `factor_spec_hash` equal the K-PIT HYPOTHESIS_FREEZE
  payloads;
- trusted-input and engine-semantic identities equal the current HypothesisDefinition
  and are identical across runs;
- `hypothesis_definition_hash` reproduces;
- no outcome access, no K-PIT mutation, no Campaign slot consumption.
