# D-SC classification vocabulary — version `dsc-vocab-v1` (PLANNING; frozen before measurement)

- **Status:** written and committed **before** any aggregate coverage
  measurement. It is the input to the review requested by the user on
  2026-09-27.
- **D-SC implementation is not authorized.** This document is the
  semantic contract a later D-SC task would implement.
- **Policy basis:** `dataset-v1-1-decisions.md` §1 (eligible classes and the
  REIT/SBI clarification) and the user's D-SC vocabulary brief of 2026-09-27.
- **Derivation:** the phrases below come from the actual interval-valid
  security-name strings in the archived Nasdaq Trader directory captures
  (1,509 distinct normalized class phrases), read against that policy.
  - Only the phrase inventory was consulted.
  - No per-key outcome was seen.
  - No rule was chosen to raise coverage.

## 0. States

| State | Meaning |
|---|---|
| `COMMON_STOCK(subtype)` | subtype ∈ {`COMMON`, `ADR_ADS`, `REIT_COMMON`}. The **only D6-eligible state** |
| `NON_COMMON(reason)` | reason ∈ {WARRANT, UNIT, RIGHT, PREFERRED, DEPOSITARY_PREFERRED, NOTE_OR_DEBT, ETF, FUND, CLOSED_END_FUND, BDC, LP_OR_MLP_UNIT, NONCOMMON_TRUST_OR_SBI, TRACKING_STOCK, TEST_ISSUE, WHEN_ISSUED, OTHER_NONCOMMON} |
| `CLASS_UNRESOLVED(reason)` | fail-closed |
| `EVIDENCE_CONFLICT` | fail-closed |

- The absence of NON_COMMON evidence never yields COMMON_STOCK.
- The absence of COMMON evidence never yields NON_COMMON.
- Ticker spelling never yields COMMON_STOCK.

## 1. String classification (one evidence string → one string-class)

All matching is case-insensitive, on whitespace-collapsed text.

**Step 1 — class phrase.**
- If the name contains `" - "`, the class phrase is the text after the
  **last** `" - "`. This is the Nasdaq "Issuer - Class" format; SEC titles are
  used whole. The phrase is read **head-first**: the earliest match in the
  step-3 list, by position in the phrase, decides.
- Otherwise (other-listed "Issuer Class" format), the whole name is read
  **tail-first**: the match with the greatest start position decides. This
  prevents issuer words (e.g. "Preferred Apartment Communities, Inc. Common
  Stock") from being read as the class.

**Step 2 — whole-string overrides**, checked in this order before step 3:

| # | Pattern (whole name) | Result |
|---|---|---|
| O1 | `\btest (stock\|issue\|symbol)\b` | NON_COMMON(TEST_ISSUE) |
| O2 | `\bclosed[- ]end fund\b` | NON_COMMON(CLOSED_END_FUND) |
| O3 | `\bbusiness development (company\|corporation)\b` | NON_COMMON(BDC) |
| O4 | `\bexchange[- ]traded (fund\|note)s?\b\|\betf\b\|\betn\b` | NON_COMMON(ETF) |
| O5 | `\bfund\b` | NON_COMMON(FUND) |
| O6 | `\btracking stock\b` | NON_COMMON(TRACKING_STOCK) |
| O7 | `\bwhen[- ](issued\|distributed)\b` | NON_COMMON(WHEN_ISSUED) |

**Step 3 — instrument patterns**, positioned as in step 1:

| Pattern | Result |
|---|---|
| `limited partner(ship)? interests?\|partnership units?\|\bcommon units?\b` | NON_COMMON(LP_OR_MLP_UNIT) |
| `units? of beneficial interest\|\btrust units?\b` | NON_COMMON(NONCOMMON_TRUST_OR_SBI) |
| `depositary shares?[^|]*?\bpreferred\b\|\bpreferred\b[^|]*?depositary` | NON_COMMON(DEPOSITARY_PREFERRED) |
| `\bpreferred\b\|\bpreference shares?\b\|trust preferred` | NON_COMMON(PREFERRED) |
| `\bnotes?\b\|\bdebentures?\b\|\bbonds?\b\|\bsenior\b\|\bsubordinated\b` | NON_COMMON(NOTE_OR_DEBT) |
| `\bwarrants?\b` | NON_COMMON(WARRANT) |
| `\bcontingent value rights?\b\|\brights?\b` | NON_COMMON(RIGHT) |
| `\bsub-?units?\b\|\bcorporate units?\b\|\btangible equity units?\b\|\bunits?\b` | NON_COMMON(UNIT) |
| `american deposit[ao]ry (shares?\|receipts?)\|\bads\b\|\badrs?\b\|sponsored adr` | COMMON_STOCK(ADR_ADS), **unless** the same phrase also matches `preferred` (already caught above) |
| `\bshares of beneficial interest\b\|\bbeneficial interest\b` | if the whole name also matches `\breit\b\|real estate investment trust` **and** `\bcommon\b`: COMMON_STOCK(REIT_COMMON). Otherwise CLASS_UNRESOLVED(SBI_AMBIGUOUS) (REIT/SBI rule B) |
| `\bseries [a-z] (?!common\b)[a-z][\w&\.\- ]* common (stock\|shares?)\b` | CLASS_UNRESOLVED(POSSIBLE_TRACKING_STOCK): a named stock group inside a series designation |
| `\b(new )?common (stock\|shares?)\b\|\bordinary shares?\b` | COMMON_STOCK(REIT_COMMON) if the whole name matches `\breit\b\|real estate investment trust`; otherwise COMMON_STOCK(COMMON) |

**Step 4 — no match.**
- A generic phrase (`\bsubordinate voting shares\b|\bclass [a-z] shares\b|\bshares\b|\bdepositary shares\b`) gives CLASS_UNRESOLVED(GENERIC_SHARES).
- An empty phrase or an issuer name only gives CLASS_UNRESOLVED(ISSUER_ONLY).
- Anything else gives CLASS_UNRESOLVED(UNRECOGNIZED).

**Examples:**

| Name | Result |
|---|---|
| "Apple Inc. - Common Stock" | COMMON |
| "XYZ Acquisition Corp - Class A Ordinary Shares" | COMMON (SPAC common is eligible only through explicit wording like this) |
| "… - Unit", "… - Warrant", "… - Right" | NON_COMMON |
| "Rayonier Inc. REIT Common Stock" | COMMON_STOCK(REIT_COMMON) |
| "Pennsylvania REIT Shares of Beneficial Interest" | COMMON_STOCK(REIT_COMMON) only if "common" also appears; otherwise CLASS_UNRESOLVED |
| "AT&T Inc." | CLASS_UNRESOLVED(ISSUER_ONLY) |

## 2. Directory flags (authoritative)

- A capture row with `Test Issue = Y` gives NON_COMMON(TEST_ISSUE).
- A capture row with `ETF = Y` gives NON_COMMON(ETF).
- **A flag overrides the name** from that same row. It is not a conflict.

## 3. Evidence admissibility

**Wayback directory evidence** is admissible for key K only when all of these
hold:
- the capture date lies in `[listed_start, listed_end]` of K;
- the symbol equals K's ticker, with `.` mapped to `-` for other-listed rows;
- the capture's venue matches K's exchange:
  - `nasdaqlisted` counts as NASDAQ;
  - for `otherlisted` / `nasdaqtraded`, exchange code N counts as NYSE, and A
    counts as NYSE MKT or AMEX;
  - Q counts as NASDAQ.

A capture that is symbol- and date-valid with an explicit class **but a
different venue** is `EXCHANGE_MISMATCH_REVIEW` evidence (§6).

**SEC evidence** (for D-SC; see §7) is admissible when:
- the filing date lies in the interval;
- `dei:TradingSymbol` equals the ticker **within the same XBRL context** as
  `dei:Security12bTitle`;
- `dei:SecurityExchangeName` in that context matches the venue. If it
  differs, §6 applies.

**Suffix exclusion evidence** (NON_COMMON only, never COMMON):
- a 5-letter NASDAQ symbol whose fifth letter has an unambiguous non-common
  meaning in Nasdaq's fifth-character table:
  - W: WARRANT;
  - R: RIGHT;
  - U: UNIT;
  - P, M, N, O: PREFERRED;
  - G, H, I: NOTE_OR_DEBT;
  - V: WHEN_ISSUED;
- a dashed symbol segment named in contract §3.3:
  - `-P…`: PREFERRED;
  - `-WS…` or `-W`: WARRANT;
  - `-U`: UNIT;
  - `-R`, `-RT`, `-R-W`: RIGHT;
  - `-WI`: WHEN_ISSUED.

Letters A, B, C, D, E, F, J, K, L, Q, S, T, X, Y and Z are **not** used,
because their meaning is a class, status or miscellaneous marker, or is
absent from the table.

**Authoritative test-symbol evidence:** symbols listed as test symbols in the
NYSE 2017-05-03 notice (NTEST, ATEST-G, ATEST-H, ATEST-L, PTEST-W, ZTEST,
ZVZZT) give NON_COMMON(TEST_ISSUE).

## 4. Per-source aggregation (repeated captures)

- Collect the string-classes of all admissible captures.
- **Ambiguous captures never negate explicit ones.**
- **Explicit classes:**
  - all COMMON: the source says COMMON (subtype: ADR_ADS or REIT_COMMON if
    any capture gives it, otherwise COMMON);
  - all NON_COMMON: the source says NON_COMMON (reason = the
    alphabetically first reason; all reasons recorded);
  - both COMMON and NON_COMMON: the source says **CONFLICT**.
- A single security_key represents one listed instrument, so no temporal
  reconciliation across the COMMON/NON boundary is admitted.
- Temporal differences **within** COMMON (e.g. "Class A Common Stock" then
  "Common Stock") are not conflicts.
- If no admissible capture is explicit, the source says UNRESOLVED (with the
  most frequent unresolved reason).

## 5. Cross-source precedence (deterministic)

Levels: **L1** SEC structured XBRL, **L2** Wayback directory, **L3** suffix and
test-symbol exclusion, **L4** current or supporting metadata (never decisive).

1. **Test flag or authoritative test symbol**, from any level: NON_COMMON(TEST_ISSUE).
2. **Explicit L1 and explicit L2 disagree** (COMMON against NON_COMMON):
   EVIDENCE_CONFLICT. A source-internal CONFLICT at L1 or L2 is also
   EVIDENCE_CONFLICT.
3. **Otherwise the highest explicit level decides.**
4. **L3 only:**
   - L3 alone can decide only NON_COMMON, and only when neither L1 nor L2 is
     explicit.
   - If L1 or L2 explicitly says COMMON and L3 says NON_COMMON, the explicit
     higher level decides. The L3 disagreement is recorded as
     `SUFFIX_OVERRIDDEN`, because suffix evidence is supporting, not
     high-quality.
5. **No explicit evidence at any level:** CLASS_UNRESOLVED (with a reason).

## 6. Exchange-transfer rule (smallest deterministic form)

- Evidence that is symbol-valid, interval-valid and explicit **but
  venue-mismatched** is `EXCHANGE_MISMATCH_REVIEW`. It never certifies
  COMMON_STOCK, never yields NON_COMMON, and never creates a conflict.
- If K has no venue-matched explicit evidence and has mismatch evidence, K is
  CLASS_UNRESOLVED(EXCHANGE_MISMATCH_REVIEW).
- Reconciliation requires a later, explicitly authorized exchange-history
  source. No general exchange-history system is built.

## 7. SEC extraction requirement (binding on a future D-SC)

SEC evidence must be extracted from the filing's XBRL instance, never from
rendered HTML.
- `dei:Security12bTitle`, `dei:TradingSymbol` and `dei:SecurityExchangeName`
  are paired **only when they share the same `contextRef`**. That context
  carries the `us-gaap:StatementClassOfStockAxis` member, or none for a
  single-class filer.
- A title without a same-context symbol is **not** symbol evidence.
- Pairing by rendered row order is forbidden, because the probe showed it
  mis-pairs classes.

## 8. Scope

- Only `INTERVAL_ASSIGNED` keys are classified. Every other D4 state is
  already excluded.
- Current directories and current Tiingo metadata (L4) are never decisive.
