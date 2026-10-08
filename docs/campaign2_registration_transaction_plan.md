# Campaign-2 Knowledge-PIT registration transaction plan (FROZEN 2026-10-08; PRODUCTION WRITE NOT AUTHORIZED)

- **Base:** `docs/campaign1_registration_transaction_plan.md` (v2) applies **mutatis
  mutandis**: the A / B / C phases, append-time attestation, partial-write recovery, and
  synthetic validation on disposable copies.
- **Records (exactly 9, in order):**
  - `ESTIMAND_POLICY` (C2 estimand policy: B and L per fold and scenario,
    `C2-DEV-SCREEN-v1`);
  - `PROGRAM_FREEZE` (`campaign2_id`, the precommitment SHA, the P-Q v2.1 identities);
  - 7 × `HYPOTHESIS_FREEZE` (`campaign2_hypothesis_id` for C1-H1…H7, with the definition
    hashes).
- **Pre-write:**
  - all A-phase values are computed by the merged C2-LX code from frozen inputs;
  - a synthetic dry run is done on a disposable copy of the production log;
  - the current K-PIT terminal state is verified (seq 9 `13d04ef4…d262`, file
    `190d53f0…f6a2`) as the append base.
- **Post-write:**
  - the 9 new records verify;
  - the preflight passes;
  - the Campaign-1 records are unchanged.
- **This plan writes nothing.** The production append needs its own explicit
  authorization.
