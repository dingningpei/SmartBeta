# Campaign-1 Knowledge-PIT registration transaction plan v2 (frozen plan; WRITE NOT AUTHORIZED)

- **Status:** FROZEN PLAN, from the "CAMPAIGN-1 REGISTRATION TRANSACTION REVIEW v2"
  authorization, on master `b73e0bf`.
- **The production append is NOT authorized by this document.** It needs a separate
  write authorization.
- **Supersedes:** the v1 review (HOLD, R1–R3). Its decisions are carried here: R1
  append-time hashes, R2 resolved by E4 definition identities, R3(b) HUMAN-channel
  ESTIMAND_POLICY.

## 0. Bound authorities

| Authority | SHA-256 |
|---|---|
| `docs/campaign1_precommitment.md` | `15f700606929889d4b9b64799e49603f0f5e84081df5557519d815cc556619d7` |
| `docs/campaign1_factorspecs.json` | `f93a218d1e8a5bc07a7e64b52e1ce66be09b6708479d5c76a5f9a2973d6d532c` |
| `docs/campaign1_identity_architecture_correction.md` | `50f9929f10b3e037b6f554eea4cdf08868e54a5b1a346338c0cdd4224a9734b8` |
| `docs/campaign1_human_hypothesis_freeze_provenance_clarification.md` | `22a0bc9d3bbbd10cc63ef8dacf5087316aaed2125145afa67c85ce7f7e74d72c` |
| C1-LX task + E1–E4 (merged code `b73e0bf`) | task `118bcf41…36fd`; E1 `6cc544ba…e9ca`; E2 `90251a1d…778c`; E3 `e070a6a7…4c07`; E4 `1c5a001c…2ade` |
| Phase-10 plan §4.1, §5.1, §5.2, §7.1, §12.3 | as reviewed in v1 and in the provenance clarification |

## 1. Why exactly nine records

- Phase-10 §7.1 requires only that ESTIMAND_POLICY precede the program's first
  HYPOTHESIS_FREEZE.
- The precommitment §13 items 4–6 and C1-LX §9 fix ESTIMAND_POLICY → PROGRAM_FREEZE →
  the seven HYPOTHESIS_FREEZE.
- FAMILY_SELECTION belongs to preregistration and is **not** required before a freeze.
- No other record is required.

## 2. A — PRE-WRITE FROZEN

| Item | Value |
|---|---|
| Production log | `data_store/knowledge_pit/knowledge.jsonl`, current SHA-256 `cf6b9aa80b1f0099458c9bf969b4157f8f8bd5a590e73354be06832a461b69b6` |
| Starting terminal record | seq **0**, `record_hash` `984d7bec4fbb159b3eba3385b2a0af24a54c2c707478af91b5ba461684da4eea` (D8 genesis ARTIFACT, channel SYSTEM, `program_id` null). No Campaign-1 record exists |
| New sequence range | **1–9** |
| `program_id` (all nine) | `campaign1:15f700606929889d4b9b64799e49603f0f5e84081df5557519d815cc556619d7` |
| `family_id` (context; not a record field) | `2d2f16f56fe34ee5fecfb4613a6a96ecdc621f2836e5c2d6ea4fba5c77f8d1d0` |
| `footprint`, `event_time` (all nine) | `null` |
| Write API | `smart_beta.science.knowledge.KnowledgeLog.append` for **all nine**, called directly. Not `append_estimand_policy`, which would force SYSTEM; not `record_hypothesis_freeze`, which would force GENERATOR. `record_program_freeze` would give a byte-identical PROGRAM_FREEZE, but direct `append` is used for uniformity |

**Records** (`refs` written as the four Phase-10 lists; empty lists omitted here for
brevity, but present in the record):

| seq | kind | channel | label | refs |
|---|---|---|---|---|
| 1 | `HUMAN_DECISION` (`ESTIMAND_POLICY`) | `HUMAN` | — | all empty |
| 2 | `HUMAN_DECISION` (`PROGRAM_FREEZE`) | `HUMAN` | — | all empty |
| 3 | `HYPOTHESIS_FREEZE` | `HUMAN` | C1-H1 | `influenced_by = [hash(seq1), hash(seq2)]` |
| 4 | `HYPOTHESIS_FREEZE` | `HUMAN` | C1-H2 | same |
| 5 | `HYPOTHESIS_FREEZE` | `HUMAN` | C1-H3 | same |
| 6 | `HYPOTHESIS_FREEZE` | `HUMAN` | C1-H4 | same |
| 7 | `HYPOTHESIS_FREEZE` | `HUMAN` | C1-H5 | same |
| 8 | `HYPOTHESIS_FREEZE` | `HUMAN` | C1-H6 | same |
| 9 | `HYPOTHESIS_FREEZE` | `HUMAN` | C1-H7 | same |

**Reference topology:**
- `influenced_by` order is exactly [ESTIMAND_POLICY, PROGRAM_FREEZE]. Phase-10
  `_normalize_refs` preserves caller order and does not sort.
- seq 1 and seq 2 carry `consulted_all_prior = true` (the Phase-10 conservative
  default) and no refs.

**Deterministic payloads** (canonical JSON: sorted keys, compact):

```
seq1 {"actor_role":"OPERATOR","construction":{"cost_bps":10.0,"factor_missing_policy":"PROPAGATE","n_groups":5,"winsorization":0.01},"consulted_all_prior":true,"decision_kind":"ESTIMAND_POLICY","estimand_kind":"MEAN_NET_LONG_SHORT","horizon":21,"program_id":"campaign1:15f700606929889d4b9b64799e49603f0f5e84081df5557519d815cc556619d7","sesoi":0.0025,"sesoi_justification_hash":"2565ce442a0edc4d4916d2dcb8621a7d9260cb6742bbcf2f6b0f4e5203844493"}
seq2 {"actor_role":"OPERATOR","consulted_all_prior":true,"decision_kind":"PROGRAM_FREEZE"}
seq3 {"factor_spec_hash":"9b27eb881a7b31bfe123866f82996638315be6257c333fcf04cfcae3a02f037c","hypothesis_id":"b5e2ced5365c5963e7ca52ec839f9a356a629ee3d41ecbaa9ded3f5572826b6e"}
seq4 {"factor_spec_hash":"85a7166bf2953fd9240037dc5189cb63dd3ae93690b2574c2f6d0567136d54fc","hypothesis_id":"39f6ded371924f6c7d7d9d25c59950123b673a3837422d36fb0e15172762374f"}
seq5 {"factor_spec_hash":"78a6d4547aecee3615841408be1df040fdb33037fe465b790bcd75c5d76fe99f","hypothesis_id":"4f6a7ddfdc67c3be4f6ddedb2f42c8ba1c52165bf01ede7ea84ff3f2cdcb53f9"}
seq6 {"factor_spec_hash":"54b7edf3c0f00e5c53c8d87eee7a9c9bfd26287e15b04dc8ac38f57d3e4f1a6f","hypothesis_id":"b7dce1008472217507d3140e1ea9dd351ee11c532e9540a541f83e2f911b7a2c"}
seq7 {"factor_spec_hash":"ee5b8ea8f57262945828d545910584b6b49546af344c9a2bfbd2164380564f26","hypothesis_id":"a6de29a5a21930c249b010978f837557e287900ad0391b7d9bd9788584920e72"}
seq8 {"factor_spec_hash":"42605a13a41cf0c9a04b614424b4bee1643bd565b1ec5b67c58a4b6d6ed816a1","hypothesis_id":"e34ecaf78791d9387a4e1b06f0d28e816e08f0dc2d19ebb5a3ceac9d5e39ed22"}
seq9 {"factor_spec_hash":"32678dfda88647a666994e62697742955f7903b141431a628b3df98895be66b0","hypothesis_id":"983189fe6c5c188c0e488c26d284b34853deadac8c4cba1e1e1eb5f33ccf3539"}
```

- **seq 1:** the ESTIMAND_POLICY content is exactly `estimand_policy_content(C.estimand_policy())`
  plus `decision_kind`, `actor_role` and `consulted_all_prior`, the same payload shape
  as the Phase-10 helper. It matches precommitment §4 and C1-LX §1.
- **seq 3–9:** the hypothesis IDs are the E4 definition-domain IDs, recomputed at review
  through the merged definition-only path `hypothesis_identities(root)`, with 0
  factor-evaluation or parquet calls.
- **`proposal_id`:** absent. There is no Phase-9 proposal.
- **Legacy C1-H1** (`09bb4ecf…38b1`; realization `c1dfedb7…c714`): **not** used in any
  record. No frozen disclosure field requires it.

**R3 rationale (frozen here):**
- Campaign-1's ESTIMAND_POLICY is a HUMAN-origin precommitment decision.
- Under the frozen channel semantics ("the channel through which an observation /
  information reached the research"; provenance clarification `22a0bc9d…`), HUMAN is
  the truthful channel.
- The Phase-10 helper `append_estimand_policy` omits `channel` and would default to
  SYSTEM. Campaign-1 therefore writes the identical payload via `KnowledgeLog.append`
  with `channel = HUMAN`.
- This is a Campaign-1 provenance choice, not a rewrite of Phase-10 helper semantics.
  No Phase-10 code changes.

## 3. B — APPEND-TIME ATTESTED (never precomputed)

- **`recorded_at` of each record:** the genuine attested system clock at append. No
  injected or frozen clock.
- **`record_hash` of each record**, and therefore the chain `prev_hash` values for
  seq 2–9.
- **The actual `hash(seq1)` and `hash(seq2)`** substituted into `influenced_by` of
  seq 3–9.
- **The resulting terminal seq-9 `record_hash`** and the log file SHA-256 after the
  transaction.

## 4. Execution procedure (for the future authorized write)

**Pre-write barrier.** Abort before the first write if any check fails:
1. master and every §0 authority hash are unchanged;
2. the production log SHA-256 equals `cf6b9aa8…b6` and `read_records` succeeds;
3. the terminal seq is 0 and its hash is `984d7bec…a4eea`;
4. no record with this `program_id` exists;
5. `hypothesis_identities(root)` reproduces the seven IDs with 0 factor evaluation;
6. `family_id` equals `2d2f16f5…d1d0`;
7. all nine planned payloads equal §2 byte for byte.

**Append loop.** For seq = 1…9:
1. confirm the head seq is seq − 1 (no interleaving);
2. call `KnowledgeLog.append` with the §2 fields;
3. after each append, verify kind, channel, `program_id`, canonical payload, refs
   (with the actual seq 1/2 hashes), and null footprint/event_time.

## 5. C — POST-WRITE VERIFIED

Registration is complete only when all of these pass:
- exactly 10 records (genesis + 9), seq 1–9;
- `read_records` succeeds, confirming chain validity;
- kinds, order and channels exactly as in §2 (nine HUMAN);
- every payload byte-equal to §2;
- the seven `hypothesis_id`s and `factor_spec_hash`es equal §2;
- each HYPOTHESIS_FREEZE `influenced_by` equals [actual hash(seq1), actual
  hash(seq2)];
- `estimand_policy_from_record(seq1)` equals `C.estimand_policy()`;
- the C1-LX `verify_kpit_ordering(log, identities=hypothesis_identities(root))` is OK;
- the final terminal seq/hash and the file SHA-256 are recorded in the registration
  report.

## 6. Partial-write recovery (frozen)

Knowledge-PIT is append-only. **Never delete, rewrite or truncate.**

| State | Action |
|---|---|
| 0 Campaign-1 records | start normally |
| 1–8 records | resume **only the exact remaining suffix**, if all of these hold: the prefix matches §2 on every deterministic field; seq values match; the chain is valid; there is no unexpected or interleaved record; the actual prefix hashes satisfy the topology; no semantic field must change. Otherwise **STOP**, and separate recovery authorization is required |
| All 9 written, verification interrupted | **verification only**; never append duplicates |
| All 9 written and verified | registration complete |

## 7. Synthetic validation (SYNTHETIC ONLY; disposable copies of the production log; deleted afterwards)

The planner's scratchpad transaction tool (SHA-256
`c55fa75da337e58cccd6ee71164827c074c77ba538fc1729352afa2c41371ec8`, not committed)
implements §4–§6 with the production `KnowledgeLog`, `read_records`,
`estimand_policy_from_record` and C1-LX `verify_kpit_ordering`.

| Test | Result |
|---|---|
| T1 full transaction | 10 records; nine HUMAN; topology exact; EstimandPolicy round-trip OK; C1-LX preflight OK. Synthetic terminal seq 9 `ad18403f…8f25`, file `ea00dcb0…69ac`. These are append-time values of the synthetic run and do **not** predict production hashes |
| T2 interrupted after seq 4, then resumed | only seq 5–9 appended; preflight OK |
| T3 re-run on a complete log | 0 bytes appended (verification only) |
| T4 foreign record interleaved after seq 3 | STOP; file unchanged |
| T5 tampered prefix (SYSTEM-channel ESTIMAND_POLICY) | STOP; file unchanged |

Factor-evaluation and parquet calls: 0. The production log stayed byte-identical
(`cf6b9aa8…b6`) before and after.

## 8. Not authorized by this plan

- the production append;
- execution-readiness realization runs;
- Campaign-1 execution;
- any code or authority change.
