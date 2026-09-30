# Discovery Dataset v1.1 — D7c Clarification Addendum: Post-Load Assertion Scope (NORMATIVE)

- **Status:** normative wording repair, decided by the user on 2026-09-29 (D7C
  IMPLEMENTATION HOLD resolution, Option A). It amends
  `docs/discovery_dataset_v1_1_d7c_clarification.md` (839d4b0;
  `508d5f1ac3da3c68233c72b4b31ea75d0ec1d88be9241e6dd018cd54467b4a89`) and
  `worker_tasks/dataset_v1/task-d7c-trustedinput-bridge.md` §2 (c48cc4c;
  `0376d82b081967af4694417db9a81c3d9dcb33c933a4af08b57676a228a3e451`).
- **What it changes:** it narrows the spec §2 sentence "Post-load assertion:
  every loaded date is < 2023-07-03" exactly as stated below.
- **What it does not change:**
  - D7c's scientific semantics, gate 13 or P1c;
  - D-SC's known-at-the-time semantics or the D8 reserve boundary;
  - TrustedInput or TRI semantics, Phase 6, or Campaign governance.

## A1. The post-load reserve assertion covers development market-data rows only

No row consumed by D7c from these sources may have a date on or after
2023-07-03:

| Source | Date columns asserted |
|---|---|
| D3 `observations` (development) | `date` |
| D3 `ca_events` (development) | `effective_date`, `knowledge_date` |
| D5 `day_flags` (development) | `date` |

Any stricter existing cutoff still applies, including the D3 accessor read
bound `end ≤ 2023-06-29`.

## A2. D-SC timelines: the boundary is enforced when queried

- **Loaded whole:** the D-SC `class_timeline` and `policy_timeline` are loaded
  as the existing complete structural artifacts. Records or segments dated on
  or after 2023-07-03 are **not** in themselves a D7c reserve violation.
- **Not allowed:** truncating the artifact, rewriting segments, making a
  development-only copy, discarding segments that straddle the boundary, or
  reinterpreting the D-SC certification.
- **At query time:** for every Phase-6 row d, the D-SC cutoff is `prev(d)`.
  - For the final row d = 2023-06-30, the cutoff is 2023-06-29.
  - **Every `class_state_at` and `policy_state_at` cutoff must be ≤
    2023-06-29.** A later cutoff fails closed with `ReserveFirewallError`.

## A3. The distinction

"The artifact contains later structural records" is **not** the same as "the
query consumes later knowledge". D7c's certification depends only on the
second.

No D-SC state or evidence whose knowledge falls after the permitted cutoff may
affect an earlier D7c row. The future-classification invariance barrier
(spec tests 34, 35, 36 and 38) remains mandatory and must not be weakened.

## A4. Required tests (added inside the existing items; no renumbering)

- **(a)** A D-SC timeline containing segments after 2023-07-03 loads without
  error.
- **(b)** A D-SC query with a cutoff after 2023-06-29 fails closed.
- **(c)** The existing future-classification invariance still holds.
