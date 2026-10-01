# Discovery Dataset v1.1 — Gate-6 v2B Protocol Addendum (DATA-FREE; NORMATIVE)

- **Status:** normative clarification of the v2B evidence protocol. It was
  decided by the user on 2026-09-30 ("G6-v2B PROTOCOL ADDENDUM — FREEZE BEFORE
  VENDOR RESEARCH"), on master `c8705c6`.
- **Parent:** `docs/discovery_dataset_v1_1_gate6_v2b_evidence_protocol.md`
  (c8705c6), SHA-256
  `f04d2253747e2b9fdebad3681ac93832b3d452d5463c009d17c5a16607b36c27`.
- **Scope:** this addendum does not redesign the parent. Where the two differ,
  this addendum governs. Every parent element not addressed here is unchanged.
- **Frozen before vendor research.** It was frozen before any vendor search,
  vendor documentation retrieval, vendor selection, trial query or second-source
  evidence access. None of these has occurred, and no vendor is named.
- **Unchanged lineage:** the same 180 specimens, strata, bands, v1 outcomes, v1
  sealed SEC evidence and frozen thresholds (S1 resolved ≥ 59; each S1 band of 30
  resolved ≥ 15).

## Record layers (machine-readable; governs A1 and A2)

v2B adds layers. It never overwrites the five frozen v1 outcome classes. Each
specimen record carries these fields:

| Field | Values | Notes |
|---|---|---|
| `v1_outcome`, `v1_reason` | the sealed v1 values | copied verbatim; never modified |
| `v2b_outcome`, `v2b_reason` | one of the five frozen classes {GENUINE_MOVE_DOCUMENTED, COMPLEX_CA_OUTSIDE_CERTIFIED_SCOPE, CERTIFIED_FIELD_OMISSION, PRICE_OR_IDENTITY_ERROR, UNRESOLVED}, with the parent §3 matrix reason | no new outcome class. `INDEPENDENT_PRICE_CORROBORATED` is a reason, not an event type |
| `v2b_error_status` | `CONFIRMED_DATA_ERROR` \| `UNCONFIRMED_ERROR_SIGNATURE` \| `RESOLVED_NO_ERROR` \| `NOT_RESOLVED` | the error-status layer, defined below |
| `v2b_signature_kind` | `PRICE` \| `CA` \| `NONE` | `PRICE` for parent matrix row M3 (strong contradiction or date offset); `CA` for row M4 (adjustment or CA explains ρ) |
| `resolution_basis` | `SEC_DOCUMENTED` \| `SEC_DOCUMENTED+PRICE_CORROBORATED` \| `INDEPENDENT_PRICE_CORROBORATION` \| `INDEPENDENT_PRICE_CORROBORATION_NO_PRIMARY` \| `NONE` | replaces the parent's `v2b_basis` labels one-for-one |
| `causal_event_classification` | `SEC_EVENT_TYPE(<type>)` \| `REMAINS_DISAGREED` \| `NOT_ESTABLISHED` | `REMAINS_DISAGREED` for every v1 REVIEWER_DISAGREEMENT specimen; `NOT_ESTABLISHED` for price-only resolution |

**How `v2b_error_status` is derived:**
- **`CONFIRMED_DATA_ERROR`:** `v2b_outcome` is CERTIFIED_FIELD_OMISSION or
  PRICE_OR_IDENTITY_ERROR, under the parent §9 confirmation requirements.
- **`UNCONFIRMED_ERROR_SIGNATURE`:** `v2b_signature_kind` ≠ `NONE` and the
  signature is not confirmed. `v2b_outcome` is then
  UNRESOLVED(ERROR_SIGNATURE_PRICE or ERROR_SIGNATURE_CA).
- **`RESOLVED_NO_ERROR`:** `v2b_outcome` is GENUINE_MOVE_DOCUMENTED or
  COMPLEX_CA_OUTSIDE_CERTIFIED_SCOPE, and no signature exists.
- **`NOT_RESOLVED`:** every other UNRESOLVED.

A specimen counts as **resolved** for the thresholds iff `v2b_error_status` is
`CONFIRMED_DATA_ERROR` or `RESOLVED_NO_ERROR`.

## A1 — Reviewer disagreement: error status, not causal label

A v1 specimen with `UNRESOLVED(REVIEWER_DISAGREEMENT)` may reach
`v2b_error_status = RESOLVED_NO_ERROR` only if all of the following hold:
1. Neither frozen v1 extractor's engine outcome for it was a DATA_ERROR outcome.
   As in parent row M10, a COMPLEX outcome also excludes it.
2. It satisfies the complete parent conditions C1–C7: identity, independence,
   raw price, adjustment and corporate action, alignment and stale, unit and
   revision, and sealed SEC non-contradiction.
3. No confirmed data error exists for it.
4. No unconfirmed error signature exists for it.

The resulting record keeps the disagreement visible:

```
v1_outcome                  = UNRESOLVED
v1_reason                   = REVIEWER_DISAGREEMENT
v2b_error_status            = RESOLVED_NO_ERROR
resolution_basis            = INDEPENDENT_PRICE_CORROBORATION
causal_event_classification = REMAINS_DISAGREED
```

- **This does not resolve the causal disagreement.** No record, report or summary
  may state or imply that the v1 reviewers agreed.
- **v2B price evidence is never used to choose between the extractors' causal
  labels or event types.**
- **Parent row M9 is narrowed accordingly:** its `v2b_reason`
  `INDEPENDENT_PRICE_CORROBORATED` carries no event type.

## A2 — Decision precedence (replaces parent §12 ordering)

1. ≥ 1 `CONFIRMED_DATA_ERROR` (any stratum): **HOLD — REMEDIATION REQUIRED**.
2. Otherwise, ≥ 1 `UNCONFIRMED_ERROR_SIGNATURE` (any stratum):
   **INCONCLUSIVE** (reason `UNCONFIRMED_ERROR_SIGNATURE`).
3. Otherwise, S1 resolved < 59, or any S1 band resolved < 15: **INCONCLUSIVE**
   (reason `INSUFFICIENT_RESOLUTION`).
4. Otherwise, **PASS**.

- **An unconfirmed signature is never ignored** because the thresholds are met.
- **It is never upgraded automatically to a data error.** Upgrading requires the
  parent §9 primary or doubly independent confirmation.
- **A vendor contradiction alone does not establish that Tiingo is wrong.**
- **Reporting:** the decision artifact reports the counts of each
  `v2b_error_status` and `v2b_signature_kind`, and resolved counts by
  `resolution_basis`, next to the v1 decision.

## A3 — Three-zone comparison semantics

There are three states for every raw-price and ratio comparison of parent §4:
**AGREE**, **AMBIGUOUS** and **STRONG_CONTRADICTION**. They replace the parent's
`RAW_AGREE` / `RAW_AMBIGUOUS` / `RAW_CONTRADICT` (and the RATIO equivalents)
one-for-one.

- **AGREE:** the boundary is frozen later. It comes only from documented source
  precision, tick and rounding rules, under the parent's form
  (max(relative, tick-based)) and ceiling (≤ ln 1.05). It is fixed **before
  specimen retrieval**.
- **STRONG_CONTRADICTION:** a ratio outside **[0.8, 1.25]**, reusing the v1
  frozen STATED_CLOSE band. It is used **only** as the strong-contradiction
  screen that raises a `PRICE` error signature (parent rows M3 and §9) in the
  parent's context.
  - It is **not** the normal agreement tolerance.
  - It is **not** a statement that a 20–25% disagreement is acceptable.
  - It is **not** a generic data-quality tolerance.
- **AMBIGUOUS:** everything between the final AGREE boundary and the
  STRONG_CONTRADICTION boundary. AMBIGUOUS never resolves a specimen. It raises no
  signature and gives `NOT_RESOLVED`, except that a v1 SEC-documented
  GENUINE_MOVE_DOCUMENTED is retained per parent row M7.
- **Neither boundary may be tuned from specimen observations.**

## A4 — Field-level independence (clarifies C2; no relaxation)

- **What independence is judged on:** the **evidentiary claim and field family**,
  not whether Vendor B and Tiingo are different companies.
- **Truth vs lineage:** the *underlying official market truth* (the official
  exchange close; a primary corporate-action announcement) is distinct from the
  *acquisition, transformation, correction and redistribution lineage* by which
  each vendor obtains its record.
  - Two vendors reporting the same official close are not dependent merely
    because the close is the same fact.
  - If both records of a field family inherit from the same intermediate
    redistributor, transformation pipeline or correction process, the second
    observation does not corroborate. That family is `IND-B`.
- **Grades preserved:**
  - **`IND-A`:** distinct documented lineage. It may corroborate.
  - **`IND-B`:** a shared lineage. It cannot corroborate.
  - **`IND-U`:** undocumented or insufficient. It is treated as `IND-B`, and it
    is **not relaxed**.
- **Per family:** vendor research must establish a grade separately for **price
  evidence** and for **corporate-action evidence**. One candidate may have
  different grades per family.
- **Gate:** C2 requires `IND-A` for both families, and the parent §7/§10
  source-level gate (stop before retrieval) is unchanged.

## A5 — Vendor-selection firewall

After this addendum is frozen, vendor research may examine **only**:
- public and vendor documentation, and product documentation;
- coverage descriptions, API schemas and field definitions;
- adjustment methodology, identity methodology and provenance or feed
  descriptions;
- licensing and retention terms, access and rate constraints, and pricing or
  access requirements.

Vendor research **must not** query or inspect any of the 180 specimen securities,
their tickers, dates, prices, corporate actions or specimen-specific
availability. Specifically:
- no trial query using a specimen;
- no coverage test against the specimen list;
- no source shopping based on observed specimen agreement;
- no exposure of the sealed specimen list to a candidate vendor for selection.

Generic, non-specimen documentation examples published by a vendor may be read.
A vendor's own demo queries must not be run against specimen identifiers.

## A6 — Source-selection principle

The selection question is: **does this source satisfy the already frozen
evidentiary requirements** (parent §13 MUST list, plus A4)? It is never "which
source is most likely to make Gate 6 pass?"

- **Criteria come first:** they are the parent §13 checklist plus A4. They are
  fixed before any candidate is evaluated, and each candidate is assessed against
  every MUST item, with the documentation cited.
- **If no realistic source satisfies every MUST item**, the outcome is
  **NO QUALIFYING SOURCE**. v2B then does not proceed.
- **The protocol is never weakened** to make a vendor qualify.

## Access record

- Network calls, provider calls and reserve reads: 0.
- Vendor research: NO. Vendor selected: NO.
- Specimen-specific access: NO. New evidence accessed: NO.
- v1 outcome changed: NO. Knowledge-PIT append: NO.
- E3 scratch provenance retained unmodified.
