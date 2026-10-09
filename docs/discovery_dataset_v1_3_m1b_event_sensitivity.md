# M1b — event-layer sensitivity model for Dataset v1.3 (FROZEN 2026-10-09; methodology; document-only)

- **Status:** FROZEN by user authorization ("三件事都授权", 2026-10-09), after ADM-13 (`ecbee07`).
  The deferral rule allowed the boundary window to be informed by v1.3 **structural** counts only;
  no returns were used (§3).
- **Binding inputs:** v1.3 admission `ecbee07` (acq `f0d0ee1b…`, ev `ff5e83ec…`); errata V13-E2 (time source = EDGAR
  index-header `ACCEPTANCE-DATETIME`, America/New_York) and V13-E4.
- **Scope:** the typed, fail-closed treatment of uncertainty coming from the v1.3 event
  layer, for any expression that depends on events.
- **Out of scope:** price-side uncertainty stays P-Q v2.x (S2, UNKNOWN causes); M1b adds
  causes, never numeric bounds.

## 1. Event-dependent expressions

- **No event expression class exists yet.** P-Q v2.2 certifies price/volume grammars only
  (A§15A.3 excludes event inputs).
- **Event inputs:** M1b defines the *uncertainty semantics* an event class must use.
  The first event expression class is frozen later, together with Campaign-3's
  hypothesis family, as a P-Q v2.3 grammar extension (§5).
- **What an event cell depends on:**
  - (a) the key's IM status;
  - (b) the event's knowledge session K(e);
  - (c) for announcement-window returns, the TRI over [K(e), K(e)+1], which is covered by
    P-Q v2.x through the normal dependency rules.

## 2. Causes (typed; precedence extends M§8A.4)

| Cause | Trigger | Effect |
|---|---|---|
| `PQV2_EVENT_IDENTITY_UNMAPPED` | the key's IM status is AMBIGUOUS, NO_FILINGS_IN_INTERVAL or NO_CANDIDATE, for any cell that depends on that key's events | cell NOT_ASSESSED; the key stays in the universe (never dropped) |
| `PQV2_EVENT_IDENTITY_CONTRADICTED` | the key is in the IM-P audit with verdict CONTRADICTED | NOT_ASSESSED for every event-dependent cell of that key |
| `PQV2_EVENT_RESERVE` | K(e) ≥ 2023-07-03, as seen by a development cell (it should be impossible; defensive) | NOT_ASSESSED; a firewall incident |
| `PQV2_EVENT_TIME_UNAVAILABLE` | the key has an in-scope `EA_8K202` event (filed ≥ 2015-01-02, inside its interval) with `k_session = null` (`EVENT_TIME_UNAVAILABLE`) whose filing date lies inside the cell's event lookback | NOT_ASSESSED. Silently skipping it could misidentify the "latest event" (ADM-13 count: 3 such events) |
| `PQV2_EVENT_SOURCE_EXCLUDED` | the key's IM status is FOREIGN_FILER (no 8-K 2.02; 6-K timing excluded by user decision) | NOT_ASSESSED for event-dependent cells; the key is retained |

**Precedence (full order):**
RESERVE > EVENT_RESERVE > SOURCE > EVENT_IDENTITY_CONTRADICTED >
EVENT_IDENTITY_UNMAPPED > EVENT_SOURCE_EXCLUDED > EVENT_TIME_UNAVAILABLE >
SIGMA_SUPPORT > WINSOR_BLOCK > EMPTY_LEG.

The cause set itself is never reduced.

## 3. Timing scenarios (a dual scenario, like T0/TS; never combined)

- **Time source:** the index-header acceptance time (V13-E2), never the submissions-JSON field.
- **Boundary set (frozen; asymmetric, pre-close):** B_e holds iff the acceptance time
  lies in **[15:45, 16:00) ET** on a trading session.
  - **Rationale (risk direction, independent of the data):**
    - **Acceptances at or after 16:00** already map to the next session (K0), the
      earliest defensible session. The only alternative (the same session) would be
      look-ahead, so no dual scenario is warranted.
    - **Acceptances shortly before the close** map to the same session (K0), but the
      information may not have been tradeable before the close. The conservative
      alternative is the next session.
  - **Structural confirmation** (ADM-13 development `EA_8K202` with K(e), N = 49,571; no
    returns):
    - [15:45, 16:00): **74** (0.15%);
    - [16:00, 16:15): 10,116 (20.4%; standard after-close release minute);
    - before 09:30: 20,779;
    - 09:30–15:45: 2,437;
    - after 16:15: 16,165.
  - **Why the draft's symmetric ±15 min window is replaced:** it would have put about 20%
    of events into a look-ahead alternative scenario.
  - **The v1.3 `boundary_flag` column** (symmetric ±15 min, an E-3 recorded fact) is
    **not** B_e. P-Q v2.3 computes B_e from `acceptance_et`.
- **Early closes:** no early-close calendar is part of the frozen inputs. B_e uses 16:00
  on every session, a recorded limitation (about 3 sessions per year).
- **Two scenarios per period** (only when some cell depends on a B_e event):
  - **K0:** K(e) by the frozen v1.3 rule (the same session);
  - **K1:** K(e) = the next trading session.
- **Combined classification:** a PASS requires PASS under **both** K0 and K1, in the same
  way T0/TS both must pass. No averaging, and no choosing the better scenario.
- **Fallback events:** `EA_PERIODIC_FALLBACK` events carry no extra cause. Their use is
  **declared** per hypothesis (admitted or not), and the declaration is part of the
  hypothesis definition hash. (The Campaign-3 plan proposes excluding them in v2.3.)

## 4. Exposure discipline

The v1.3 EV-T gate reads development returns. Every Campaign that uses v1.3 events must
carry, verbatim, an exposure disclosure listing:
- EV-T's statistic family;
- its sample;
- that it read no factor or long-short outcome.

## 5. Identity impact

- **ED tokens to be added** by the P-Q v2.3 amendment:
  - `event_identity_unmapped_not_assessed`;
  - `event_identity_contradicted_not_assessed`;
  - `event_reserve_not_assessed`;
  - `event_time_unavailable_not_assessed`;
  - `event_source_excluded_not_assessed`;
  - `event_timing_dual_scenario_k0_k1_preclose_15m`.
- **CS-ID:** unchanged.
- **Event grammar:** the v2.3 amendment also defines the first event grammar.
- **Freezing M1b alone changes no current identity.** It freezes the semantics that v2.3
  must adopt.

## 6. Non-claims

- **What the causes are.** They express *assessability*. They make no claim about how
  often the market learns information before SEC acceptance.
- **[A-EVT] (assumption):** an 8-K Item 2.02 acceptance time is not later than the first
  public availability of the earnings information **for trading purposes after
  K(e)**. Earlier leaks (press releases) make K(e) conservative-late, not early. This is
  an assumption, disclosed rather than proven; EV-T is only a sanity check.
- **Not certified:** identity knowledge timing.
- **Early-close sessions** are not boundary-adjusted.
