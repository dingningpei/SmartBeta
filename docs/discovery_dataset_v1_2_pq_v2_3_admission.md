# Dataset v1.2 + v1.3 — P-Q v2.3 real development-data admission evidence (PQ2-ADM v2.3, from ADM-1)

- **Status:** planner admission evidence. Authorized by the user ("1. 授权", 2026-10-09).
- **Software bound:** master `443156b`, the certified P-Q v2.3 software. The I1–I15
  evidence is `docs/discovery_dataset_v1_2_pq_v2_3_software_barrier.md` (certifying tree
  `553778c`); `443156b` adds only that document.
- **Admission spec:** PQ2-ADM ADM-1…ADM-6, plus the v2.3 plan §4 additions:
  - the event structural census;
  - certification of the positive-control spec.
- **Authority selection:** v2.3 is evaluated explicitly. `CURRENT_AUTHORITY` stays v2.1;
  Campaign-2 is unaffected.
- **No empirical execution:** no factor values, event-primitive values, L/B statistics,
  screens, Campaign attempts or slots. The event census uses **dates and identities
  only**; no price or return value is read for it.

## Identities

| Item | Value |
|---|---|
| P-Q authority | v2.3 — methodology `d4ec7f6caaa6cc74a340fdcf28b5d2030b3a650c383518284099ebd5a0b26aad` (chain v2.2 `ab12886c…`, v2.1 `66772bb1…`), ED `b3907c69…01ea7b`, CE `f3c7c7df…2d24a91`, CS `432de097…` |
| Dataset v1.2 / overlay / D7c / D7a | `a065493d…b9da` / `pq-59c3bd7abe08eef5` / `ti-aa23fabd3db4e595` / `r-34565f074d64ccda` |
| Dataset v1.3 event layer | acq `f0d0ee1b4f5875b1d8ad970e041ebe73d8278cc283477378af4bb1bf9fa04f54`, ev `ff5e83ec253f007493bfc7341fd4dcc90606ee932855efe3313686d1afabcca3` (manifest-verified by `pqv2_events.load_event_layer`) |

## Environment

- **Credentials and network:** credentials scrubbed; guard `6697069a…8368` verified; the
  self-test blocked 3 outbound paths, with 0 credential names present.
- **Scripts** (scratchpad): `adm23/adm2.py` (`97815a26…`; the v2.2 ADM-2 script plus the
  v2.3 authority and the event census through the production `event_windows` /
  `event_cell_causes`), `adm23/adm4.py` (`11343a30…`).

## Results

**ADM-1 — Authority: PASS**
- `verify_authority` passes for v2.3, v2.2 and v2.1 (the chain);
- `require_admitted_authority` resolves v2.3;
- `CURRENT_AUTHORITY` = v2.1;
- 11/11 pinned hashes match.

**ADM-2 — Real development data: PASS**
- **Determinism:** two fresh runs are identical except for the wall time and RSS.
- **Price side** (identical to the v2.1/v2.2 ADMs):
  - source digest `198a9606…cfed`;
  - 17,469 flags = 2,687 RESOLVED + 14,701 UNKNOWN_SOURCE + 81 UNKNOWN_RESERVE;
  - 147 MISDATED candidates; 203 flags reached by a C1 dependency;
  - 312 reused tickers.
- **Event layer** (production loader):
  - 1,921 keys: MAPPED 1,654, FOREIGN_FILER 169, NO_CANDIDATE 69, AMBIGUOUS 22,
    NO_FILINGS_IN_INTERVAL 7;
  - 49,571 timed development `EA_8K202` events;
  - 2 in-scope null-time candidates (by the loader's filing-date proxy);
  - reserve incident: **false**.
- **Event structural census** (TOP_1000, the 72 IS/OOS formations, 72,000 key-formation
  cells; dates and identity only):

  | Item | Cells |
  |---|---|
  | e* observed (K0) | **62,630 (87.0%)**; K1 the same count |
  | K1 window ≠ K0 window (pre-close B_e e*) | **99 (0.14%)** |
  | cells with an event cause | **8,124 (11.3%)**: `PQV2_EVENT_SOURCE_EXCLUDED` (foreign filers) 5,608; `PQV2_EVENT_IDENTITY_UNMAPPED` 2,516 |
  | `TIME_UNAVAILABLE` | 0 |
  | event-anchored TRI windows (cause-free cells) reached by an unresolved price flag | **0**. The reach routine is the one used for the C1 census, which had 0 mismatches vs C |

  The census measures **structural assessability only, not performance.** About 11% of
  TOP_1000 cells will be NOT_ASSESSED for event expressions by construction (FPI and
  unmapped keys). They are retained, never dropped.

**ADM-3 — Firewall read audit: PASS**
- 1,801 opens per run;
- **0** under `reserve_sealed/` (including `reserve_sealed/edgar-*`), `raw/` (including
  `raw/edgar-*`), `dsc_evidence` or a final holdout;
- 3 development reads of `derived/edgar-<acq>/` (manifest, events, identity map).

**ADM-4 — Certification table: PASS**

| Spec | v2.3 | Other |
|---|---|---|
| C1-H1…H7 | SIMPLE / STD_X_RANK (unchanged) | — |
| Perturbed C1-H1 (lag 252 → 251) | SIMPLE | v2.1: NOT_CERTIFIED |
| **Positive control** `ear` (sign +1; field role = `event_ear_k_kp1`) | **EVENT_SIMPLE** | v2.2: NOT_CERTIFIED |
| `1 / ear` (event primitive as divisor) | NOT_CERTIFIED (E§3) | — |

- **Interface note:** the implemented convention (B/C/G/H) is that an event field's
  **role is its semantic id** (`event_ear_k_kp1`, `event_days_since_ea`,
  `event_recent_ea_w<W>`). The plan §2 table listed the primitive names; every
  implementation adopted the stricter alias = semantic id rule, so roles and semantics
  cannot drift. A first ADM-4 attempt that used the alias `ear` was correctly rejected
  (fail-closed).

**ADM-5 — Resources: PASS (recorded)**
- ADM-2 with the event census: about 177 s wall, peak RSS about 2.4–3.2 GB.

**ADM-6 — Immutability: PASS**
- The following are byte-identical:
  - K-PIT `190d53f0…`;
  - the Campaign-1 artifacts;
  - the v2.3 software evidence;
  - the v1.3 admission.
- No Campaign-2 output directory exists.
- 0 K-PIT writes, 0 attempts, 0 slots.

## Non-claims

- **Assessability only:** P-Q v2.3 certifies assessability, not usefulness or alpha.
  UNKNOWN causes carry no bound.
- **Assumptions:** [A-STD] and [A-EVT] are assumptions. Early closes are not
  boundary-adjusted.
- **Not certified:** identity knowledge timing.
- **No confirmatory capability yet:** no admitted InferenceProcedure exists (Track P
  pending), and no reserve-fold data path exists (Campaign-3 prerequisite P7).
- **No reserve or holdout use.**

## Disposition

**P-Q v2.3 PQ2-ADM PASS — ADMITTED FOR PROSPECTIVE DEVELOPMENT USE (explicit authority selection; event layer v1.3)**
