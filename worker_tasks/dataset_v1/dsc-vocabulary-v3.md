# D-SC classification vocabulary — version `dsc-vocab-v3` (PLANNING; FROZEN BEFORE MEASUREMENT)

- **Status:** frozen and committed **before** the single v3 full-population
  structural measurement, per the user's v3 brief of 2026-09-27.
- **D-SC implementation is not authorized.**
- **Supersedes** `dsc-vocab-v2` (commit 0185630), which is kept as history.
- **Inputs:**
  - the archived Nasdaq Trader directory captures (`dsc_evidence/wayback-20260927/`);
  - the SEC quarterly full-index regulatory event table
    (`dsc_evidence/sec-regindex-20260927/`): 136 requests; 1993Q1–2026Q3;
    `regulatory_events.csv`, SHA-256 prefix `6ff336f9632d2aee`;
    `regulatory_cik_names.json`, prefix `c69db42a52a968d5`.
- **No new network data after this freeze.**

## 0. Two orthogonal dimensions

**A. SECURITY CLASS** of the traded instrument, per `security_key` and per f:
- `COMMON_STOCK(subtype ∈ {COMMON, ADR_ADS, REIT_COMMON})`;
- `NON_COMMON(reason)`;
- `CLASS_UNRESOLVED(reason)`;
- `EVIDENCE_CONFLICT(reason)`.

**B. ISSUER POLICY STATUS** (regulatory overlay), per `security_key` and
per f:
- `BDC`;
- `REGISTERED_FUND`;
- `ISSUER_STATUS_UNRESOLVED`;
- `NO_REGULATORY_EXCLUSION`.

`NO_REGULATORY_EXCLUSION` means that **no positive exclusion evidence** was
found. It is **not** a certification that the issuer is unrestricted, and
provenance records it as such. A BDC's instrument may correctly have
`security_class = COMMON_STOCK`. Regulatory status never alters the security
class.

**Conceptual D6 requirement (D-SC part only):**
`security_class(f) = COMMON_STOCK` **and**
`issuer_policy_status(f) ∉ {BDC, REGISTERED_FUND, ISSUER_STATUS_UNRESOLVED}`,
together with all other frozen D6 conditions.

## 1. Security class — string grammar (unchanged from v2 §1)

The v2 rules are carried forward verbatim:
- whole-name overrides (test, closed-end fund, BDC wording, ETF/ETN, fund,
  tracking, when-issued);
- candidate text, then the reference split, then the issuer-terminator cut
  within the pre-reference text;
- non-common instrument terms in the instrument segment take precedence;
- depositary evidence: bare ADS/ADR is CLASS_UNRESOLVED(BARE_ADS); a
  common/ordinary reference gives COMMON_STOCK(ADR_ADS); a preferred reference
  gives DEPOSITARY_PREFERRED;
- the REIT/SBI rule;
- possible tracking stock;
- common equity;
- generic, issuer-only and unrecognized fallbacks.

Directory flags (Test Issue = Y, ETF = Y) override the name of their own row.

## 2. Security class — point-in-time timeline

The v2 §4 rules apply, with one change (§2.2).

**2.1** `class_state(K, f)` uses only admissible explicit points dated ≤ f−1:
- same-date COMMON/NON disagreement: EVIDENCE_CONFLICT(CONCURRENT);
- three or more alternating runs: EVIDENCE_CONFLICT(ALTERNATING);
- two runs: the new state begins at its first observation;
- **last-known state:** the gap before a new-class observation keeps the
  contemporaneously last-known class. Later evidence never rewrites an
  earlier f.
- The static test-symbol list and applicable suffix apply as in v2 §2–3,
  including the **suffix contradiction rule**: a suffix together with a
  would-be COMMON state gives EVIDENCE_CONFLICT(SUFFIX_CONTRADICTION).

**2.2 When-issued correction (new).** An explicit `NON_COMMON(WHEN_ISSUED)`
point dated **on or after** any explicit COMMON point of the same key is a
**parallel/separate-line observation**:
- it is retained and flagged `WHEN_ISSUED_PARALLEL`;
- it does **not** enter the run sequence, so it cannot create a transition,
  alternation or concurrent conflict.

No evidence type in the admissible sources establishes that the key itself
became the when-issued instrument. A when-issued point that precedes every
COMMON point is an ordinary explicit NON_COMMON point.

## 3. Issuer policy status — regulatory overlay (new)

**3.1 Events.** Only documented SEC form events from the full-index table are
used. File-number prefixes are never used.

| Form (including `/A`) | Meaning (form text) | Effect |
|---|---|---|
| N-54A | election to be subject to §§55–65 (§54(a)) | BDC begins |
| N-54C | withdrawal of the election (§54(c)) | BDC ends; no unrestricted certification follows |
| N-8A | notification of registration (§8(a)) | REGISTERED_FUND begins |
| N-8F | application for deregistration only | **no effect**; REGISTERED_FUND continues (fail-closed) |

- The boundary is the index **Date Filed**. An event is admissible at f only
  if Date Filed ≤ f−1.
- An `/A` amendment is admissible positive evidence of the same base form at
  its own filing date.

**3.2 Issuer mapping (deterministic).**
- A key's issuer names are taken from its venue-admissible, interval-dated
  capture names:
  - dashed names: the text before the last `" - "`;
  - otherwise the text before the reference marker, up to and including the
    last issuer-terminator token, or else up to the first class word.
- Normalization removes only non-semantic differences:
  - lower-casing, `&` → `and`, and punctuation to spaces;
  - a trailing EDGAR state tag such as `/md/` and a leading `the`;
  - trailing tokens in {inc, incorporated, corp, corporation, co, company,
    ltd, limited, llc, lp, plc, sa, nv, ag, se, the}.
  - No fuzzy matching.
- Each regulatory CIK's name set is **every** company name that CIK carries
  in any index row from 1993Q1 to 2026Q3.
- **Match:** exact equality of a normalized key issuer name with any
  normalized name of a regulatory CIK.
  - One CIK: mapped.
  - More than one CIK: `ISSUER_STATUS_UNRESOLVED` for every f
    (AMBIGUOUS_MAPPING).
  - No CIK: no regulatory evidence, so `NO_REGULATORY_EXCLUSION`.

**3.3 Status at f**, for a mapped CIK, using events with Date Filed ≤ f−1:
1. `BDC` if the latest of {N-54A, N-54C} is an N-54A.
2. Otherwise `REGISTERED_FUND` if any N-8A exists (N-8F never terminates it).
3. Otherwise `ISSUER_STATUS_UNRESOLVED` (WITHDRAWN_NO_SUCCESSOR_STATUS) if the
   latest of {N-54A, N-54C} is an N-54C.
4. Otherwise `NO_REGULATORY_EXCLUSION`.

A reported BDC status takes precedence over REGISTERED_FUND; both are
excluded.

**3.4 Horizon gap (disclosed).**
- The earliest indexed event is 1994-01-06; paper-era filings are absent.
- A status established before electronic filing, with no later indexed
  N-54A, N-54C or N-8A, is **not detected**.
- No prior state is invented.

## 4. Preserved

- SEC XBRL-context extraction requirement (v1 §7).
- Exchange-mismatch rule: EXCHANGE_MISMATCH_REVIEW never certifies or
  excludes.
- Only `INTERVAL_ASSIGNED` keys are classified.
- No returns, values or performance are used.
- The unresolved residual is acceptable; no rule is chosen for coverage.

## 5. Measurement definition (single run)

**Security-class and overlay counts:** per key at C, and per formation date f
(the last NYSE session of each month, 2016-01 to 2026-08).

**Structural eligibility estimate** (not D6):
- security class COMMON_STOCK at f;
- overlay ∉ {BDC, REGISTERED_FUND, ISSUER_STATUS_UNRESOLVED} at f;
- listed at f−1 and f;
- listing age ≥ 252 sessions through f−1;
- at least 50 usable sessions in f−63…f−1;
- Q-D6-1 PIT missingness ≤ 5%.

Usable rows exclude D5 CORRUPT, INTERVAL_MISMATCH and off-calendar rows.
