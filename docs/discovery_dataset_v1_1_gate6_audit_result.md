# Discovery Dataset v1.1 — Gate-6 Specimen Audit Result (DATA-FREE)

- **Result:** **GATE 6 SPECIMEN AUDIT INCONCLUSIVE**, computed mechanically under the
  frozen decision rule (spec `worker_tasks/dataset_v1/task-g6-specimen-audit.md` §9).
- **Gate-6 status for development admission:** NOT_ASSESSED, and still blocking
  (clarification §8). INCONCLUSIVE is not PASS.
- **No resampling:** INCONCLUSIVE does not authorize resampling. Any follow-up
  audit is a new, separately frozen version, and these v1 results are kept.
- **Data-free:** this file holds hashes, counts and provenance only. It contains
  no security keys, observation dates, prices, ratios, quotes or contact details.

## Authority and inputs

| Item | Value |
|---|---|
| Spec / clarification / addendum | 05ef922 (`1dc4c7e0…e7e2`) / cab82e0 (`0b529dca…3789`) / 4695544 (`9960d3e1…cfa6`) |
| Pre-network manifest | cb7797f (`b4dbfda00f3144fa28ed514e9f1eddbd21babe66cd5f6d937dbb392baaa2cc45`) |
| `g6_id` | `g6-2264dbcac2a09a3d` |
| Population | 17,469 / 2,674; `d2153329b8c13a5d20d2bbe351972e048c9dc421216051e08633ffda68fb2783` |
| Specimen | 180 / 180 securities; `c0c15d20b4935a86d8ca75745425fc18b560c5e0619f908a3a1f3b8705d23b1e` |
| Fetch plan | 1,000 unique GETs; `98aa5f81e80b41f41af8672af3e24e3f2c897bb8ebfef8771038762eacebca66` |
| Fetched-document manifest | `27ea7c277549189c20baba8371169711ae94056d01e2ff4b9437646f1048e954` |
| Retrieval log | `13103665ba89442f487041d6c285bf64f92dae6b20e9dd149bd91d8bd61f70d5` |
| Evidence set | `c3d624536bd7421a2dcd77ef1095ff13ae1e445a98678f41b80e65632772e2c6` |

**E2 observability limitation:** request-start timestamps were not persisted,
and completion timestamps can show 5 completions in a sliding one-second
window. The frozen throttle semantics remain the authority.

## Blind double extraction

| Extraction | Runtime | File SHA-256 | Facts |
|---|---|---|---|
| A (worker) | Pi via Herdr, deepseek / deepseek-v4-flash | `282d14c48ab4d9738fba5c2362c5516c9df4fe4e13161ea12c07b55c5bdd3ee4` | 258 |
| B (planner) | Claude Code planner, claude-opus-5-5 | `672d96f58b24f60054226bd6665650ad7e1dbdf061c52cb24fb850c5989bdabc` | 225 |

- **Shared inputs:** the extraction context (`47d56bd1…391e`) and the rules sheet
  (`0daccaa7…25fc`). Neither contains rho, band or stratum.
- **Blindness:** each extractor sealed its file without access to the other's.
  Extraction B was sealed before any output of extraction A was read.
- **Validation:** both files pass the frozen validator (0 invalid facts).
- **Reconciliation:** mechanical. Extractors agreed on 148 specimens and disagreed
  on 32, and every disagreement became `UNRESOLVED(REVIEWER_DISAGREEMENT)`.

## Outcomes (180 specimens)

| Outcome | Count |
|---|---|
| GENUINE_MOVE_DOCUMENTED | 31 |
| COMPLEX_CA_OUTSIDE_CERTIFIED_SCOPE | 0 |
| CERTIFIED_FIELD_OMISSION | 0 |
| PRICE_OR_IDENTITY_ERROR | 0 |
| UNRESOLVED | 149 |

- **GENUINE_MOVE_DOCUMENTED by event type:** {'ACQUISITION_OF_ISSUER_ANNOUNCED': 3, 'BANKRUPTCY_OR_RESTRUCTURING': 1, 'CLINICAL_OR_REGULATORY_RESULT': 17, 'EARNINGS_OR_GUIDANCE': 3, 'EQUITY_OFFERING_PRICED': 3, 'SPAC_COMBINATION_EVENT': 1, 'STRATEGIC_TRANSACTION_OR_CONTRACT': 3}.
- **UNRESOLVED by reason:** {'AMBIGUOUS_ISSUER_MAPPING': 5, 'NO_EXPLANATORY_FILING': 79, 'NO_FILINGS_IN_WINDOW': 7, 'NO_ISSUER_MAPPING': 13, 'NO_ISSUER_NAME': 13, 'REVIEWER_DISAGREEMENT': 32}.
- **Data errors:** 0, so T1–T5 were not triggered (they apply only to a HOLD).

## Decision path

1. There are no DATA_ERROR outcomes, so the result is not a HOLD.
2. S1 resolved = 30 of 120. This is below min(59, ⌈120/2⌉) = 59, so
   the result is **INCONCLUSIVE**. Every S1 band is also below its threshold:

| S1 band | n | resolved | threshold min(16, ⌈n/2⌉) |
|---|---|---|---|
| B1 | 30 | 8 | 15 |
| B2 | 30 | 10 | 15 |
| B3 | 30 | 7 | 15 |
| B4 | 30 | 5 | 15 |

**Reported quantities (these never affect the decision):**
- S1 resolution rate 0.250, Clopper–Pearson 95% interval for the
  UNRESOLVED share [0.6627, 0.8245].
- COMPLEX share within S1: 0 of 120, 95% upper bound 0.0303.
- S2 resolution: 1 of 40. S3 resolution: 0 of 20.

## Artifacts (outside Git: `data_store/discovery_dataset_v1/derived/qc_audit/g6-2264dbcac2a09a3d/`)

| File | SHA-256 |
|---|---|
| `facts_worker.jsonl` | `282d14c48ab4d9738fba5c2362c5516c9df4fe4e13161ea12c07b55c5bdd3ee4` |
| `facts_planner.jsonl` | `672d96f58b24f60054226bd6665650ad7e1dbdf061c52cb24fb850c5989bdabc` |
| `outcomes.parquet` | `ad8345beee559d8360bfe1bc87c3345065ac88f236d36e01a511c99c9b99a225` |
| `g6_audit.json` | `f52eb62a7b46ab94083546a2285dc9eabc497167c96421d92aa07944abb10914` |

- **Determinism:** reconciliation, classification, aggregation and the decision
  were re-run under `TZ=Asia/Tokyo LC_ALL=C` on the same sealed facts. The
  artifacts were byte-identical.
- **Access:** network access, provider calls, reserve reads, specimen
  replacements and supplemental evidence were all 0.
- **Knowledge-PIT:** no record appended (clarification §9).

## Nonclaims

This result claims nothing about the correctness of any suspect observation, the
completeness of corporate actions, reserve or confirmation eligibility, alpha,
or production readiness. It does not admit Dataset v1, and it does not authorize
any data remediation.
