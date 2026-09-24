# Archived Pilot-1A configurations (evidence only)

## `pilot1a-real-v2.anthropic.NOT-AUTHORIZED.json`

| Field | Value |
|---|---|
| Authorized for execution | **NO** |
| Model calls | **0** |
| Experiments | **0** |
| Holdout consumption | **0** |
| Run directory ever created | **NO** |
| Provider / model | Anthropic / `claude-opus-5-5` |
| Bound commit | `cf1092f2cbacd82db6ee1819f200ebf502871cae` |
| Config canonical hash | `74504a9a3a67f369992d8bc06d31a2f790562fce84ecf5d58a93d5b10158badb` |
| File SHA-256 | `c44047510e43489cf943c93073e4440371e2137c2380296869529692e9ad7dbe` |

This config was generated at Barrier PA (2026-09-24) and passed the side-effect-free
preflight only. It was **never approved and never executed**. The user then changed
the Pilot-1A generator decision to DeepSeek (`worker_tasks/pilot1/pilot1-plan.md`
§26f). The file is kept byte-identical as implementation evidence.

**It is not, and must never be classified as, a historical real Pilot-1A run.** It
has no run directory and no journal under `pilot_runs/`, so the single-run guard
(which classifies runs only from authenticated journals) never sees it. Its bound
commit also no longer equals HEAD, so preflight refuses it.
