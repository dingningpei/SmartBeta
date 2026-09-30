# Discovery Dataset v1.1 — Gate-6 Corporate-Action Specimen Audit Clarification (NORMATIVE)

- **Status:** normative clarification, written under the user's "GATE-6
  CORPORATE-ACTION SPECIMEN AUDIT — PLANNING / SPEC FREEZE AUTHORIZED"
  instruction of 2026-09-30. It follows the final aggregate admission review
  (master `4c273fd`), which found the gate-6 specimen audit to be the sole
  Dataset-v1 development admission blocker.
- **Documentation only:** it changes no completed stage, no artifact, no
  CORP_ACTION_SUSPECT disposition and no sealed phase.
- **It clarifies, without modifying:**
  - `docs/discovery_dataset_v1_contract.md` §§8, 11.2, 12, 14, 16 (gate 6), 18 —
    `d03f35549777e5a94046f06dd0621acfc3a1dedb7a4b40adc55fdae911fa80e3`;
  - `docs/discovery_dataset_v1_1_classification_amendment.md` §1, which lists
    §16 as unchanged (so the gate-6 wording still applies) —
    `f89a7128bb8113c6978fc6bca8bcaf6048cd0c7db1fe5ac2aaffa1c7d519c623`;
  - `worker_tasks/dataset_v1/task-d5-qc.md` §§3–4 (9b52b49), whose gate-6 PASS
    rule did not include the specimen audit —
    `7ad986ef46384f5cc1052d00b4b39a2e4b444dd1ef448aeea33cb1c3bacd48f8`.

## 1. The exact authority

- **Contract §8:** "A day with |raw return| > 50% and no split or dividend
  event is flagged **CORP_ACTION_SUSPECT**. Flagged days are retained, counted
  and specimen-audited, never silently dropped."
- **Contract §16, gate 6:** "**Corporate actions:** malformed counts, and the
  CORP_ACTION_SUSPECT list with a specimen audit." §16 adds: "All of these
  must PASS before Dataset v1 is admitted for Discovery Campaign 1."
- **Where D5 left it:** D5 (`q-5d1028655f8c82e1`) delivered the counted,
  retained, dispositioned list. No frozen stage specified or performed the
  specimen audit.

**What the contract does not specify:** specimen size, sampling method,
evidence source, pass threshold and classification vocabulary. All five are
left open. The frozen spec
`worker_tasks/dataset_v1/task-g6-specimen-audit.md` fixes them. Nothing
below claims they came from the contract.

## 2. Development scope; the reserve part is DEFERRED

- **The gate-6 specimen audit that admission needs** covers the development
  CORP_ACTION_SUSPECT population only: every D5 development `day_flags` row
  with `class_or_flag = CORP_ACTION_SUSPECT`. There are 17,469 rows over
  2,674 `security_key`s, with dates from 2015 to 2023-06-30. The population
  SHA-256 is fixed in the spec.
- **Reserve suspect days** (dated on or after 2023-07-03): their specimen
  audit is **DEFERRED / NOT ASSESSED**, as for reserve gate 11 (D6
  clarification). Contract §12 forbids reading reserve values, so this is not
  a failure.
  - It may run only inside a separately authorized Phase-10 confirmation
    execution.
  - It blocks only future confirmation use, not development admission.
- **Reserve evidence is never used** to judge a development observation. No
  reserve market value, reserve outcome or reserve `day_flags` row is read.

## 3. What "specimen-audited" means (interpretation)

- **The audit is observational data-quality validation.** It asks whether
  independent evidence supports how Dataset v1 currently treats a
  predeclared, deterministic specimen of development suspect days: as a
  usable, genuine raw price move.
- **Two consequences follow from the §8 definition:**
  - A suspect day that turns out to be a split or cash distribution which
    Tiingo did not record on that day is **not** "explained and fine". The
    flag exists because no event was recorded, so the certified return path
    produced a wrong return. It is a **data error in a certified field**.
  - A suspect day explained by a complex corporate action (spin-off, in-kind
    distribution, rights, merger or exchange conversion, share-class
    reclassification) falls under the existing §18 nonclaim ("complex
    corporate actions … NOT CERTIFIED"). It is recorded and counted. On its
    own it is not a certified-path error.
- **Scope of the result:** a specimen result says nothing about every suspect
  day. It never certifies all 17,469 observations.

## 4. The audit changes nothing

The audit does not rewrite returns, insert or alter corporate-action events,
change D5 dispositions, or regenerate D3, D5, D7a or D7c.

Every data error it finds is **recorded**. Whether Dataset v1 needs
remediation, and what kind, is a separate decision taken after the audit
result.

## 5. Tiingo cannot certify itself

Dataset v1's prices and corporate-action fields come from Tiingo. Tiingo
evidence (`splitFactor`, `divCash`, adjusted fields, Tiingo metadata, D3, D5,
D7a or D7c values) may be recorded as **context**. Rules:
- Tiingo evidence may locate a *recorded* event, for example to show that a
  split was misdated.
- Tiingo evidence alone never establishes any audit outcome other than
  UNRESOLVED.
- Every other outcome requires at least one independent primary document.

## 6. Historical occurrence is not point-in-time knowledge

The audit may use documents filed after the suspect day (up to the window in
the spec) to establish what **happened historically**.
- That is retrospective validation. It is **not** evidence of what a strategy
  could know at formation time.
- It does not upgrade any knowledge-date certification.
- The existing nonclaims stay unchanged, including vendor revision history,
  delisting-knowledge timing, issuer-identity knowledge timing and
  continuity, and complex corporate actions.

## 7. The evidence window stops before the reserve

- Every external document used must have an SEC filing date on or before
  **2023-06-30**.
- Every SEC index row used for issuer mapping or filing enumeration must also
  be dated on or before 2023-06-30.
- If a development suspect day can only be explained by later evidence, it
  stays UNRESOLVED.

## 8. Mapping audit results to gate 6

| Audit result (frozen rule in the spec) | Gate-6 status for development admission |
|---|---|
| GATE 6 SPECIMEN AUDIT PASS | PASS (development; reserve part DEFERRED) |
| GATE 6 SPECIMEN AUDIT HOLD — REMEDIATION REQUIRED | FAIL until a separate remediation decision resolves it |
| GATE 6 SPECIMEN AUDIT INCONCLUSIVE | NOT_ASSESSED, and still blocking. Any follow-up audit is a new, separately frozen version, and the original results are kept |

- **Planner judgement cannot change the result.** It is computed
  mechanically from the frozen rule.
- **No resampling:** no audit result authorizes drawing a further or
  replacement specimen.

## 9. Knowledge-PIT

The audit needs **no** Knowledge-PIT record.
- **Not a hypothesis test:** the audit is not a `HYPOTHESIS_FREEZE` or
  `PREREGISTRATION`. It tests no scientific hypothesis over outcomes and
  evaluates no factor.
- **No confirmation evidence:** it reads no confirmation evidence, so no
  `ACCESS` or `CONSUMPTION` record applies. The Phase-10 ACCESS writers are
  P10-H and P10-I, at evaluation ingestion.
- **No sealed reserve package:** its external evidence consists of public,
  development-period SEC filings, so no `ARTIFACT` registration applies (D8
  precedent).
- **Nothing derived:** it produces no `DERIVED` evaluation input.

This follows the D7c clarification §6 precedent for derived development
artifacts. No append is authorized.

## 10. Nonclaims

A PASS supports at most this claim:

> The predeclared development-only gate-6 specimen audit found no audit
> outcome requiring remediation under the frozen decision rule.

It does **not** claim:
- that every suspect observation is correct;
- that Tiingo's price level is verified for any specimen explained by a
  documented market event;
- that corporate-action semantics are universally certified;
- that point-in-time corporate-action knowledge is certified;
- that complex corporate actions are certified;
- reserve or confirmation eligibility;
- alpha, predictive validity, investability or production readiness.
