# Dataset v1.3 — SEC EDGAR event layer (FROZEN DESIGN 2026-10-08; nothing acquired; implementation not authorized)

- **Status:** FROZEN design by user approval ("按建议冻结", 2026-10-08), roadmap M3. The worker specs and every acquisition need separate authorization.
- **Change type:** an **additive** layer on top of Dataset v1.2. The v1.2 artifacts,
  identities and admission are unchanged. v1.3 adds one event panel and one identity map.
- **Inputs:** the M2 research record (`docs/edgar_feasibility_m2.md`), with its four
  follow-ups folded in (user decision 2026-10-08).
- **Scope:** **event dates only.** XBRL fundamentals are deferred to a separate v1.4
  (freeze decision 1).
- **Why event dates alone are enough for a first hypothesis.** The earnings-announcement
  surprise can be proxied by the **announcement-window return** (Brandt, Kishore,
  Santa-Clara & Venkatachalam, "Earnings Announcements are Full of Surprises", SSRN
  working paper 909563, 2006; verified 2026-10-08), which needs only v1.2 prices plus the announcement
  date.

## 1. What v1.3 delivers

| Artifact | Content |
|---|---|
| **IM — identity map** | each D4 `security_key` → a CIK with a validity interval, or a typed non-mapping status |
| **EV — event panel** | each mapped key → its earnings-event records, each with an exact public knowledge session |
| **Manifest** | source hashes, SEC retrieval times, the mapping audit, gate results; the development / reserve split exactly as in v1 §11–§12 |

## 2. Sources (public domain; SEC fair-access ≤ 10 requests/s with a contact User-Agent)

| Source | Use |
|---|---|
| `submissions/CIK##########.json` (+ paginated `files`) | per filing: `form`, `filingDate`, **`acceptanceDateTime`**, `items`, `reportDate`, `accessionNumber`; `formerNames` with dates |
| `company_tickers.json` | current ticker → CIK (active route) |
| `cik-lookup-data.txt` | historical and former names → CIK (ended route) |
| Filing primary-document headers or XBRL `dei:TradingSymbol` (only for the precision audit) | independent symbol evidence |

**Retrieval rules:**
- every raw response is stored byte-exact under `raw/` with its retrieval time and SHA-256;
- normalization reads only stored raw bytes;
- re-acquisition must reproduce the hashes, or record the divergence.

## 3. Identity map (IM) — fail-closed

**Mapping routes:**
1. **Active keys:** current ticker → CIK.
2. **Ended keys:** issuer name → normalized name → CIK candidates.
   - **Tiingo names:** the issuer name comes from Tiingo metadata. A **name snapshot** is
     taken at acquisition and stored in `raw/` (allowed under the Tiingo licensing
     disposition only as an internal artifact; never redistributed).
   - **Interval check:** candidates are restricted by `formerNames` intervals that
     overlap the key's D4 listing interval.

**Verification** (M2 rule, kept): exactly one candidate filed a periodic report inside
the key's listed interval.

**Statuses:**

| Status | Meaning |
|---|---|
| MAPPED | the only status whose events enter EV |
| AMBIGUOUS | more than one verified candidate |
| NO_FILINGS_IN_INTERVAL | — |
| NO_CANDIDATE | — |
| FOREIGN_FILER | 20-F / 40-F filer, no 10-Q; mapped, but reported **separately**; its events are typed `FPI_*` |

Unmapped keys stay in every universe. Their event fields are typed-missing with the status
as the reason; they are **never dropped** (the M2 limitation: non-coverage is concentrated
in ended keys and may be outcome-correlated).

**Precision audit (M2 follow-up 1; a binding gate).**
- **Sample:** a seeded random sample of **200 MAPPED keys** (100 active, 100 ended).
- **Per key, independent symbol evidence:** `dei:TradingSymbol`, or the filing-header
  trading symbol, in at least one periodic filing inside the interval must equal the
  key's D4 ticker (ticker-history aware).
- **Outcomes:** each key is CONFIRMED, CONTRADICTED or NO_SYMBOL_EVIDENCE.
- **Gate IM-P:** a CONTRADICTED rate ≤ 1% **and** a one-sided 95% upper bound on the
  contradicted share ≤ 3%.

**Identity knowledge timing.** As in v1.2, *when* a key ↔ issuer link became knowable is
**NOT CERTIFIED**. The map is a retrospective construction, and this boundary is carried
forward unchanged.

## 4. Event panel (EV)

**Event types:**

| Type | Rule |
|---|---|
| `EA_8K202` | 8-K (original, not `/A`) whose `items` include 2.02 |
| `EA_PERIODIC_FALLBACK` | a 10-Q/10-K with **no** `EA_8K202` in the window [report period end, filing]; typed separately, **never** merged silently with `EA_8K202` |
| `FPI_6K` / `FPI_20F` | foreign private issuers; separate types |

**Knowledge timing (M2 follow-up 2; a binding rule):**
- Source field: `acceptanceDateTime`, in US Eastern time.
- **Knowledge session K(e):**
  - the session that contains the acceptance time, if it is before 16:00 ET on a
    trading session;
  - otherwise the **next** trading session (after-close, weekend or holiday);
  - the session calendar is the D3/D7c trusted session set.
- **Factor use:** at formation row f, an event is usable iff K(e) ≤ the date of row f−1
  (the D7c bridge-lag convention of M§10). Never K(e) ≥ f.
- **Announcement-window return:** defined on sessions [K(e), K(e)+1], computed from
  v1.2 TRI, so that P-Q v2.x correction sensitivity applies to it. **This makes the
  return a new price-derived dependency**, and its expression class needs M1b
  certification.
- **Excluded:** `filingDate` is never used as the knowledge time. Amendments (`8-K/A`)
  never create or move an event.

**Coverage (M2 follow-up 3; a binding gate).**
- **Gate EV-C:** among MAPPED domestic keys, the median `EA_8K202` events per listed year
  in development is ≥ 3.0, and ≥ 80% of mapped domestic key-years have ≥ 3 `EA_8K202`
  events.
- **Failing EV-C** means EA events are not reliably dated for that year. EV still
  records the facts, but no hypothesis may use `EA_8K202` alone for those years.

**Gate EV-T (timing consistency).** For a seeded sample of 200 `EA_8K202` events, the
v1.2 absolute return on K(e) exceeds its own trailing median absolute return in ≥ 60% of
cases. This is a sanity check that the dated session is the information session, not a
proof.

- **Disclosure about EV-T:** it reads development returns. It reads no factor or
  long-short outcome, and it is a data-quality diagnostic. It must be precommitted
  **before** any EA hypothesis is frozen, and it is disclosed as a data exposure in any
  later Campaign (the same discipline as the C2 re-use disclosure).

## 5. Firewall

- **Acquisition:** it may fetch full filing histories, including dates ≥ 2023-07-03.
- **Reserve and holdout:** every event with K(e) ≥ 2023-07-03 is written **only** to
  `reserve_sealed/` under the v1 §12 rules. Development EV loaders fail closed on any
  later row. Final-holdout rules are unchanged.
- **K-PIT:** the v1.3 dataset is registered by an ARTIFACT record, as for v1.2, only
  under its own authorization.

## 6. Sensitivity model (M1b; designed together with v1.3, frozen separately)

Event-layer uncertainty sources, and their proposed typed causes (no numeric bound
claimed):

| Source | Proposed cause | Effect |
|---|---|---|
| IM status ≠ MAPPED | `PQV2_EVENT_IDENTITY_UNMAPPED` | event-dependent cells NOT_ASSESSED with that cause; the same cause discipline as UNKNOWN_SOURCE |
| Acceptance within ±15 min of 16:00 ET | `EVENT_TIMING_BOUNDARY` | evaluate under both K(e) choices; the T0/TS-style dual scenario |
| `EA_PERIODIC_FALLBACK` | a typed class, not a cause | hypotheses must declare whether fallback events are admitted |
| Price-derived announcement return | existing P-Q v2.x | S2 / UNKNOWN sources apply through TRI dependency |

## 7. Implementation DAG (after freeze; worker specs separate)

| Task | Work | Depends on |
|---|---|---|
| E-1 acquisition | SEC submissions for candidate CIKs + Tiingo name snapshot; raw store; manifest | freeze |
| E-2 identity map | routes, verification, statuses; the IM-P precision audit | E-1 |
| E-3 event normalization | types, `acceptanceDateTime` → K(e), the firewall split | E-1 |
| E-4 integration + gates | EV panel joined to D4 keys; EV-C, EV-T; deterministic rebuild | E-2, E-3 |
| ADM | admission review (planner) | E-4 |

- **Network:** E-1 is the only networked task. It runs with a contact User-Agent supplied
  per process (see freeze decision 2). Every later task is offline and runs under the
  network guard.

## 8. Non-claims

- **Event dates are filing facts.** They show when SEC accepted a filing. They do not
  show that the market learned the information then; press releases can precede 8-K
  acceptance. EV-T is a sanity check only.
- **Not certified:**
  - identity knowledge timing;
  - the economic content of the events.
- **Admission scope.** Admission would mean development-use readiness only. It would not
  mean any alpha, Campaign or reserve claim.

## Freeze decisions (user-approved)

1. **XBRL:** DEFERRED to Dataset v1.4. v1.3 is event dates only.
2. **SEC contact for E-1:** option (a), user-approved.
   - **Role deviation:** the planner runs E-1's **live acquisition step** itself. This is
     an explicit, user-approved deviation from CLAUDE.md §1 role separation, scoped to
     the networked fetch only.
   - **Pi worker scope:** the E-1 *software* (fetch client, raw store, manifest) is still
     implemented by a Pi worker and reviewed. Normalization and every later task stay
     with Pi workers, offline.
   - **Contact handling:** the contact is supplied per process from the conversation. It
     is never written to a file, log, manifest or commit.
3. **Gate thresholds:** ACCEPTED: IM-P ≤ 1% with a 95% UB ≤ 3%; EV-C median ≥ 3.0 and ≥ 80%; EV-T ≥ 60%.
4. **Foreign private issuers:** kept as separate typed events (`FPI_*`), never merged with `EA_8K202`.
