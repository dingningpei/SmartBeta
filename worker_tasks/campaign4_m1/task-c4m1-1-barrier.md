# C4M1-1 — merge barrier record

- **Review:** worker commits `2eec225` → `18c6215` (E1 R1–R3) → `e7c8831` (R4) →
  `8f21a73` (docstring), all on base `49a19e2`. Each was reviewed at its exact SHA. The 40
  task tests were re-run.
- **Planner real-data check** (generator-visible C3 data, read-only): with the cut at
  2020-07-31, `control_metrics` reproduces C3's persisted history metrics for 99 of 99
  experiments, **bitwise** in both value and `n_obs`.
- **Protected paths:** the diff against `49a19e2` touches only the 7 owned files.
- **Merge:** `aae0e61` (`--no-ff`).
- **Barrier (2026-10-16):** 6929 passed, 1 skipped, 0 failed (845 s). **PASS.**
