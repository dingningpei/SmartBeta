# M1b — event-layer sensitivity model for Dataset v1.3 (DRAFT methodology; freeze DEFERRED by user decision 2026-10-08)

- **Deferral:** per user decision ("按照你的建议", 2026-10-08), this freezes after
  ADM-13, together with the first event hypothesis (P-Q v2.3). The ±15 min boundary may
  be informed by v1.3 **structural** counts only (no returns).
- **Status:** planner draft, designed with M3 (roadmap; v1.3 design `c9daa54b…` §6).
  It is a **methodology**: freezing it needs your explicit approval, separately from the
  v1.3 worker specs.
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

**Precedence (full order):**
RESERVE > EVENT_RESERVE > SOURCE > EVENT_IDENTITY_CONTRADICTED >
EVENT_IDENTITY_UNMAPPED > SIGMA_SUPPORT > WINSOR_BLOCK > EMPTY_LEG.

The cause set itself is never reduced.

## 3. Timing scenarios (a dual scenario, like T0/TS; never combined)

- **Boundary set.** B_e holds iff `acceptanceDateTime` ∈ [15:45, 16:15) ET on a trading
  session, or the session is an early-close session within ±15 min of its close.
- **Two scenarios per period** (only when some cell depends on a B_e event):
  - **K0:** K(e) by the frozen v1.3 rule;
  - **K1:** K(e) shifted to the alternative session.
- **Combined classification:** a PASS requires PASS under **both** K0 and K1, in the same
  way T0/TS both must pass. No averaging, and no choosing the better scenario.
- **Fallback events:** `EA_PERIODIC_FALLBACK` events carry no extra cause. Their use is
  **declared** per hypothesis (admitted or not), and the declaration is part of the
  hypothesis definition hash.

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
  - `event_timing_dual_scenario_k0_k1`.
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
