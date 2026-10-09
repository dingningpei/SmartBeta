# PQ22-E2 — two-tier grammar cross-check (FROZEN 2026-10-09; user-approved option (a))

- **Defect:** the PQ22-H cross-check (plan §4) runs at least 2,000 brute-force grammar
  members per class in the default suite. That takes about 14.5 minutes (8,005 tests,
  871 s). The full suite then exceeds the 900 s nested-run timeout of the **sealed**
  Phase-5A test `tests/test_phase5a_certification.py::test_item12_full_regression`, so
  it fails deterministically: 1 failed / 13,825 passed at `debee06`. The sealed test is
  not modified.
- **Correction (it amends plan §4 and §6; no evidence requirement is weakened):**
  1. **Default suite** (`tests/test_pqv2_grammar_crosscheck.py` without an environment
     flag):
     - a deterministic **fixed subset**: the first 50 members per class of
       `generate_population(seed=20261008)`;
     - plus the full adversarial set;
     - plus every non-population test.

     Target runtime ≤ 60 s.
  2. **Full tier:** with `PQV2_FULL_CROSSCHECK=1` the full population (≥ 2,000 per class)
     runs exactly as before.
  3. **The barrier runs the full tier explicitly.** The I3/I6 v2.2 recertification
     evidence must come from a full-tier run, recorded with its counts and runtime.
  4. **Gating test:** a test asserts that the subset is the frozen prefix (seed and
     indices), so the default tier cannot drift.
- **Owner:** PQ22-H-R2, same owned files as PQ22-H. New mutation H22-M3: the full tier
  silently ignores the flag (it runs the subset) — must be killed by a flag-aware test.
