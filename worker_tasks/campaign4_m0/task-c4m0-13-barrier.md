# Campaign-4 M0 — C4M0-13 merge barrier record

- **Merged:** worker commit `1e00f30` (exact-SHA independent review, 17 task tests re-run) as
  merge `1cdd750` (`--no-ff`) on base `ef4a090`. Erratum E1 and the blind coding file:
  `cc85578`.
- **First barrier run (2026-10-15):** 6887 passed, 1 skipped, **2 failed**. Both failures are
  `tests/test_dataset_v1_reserve_artifact.py::test_item53_available_from_from_evidence_only`
  (directly, and via the `item12` inner suite). The sealed test asserts that its fixture
  date `2026-10-15` differs from the run date; it fails only on that calendar day.
  - Pre-existing: it fails identically on pre-merge `ef4a090`.
  - Neither the test nor its module is touched by the merge.
  - Disposition: an explained latent date-coincidence defect in a sealed test. **Not
    modified**; any fix needs separate authorization.
- **Re-run (2026-10-16, master `cc85578`):** **6889 passed, 1 skipped, 0 failed** (837 s).
- **Barrier: PASS.**

Co-recorded by the planner.
