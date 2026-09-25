# Phase 10 — Knowledge-PIT & Confirmatory Evidence Protocol (IMPLEMENTATION PLAN / SPEC)

**STATUS: FROZEN PLAN — Barrier 0 PASSED (independent review, 2026-09-24).**
This document freezes the Phase-10 contracts, task DAG, ownership and
barriers. Execution proceeds wave by wave under `CLAUDE.md` §4, each wave
separately authorized; Track P (§9.7) requires its own PA-0 authorization. It
changes no production code, tests, data or sealed authority. No push, no tag.

- **Scientific authority:** `docs/planning/POST_PILOT1A_SCIENTIFIC_EVIDENCE_DESIGN.md`
  v3.1, frozen at commit `610fd0bca979f0c2eb04b7f95be4e9de3651f9d9`
  (hereafter **SD**). Where this plan and SD conflict, SD governs the
  science, and the conflict is itself a STOP condition (§24).
- **Baseline:** `master` at `610fd0bc…`, which contains `pilot1a-complete`
  → `1d1f70ae…` and `phase9-complete` → `76691c8…`.
- **Process authority:** `CLAUDE.md` (Planning Claude plans and reviews; Pi
  workers implement through Herdr; waves and barriers).

Barrier-0 decisions are marked **[R-n]**. The approved Barrier-0 decision
table is §25.

---

## 1. Phase objective

Establish a deterministic protocol governing the transition from
**exploratory empirical research** to **admissible confirmatory scientific
assessment**.

Core invariant:

> **EvidenceRole(E, H, K) is derived — never assigned — from an immutable,
> append-only Knowledge-PIT provenance history K.**

Phase 10 is the **Minimum Defensible Version** (SD §12). It is not a
general statistical-research framework.

- **Output today:** on every existing window, the correct answer is
  NOT_ASSESSED (SD §12, §18).
- **Value:** refusal and classification, not discovery.

---

## 2. Authority / baseline

### 2.1 Sealed authority (consumed read-only, never modified in place)

| Layer | Sealed guarantees Phase 10 relies on | Phase-10 use |
|---|---|---|
| Phase 6 | `FactorSpec` admission; `factor_spec_hash` | hypothesis identity input |
| Phase 7 | `evaluate()` as the sole metric authority; P7-B `align_forward_returns`; purge; `EvaluationRecord`; metric primitives (`rank_information_coefficient`, `information_coefficient`, with `.per_date`) | the only producer of empirical evidence and series (§12.1) |
| Phase 8 | `ExperimentRegistry` / `RegistrySnapshot`, `hypothesis_id_for`, `experiment_id_for`, `DecisionRecord` (governance ACCEPT), `HoldoutGovernance` (exact-id, per-experiment), search-family ledger | governance provenance only (§12.2); never the scientific judge |
| Phase 9 | `GenerationEvent` write-ahead; proposal registry; `GeneratorVisibleResearchHistory` positive allowlist (`DevelopmentFoldRole` excludes HOLDOUT); `ResearchFeedback` | source of machine exposure records (§12.3); firewall that Phase 10 must not breach |
| Pilot 1A | `pilot_evidence/pilot1a-real-deepseek-v1/**` (SHA256SUMS); journal `8189309…` | read-only adversarial reference (§19) |

### 2.2 Standing NOT CERTIFIED boundaries carried forward unchanged

- **Phase 7 + 9 temporal composition (FIX B, `pilot1-plan.md` §26c) stays
  NOT CERTIFIED.**
  - Phase 10 does **not** repair it; it compensates.
  - Knowledge-PIT footprints of generator-visible robustness aggregates are
    over-approximated to the whole evaluation range, including the holdout
    (§12.3). That makes any such exposure *detectable and fail-closed*.
  - It does not *prevent* the composition leak for development experiments.
- **Identifier continuity** across vendors and code changes stays NOT
  CERTIFIED (§6.7).
- **Semantic equivalence** stays NOT CERTIFIED (Phase-9 nonclaim; SD §10).
- **Pilot-1A comprehensive secret-value exclusion** stays NOT CERTIFIED
  until P10-V (§20). The historical run is never re-certified.

### 2.3 Sealed-authority rule

No Phase-10 task modifies:
- any existing file under `smart_beta/spec/`, `smart_beta/evaluation/`,
  `smart_beta/experiment/`, `smart_beta/research/` or `smart_beta/pilot/`;
- any existing test file;
- `pilot_evidence/**`;
- `docs/phase*_certification.md`;
- `worker_tasks/phase6..9/**` or `worker_tasks/pilot1/**`;
- SD.

The **only** exceptions are the explicitly owned paths in §16:
- the **new** additive Phase-7 file `smart_beta/evaluation/inferential_series.py`
  (P10-S);
- the **versioned observational fold-trace hook** in sealed
  `smart_beta/evaluation/engine.py` (P10-S), exactly as defined in §12.1(a)
  (amendment approved after the Wave-1 blocker). It changes no Phase-7
  evaluation semantics;
- P10-V's two pilot-harness files, pre-authorized as the future security fix
  in `pilot1-plan.md` §26g.

Every worker report must state **`sealed files modified: NONE`**, or list
exactly its authorized versioned-extension paths.

---

## 3. Frozen terminology

| Term | Meaning |
|---|---|
| **K** | the Knowledge-PIT log: one append-only, hash-chained record sequence per research environment (not per run) |
| **seq** | a record's 0-based position in K. **The authoritative order.** |
| **KnowledgeSnapshot** | `(length, head_hash)` of a K prefix; every derived result names one |
| **τ(H)** | seq of H's `HYPOTHESIS_FREEZE` record |
| **τ_P** | seq of the `PREREGISTRATION` record P. **The family freezes at τ_P.** |
| **EvidenceFootprint** | the canonical **source-observation footprint** (SOF): a set of `(subject_key, observation_kind, observation_date)`; derived quantities expand to it by frozen rules (§6) |
| **confirmation window** | the calendar interval [w₀, w₁] of formation dates evaluated confirmatorily; it is the HOLDOUT fold of the study's Phase-7 partition |
| **confirmation footprint** | the SOF observations dated in (w₀, w_end] used by the study (inputs and outcomes), where w_end = the last realization date |
| **conditioning footprint** | SOF observations dated ≤ w₀, used only as look-back; never part of freshness |
| **admitted procedure** | an `InferenceProcedure` version with a `PROCEDURE_ADMISSION` record in K and a matching implementation hash (§9.3) |
| **evidence role** | derived value of EvidenceRole(E, H, K) (§5) |
| **evidence grade** | G1–G5 (SD §5.1), a function of the role (§8) |
| **study** | one `ConfirmationStudy`, i.e. one preregistration, one confirmation artifact, one consumption |
| **family** | the members of one preregistration; m = number of members, fixed at τ_P |
| **assessment** | a `ScientificAssessment` for one member under one study, relative to one KnowledgeSnapshot |
| **θ′** | the direction-adjusted estimand: θ′ = s·θ, where s = +1 for POSITIVE and −1 for NEGATIVE. Null H0: θ′ ≤ 0. SESOI δ > 0 is stated in θ′ units. |

---

## 4. Exact contracts (P10-A: `smart_beta/science/contracts.py`)

P10-A freezes the closed vocabularies and the canonical serialization that
every later task imports. It has no behavior beyond validation, serialization
and hashing.

### 4.1 Canonical serialization

- `canonical_json(obj)`: UTF-8; `sort_keys=True`; separators `(",", ":")`;
  `ensure_ascii=False`.
  - Floats must be finite (non-finite → `ScienceContractError`).
  - Dates are ISO `YYYY-MM-DD`; UTC timestamps are ISO-8601 with `Z`.
  - Tuples serialize as lists; enums as their `.value`.
  - No `None` in hashed maps unless the field is declared optional.
- `content_hash(obj) = sha256(canonical_json(obj))` as lowercase hex.
- Timestamps and UUIDs never enter a semantic identity, with one exception:
  the `recorded_at` field of a K record is hashed (tamper-evidence) but is
  *metadata*, except where §5.5 uses it for G1.

### 4.2 Closed enums

- `RecordKind`: `ARTIFACT`, `DERIVED`, `GENERATOR_INPUT`, `HUMAN_DECISION`,
  `EXPOSURE_DECLARATION`, `ACCESS`, `HYPOTHESIS_FREEZE`, `PREREGISTRATION`,
  `CONSUMPTION`. Nine kinds; the justification is in §5.2.
- `Channel`: `GENERATOR`, `PROGRAM`, `HUMAN`, `PRETRAINING`, `PUBLIC`, `SYSTEM`.
- `Polarity` (declarations only): `EXPOSED`, `NOT_EXPOSED`.
- `EvidenceRole`, in frozen order from strongest to weakest:
  `CONFIRMATION_PROSPECTIVE` > `CONFIRMATION_HISTORICAL_RECORDED` >
  `CONFIRMATION_HISTORICAL_DECLARED` > {`ROBUSTNESS`, `DEVELOPMENT`,
  `UNKNOWN_EXPOSURE`}. The last three are equally inadmissible.
- `EvidenceGrade`: `G1`..`G5` (§8).
- `AssessmentState`: `SUPPORTED`, `NOT_SUPPORTED`, `INCONCLUSIVE`, `NOT_ASSESSED`.
- `Direction`: `POSITIVE`, `NEGATIVE`.
- `EstimandKind`: `MEAN_RANK_IC`, `MEAN_PEARSON_IC`, `MEAN_NET_LONG_SHORT`.
  Benchmark-adjusted alpha is **not** admissible (SD §6).
- There is **no closed test-procedure enum.** Procedures are registered
  `InferenceProcedure` contracts, gated by admission (§9, **[R-2]**).
- `PValueType`: `EXACT`, `ASYMPTOTIC`.
- `MissingnessPolicy`: `COMPLETE_REQUIRED`. This is the only v1 value, an
  implementation limitation (§9.5, **[R-7]**).
- `ObservationKind` (footprint): `PRICE_CHANGE`, `PRICE_LEVEL`,
  `TRADING_ACTIVITY`, `SHARES_OUTSTANDING`, `FUNDAMENTAL_REPORT`,
  `REFERENCE`, `MARKET_SERIES` (§6.2, **[R-4]**).
- `DeclarantRole`: `RESEARCHER`, `REVIEWER`, `OPERATOR`,
  `VENDOR_DOCUMENTATION`, `PUBLIC_RECORD` (§5.2a, **[R-8]**).
- `EffectSizeQualification`: `EFFECT_BELOW_SESOI`, `SESOI_NOT_EXCLUDED`,
  `NOT_APPLICABLE` (§11.2, **[R-1]**).
- `ReasonCode` (closed; each NOT_ASSESSED carries ≥ 1):
  `KNOWLEDGE_INTEGRITY_FAILURE`, `NOT_PREREGISTERED`, `PREREG_HASH_MISMATCH`,
  `ROLE_DEVELOPMENT`, `ROLE_UNKNOWN_EXPOSURE`, `ROLE_ROBUSTNESS`,
  `FOOTPRINT_ALREADY_CONSUMED`, `FOOTPRINT_OVERLAP_UNDETERMINABLE`,
  `FOOTPRINT_MISMATCH`, `GOVERNANCE_PROVENANCE_MISSING`,
  `GOVERNANCE_INVALID`, `SERIES_MISSING`, `SERIES_BINDING_FAILURE`,
  `MISSINGNESS_PATTERN_UNSUPPORTED`, `MISSINGNESS_POLICY_UNSUPPORTED`,
  `PROCEDURE_NOT_ADMITTED`, `PROCEDURE_IDENTITY_MISMATCH`,
  `PROCEDURE_REVOKED`, `PROCEDURE_ESTIMAND_UNSUPPORTED`,
  `PROCEDURE_PARAMS_INVALID`, `INFERENCE_INVALID`, `STUDY_INTERRUPTED`,
  `ESTIMAND_POLICY_VIOLATION`, `FIREWALL_VIOLATION`.
- Informational flags (not reasons): `DECLARATION_DEPENDENT`,
  `SERIES_IDENTICAL_GROUP`. The effect-size qualification is a dedicated
  field, not a flag (§11).

### 4.3 Constants

- `NULL_HYPOTHESIS = "theta_prime_le_0"`: the only null.
- `NOT_SUPPORTED_SCOPE = "HYPOTHESIS_LOCAL"`: constant. There is no
  family-wise alternative in Phase 10.
- `PRODUCTION_READINESS = "NOT_CERTIFIED"`: constant.
- `FOOTPRINT_MATERIALITY_OBSERVATIONS = 1`: any shared source observation
  is material (**[R-4]**).
- `DERIVATION_RULES_VERSION = "derivation-rules-v1"` (§6.3).
- `PROTOCOL_VERSION = "phase10-v1"`: included in every assessment hash.
- `validate_footprint_shape(body)`: a structural schema check of the §6.4
  canonical body (keys, types, sorted/disjoint intervals). There is no set
  algebra; it lets P10-B validate records without depending on P10-C.
  - **Corrected after the Wave-2 P10-C blocker.** The validator checks, and
    fails closed on, **only** the structural/canonical block properties
    frozen in §6.4:
    - subject keys within a block are strictly sorted and unique;
    - the canonical interval shape holds;
    - subject sets are disjoint across same-kind blocks;
    - no two same-kind blocks have identical interval lists;
    - blocks are strictly ordered by `(observation_kind,
      tuple(subject_keys))`.
  - It **must not** enforce one block per `observation_kind`.
  - It is a checker: it rejects a non-canonical body and never
    canonicalizes. Canonical construction is P10-C's job (§6.5).

### 4.4 Package surface

- `smart_beta/science/__init__.py` (P10-A) re-exports **only**
  `contracts`, so it is importable without sibling modules (the P9-C
  precedent).
- `tests/phase10_fixtures.py` (P10-A) holds shared *synthetic* builders:
  - synthetic calendars, security maps, panels and alignments;
  - K-log builders;
  - declaration helpers.

  Later tasks import it and never modify it; task-specific fixtures live in
  the task's own test file.

---

## 5. Knowledge-PIT model (P10-B log; P10-D role derivation)

### 5.1 Record envelope (P10-B: `smart_beta/science/knowledge.py`)

`KnowledgeRecord` (frozen):

| Field | Rule |
|---|---|
| `seq` | = log length at append; strictly increasing by 1 |
| `prev_hash` | `record_hash` of seq−1 (64 zeros for seq 0) |
| `kind` | `RecordKind` |
| `channel` | `Channel` |
| `program_id` | Phase-9 `ResearchProgram` id or `None` |
| `refs` | `{derived_from, included, influenced_by, consulted}`: tuples of `record_hash`es. **Every ref must name a record with a smaller seq**, so K is a DAG by construction and forward references are impossible. |
| `footprint` | canonical `EvidenceFootprint` body (§6) or `None`; required per kind (§5.2) |
| `event_time` | a date; only for `EXPOSURE_DECLARATION` with polarity `EXPOSED` (when the exposure happened, which may predate recording) |
| `recorded_at` | UTC timestamp at append (attested system clock) |
| `payload` | kind-specific map, validated by kind |
| `record_hash` | `content_hash` of all of the above |

**Append-only store:**
- a JSONL file with one canonical record per line;
- `fsync` after each append;
- an exclusive process lock;
- appends are atomic at the line level.

**Read path:**
- verify the chain from genesis;
- a truncated final line is reported as `TruncatedTail`; it is never
  silently dropped and never treated as a valid record;
- any hash or chain mismatch → `KnowledgeIntegrityError`;
- nothing is ever rewritten or deleted.

### 5.2 Record kinds (the minimum set; each is necessary)

| Kind | Why necessary | Required content |
|---|---|---|
| `ARTIFACT` | freshness and footprint identity of physical evidence | `footprint` (computed, never declared, §6.4); payload: `packaging_hash` (sha256 of bytes, metadata only), `sealed: bool`, `available_from: date` (attested), `source_label` (metadata only; **not identity**) |
| `DERIVED` | any function of evidence (metric, bit, series, assessment) carries its parents' exposure | `refs.derived_from` non-empty; `footprint` = the canonical union of the parents' footprints. The **writers** (P10-I, P10-H) compute it with P10-C at append; P10-D re-verifies it at every role computation (unverifiable → rule 2). The log itself (P10-B) validates footprint *shape* only (P10-A schema) and is footprint-algebra-agnostic. Payload: `derivation_kind`, `content_hash` of the derived object. |
| `GENERATOR_INPUT` | the exact machine channel into hypotheses | `refs.included` (the records the generator saw); payload: `generation_event_id`, `history_snapshot_hash`, generator/model identity; `footprint` = union over `included` (verified) |
| `HUMAN_DECISION` | program, prompt, policy and estimand-policy acts and family selection | `refs.consulted`, **or** `payload.consulted_all_prior = true` (the conservative default, meaning every record with a smaller seq); payload: `decision_kind` ∈ {`PROGRAM_FREEZE`, `PROMPT_CHANGE`, `POLICY_CHANGE`, `ESTIMAND_POLICY`, `FAMILY_SELECTION`, `PROCEDURE_ADMISSION`, `PROCEDURE_REVOCATION`, `OTHER`}, `actor_role`; the admission and revocation payloads are fixed in §9.3 |
| `EXPOSURE_DECLARATION` | unobservable channels, declared | the full `ExposureDeclaration` contract in §5.2a; `event_time` = `claim.exposure_event_date` (required iff EXPOSED) |
| `ACCESS` | a machine read of an artifact | payload: `artifact_record_hash`, `component` |
| `HYPOTHESIS_FREEZE` | τ(H) | payload: `hypothesis_id` (Phase 8), `proposal_id` (Phase 9, optional), `factor_spec_hash`; `refs.influenced_by` (generator inputs and human decisions) |
| `PREREGISTRATION` | τ_P; freezes the family | payload: full canonical `PreRegistration` body (§7) + its hash; `refs.influenced_by` = the members' `HYPOTHESIS_FREEZE` records + the `ESTIMAND_POLICY` record; `refs.consulted` (or `consulted_all_prior`) |
| `CONSUMPTION` | one-use governance | payload: `study_id`, `prereg_record_hash`, `artifact_record_hash`; `footprint` = the confirmation footprint consumed |

- **Deliberately not separate kinds:** confirmation series, inference results
  and assessments are `DERIVED` records (with `derivation_kind`). Anything
  produced from confirmation data therefore inherits the confirmation
  footprint and is exposure-tracked automatically.
- **Minimum representation:** one envelope with typed payloads, not ten
  classes. SD's candidate edge types map as follows:
  - `derived_from`, `included_in`, `influenced` and `consulted` → `refs`;
  - `observed_by` → `channel`;
  - `available_before` → `payload.available_from`;
  - `frozen_before` → `seq`;
  - `consumed_by` → `CONSUMPTION`.

### 5.2a `ExposureDeclaration` contract (frozen; R-8)

This is the payload of an `EXPOSURE_DECLARATION` record, schema
`exposure-declaration-v1`. It is immutable: there is no edit, retraction or
deletion, and corrections are new declarations.

| Field | Rule |
|---|---|
| `declaration_id` | = the envelope `record_hash` (derived, never supplied) |
| `schema_version` | `"exposure-declaration-v1"` |
| `declarant` | `{declarant_id, role}`. `declarant_id` is a stable pseudonymous id (no personal data). `role` ∈ `DeclarantRole` {`RESEARCHER`, `REVIEWER`, `OPERATOR`, `VENDOR_DOCUMENTATION`, `PUBLIC_RECORD`}. |
| `channel` | `HUMAN` \| `PRETRAINING` \| `PUBLIC` |
| `scope` | `{program_ids: non-empty tuple or ("*",), hypothesis_ids: tuple (empty = every hypothesis in scope)}` |
| `footprint` | a **determinable** source-observation footprint (§6) that the claim covers. An undeterminable scope → the append is rejected. |
| `claim` | `{polarity: EXPOSED \| NOT_EXPOSED, exposure_event_date (required iff EXPOSED), basis_hash, basis_reference?}` |
| channel extras | PRETRAINING: `{model_id, documented_cutoff (date \| "UNDOCUMENTED"), source_reference}`. PUBLIC: `{reference, class_match: bool}`, where `class_match` applies to the listed `hypothesis_ids`. |
| `knowledge_snapshot_ref` | `{length, head_hash}` of the K prefix the declarant attested against. It **must equal** the record's own `(seq, prev_hash)`; otherwise the append is rejected. The declaration is therefore bound to exactly the history that existed when it was made. |
| envelope | `seq`, `recorded_at` (§5.1) |

**Application rules (P10-D):**
1. A `NOT_EXPOSED` declaration counts for G2/G3 **only** if its seq < τ_P.
   By rule 2 (§5.4), any pre-τ_P access of overlapping data already fails
   closed, so τ_P is also the access boundary.
2. A `NOT_EXPOSED` declaration recorded at seq ≥ τ_P is ignored, so it
   **never upgrades** `UNKNOWN_EXPOSURE` → G3 (or anything else).
3. An `EXPOSED` declaration counts whenever it was recorded if either:
   - its seq < τ_P;
   - its `exposure_event_date` < date(`recorded_at` of P).

   Late exposure can only downgrade.
4. If `EXPOSED` and `NOT_EXPOSED` declarations overlap in scope and
   footprint, **EXPOSED wins**.
5. "Covering" means `scope` contains H and its program, and `footprint`
   ⊇ fp(E) as SOF.

**Mechanically certified:**
- the declaration's existence, content hash and immutability;
- its K order and snapshot binding;
- the coverage computation;
- the application rules above.

**Declaration-dependent (never certified):**
- the truth of the claim;
- the authenticity of the declarant's identity (no cryptographic signature in
  the MDV);
- the accuracy of a documented model cutoff or a public reference.

G3 results carry `DECLARATION_DEPENDENT`, plus the declaration hashes they
rely on.

### 5.3 Influence ancestry and exposed footprint (P10-D: `smart_beta/science/roles.py`)

For a preregistration P at τ_P and a member H (with program lineage L(H)):

1. **Seed set.**
   - `HYPOTHESIS_FREEZE(H)` and the refs of `PREREGISTRATION(P)`.
   - **Program-scope conservatism:** every record with channel ∈
     {GENERATOR, PROGRAM, HUMAN}, seq < τ_P, and `program_id` ∈ L(H).
   - **`consulted_all_prior` seeding (clarified before Wave-3 integration).**
     Explicit influence/reference edges are always seeds. For **every**
     record whose ancestry is constructed, **including the PREREGISTRATION
     P itself**, `consulted_all_prior = true` additionally seeds every
     eligible record with seq strictly smaller than that record's own seq,
     subject to the rules above.
     - A preregistration's `consulted_all_prior = true` is **not** ignored.
     - Ordering authority is K sequence and prefix membership, never
       wall-clock timestamps.
2. **Closure.** Follow `refs` (all four lists) transitively.
   `consulted_all_prior = true` adds every record with a smaller seq than
   that decision.
3. **Anc(H, P, K)** = the closure, restricted to seq < τ_P (automatic,
   because refs point backwards).
4. **ExposedFP(H, P, K) = ObservedFP ∪ DeclaredExposedFP.** Influence
   ancestry and empirical exposure are **distinct** concepts: membership in
   Anc does not by itself mean a record embodies an observation of data.
   - **ObservedFP** = ∪ footprint(r) for r ∈ Anc with record type ∈
     {**DERIVED**, **GENERATOR_INPUT**}. In Phase-10 v1, only these two
     record types embody empirical observation.
   - **Descriptor and governance records contribute no footprint merely by
     being in Anc:** `ARTIFACT`, `CONSUMPTION` and `EXPOSURE_DECLARATION`.
     They are not inert; they act only through their dedicated frozen
     rules:
     - declaration `EXPOSED` → the DeclaredExposedFP rule below;
     - declaration `NOT_EXPOSED` → coverage in §5.4 rules 4–5;
     - PRETRAINING/PUBLIC → the residual and class-match cap rules;
     - `ACCESS` → the dedicated pre-freeze ACCESS rule (§5.4 rule 2);
     - `CONSUMPTION` → ROBUSTNESS (rule 1a) and one-use governance
       (§6.8).
   - **Self-exposure is excluded.** A study's own sealed ARTIFACT that is
     in Anc (e.g. via `consulted_all_prior`) never makes the study
     DEVELOPMENT by carrying the study's evidence footprint.
   - **DeclaredExposedFP (dedicated declaration rule, unchanged)** = ∪
     footprints of every `EXPOSURE_DECLARATION` with channel HUMAN and
     polarity EXPOSED for which either holds, whether or not it is in Anc:
     - it was recorded at seq < τ_P;
     - **or** its `event_time` < date(`recorded_at` of P).

     The second case means a *late* declaration of an *earlier* exposure
     still counts, so it downgrades.
   - **Fail-closed rules are unchanged:** undeterminable overlap,
     unverifiable ancestry footprints, missing required declarations,
     pre-freeze ACCESS and unknown provenance still yield UNKNOWN_EXPOSURE
     (§5.4 rule 2) or NOT_ASSESSED.
5. **Residual declarations** (never role-raising):
   - PRETRAINING declarations for every generator model identity appearing
     in Anc;
   - PUBLIC declarations overlapping footprint(E).
6. **Mandatory P10-D regression cases for this clarification:**
   1. **Implicit prior empirical exposure:** a prior DERIVED record from
      another program, whose footprint overlaps E, recorded at seq < τ_P
      and not listed in `refs.consulted`, while P has `consulted_all_prior
      = true` → **DEVELOPMENT** (never G2).
   2. **ARTIFACT self-exposure protection:** the study's own sealed ARTIFACT
      is in Anc via `consulted_all_prior`. Its footprint contributes nothing
      to ExposedFP, and there is no downgrade from that fact alone.
   3. **NOT_EXPOSED declaration:** an `EXPOSURE_DECLARATION(NOT_EXPOSED)`
      in Anc is **not** unioned into ExposedFP; it acts only through
      coverage.
   4. **EXPOSED declaration path separation:** a HUMAN
      `EXPOSURE_DECLARATION(EXPOSED)` overlapping E downgrades through
      DeclaredExposedFP, **not** through ObservedFP. This is made
      observable, e.g. ObservedFP ∩ fp(E) = ∅ while the role is
      DEVELOPMENT, and a late EXPOSED declaration outside Anc still
      downgrades.

### 5.4 EvidenceRole(E, H, P, K) — frozen rule order (first match wins)

Preconditions:
- K must verify; otherwise the computation raises and the study is
  NOT_ASSESSED with `KNOWLEDGE_INTEGRITY_FAILURE`. No role is emitted.
- If `HYPOTHESIS_FREEZE(H)`, `PREREGISTRATION(P)` or `ARTIFACT(E)` is
  absent, the role is `UNKNOWN_EXPOSURE`.

`overlap(A, B)` returns {`DISJOINT`, `OVERLAP`, `UNDETERMINABLE`} (§6.6).

| # | Role | Condition |
|---|---|---|
| 1a | `ROBUSTNESS` | overlap(fp(E), fp(c)) = OVERLAP for some `CONSUMPTION` c whose preregistration contains H |
| 1b | `DEVELOPMENT` | overlap(fp(E), ExposedFP) = OVERLAP |
| 2 | `UNKNOWN_EXPOSURE` | any of: overlap = UNDETERMINABLE; an Anc record lacks a required footprint or has an unverifiable one; a generator identity in Anc has no PRETRAINING declaration recorded before τ_P; an `ACCESS` record with seq < τ_P exists for **any** artifact whose footprint overlaps (or undeterminably overlaps) fp(E) |
| 3 | `CONFIRMATION_PROSPECTIVE` | seq(ARTIFACT(E)) > τ_P **and** min date of fp(E) > date(`recorded_at` of P) **and** `available_from`(E) > date(`recorded_at` of P) |
| 4 | `CONFIRMATION_HISTORICAL_RECORDED` | seq(ARTIFACT(E)) < τ_P, `sealed = true`; HUMAN `NOT_EXPOSED` covering fp(E) recorded before τ_P; a PUBLIC declaration covering fp(E) recorded before τ_P; **no** PUBLIC declaration with `class_match = true` listing H (§5.2a) |
| 5 | `CONFIRMATION_HISTORICAL_DECLARED` | E is historical (not rule 3), rule 4 fails, and HUMAN `NOT_EXPOSED` plus PUBLIC declarations covering fp(E) exist, recorded before τ_P. Covers: the artifact was recorded after τ_P but its data predate P; it was unsealed at ingestion; or the PUBLIC class-match cap applies. |
| 6 | `UNKNOWN_EXPOSURE` | otherwise (fail-closed catch-all) |

"Covering" is defined in §5.2a: scope contains H, and the declaration footprint ⊇ fp(E) as a source-observation footprint.

### 5.5 Invariants (mechanically tested)

1. **Derivation.** `evidence_role()` is a pure function of (E, H, P, K
   prefix). There is no setter, no persisted role field and no role input
   on any public constructor. A role may be cached only keyed by
   KnowledgeSnapshot.
2. **Append-only.** No API edits or deletes; corrections are new records.
3. **Downgrade-only monotonicity.** For every K′ extending K,
   role(E, H, P, K′) ≤ role(E, H, P, K) in the §4.2 order. Tested with a
   property-style randomized-append generator seeded deterministically.
   - Late `EXPOSED` declarations may downgrade.
   - A `NOT_EXPOSED` declaration counts only if recorded at seq < τ_P;
     non-exposure is never back-filled.
4. **Chain closure.** Take W → DERIVED(OOS metric for H1) → GENERATOR_INPUT
   g → HYPOTHESIS_FREEZE(H2) (via `influenced_by`), with seq(g) < τ(H2).
   Then any E overlapping W is DEVELOPMENT for H2. The same holds when the
   DERIVED record is a single pass/fail bit.
5. **Relativity.** The same E can be CONFIRMATION for H1 and DEVELOPMENT
   for H2.
6. **G1 time basis.** G1 compares data dates with the attested
   `recorded_at` of P. The system clock is an attested input (a nonclaim,
   §22). `seq` alone orders everything else.

### 5.6 Deterministic replay

`replay(K_path, snapshot)` recomputes every role, footprint union,
inference, Holm result and assessment from the K prefix plus the
content-addressed study store (§13). All hashes must equal the recorded
ones. Any difference → `ReplayMismatchError`.

---

## 6. EvidenceFootprint model (P10-C: `smart_beta/science/footprint.py`) — REPAIRED at Barrier 0 (R-4)

### 6.1 Frozen invariant

Confirmation freshness belongs to **underlying empirical source
observations**, never to derived metric cells, filenames, artifact hashes,
vendor objects, dataset ids or run ids.

- **MDV rule:** any shared governed source observation ⇒ not fresh.

### 6.2 Source-observation primitive

**Review of the v1 tuple `(security_key, variable_class, date)`:
insufficient.**

1. It treated *derived* quantities (returns, forward returns) as cells. An
   h-day return was a block of `PRICE_RETURN` cells on the sessions [t, t+h].
   - Two adjacent, non-overlapping returns therefore shared the boundary
     session and produced spurious overlap.
   - Price levels and price changes were conflated.
2. It had no subject for **market-level series**: risk-free rate, index
   levels, benchmark factors. Excess or benchmark-relative quantities could
   not expand to all of their sources.
3. Fundamentals keyed by an unspecified date could make a restatement or a
   later vintage of the same report look like a new observation, and so
   "fresh".

**Revised primitive (frozen):**

```
SourceObservation = (subject_key, observation_kind, observation_date)
```

| Component | Rule |
|---|---|
| `subject_key` | either `SEC:<MARKET>:<canonical_code>` (from a frozen, hashed **SecurityMap**: vendor identifier → canonical key) or `MKT:<series_id>` (from a frozen, hashed **MarketSeriesMap**). Unmapped → undeterminable. |
| `observation_kind` | a closed, source-level `ObservationKind` enum (below) |
| `observation_date` | the session date of the observation. For `FUNDAMENTAL_REPORT`, the **fiscal period end**, so every vintage, restatement and vendor copy of the same report is the same observation. |

`ObservationKind`:

| Kind | Meaning |
|---|---|
| `PRICE_CHANGE` | one session's close-to-close price change of a security, including that session's distribution and corporate-action adjustment |
| `PRICE_LEVEL` | a session's price level (open, high, low, close, adjusted or not) |
| `TRADING_ACTIVITY` | volume, amount, turnover on a session |
| `SHARES_OUTSTANDING` | share count on a session |
| `FUNDAMENTAL_REPORT` | one fiscal-period report of a security, all vintages |
| `REFERENCE` | listing, status or ST flags on a session |
| `MARKET_SERIES` | one session's value of an `MKT:` series |

**Linkage rule (frozen, conservative):** `PRICE_LEVEL(s, d)` is treated as
overlapping `PRICE_CHANGE(s, d)` and `PRICE_CHANGE(s, next_session(d))`,
because consecutive levels reveal the change between them. No other
cross-kind linkage exists in the MDV.

### 6.3 Source-observation footprint vs derived-evidence descriptor

- **Source-observation footprint (SOF):** the canonical set of
  `SourceObservation`s. **All freshness, overlap, consumption and
  ExposedFP computations use the SOF only.**
- **Derived-evidence descriptor (DED):** an informational list of `(derived
  variable, parameters, formation dates)`, kept for audit. It is **never**
  used for freshness.
- **Derivation rules (frozen, versioned `DERIVATION_RULES_VERSION`,
  closed):** each derived variable expands conservatively back to its SOF.

| Derived variable | SOF expansion |
|---|---|
| `RETURN_1D(s, d)` | `PRICE_CHANGE(s, d)` |
| `FWD_RETURN(s, t, h)` | `PRICE_CHANGE(s, d)` for the h sessions d ∈ (t, t+h] of the frozen PIT calendar |
| `EXCESS_RETURN(...)` | the return's expansion ∪ `MARKET_SERIES(rf, same dates)` |
| `BENCHMARK_RELATIVE(...)` | the return's expansion ∪ `MARKET_SERIES(benchmark, same dates)` |
| `PRICE_FIELD(s, d)` | `PRICE_LEVEL(s, d)` (plus the linkage rule) |
| `ACTIVITY_FIELD(s, d)` | `TRADING_ACTIVITY(s, d)` |
| `MARKET_CAP(s, d)` | `PRICE_LEVEL(s, d)` ∪ `SHARES_OUTSTANDING(s, d)` |
| `FUNDAMENTAL_FIELD(s, t)` | `FUNDAMENTAL_REPORT(s, p)` for **every** fiscal period p whose report the Phase-6 PIT selection may use at t within the declared look-back |
| `SIGNAL(FactorSpec, t)` | the union of its data requirements' expansions over [t − lookback, t]; the look-back comes from the Phase-6 expression windows. Unknown look-back → undeterminable. |
| aggregate (per-date IC, portfolio return, metric, bit, series, assessment) | the union of its inputs' expansions |

- A vendor column maps to a derived variable through a frozen, hashed
  **VariableMap**. An unmapped column, a missing rule or a missing calendar
  → `determinable = false`.
- **Confirmation footprint:** the SOF observations dated in **(w₀,
  w_end]** that the study's derived quantities expand to (inputs and
  outcomes). w_end is the last realization date.
- **Conditioning footprint:** SOF observations dated ≤ w₀, used only as
  look-back.

With `PRICE_CHANGE` atoms, a development return realized at or before w₀ and
a confirmation forward return formed at w₀ share **no** observation. A
correct Phase-7 purge therefore does not produce artificial overlap. A purge
failure does produce overlap and is caught (§18).

### 6.4 Canonical form and identity

- **Canonical body:** `{schema: "evidence-footprint-v2", determinable,
  unresolved, derivation_rules_version, security_map_hash,
  market_series_map_hash, variable_map_hash, calendar_hash, blocks, ded}`.
  - Each block is `{observation_kind, subject_keys: sorted unique,
    intervals}`, where `intervals` are sorted, disjoint, maximal closed
    calendar intervals.
  - Two intervals merge when no session of the frozen calendar lies strictly
    between them.
  - Normalization: expand to per-(kind, subject) interval lists; merge; group
    subjects with identical interval lists. The result is unique for a given
    SOF.
  - **Block structure (frozen; corrected after the Wave-2 P10-C blocker).**
    - A block is one `observation_kind` plus the canonical set of subjects
      that share **exactly the same** canonical interval list.
    - A footprint **may contain multiple blocks with the same
      `observation_kind`**; a repeated `observation_kind` is valid.
    - **Within a block:** `subject_keys` are non-empty, strictly sorted and
      unique. `intervals` use the canonical closed-interval representation,
      are strictly ordered and do not overlap.
    - **Across blocks of the same `observation_kind`:**
      - subject sets are **disjoint**, so each `(observation_kind,
        subject_key)` belongs to exactly one block;
      - no two blocks have identical interval lists. Blocks that would have
        identical lists are non-canonical, and their subject sets must be
        merged into one block.
    - **Canonical block order:** blocks are strictly ascending by the total,
      deterministic key `(observation_kind, tuple(subject_keys))`, compared
      lexicographically. The key uses the complete subject tuple, not only
      the first subject. It is total because subject sets of same-kind blocks
      are disjoint and non-empty.
    - This representation preserves every subject↔date association. Two
      source-observation footprints that share no `(subject_key,
      observation_kind, observation_date)` therefore never collapse to the
      same body or `footprint_id` merely because they use the same
      `observation_kind`.
    - Footprint semantics (R-4, §6.1–6.3, §6.6) are unchanged.
- **`footprint_id`** = `content_hash({schema, determinable, unresolved,
  blocks})`. Identity is the SOF only: the maps, calendar, rules version and
  DED are audit fields. Two vendors whose data map to the same observations
  therefore have the same id.
- An undeterminable footprint lists its `unresolved` items. Its id never
  equals a determinable id.
- **Declared footprints** (preregistrations of windows that may lie in the
  future, §7.2) use *every calendar day* in (w₀, w₁ + `realization_bound_days`]
  for each declared kind and subject. This conservative superset needs no
  future calendar.

### 6.5 Construction (never declared for artifacts)

- `footprint_from_panel(frame, key_columns, variable_map, security_map,
  market_series_map, calendar, rules)` reads **only** identifier columns,
  date columns and column names. It never reads values, so computing a
  footprint observes no outcome (this is what makes G2 sealing possible).
- `expand(derived_variable, params, dates, subjects)` applies the §6.3
  rules.
- Set operations: `union`, `intersect`, `restrict(fp, (d0, d1])`, and
  `covers(a, b)`. `covers` means a ⊇ b as SOF, with calendar-day declared
  blocks covering any session in range.
- A DERIVED record's SOF = the union of its parents' SOFs. **Transformations,
  horizon changes, file splits, renames and vendor repackaging can never
  manufacture fresh evidence.**

### 6.6 Overlap policy (frozen)

`overlap(a, b)`:
- `UNDETERMINABLE` if either `determinable = false`;
- `OVERLAP` if ≥ `FOOTPRINT_MATERIALITY_OBSERVATIONS` (= 1) source
  observations are shared, counting linkage (§6.2);
- else `DISJOINT`.

| Case | Detection | Result |
|---|---|---|
| exact reuse, renamed file | same SOF | OVERLAP |
| copied or split into files | union of parts = original SOF; each part intersects | OVERLAP |
| same observations, another vendor | same subject keys, kinds, dates after mapping | OVERLAP; if a mapping is missing → UNDETERMINABLE |
| partial time overlap | interval intersection | OVERLAP (≥ 1 shared observation) |
| partial universe overlap | subject-key intersection | OVERLAP |
| transformed (rank, z-score, winsorize, excess vs raw) | derivation rules → same `PRICE_CHANGE` atoms | OVERLAP |
| different horizons from overlapping returns (h = 5 vs 20) | both expand to `PRICE_CHANGE` on shared sessions | OVERLAP |
| later vintage or restatement of the same fundamentals | same (subject, `FUNDAMENTAL_REPORT`, period end) | OVERLAP |
| adjacent, non-overlapping returns (development ≤ w₀, confirmation > w₀) | disjoint `PRICE_CHANGE` atoms | DISJOINT |
| unknown mapping, missing rule, calendar gap, missing parents | `determinable = false` | UNDETERMINABLE → fail closed |

**Carving (R-4):**
- A preregistration may declare a confirmation footprint that excludes
  consumed observations, e.g. a disjoint universe or a later window, but only
  at preregistration.
- At consumption, the artifact's computed confirmation SOF must be ⊆ the
  declared footprint; otherwise `FOOTPRINT_MISMATCH`.
- Carving never happens after data access.

### 6.7 What the MDV can and cannot detect

- **Can:** reuse of the same source observations under renaming, copying,
  splitting, repackaging, transformation, horizon change, vintage change,
  and re-vendoring *where the maps link both vendors to the same subject
  key*.
- **Cannot — nonclaim:** the same observation under an **unlinked**
  identifier change. **Unlinked identifier continuity is NOT CERTIFIED.** A
  known code change must be linked explicitly in the frozen SecurityMap.
- **Cannot — nonclaim:** general cross-vendor semantic equivalence, and
  economically related but distinct observations (e.g. an index level vs its
  constituents' prices unless an `MKT:` series is mapped to its constituents;
  an ADR vs its local share).
- **Cannot — nonclaim:** observations not mapped by any rule; these are
  undeterminable and refused, never assumed fresh.

### 6.8 Persistent cross-run governance

- The consumption ledger **is** K (`CONSUMPTION` records carrying SOFs). One
  K exists per research environment and persists across runs.
- Freshness is checked against **all** prior `CONSUMPTION` SOFs globally,
  whatever the hypothesis, program, run_id, file, vendor or artifact hash.
- A new run_id, file, vendor or packaging hash never changes an SOF.

---

## 7. PreRegistration model (P10-E: `smart_beta/science/preregistration.py`)

### 7.1 EstimandPolicy (frozen before exploration)

`EstimandPolicy{program_id, estimand_kind, horizon, construction (n_groups,
cost_bps, winsorization, factor_missing_policy — the Phase-6/7 factor-panel
policy, distinct from the §9.5 inferential `MissingnessPolicy`), sesoi δ (> 0, θ′ units),
sesoi_justification_hash}`. The direction is declared per member in the
preregistration (§7.2).

- It is recorded as a `HUMAN_DECISION` with `decision_kind =
  ESTIMAND_POLICY`, **before the program's first `HYPOTHESIS_FREEZE`**.
- **MDV:** exactly one primary estimand per program. There are no
  hypothesis-class menus, so there is nothing to shop between (SD §6).
- Any member whose estimand, horizon, construction or δ differs from the
  policy → `ESTIMAND_POLICY_VIOLATION` (preregistration refused).

### 7.2 PreRegistration (one object; the family lives here) [R-5a]

The family belongs **directly in the PreRegistration**, not in a separate
StudyProtocol.
- Membership, α, the confirmation footprint and every member contract must
  freeze atomically under one hash at one τ_P.
- Splitting them would create an ordering hazard: family changes after
  member freezes.

`PreRegistration`:

| Field | Content |
|---|---|
| `prereg_id` | `content_hash` of the body below |
| `family_id` | = `prereg_id` (one family per preregistration) |
| `estimand_policy_record` | `record_hash` of the ESTIMAND_POLICY decision |
| `members` | a tuple, sorted by `hypothesis_id`, of `MemberContract` |
| `alpha_study` | α for Holm, in (0, 0.5) |
| `confirmation` | `{window: [w0, w1], realization_bound_days, subjects: subject-key set, observation_kinds, declared_footprint (canonical calendar-day SOF body, §6.4), partition_spec (the canonical sealed Phase-7 PartitionRef representation with HOLDOUT = window; see "Frozen identity details" below), dataset_contract: VariableMap + SecurityMap + MarketSeriesMap + calendar hashes + DERIVATION_RULES_VERSION}` |
| `partition_ref_hash` | the §4.1 canonical SHA-256 identity of the canonical `partition_spec`, i.e. `content_hash(PartitionRef.to_dict())`. Semantics are identical to the frozen P10-S `InferentialSeriesBundle.partition_ref_hash` (§12.1(b)). |
| `analysis_plan_id` | `content_hash` of `{PROTOCOL_VERSION, DECISION_RULE_VERSION, per-member (estimator_id, procedure_ref, params, missingness_policy, bound_alpha)}` |
| `power_disclosure` | per member `{mde, sigma_lr, T_conf, source_record}` or `{unavailable_reason}`. Disclosure is mandatory; **there is no gate** (SD §7.4, §12). |

**Frozen identity details (clarified before Wave-3 integration, after the
P10-E review):**
- **Partition authority.**
  - `partition_spec` is the canonical persisted representation of the
    sealed Phase-7 `PartitionRef`.
  - Runtime `Partition` / `Partition.partition_id` is **not** the Phase-10
    preregistration identity, and there are never two competing partition
    identities.
  - Canonical path: input → `PartitionRef.from_dict(input)` →
    `PartitionRef.to_dict()`. The supplied representation must **already
    equal** that result, or the preregistration is **refused**.
  - A non-canonical caller representation is never silently normalized.
  - Validation authority is the sealed `PartitionRef`. P10-E does not
    reproduce its rules locally.
- **`partition_ref_hash`** = §4.1 `content_hash(PartitionRef.to_dict())`,
  the same frozen serialization and hash semantics as P10-S.
  - Separate call sites (P10-E via `smart_beta.science.contracts`, P10-S via
    its local §4.1 implementation) must produce identical values, and
    cross-module equality is tested. No second, divergent hash algorithm
    exists.
- **Cross-phase binding (future P10-H requirement):**
  `preregistration.partition_ref_hash ==
  inferential_series_bundle.partition_ref_hash`.
  - A mismatch fails closed.
  - There is no fallback to `Partition.partition_id`, to date-window
    equality alone, or to a locally reconstructed partition identity.
- **`DECISION_RULE_VERSION = "phase10-decision-rule-v1"`**, a frozen
  Phase-10 protocol constant hashed into `analysis_plan_id`. It is protocol
  authority, not a worker-local choice. Any future change to the scientific
  decision semantics (§11.2) requires a new decision-rule version, and the
  v1 string never silently acquires new semantics.
- **`dataset_contract`** is accepted with exactly the fields already frozen
  by the §6.4 footprint audit contract (`security_map_hash`,
  `market_series_map_hash`, `variable_map_hash`, `calendar_hash`) plus
  `derivation_rules_version`. There are no worker-chosen fields.

`MemberContract`:

| Field | Content |
|---|---|
| `hypothesis_id` | Phase-8 hypothesis id |
| `hypothesis_freeze_record` | the member's `HYPOTHESIS_FREEZE` record hash (seq < τ_P) |
| `factor_spec_hash` | Phase-6 identity |
| `estimand_kind`, `horizon`, `construction` | must equal the EstimandPolicy |
| `direction` | Direction |
| `null` | `NULL_HYPOTHESIS` (constant) |
| `sesoi` | δ from the policy |
| `estimator_id` | `"mean_per_date_v1"`: the arithmetic mean of the member's per-date series of `estimand_kind` on the HOLDOUT fold |
| `procedure_ref` | `{procedure_id, version, contract_hash}` of an **admitted** InferenceProcedure (§9.3) |
| `admission_record_hash` | `record_hash` of the exact `PROCEDURE_ADMISSION` record the preregistration relies on. It must lie in the preregistration-freeze Knowledge-PIT prefix (§7.3 check 4, §9.3), and is bound here for deterministic replay. |
| `params` | valid under the procedure's `param_schema` |
| `dependence_justification_hash` | why the procedure's dependence assumptions fit this estimand, horizon and overlap. `dependence_design_id = content_hash(procedure_ref, params, justification)`. |
| `missingness_policy` | `MissingnessPolicy` (v1: `COMPLETE_REQUIRED`), which must be ∈ the procedure's `supported_missingness` |
| `bound_alpha` | α for the hypothesis-local one-sided upper bound (NOT_SUPPORTED and effect-size qualification) |

### 7.3 Validation at preregistration (refuse → no PREREGISTRATION record)

1. Every member's `HYPOTHESIS_FREEZE` exists with seq < now; the
   ESTIMAND_POLICY precedes all of them.
2. There are no exact duplicates, i.e. members with the same
   (`factor_spec_hash`, estimand, construction).
3. The declared confirmation footprint is determinable, and it is
   `DISJOINT` from **every** prior `CONSUMPTION` footprint.
   - OVERLAP → `FOOTPRINT_ALREADY_CONSUMED`.
   - UNDETERMINABLE → `FOOTPRINT_OVERLAP_UNDETERMINABLE`.
4. **Admission ordering — P10-E owns this check (clarified before
   Wave 3).** For each member, the record named by `admission_record_hash`
   must:
   - belong to the **preregistration-freeze Knowledge-PIT prefix**, i.e.
     the K prefix visible when the PREREGISTRATION record is appended. That
     means seq < τ_P.
   - be a `HUMAN_DECISION` record with `decision_kind =
     PROCEDURE_ADMISSION`, whose `procedure_id`, `version` and
     `contract_hash` equal the member's `procedure_ref`.

   There must be no `PROCEDURE_REVOCATION` for that version in the same
   prefix. The procedure must support the estimand and missingness policy,
   and `params` must validate (§9.3).

   **Mechanical binding:** every `admission_record_hash` is included in the
   PREREGISTRATION record's `refs.consulted`. Because K rejects refs to
   later or unknown records (§5.1), an admission created after the freeze
   can never be referenced. **No retrospective admission can make an
   already-frozen preregistration admissible.**

   An admission that is absent from the prefix, later than it,
   unverifiable, or mismatched to `procedure_ref` fails closed: the
   preregistration is refused with `PROCEDURE_NOT_ADMITTED`, or
   `PROCEDURE_IDENTITY_MISMATCH` for a contract mismatch.

   **Authority:** record sequence and prefix membership are the only
   authority for admission ordering. Wall-clock `recorded_at` timestamps
   are metadata and never establish admission-before-freeze.

   `partition_spec` places the whole window in the HOLDOUT fold.
5. No retroactivity: τ_P = the append seq. No field references a record
   with seq ≥ τ_P. **Retrospective preregistration is impossible by
   construction.** "Backdating" is meaningless, because order is seq, and
   `recorded_at` is written by the log.

After τ_P, the preregistration is immutable. Any change is a **new**
preregistration with a new id and a new τ_P. Roles are recomputed at the new
τ_P, where any access to the old study's data has already made those source observations
exposed.

---

## 8. Evidence-grade model (SD §5.1, frozen mapping)

| Role | Grade | Confirmatory inference admissible | Assessment qualifier |
|---|---|---|---|
| CONFIRMATION_PROSPECTIVE | **G1** | yes | — |
| CONFIRMATION_HISTORICAL_RECORDED | **G2** | yes | residuals disclosed |
| CONFIRMATION_HISTORICAL_DECLARED | **G3** | yes, labeled `DECLARATION_DEPENDENT` | residuals disclosed; the separation is a declaration |
| DEVELOPMENT, ROBUSTNESS | **G4** | no → NOT_ASSESSED | — |
| UNKNOWN_EXPOSURE | **G5** | no → NOT_ASSESSED | — |

- **Residual disclosures** are attached to every G1–G3 assessment as record
  hashes plus summaries:
  - PRETRAINING model id and cutoff vs the window, including "window starts
    after the documented cutoff" when true;
  - PUBLIC declarations;
  - HUMAN declarations.
- They never raise a role. G1 and G2 carry the literal text *"pretraining
  exposure: not reconstructable; residual disclosed"* whenever any window
  date ≤ the documented cutoff.

---

## 9. Inference contract (P10-F: `smart_beta/science/inference.py`) — REPAIRED at Barrier 0 (R-2, R-7)

### 9.1 Principle

Phase 10 does **not** freeze any statistical test as scientific authority.

Holm requires **valid individual p-values under their nulls**: P(p ≤ u) ≤ u,
exactly or asymptotically as the procedure's contract states. It does
**not** require finite-sample exact p-values merely because Holm is used.
The Holm guarantee inherits the validity type of the procedures that produce
the p-values.

Phase 10 therefore provides an **InferenceProcedure contract, an admission
gate and a single-dispatch executor**. It ships **zero admitted production
procedures**. Statistical certification of any procedure is a separate,
bounded track (§9.7, P10-P).

### 9.2 `InferenceProcedure` contract (frozen shape)

Every implementation registers exactly one `InferenceProcedureContract`:

| Field | Content |
|---|---|
| `procedure_id` | stable identifier |
| `version` | semver string; any behavior change is a new version |
| `supported_estimands` | subset of `EstimandKind` |
| `null_semantics` | must equal `NULL_HYPOTHESIS` (H0: θ′ ≤ 0) |
| `direction_semantics` | how `Direction` maps to θ′ = s·θ (must match §3) |
| `dependence_assumptions` | declared text + hash (e.g. stationarity, mixing, overlap structure). **Recorded, not certified by Phase 10.** |
| `sample_requirements` | machine-checkable predicates on the input (minimum n, parameter ranges relative to n) |
| `param_schema` | the closed schema of parameters a preregistration may set |
| `p_value_semantics` | `{type: EXACT \| ASYMPTOTIC, statement}`: a one-sided p-value for H0 valid under the stated assumptions |
| `bound_semantics` | a one-sided upper confidence bound for θ′ at level 1 − `bound_alpha`, with the same validity type and assumptions |
| `supported_missingness` | subset of `MissingnessPolicy` (§9.5) |
| `failure_conditions` | the enumerated conditions that yield INVALID, each mapped to a reason code |
| `implementation_identity` | `{module, qualname, source_sha256}`. `source_sha256` is computed at runtime from the implementation's source file bytes. |
| `test_only` | bool; `true` procedures are refused by the production registry |
| `contract_hash` | `content_hash` of all fields except `implementation_identity` |

### 9.3 The two gates: preregistered **and** admitted

A scientific verdict requires **both** of the following. Failing either →
NOT_ASSESSED; there is **never** a substitute procedure.

1. **Preregistered.**
   - `MemberContract.procedure_ref = {procedure_id, version, contract_hash}`.
   - `params` are valid under `param_schema`.
   - `missingness_policy` ∈ `supported_missingness`.
   - `estimand_kind` ∈ `supported_estimands`.

   All of these are checked at preregistration (§7.3) and again at
   execution.
2. **Phase-10-admitted.** K holds a `HUMAN_DECISION` with `decision_kind =
   PROCEDURE_ADMISSION`, with seq < τ_P, whose payload names:
   - `procedure_id`, `version`, `contract_hash`;
   - `implementation source_sha256`;
   - `validation_dossier {path, sha256}`;
   - `review_record` (the PA barrier record, §17).

   Before inference runs, all of the following are checked:
   - the executing registry's implementation hashes to the admitted
     `source_sha256` and `contract_hash`
     (else `PROCEDURE_IDENTITY_MISMATCH`);
   - the implementation is not `test_only` in a production registry;
   - there is no `PROCEDURE_REVOCATION` for that version with seq ≤ the
     execution snapshot (else `PROCEDURE_REVOKED`).

   A revocation recorded later **downgrades** existing assessments on
   reassessment (§11.3). It never upgrades them.

**Temporal authority and component responsibilities (clarified before
Wave 3; not a redesign).**
- **Record sequence and Knowledge-PIT prefix membership are authority;
  timestamps are metadata only.** Every ordering decision below is
  replayable from immutable K sequence and prefix evidence.
- **Preregistration (P10-E):** "was the procedure already admitted when the
  study froze?" P10-E verifies and binds the exact admission record in the
  preregistration-freeze prefix (§7.3 check 4).
- **Execution (P10-H):** "is that exact admission still valid in the
  execution snapshot?" P10-H owns the orchestration of historical records
  and snapshots. For each member it:
  - supplies `run_inference` with the **exact preregistered admission
    record** (verified equal to `admission_record_hash`) plus **every**
    `PROCEDURE_REVOCATION` record in the authorized execution-snapshot
    prefix;
  - records the snapshot `(length, head_hash)` in the assessment.

  A revocation of that version in the execution snapshot yields
  `PROCEDURE_REVOKED`.
- **Dispatch (P10-F):** remains the single deterministic inference
  dispatcher. It enforces its frozen admission, identity and refusal checks
  on exactly the records supplied to it. It does **not** reconstruct
  historical order. P10-H can never bypass P10-F: inference runs only
  through `run_inference`.

Reason codes: `PROCEDURE_NOT_ADMITTED`, `PROCEDURE_IDENTITY_MISMATCH`,
`PROCEDURE_REVOKED`, `PROCEDURE_ESTIMAND_UNSUPPORTED`,
`PROCEDURE_PARAMS_INVALID`, `MISSINGNESS_POLICY_UNSUPPORTED`.

### 9.4 Invocation and hard rules

- **Signature:** `run_inference(request, registry, admission_records) ->
  InferenceResult`. It is pure and deterministic, with no I/O. It takes K
  records as plain validated mappings, so P10-F does not import P10-B.
- **Request:** the member contract; the member's HOLDOUT-fold per-date
  series of exactly `estimand_kind` from the P10-S bundle; the series record
  hash; the expected formation-date index of the HOLDOUT fold (§9.5).
- **One primary estimand per member.** Secondary quantities are never passed
  in.
- **No endpoint switching:** the estimand, series and fold come from the
  contract, never from the call site.
- **No hidden fallback:** exactly one dispatch, to `procedure_ref`. There is
  no try/except that switches procedure, version, parameters or estimator.
  Tested by making every other registered procedure raise on call.
- **No automatic repair:** no winsorizing, dropping, re-lagging or
  re-windowing after data are seen.
- **Missing inputs:** an absent series → INVALID (`SERIES_MISSING`). There
  is never a substitute metric.
- **Contracted failures:** any `failure_conditions` match, any unmet
  `sample_requirements`, or any non-finite statistic, p-value or bound →
  INVALID with the mapped reason (`INFERENCE_INVALID` by default). An
  INVALID result carries **no** p-value and no bound.

### 9.5 `MissingnessPolicy` (preregistered; R-7)

- `MemberContract.missingness_policy` is a frozen, preregistered field, and
  procedures declare `supported_missingness`.
- **Phase-10 v1 defines and supports only `COMPLETE_REQUIRED`:** the
  series' date index equals the HOLDOUT fold's expected formation dates
  (from the partition and frozen calendar, after purge) and every value is
  finite.
- **Violation of the declared policy** → INVALID with
  `MISSINGNESS_PATTERN_UNSUPPORTED` → NOT_ASSESSED.
- **A policy that the procedure does not support** →
  `MISSINGNESS_POLICY_UNSUPPORTED`, and the preregistration is refused.
- **Stated limitation:** `COMPLETE_REQUIRED` is a Phase-10 **implementation
  limitation**, not a theorem that scientific inference requires complete
  financial panels. Future admitted procedures may support other
  preregistered policies (e.g. declared maximum gaps with a stated
  treatment) by adding `MissingnessPolicy` values, without changing the core
  ontology.

### 9.6 `InferenceResult` (frozen)

`{status: VALID|INVALID, reason?, procedure_ref, implementation_source_sha256,
admission_record_hash, params, missingness_policy, n, estimate_theta_prime,
se?, statistic?, p_one_sided, p_value_semantics_type, bound_alpha,
upper_bound_theta_prime, input_series_hash, result_hash}`.
- Floats are serialized exactly (`repr` round-trip).
- The `p_value_semantics_type` is copied into the assessment, so an
  asymptotic guarantee is never reported as exact.

### 9.7 First procedure: separate architecture from statistical certification

- **Architecture (P10-F, on the Phase-10 critical path):** the contract,
  registry, gates, executor and INVALID handling. Its tests use `test_only`
  fixture procedures in injected registries (e.g. a deterministic procedure
  returning a p-value from a lookup table), admitted in *test* K fixtures.
  Nothing statistical is certified by P10-F.
- **Statistical certification (P10-P, separate track, not on the
  Phase-10 certification critical path):**
  - **PA-0, candidate spec freeze** (separate authorization): the planner
    proposes one minimal candidate. Non-authoritative candidates: an
    equal-weighted-cosine HAR t-test (Lazarus, Lewis, Stock & Watson 2018),
    a non-overlapping-observations t-test, NW/HAC with fixed-b critical
    values. The freeze covers the estimand, assumptions, parameters and
    validation design.
  - **P10-P implements it** in `smart_beta/science/procedures/<id>.py`, plus
    a **validation dossier** `docs/phase10/procedures/<id>-<version>-validation.md`
    containing:
    - the primary source and equations;
    - the assumptions;
    - a deterministic, seeded Monte-Carlo size and bound-coverage study over
      declared DGPs, covering the declared T range, AR(1)/MA(h−1) dependence
      grids, heavy tails, and the cross-sectional-average structure of the
      estimand;
    - a test that reproduces the dossier tables bit-identically;
    - limitations.
  - **PA, admission barrier:** independent statistical review of the
    dossier. Only then is a `PROCEDURE_ADMISSION` record appended, by
    explicit human decision.
- **Until PA passes,** every real study is NOT_ASSESSED
  (`PROCEDURE_NOT_ADMITTED`). This is the correct, honest outcome.

### 9.8 Power disclosure

`power_disclosure` in the preregistration is a mandatory, *declared* field:
`{mde, method_statement, source_record}` or `{unavailable_reason}`. Phase 10
computes nothing from it and gates nothing on it (SD §7.4).

---

## 10. Holm-family semantics (P10-G: `smart_beta/science/assessment.py`)

- **Freeze point:** the family is frozen at τ_P.
  - Identity: `family_id = prereg_id`.
  - Members: `prereg.members`; m = `len(members)`.
  - Primary tests: `members[i].procedure_ref` + `params` (admitted
    procedures only).
  - α: `alpha_study`.
  - Also frozen: `confirmation study = study_id`, `analysis_plan_id`.
  - Nothing added or removed later can change m.
- **Order:** Holm runs **only after** every member's inference has been
  computed by its preregistered procedure.
- **Inputs:** exactly the frozen members, in `hypothesis_id` order. An extra
  or missing member → `HolmFamilyMismatchError`, so no assessment is
  produced.
- **Inadmissible members [R-3]:** a member whose inference is INVALID, or
  whose role is inadmissible, gets state NOT_ASSESSED. It enters the
  step-down with **p := 1** (a predeclared, conservative rule; m stays the
  frozen size). This preserves FWER for the remaining members and never
  produces a rejection for that member.
- **Step-down:**
  - sort by (p, hypothesis_id);
  - reject p_(k) ≤ α/(m−k+1) while that holds;
  - adjusted p̃_(k) = max_{j≤k} min(1, (m−j+1)·p_(j)).
- **`holm_rejected(i)`** ⇔ p̃_i ≤ α_study.
- **Validity inheritance:** Holm needs valid individual p-values (exact or
  asymptotic, as each admitted procedure declares), not finite-sample exact
  ones. The assessment records each member's `p_value_semantics_type`.
- **Explicit limits:**
  - Holm does **not** validate individual p-values. Their validity rests on
    the admitted procedure's documented validation and stated assumptions.
  - Holm does **not** correct the adaptive exploration history.
  - Holm provides **no** family-wise control for NOT_SUPPORTED.

---

## 11. ScientificAssessment state machine (P10-G)

### 11.1 Record

`ScientificAssessment` is frozen, content-hashed and separate from the
Phase-8 `DecisionRecord`. It carries no reference to a DecisionOutcome.

| Field | Content |
|---|---|
| `assessment_id` | content hash |
| identity | `protocol_version`, `study_id`, `prereg_id` (= `family_id`), `analysis_plan_id`, `hypothesis_id`, `artifact_record_hash`, `footprint_id` |
| snapshot | `knowledge_snapshot {length, head_hash}` |
| evidence | `evidence_role`, `evidence_grade`, `residual_disclosures` |
| `governance_validity` | VALID / INVALID / MISSING (§12.2) |
| `state` | `AssessmentState` |
| `economic_state` | = `state` if `estimand_kind = MEAN_NET_LONG_SHORT`, else NOT_ASSESSED |
| `reason_codes` | sorted tuple |
| `flags` | sorted tuple |
| `inference` | `InferenceResult` summary + `result_hash`; `procedure_ref`, `implementation_source_sha256`, `admission_record_hash`, `p_value_semantics_type`; `dependence_design_id`; `missingness_policy` |
| `primary_null_rejected` | bool, or `null` if NOT_ASSESSED (§11.2) |
| `sesoi_excluded_by_upper_bound` | bool, or `null` if NOT_ASSESSED (§11.2) |
| `effect_size_qualification` | `EffectSizeQualification` (§11.2) |
| `multiplicity` | `{family_id, m, alpha_study, holm_rank, holm_adjusted_p, holm_rejected}` |
| `not_supported_scope` | `HYPOTHESIS_LOCAL` (constant) |
| `series_identical_group` | members whose confirmation series are byte-identical (annotation only; reporting does not double-count support) |
| constant | `production_readiness = NOT_CERTIFIED` |
| provenance | the DERIVED record hashes of the series, inference and assessment |

### 11.2 Frozen evaluation (R-1 repaired: no precedence hiding)

**Step 1 — admissibility.**
- The state is **NOT_ASSESSED** if any reason applies. **All** applicable
  reason codes are collected. Reasons:
  - K integrity failure;
  - not preregistered, or a preregistration hash mismatch;
  - role ∉ {G1, G2, G3};
  - footprint consumed, undeterminable or mismatched;
  - governance provenance missing or invalid;
  - procedure not admitted, revoked, identity mismatch, or unsupported
    estimand, parameters or missingness;
  - series missing or binding failure;
  - missingness pattern unsupported;
  - INVALID inference;
  - study interrupted;
  - estimand-policy violation;
  - a firewall violation affecting the member.
- In that case both booleans below are `null`, and
  `effect_size_qualification = NOT_APPLICABLE`.

**Step 2 — two independent, always-recorded determinations.** For an
admissible member, compute both:
- `primary_null_rejected` ⇔ `holm_rejected`: evidence that θ′ exceeds the
  **null boundary 0**, at family-wise level α_study.
- `sesoi_excluded_by_upper_bound` ⇔ UB < δ: the hypothesis-local
  predeclared bound places θ′ **below the SESOI**.

These are different claims. "θ′ > 0" is **not** "θ′ ≥ δ", and neither
determination is suppressed by the other.

**Step 3 — deterministic mapping (a total function of the two booleans):**

| `primary_null_rejected` | `sesoi_excluded_by_upper_bound` | `state` | `effect_size_qualification` |
|---|---|---|---|
| true | false | **SUPPORTED** | `SESOI_NOT_EXCLUDED` |
| true | true | **SUPPORTED** | `EFFECT_BELOW_SESOI` |
| false | true | **NOT_SUPPORTED** | `NOT_APPLICABLE` |
| false | false | **INCONCLUSIVE** | `NOT_APPLICABLE` |

**Semantics:**
- **SUPPORTED:** the confirmatory primary test rejects H0: θ′ ≤ 0 in the
  declared direction after the frozen family's Holm procedure. The guarantee
  is Holm FWER over the frozen family, conditional on valid individual
  p-values of the declared validity type.
  - SUPPORTED **never** asserts that θ′ reaches δ.
  - `EFFECT_BELOW_SESOI` means the effect is detectably positive, but its
    predeclared hypothesis-local bound lies below the SESOI. It is **not**
    NOT_SUPPORTED.
  - `SESOI_NOT_EXCLUDED` means only that the bound does not exclude δ. It is
    not evidence that θ′ ≥ δ.
- **NOT_SUPPORTED:** the null is not rejected, and the predeclared
  hypothesis-local bound excludes the SESOI. It is hypothesis-local, with no
  family-wise claim.
- **INCONCLUSIVE:** neither determination holds.
- A G3 result always carries `DECLARATION_DEPENDENT`.

### 11.3 Snapshot relativity (downgrade-only reassessment)

- An assessment is valid relative to its KnowledgeSnapshot.
- `reassess(assessment, K_now)` recomputes the roles against K_now. If
  downgraded, it emits a **new** DERIVED reassessment record, e.g. state
  NOT_ASSESSED with `ROLE_DEVELOPMENT` after a late human-exposure
  declaration.
- The original is never mutated, and a reassessment can never upgrade.

---

## 12. Phase-7/8/9 interfaces

### 12.1 Phase-7 versioned interface (P10-S) — AMENDED (Wave-1 blocker)

**Amendment provenance.** The v1 text (frozen at `dea829c`) was blocked by
P10-S in Wave 1 as a specification defect. This amendment was approved by
independent review, with explicit authorization for ONE narrow,
observation-only modification of the sealed `smart_beta/evaluation/engine.py`.

**Frozen invariant.** Phase 7 owns empirical evaluation semantics. Phase 10
consumes authoritative outputs from that execution. Phase 10 never
reconstructs or independently reimplements Phase-7 evaluation semantics.

**Why v1 was insufficient (evidence, `engine.py` at `dea829c`):**
- No authoritative alignment digest exists: `ForwardReturnAlignment` carries
  no hash.
- `evaluate()` builds `primary_panel = winsorize_panel(primary_alignment.panel,
  primary_point.winsorization)`, using the grid point for `spec.horizons[0]`,
  **before** slicing folds, and `EvaluationRecord` does not persist the
  parameter. The v1 sidecar could not observe the transformed panel.
- Fold metrics come from independent per-fold `evaluate_portfolio(sliced,
  …)` runs, whose first rebalance has turnover 0. Slicing the whole-sample
  `cost_adjusted_series` is therefore not equivalent.

**(a) Versioned observational hook in sealed `smart_beta/evaluation/engine.py`
(the only authorized sealed Phase-7 change):**
- Signature: `evaluate(..., *, fold_trace_sink=None)`, a keyword-only
  argument, default `None`.
- **When `None`:** control flow, arithmetic and the returned
  `EvaluationRecord` are byte-identical to the sealed version.
- **When supplied:**
  - Once, before the fold loop, the engine calls
    `fold_trace_sink.record_primary(horizon, n_groups, winsorization,
    transaction_cost_bps)`, passing the values the engine actually uses
    (`primary_horizon`, `primary_point.n_groups`,
    `primary_point.winsorization`, `spec.cost_model.transaction_cost_bps`).
  - For each fold, in partition order, immediately after `fold_portfolio =
    evaluate_portfolio(sliced, …)` (the hook stays at this authorized
    observation point), the engine calls `fold_trace_sink.record_fold(fold_key,
    role, panel=<isolated deep copy of sliced>, portfolio=<isolated deep copy
    of fold_portfolio>)`.
    - The portfolio copy must isolate every mutable structure reachable
      through the sink-visible object: at minimum `net_returns`,
      `gross_returns`, `turnover`, `costs` and `accounting`.
- **Observation-isolation invariant (frozen; amended after the P10-S-R2
  review):** every mutable object visible to `fold_trace_sink` is isolated
  from every mutable object subsequently used by authoritative Phase-7
  execution. The sink shares **no** mutable state with authoritative engine
  objects. "Read-only by contract" is **not** sufficient; isolation is by
  copy.
- **Observation only, never intervention:** the engine **ignores callback
  return values**. The sink has no mechanism for changing the panels the
  engine uses, portfolio results, parameters, control flow, metrics or
  `EvaluationRecord` contents. The engine uses no value that is read back
  from the sink.
- **Sole side effect:** a sink exception propagates and fails the
  evaluation closed **before** holdout consumption (step 8).
- No other function or file changes.

**(b) Additive module `smart_beta/evaluation/inferential_series.py` (new):**
- **`FoldTraceCollector`** implements the sink. It is append-only and
  single-use: a second primary record, a duplicate fold key, or use after
  `seal()` is refused. Its callbacks return `None`.
- **`build_inferential_series(record, collector) -> InferentialSeriesBundle`**
  never loads data, aligns, winsorizes, slices, forms portfolios or calls
  `evaluate()`.
- **Per fold, `rank_ic` / `pearson_ic`** =
  `rank_information_coefficient(trace.panel).per_date` /
  `information_coefficient(trace.panel).per_date`: the sealed P7-D primitive
  applied to the isolated copy of the engine's actual fold panel.
- **Per fold, `net_long_short` / `gross_long_short`** =
  `trace.portfolio.net_returns` / `.gross_returns`: the isolated copy of the
  actual per-fold execution output, neither recomputed nor sliced from a
  whole-sample series.
- **Series mapping:** series map to Phase-7 `Series` (dates → `date`;
  non-finite values → `None`). Dates the primitive omits (fewer than 2
  finite pairs) stay absent; the preregistered `MissingnessPolicy` handles
  them downstream.
- **Bundle:** `{schema: "inferential-series-v2", evaluation_record_hash =
  record.content_hash, spec_hash, partition_ref_hash, primary: {horizon,
  n_groups, winsorization, transaction_cost_bps} as reported by the engine,
  folds: (fold_key, role, rank_ic, pearson_ic, net_long_short,
  gross_long_short, bound)}`, plus a `content_hash`.
  - **`partition_ref_hash`** is the deterministic SHA-256 identity of the
    canonical persisted `record.partition` (`PartitionRef`) used for binding.
    It is **not** asserted to equal Phase-7 `Partition.partition_id`, and no
    compatibility alias named `partition_id` exists. If future code needs the
    runtime Phase-7 partition identity, it must be exposed separately through
    a future authorized, versioned interface.
  - **`bound`** is a per-inferential-series mapping `{rank_ic: bool,
    pearson_ic: bool, net_long_short: bool}`, not one boolean per fold
    (approved interpretation).
  - The content hash follows §4.1's canonical rules, implemented locally;
    `smart_beta.science` is never imported.
  - **`alignment_digest` is removed**, because no authoritative Phase-7
    digest exists. Binding is established by the record hash, by tracing
    within the same call, and by bit-equal aggregates.

**(c) Binding cross-check (fails closed with `SeriesBindingError`):**
1. The collector's fold keys and roles equal `record.fold_results`, in
   order, and there is exactly one primary record.
2. For each fold and each metric present in `record.fold_results` among
   {`ic`, `rank_ic`, `long_short`, `turnover_cost_adjusted`}, the sealed
   primitive applied to the traced object must give `.to_metric_value(key)`
   equal to the record's value: bit-equal value and equal `n_obs`. The
   primitives are `information_coefficient(panel)`,
   `rank_information_coefficient(panel)`,
   `long_short_mean_tstat(portfolio.gross_returns)` and
   `long_short_mean_tstat(portfolio.net_returns)`.
3. `spec_hash` and `partition_ref_hash` (computed from `record.partition`)
   are consistent, and the primary horizon matches.
4. A series is `bound` only if its estimand's metric was selected in
   `spec.metrics`. Mapping: `MEAN_RANK_IC` ↔ `rank_ic`; `MEAN_PEARSON_IC` ↔
   `ic`; `MEAN_NET_LONG_SHORT` ↔ `turnover_cost_adjusted`. An unbound,
   missing or mismatched series fails closed, and Phase 10 refuses it
   downstream as `SERIES_BINDING_FAILURE`.

**(d) Caller contract (P10-H):** one `evaluate(...,
alignments_by_horizon=…, fold_trace_sink=collector)` call per member, then
`build_inferential_series(record, collector)`. Collectors are single-use.

**Escalation:** any need to change sealed Phase-7 code beyond hook (a) is a
STOP.

### 12.2 Phase-8 interface (read-only; P10-I `adapters.py`)

`governance_provenance(hypothesis_id, registry_snapshot, search_ledger) ->
GovernanceProvenance{hypothesis_id, experiment_ids, family_id, search_status,
decision_record_hashes}`.
- VALID iff the hypothesis is registered and has an admissible
  search-governance status.
- MISSING → `GOVERNANCE_PROVENANCE_MISSING`; conflict or invalid →
  `GOVERNANCE_INVALID`.
- Phase-8 `DecisionRecord` outcomes are recorded as provenance hashes only.
  **Governance ACCEPT is never read as, mapped to, or combined into a
  scientific state.** Historical Phase-8 decisions are not reinterpreted.

**Confirmation-consumption ownership — decided: Phase 10, not a Phase-8
extension [R-6].**
- Phase-8 `HoldoutGovernance` is exact-id and per-experiment
  (`consumed_by` = one experiment id). It explicitly does not detect
  overlap.
- A multi-member family consumes one footprint in one batch, so reusing the
  Phase-8 mechanism would either conflict or need a sealed change.
- The K `CONSUMPTION` ledger is stricter (footprint overlap) and already
  persistent and cross-run.
- Phase-8 holdout governance is **not used** by confirmation studies and
  stays untouched.
- Confirmation evaluations are **not** registered as Phase-8 experiments, do
  not consume Phase-8 search budget, and produce no DecisionRecord. A search
  attempt and a statistical test are distinct (SD §3).

### 12.3 Phase-9 interface (read-only; P10-I `adapters.py`)

No Phase-9 metadata extension is required. Knowledge-PIT influence edges are
reconstructed read-only:

- **Ingest per GenerationEvent:**
  - one `GENERATOR_INPUT` (`generation_event_id`, `history_snapshot_hash`,
    model identity);
  - `included` refs to DERIVED records for exactly the items in the
    generator-visible history at that snapshot.
- **Development-evidence footprints (conservative):**
  - fold metrics (IS/OOS/WF) → the SOF expansion (§6.3 rules) of the fold's
    signals and forward returns over its universe;
  - **robustness, subperiod, parameter and universe aggregates → the whole
    evaluation range, including the holdout** (the FIX-B-compensating
    over-approximation, §2.2).
- **Unreconstructable visible history:**
  - If the registry order is reconstructable, `included` over-approximates
    to all EvaluationRecords registered before the event (determinable).
  - Otherwise the GENERATOR_INPUT is undeterminable → UNKNOWN_EXPOSURE.
- **Registered evaluations:** every Phase-8-registered `EvaluationRecord`
  of a program → a DERIVED record with channel PROGRAM, whose footprint is
  the **entire** evaluation including the holdout (the judge and humans may
  have seen it).
- **Hypotheses:** `HYPOTHESIS_FREEZE` per admitted proposal/experiment, with
  `influenced_by` = its GenerationEvent's GENERATOR_INPUT.
- **Program freezes:** each `ResearchProgram` freeze → a `HUMAN_DECISION
  PROGRAM_FREEZE` with `consulted_all_prior = true`, unless a narrower
  consulted list is declared.

**Firewall (§14):** Phase-10 objects live only in the Phase-10 study store
and K. `GeneratorVisibleResearchHistory` (sealed, allowlist) has no field
that can carry them, and confirmation evaluations never enter the Phase-8
registry that P9-B projects.

---

## 13. Persistence / replay / crash-consistency (P10-B, P10-H)

### 13.1 Stores

- **K:** one JSONL log per research environment (a configured path; tests
  use tmp dirs).
- **Study store:** `<env>/phase10/studies/<study_id>/`, content-addressed
  JSON objects:
  - the preregistration body;
  - the EvaluationRecord per member;
  - the InferentialSeriesBundle per member;
  - the InferenceResults;
  - the Holm table;
  - the assessments.

  Written atomically (tmp + fsync + rename); an object's filename is its
  hash.

### 13.2 Study state machine (P10-H: `smart_beta/science/study.py`)

```
PREREGISTERED --(checks pass)--> CONSUMED --> EVIDENCE_PERSISTED --> INFERRED --> ASSESSED
      |                                  \--(crash before evidence)--> TERMINAL_NOT_ASSESSED(STUDY_INTERRUPTED)
      \--(roles/footprint/governance inadmissible)--> REFUSED (NOT_ASSESSED; no data read; no CONSUMPTION)
```

1. **`ingest_confirmation_artifact`:** hash the bytes; compute the
   footprint from key columns only; append `ARTIFACT (sealed=true)`.
2. **`execute(study_id)`:** verify K and the preregistration hash; compute
   roles and grades per member; run consumption checks (in-window footprint
   ⊆ declared; disjoint from all prior CONSUMPTION); check governance
   provenance.
   - Any failure for **any** member → the whole study is REFUSED:
     NOT_ASSESSED assessments are persisted for every member, but **no
     CONSUMPTION is appended and no outcome value is read**. There are no
     partial families.
   - After consumption, a member can become NOT_ASSESSED only through
     series or inference failure (then p := 1, §10).
3. **Write-ahead:** append `CONSUMPTION`, then `ACCESS`, **before** any
   outcome value is read.
4. Derive alignments once (P7-B). Per member, create a fresh single-use
   `FoldTraceCollector` and run `evaluate(..., alignments_by_horizon=...,
   fold_trace_sink=collector)` (a fresh Phase-7 in-memory holdout token per
   call). Then run `build_inferential_series(record, collector)` (§12.1(d)).
   Persist both, and append DERIVED records.
5. Re-check each member's procedure admission, identity and revocation
   against the authorized execution K snapshot (§9.3 temporal authority).
   Pass `run_inference` exactly the preregistered admission record (matching
   `admission_record_hash`) and every `PROCEDURE_REVOCATION` in the
   execution-snapshot prefix. Run inference per member through the single
   dispatch → DERIVED records.
6. Run Holm and the assessments → DERIVED records. The study is ASSESSED.

### 13.3 Crash rules

- **CONSUMPTION present, evidence not persisted:** the study is terminal
  NOT_ASSESSED (`STUDY_INTERRUPTED`).
  - The footprint stays consumed. There is **no re-read and no
    re-registration**; a new study would face the consumed footprint.
- **Evidence persisted:** resume steps 5–6 deterministically from the stored
  objects; the result is identical.
- **Same study_id with a different preregistration or artifact hash:** hard
  error.

### 13.4 Replay

`replay_study(K, store, study_id)` recomputes the roles (at the recorded
snapshot), inference, Holm and assessments. Every hash must match.

**Tamper detection:**
- a K chain break, an edited record, or a reordered line →
  `KnowledgeIntegrityError`;
- a store object whose hash ≠ its name → `StudyStoreIntegrityError`.

---

## 14. Generator firewall requirements

1. **Structural:**
   - Phase-10 results are never written to the Phase-8 registry, the
     Phase-9 proposal registry or `ResearchFeedback`.
   - Confirmation evaluations are not Phase-8 experiments.
   - P10-Z asserts that `GeneratorVisibleResearchHistory` built from
     authorities after a completed study contains no Phase-10 object, no
     confirmation-fold value and no assessment state.
2. **Audit:** `audit_generator_inputs(K) -> FirewallAuditReport` flags every
   `GENERATOR_INPUT` that meets either condition:
   - its footprint OVERLAPs (or undeterminably overlaps) any
     `CONSUMPTION` footprint;
   - its `included` closure contains any DERIVED record descended from a
     confirmation study (series, inference, assessment — SUPPORTED **or**
     NOT_SUPPORTED **or** any bit).

   Violations → `FIREWALL_VIOLATION`. Affected later hypotheses are already
   DEVELOPMENT by rule 1b (mechanically, no manual relabel).
3. **Future development cycle:** a program that deliberately feeds
   confirmation results to a generator starts a new development cycle. The
   evidence becomes development evidence for later hypotheses automatically
   through K. **No code for this is written in Phase 10**, and the sealed
   Phase-9 projection offers no channel for it.

---

## 15. Task DAG

```
                 Barrier 0 (this plan frozen + authorized)
                                  |
   Wave 1:  P10-A contracts   P10-S series (Phase-7 additive)   P10-V secret sweep
                                  |
                              Barrier 1
                                  |
   Wave 2:  P10-B knowledge log   P10-C footprint   P10-F inference
                                  |
                              Barrier 2
                                  |
   Wave 3:  P10-D roles   P10-E preregistration   P10-I Phase-8/9/Pilot adapters + firewall audit
                                  |
                              Barrier 3
                                  |
   Wave 4:  P10-G assessment + Holm
                                  |
                              Barrier 4
                                  |
   Wave 5:  P10-H confirmation study (orchestration, crash, replay, Pilot-1A retrospective)
                                  |
                              Barrier 5
                                  |
   Wave 6:  P10-Z certification & adversarial suite
                                  |
                           Final Barrier

   Track P (separate; NOT on the Phase-10 certification critical path; may start after B2):
       PA-0 candidate-procedure spec freeze (separate authorization)
         -> P10-P first procedure + validation dossier
         -> PA admission barrier (independent statistical review)
         -> human PROCEDURE_ADMISSION record
```

Dependencies:

| Task | Depends on |
|---|---|
| A | none |
| S | none |
| V | none |
| B | A |
| C | A |
| F | A, S |
| D | B, C |
| E | A, B, C |
| G | A, E, F |
| I | B, C |
| H | B–G, I, S |
| Z | A–I, S, V (not P) |
| P | A, F, S; PA-0 authorization |

No task in a wave owns a file owned by another task, and no cycles exist.

---

## 16. Per-task ownership and contracts

Everywhere below, "forbidden" includes every sealed path in §2.3 and every
file owned by another task. Every task report must include: the tests run,
the adversarial tests, the completion SHA, and `sealed files modified: NONE`
(or its authorized paths).

**P10-A — contracts** (Wave 1)
- **Owns:** `smart_beta/science/__init__.py`, `smart_beta/science/contracts.py`,
  `tests/test_science_contracts.py`, `tests/phase10_fixtures.py`.
- **Contract:** §4, exactly.
- **Tests:**
  - canonical-JSON stability and key order;
  - non-finite floats rejected;
  - enums closed (unknown value rejected);
  - hash golden values;
  - importing `smart_beta.science` does not import sibling modules.
- **Adversarial:** a timestamp or UUID in a semantic payload → rejected;
  equal content → equal hash.
- **Evidence:** golden-hash table in the test file.

**P10-S — inferential series, Phase-7 versioned interface** (Wave 1; AMENDED; re-run as P10-S-R2)
- **Owns (new files):** `smart_beta/evaluation/inferential_series.py`,
  `tests/test_evaluation_inferential_series.py`,
  `tests/test_evaluation_engine_fold_trace.py`.
- **Owns (authorized sealed modification):** `smart_beta/evaluation/engine.py`,
  **only** the §12.1(a) observational hook.
- **Forbidden:**
  - `smart_beta/evaluation/__init__.py`, `metrics.py`, `portfolio.py`,
    `robustness.py`, `spec.py`, `forward_returns.py`, `partition.py`;
  - every other sealed path;
  - every existing test file, including `tests/test_evaluation_engine.py`;
  - any change to `engine.py` other than the hook.
- **Contract:** §12.1 (amended).
- **Mandatory regression and adversarial evidence** (each item an explicit
  test):
  1. `evaluate(..., fold_trace_sink=None)` produces the same
     `EvaluationRecord`/`content_hash` as the pre-hook behaviour, on the
     existing engine fixtures (golden hashes taken at `dea829c`).
  2. `evaluate(..., fold_trace_sink=collector)` produces the same
     `EvaluationRecord`/`content_hash` as `sink=None`.
  3. `record_primary` reports exactly the engine-used primary horizon,
     `n_groups`, winsorization and transaction cost.
  4. Fold traces contain the actual engine-created winsorized sliced panel
     and per-fold portfolio.
  5. Per-date IC/rank-IC are computed only from the traced fold panel, with
     the existing sealed metric primitives.
  6. Net/gross long-short series come directly from the traced fold
     portfolio.
  7. The sidecar never loads market data, aligns, winsorizes, slices, forms
     portfolios or calls `evaluate()`. Monkeypatched sentinels on
     `align_forward_returns`, `winsorize_panel`, `evaluate_portfolio`,
     `evaluate` and data loaders raise if called during
     `build_inferential_series`.
  8. Sink return values cannot influence engine execution: a sink returning
     arbitrary objects yields an identical record.
  9. Sink mutation of its received panel cannot alter the `EvaluationRecord`.
  9b. **Adversarial portfolio mutation (added after the P10-S-R2 review):**
      a malicious sink mutates every practically mutable sink-visible
      portfolio component, at minimum `net_returns`, `gross_returns` and any
      mutable accounting/table structure (`accounting`, `turnover`, `costs`).
      Compared with the no-sink baseline, these mutations must not alter
      subsequent Phase-7 metric computation, any `EvaluationRecord` field,
      `EvaluationRecord.content_hash` or holdout semantics. The test must
      prove isolation by mutation, not rely on object identity or a
      "read-only" convention. A no-shared-mutable-state check (no
      `numpy.shares_memory` between sink-visible and engine-held arrays) is
      also required.
  10. A sink exception propagates and prevents holdout consumption (the
      `HoldoutRegistry` remains unconsumed).
  11. Fold metric binding is bit-equal in value and equal in `n_obs` to the
      corresponding `FoldResult`, including a winsorization ≠ 0 fixture and
      a multi-fold fixture where fold turnover differs from the global
      series.
  12. `alignment_digest` does not exist in the amended contract, and the
      bundle exposes `partition_ref_hash` with no `partition_id` alias.
      `bound` is the per-series mapping.
  13. Missing, unbound or mismatched series (tampered record, wrong
      collector, duplicate or out-of-order folds, reused collector) fail
      closed with `SeriesBindingError`.
  14. `metrics.py`, `portfolio.py`, `robustness.py` and `spec.py` are
      byte-identical to `dea829c` (a digest test).
  15. Existing tests remain unmodified.
- **Evidence:**
  - `git diff --stat` showing only the owned paths;
  - the exact `engine.py` diff, containing only the hook;
  - the full suite with provider credentials scrubbed process-locally.

**P10-V — full-package secret-value sweep (P1A-SV)** (Wave 1)
- **Owns:** `smart_beta/pilot/runner.py`, `smart_beta/pilot/artifacts.py`
  (the authorized versioned changes, `pilot1-plan.md` §26g),
  `tests/test_pilot_secret_sweep.py` (new).
- **Forbidden:** `pilot_evidence/**`, every other pilot file, and existing
  pilot tests (which must pass **unmodified**).
- **Contract:** exactly `pilot1-plan.md` §26g.
- **Tests:** synthetic sentinels only; a sentinel in each artifact class
  (raw, JSON-escaped, URL-encoded, base64) → fail closed; the clean package
  → pass; file count asserted; the value is never printed or written.
- **Adversarial:**
  - an unreadable file, or a count mismatch, → fail closed;
  - the credential is detected by captured name, not only by the fixed
    list;
  - no real key and no provider call in any test.
- **Evidence:** `pilot_evidence/**` unchanged (SHA256SUMS verify).

**P10-B — Knowledge-PIT log** (Wave 2)
- **Owns:** `smart_beta/science/knowledge.py`, `tests/test_science_knowledge.py`.
- **Contract:** §5.1–5.2a, §13.1 (K only), plus snapshot and replay of the
  log. This includes ExposureDeclaration append validation: schema,
  `knowledge_snapshot_ref` = (seq, prev_hash), and footprint shape
  determinable.
- **Tests:**
  - append and read round-trip;
  - per-kind payload validation, including footprint *shape* via
    `validate_footprint_shape`;
  - forward or unknown refs rejected;
  - snapshot `(length, head_hash)` stability.
- **Adversarial:**
  - an edited, deleted, reordered or inserted line → `KnowledgeIntegrityError`;
  - a truncated tail is reported, not accepted;
  - no API mutates a record;
  - concurrent append is blocked by the lock.
- **Note:** B does not import P10-C. The union is verified by D at read
  time and computed by the writers (I, H), so B and C are fully independent
  within Wave 2.

**P10-C — EvidenceFootprint** (Wave 2)
- **Owns:** `smart_beta/science/footprint.py`, `tests/test_science_footprint.py`.
- **Contract:** §6.
- **Tests:**
  - canonicalization uniqueness (permutations and splits → the same id);
  - interval merging by session gaps;
  - every §6.3 derivation rule;
  - the linkage rule;
  - fundamentals keyed by fiscal period end;
  - `MKT:` subjects;
  - `covers`, `intersect`, `restrict`;
  - the value columns are never read (a panel with poisoned values yields
    the same id).
- **Adversarial:** the §6.6 table, one test per row, plus:
  - an unmapped subject or column, or a missing rule → UNDETERMINABLE;
  - a calendar gap → UNDETERMINABLE;
  - an undeterminable id never equals a determinable id;
  - a DED difference never changes `footprint_id`.

**P10-F — inference-procedure contract, admission gate, executor** (Wave 2)
- **Owns:** `smart_beta/science/inference.py`, `tests/test_science_inference.py`.
- **Contract:** §9.1–9.6, §9.8. **No statistical procedure is implemented
  or certified here.**
- **Tests:** with `test_only` fixture procedures in an injected registry:
  - contract validation and hashing;
  - `source_sha256` computed from the implementation file;
  - admission matching (id, version, contract hash, source hash);
  - revocation;
  - estimand, parameter and missingness support;
  - the `COMPLETE_REQUIRED` check against the expected fold index;
  - `InferenceResult` hashing and the `p_value_semantics_type` pass-through.
- **Adversarial:**
  - not admitted → `PROCEDURE_NOT_ADMITTED`;
  - admitted, but the source is edited → `PROCEDURE_IDENTITY_MISMATCH`;
  - revoked → `PROCEDURE_REVOKED`;
  - a `test_only` procedure in the production registry → refused;
  - a missing series → INVALID;
  - a missing or extra date, or a non-finite value →
    `MISSINGNESS_PATTERN_UNSUPPORTED`;
  - every other registered procedure raises if called (no fallback);
  - an INVALID result has no p-value or bound.

**P10-P — first inference procedure + validation dossier** (Track P; after
PA-0 authorization)
- **Owns:** `smart_beta/science/procedures/__init__.py`,
  `smart_beta/science/procedures/<procedure_id>.py`,
  `tests/test_science_procedure_<procedure_id>.py`,
  `docs/phase10/procedures/<procedure_id>-<version>-validation.md`.
- **Forbidden:** every other science module (it registers through P10-F's
  public API only); the sealed paths.
- **Contract:** the PA-0-frozen candidate spec; the §9.2 contract fields;
  the dossier contents in §9.7.
- **Tests:**
  - the formula against an independent naïve reference;
  - golden values;
  - the contract's failure conditions;
  - the seeded Monte-Carlo size and coverage study reproduced
    bit-identically, with its tables equal to the dossier's.
- **Completion evidence:** the dossier sha256. **Admission is a separate
  human decision after PA**, never part of the worker's task.

**P10-D — evidence roles** (Wave 3)
- **Owns:** `smart_beta/science/roles.py`, `tests/test_science_roles.py`.
- **Contract:** §5.3–5.5, §8.
- **Tests:** every rule row 1a–6; grade mapping; residual disclosures.
- **Adversarial:**
  - the Knowledge-PIT section of §18;
  - a randomized-append monotonicity property with 500 seeded trials;
  - no persisted role field or setter (introspection test).

**P10-E — preregistration** (Wave 3)
- **Owns:** `smart_beta/science/preregistration.py`,
  `tests/test_science_preregistration.py`.
- **Contract:** §7.
- **Tests:**
  - hash stability;
  - members sorted;
  - EstimandPolicy equality enforced;
  - `analysis_plan_id` composition;
  - appending produces a PREREGISTRATION record with correct refs.
- **Adversarial:**
  - the Preregistration section of §18;
  - no retroactive field;
  - duplicate members;
  - overlap with a consumed footprint at preregistration;
  - **admission ordering (§7.3 check 4):**
    - an admission in the freeze prefix → admissible;
    - an admission created after the freeze → refused;
    - an unverifiable or missing admission reference → refused;
    - a retrospective admission cannot repair a frozen preregistration;
    - an admission contract mismatched to `procedure_ref` → refused;
    - an admission revoked within the freeze prefix → refused;
    - forged `recorded_at` timestamps cannot substitute for K ordering.

**P10-G — assessment + Holm** (Wave 4)
- **Owns:** `smart_beta/science/assessment.py`, `tests/test_science_assessment.py`.
- **Contract:** §10–11.
- **Tests:**
  - Holm against a naïve reference, including ties;
  - adjusted p-values;
  - m fixed;
  - state order;
  - `economic_state`;
  - series-identical annotation;
  - reassessment downgrade-only.
- **Adversarial:**
  - the Holm and R-1 sections of §18;
  - the full 4-row truth table of §11.2;
  - `EFFECT_BELOW_SESOI` is never mapped to NOT_SUPPORTED;
  - NOT_SUPPORTED never carries multiplicity fields claiming family-wise
    control.

**P10-I — adapters + firewall audit** (Wave 3)
- **Owns:** `smart_beta/science/adapters.py`, `tests/test_science_adapters.py`.
- **Contract:** §12.2–12.3, §14.2.
- **Tests:**
  - synthetic Phase-8 registry and Phase-9 loop runs → the expected K
    records and footprints;
  - robustness aggregates → a whole-range footprint;
  - over-approximation when the visible history is unreconstructable;
  - `governance_provenance` VALID / MISSING / INVALID.
- **Adversarial:** the Firewall section of §18; **no metric value from any
  HOLDOUT fold appears in any K payload** (a scan test).

**P10-H — confirmation study** (Wave 5)
- **Owns:** `smart_beta/science/study.py`, `tests/test_science_study.py`,
  `tests/test_science_pilot1a_retrospective.py`.
- **Contract:** §13, §19.
- **Tests:**
  - an end-to-end synthetic G1 study (prospective) and G2/G3 studies;
  - refusal paths;
  - write-ahead ordering (CONSUMPTION before the first outcome read, via an
    instrumented reader);
  - crash injection at every state boundary;
  - resume;
  - replay equality.
- **Adversarial:**
  - the Replay, Footprint-integration and Pilot-1A sections of §18;
  - **execution-time revocation (§9.3 temporal authority):**
    - a preregistered admission revoked before execution → `PROCEDURE_REVOKED`;
    - only the exact preregistered admission record is supplied to P10-F;
    - timestamps cannot substitute for K ordering;
    - no inference path bypasses `run_inference`.

**P10-Z — certification & adversarial suite** (Wave 6)
- **Owns:** `tests/test_phase10_certification.py`.
- **Contract:** one test per clause of §21 and per row of §18, end-to-end
  through the public APIs only.
- **Additional:** a no-network/no-provider guard (sockets blocked;
  `DEEPSEEK_API_KEY`, `ANTHROPIC_API_KEY`, `TIINGO_API_KEY` and
  `TUSHARE_*` scrubbed); a sealed-file digest check against baseline
  `610fd0bc`.

---

## 17. Barrier criteria

At every barrier: exact-SHA independent review by Planning Claude; the full
suite green, with the Tiingo live skip as the only permitted skip; the
sealed-file digest check; merges only of reviewed SHAs in DAG order; and
cleanup under `CLAUDE.md` §7.

| Barrier | Criteria |
|---|---|
| **B0** | **PASSED.** Plan reviewed and frozen; R-1…R-8 (incl. R-5a) approved as in §25 |
| **B1** | Contracts frozen (golden hashes); the P10-S bit-equal binding demonstrated, or its blocker reported; P10-V sentinel suite green and `pilot_evidence` unchanged |
| **B2** | K log integrity and tamper suite, including ExposureDeclaration append validation (schema, snapshot binding, determinable footprint); footprint canonicalization, derivation rules and the §6.6 table; procedure gate/executor suite with `test_only` fixtures; no production procedure admitted |
| **B3** | Role rules 1a–6, the monotonicity property, no mutable role, and read-time DERIVED-union verification; preregistration refusal matrix; retroactivity impossible; adapters' conservative footprints; firewall audit; a holdout-value scan of K payloads |
| **B4** | Holm and state machine; NOT_SUPPORTED hypothesis-local; reassessment downgrade-only |
| **B5** | End-to-end studies; write-ahead proven; crash matrix; replay equality; Pilot-1A retrospective = NOT_ASSESSED with no provider call and evidence unchanged |
| **PA-0** (Track P) | Candidate procedure spec (estimand, assumptions, parameters, validation DGPs and acceptance criteria) frozen and separately authorized |
| **PA** (Track P) | Independent statistical review of the P10-P dossier; tables reproduced bit-identically; limitations recorded; then, and only then, a human `PROCEDURE_ADMISSION` record. **Not required for the Final Barrier.** |
| **Final** | P10-Z green; §21 statement clause-by-clause mapped to tests; §22 nonclaims recorded in `docs/phase10_confirmatory_evidence_certification.md` (written by the planner at the seal, not by a worker); STOP before tag |

---

## 18. Adversarial test matrix (binding; owner in brackets)

**Knowledge PIT [D, Z]**

| Case | Expected |
|---|---|
| generator saw an OOS metric before H2's freeze | DEVELOPMENT |
| generator saw only a pass/fail bit derived from W | DEVELOPMENT |
| historical separation by human declaration only (unsealed or late artifact) | G3, not G2 |
| G2 conditions but a PUBLIC EXPOSED declaration lists H | G3 |
| missing footprint on an Anc record, missing PRETRAINING declaration, or a missing artifact record | UNKNOWN_EXPOSURE |
| pre-τ_P ACCESS of an overlapping artifact | UNKNOWN_EXPOSURE |
| a later EXPOSED declaration with event_time < τ_P | downgrade allowed |
| a later NOT_EXPOSED declaration (seq > τ_P) | no upgrade |
| a development metric whose forward returns realize inside the window (purge failure) | DEVELOPMENT |
| NOT_EXPOSED declaration appended after τ_P for evidence that was UNKNOWN_EXPOSURE | stays UNKNOWN_EXPOSURE (never G3) |
| EXPOSED and NOT_EXPOSED declarations overlap | EXPOSED wins (downgrade) |
| declaration whose `knowledge_snapshot_ref` ≠ its own (seq, prev_hash) | append rejected |
| declaration with an undeterminable footprint scope | append rejected |

**Preregistration [E, H, Z]**

| Case | Expected |
|---|---|
| endpoint changed after confirmation access | new preregistration; source observations exposed/consumed → refused / NOT_ASSESSED |
| SESOI changed after results | `ESTIMAND_POLICY_VIOLATION` (δ frozen in policy) / new preregistration refused |
| test changed after results | new preregistration → consumed footprint → refused |
| family membership changed after access | the same (new preregistration refused); the original's m is unchanged |
| a preregistration referencing a record with seq ≥ τ_P | impossible (rejected) |

**Inference [F, Z]**

| Case | Expected |
|---|---|
| missing per-date series | NOT_ASSESSED (`SERIES_MISSING`) |
| non-finite output or contracted failure condition | INVALID → NOT_ASSESSED |
| hidden fallback test | impossible (other procedures raise if called) |
| preregistered but not admitted procedure | preregistration refused / NOT_ASSESSED (`PROCEDURE_NOT_ADMITTED`) |
| admission in the preregistration-freeze K prefix | admissible |
| admission created after the preregistration freeze (retrospective) | refused; it cannot be referenced (a forward ref) and cannot repair a frozen preregistration |
| admission ordering unverifiable | refused (fail closed) |
| preregistered admission revoked before execution (execution snapshot) | NOT_ASSESSED (`PROCEDURE_REVOKED`) |
| wall-clock timestamp suggests admission-before-freeze, but K order says otherwise | refused (sequence/prefix is authority) |
| admitted procedure whose source changed | `PROCEDURE_IDENTITY_MISMATCH` |
| procedure revoked after the assessment | reassessment → NOT_ASSESSED (downgrade only) |
| estimand not in `supported_estimands` | preregistration refused |
| missing date under `COMPLETE_REQUIRED` | NOT_ASSESSED (`MISSINGNESS_PATTERN_UNSUPPORTED`) |
| `test_only` procedure in the production registry | refused |

**R-1 effect detection vs SESOI [G, Z]**

| Case | Expected |
|---|---|
| Holm rejects; UB ≥ δ | SUPPORTED, `SESOI_NOT_EXCLUDED` |
| Holm rejects; UB < δ | SUPPORTED, `EFFECT_BELOW_SESOI` (**not** NOT_SUPPORTED) |
| no rejection; UB < δ | NOT_SUPPORTED, `NOT_APPLICABLE` |
| no rejection; UB ≥ δ | INCONCLUSIVE, `NOT_APPLICABLE` |
| NOT_ASSESSED member | both booleans `null`, `NOT_APPLICABLE` |

**Holm [G, Z]**

| Case | Expected |
|---|---|
| family frozen before confirmation | m and members from the preregistration only |
| extra or missing member at Holm | `HolmFamilyMismatchError` |
| invalid individual p-value | that member NOT_ASSESSED; p := 1 in step-down |
| SUPPORTED without an adjusted rejection | impossible |
| NOT_SUPPORTED | `not_supported_scope = HYPOTHESIS_LOCAL`; no family-wise field |

**EvidenceFootprint [C, H, Z]**

| Case | Expected |
|---|---|
| same data renamed | reuse detected |
| copied to another file | reuse detected |
| another vendor, mapped | not fresh |
| another vendor, unmapped | fail closed |
| partial date overlap | governed (refused) |
| partial universe overlap | governed (refused) |
| transformed same observations | not fresh |
| horizon 5 vs 20 over the same dates | not fresh (shared `PRICE_CHANGE`) |
| excess return vs raw return, same dates | not fresh |
| restated / later-vintage fundamentals | not fresh (same fiscal-period observation) |
| development return realized ≤ w₀, confirmation formed at w₀ | DISJOINT (no artificial boundary overlap) |
| a price-level signal on a date whose next change is consumed | OVERLAP via linkage |
| unmapped `MKT:` series or missing derivation rule | fail closed |
| unknown overlap | fail closed |
| new run_id, new packaging hash | not fresh |

**Firewall [I, Z]**

| Case | Expected |
|---|---|
| confirmation metric in a generator input | `FIREWALL_VIOLATION` |
| SUPPORTED bit reaches the generator | violation |
| NOT_SUPPORTED bit reaches the generator | violation |
| visible history after a study contains Phase-10 data | impossible (asserted) |

**Replay / integrity [B, H, Z]**

| Case | Expected |
|---|---|
| same K + same preregistration + same evidence | identical assessment hashes |
| K tampering (edit, delete, reorder, insert) | detected |
| study-store object tampering | detected |
| crash after CONSUMPTION, before evidence | `STUDY_INTERRUPTED`; footprint stays consumed |

**Pilot-1A [H]:** see §19.

---

## 19. Pilot-1A retrospective expected behavior (P10-H test)

**Inputs** (read-only; structural fields only):
- the Pilot journal (`8189309…`): generation events, proposals, fold
  boundaries, universe;
- declared records:
  - HUMAN EXPOSED of the Gate-B window (Phase-5A artifacts), with event_time
    before the Pilot;
  - HUMAN EXPOSED of the holdout via the Pilot report;
  - PRETRAINING `deepseek-v4-pro` (§5.2a contract), with
    `documented_cutoff` = the provider's documented date or "UNDOCUMENTED".

**Expected:**
- Exp 1–3 are ingested as `HYPOTHESIS_FREEZE`.
- Any attempted study using any Pilot window as E → role DEVELOPMENT (G4)
  for all three:
  - via the human declarations;
  - for Exp 2 and 3, additionally via GENERATOR_INPUT chain closure;
  - via PROGRAM-channel whole-evaluation footprints.
- The study is REFUSED: NOT_ASSESSED with `ROLE_DEVELOPMENT`; **no
  CONSUMPTION and no data read**.
- There is **no retrospective preregistration**: τ_P is the append seq
  today, and the Pilot window's source observations are already in ExposedFP.
- Governance ACCEPT (Exp 1) is recorded only as a provenance hash, never as
  support.

**Asserted invariants:**
- `pilot_evidence/**` passes `sha256sum -c SHA256SUMS` before and after;
- no network or provider call;
- no HOLDOUT metric value is copied into K.

---

## 20. P1A-SV placement

**P10-V, Wave 1.** It is independent of science, has disjoint files, and is
merged by Barrier 1.

- It is **not** part of scientific inference or of the §21 statement.
- The Final Barrier requires it to be merged. Any future real-model run
  (a separate authorization) requires P10-V to be merged and green first.
- Pilot 1A is **not** rerun and its evidence is not modified.
- The historical run's "comprehensive secret-value exclusion NOT CERTIFIED"
  stands.

---

## 21. Exact candidate Phase-10 certification statement

> **Phase 10 certifies, mechanically and deterministically, on synthetic
> fixtures and preserved historical evidence, that:**
>
> 1. **Append-only Knowledge-PIT provenance.** Every record the protocol
>    uses is appended to a hash-chained, content-addressed log K whose
>    references point only to earlier records. Any edit, deletion,
>    reordering or insertion is detected, and every derived result names the
>    KnowledgeSnapshot it was computed from.
> 2. **Deterministic, hypothesis-relative evidence roles.** EvidenceRole(E,
>    H, P, K) is computed only by the frozen rule order of the plan (§5.4)
>    from K, and is never stored as a mutable field or assigned manually.
>    - A recorded pre-freeze influence path from evidence whose
>      source-observation footprint overlaps E's yields DEVELOPMENT.
>    - Later records can only downgrade a role.
>    - Exposure declarations are immutable, bound to the K prefix they attest
>      against, and count toward a confirmation role only if recorded before
>      the preregistration freeze.
> 3. **Fail-closed unknown exposure.** Missing, unverifiable or
>    undeterminable provenance or footprint overlap yields UNKNOWN_EXPOSURE
>    or NOT_ASSESSED, never a confirmation role.
> 4. **Preregistration before confirmation observation.** Every
>    confirmatory assessment references a preregistration whose record
>    precedes, in K order, the consumption and every read of its
>    confirmation data. A preregistration cannot reference later records, so
>    retrospective preregistration is impossible.
> 5. **EvidenceFootprint-based one-use governance.**
>    - Confirmation freshness is keyed to the canonical source-observation
>      footprint, to which derived quantities are expanded by frozen
>      derivation rules. It is not keyed to derived cells, files, artifact
>      hashes, vendors, dataset ids or run ids.
>    - A footprint that shares any source observation with, or cannot be
>      shown disjoint from, any previously consumed confirmation footprint is
>      refused.
>    - Consumption is recorded write-ahead and is never released.
> 6. **One primary estimand/test contract.** Each member has exactly one
>    primary estimand (fixed by a program estimand policy recorded before the
>    program's first hypothesis freeze), direction, null, SESOI, estimator,
>    inference-procedure reference (id, version, contract hash), parameters,
>    missingness policy and bound level, all frozen at preregistration.
> 7. **Deterministic execution of a preregistered AND Phase-10-admitted
>    inference procedure.**
>    - An inferential result is produced only when the member's
>      preregistration references a procedure version admitted in K before
>      the preregistration freeze, not revoked, and whose executing
>      implementation matches the admitted source hash and contract.
>    - That procedure alone is executed, on the Phase-7-bound per-date series
>      of the confirmation fold, under the preregistered missingness policy.
>    - There is no endpoint switch, fallback procedure or post-data repair.
>    - Missing inputs, unsupported missingness patterns or unadmitted
>      procedures yield NOT_ASSESSED.
>    - Neither preregistration nor admission is certified to make a procedure
>      statistically valid. Validity rests on the admitted procedure's
>      separately documented validation and stated assumptions.
> 8. **Holm over the frozen confirmatory family.** SUPPORTED is emitted only
>    for a Holm-adjusted rejection of the null boundary in the declared
>    direction, over exactly the m members frozen at preregistration. This
>    provides family-wise error control conditional on the validity (of the
>    declared type) of the individual p-values. Every SUPPORTED record also
>    carries a deterministic effect-size qualification relative to the SESOI
>    (`EFFECT_BELOW_SESOI` or `SESOI_NOT_EXCLUDED`) and never asserts that the
>    SESOI is reached.
> 9. **Hypothesis-local NOT_SUPPORTED.** NOT_SUPPORTED is emitted only when
>    the null is not rejected and the member's predeclared one-sided bound
>    excludes its SESOI. It is recorded as hypothesis-local, with no
>    family-wise claim. INCONCLUSIVE means neither determination holds.
>    NOT_ASSESSED means inadmissible.
> 10. **Separation from governance.** ScientificAssessment is a distinct
>     record. Phase-8 governance ACCEPT, development evidence and
>     unknown-exposure evidence are never converted into scientific support,
>     and production readiness is constantly NOT_CERTIFIED.
> 11. **Generator firewall against confirmation evidence.** Confirmation
>     evidence and anything derived from it (including SUPPORTED or
>     NOT_SUPPORTED bits) are structurally absent from the sealed
>     generator-visible history. Any recorded generator input that touches
>     consumed confirmation observations or their derivatives is detected as
>     a firewall violation.
> 12. **Deterministic replay.** The same K prefix, preregistration, admitted
>     procedure implementation and persisted evidence reproduce
>     byte-identical roles, inference results, Holm results and assessments.

---

## 22. Explicit Phase-10 nonclaims

Phase 10 does **not** claim:
- that any factor is true, or that alpha or a premium exists;
- production readiness or tradeability;
- complete reconstruction of human knowledge or exposure;
- complete reconstruction of LLM pretraining knowledge. G1 and G2 do
  **not** prove absence from pretraining; pretraining is a disclosed
  residual;
- the truth of declarations. **G3 depends on declarations**, as do vendor
  `available_from` dates, model cutoffs and the system clock behind
  `recorded_at`;
- universal statistical validity. **Scientific support is conditional on
  the preregistered and admitted procedure's validity assumptions and on its
  separately documented validation**, which are recorded, not certified by
  Phase 10;
- that preregistration or admission makes a procedure valid. Admission
  certifies process and implementation identity only;
- that any production inference procedure is admitted at the Phase-10 seal.
  Until Track P's PA barrier passes, every real study is NOT_ASSESSED;
- that SUPPORTED means the effect reaches the SESOI. It asserts only
  θ′ > 0 at the Holm level, with an explicit effect-size qualification;
- that complete panels are scientifically necessary. `COMPLETE_REQUIRED` is a
  v1 implementation limitation;
- the authenticity of declarant identities (no cryptographic signatures);
- family-wise error control for NOT_SUPPORTED;
- that Holm corrects the adaptive exploration history, validates individual
  p-values, or controls error across studies;
- general semantic or cross-vendor data equivalence beyond the
  source-observation canonicalization and derivation rules. **Unlinked
  identifier continuity is NOT CERTIFIED**, and unmapped observations are
  refused, never assumed fresh;
- reusable confirmation data;
- generalization outside the confirmation study. **One confirmation period
  does not prove persistence**;
- repair of the Phase 7 + 9 temporal composition (FIX B remains NOT
  CERTIFIED).

Carried forward as explicit statements:
- **Historical evidence is not automatically invalid.**
- **Prospective evidence has the strongest mechanically auditable
  separation.**
- **Robustness is not replication.**
- **Replication is not production readiness.**

---

## 23. Deferred work (not Phase 10)

- A family-wise NOT_SUPPORTED (refutation) procedure, which would need its
  own preregistered method.
- Admission of any specific inference procedure (Track P: PA-0 → P10-P →
  PA); further procedures (e.g. block bootstrap, NW fixed-b, EWC-HAR) each
  need their own dossier and admission.
- `MissingnessPolicy` values beyond `COMPLETE_REQUIRED`, each of which needs
  an admitted procedure supporting it.
- Additional cross-kind linkage rules and constituent expansion of `MKT:`
  series.
- Multi-estimand program policies and hypothesis-class menus.
- Declared estimand-invariance rewrite rules (equivalence at promotion). The
  MDV only refuses exact duplicates and annotates byte-identical series.
- Benchmark-adjusted alpha (needs a certified benchmark factor set).
- Robustness and replication axes; exploration reports (DSR, SPA,
  Romano-Wolf, PBO); e-values; reusable holdout.
- FIX B (a sealed-level Phase 7/9 correction).
- Identifier-continuity certification.
- External anchoring of K heads (e.g. signed or remote timestamps).
- A prospective-accrual data track; a broader universe.
- Any real-model scientific pilot (separate authorization, after P10-V).

---

## 24. STOP conditions (in addition to `CLAUDE.md` §5)

Stop and report; never route around any of the following:
- any need to modify a sealed file outside §16's authorized paths;
- any change to sealed Phase-7 code beyond the §12.1(a) observational
  hook, or any hook behaviour that is not purely observational (return
  values used, engine inputs mutable through the sink, or control flow
  affected other than by a sink exception failing closed before holdout
  consumption);
- any attempt to append a `PROCEDURE_ADMISSION` without a passed PA
  barrier, or a `test_only` procedure reaching a production registry;
- any procedure dossier that cannot be reproduced bit-identically;
- any conflict between this plan and SD;
- any test that would require a real model call, live market-data access or
  a holdout read;
- `pilot_evidence/**` digest change;
- any role derivation that would need a manual input;
- any case where a footprint cannot be computed from key columns without
  reading values;
- two tasks needing the same file;
- any Barrier-0 decision (§25) reversed after B0.

---

## 25. Barrier-0 decision table (APPROVED at Barrier 0)

User dispositions (Barrier-0 review):
- **R-1:** approved with clarification.
- **R-2:** not approved.
- **R-3:** approved.
- **R-4:** approved in principle; the footprint model needed repair.
- **R-5 / R-5a / R-6:** approved.
- **R-7:** not approved as a principle.
- **R-8:** approved in principle; it must become a contract.

The table below states the repaired rules.

| # | Final rule | Rationale | Mechanical enforcement point | Nonclaim / limitation |
|---|---|---|---|---|
| **R-1** | Two independent booleans are always recorded: `primary_null_rejected` (Holm) and `sesoi_excluded_by_upper_bound`. The state is a total mapping of the two (§11.2). SUPPORTED always carries `effect_size_qualification` ∈ {`EFFECT_BELOW_SESOI`, `SESOI_NOT_EXCLUDED`}. | Evidence of θ′ > 0 differs from evidence about θ′ vs δ; neither may hide the other | P10-G `assess()`; a truth-table test over all 4 combinations; the schema requires both booleans on admissible members | SUPPORTED never asserts θ′ ≥ δ; the qualification is hypothesis-local |
| **R-2** | No statistical test is frozen as authority. A verdict requires a **preregistered AND Phase-10-admitted** `InferenceProcedure` version whose implementation hash matches its admission record. Phase 10 ships zero admitted production procedures. The first procedure is certified on a separate track (PA-0 → P10-P → PA). | Holm needs valid, not exact, p-values; validity is a property of a validated procedure, not of preregistration | P10-E (preregistration refusal) and P10-F gates (§9.3); the production registry refuses `test_only`; P10-H re-checks at execution | Admission certifies process and identity, not universal validity; validity is of the declared type (exact or asymptotic) under stated assumptions |
| **R-3** | An invalid or inadmissible member (after consumption) → NOT_ASSESSED; it enters the Holm step-down with p := 1, m fixed | Preserves FWER for the other members without fabricating evidence | P10-G Holm input validation; `HolmFamilyMismatchError` | The invalid member receives no verdict; power for the others is reduced |
| **R-4** | Freshness is attached to **source observations** `(subject_key, observation_kind, observation_date)`. Derived quantities expand through frozen derivation rules. Any shared source observation (with the linkage rule) ⇒ not fresh. Carving happens only at preregistration. Undeterminable ⇒ fail closed. | Derived-cell identity caused spurious boundary overlap and missed market-series and vintage reuse | P10-C `footprint_from_panel` / `expand` / `overlap`; P10-E preregistration check; P10-H consumption check | No general cross-vendor semantic equivalence; unlinked identifier continuity NOT CERTIFIED; unmapped observations are refused, not assumed fresh |
| **R-5** | Confirmation evaluations run through Phase-7 `evaluate()` directly, outside Phase-8 orchestration and the registry; no DecisionRecord; no search budget | Keeps governance ACCEPT separate and keeps confirmation evidence out of generator-visible history | P10-H `execute()`; P10-Z asserts no Phase-8 registry write | Phase-8 governance does not judge science; historical decisions are not reinterpreted |
| **R-5a** | The family, α, the confirmation footprint and every member contract freeze atomically in one PreRegistration (no separate StudyProtocol) | Avoids ordering hazards between family and member freezes | P10-E: a single hash and a single K record at τ_P | One family per preregistration |
| **R-6** | The consumption ledger is K (`CONSUMPTION` records with SOFs), global and cross-run; Phase-8 `HoldoutGovernance` is untouched and unused for confirmation | Phase-8 holdouts are exact-id and per-experiment; families need footprint-level batch consumption | P10-E preregistration and P10-H execute: global overlap check; write-ahead | Detection is limited to §6.7 |
| **R-7** | A preregistered `MissingnessPolicy` per member. v1 supports only `COMPLETE_REQUIRED`; any other pattern → NOT_ASSESSED (`MISSINGNESS_PATTERN_UNSUPPORTED`); an unsupported policy → preregistration refused | Makes missingness an explicit, frozen contract term | P10-F executor; P10-E preregistration check | `COMPLETE_REQUIRED` is an implementation limitation, **not** a scientific principle; future procedures may add policies |
| **R-8** | The `ExposureDeclaration` contract (§5.2a): immutable, snapshot-bound, scoped, footprint-covered. NOT_EXPOSED counts only if recorded before τ_P; later NOT_EXPOSED never upgrades; EXPOSED can always downgrade and wins conflicts. Historical roles require pre-τ_P HUMAN NOT_EXPOSED and PUBLIC declarations; PRETRAINING declarations are required for every generator identity in Anc. | Declarations are the only access to unobservable channels; their timing must be mechanical | P10-B append validation (schema, snapshot binding, determinable footprint); P10-D application rules; monotonicity property test | The truth of the claim and the declarant's authenticity are not certified; G3 is declaration-dependent |

---

## 26. Planning self-review (performed before reporting)

| Concern | Finding / repair |
|---|---|
| circular ownership | none; the DAG in §15 is acyclic. **Repair made:** an initial B→C same-wave dependency was removed; B validates shape only, and union verification moved to D (read) and I/H (write). |
| mutable evidence roles | a pure function; no field or setter; introspection test (D) |
| filename- or vendor-based freshness | the footprint is source-observation-based; the vendor label, packaging hash and DED are metadata only (§6.4) |
| retrospective preregistration | impossible: τ_P = append seq; no forward refs (§7.3) |
| evidence-role upgrades | downgrade-only property test; NOT_EXPOSED counts only pre-τ_P (§5.5) |
| hidden confirmation reuse | global CONSUMPTION check at preregistration and at execute; write-ahead (§13) |
| hidden endpoint switching | one program estimand policy recorded before the first freeze; the contract fixes the series (§7.1, §9.2) |
| hidden test fallback | single dispatch to the admitted `procedure_ref`; every other registered procedure raises if called (§9.4) |
| Holm on invalid p-values | invalid → NOT_ASSESSED, p := 1 (conservative), never a fabricated valid p (§10) |
| accidental family-wise NOT_SUPPORTED claim | constant `HYPOTHESIS_LOCAL`; no multiplicity field used for it; test (G) |
| confirmation leakage to the generator | structural separation + audit (§14) |
| alternate PIT paths | the sidecar consumes the same alignment objects and never loads data (§12.1); the footprint reads key columns only |
| inability to reconstruct decisions | replay (§5.6, §13.4) |
| overlapping worker ownership | §16 files disjoint; existing tests unmodified |
| unnecessary statistical machinery | zero shipped procedures; one missingness policy; one estimand per program; the first procedure is on a separate, bounded Track P |
| **repair made during review** | Phase-8 `HoldoutGovernance` is per-experiment and exact-id, so it would conflict with batch families. It was dropped from the confirmation path (R-6) instead of being extended. |
| **Barrier-0 repair (R-2)** | The earlier claim that "Holm needs exact p-values" was wrong and has been removed. No test is frozen as authority; the InferenceProcedure admission gate plus Track P replace it. |
| **Barrier-0 repair (R-1)** | Precedence hid the SESOI determination; replaced by two recorded booleans and a total mapping |
| **Barrier-0 repair (R-4)** | Derived-cell footprints caused spurious boundary overlap and missed market-series and vintage reuse; replaced by source observations + derivation rules |
| **Barrier-0 repair (R-7)** | Complete panels are no longer a scientific principle; `MissingnessPolicy` is a preregistered contract term |
| **Barrier-0 repair (R-8)** | Declarations are now a frozen, snapshot-bound contract; late NOT_EXPOSED can never upgrade |
| **repair made during review** | Robustness aggregates can span the holdout (FIX B); their footprints are over-approximated to the whole range (§12.3) |
| **repair made during review** | Declared footprints of future windows would need future calendars → calendar-day superset (§6.4) |
| **repair made during review** | Partial-family admissibility was ambiguous → the whole study is refused pre-consumption (§13.2) |

---

## 27. Repository action

This plan is committed as a planning artifact only. No code, no push and no
tag are part of the freeze. Implementation proceeds only through
separately authorized waves executed by Herdr-launched Pi workers in
isolated task worktrees.
