# D-SC classification vocabulary — version `dsc-vocab-v4` (PLANNING; FROZEN BEFORE FINAL REMEASUREMENT)

- **Status:** final classification-rule freeze candidate, per the user's v4
  brief of 2026-09-27. Committed **before** the single final offline
  remeasurement.
- **D-SC implementation is not authorized.**
- **Supersedes** `dsc-vocab-v3` (commit 385cf9a), which is kept as history.
- **Evidence:** the already acquired Wayback captures and the SEC full-index
  regulatory table (v3 inputs). No network access.

## A. Delta from v3

1. **Issuer types are removed from the security-class grammar.** The v2/v3
   whole-name overrides for FUND (`\bfund\b`), CLOSED_END_FUND
   (`closed[- ]end fund`) and BDC (`business development
   (company|corporation)`) no longer produce a security class.
   - A capture whose instrument text is only such a label ("… - Closed End
     Fund", "… - Business Development Company") falls through the grammar to
     CLASS_UNRESOLVED.
   - That unresolved point never negates an established explicit state, so no
     COMMON → NON_COMMON security-class transition can arise from issuer-type
     wording.
2. **New name-based issuer-policy overlay** (§3) carries that wording as
   exclusion evidence, alongside the unchanged SEC-form overlay.
3. **Label rename:** `NONCOMMON_TRUST_OR_SBI` becomes `NONCOMMON_TRUST_OR_UNIT`.
   The pattern is unchanged.
4. **New limitation label:** `PRE_EDGAR_REGULATORY_STATUS_NOT_CERTIFIED`
   (§5). There are no ticker-specific rules.

**Unchanged from v3:**
- the instrument-segment grammar and reference split;
- the strict ADR/ADS rule (bare gives CLASS_UNRESOLVED);
- REIT/SBI;
- possible tracking stock;
- test issue (flag, list and name) and ETF (flag and name);
- when-issued: the name rule plus the v3 §2.2 parallel-line correction;
- the suffix rule including suffix contradiction;
- the PIT security-class timeline with last-known-state semantics;
- the SEC-form overlay (N-54A, N-54C, N-8A, N-8F semantics);
- deterministic issuer mapping and ambiguity fail-closed;
- the SEC XBRL-context requirement;
- exchange-mismatch handling;
- the scope (INTERVAL_ASSIGNED keys only).

## 1. Security-class whole-name overrides (v4 list, in order)

| # | Pattern (whole name) | Result |
|---|---|---|
| O1 | `\btest (stock\|issue\|symbol)\b` | NON_COMMON(TEST_ISSUE) |
| O4 | `\bexchange[- ]traded (fund\|note)s?\b\|\betf\b\|\betn\b` | NON_COMMON(ETF) |
| O6 | `\btracking stock\b` | NON_COMMON(TRACKING_STOCK) |
| O7 | `\bwhen[- ](issued\|distributed)\b` | NON_COMMON(WHEN_ISSUED) |

**Removed:** O2 (closed-end fund), O3 (business development
company/corporation) and O5 (fund).

All v2 §1.2–§1.9 rules follow unchanged. Row flags still apply: Test Issue = Y
gives TEST_ISSUE and ETF = Y gives ETF.

**Instrument-level NON_COMMON reasons in v4:**
- WARRANT, UNIT, RIGHT;
- PREFERRED, DEPOSITARY_PREFERRED;
- NOTE_OR_DEBT;
- ETF;
- LP_OR_MLP_UNIT, NONCOMMON_TRUST_OR_UNIT;
- TRACKING_STOCK, TEST_ISSUE, WHEN_ISSUED.

## 2. Security-class timeline

Unchanged from v3 §2, including:
- concurrent and alternating conflicts;
- two runs forming a transition from the first new observation;
- last-known state;
- the when-issued parallel-line rule;
- static test and suffix handling, with suffix contradiction.

## 3. Issuer-policy overlay (two positive-exclusion sources)

**3.1 SEC_FORM source.** Unchanged v3 §3 (mapping, events, status
machine):
- `SEC_FORM_BDC` runs from the N-54A filing date until an N-54C.
- `SEC_FORM_REGISTERED_FUND` runs from the N-8A filing date; N-8F has no
  effect.
- N-54C with no successor gives `ISSUER_STATUS_UNRESOLVED(WITHDRAWN_NO_SUCCESSOR_STATUS)`.
- An ambiguous mapping gives `ISSUER_STATUS_UNRESOLVED(AMBIGUOUS_MAPPING)`.
- Only filings with Date Filed ≤ f−1 count.

**3.2 DIRECTORY_NAME source (new).** It is built from K's venue-admissible,
interval-dated capture rows (the same admissibility as security-class
evidence). Matching is case-insensitive unless stated, on the whole name:

| Evidence | Pattern | Exclusion |
|---|---|---|
| NAME_BASED_BDC | `\bbusiness development (company\|corporation)\b`, or the case-sensitive token `\bBDC\b` | BDC |
| NAME_BASED_REGISTERED_FUND | `\bclosed[- ]end fund\b` or `\bfund\b` | REGISTERED_FUND |

- **PIT:** the exclusion is active at f iff at least one such capture is dated
  ≤ f−1. It begins at the first such capture's date and is **never
  backfilled**.
- It persists afterwards: no admissible source provides termination
  evidence, so this is fail-closed.
- It is **exclusion evidence only**. It does not certify legal regulatory
  status.
- A row with ETF = Y is an ETF instrument (security class) and is not
  counted here.

**3.3 Combination at f.** The policy status at f is:
1. **EXCLUDED** if SEC_FORM_BDC, SEC_FORM_REGISTERED_FUND, NAME_BASED_BDC or
   NAME_BASED_REGISTERED_FUND is active at f. Any one suffices; each active
   source is recorded in provenance.
2. Otherwise `ISSUER_STATUS_UNRESOLVED` if the SEC source gives
   ISSUER_STATUS_UNRESOLVED at f.
3. Otherwise `NO_REGULATORY_EXCLUSION`. This is **not** a certification of
   unrestricted status.

Positive exclusion evidence is never overridden by the absence of evidence in
another source. Only evidence knowable by f−1 is used, and no later source
rewrites an earlier f.

## 4. Eligibility composition (D-SC part; conceptual, for D6)

`security_class(f) = COMMON_STOCK` **and** policy status ∉ {EXCLUDED,
ISSUER_STATUS_UNRESOLVED}, together with the frozen D6 structural
conditions.

## 5. Accepted Dataset v1.1 limitations (explicit)

- **`PRE_EDGAR_REGULATORY_STATUS_NOT_CERTIFIED`.**
  - BDC elections or fund registrations made before the electronic index
    (earliest indexed event 1994-01-06), without a later indexed N-54A, N-54C
    or N-8A and without explicit name evidence, are not detected.
  - Such issuers may be false eligibles.
  - Known examples, as evidence of the limitation only (no special rules):
    CSWC, EQS.
- **Registered funds** whose status predates electronic evidence and whose
  names lack explicit fund wording may remain undetected.
- **Classification coverage is not complete:** bare ADS, issuer-name-only,
  generic shares, SBI-ambiguous and other unresolved keys are fail-closed and
  ineligible.

## 6. Final measurement (single run)

- Security class and overlay per key at C and per formation date f (monthly
  2016-01 to 2026-08), with overlaps between overlay sources.
- **Structural eligibility estimate:**
  - COMMON_STOCK at f;
  - policy not EXCLUDED or UNRESOLVED at f;
  - listed at f−1 and f;
  - age ≥ 252 sessions;
  - at least 50 usable sessions in f−63…f−1;
  - Q-D6-1 PIT missingness ≤ 5%.
