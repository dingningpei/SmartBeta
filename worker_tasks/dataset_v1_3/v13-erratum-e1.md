# V13-E1 — freeze three gate parameters the frozen v1.3 specs left open (FROZEN 2026-10-08; user-approved)

- **Defect class:** specification gap (planner). The design (`c9daa54b…`) and the plan
  (`408e86b4…`) said "seeded" and "trailing median" without values. The workers chose the
  values; they become binding only through this erratum.
- **Timing:** no real data has been read. E-1L has not run.

| Parameter | Frozen value | Where |
|---|---|---|
| IM-P sample seed | `1302` (E-2 `DEFAULT_IM_P_SEED`; merged in `9869030`) | `edgar_identity.select_im_p_sample` |
| EV-T sample seed | `1304` (E-4 `DEFAULT_BUILD_SEED`) | `edgar_build.run_ev_t_gate` |
| EV-T trailing window | the **252** trusted development sessions strictly before K(e); finite `abs(daily_total_return)` only; an event with no finite current return or an empty window is unusable (not counted as a failure) | `edgar_build.EV_T_TRAILING_SESSIONS` |

- **Effect:** no code change. E-4 `c2a34b6` merges as reviewed after approval.
- **Rule for future specs (planner process fix):** every "seeded" or "window"
  specification must state its literal value at freeze time.
