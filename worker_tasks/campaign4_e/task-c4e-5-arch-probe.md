# Campaign-4 wave E — task C4E-5 (cross-architecture equality probe) (FROZEN 2026-10-14; launch authorized)

## Authority

- `worker_tasks/campaign4_e/campaign4-e-plan.md`, section E5, including the desktop facts table;
- the binding constraints in that plan (untouched bound/sealed files, no data access, no weakening);
- base: master at freeze.

## Purpose

Real-data campaign runs may move to the desktop (WSL2, x86_64, OpenBLAS) only if its numeric
outputs equal the laptop's (arm64, Accelerate) **bit for bit**. C4E-5 builds the probe and the
gate. It does not decide where anything runs.

## Content (owned, all new)

### 1. `scripts/arch_equality_probe.py`

Runs a fixed, ordered list of **probe items** on committed synthetic fixtures only (no
`data_store`, no network, no K-PIT) and writes a canonical JSON:

```
{"schema": "arch-equality-probe-v1",
 "items": {"<item id>": "<sha256 of the item's canonical output>", ...},
 "environment": {...}}            # descriptive only, never compared
```

**Canonical output encoding:** floats as the hex of `struct.pack('>d', x)` (so `-0.0 ≠ 0.0`
and every NaN payload is visible), ints and strings as-is, arrays/frames element-wise in
row-major order with dtype and shape included, keys sorted, `separators=(',', ':')`.

**Probe items** (each isolated, so a mismatch localizes to one item):

| Id | Content |
|---|---|
| `K-numpy` | seeded (`numpy.random.default_rng(20261014)`) kernels: `sum`, `cumsum`, `mean`, `std(ddof=1)`, `dot`, `matmul` (64×64), `linalg.lstsq`, `linalg.inv`; `math.fsum` |
| `K-pandas` | seeded frame: `groupby().mean()`, `rank(method="average")`, `rolling(12).std()`, `qcut` |
| `S-ewc` | the frozen `EWCT23OneSided` on three fixed seeded series (n = 54, 120, 240): t, p, df, every result field |
| `S-hac` | every other t-statistic inference procedure in `smart_beta/science/procedures/` that a Campaign-3/4 decision uses (the worker lists them), on the same series |
| `P-pqv2` | the frozen `build_pqv2_diagnostics` on the PQ2-H / C3 parity fixture used by `tests/test_pqv2_evaluation.py`: every cell's value, t_stat, n_obs, status, reason and period arrays (this covers the portfolio sort and the P-Q envelope) |
| `P-pqv25` | `build_pqv25_diagnostics` at d = −0.55 on the same fixture, TD cells only |
| `P-event` | the same as `P-pqv2` for one EVENT_SIMPLE fixture (the one PQ25-H used) |
| `J-panel` | the C3-L-R6 fast panel (`campaign3_launch` `fast_panel_for`) on the synthetic fixture its existing tests use: the full panel frame |

**CLI:** `--out PATH`, and `--check REFERENCE` which compares `items` with the reference and
exits **0** if every item hash is equal, **3** otherwise, printing every differing item id.
There is no tolerance option.

### 2. `tests/fixtures/arch_equality/reference_laptop.json`

The probe output generated on this laptop (arm64 / macOS, the main `.venv`), committed.

### 3. `tests/test_arch_equality_probe.py`

1. The probe output on the running machine equals `reference_laptop.json` item for item.
   On the laptop this is a regression check; on the desktop it **is** the gate.
2. Determinism: two in-process runs give byte-identical JSON.
3. Encoding: `-0.0` vs `0.0` and two distinct NaN payloads produce different item hashes.
4. `--check` returns 3 and names the item when one reference hash is altered.
5. The probe reads no path under `data_store` (assert via the fixture paths it opens or an
   equivalent guard).

## Gate rule (frozen)

A machine may run real-data campaign steps only if `scripts/arch_equality_probe.py --check
tests/fixtures/arch_equality/reference_laptop.json` exits 0 on it, at the same master SHA as
the reference. Any differing item is a **STOP**: it is reported with its item id. It is never
toleranced away, and no item is dropped to pass.

## Execution after merge (planner)

1. `git pull` the desktop repo (`~/smart_beta/main`) to the merged master; its untracked draft
   doc is left untouched. Sync the desktop venv only if `pyproject` requires it.
2. Run the probe check and the test file on the desktop via SSH, credentials scrubbed.
3. Record the outcome (PASS, or the list of differing items) in
   `docs/campaign4_e/arch_equality_result.md`. On a difference: STOP and report; the remedy
   (e.g. a pinned BLAS / thread setting, or laptop-only for those paths) is a new design
   decision for the user.

## Owned files (all new)

- `scripts/arch_equality_probe.py`
- `tests/test_arch_equality_probe.py`
- `tests/fixtures/arch_equality/**`

## Launch

- One Pi worker via Herdr, worktree `worktrees/task-c4e-5-arch-probe`.
- **Stop rule:** if a named frozen function or fixture is missing, or a probe item cannot be
  run without real data, STOP and report. Do not substitute.

## Freeze (user, 2026-10-14: "好的， 按照你的建议")

This spec is frozen, including the gate rule. The C4E-5 Pi launch is authorized, as are the
planner's desktop steps after merge.
