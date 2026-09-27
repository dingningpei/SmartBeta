# D-SC — Security-classification evidence review and bounded metadata probe (plan)

- **Status:** PLAN ONLY. Nothing in this file has been executed. Running the
  public-evidence review and the probe requires the user's execution
  authorization. The authorization of 2026-09-27 approved *preparing* both
  plans.
- **Finding addressed:** SECURITY-CLASSIFICATION / IDENTIFIER-SEMANTICS
  (OPEN; blocks D6).
- **Ownership:** an independent gate before D6. It is not D4 (identity) and
  not D5 (data quality). Classification is an eligibility property (contract
  §3.2–3.3) of a supported-tickers row, i.e. of a `security_key`.
- **Contract:** frozen and unchanged by this work. Any stricter exclusion rule
  changes a §22 item and therefore creates a **new dataset version** (§19).
  That decision belongs to the user, at a methodology-freeze barrier.

## 1. What is already known (structural, offline)

- **Snapshot fields:** `ticker`, `exchange`, `assetType`, `priceCurrency`,
  `startDate`, `endDate`.
  - There is **no name field**.
  - `assetType` takes only `Stock`, `ETF` and `Mutual Fund`, and warrants,
    units, rights and preferreds appear as `Stock`.
- **The frozen §3.3 rule is written for dash-delimited segments.** NASDAQ
  identifiers of that kind appear without a dash (e.g. `ABLLW`).
- **5-letter alphabetic NASDAQ pool tickers, by last letter:**

  | Last letter | W | U | R | P | L | Z | O | A | Q | N | V | M | B | … |
  |---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
  | Count | 877 | 689 | 158 | 106 | 70 | 51 | 35 | 35 | 29 | 21 | 20 | 18 | 18 | … |

  The 4-letter base ticker exists in the snapshot for 750 / 551 / 119 / 77 of
  the W / U / R / P groups.
- **37 exchange test symbols are in the pool with OK data:** the `ATEST*`,
  `CTEST*`, `MTEST*`, `NTEST*` and `PTEST*` families and `ZBZZT`, `ZJZZT`,
  `ZVZZT`, `ZWZZT` and `ZXZZT`. §3 does not exclude test issues.
- **Space/slash identifiers:** 10 are `UNREQUESTABLE_IDENTIFIER` (e.g.
  `BC/PA`, `DTE 6.25`). No classification is inferred for them.
- **Spelling alone certifies nothing.** No rule is adopted from spelling
  unless authoritative documentation certifies it.

## 2. Part A — public-evidence review (read-only, unauthenticated)

**Sources to consult, each recorded with its URL, retrieval UTC time and the
SHA-256 of the saved copy.** Copies are stored outside Git under
`<data_store>/discovery_dataset_v1/dsc_evidence/<review_id>/`; only derived,
data-free summaries are committed.

| # | Source | Question it must answer |
|---|---|---|
| A1 | Nasdaq Trader symbology documentation for fifth-character identifiers | The authoritative meaning of each fifth letter (W, U, R, P, and also L, Z, O, N, M, Q, V, A, B, F, Y, …), whether each marks a non-common issue, and **since when**. Any convention changed during 2015–2026 must be dated |
| A2 | CQS/CTA and NYSE symbology suffix conventions | Suffixes for preferreds, warrants, rights, units and when-issued securities on NYSE, NYSE American and Arca, and confirmation that Tiingo's dashed forms (`-P-D`, `-WS`) correspond to them |
| A3 | Official exchange test-symbol lists (Nasdaq; NYSE/CTA) | The authoritative set or pattern of test symbols, to support a `TEST_ISSUE` exclusion |
| A4 | Nasdaq Trader symbol directories (`nasdaqlisted.txt`, `otherlisted.txt`, `nasdaqtraded.txt`) | Current security name, `Test Issue` flag and `ETF` flag. Use: measure the precision and recall of the A1/A2-certified syntax rule against *currently listed* pool tickers (a non-performance calibration). **Current listings only:** no historical claim is made from it |

**Firewall:** no price, volume, return or factor data is consulted.

**Output (report, data-free):**
- the certified syntax rules, each with its documentary citation and dates of
  validity;
- the calibration table from A4: rule class against directory name or flag,
  as counts;
- the residual set that cannot be classified by documented rule, which
  becomes `CLASS_UNRESOLVED`.

## 3. Part B — bounded Tiingo metadata specimen probe (authenticated)

**Purpose:** decide only whether `GET /tiingo/daily/{ticker}` (the existing
`TiingoClient.get_meta`) provides useful, non-performance classification
evidence, beyond the snapshot and Part A.

**Specimen set (predeclared; 19 tickers, chosen deterministically from the
snapshot and pool; no substitutions):**

| Group | Tickers | Why |
|---|---|---|
| Ordinary common and share class | `AAPL`, `A`, `BRK-B` | negative controls |
| NASDAQ fifth letter W / U / R / P | `AACIW`, `AACBU`, `AACBR`, `ACGLP` | the main suspect classes (first alphabetical single-row OK pool ticker with an existing base ticker) |
| NASDAQ fifth letter L / Z / O / Q | `AEPPL`, `AEPPZ`, `ACGLO`, `ACETQ` | letters beyond W/U/R/P (chosen by the same rule) |
| Exchange test symbols | `ZVZZT`, `NTEST` | the test-issue class |
| Dash-suffix calibration (outside the pool; known by the frozen rule) | `ABR-P-D`, `AAC-WS` | what the metadata shows for a known preferred and a known warrant |
| Unusual requestable identifier | `ARGD-1` | an unusual syntax that is requestable |
| Reused tickers | `ACI`, `AAAP`, `ABLLW` | shows that metadata describes the **current** security only |

**Rules:**
- **Hard cap: 20 authenticated requests in total**, including any retry.
  - One request per specimen.
  - A transport failure is recorded and not retried. Stop if 3 or more
    requests fail.
- Metadata endpoint only. **No `/prices` call and no fundamentals.** Only
  `name`, `description`, `exchangeCode`, `startDate`, `endDate` and `ticker`
  are read.
- **Current metadata is never historical identity evidence.** For a reused
  ticker it describes today's security. A metadata-derived class could only
  ever apply to a ticker whose snapshot rows are a single row (no reuse), and
  that restriction would be written into any later D-SC spec.
- The probe runs from a planner script outside the repository, with only
  `TIINGO_API_KEY` kept in its environment (every other credential scrubbed).
  The credential is never printed or persisted.
- **Recorded:** for each request, the UTC time, HTTP status, body SHA-256 and
  field names present. Bodies are saved outside Git under
  `<data_store>/discovery_dataset_v1/dsc_probe/<probe_id>/`. A data-free
  summary gives the request count and whether the classification evidence is
  useful.
- **No expansion.** A crawl of the pool (about 10,900 symbols) or of any set
  larger than these 19 requires a separate authorization.

**Predeclared usefulness criterion.** The endpoint is **USEFUL** only if both
of the following hold; otherwise it is **NOT USEFUL**, the probe stops, and
no further metadata calls are proposed:
1. For at least 6 of the 8 NASDAQ fifth-letter specimens, `name` or
   `description` contains explicit security-type text such as warrant, unit,
   right, preferred, depositary, notes, debenture or test.
2. None of the 3 negative controls contains such text.

The test-symbol and dash-calibration specimens are reported descriptively.

## 4. After Parts A and B (decision barrier; user)

The planner delivers a D-SC evidence report and a proposed classification
rule. Each class is a disposition per `security_key`:

- `COMMON_ELIGIBLE`;
- `EXCLUDED_NONCOMMON(reason, citation)`;
- `TEST_ISSUE`;
- `CLASS_UNRESOLVED`, which fails closed and is excluded and counted.

It also sets out two version options:

- **Keep v1 literal:** no rule change. The §17 "common-stock" wording cannot
  then be claimed, and the residual is disclosed.
- **Dataset v1.1:** a contract amendment under §19 adding the documented
  rules and the test-issue exclusion. The existing acquisition is reused as a
  superset, and the version and hash chain are recorded.

The D-SC implementation task spec is written only after the user's version
decision. D6 stays blocked until then.
