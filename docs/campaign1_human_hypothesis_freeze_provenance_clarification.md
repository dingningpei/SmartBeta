# Campaign-1 human hypothesis freeze provenance clarification (frozen; documentation only)

- **Status:** FROZEN. Approved by the user ("CAMPAIGN-1 HUMAN HYPOTHESIS FREEZE
  CLARIFICATION APPROVED") on master `1e4d1df`, following the planner's
  "CHANNEL.GENERATOR AUTHORITY REVIEW — CASE B".
- **Scope:** Campaign-1 `HYPOTHESIS_FREEZE` provenance only. This is not a
  registration, not a Knowledge-PIT write and not a confirmation claim.
- **Campaign binding:** `docs/campaign1_precommitment.md`
  (`15f700606929889d4b9b64799e49603f0f5e84081df5557519d815cc556619d7`) §13 items 4–6;
  C1-LX merged at `1e4d1df`.

## 1. Reviewed authority

| Authority | Content relied on |
|---|---|
| `worker_tasks/phase10/phase10-plan.md` §4.2 (L143) | `Channel` is a closed enum: `GENERATOR`, `PROGRAM`, `HUMAN`, `PRETRAINING`, `PUBLIC`, `SYSTEM`. No definition is given there |
| Phase-10 plan §5.1 | `channel` is an envelope field. `record_hash` = `content_hash` of all envelope fields |
| Phase-10 plan §5.2 (L267, L278) | the `HYPOTHESIS_FREEZE` row fixes the payload (`hypothesis_id`, optional `proposal_id`, `factor_spec_hash`) and `refs.influenced_by` ("generator inputs and human decisions"). It does **not** fix a channel. The design edge "`observed_by` → `channel`" |
| Phase-10 plan §5.3 (L337–L340) | program-scope conservatism seeds every record "with channel ∈ {GENERATOR, PROGRAM, HUMAN}" in the program lineage |
| Phase-10 plan §12.3 (L1507–L1508) | ingestion: "`HYPOTHESIS_FREEZE` per admitted proposal/experiment, with `influenced_by` = its GenerationEvent's GENERATOR_INPUT" |
| `docs/planning/POST_PILOT1A_SCIENTIFIC_EVIDENCE_DESIGN.md` §4 (L251, L263) | "`observed_by` \| node → channel (generator, human:role, program, pretraining:model, public)". Ch(H) is defined "for an LLM-generated H" |
| `smart_beta/science/contracts.py` `Channel` (L107–L108) | "The channel through which an observation reached the research." |
| `smart_beta/science/adapters.py` `record_hypothesis_freeze` (L1488–L1525) | hard-codes `Channel.GENERATOR`. Its docstring says "`influenced_by` must name the `GENERATOR_INPUT` record of the event that produced the hypothesis (plan section 12.3)" |
| `smart_beta/science/adapters.py` `record_program_freeze` (L1526–L1562) | records the human act with `Channel.HUMAN` |
| `smart_beta/science/knowledge.py` (L138–L141, L1082–L1089) | `channel` is constrained only for `EXPOSURE_DECLARATION` (HUMAN / PRETRAINING / PUBLIC; the envelope equals the payload). No channel constraint exists for `HYPOTHESIS_FREEZE` |
| `smart_beta/science/roles.py` (L164, L506; L885–L891, L1224; L455, L621, L1031, L1233, L1283) | program-scope seeding treats GENERATOR, PROGRAM and HUMAN identically. Generator model-identity (PRETRAINING) obligations are keyed on `record.kind == GENERATOR_INPUT`. HUMAN / PUBLIC / PRETRAINING are queried only on `EXPOSURE_DECLARATION` records |
| `docs/phase10_confirmatory_evidence_certification.md` | no statement about channel semantics |

## 2. Review conclusions (Q1–Q10, compact)

| # | Question | Conclusion |
|---|---|---|
| Q1 | Normative meaning of `Channel` | The channel through which an observation or information reached the research. This is the design's `observed_by` edge (contracts.py; plan §5.2; SD L251) |
| Q2 | Why `record_hypothesis_freeze` uses GENERATOR | Documented: it is the §12.3 generator-origin adapter, bound by its docstring to the GENERATOR_INPUT / GenerationEvent that produced the hypothesis |
| Q3 | Must every `HYPOTHESIS_FREEZE` be GENERATOR? | No. No authority says so, and the validator does not constrain it |
| Q4 | Human vs generator distinguished at the record level? | Not explicitly. Only the generator-origin path is defined (§12.3; SD Ch(H) "for an LLM-generated H"). Pilot-1A Exp 1 was itself LLM-generated, so it is no human precedent |
| Q5 | An existing value for a human-precommitted hypothesis | `HUMAN`: the design's `human:role` channel, Phase-10's own use of HUMAN for human acts (`record_program_freeze`), and the same seed class as GENERATOR. No prior text named it for human freezes; this document supplies that clarification for Campaign-1 |
| Q6 | Where `channel` participates | record content hash YES; hash chain YES (via `record_hash`); validation only for `EXPOSURE_DECLARATION`; information-flow seeding YES, with GENERATOR = PROGRAM = HUMAN; confirmation eligibility only via declaration records; provenance claim YES (`observed_by`); replay identity YES (hashed) |
| Q7 | Does GENERATOR on a Campaign-1 freeze state something false? | Yes. It asserts arrival via the generator channel, and Campaign-1 `influenced_by` would not name a producing GENERATOR_INPUT, violating the helper's documented precondition |
| Q8 | Does changing the channel alter the firewall? | No. For `HYPOTHESIS_FREEZE`, GENERATOR and HUMAN are equivalent in the existing same-program seed semantics, and generator obligations key on the GENERATOR_INPUT record kind. Only the record hash changes |
| Q9 | Can the helper be used unchanged? | No (Q7) |
| Q10 | Smallest correction | This documentation-only clarification plus use of the existing public `KnowledgeLog.append`. Zero Phase-10 code change |

## 3. Campaign-1 rule (frozen)

For each Campaign-1 HUMAN-PRECOMMITTED hypothesis (C1-H1 … C1-H7):

- **Kind:** `kind = HYPOTHESIS_FREEZE`.
- **Channel:** `channel = HUMAN`.
- **API:** written with the existing public **`KnowledgeLog.append(...)`**.
  **`record_hypothesis_freeze(...)` is NOT used** for Campaign-1.
- **Payload:** exactly the existing Phase-10 §5.2 payload (`hypothesis_id`,
  `factor_spec_hash`, and `proposal_id` omitted because there is no Phase-9
  proposal). No new field.
- **`refs.influenced_by`:** the actual Campaign-1 upstream human-decision records,
  namely the Campaign-1 `ESTIMAND_POLICY` and `PROGRAM_FREEZE` record hashes. This
  matches the C1-LX §9 preflight, which requires both.
- **Unchanged Phase-10 rules:** reference rules (backward-only), hash-chain semantics
  and existing validation. No Campaign-only validator is introduced.

**This is not a universal rule.** It establishes the truthful provenance path for
Campaign-1 only. Future human-origin research programs must record their own actual
authority and provenance graph. They need not have exactly these `influenced_by`
records.

## 4. Information-flow statement (bounded)

For the existing same-program seed semantics (plan §5.3; roles.py L164/L506),
GENERATOR, PROGRAM and HUMAN are treated equivalently. Generator-specific declaration
obligations (PRETRAINING for generator model identities) are keyed from
`GENERATOR_INPUT` records (roles.py L885–L891, L1224), not from a `HYPOTHESIS_FREEZE`
carrying `Channel.GENERATOR`.

This clarification therefore changes truthful provenance and hash content. It does
**not** weaken the existing information-flow firewall. No broader claim is made.

## 5. Confirmation boundary

**Campaign-1 is not stated to be confirmation-eligible.** This clarification only
establishes a truthful registration path. Eligibility remains dependent on every
existing Phase-10 requirement and on the later registration and evidence state.

## 6. Synthetic probe (review evidence)

**SYNTHETIC VALIDATION ONLY. NOT a production Knowledge-PIT write. NOT Campaign
registration. NOT confirmation evidence.**

- **Procedure:** on a disposable log in the planner scratchpad (deleted afterwards), a
  `record_program_freeze` record (channel HUMAN) was followed by
  `KnowledgeLog.append(kind=HYPOTHESIS_FREEZE, channel=HUMAN, refs={"influenced_by": (<program-freeze hash>,)}, payload={hypothesis_id, factor_spec_hash})`.
- **Result:** the existing validator accepted it, and `read_records` read it back as
  (`HYPOTHESIS_FREEZE`, `HUMAN`). A GENERATOR-channel record with the same content was
  also accepted, with a different `record_hash`.

## 7. Explicit non-changes

| Item | Change |
|---|---|
| Phase-10 code | NONE |
| Phase-10 schema | NONE |
| `Channel` enum | NONE |
| Validators | NONE |
| Information-flow rules | NONE |
| Confirmation rules | NONE |
| Campaign hypotheses | NONE |
| Campaign precommitment | NONE |
| `SearchPolicy` | NONE |
| `family_id` | NONE |
| m | NONE |
| α | NONE |
| Statistical budget | NONE |
| Dataset-v1.2 | NONE |
| C1-LX | NONE |
| Empirical information used | NONE |

## 8. Next step (not executed here)

A separate **CAMPAIGN-1 REGISTRATION PLAN / TRANSACTION REVIEW** must precede any
production K-log append. It must fix:
- the exact ordered records (`ESTIMAND_POLICY` → `PROGRAM_FREEZE` → the seven
  `HYPOTHESIS_FREEZE`);
- identities and `influenced_by`;
- pre-write validation;
- expected sequence numbers and previous hashes;
- deterministic payloads;
- post-write verification;
- failure and partial-write handling.
