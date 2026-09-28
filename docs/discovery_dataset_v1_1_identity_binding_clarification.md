# Discovery Dataset v1.1 — Issuer-Identity Binding Clarification (NORMATIVE)

- **Status:** normative clarification, decided by the user on 2026-09-28
  (Option B, retrospective identity binding).
- **Documentation only:** it changes no rule, no implementation semantics and
  no reference count.
- **It clarifies, without modifying:**
  - `docs/discovery_dataset_v1_1_classification_amendment.md` (b6225b6;
    SHA-256 `f89a7128bb8113c6978fc6bca8bcaf6048cd0c7db1fe5ac2aaffa1c7d519c623`);
  - `worker_tasks/dataset_v1/dsc-vocabulary-v4.md` (cb811d0; `fd748432…`);
  - `worker_tasks/dataset_v1/task-dsc-classifier.md` (ec148ca; `6571e541…`).
- There is no `dsc-vocab-v5`, and no remeasurement.

## 1. Clarification

Dataset v1 separates:
1. **retrospective security/issuer identity resolution**, from
2. **point-in-time classification and regulatory evidence**.

**Issuer identity binding is a retrospective Dataset-v1 identity-resolution
input**, consistent with the D4 identity model (the supported-tickers
snapshot at C, with identifier continuity `NOT_CERTIFIED`). This covers the
D-SC mapping of a `security_key` to a regulatory CIK through the issuer names
in the key's interval-dated directory captures.

- The **first observed historical directory capture** linking a key to an
  issuer is **not** interpreted as the knowledge date at which that issuer
  identity became economically true or publicly knowable.
- **D-SC Knowledge-PIT applies to** the dated classification evidence
  (directory capture dates) and the dated regulatory evidence (SEC Date
  Filed) used **after** identity binding. It does not apply to the
  retrospectively resolved issuer binding itself.
- **This does NOT certify historical issuer-identity continuity.**
  Issuer-identity continuity remains `NOT_CERTIFIED` under the Dataset-v1
  identity model.
- A **later identity-resolution observation may allow an earlier-dated
  public regulatory filing to be associated retrospectively** with the
  resolved `security_key`.
  - This is an **intentional Dataset-v1 identity-model limitation**.
  - It is **not** evidence that the ticker-to-issuer association itself was
    known at that earlier date.

## 2. Empirical scale of the limitation (disclosure only)

Measured on the reconciled production candidate `c-27b6dabd0db0cb15`
(`dsc_hash 2ee4940c14a85048fddd9e3e675c3e9a6f40a11c8887192a5e61939adfc62b60`):
- **252** keys have a regulatory issuer binding whose first observed linking
  capture is later than the regulatory evidence that drives their policy
  state.
- **191** of those gaps intersect the formation window (f−1 ≥ 2016-01-28).

**Examples (disclosure only):** BCIC, BXSL, BMEZ, BSTZ.
- No ticker-specific rule exists.
- Their states are unchanged.
- They are not used to tune any rule.

## 3. Certification wording (do not strengthen)

| Dimension | Status |
|---|---|
| CLASSIFICATION EVIDENCE PIT | PASS (subject to the D-SC barrier) |
| REGULATORY EVENT-DATE PIT | PASS (subject to the D-SC barrier) |
| ISSUER-IDENTITY KNOWLEDGE-PIT | **NOT CERTIFIED** |

> D-SC is PIT-certified with respect to classification evidence dates and
> regulatory-event dates under the Dataset-v1 retrospectively resolved
> identity binding; historical issuer-identity knowledge timing/continuity is
> NOT CERTIFIED.

The broader claim "all D-SC policy state is based only on information
knowable by f−1" is **not** made.

## 4. Unchanged reference

The frozen v4 reference and the reconciled candidate remain authoritative:

| Item | Value |
|---|---|
| COMMON_STOCK | 7,026 |
| NON_COMMON | 2,426 |
| CLASS_UNRESOLVED | 901 |
| EVIDENCE_CONFLICT | 0 |
| Genuine transitions | 10 |
| Overlay EXCLUDED | 224 |
| Overlay ISSUER_STATUS_UNRESOLVED | 40 |
| Issuer mapping | MAPPED 248, AMBIGUOUS 10, NO_MATCH 9,680, NO_NAME 415 |
