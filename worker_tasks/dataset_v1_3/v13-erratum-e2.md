# V13-E2 — acceptance time from the EDGAR index header, not submissions JSON (FROZEN 2026-10-08; user-approved option (a); L3 budget pending)

- **Defect class:** **unexpected data semantics**, which contradicts the frozen design V§4
  (`c9daa54b…`). V§4 designated the submissions-JSON `acceptanceDateTime` as the
  knowledge-time source.
- **Evidence:** a planner probe, 2026-10-08. It made about 25 SEC requests and stored no
  raw responses. The sample was 20 Item 2.02 8-Ks, 4 each from 5 large filers. Results
  against the index-header `ACCEPTANCE-DATETIME` (EDGAR system time, US Eastern):

  | Reading | Matches |
  |---|---|
  | JSON read as UTC | 8 / 20 (2 filers) |
  | JSON read as ET wall clock | 0 / 20 |
  | JSON later than the true UTC by exactly one ET offset (+4 h EDT / +5 h EST) | the remaining 12 (3 filers) |

  For example, AAPL: header `2026-07-30T16:30:28` ET, JSON `2026-07-31T00:30:28Z`.
  - **Interpretation:** the JSON field is not a reliable timestamp. In this sample the
    error was always late, but 20 filings do not certify a direction.
- **Correction (binding; amends V§2, V§4 and plan §3–§5):**
  1. **The knowledge-time source** is the `<ACCEPTANCE-DATETIME>YYYYMMDDHHMMSS` value
     of `https://www.sec.gov/Archives/edgar/data/<cik>/<acc-no-dashes>/<acc>-index-headers.html`,
     read as an America/New_York wall-clock time. K(e) follows the unchanged V§4 rule.
  2. **The JSON value** is kept as `acceptance_json_raw`, together with a
     `json_discrepancy` class: `EXACT_UTC`, `PLUS_ONE_OFFSET` or `OTHER`. It is a
     recorded fact, never a time source. ADM-13 reports the census of these classes.
  3. **A missing or unparseable header** gives the event `k_session = null`, with the
     typed reason `EVENT_TIME_UNAVAILABLE`. The event is retained but never usable. No
     fallback to JSON or `filingDate`.
  4. **E-1L phase L3** fetches one index-header page per event-typed filing: an original
     8-K with Item 2.02, 10-Q, 10-K, 6-K, 20-F or 40-F, for a candidate CIK, with
     `filingDate` ≥ 2015-01-02.
     - The L3 request budget is set from the L1 filing census and needs the user's
       approval before L3 runs.
     - The rate cap stays at 8 requests/s.
  5. **Repairs, each by its original worker:**
     - **E-1-R1:** `build_l3_plan`, plus a header parser (strict 14-digit field;
       anything else → `None`);
     - **E-3-R1:** K(e) from the header time, plus the `acceptance_json_raw` /
       `json_discrepancy` columns and the `EVENT_TIME_UNAVAILABLE` path.
     - **E-4:** no change expected. If E-4 must change, STOP.
  6. **New mutations:**
     - E1-M5: the L3 plan omits a filing type;
     - E3-M6: JSON used when the header is missing;
     - E3-M7: the header parsed as UTC;
     - E3-M8: the discrepancy class computed with the wrong offset sign.

## Addendum (2026-10-08, after L1): real SEC schema facts + E-2-R1

**E-2 implementation defect:**
- `edgar_identity._view_from_payload` reads `entityName`, but the real submissions JSON
  key is **`name`**.
- **Consequence:** every current-name match fails, and on L1 data 362 of 471 ended keys
  became NO_CANDIDATE.
- **With the field corrected** (planner scratch estimate, no production change):
  - active keys: 1,443 / 1,450 MAPPED or FOREIGN_FILER;
  - ended keys: 357 MAPPED + 19 FOREIGN_FILER of 471.
- **Contributing cause:** the planner did not specify field names, and the worker
  invented the fixture schema.

**Binding real-schema facts** (from L1 raw data; workers must use exactly these):
- **Top-level keys:** `cik`, `entityType`, `sic`, `sicDescription`, **`name`**,
  `tickers`, `exchanges`, …, `formerNames`, `filings`. There is **no** `entityName`.
- **`formerNames` entries:** `{"name": str, "from": "YYYY-MM-DDT00:00:00.000Z", "to":
  "YYYY-MM-DDT00:00:00.000Z"}`.
- **`filings.recent` keys:** `acceptanceDateTime`, `accessionNumber`, `act`,
  `core_type`, `fileNumber`, `filingDate`, `filmNumber`, `form`, `isInlineXBRL`, `isXBRL`,
  `isXBRLNumeric`, `items`, `primaryDocDescription`, `primaryDocument`, `reportDate`,
  `size`. Paginated `files/*.json` carry the same arrays at the top level.
- **`filings.files` entries:** `{"name": "CIK##########-submissions-NNN.json",
  "filingCount", "filingFrom", "filingTo"}`.
- **`items`:** a comma-separated string, for example `"2.02,9.01"` or `"2.02"`.
- **Fixtures:** every test fixture of E-1/E-2/E-3 must use this exact schema, and each
  repair adds at least one fixture copied in shape (with synthetic values) from it.

**Repairs** (each by its task's worker role; the original Herdr sessions were closed after
merge, so each is relaunched with its task context):

| Repair | Scope |
|---|---|
| **E-1-R1** | L3 plan + header parser (above) |
| **E-2-R1** | read `name`; the real-schema fixture; mutation E2-M5 (`entityName` only → NO_CANDIDATE regression) |
| **E-3-R1** | header time source (above) + real-schema fixture |

**L3 scope:** the request budget is still **pending user approval** (see the planner
report). The scoped census after the E-2 fix is 164,403 event-typed filings within the
mapped intervals:

| Event type | Filings |
|---|---|
| 8-K 2.02 | 58,433 |
| periodic | 55,544 |
| 6-K | 48,797 |
| 20-F | 1,629 |
