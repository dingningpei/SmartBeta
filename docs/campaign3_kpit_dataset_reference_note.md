# Campaign-3 — K-PIT reference to Dataset v1.2 (governance note; user-approved 2026-10-10: "只用哈希引用 + 写明解释")

## Facts

- The Dataset v1.2 R3 identity rule (`docs/discovery_dataset_v1_2_development_admission.md`
  §7) says that v1.2 records are `NONE_AUTHORIZED`: no Knowledge-PIT record is authorized
  for the v1.2 development artifacts or for their admission. It adds: "Any future
  registration follows the separately governed Phase-10 rules."
- `quarantine.verify_dataset_v1_2_identity` (a P-Q pinned file) enforces this rule. It
  fails if **any** K-PIT record's canonical JSON contains the literal dataset id
  `discovery_dataset_v1_2`.
- The C3-R CAMPAIGN precommitment payload (as of `db51551`) embedded
  `bindings.to_content()`, which includes that literal. Appending it to the
  production K-PIT would permanently break every v1.2 identity check: P-Q evaluation,
  and the Campaign-1/2 runners. K-PIT is append-only, so this could never be undone. The
  latent Campaign-2 registration code has the same property, but it was never
  executed.
- A planner dry-run check found **no** occurrence of the literal in the GENERATOR records
  (`GENERATOR_INPUT`, `HYPOTHESIS_FREEZE`).

## Interpretation (binding for Campaign-3)

1. **What R3 forbids:** K-PIT records that **register or describe Dataset v1.2 artifacts
   themselves** (data registration). That is what the literal-id scan guards.
2. **What is permitted:** a Campaign precommitment, governed by the Phase-10 rules, that
   **binds** the dataset it will consume. The reference is the **dataset identity
   hash** `a065493d295883f425d499816a98ac55a03cd67a1086bd7b64249b90fe76b9da`, together
   with the overlay, D7c and event-layer hashes. The binding is cryptographic: any
   other dataset fails it.
3. **Required in every Campaign-3 K-PIT record** (CAMPAIGN, the control
   HYPOTHESIS_FREEZE, GENERATOR_INPUT, HYPOTHESIS_FREEZE, TB_SELECTION, and any later
   ACCESS):
   - the literal `discovery_dataset_v1_2` must not appear anywhere;
   - the dataset is referenced by its identity hash only.
4. **No frozen check is modified.** `verify_dataset_v1_2_identity` stays exactly as
   pinned.
5. **Before the first production append**, the planner simulates the complete record
   sequence on a **copy** of the production K-PIT and runs
   `verify_dataset_v1_2_identity` against it. It must pass.

## Disclosure

The literal scan cannot tell "binds by hash" apart from "does not reference". This note
states that hash binding is a deliberate, permitted reference, so that no reader mistakes
it for evasion. Any future change to R3 itself needs its own amendment.
