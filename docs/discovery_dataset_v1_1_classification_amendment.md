# Discovery Dataset v1.1 — Security Classification Amendment (FROZEN)

**Status:** FROZEN. The user approved it on 2026-09-27 ("V1.1 CLASSIFICATION
RULE READY TO FREEZE; there will be no dsc-vocab-v5").

**Amends:** `docs/discovery_dataset_v1_contract.md` (SHA-256
`d03f35549777e5a94046f06dd0621acfc3a1dedb7a4b40adc55fdae911fa80e3`).
- The v1 contract file stays unchanged as history.
- Dataset v1.1 is Dataset v1 with this amendment and the Q-D6-1 clarification
  below. **No other v1 methodology changes.**
- **Dataset id:** `discovery_dataset_v1_1`, bound to the SHA-256 of this
  file's committed bytes. That hash is recorded outside the file, in the
  commit message.

**Normative rule text:** `worker_tasks/dataset_v1/dsc-vocabulary-v4.md` at
commit `cb811d0`.
- Vocabulary SHA-256:
  `fd7484328c1c1efae2c02f334571f8bcea7d363c051bcccf49594d2dab34f84d`.
- v4 incorporates, by stated reference and unchanged, the rules of
  `dsc-vocabulary-v3.md` (385cf9a), `dsc-vocabulary-v2.md` (0185630) and the
  v1 admissibility, XBRL and exchange-mismatch sections
  (`dsc-vocabulary-v1.md`, 58a7489).
- Where this summary and v4 differ, **v4 governs**.

## 1. What changed from v1 and why

| v1 section | v1 rule | v1.1 rule |
|---|---|---|
| §3.2–3.3 eligibility (type, suffix) | assetType = Stock; dash-segment suffix exclusions | Security class must be `COMMON_STOCK` from **positive, interval-dated, point-in-time evidence**, and no issuer-policy exclusion may be active (§3–§4). The suffix rule is kept only as exclusion or contradiction evidence |
| §3 (implicit) | test issues not excluded | Test issues are NON_COMMON(TEST_ISSUE) (authoritative flag, list or name) |
| §10 missingness | "> 5% across in-window listed sessions" | Point-in-time form (Q-D6-1, `dataset-v1-1-decisions.md` §2): at f, over trusted listed sessions from the first usable row through f−1, excluding off-calendar rows from both counts |
| §17 claim | "US-exchange USD common-stock listings" | "…listings with positive, point-in-time common-stock classification evidence and no observed BDC or registered-fund exclusion evidence" |
| §18 nonclaims | — | adds §5 of this amendment |
| §20 DAG | D4 → D6 | D4 → **D-SC** → D6 (D-SC also uses D5 only for structural validation) |
| §22 frozen table | suffix list, type | replaced by this amendment for classification |

**Why the change was required** (D-SC evidence reviews of 2026-09-27):
- Tiingo assetType is too coarse.
- Ticker spelling is neither sufficient (for example a 5-letter common stock)
  nor complete (4-letter preferreds and notes exist).
- Test issues cannot be found by pattern.
- Current directories and current metadata are not historical evidence.

**Unchanged v1 sections:**
- §1, §2, §4 (identity; reused by D-SC unchanged), §5, §6, §7, §8, §9, §11,
  §12, §13, §14, §15, §16 (D5 gates 1–10 as implemented; gates 11–14 remain
  with D6–D8), §19 (versioning rules) and §21.

**Artifact reuse:**
- The acquisition (batch `b-c21b48190918f21f`), D1, D3
  (`n-f874d3ecb61374d4`), D4 (`i-a21f1e46621dee94`) and D5
  (`q-5d1028655f8c82e1`) remain valid. **None is regenerated.**
- D-SC adds a new artifact bound to this amendment's hash.

## 2. Dimension 1 — SECURITY CLASS (of the traded instrument)

**States:**
- `COMMON_STOCK(subtype ∈ {COMMON, ADR_ADS, REIT_COMMON})`;
- `NON_COMMON(reason)`;
- `CLASS_UNRESOLVED(reason)`;
- `EVIDENCE_CONFLICT(reason)`.

**Frozen rules (v4):**
- **Instrument-segment precedence.** Split at the reference clause first,
  then take the instrument segment. Explicit non-common instrument terms
  (warrant, ADW, unit, right, preferred, depositary preferred, notes/debt,
  LP unit, trust unit) beat embedded common or ADS words.
- **Strict ADR/ADS.**
  - Bare ADS/ADR gives CLASS_UNRESOLVED(BARE_ADS).
  - COMMON_STOCK(ADR_ADS) only with representation wording naming common or
    ordinary equity, or context-bound SEC XBRL class evidence.
  - A preferred reference gives DEPOSITARY_PREFERRED.
- **REIT/SBI.**
  - Positive REIT common gives COMMON_STOCK(REIT_COMMON).
  - SBI wording alone gives CLASS_UNRESOLVED(SBI_AMBIGUOUS).
  - Positive non-common trust or unit gives NON_COMMON.
- **Test and ETF:** from directory flags, names or the authoritative
  test-symbol list. Tracking stock, when-issued and possible-tracking rules as
  in v4.
- **Suffix contradiction.** An applicable Nasdaq fifth-character or CQS dash
  suffix is exclusion evidence only. Together with a would-be COMMON state it
  gives EVIDENCE_CONFLICT(SUFFIX_CONTRADICTION).
- **Point-in-time last-known-state timeline:**
  - only admissible evidence dated ≤ f−1 counts;
  - same-date COMMON/NON disagreement gives CONCURRENT conflict;
  - three or more alternating runs give an ALTERNATING conflict;
  - two runs give a **genuine transition** from the first new-class
    observation, never backfilled;
  - gaps keep the last-known class.
- **When-issued parallel line.** A WHEN_ISSUED observation dated on or after
  an established COMMON observation is retained and flagged but never
  transitions the class.
- **Exchange mismatch.** Symbol- and interval-valid explicit evidence from a
  different venue is EXCHANGE_MISMATCH_REVIEW. It never certifies, excludes
  or conflicts.
- **SEC XBRL context binding.** SEC class evidence pairs
  `dei:Security12bTitle`, `dei:TradingSymbol` and `dei:SecurityExchangeName`
  only within the same XBRL context. Rendered-HTML row order is inadmissible.

**Issuer/entity concepts (Fund, Closed-End Fund, Business Development
Company) are NOT security classes.**

## 3. Dimension 2 — ISSUER / POLICY EXCLUSION OVERLAY

**Positive exclusion sources.** Each is admissible at f only if knowable by
f−1, and none is backfilled:

| Source | Evidence | Effect |
|---|---|---|
| SEC-form BDC exclusion | Form N-54A (Date Filed) | begins; ended by a later N-54C |
| SEC-form registered-fund exclusion | Form N-8A (Date Filed) | begins; **N-8F does not terminate** it without admissible termination evidence |
| Directory-name BDC exclusion | an admissible capture with explicit "Business Development Company/Corporation" or token "BDC" | begins at the capture date; persists |
| Directory-name registered-fund exclusion | an admissible capture with explicit "Closed End Fund" or "Fund" | begins at the capture date; persists |

**Combination and states:**
- Sources combine by **OR**, and source-level provenance is kept.
- An N-54C with no successor status, or an ambiguous issuer mapping, gives
  **ISSUER_STATUS_UNRESOLVED** (unresolved regulatory transition), which is
  ineligible.
- **Absence of exclusion evidence** is recorded as
  **`NO_REGULATORY_EXCLUSION_OBSERVED`**. It is **not**
  `CERTIFIED_UNRESTRICTED_ISSUER`.
- **Issuer mapping** for the SEC forms is deterministic: exact equality after
  non-semantic normalization, against every historical index name of the
  CIK. There is no fuzzy matching.

## 4. Eligibility composition (the D-SC part of D6 eligibility)

At formation f:
- `security_class(f) = COMMON_STOCK`; **and**
- the overlay at f is neither EXCLUDED nor ISSUER_STATUS_UNRESOLVED; **and**
- every other frozen D6 condition holds (v1 §3 rules 1, 4 and 5, §5, §10 in
  its Q-D6-1 point-in-time form, and §4 identity).

## 5. Accepted limitations (normative nonclaims)

- **A. `PRE_EDGAR_REGULATORY_STATUS_NOT_CERTIFIED`.**
  - Electronic SEC regulatory event coverage (earliest indexed event
    1994-01-06) does not establish pre-electronic BDC or registered-fund
    status.
  - Known possible false eligibles: CSWC and EQS. These are examples, **not**
    ticker-specific exceptions.
  - Registered funds whose status predates electronic evidence and whose
    names lack explicit fund wording may be undetected.
- **B. `HISTORICAL_EXCHANGE_CLASS_LABEL_NOT_CERTIFIED`.**
  - Historical exchange-directory class labels may be wrong or too coarse.
    Known examples: CIB and ELP, labelled common before later evidence
    identified preferred-share or unit ADSs.
  - The system follows evidence knowable at the time, with **no retrospective
    ticker corrections**.
- **C. `CLASS_UNRESOLVED` residual.** 901 keys in the v4 reference
  measurement are fail-closed and ineligible.
- **D. Regulatory absence is not positive certification.**

## 6. Reference measurement (supporting evidence; NOT normative; does NOT certify D6)

This is the single v4 offline structural measurement (2026-09-27), using no
returns, values or performance:

| Item | Value |
|---|---|
| Assigned keys | 10,353 |
| COMMON_STOCK at C | 7,026 |
| NON_COMMON | 2,426 |
| CLASS_UNRESOLVED | 901 |
| EVIDENCE_CONFLICT | 0 |
| Genuine class transitions | 10 |
| Overlay EXCLUDED at C | 224 |
| ISSUER_STATUS_UNRESOLVED at C | 40 |
| Minimum structurally eligible COMMON | development 3,061; reserve 3,929 |

All 128 monthly formation dates have ≥ 500 eligible.

**Evidence inputs** (stored outside Git under
`data_store/discovery_dataset_v1/dsc_evidence/`):
- Wayback directory captures: 127 files; `SHA256SUMS` hash
  `5fdc968ed2ea149bd65d64f6263d53d1364e52886b14cb83260b4c6e4de9bd0e`.
- SEC full-index `master.gz` files, 1993Q1–2026Q3: 135 files; aggregate
  `16cf464c905531bb76037bb95b1af7e97de0cf2158d672d31db1748e9db39c69`.
- SEC request log: `d2c70fa0b3b2d246fab8d0defcc80f3523a7b5dafa2adcd2bffd559806a2c85a`.
