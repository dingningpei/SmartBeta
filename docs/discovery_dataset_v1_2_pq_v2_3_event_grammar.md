# P-Q v2.3 — event grammar EVENT_G (FROZEN 2026-10-09; document-only; NOT implemented)

- **Status:** FROZEN by user approval ("都按建议", 2026-10-09).
- **Methodology identity:** P-Q v2.3 = the SHA-256 of this document as committed,
  always bound with the v2.2 amendment `ab12886cdbac42725bcc283d2e37567e15340b3b80cb603f2a4df8a31a77af76`
  and the v2.1 base `66772bb1b12a134ca35a3ff5e3d475e00c998b95d3cdcb1e3223fd5f012d5d1e`.
- **What it amends:** P-Q v2.2 (`ab12886c…`, on the v2.1 base `66772bb1…`) **only** by
  adding EVENT_G (§3) and the M1b causes. SIMPLE_G, STD_X_RANK_G, S2 and CS are
  unchanged.
- **Binding inputs:**
  - M1b (frozen, `3c07c3f`, `94309a61…`): causes, precedence, the K0/K1 pre-close
    boundary;
  - Dataset v1.3 (ADM-13 `ecbee07`; acq `f0d0ee1b…`, ev `ff5e83ec…`).
- **Purpose:** make certified, P-Q-assessable expressions that use **earnings-event
  timing**: the generator's event candidates and the Campaign-3 positive control.

## 1. Event inputs (development only)

- **The event set** of key k: the `EA_8K202` events of k in the v1.3 development panel
  with `k_session` present.
  - **Excluded:** fallback, `FPI_*` and null-time events, which are handled by causes
    (§4).
  - **Admitted keys:** IM status MAPPED only.
- **Knowledge rule** (V§4 plus the D7c bridge lag): at formation row f, an event is
  *observed* iff K(e) + 1 ≤ date(f − 1), so that its two-session announcement window is
  complete.

## 2. Event primitives (per key k, formation row f)

Let e*(f) be the latest observed event with K(e*) ≥ date(f − 1) − 504 sessions. If none
exists, every primitive is NaN (FACTUAL_MISSING). That is correction-invariant, because
event presence does not depend on prices.

| Primitive | Definition | Price dependency |
|---|---|---|
| `ear` | TRI(K(e*)+1) / TRI(K(e*)−1) − 1 (two-session announcement return; v1.2 TRI, one security) | **algebraic**, m_j for j ∈ (K(e*)−1, K(e*)+1]: an **event-anchored** TRI window (§5) |
| `days_since_ea` | trading sessions from K(e*) to date(f − 1), ≥ 1 | none |
| `recent_ea(w)` | 1 if `days_since_ea` ≤ w, else 0; 2 ≤ w ≤ 504 | none |

## 3. EVENT_G grammar (own-cell; Prop. 4)

```
X   := P | c·X | X ± c | X × X | X + X | X − X | X / D | lag-free
P   := ear | days_since_ea | recent_ea(w) | any SIMPLE_G term
D   := the SIMPLE_G denominator set (event primitives are NOT admitted as divisors)
```

- **Constraints:**
  - at least one event primitive;
  - no cross-sectional node;
  - **no `lag` of an event primitive** (the event recency is already explicit);
  - the SIMPLE_G bounds apply (lookback ≤ 504) to the price terms;
  - sign +1.
- **Class:** `EVENT_SIMPLE`. It is assigned by the Prop. 4 simple rule (§11.1), over B's
  finite-factor ranked set (H-R1).
- **Excluded in v2.3:**
  - fallback events;
  - event primitives inside `standardize`/`rank`;
  - multiple-event aggregates (for example the mean of the last 4 EARs);
  - event-anchored rolling windows.

## 4. Causes (M1b, adopted verbatim)

| Cause | Trigger | Cell scope |
|---|---|---|
| `PQV2_EVENT_IDENTITY_UNMAPPED` | IM status AMBIGUOUS, NO_FILINGS_IN_INTERVAL or NO_CANDIDATE | every event-dependent cell of that key |
| `PQV2_EVENT_IDENTITY_CONTRADICTED` | IM-P verdict CONTRADICTED | every event-dependent cell of that key |
| `PQV2_EVENT_SOURCE_EXCLUDED` | IM status FOREIGN_FILER | every event-dependent cell of that key |
| `PQV2_EVENT_TIME_UNAVAILABLE` | an in-scope `EA_8K202` with null K, filed in (K(e*), date(f − 1)]; or within the 504-session lookback when e* is absent | that cell |
| `PQV2_EVENT_RESERVE` | a development cell sees K ≥ 2023-07-03 | that cell (firewall incident) |

- **Plus the price causes:** the existing SOURCE / RESERVE causes apply to the
  event-anchored TRI window through the unchanged §8A rules.
- **Precedence:** the M1b order.

## 5. Certification argument (to be proven in the frozen amendment)

1. **Event-anchored windows are price-independent.** K(e) comes from SEC acceptance
   times (V13-E2), not from prices. So for each (k, f) the window (K−1, K+1] is a
   **fixed** set of sessions before any correction is considered. Prop. 2 (TRI prefix
   cancellation) applies to `ear` exactly as to a fixed-offset ratio. The only
   difference is that the offsets vary by key.
2. **Own-cell, so Prop. 4 holds.** Uncertain members are placed anywhere; precondition
   (iii) holds because:
   - event presence and `days_since_ea` are correction-invariant;
   - `ear` is a finite TRI ratio;
   - event primitives are never divisors.
3. **Timing (M1b §3):**
   - if e*'s acceptance lies in [15:45, 16:00) ET, the cell is evaluated under **K0**
     (the same session) and **K1** (the next session). K1 moves both the window and the
     observability by one session;
   - the scenarios are independent of T0/TS, so an event-dependent period carries four
     scenario combinations;
   - **classification:** PASS requires PASS under **every** combination, with no
     averaging (the T0/TS rule extended).
4. **Turnover (Prop. 8)** and **the leg envelope (Prop. 7)** are unchanged per scenario.

## 6. Identities (frozen literals)

- **ED-ID v2.3** = the SHA-256 of the exact line below
  (`b3907c69878342fa9e8b07cdf35e4ddd6b47823fe0388ee9fb1762235e01ea7b`). It is the
  v2.2 list (`a67afc6b…`) with the eight event tokens appended in this order.

```
{"rules":["tri_log_linear_prefix_cancellation","lag_shift","rolling_window_union_numerical","arithmetic_conservative_union","cross_sectional_whole_row","label_candidate_e_[f,next(e)]","dtr_{prev,s,next}","dv_{prev,s}","misdated_recorded_pm5","reserve_unknown_not_assessed","source_unresolved_unknown_not_assessed","add_sub_support_union","event_anchored_tri_window","event_primitive_price_independent","event_identity_unmapped_not_assessed","event_identity_contradicted_not_assessed","event_reserve_not_assessed","event_time_unavailable_not_assessed","event_source_excluded_not_assessed","event_timing_dual_scenario_k0_k1_preclose_15m"],"version":"pq-v2.3-ed-4"}
```

- **CE-ID v2.3** = the SHA-256 of the exact line below
  (`f3c7c7dfaa7de050b991fd64e7d586e69c787707834116d827301ad332d24a91`).

```
{"classes":{"simple_exact":"GRAMMAR_SIMPLE_G_v1","std_x_rank_sandwich":"GRAMMAR_STD_X_RANK_G_v1","event_simple":"GRAMMAR_EVENT_G_v1"},"bounds":{"lookback_max":504,"window_min":2,"window_max":504},"denominators":["tri_ratio","rolling_mean_dv","rolling_std_ret_assumption_A_STD"],"event_data":{"acq_id":"f0d0ee1b4f5875b1d8ad970e041ebe73d8278cc283477378af4bb1bf9fa04f54","ev_id":"ff5e83ec253f007493bfc7341fd4dcc90606ee932855efe3313686d1afabcca3"},"event_window":"K_and_K_plus_1","other":"NOT_ASSESSED_UNLESS_SEPARATELY_FROZEN","version":"pq-v2.3-ce-3"}
```

- **CS-ID:** unchanged (`432de097…bdae2`).
- **Authority:** `AUTHORITY_V2_3`, explicitly selectable (the PQ22-E1 pattern).
  `CURRENT_AUTHORITY` stays v2.1.

## 7. Oracle requirements (additions)

- **Event-anchored worlds:** a per-key event calendar, with the brute-force check of
  membership / L / turnover over correction grids that touch the event window.
- **Required cases:**
  - an event inside vs outside the TRI suspect window;
  - an event at the 504-session lookback edge;
  - a pre-close B_e event (K0/K1 both evaluated);
  - a 16:05 acceptance (no K1 scenario);
  - each M1b cause;
  - an FPI key;
  - a null-time in-scope event between e* and f;
  - an event primitive as a divisor (must be NOT_CERTIFIED);
  - an event primitive inside `standardize` (must be NOT_CERTIFIED).
- **Random EVENT_G population:** ≥ 2,000 members, with the two-tier cross-check
  (PQ22-E2 pattern).

## 8. Implementation impact (estimate; the implementation spec comes after the freeze)

| Layer | Work |
|---|---|
| **New B-side loader** (`pqv2_events.py`) | v1.3 events + IM statuses → per-key event calendars and cause sources (development only; fail-closed) |
| **C** | per-key event-anchored windows; the EVENT_G recognizer |
| **H** | event inputs; the K0/K1 scenario dimension; four-way classification |
| **F** | the scenario dimension passes through (the same envelope per scenario) |
| **D/E** | expected unchanged |
| **G oracle** | event worlds plus the generator |

Then I1–I15 plus ADM, as for v2.2.

## 9. Non-claims

- **Assessability only:** EVENT_G certifies *assessability*. It says nothing about the
  economic content of earnings events.
- **Assumptions carried:** [A-EVT] and [A-STD]. Identity knowledge timing is NOT
  CERTIFIED. Early-close sessions are not boundary-adjusted.

## Freeze decisions (user-approved recommendations)

1. **Primitives:** the minimal set `ear`, `days_since_ea`, `recent_ea(w)`. No
   aggregates.
2. **Announcement window:** two sessions, [K, K+1], so `ear` = TRI(K+1)/TRI(K−1) − 1.
3. **Event-anchored rolling windows:** excluded in v2.3.
