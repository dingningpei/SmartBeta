# P-Q v2.5 — Erratum E1: TD arithmetic and seam (precision; methodology unchanged)

- **Amends:** `docs/discovery_dataset_v1_2_pq_v2_5_terminal_td_design_draft.md` (frozen,
  SHA-256 `5af8c4bffed24062977b0442cfeaff388e2042eedd173f5300893b45252c4460`). That
  document is **not modified**, per the Campaign-3 lesson that errata to hash-bound
  documents are separate.
- **Issued:** 2026-10-14, before any implementation. The planner found this while
  specifying wave 1.

## E1.1 TD values are computed from D7b's r_obs, not from T0 outputs

- **What §3.1–§3.2 state:** TD is derived from the T0 value (`v^TD = (1 + v^T0)(1 + τ) − 1`)
  and from T0 label bounds. That is algebraically correct.
- **The floating-point problem:**
  - D7b forms `t0_value = (1.0 + r) * (1.0 + 0.0) - 1.0` (`delisting_bridge` :1737), which
    need not equal r bit for bit.
  - Re-associating the label product (`(1 + t0_lo)(1 + τ)` vs `(1 + ts_value)·lo`) can
    also differ in the last bit.
  - Either would break the §5.1 *identity* barrier at d = −1.
- **Binding rule:**
  - For every `ADMISSIBLE_TERMINAL` / `ADMISSIBLE_TERMINAL_EMPTY` member, compute
    `v^TD = (1.0 + r) * (1.0 + tau) - 1.0` with **exactly D7b's expression** (:1743),
    where r is D7b's own `r_obs` input (0.0 for `_EMPTY`).
  - The r_obs input is obtained through the same private D7b helpers that
    `pqv2_evaluation._reconstruct_b` already calls, import-only.
  - All other members take their T0 value unchanged.

## E1.2 The seam for L-TD (resolves the open point in §3.3)

- **The seam:** TD enters the frozen P-Q pipeline **in the TS slot**.
  - The frozen `pqv2_labels.label_envelope(..., t0_value=…, ts_value=v^TD)` produces the
    labels.
  - The frozen `pqv2_envelope.compose_series` consumes them.
  - Every output field named `*_ts` / `TS` in that call is TD.
- **Why the seam works:**
  - no frozen file is modified;
  - the labels and the Prop-9 invariant are computed by the frozen code itself;
  - at d = −1 the arithmetic is identical to TS by construction (E1.1).
- **Not adopted:** the label-transform route in §3.2. It stays a valid derivation, but it
  is not the implementation.

## E1.3 Authority verification cannot reuse the frozen dispatcher alone

- **The limitation:** `pqv2_core.verify_authority` and `identity_lines` dispatch on object
  identity (`authority is AUTHORITY_V2_4`, …), so a new `AUTHORITY_V2_5` object is not
  recognized.
- **What v2.5 does instead:** it supplies `verify_authority_v2_5`, which:
  - (a) hashes the v2.5 design document, this erratum, and the base v2.4 document;
  - (b) calls the frozen `verify_authority(root, AUTHORITY_V2_4)`, which covers the v2.4 →
    v2.1 chain and `PINNED_CODE`;
  - (c) takes CS from `identity_lines(root, AUTHORITY_V2_4)` and ED/CE from the v2.5
    document via the frozen `_extract_code_block_after`, then compares their SHA-256
    values with `AUTHORITY_V2_5`;
  - (d) checks the C3-bound file lists against a frozen literal hash table.
- **Still to be decided:** `pqv2_core.require_admitted_authority` does not admit v2.5.
  Campaign-4 admission checks are therefore specified in a later wave (PQ25-H or the
  Campaign-4 wrappers), not by modifying `pqv2_core`.
