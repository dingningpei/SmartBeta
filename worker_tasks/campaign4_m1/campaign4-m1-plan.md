# Campaign-4 M1 — wave plan: generator contract (M1-a) + walk-forward harness (M1-e) (DRAFT; not frozen)

## Authority

- `docs/campaign4_m1a_generator_contract_design_draft.md` (Q1–Q4, user 2026-10-15);
- `docs/campaign4_m1a_contract_calibration_draft.md` (Q5–Q8, pending);
- `docs/discovery_dataset_v1_2_pq_v2_5_admission.md` (v2.5 is the terminal-stress authority;
  the C4 wrappers enforce the admission);
- `docs/campaign4_e/desktop_usage_policy.md` (cited results come from the laptop).

## Binding constraints (every task)

1. **IS only.** Nothing opens an OOS, DEVELOPMENT_SEALED, HOLDOUT or reserve path. Every
   entry point asserts the IS fold before building a panel.
2. **Training-window firewall.** Anything shown to the generator, used by the A4 gate or
   used for selection is computed from periods dated before the window cut. A renderer or
   gate given a later period raises.
3. **No modification of** `smart_beta/campaign/campaign3_*.py`,
   `smart_beta/research/{generator,llm_generator,llm_generator_deepseek,policy}.py`,
   `smart_beta/research/prompts/campaign3_v*.txt`, `smart_beta/dataset_v1/pqv2_*.py`,
   `smart_beta/dataset_v1/pqv25/**`, `smart_beta/diagnostics/**`, or any sealed file. They
   are import-only.
4. **v2.5 admission enforced:** `verify_authority_v2_5()` is called before any TD build,
   with d = −0.55 only.
5. **No K-PIT write.** The executor runs with `kpit=None`, and nothing appends to any
   knowledge log.
6. **No network in tests.** Tests use a fixed in-process generator. Only the planner's
   real runs call DeepSeek.
7. **Determinism.** Given the same generator responses, outputs are byte-identical.

## Tasks and DAG

| Task | Owns | Depends on |
|---|---|---|
| C4M1-1 contract core | `smart_beta/campaign4/{__init__,contract,diversity,ranking,feedback}.py`, `smart_beta/research/prompts/campaign4_v1.txt`, `tests/test_c4m1_contract_core.py`, `tests/fixtures/c4m1/core/**` | master at freeze |
| C4M1-2 executor + walk-forward harness | `smart_beta/campaign4/{generation,walkforward,deepseek}.py`, `scripts/run_c4_walkforward.py`, `tests/test_c4m1_walkforward.py`, `tests/fixtures/c4m1/walkforward/**` | C4M1-1 merged |

The two tasks run sequentially. One Pi worker per task via Herdr, each in its own worktree.

## After both merges (planner)

1. Run the barrier.
2. Do a synthetic dry run of `run_c4_walkforward.py` with the fixed generator.
3. **STOP** for the live-run authorization.
4. After authorization, run the four live DeepSeek runs on the laptop: K-PIT copy, scratch
   run root, production K-PIT hash checked before and after.
5. Write `docs/campaign4_m1e_result.md`, with the pre-declared descriptive reading and the
   trial accounting (all 400 proposals plus any desktop exploration).
