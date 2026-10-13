# Campaign-3 P5 — Erratum P5-E2 (separate amendment document)

- Amends `docs/campaign3_p5_precommitment.md` (bound SHA-256 `95093b99692a4c52f33b96830dbe2a7064cf1c7df06646e705a765acdb791288`, including E1), which is **not modified**. This erratum was first appended to that file in `9ab0c9a`, which broke its binding hash; it was moved here and the P5 file was restored to its bound content.

## P5-E2 (APPROVED 2026-10-13, user: "A") — T-B infeasible; forward the IS top-1 to T-C

**Evidence** (attempt 4, after TB_SELECTION at K-PIT seq 159):
- **The holdout fold cannot be evaluated:** D7b (`delisting_bridge._domain_reason`, a
  P-Q pinned file) returns `NOT_ASSESSED_FINAL_HOLDOUT_OVERLAP` for **any** fold whose
  partition role is `holdout`. The P4/P5 "DEVELOPMENT_SEALED" T-B fold **is** the frozen
  partition's (`b4b56dbf`) holdout fold, sealed since Campaign-1. Every holdout-fold
  T-B cell is NOT_ASSESSED, with no values computed. T-B as designed (OOS 18 +
  DEVELOPMENT_SEALED 18 = 36) was therefore infeasible from the start. This is a
  planner design defect: the D7b holdout rule was never checked.
- **OOS alone cannot be tested:** OOS has 18 periods, and the admitted
  `EWC_T23_ONE_SIDED` requires T ≥ 24, so every OOS-only test is INVALID.
- **No result was seen:** neither the user nor the planner has seen any IS or T-B
  statistic. The rule change below is decided blind to outcomes.

**Correction (binding; it supersedes D8 and D10):**

1. **T-B is dropped.**
   - TB_SELECTION (seq 159) stays in K-PIT as a historical record.
   - The partial OOS T-B diagnostics of the first selected candidate are **not used**
     for any decision, and are reported as such.
2. **Forwarding (replaces D10):**
   - the forwarded hypothesis is **rank 1 of the D9 IS ranking table** (eligible =
     ASSESSED + CERTIFIED with an L envelope assessed in every scenario; ordered by the
     minimum over scenarios of the L-envelope EWC t in the declared sign; ties broken by
     canonical key), with the control never eligible;
   - no eligible candidate → **NO_RESERVE_USE**.
   - The D9 table is recomputed with the unchanged C3-S `rank_candidates` over the
     persisted attempt-4 exports, and its `content_hash` **must equal**
     `ranking_table_hash` in TB_SELECTION (seq 159). Otherwise STOP.
3. **The D8 floor is removed.** There is no floor, because no valid T-B statistic
   exists.
4. **Confirmatory error control is unchanged:** T-C, m = 1, one-sided α = 0.05 with
   `EWC_T23_ONE_SIDED`, B **and** L required under all T0/TS × K0/K1 combinations (P7
   decision 4). The IS data only selects; the reserve never influenced the selection.
5. **The power statement is restated:** for a true annual SR of 1.0 the T-C power is
   about 0.50, given that the forwarded candidate is that factor (§2). IS selection is
   search-inflated, so the probability that the IS top-1 is a true strong factor is
   unknown and plausibly low. The report must say this verbatim.
6. **The report** contains:
   - the full IS ranking table (descriptive; IS is the generator-visible tier);
   - the forwarded id or NO_RESERVE_USE;
   - the T-B infeasibility disclosure;
   - attempts 1–3 (RUN_INCOMPLETE);
   - the D12 break-even, which is unavailable without T-B and is reported as such.
7. **T-C still requires a separate user authorization.**
