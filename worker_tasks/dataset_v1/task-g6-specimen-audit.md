# G6 — Dataset-v1 gate-6 corporate-action specimen audit (Dataset v1.1) — FROZEN BEFORE AUDIT EXECUTION

- **Status:** FROZEN BEFORE AUDIT EXECUTION (2026-09-30), under the user's
  "GATE-6 CORPORATE-ACTION SPECIMEN AUDIT — PLANNING / SPEC FREEZE AUTHORIZED".
  - No audit outcome, specimen, specimen magnitude or external document was
    observed before this freeze.
  - The only production facts computed during planning were structural: the
    population count, per-year counts, the per-key count distribution, the
    population hash (§2) and issuer-name coverage (2,507 of 2,674 suspect
    keys).
- **Every step below needs its own separate authorization:**
  - G6-SW, implementing and merging the software;
  - G6-E1, offline selection and fetch plan;
  - G6-E2, SEC document retrieval over the network;
  - G6-E3, fact extraction, classification and decision.
- **Authority, in precedence order** (on any conflict, stop and report):
  1. `docs/discovery_dataset_v1_contract.md` §§8, 11.2, 12, 14, 16 (gate 6),
     18 — `d03f35549777e5a94046f06dd0621acfc3a1dedb7a4b40adc55fdae911fa80e3`;
  2. `docs/discovery_dataset_v1_1_gate6_audit_clarification.md` (cab82e0) —
     `0b529dca209b4fa9f54f9662bb3d9205f07b0056232532b2a529a3a513f43789`;
  3. `docs/discovery_dataset_v1_1_classification_amendment.md` (b6225b6) —
     `f89a7128bb8113c6978fc6bca8bcaf6048cd0c7db1fe5ac2aaffa1c7d519c623`;
  4. `worker_tasks/dataset_v1/task-d5-qc.md` §§3, 5, 6 (9b52b49) —
     `7ad986ef46384f5cc1052d00b4b39a2e4b444dd1ef448aeea33cb1c3bacd48f8`;
  5. `docs/discovery_dataset_v1_1_d7c_clarification.md` (839d4b0) and the
     addendum (1556167), for the D7c artifact semantics.
- **Owned files (new):**
  - `smart_beta/dataset_v1/gate6_audit.py`: population, strata, sampling,
    issuer mapping, fetch plan, fact validation, rule engine, decision and
    artifact;
  - `smart_beta/dataset_v1/gate6_sec_fetch.py`: the SEC document fetcher,
    bound to the fetch plan;
  - `tests/test_dataset_v1_gate6_audit.py`;
  - `tests/fixtures/dataset_v1/g6/**`, including `mutations.json`.
- **Must not modify** any existing file, including every D1–D8, D-SC, D7a,
  D7b and D7c file and every sealed phase.
- **G6-SW is offline:** 0 provider or network calls. Tests use a fake
  transport only.

**Pinned read-only code** (SHA-256; fail closed on mismatch):

| File | Use | SHA-256 |
|---|---|---|
| `smart_beta/dataset_v1/reserve_firewall.py` | `DevelopmentAccessor` for D3 | `4b8a74b5f950bd3bdf58155bbfae32401753f2bed45d5031860d6edb1d493e15` |
| `smart_beta/dataset_v1/classification.py` | `norm` only (name normalization) | `e03f5611bc91419c34429619ab62c8d89218dce1d693075c0fec3f1ad6a6bfda` |
| `smart_beta/vendors/tiingo/calendar_source.py` | `build_nyse_calendar` | `79bca35ddd46f87102e90f6ce0c2d51a8b0d16f1fe9caf56b7674b43fce8e23f` |

## 1. Production bindings (exact; fail closed; never "latest")

| Object | Binding |
|---|---|
| D5 development `day_flags` | `derived/qc/q-5d1028655f8c82e1/day_flags.parquet` — `f4010ebeabf5cfae2684f3658a5f19f922f15e2c8898410049f2148440cf7891`; `qc_hash 3d63e51b983096f0ae579cd1e382c613d9329d239c3f848cc785dee2d61cdfc3` |
| D3 development `observations`, `ca_events` | only through `DevelopmentAccessor`, `end ≤ 2023-06-30`: `557001d0e9c617befd3c036683f171094e86e19badb760098f40c08f3f56596e`, `afd4a2986c934d84c2b23eaf68f23ee18746e7c929c6b8625b6c9911e4a2b109` (build `n-f874d3ecb61374d4`) |
| D4 identity intervals | `derived/identity/i-a21f1e46621dee94/identity_assignment.parquet` — `fee4614009095773f75fb832343a5fa9b87b9de1cf363125567222b08fad3f8b` |
| D7c admitted returns | `derived/trusted_inputs/ti-aa23fabd3db4e595/daily_total_return.parquet` — `511bcf4ac4ea2bd02158438732e0fdf19c04d783b88ca7a01ff5a364ec8e688c`; columns `security_key` and `source_session` only |
| D-SC issuer names | `derived/classification/c-27b6dabd0db0cb15/issuer_mapping.parquet` — `dbfb424e50ccdc8127ba9064228738cfc105d5d4e7d4071694a27e28d3e127b5`; column `issuer_names_norm` only |
| SEC full-index | `dsc_evidence/sec-regindex-20260927/raw/master_YYYYQn.gz` for 1993Q1–2023Q2 only (122 files). Each file's SHA-256 must equal its entry in `sec_index_files_sha256` of `derived/classification/c-27b6dabd0db0cb15/dsc.json` (`dsc_hash 2ee4940c14a85048fddd9e3e675c3e9a6f40a11c8887192a5e61939adfc62b60`). Rows with `Date Filed` > 2023-06-30 are discarded at parse time. |
| Trusted calendar | `build_nyse_calendar(2015-01-02, 2023-06-30)` |

**Never opened:**
- `reserve_sealed/`, `raw/`, the reserve `day_flags`;
- any SEC index file from 2023Q3 onward;
- any Tiingo endpoint.

**Hard assertions:** every population date and every D3 read date is
< 2023-07-03.

## 2. Population (frozen)

- **Definition:** P = every row of the bound D5 development `day_flags` with
  `class_or_flag == "CORP_ACTION_SUSPECT"`. The suspect-day definition is D5's
  own and is not changed.
- **Frozen facts:**
  - |P| = **17,469**;
  - 2,674 distinct `security_key`s;
  - every row has disposition `UNCERTIFIED_SEMANTICS`;
  - no duplicate `(security_key, date)`;
  - maximum date 2023-06-30.
- **Population hash:** SHA-256 of UTF-8 lines `"{security_key}\t{date:%Y-%m-%d}\n"`,
  sorted by `(security_key, date)`:
  **`d2153329b8c13a5d20d2bbe351972e048c9dc421216051e08633ffda68fb2783`**.
  A mismatch in count or hash fails closed.
- **Magnitude ρ(t):** `close_t / close_p`, using raw D3 closes. p is the
  previous **usable** session of the same key inside its D4 assigned interval
  (present, on-calendar, not CORRUPT, not INTERVAL_MISMATCH), exactly as in the
  D5 §3 L1 rule.
  - Every population day must have ρ < 0.5 or ρ > 1.5. Otherwise the run
    fails closed with `PopulationInconsistencyError`.
  - ρ is computed transiently for band assignment. It is stored only in the
    `data_store` specimen file and never in Git.

## 3. Strata and allocation (frozen)

**Magnitude bands.** They are fixed in advance from split-ratio signatures,
not from the data:

| Band | Rule | Signature it targets |
|---|---|---|
| B1 `DOWN_EXTREME` | ρ < 1/3 | forward split ≥ 3:1, collapse |
| B2 `DOWN` | 1/3 ≤ ρ < 1/2 | 2:1 split, large drop |
| B3 `UP` | 3/2 < ρ ≤ 3 | reverse split ≤ 1:3, large rise |
| B4 `UP_EXTREME` | ρ > 3 | reverse split stronger than 1:3, extreme rise |

**Strata.** They are disjoint and exhaustive, and are assigned in this order:
- **S3 `NEAR_RECORDED_SPLIT`:** the D3 `ca_events` of the key's ticker contain
  an `action_type == "split"` event whose `effective_date` d ≠ t lies inside
  the key's D4 interval and within **±3 trusted sessions** of t. This is the
  targeted misdating probe. A split dated exactly t is impossible by the D5
  definition and fails closed.
- **S1 `CONSUMED`:** the day is not in S3, and D7c `daily_total_return`
  contains `(security_key, source_session = t)`. These days reach the Phase-6
  inputs.
- **S2 `NOT_CONSUMED`:** every other day. These are masked instruments and
  non-common or policy-excluded days.

**Allocation.** At most 180 specimens:

| Stratum | Allocation |
|---|---|
| S3 | 20 in total (bands ignored) |
| S1 | 30 per band (B1–B4) = 120 |
| S2 | 10 per band = 40 |

**Why these sizes.** They are sized to detect systematic failure modes, not
to estimate an error rate:
- 30 draws in an S1 band see at least one instance of a mechanism present in
  ≥ 10% of that band's days with probability 1 − 0.9³⁰ = 0.958.
- 120 S1 draws detect a mechanism present in ≥ 2.5% of consumed suspect days
  with probability 1 − 0.975¹²⁰ = 0.952.
- 40 S2 draws detect ≥ 7.2% with probability 0.95.
- 20 S3 draws detect a misdating mechanism present in ≥ 14% of near-split
  days with probability 0.95.

**Dividend proximity is not a stratum.** Regular dividends fall within ±3
sessions of many days and carry no signature of a > 50% move. Recorded
dividend proximity is kept as a covariate (§7).

## 4. Deterministic sampling algorithm `g6-specimen-v1` (frozen)

- **Rank:** `rank(K, t) = SHA-256("g6-specimen-v1|" + security_key + "|" + t.isoformat())`,
  compared as a hex string. The seed string is frozen, and changing it creates
  a new audit version.
- **Cell order:** S3; then S1 B1, B2, B3, B4; then S2 B1, B2, B3, B4.
- **Within a cell:**
  - group the candidates by calendar year of t, and sort each year's list by
    rank;
  - visit the years in ascending order, cycling. At each visit take the
    lowest-ranked candidate whose `security_key` has not been selected in
    **any** cell;
  - skip a year once it is exhausted, and stop when the allocation is filled
    or every year is exhausted.
- **Per-key cap:** 1 specimen per `security_key` across the whole specimen.
- **Small cells:** a cell with fewer eligible candidates than its allocation
  is taken in full (census). The shortfall is recorded and **never
  reallocated**.
- **No replacement:** a selected specimen is never replaced or dropped for
  any reason, including missing evidence. Selection takes no evidence, fact
  or outcome input. The API makes this impossible, and a test enforces it.
- **Specimen hash:** SHA-256 of canonical JSON of the ordered list
  `[selection_order, stratum, band, security_key, t]`, plus the algorithm id,
  seed, allocation and `population_sha256`.

## 5. Issuer mapping and fetch plan (offline, G6-E1)

**Names:** the key's `issuer_names_norm` from D-SC. An empty list gives
`UNRESOLVED(NO_ISSUER_NAME)`.

**Index:** parse the bound master files as in D-SC spec §2: gzip, latin-1,
data rows after the first `-----` line, `CIK|Company Name|Form Type|Date
Filed|Filename`. Keep only rows with `Date Filed` ≤ 2023-06-30.

**Mapping:**
- Candidate CIKs are those that carry, in any kept row, a Company Name with
  `classification.norm(name)` equal to any of the key's names.
- If there is more than one candidate, keep those with at least one filing in
  [t − 400 days, min(t + 30 days, 2023-06-30)].
- Exactly one remaining CIK is accepted. Zero candidates gives
  `UNRESOLVED(NO_ISSUER_MAPPING)`; more than one gives
  `UNRESOLVED(AMBIGUOUS_ISSUER_MAPPING)`.

**Event window:** filings of the mapped CIK with `Date Filed` in
[t − 60 days, min(t + 30 days, 2023-06-30)]. Forms are ranked by priority:

| Priority | Form types (exact index strings) |
|---|---|
| 1 | `8-K`, `8-K/A`, `6-K`, `6-K/A` |
| 2 | `25-NSE`, `25`, `15-12B`, `15-12G`, `SC TO-T`, `SC 14D9`, `DEFM14A`, `425`, `S-4`, `S-4/A`, `F-4` |
| 3 | `DEF 14A`, `DEF 14C`, `PRE 14A`, `PRE 14C`, `DEFA14A` |
| 4 | `10-12B`, `10-12B/A`, `10-12G`, `424B1`, `424B2`, `424B3`, `424B4`, `424B5` |
| 5 | `10-Q`, `10-Q/A`, `10-K`, `10-K/A`, `20-F`, `40-F` |

- **Selection:** order by (priority, |Date Filed − t| in days, Date Filed,
  Filename) and take at most **12** filings per specimen.
- **No filings in the window** gives `UNRESOLVED(NO_FILINGS_IN_WINDOW)`.

**Fetch plan and pre-network commit:**
- The fetch plan is the set of `(specimen id, CIK, filename)` rows. Its hash
  is recorded.
- Before any network request, the planner commits a data-free manifest to
  Git with no keys, dates, prices or ratios:
  `docs/discovery_dataset_v1_1_gate6_specimen_manifest.md`. It holds
  `population_sha256`, `specimen_sha256`, `fetch_plan_sha256`, per-cell
  counts, shortfalls and mapping-state counts.

## 6. Evidence hierarchy (frozen)

| Tier | Source | Facts it can establish | Coverage | Identifiers | Stability | Primary? | Network |
|---|---|---|---|---|---|---|---|
| E1 | SEC EDGAR filings (complete submission `.txt` from `https://www.sec.gov/Archives/<Filename>`), filed ≤ 2023-06-30 | split or reverse-split ratio and market-effective date; cash or special distribution and ex-date; spin-off, merger or exchange conversion, rights, reclassification; dated material events; ticker assignment; stated closing prices | 1993 onward (index); every period in scope; US filers and foreign filers (6-K, 20-F) | CIK, through §5 mapping | immutable accession-numbered filings | **primary, independent of Tiingo** | yes: public, no credential, SEC fair-access User-Agent with a contact address |
| E1-idx | SEC full-index (already recorded, hash-bound) | issuer name ↔ CIK; the filing inventory in a window | 1993Q1–2023Q2 used | CIK, company name | hash-bound local copy | primary (index) | **no** (already recorded) |
| C | Tiingo and derived Dataset-v1 data (D3 `ca_events`, raw closes, D5 flags, D7c admission) | the recorded event dates and factors; ρ | — | security_key, ticker | bound hashes | **context only; never independent** | no |

**Considered and excluded from v1 of this audit:**
- **Exchange corporate-action notices** (Nasdaq Daily List, NYSE notices):
  stable, complete historical retrieval is not established, and access may
  need a subscription.
- **A second price vendor:** none is recorded. Adding one is a new-provider
  entitlement and cost decision.
- **News and press archives:** not stable, not primary.

**Consequence:** a pure bad print with no explanatory filing ends
UNRESOLVED. If the result is INCONCLUSIVE, a follow-up audit adding an
independent price source must be separately frozen and authorized (§11).

**Tiingo self-certification is forbidden (clarification §5):**
- Every non-UNRESOLVED outcome requires at least one E1 filing reference
  (accession number plus cached SHA-256).
- A fact that cites a non-E1 source is rejected.
- Tiingo context is used **only** to decide OMITTED vs MISDATED once an E1
  fact has established the event.

## 7. Facts and extraction procedure (G6-E3)

**Two blind extractions:**
- The worker records one fact set per specimen in `facts_worker.jsonl`.
- The planner records an independent fact set, without seeing the worker's
  facts, in `facts_planner.jsonl`.
- Both read only the cached, hash-verified E1 documents.
- Each fact carries: `specimen_id`, `accession`, `form`, `date_filed`,
  `doc_sha256`, a `locator` (the document name inside the submission) and a
  verbatim `quote` of at most 300 characters.

**Fact types (closed list):**

| Fact | Fields |
|---|---|
| `TICKER_LINK` | ticker as written, issuer name as written |
| `TICKER_REASSIGNMENT` | date, from issuer, to issuer |
| `SPLIT` | f = new shares per old share (reverse 1:10 gives f = 0.1); `market_effective_date` (first split-adjusted trading session) |
| `CASH_DISTRIBUTION` | amount per share (USD), `ex_date` |
| `COMPLEX_ACTION` | type ∈ {`SPINOFF`, `IN_KIND_DISTRIBUTION`, `RIGHTS_OFFERING`, `MERGER_CONVERSION`, `SHARE_CLASS_EXCHANGE`, `UNIT_SEPARATION`}; `effective_date` |
| `MATERIAL_EVENT` | type (see §8 table); `event_date`; timing ∈ {`BEFORE_OPEN`, `DURING_SESSION`, `AFTER_CLOSE`, `UNSTATED`} |
| `STATED_CLOSE` | date, price as stated |

**Covariates (not facts; never outcome-determining):**
- `TICKER_LINK` present or absent;
- recorded Tiingo dividend within ±3 sessions;
- recorded Tiingo split within ±5 sessions (offset);
- year;
- D-SC class context.

## 8. Rule engine (mechanical; frozen)

Notation: t is the suspect session, p the previous usable session (§2), and
the move window is W = (close(p), close(t)]. A date is "in W" if it is a
calendar date in (p, t]. For events, timing narrows the boundaries:
- the event date is p with timing `AFTER_CLOSE`; or
- the event date is t with timing ≠ `AFTER_CLOSE`; or
- the event date is a non-session date strictly between p and t; or
- the event date is p or t with timing `UNSTATED`.

Steps are evaluated in order. The first step that decides, decides.

0. **Mapping.** A §5 mapping failure gives `UNRESOLVED` with the §5 reason.
   Every planned filing unretrievable gives `UNRESOLVED(EVIDENCE_UNRETRIEVABLE)`.
   A request cap reached before the specimen's filings were fetched gives
   `UNRESOLVED(REQUEST_CAP_REACHED)`.
1. **Identity.**
   - A `TICKER_REASSIGNMENT` whose date is in W gives
     `PRICE_OR_IDENTITY_ERROR(IDENTITY_MISMATCH)`.
   - A `STATED_CLOSE` for date t or p whose ratio to the D3 raw close of that
     date is outside [0.8, 1.25] gives
     `PRICE_OR_IDENTITY_ERROR(PRICE_CONTRADICTED)`.
2. **Mechanical explanations.** Collect every candidate:
   - **(a)** a `SPLIT` with `market_effective_date` in (p, t] and a = ρ·f in
     [0.5, 1.5];
   - **(b)** a `CASH_DISTRIBUTION` with `ex_date` in (p, t], amount <
     close_p, and a = ρ·close_p/(close_p − amount) in [0.5, 1.5];
   - **(c)** a `COMPLEX_ACTION` with `effective_date` in (p, t].

   Then:
   - Candidates from more than one of (a), (b) and (c), or two `SPLIT` or two
     `CASH_DISTRIBUTION` facts with different values, give
     `UNRESOLVED(EVIDENCE_CONFLICT)`.
   - **(a) alone:**
     - if D3 `ca_events` for the ticker, inside the key's interval, has a
       `split` at d ≠ t within ±5 trusted sessions whose factor equals the
       fact's f to a relative 1e-6 →
       `CERTIFIED_FIELD_OMISSION(MISDATED_SPLIT)`, recording the offset d − t;
     - otherwise `CERTIFIED_FIELD_OMISSION(OMITTED_SPLIT)`.
   - **(b) alone:** the same test against D3 `dividend` events (amount equal
     to a relative 1e-6) gives `MISDATED_CASH_DISTRIBUTION` or
     `OMITTED_CASH_DISTRIBUTION`.
   - **(c) alone:** `COMPLEX_CA_OUTSIDE_CERTIFIED_SCOPE(<type>)`.
3. **Documented market move.** A `MATERIAL_EVENT` in W whose type allows the
   direction of ρ gives `GENUINE_MOVE_DOCUMENTED(<type>)`:

   | Type | Allowed direction |
   |---|---|
   | `CLINICAL_OR_REGULATORY_RESULT` | either |
   | `ACQUISITION_OF_ISSUER_ANNOUNCED` | up |
   | `TRANSACTION_TERMINATED` | either |
   | `BANKRUPTCY_OR_RESTRUCTURING` | down |
   | `GOING_CONCERN_OR_DEFAULT` | down |
   | `EQUITY_OFFERING_PRICED` | down |
   | `EARNINGS_OR_GUIDANCE` | either |
   | `DELISTING_OR_DEFICIENCY_NOTICE` | down |
   | `STRATEGIC_TRANSACTION_OR_CONTRACT` | either |
   | `LITIGATION_OR_ENFORCEMENT_OUTCOME` | either |
   | `SPAC_COMBINATION_EVENT` | either |

   Any other type, or a direction the table does not allow, does not decide.
4. **Otherwise** `UNRESOLVED(NO_EXPLANATORY_FILING)`.

**Final outcome per specimen:**
- Run the engine separately on the worker facts and on the planner facts.
- If outcome and reason agree, that is the final outcome.
- Otherwise the final outcome is `UNRESOLVED(REVIEWER_DISAGREEMENT)`.
- No other adjudication exists.

**Outcome vocabulary:** each specimen gets exactly one outcome.

| Outcome | Maps to | Meaning under Dataset-v1 semantics |
|---|---|---|
| `GENUINE_MOVE_DOCUMENTED` | plausible real market move | Supports the current "usable genuine move" treatment. The price level itself is not verified |
| `COMPLEX_CA_OUTSIDE_CERTIFIED_SCOPE` | explained corporate action (complex) | A known §18 nonclaim; not a certified-path error |
| `CERTIFIED_FIELD_OMISSION` | vendor/data error | Split or cash distribution missing or misdated in Tiingo, so the certified return is wrong |
| `PRICE_OR_IDENTITY_ERROR` | vendor/data error | Series identity or price contradicted by primary evidence |
| `UNRESOLVED` | unresolved | Kept as it is; never forced |

`DATA_ERROR = {CERTIFIED_FIELD_OMISSION, PRICE_OR_IDENTITY_ERROR}`.

## 9. Gate-6 decision rule (frozen; mechanical)

Let:
- n₁ be the number of S1 specimens and r₁ the resolved (non-UNRESOLVED) S1
  specimens;
- n_b and r_b be the same counts for each S1 band b.

Then:
1. **GATE 6 SPECIMEN AUDIT HOLD — REMEDIATION REQUIRED** if any specimen, in
   any stratum, has a DATA_ERROR outcome.
   - **Why a single error is enough:** each confirmed DATA_ERROR proves that a
     defect mechanism exists in the certified return path or in identity. The
     specimen cannot bound its extent. Whether to remediate is a separate
     decision (clarification §4).
2. Otherwise **GATE 6 SPECIMEN AUDIT INCONCLUSIVE** if either:
   - r₁ < min(59, ⌈n₁/2⌉), because below 59 resolved S1 draws the audit loses
     95% detection of a 5%-prevalence mechanism (1 − 0.95⁵⁹ = 0.952); or
   - for any S1 band, r_b < min(16, ⌈n_b/2⌉), because below 16 it loses 80%
     detection of a 10%-prevalence mechanism within that band
     (1 − 0.9¹⁶ = 0.815).
3. Otherwise **GATE 6 SPECIMEN AUDIT PASS**.

**Escalation tags.** These attach to a HOLD. They classify it as
`SYSTEMATIC` or `ISOLATED` for remediation planning and never change the
decision. `SYSTEMATIC` applies if any of these holds:
- **T1:** ≥ 2 `CERTIFIED_FIELD_OMISSION` with the same reason;
- **T2:** ≥ 2 `IDENTITY_MISMATCH`;
- **T3:** ≥ 2 `MISDATED_*` with the same signed offset;
- **T4:** ≥ 2 DATA_ERRORs in the same calendar year and band;
- **T5:** a DATA_ERROR in S3 together with a DATA_ERROR of the same reason in
  S1 or S2.

Otherwise the HOLD is `ISOLATED`.

**Always reported, with no effect on the decision:**
- counts by stratum × band × outcome × reason;
- UNRESOLVED counts by reason, and their concentration by year and band;
- Clopper–Pearson 95% intervals for the `COMPLEX_CA_OUTSIDE_CERTIFIED_SCOPE`
  and UNRESOLVED shares within S1;
- S2 and S3 resolution rates;
- covariates.

**No further sampling** follows any result. INCONCLUSIVE never authorizes
resampling this population. A follow-up audit is a new version (for example
`g6-specimen-v2` with an independent price source), frozen and authorized
separately. The v1 results are kept.

## 10. SEC retrieval protocol (G6-E2; network; separate authorization)

- **Allowed requests:** `GET https://www.sec.gov/Archives/<Filename>`, and
  only for Filenames present in the hash-verified fetch plan. The host is
  exactly `www.sec.gov` and the path prefix `/Archives/edgar/data/`. Any other
  URL is refused before a socket opens.
- **User-Agent:** `SmartBeta-Gate6-Audit <contact>`.
  - The contact is read from the environment variable `SEC_CONTACT`.
  - A contact address must be separately authorized for this audit. The
    address approved for the 2026-09-27 SEC probes is not automatically
    reused.
  - The value is never written to logs, artifacts, reports or Git; the
    artifact records `contact: REDACTED`.
- **Limits:**
  - at most 4 requests per second;
  - at most 1 GET per planned filing, plus retries;
  - retries: 429, 5xx and timeouts get at most 2 retries (backoff 2 s, then
    8 s). 403 and 404 are not retried; the filing is marked unretrievable;
  - a hard cap of **2,500** requests. At the cap the run stops cleanly and
    the remaining filings are marked `REQUEST_CAP_REACHED`.
- **Cache:** atomic writes to `<audit dir>/evidence/<accession>.txt` with
  SHA-256. A resumed run skips verified files and re-fetches a corrupted file
  once.
- **Logging:** `retrieval_log.jsonl` records URL, status, bytes, SHA-256,
  attempt and UTC time.
- **Credentials:** no credential is read or needed. Every run scrubs the
  project's provider variables process-locally (`TIINGO_API_KEY`,
  `TUSHARE_PROXY_TOKEN`, `TUSHARE_BASIC_PROXY_TOKEN`, `TUSHARE_API_TOKEN`,
  `DEEPSEEK_API_KEY`, `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`,
  `OPENAI_API_KEY`).
- **Expected volume:** at most 180 × 12 = 2,160 filings.

## 11. Artifact and provenance

- **Location:** `data_store/discovery_dataset_v1/derived/qc_audit/<g6_id>/`,
  outside Git. `g6_id = "g6-" +` the first 16 hex characters of the SHA-256
  of canonical JSON of the §1 bindings plus the code SHA-256.

| File | Contents |
|---|---|
| `population.json` | bindings, `population_sha256`, count, per-year and per-stratum × band counts |
| `specimen.parquet` | `specimen_id`, `selection_order`, `stratum`, `band`, `year`, `security_key`, `ticker`, `t`, `p`, `rho` (float64), `rank_hex`, `near_split_offset` (nullable), `dividend_within_3` (bool) |
| `fetch_plan.parquet` | `specimen_id`, `mapping_state`, `cik` (nullable), `accession`, `form`, `date_filed`, `filename`, `priority` |
| `specimen.json` | algorithm id, seed, allocation, shortfalls, `specimen_sha256`, `fetch_plan_sha256` |
| `evidence/`, `retrieval_log.jsonl` | cached E1 filings and their log (G6-E2) |
| `facts_worker.jsonl`, `facts_planner.jsonl` | the two blind fact sets (G6-E3) |
| `outcomes.parquet` | per specimen: the worker and planner outcomes and reasons, the final outcome and reason, and the deciding fact references |
| `g6_audit.json` | every hash (population, specimen, fetch plan, evidence set, the two fact sets, outcomes); counts and reconciliation; intervals; decision; tags; procedure metadata (worker runtime and model, Herdr workspace, planner model, protocol version `g6-protocol-v1`); the certification text |

- **Determinism:** outputs are deterministic and carry no timestamps, except
  the timestamps in `retrieval_log.jsonl`, which are excluded from every hash.
  No secret material is stored.
- **Git:** only two data-free documents are committed: the pre-network
  specimen manifest (§5) and `docs/discovery_dataset_v1_1_gate6_audit_result.md`
  (hashes, counts, decision and tags). No keys, dates, prices, ratios or
  quotes go into Git (contract §14).

## 12. Knowledge-PIT

No record is needed and none is authorized (clarification §9).

## 13. Required tests (synthetic; socket guard; credential sentinel) — 60

**Population and bindings**
1. P equals the fixture `day_flags` CORP_ACTION_SUSPECT rows, and the
   population hash matches the canonical rule.
2. A reserve-dated row (≥ 2023-07-03) in the input fails closed.
3. A `day_flags` SHA-256 mismatch fails closed.
4. Adding, dropping or changing one row changes the population hash, and a
   bound hash mismatch fails closed.
5. A population day with ρ inside [0.5, 1.5] raises
   `PopulationInconsistencyError`.
6. ρ uses the previous *usable* session, skipping VENDOR_MISSING, CORRUPT and
   INTERVAL_MISMATCH.
7. D3 is read only through `DevelopmentAccessor` with `end ≤ 2023-06-30`, and
   an audit hook sees no `reserve_sealed`, `raw` or reserve `day_flags` open.

**Strata**
8. Band boundaries are exact: 1/3 → B2, 1/2 is outside P, 3/2 is outside P,
   3 → B3, 3+ε → B4.
9. S3 uses ±3 **trusted sessions**, not calendar days, and a split dated t
   fails closed.
10. S1 membership equals D7c `(security_key, source_session)` exactly.
11. The strata are disjoint and exhaustive, and S3 takes precedence.

**Sampling**
12. Two runs give identical specimens, including under `TZ=Asia/Tokyo LC_ALL=C`.
13. Rank vectors equal the SHA-256 of `seed|key|date`.
14. The seed is pinned, and a changed seed is rejected by the binding.
15. Year round-robin order within a cell.
16. At most 1 specimen per key across all strata.
17. Cell processing order is S3, then S1 B1–B4, then S2 B1–B4.
18. A small cell becomes a census with its shortfall recorded and not
    reallocated.
19. Cap-induced exhaustion is recorded as a shortfall.
20. Selection is independent of outcomes: the API accepts no evidence input,
    and outcome files present in the directory do not change the specimen.
21. A duplicate specimen entry is impossible, and an injected duplicate fails
    closed.
22. `specimen_sha256` and `fetch_plan_sha256` are verified at G6-E2 and G6-E3
    start, and a mismatch fails closed.

**Mapping and fetch plan**
23. Unique name→CIK mapping; zero candidates → `NO_ISSUER_MAPPING`; several
    candidates → the activity-window tie-break, then `AMBIGUOUS_ISSUER_MAPPING`;
    empty names → `NO_ISSUER_NAME`.
24. Index rows with `Date Filed` > 2023-06-30 and files from 2023Q3 onward are
    never used.
25. Event-window bounds, priority table, ordering and the 12-filing cap;
    `NO_FILINGS_IN_WINDOW`.

**Facts**
26. Schema validation: closed fact types and required fields.
27. A fact citing a non-E1 source is rejected (Tiingo self-certification).
28. A fact document with `date_filed` > 2023-06-30 is rejected.
29. A `doc_sha256` that differs from the cache is rejected.
30. A fact for a specimen not in the specimen set is rejected.

**Rule engine**
31. Reverse split f = 0.1, ρ = 9.6, effective date in (p, t] →
    `OMITTED_SPLIT`.
32. The same, with a D3 split at t + 1 with equal f → `MISDATED_SPLIT`, offset
    +1.
33. A split effective outside (p, t] → no explanation.
34. A split with a outside [0.5, 1.5] → no explanation.
35. Omitted and misdated cash distributions.
36. A spin-off effective in the window → `COMPLEX_CA_OUTSIDE_CERTIFIED_SCOPE`.
37. Timing rules: `AFTER_CLOSE` on p decides; `AFTER_CLOSE` on t does not;
    weekend-gap and `UNSTATED` cases.
38. Direction table: `BANKRUPTCY_OR_RESTRUCTURING` with ρ > 1.5 does not
    decide, giving `UNRESOLVED(NO_EXPLANATORY_FILING)`.
39. An unlisted event type does not decide.
40. `TICKER_REASSIGNMENT` in W → `IDENTITY_MISMATCH`, overriding a matching
    split.
41. A `STATED_CLOSE` outside [0.8, 1.25] → `PRICE_CONTRADICTED`.
42. Explanations from two of (a), (b), (c) → `EVIDENCE_CONFLICT`.
43. Two `SPLIT` facts with different f → `EVIDENCE_CONFLICT`.
44. Worker/planner disagreement → `REVIEWER_DISAGREEMENT`; agreement → that
    outcome.
45. Tiingo context alone (D3 events only, no E1 fact) never yields a
    non-UNRESOLVED outcome.
46. Every mapping and retrieval failure reason from step 0 is reachable and
    exact.

**Decision**
47. One DATA_ERROR in S2 alone gives HOLD.
48. Each tag T1–T5, and `ISOLATED`, at its boundary.
49. INCONCLUSIVE boundaries for r₁ (58 vs 59), for r_b (15 vs 16), and the
    ⌈n/2⌉ form when cells are short.
50. PASS when there is no error and the thresholds are met; UNRESOLVED
    specimens stay in every count.
51. No API converts or drops an UNRESOLVED result.
52. Reconciliation: stratum × band × outcome counts sum to the specimen size,
    and a mismatch fails closed.
53. Artifacts are deterministic with no timestamps. The Git-summary generator
    emits no key, date, price, ratio or quote.
54. The certification text and nonclaims are exact, and the CLI prints only
    ids and hashes.

**Fetcher (fake transport)**
55. Host and path allowlist; a Filename outside the fetch plan is refused
    before any socket opens.
56. The fetch-plan hash is verified before the first request, and a mismatch
    refuses everything.
57. The contact comes from `SEC_CONTACT`, is missing → refuse, and never
    appears in any log or artifact (byte scan).
58. Rate ≤ 4 requests/s under a fake clock; the retry policy (429, 5xx,
    timeout ≤ 2; 403 and 404 none); the 2,500 cap → `REQUEST_CAP_REACHED`.
59. Atomic cache with SHA-256; resume skips verified files; a corrupted file
    is re-fetched once.
60. Provider credential sentinels are never read or logged; no non-SEC socket
    opens; G6-SW imports no vendor client.

## 14. Mutations (all must be killed) — 42

- **Population and bindings:** M1 include reserve rows · M2 accessor `end`
  unbounded · M3 include `OHLC_INCONSISTENT` in P · M4 population hash over
  unsorted rows
- **Sampling:** M5 unseeded `random` · M6 time-based seed · M7 rank over key
  only · M8 no per-key cap · M9 per-key cap per cell only · M10 reallocate
  shortfalls · M11 replace UNRESOLVED specimens · M12 allow duplicates
- **Strata:** M13 S1 by D-SC class instead of D7c admission · M14 band
  boundary `<` ↔ `≤` · M15 S3 in calendar days · M16 drop year round-robin
- **Evidence and facts:** M17 accept a non-E1 fact · M18 accept `date_filed` >
  2023-06-30 · M19 skip `doc_sha256` check · M20 include index rows after
  2023-06-30 in mapping
- **Rule engine:** M21 split window includes p · M22 widen the a tolerance to
  [0.4, 1.6] · M23 never test MISDATED · M24 identity does not override · M25
  resolve conflicts by precedence · M26 disagreement resolved to the worker
  outcome · M27 accept unlisted event types · M28 ignore the direction table ·
  M29 accept `AFTER_CLOSE` on t · M30 Tiingo-only evidence yields
  `GENUINE_MOVE_DOCUMENTED`
- **Decision:** M31 ignore DATA_ERRORs in S2 · M32 r₁ threshold 58 · M33 drop
  UNRESOLVED from counts · M34 force PASS despite INCONCLUSIVE · M35 skip
  specimen-hash verification · M36 skip reconciliation · M37 the Git summary
  leaks ρ
- **Fetcher:** M38 allow URLs outside the plan · M39 write the contact to the
  log · M40 no rate limit · M41 retry 404 · M42 ignore the request cap

The worker commits `tests/fixtures/dataset_v1/g6/mutations.json` (42 ids,
each with file, old and new strings, and the killing test). The planner
re-executes every entry, adds its own conditional forms and a no-op control.

## 15. Execution sequence (each step separately authorized; STOP between)

1. **G6-SW:**
   - Pi worker through Herdr; isolated worktree and branch;
   - review; 60 tests; 42 mutations plus planner forms; a credential-scrubbed
     full suite;
   - merge `--no-ff`.
2. **G6-E1 (offline):**
   - population check, strata, specimen, mapping, fetch plan;
   - the planner independently re-derives the specimen and fetch-plan hashes;
   - commit the data-free specimen manifest **before any network request**.
3. **G6-E2 (network):** SEC retrieval under §10, once the network and
   contact address are authorized.
4. **G6-E3:**
   - worker facts, then blind planner facts;
   - engine, decision and artifact;
   - commit the data-free result document.
5. **Admission:** the aggregate Dataset-v1 admission review is rerun
   separately.

## 16. Certification and nonclaims

**Maximum claim on PASS:**
> The predeclared development-only gate-6 specimen audit (`g6-specimen-v1`,
> population `d2153329…2783`, specimen `<specimen_sha256>`) found no audit
> outcome requiring remediation under the frozen decision rule.

**Not claimed:**
- that every suspect observation is correct;
- that Tiingo's price level is verified for `GENUINE_MOVE_DOCUMENTED`
  specimens;
- universal corporate-action certification;
- certification of complex corporate actions;
- point-in-time corporate-action knowledge;
- any result for the reserve suspect days, which stay DEFERRED;
- reserve or confirmation eligibility;
- alpha, predictive validity, investability or production readiness.

## Report (G6-SW worker)

- files changed;
- the test and mutation counts, with test-to-item and mutation-to-test maps;
- the commit SHA;
- the line `sealed files modified: NONE`.
