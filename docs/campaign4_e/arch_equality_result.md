# Campaign-4 wave E — C4E-5 cross-architecture equality result

**Disposition: GATE FAIL (desktop).** Per the frozen gate rule
(`worker_tasks/campaign4_e/task-c4e-5-arch-probe.md`), the desktop may **not** run real-data
campaign steps. Nothing was toleranced, dropped or patched. The remedy is a new user decision.

## Run identity

| Item | Value |
|---|---|
| Code | C4E-5 `72d2cc9`, merged as `960fa55`; barrier 6872 passed, 1 skipped (exit 0) |
| Reference | `tests/fixtures/arch_equality/reference_laptop.json` (arm64 macOS 27.0, Accelerate) |
| Desktop | WSL2 Ubuntu 24.04.5, i7-10700K x86_64 AVX2, OpenBLAS 0.3.34; Python 3.12.3; numpy 2.5.3 / pandas 3.0.5 / scipy 1.18.1 (identical library versions) |
| Desktop repo | fast-forwarded `c6c6257` → `960fa55`; its untracked `docs/campaign3_discovery_program_plan_draft.md` (byte-identical to the now-tracked file, blob `c202a264`) moved to `~/smart_beta/backup_untracked_20261015/` |
| Invocation | network guard (`sitecustomize.py`, same SHA-256 `6697069a…` as the laptop), credentials unset, `PYTHONPATH=guard:repo` (the desktop venv has no editable install of `smart_beta`) |

## Item result (`--check`, exit 3)

| Item | Desktop vs reference |
|---|---|
| J-panel (C3-L-R6 fast panel) | **equal** |
| P-event (EVENT_SIMPLE diagnostics) | **equal** |
| K-numpy | differs |
| K-pandas | differs |
| S-ewc | differs |
| S-hac | differs |
| P-pqv2 | differs |
| P-pqv25 | differs |

`tests/test_arch_equality_probe.py` on the desktop: 5 passed, 2 failed (the two reference-equality tests), as the gate requires.

## Diagnostic localization (planner, read-only, scratch only)

All differences are float last-bit effects; integers, strings, periods, statuses, the
sort/assignment and the panel are identical.

| Leaf | Differing | Max ULP | Max rel |
|---|---|---|---|
| K-numpy `inv` / `lstsq` / `dot` (BLAS) | 4058/4096, 64/64, 1/1 | 49150 / 1800 / 5 | 7e-12 / 3e-13 / 8e-16 |
| K-pandas `rolling_std` | 134/240 | 3 | 6e-16 |
| S-ewc `upper_bound_theta_prime` (n = 54, 240) | 2/3 series | 1 | 1e-16 |
| S-hac `long_short_mean_tstat` t | 3/3 | 120 | 2e-14 |
| S-hac nonoverlap / gap-aware | 4 leaves | 3 | 4e-16 |
| P-pqv2 cell `t_stat` (→ `cell_hash`) | 2/4 cells | 1 | 2e-16 |
| P-pqv25 TD cell `t_stat` (→ `cell_hash`) | 1/2 cells | 1 | 2e-16 |

**Remedy experiment (diagnostic only):** `OPENBLAS_NUM_THREADS=1` alone changes nothing.
Adding `OPENBLAS_CORETYPE=Prescott` and disabling numpy's x86 SIMD features makes P-pqv2 and
P-pqv25 equal, but K-numpy, K-pandas, S-ewc and S-hac still differ (consistent with libm /
compiler FMA differences, which environment settings cannot remove). Bitwise cross-architecture
equality is therefore not achievable by configuration.

## Consequence

Values agree to ~1e-14 relative, but every content hash that covers a t-statistic (cell
hashes, diagnostics hashes, K-PIT-bound digests) differs, so desktop-produced artifacts
cannot be identity-checked against laptop ones. Real-data campaign steps stay on the laptop
until the user decides otherwise.
