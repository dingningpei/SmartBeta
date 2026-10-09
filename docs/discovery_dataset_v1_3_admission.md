# Dataset v1.3 — EDGAR event layer: production build and admission evidence (ADM-13)

- **Status:** planner admission evidence. Authorized by the user ("授权", 2026-10-09).
- **Software:** master `03e08ad`. All v1.3 tasks and repairs are merged:
  - E-1 `d4d293e`, E-1-R1 `50f31e3`, E-1-R2 `4ac60f1`;
  - E-2 `b58972c`, E-2-R1 `0a8efad`, E-2-R2 `5c9e98b`;
  - E-3 `caec125`, E-3-R1 `df18f05`, E-3-R2 `86f8ccd`;
  - E-4 `c2a34b6`, E-4-R1 `7339f45`, E-4-R2 `5d002d0`, E-4-R3 `35f1d18`.
- **Authority:**
  - design `c9daa54b…b269`;
  - plan `408e86b4…` with §8 (Q1);
  - errata V13-E1 (seeds and window), V13-E2 (header time source, real schema, L3
    scope/budget), V13-E3 (L2 inline-XBRL selection + extractor), V13-E4 (+ addendum
    E-4-R3).
- **Not done:** no K-PIT write and no ARTIFACT registration (that needs its own
  authorization). No factor, long-short, Campaign or reserve-outcome computation.

## Identities

| Item | Value |
|---|---|
| Raw acquisition `acq_id` | `f0d0ee1b4f5875b1d8ad970e041ebe73d8278cc283477378af4bb1bf9fa04f54` (`raw/edgar-<acq_id>/`; 135,969 files; manifest verified) |
| Build `ev_id` | `ff5e83ec253f007493bfc7341fd4dcc90606ee932855efe3313686d1afabcca3` (`derived/edgar-<acq_id>/manifest.json`) |
| Seeds | IM-P `1302`, EV-T `1304` (V13-E1; both recorded in the manifest) |
| Development / reserve split | development K(e) ≤ 2023-06-30 in `derived/edgar-<acq_id>/events.parquet`; K(e) ≥ 2023-07-03 only in `reserve_sealed/edgar-<acq_id>/events.parquet` |

## Acquisition (E-1L, planner-run per the user-approved V§ freeze decision 2)

| Phase | Content | Requests | Result |
|---|---|---|---|
| L1 | reference files, 2,284 CIK submissions (+ paginated), 512 Tiingo issuer names | 4,178 | 4,177 × 200, 1 timeout |
| L3 | index headers for event filings in scope; 6-K excluded (user choice 1) | 131,605 of the 135,000 budget | 131,467 / 131,467 headers present after one retry pass of 14 |
| L2 | IM-P symbol evidence: latest 2 inline-XBRL periodic primary documents per sampled key | 317 | 317 × 200; 41 keys without inline XBRL |

- **Contact:** the SEC contact was supplied per process only. A contact scan found **0**
  occurrences after each phase and before finalization.
- **Tiingo names** are an internal artifact (no redistribution).

## Results

**Identity map** (1,921 development TOP_1000 keys):

| Status | Keys |
|---|---|
| MAPPED | 1,654 |
| FOREIGN_FILER | 169 |
| NO_CANDIDATE | 69 |
| AMBIGUOUS | 22 |
| NO_FILINGS_IN_INTERVAL | 7 |

Unmapped keys are retained (fail-closed).

**Event panel:** 261,572 events.
- **Split:** 242,710 development, 18,862 reserve.
- **Development by type:**

  | Type | Events |
  |---|---|
  | `FPI_6K` | 110,008 |
  | `EA_8K202` | 97,237 |
  | `EA_PERIODIC_FALLBACK` | 32,740 |
  | `FPI_20F` | 2,725 |

**K(e) availability** (development):
- 52,633 events carry an index-header K(e). 190,077 are `EVENT_TIME_UNAVAILABLE`, broken
  down as follows:
  - out-of-scope filings (filed before 2015-01-02 or outside the key's interval, so no
    header was fetched): `EA_8K202` 47,663, fallback 30,992, `FPI_20F` 1,411, `FPI_6K`
    50,694;
  - in-scope `FPI_6K` 59,314 (6-K excluded by user choice);
  - **in-scope `EA_8K202` 3**: SEC headers with an empty `ACCEPTANCE-DATETIME` field;
  - in-scope fallback 0; in-scope `FPI_20F` 0.
- In-scope `EA_8K202` with K(e): 49,201 of 49,204.

**JSON-time discrepancy census** (V13-E2; all events with a header):

| Class | Events |
|---|---|
| `EXACT_UTC` | 51,400 |
| `PLUS_ONE_OFFSET` | 20,095 (28.1%) |
| `OTHER` | **0** |

For `EA_8K202`: 48,891 EXACT_UTC and 18,730 PLUS_ONE_OFFSET. The submissions-JSON field
is wrong by exactly one ET offset for about 28% of filings and is never used as a time
source.

**Boundary facts:** 15,759 events have `boundary_flag` set (acceptance within ±15 minutes
of a 16:00 close). They are recorded for M1b; early-close sessions use 16:00 because no
early-close table is injected.

### Gates (frozen thresholds; manifest `ff5e83ec…`)

| Gate | Status | Metrics |
|---|---|---|
| **IM-P** | **PASS** | 200 sampled; 158 with symbol evidence (99 active, 59 ended; sufficiency floors 120 / 50 met); 158 CONFIRMED, **0 CONTRADICTED**; one-sided 95% Clopper–Pearson UB **1.88%** (≤ 3%); 42 NO_SYMBOL_EVIDENCE |
| **EV-C** | **PASS** | median `EA_8K202` per mapped domestic key-year **4.0** (≥ 3.0); share of key-years with ≥ 3: **84.2%** (≥ 80%); 12,842 key-years |
| **EV-T** | **PASS** | 200 sampled `EA_8K202` development events (seed 1304); 191 usable, 9 unusable; abs(return dated K(e)) > own trailing-252 median abs(return) in **150 / 191 = 78.5%** (≥ 60%); worst case (unusable counted as misses) 150 / 200 = **75.0%** |

### EV-T history (binding disclosure)

1. **The first build** read D7c rows by label and ignored the frozen bridge lag (M§10:
   row d holds the observation of prev(d)), so it measured the session **before**
   K(e). Result: 56.5% (108 / 191), recorded as **invalid** (an implementation
   defect).
2. **E-4-R2** (`5d002d0`) reads the observation dated K(e). The result became
   150 / 191 = 78.5%, but the gate returned NOT_CERTIFIED through a worker-added rule
   ("usable < sample size") that is in no frozen document.
3. **E-4-R3** (`35f1d18`, user-approved after observation and disclosed) implements
   V13-E1 literally (unusable not counted as a failure). The conclusion does not depend
   on this choice: the worst case is 75.0% ≥ 60%. The threshold, sample size, seed and
   window were never changed.

### Exposure disclosure (binding for any Campaign using v1.3)

EV-T read development `daily_total_return` values for its 200-event sample and their
252-session trailing windows, in three runs (one misaligned, two aligned). It
read no factor value and no long-short outcome. Any v1.3-based Campaign must carry this
disclosure verbatim.

### Firewall, determinism, immutability

- **Determinism:** two dry builds in fresh processes (`PYTHONHASHSEED` 1 and 77) are
  byte-identical, and the write build's manifest equals them.
- **File-open audit** (write build, 143,133 opens):
  - 0 opens under `reserve_sealed/` other than the build's own
    `reserve_sealed/edgar-<acq_id>/` output;
  - 0 `raw/` opens outside `raw/edgar-<acq_id>/`;
  - 0 `dsc_evidence` / final-holdout opens.
- **Firewall split verified:** the development max K(e) is 2023-06-30 and the reserve
  min K(e) is 2023-07-03.
- **Unchanged:** K-PIT `190d53f0…f6a2`; the Campaign-1 ledger, `report.json` and
  `report.md`. Only the new `derived/edgar-<acq_id>/` and
  `reserve_sealed/edgar-<acq_id>/` directories were created.
- **Resources:** write build 128 s wall, peak RSS 4.0 GB.

## Non-claims

- **What event dates are:** they are SEC acceptance facts. EV-T is a sanity check, not
  proof that the market learned the information at K(e). [A-EVT] remains an assumption.
- **Not certified:**
  - issuer-identity knowledge timing;
  - the ended-stratum mapping beyond the IM-P sample;
  - the economic content of events.
- **Mapping coverage is not uniform:** unmapped keys (98) are concentrated in ended
  keys, so non-coverage may be outcome-correlated. They are retained and typed.
- **Not built:** no M1b sensitivity model or event-expression class exists yet. Using
  events in a factor needs M1b plus a P-Q v2.3 grammar extension, each frozen separately.
- **Admission scope:** development use only. It makes no alpha, Campaign, reserve or
  K-PIT claim.

## Disposition

**DATASET v1.3 EDGAR EVENT LAYER — ADM-13 PASS (IM-P PASS, EV-C PASS, EV-T PASS) — ADMITTED FOR DEVELOPMENT USE**

K-PIT ARTIFACT registration of v1.3 is **not** performed; it needs its own authorization.
