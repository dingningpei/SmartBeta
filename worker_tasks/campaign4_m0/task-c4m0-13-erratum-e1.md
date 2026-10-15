# C4M0-13 — erratum E1 (input path) (APPROVED 2026-10-15)

Separate erratum to the frozen `worker_tasks/campaign4_m0/task-c4m0-13-direction-exposure.md`,
which is not edited.

## Defect (specification, not implementation)

The spec's Inputs table names
`data_store/campaign3/76a2c212…/generation_state/executor_state.json` as the proposal source.
That file holds 26 proposals from a later stage, and none of them is among the 99 candidates.
The worker found this read-only, and the planner confirmed it independently.

## Correction

The proposal source is
`data_store/campaign3/d6c34dbc5919bb6c63bae410055aff066eb2e9c966c6f6fca31576713202e83d/generation_state/executor_state.json`.
Its proposals intersect the 99 candidates of `d6c34dbc…/generation.json` at 99 of 99, in the
same order. Every other input is unchanged; in particular the screen cache stays at
`76a2c212…/screen_cache/`.

## Effect

- **Methodology:** none. The script takes the path as an argument, so no code changes.
- **Failure mode:** `load_candidates` fails closed on any proposal that is absent or whose
  spec hash does not match.

## Approval (user, 2026-10-15: "好的，继续")
