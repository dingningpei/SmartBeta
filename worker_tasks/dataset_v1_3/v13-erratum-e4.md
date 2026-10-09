# V13-E4 — E-4 build path: seed separation and header-time wiring; E-3 fallback removal (FROZEN 2026-10-09; user-approved)

- **Defect class:** implementation defects found by the planner on the production build
  path before ADM-13 executed. A contributing specification gap: V13-E2 §5 stated "E-4:
  no change expected"; it is overridden here.
  1. **Seed conflation:** `edgar_build.build(seed=1304)` passes the EV-T seed into
     `_derive_identity_rows → run_im_p(seed=seed)`, so the IM-P sample uses 1304 instead
     of the frozen **1302** (V13-E1). The L2 evidence was fetched for the 1302 sample.
  2. **Header times not wired:** `_derive_event_rows` calls `edgar_events.normalize_key`
     without `header_times`.
  3. **Forbidden fallback:** E-3-R1 `normalize_key` / `normalize_events` fall back to the
     submissions-JSON `acceptanceDateTime` when `header_times is None`. V13-E2 §3
     forbids any JSON fallback.
- **Correction (binding):**
  1. **E-4-R1 (`edgar_build.py` + its tests/fixtures):**
     - `build(..., im_p_seed=1302, ev_t_seed=1304, ...)` replaces the single `seed`. The
       manifest records both seeds;
     - `_derive_identity_rows` builds the IM-P evidence from the raw store with
       `edgar_identity.build_symbol_evidence(raw_root, sample_rows)`, using the sample
       selected with `im_p_seed`;
     - `_derive_event_rows` loads `edgar_events.load_raw_header_times(raw_root, cik)` for
       every mapped CIK and passes `header_times`;
     - mutations E4-M4 (IM-P uses the EV-T seed) and E4-M5 (header times omitted).
  2. **E-3-R2 (`edgar_events.py` + its tests/fixtures):**
     - `header_times is None` → `EventInputError` (fail closed); the JSON-time path is
       removed;
     - `acceptance_json_raw` and `json_discrepancy` remain recorded facts;
     - mutation E3-M9 (the JSON fallback restored).
- **Interfaces:** unchanged otherwise. The L2 and L3 relpaths are as merged.

## Addendum E-4-R3 (user-approved 2026-10-09: "批准E-4-R3")

- **Defect:** `run_ev_t_gate` returns NOT_CERTIFIED when the usable events number fewer
  than the sample size. That rule is not in any frozen document. V13-E1 states that an
  event with no finite current return or an empty window is "unusable (not counted as
  a failure)".
- **Disclosure:** this was found after the corrected EV-T run was seen:
  - 150 hits of 191 usable (78.5%), 9 unusable;
  - the pre-E-4-R2 misaligned run gave 56.5% and is recorded as invalid.

  The decision does not depend on the outcome. Under the most conservative reading
  (unusable counted as misses) the fraction is 150/200 = 75.0%, also ≥ 0.60.
- **Correction:** the EV-T fraction = hits / usable, per V13-E1. PASS iff fraction ≥ 0.60
  and usable ≥ 1. The manifest also records `n_unusable` and `worst_case_fraction` =
  hits / sample_size (reported, not gating). The threshold, sample size, seed (1304) and
  window (252) are unchanged.
- **Mutation:** E4-M7 (the `usable < sample_size → NOT_CERTIFIED` rule restored) must be
  killed.
