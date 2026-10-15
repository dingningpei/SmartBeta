# Campaign-4 wave E — desktop usage policy (option A) (FROZEN 2026-10-15)

Separate addendum to `worker_tasks/campaign4_e/campaign4-e-plan.md` and the frozen
`task-c4e-5-arch-probe.md`, neither of which is edited. Basis:
`docs/campaign4_e/arch_equality_result.md` (desktop gate FAIL, last-bit float differences).

## Rule

The desktop is an **exploration-only** compute tier. The laptop remains the only machine
whose outputs are evidence.

| | Desktop (exploration) | Laptop (record) |
|---|---|---|
| Gate status | FAIL (unchanged; never toleranced) | reference |
| May run | IS-fold computations: candidate generation, panels, diagnostics, screening statistics | everything currently authorized |
| May not | write the production K-PIT; read or evaluate the reserve or the DEVELOPMENT_SEALED fold; produce any record, digest or hash that a campaign decision, K-PIT entry or certification cites | — |
| Outputs | a **shortlist of candidate identities** (factor specs / generator inputs), plus an exploration log | every cited number, hash and record, recomputed from scratch |

## Binding conditions

1. **Recompute, never import.** A desktop-shortlisted candidate enters any recorded step only
   by full recomputation on the laptop from its spec. No desktop number, cache, export or
   hash is copied into a laptop run root, a document's evidence table or the K-PIT.
2. **Trial accounting.** Desktop exploration is selection. Every recorded campaign step that
   uses a desktop shortlist must declare the full exploration count (the number of distinct
   candidates evaluated on the desktop, from the exploration log) and count it in that step's
   multiplicity / trial accounting. A shortlist without its log cannot be used.
3. **Same code.** The desktop runs at a master SHA that exists on `origin`, recorded in the
   exploration log; no uncommitted code.
4. **Isolation.** The network guard (`planner_tools/guard/sitecustomize.py`, SHA-256
   `6697069a…`, verified before each session), credentials unset, `PYTHONPATH=guard:repo`;
   a K-PIT **copy** plus a scratch run root, with the desktop's production K-PIT SHA-256
   checked before and after. Raw data never leaves `~/smart_beta/data_store`.
5. **Labelling.** Anything reported from the desktop is labelled EXPLORATORY (DESKTOP), never
   IS evidence.

## Re-opening

Running record steps on the desktop requires a new gate PASS (e.g. after a change of
hardware or numeric stack) or a new user decision. This policy does not relax the gate.

## Freeze (user, 2026-10-15: "按照你的建议，选 A")
