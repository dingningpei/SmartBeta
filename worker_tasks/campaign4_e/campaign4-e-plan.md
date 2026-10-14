# Campaign-4 wave E — engineering plan (DRAFT for freeze; not authorized for launch)

- **Authority:** `docs/campaign4_program_plan_draft.md` §4, decision S3 (user, 2026-10-14).
- **Base:** master `09296ef` (re-verified at launch; CLAUDE.md §4.1).
- **Scope change from the program draft (planner finding, 2026-10-14; please confirm at
  freeze):**
  - **E1–E3 are deferred, not specified.**
    - **E3 cannot touch the bound file.** `build_event_inputs` lives in
      `smart_beta/dataset_v1/pqv2_events.py`. That file is in
      `campaign3_run.PQ_PINNED_FILES`, whose SHA-256 the Campaign-3 CAMPAIGN record
      (attempt 4) binds. Editing it would break re-verification of that record, and of
      any later T-C on `ed358470`.
    - **E1/E2 would only speed up closed code.** They target `campaign3_forward.py` and
      the C3 export path. Those files are not hash-bound, but Campaign-3 is closed, so
      the speed-up would apply only to a campaign that will not run again.
    - **Where they go instead:** under S1 = C the next campaign's code (on Dataset v2 or
      v1.2) is not yet designed. E1–E3 become **design requirements** of that campaign's
      implementation spec: parallel forwarding, export-time panel/claim hashes, and a
      vectorized event-input builder as a **new** module with bit-identical equivalence
      (the C3-L-R6 pattern).
  - **Wave E is therefore E5 only.** E4 is closed by measurement, with no code change.
- **Binding constraints:**
  - **Untouched files:** no task touches a file in `pqv2_core.PINNED_CODE`,
    `campaign3_run.PQ_PINNED_FILES`, `CAMPAIGN3_CODE_FILES` or `RESERVE_CHAIN_FILES`, nor
    any sealed-phase module.
  - **No data access:** no real reserve read, no network, credentials scrubbed
    (`planner_tools/scrubrun.sh`).
  - **No weakening:** no test is deleted, loosened or made conditional in a way that
    removes it from **every** barrier run.

## E4 — closed without code (planner finding, 2026-10-14)

- **The measurement** is in `docs/campaign4_e/suite_timing.md`.
- **The "900 s bound" is item 12's subprocess timeout**, which applies to the *inner*
  suite (about 420 s). The outer wall time includes that inner re-run, so the headroom is
  about 2.1×, not 10%.
- **No test is moved or marked.** The sealed Phase-5A test is untouched.
- **Routine pre-merge checks** may deselect item 12. **Barriers** run the full suite.
- **Watch threshold:** an inner run above 750 s.

## E5 — cross-architecture equality gate (design input needed)

- **Problem:** full real-data runs on the desktop need proof that numeric outputs equal
  the laptop's (arm64 / macOS, Python 3.12.5, numpy 2.5.3, pandas 3.0.5).
- **Deliverable:**
  - a deterministic **equality probe**: `scripts/arch_equality_probe.py` (new) runs a
    fixed set of pure computations on committed synthetic fixtures only and writes a
    canonical JSON of content hashes. The set covers the P-Q envelope, the portfolio
    sort, the HAC/EWC t-statistics and one C3-L-R6-style panel join;
  - a test that compares the probe output against a committed laptop reference.
  - **Gate:** a machine may run real-data campaign steps only if its probe output equals
    the reference, **bit for bit**. Any difference is a STOP: report it, never tolerance
    it away.
- **Open input (needed before freeze):** the desktop's CPU architecture, OS, and Python
  / numpy / pandas versions, and how the planner reaches it. Without these, E5 cannot be
  frozen.
- **Owned files:** `scripts/arch_equality_probe.py`, `tests/test_arch_equality_probe.py`,
  `tests/fixtures/arch_equality/**` (all new).

## DAG and launch

- E5 runs as one Pi worker via Herdr (CLAUDE.md §2), on worktree
  `worktrees/task-c4e-5-arch-probe`.
- Review, merge and barrier follow CLAUDE.md §4.
- E5 is frozen only once the desktop facts are supplied.
