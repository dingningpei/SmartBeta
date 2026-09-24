# Post-Pilot-1A — Scientific Evidence Framework (RESEARCH / ARCHITECTURE DESIGN, v3.1 — FROZEN)

**Status:** research and design only; **FROZEN at v3.1** as the scientific
authority for the Phase-10 plan (`worker_tasks/phase10/phase10-plan.md`). No
code, tests, sealed authority, Pilot evidence, holdout, credential or model
was touched.

- **v1** was committed at `1babbd1`. **v2** was the second-pass review
  (§0.2). **v3** made narrow third-pass corrections (§0.1). **v3.1** adds the
  two freeze clarifications (§0.0). Later versions supersede earlier ones
  wherever they differ.
- Baseline: `pilot1a-complete` → `1d1f70ae41ab91676002373a52be3815436549bb`.
- Sources read: Pilot-1A evidence (`pilot_evidence/pilot1a-real-deepseek-v1/`),
  `worker_tasks/phase7/phase7-plan.md` §7–9, `worker_tasks/phase8/phase8-plan.md`
  §5, §7, §10–11, `worker_tasks/phase9/phase9-plan.md`,
  `docs/phase9_research_loop_certification.md` §9, `worker_tasks/pilot1/pilot1-plan.md`
  §26b–26g, and the implementations in `smart_beta/evaluation/`,
  `smart_beta/experiment/judge.py` and `smart_beta/research/history.py`.

**Claim labels:**
- **[T]** theorem or result from the literature, valid under the stated
  assumptions;
- **[A]** assumption;
- **[M]** methodological recommendation from the literature;
- **[D]** design choice (ours);
- **[I]** inference for smart_beta.

---

## 0. REVIEW HISTORY

### 0.0 FREEZE CLARIFICATIONS (v3.1)

| # | v3 position | v3.1 frozen position |
|---|---|---|
| A1 | SUPPORTED (Holm) and NOT_SUPPORTED (bound below SESOI) listed together, implying a common multiplicity guarantee | **CHANGED.** They do **not** share a multiplicity guarantee. **SUPPORTED** carries Holm FWER control over the frozen confirmatory family, conditional on valid individual p-values. **NOT_SUPPORTED** is a **hypothesis-local** conclusion: a predeclared hypothesis-local bound excludes the predeclared SESOI. Phase 10 claims **no** family-wise refutation control. INCONCLUSIVE = neither established. NOT_ASSESSED = evidence or protocol inadmissible. Family-wise NOT_SUPPORTED control is a possible future extension only (§11, §17). |
| A2 | "A confirmation source may be consumed only once" | **CHANGED.** Confirmation freshness belongs to the **underlying empirical observations** (**EvidenceFootprint**, §4.7), never to filenames, artifact hashes, vendor objects or dataset identifiers. Material overlap with an already-consumed footprint never receives fresh-confirmation status. Undeterminable overlap fails closed. The exact schema and overlap policy are frozen in the Phase-10 spec. |

### 0.1 THIRD-PASS CORRECTIONS (v3, narrow)

Only three conceptual corrections were made; other sections are unchanged
apart from consistency edits.

| # | v2 position | v3 position |
|---|---|---|
| C1 | "Strong confirmation is prospective"; historical data give at most replication, with unknown exposure for LLM hypotheses | **CHANGED.** *Statistical admissibility* is separated from *epistemic (Knowledge-PIT) assurance* (§5). Historical data can support valid confirmatory inference when selection was independent of them. Five grades: G1 prospective, G2 historical with strong recorded separation, G3 historical with declared separation, G4 known-exposed, G5 unknown exposure. Model pretraining is a **disclosed residual that caps claim strength**, not an impossible "prove absence" requirement. |
| C2 | Holm condition included a generic development/confirmation embargo | **CHANGED.** Holm needs only valid individual p-values, under any dependence among them (§9). Time-series dependence is a validity problem *for each individual test*. Every confirmatory analysis predeclares a dependence-handling design matched to estimand, horizon, overlap, serial dependence and cross-sectional construction. An embargo is one option, not a universal requirement. Holm does not make invalid p-values valid. |
| C3 | Exposure log + relation | **CHANGED.** Knowledge PIT is an **append-only, content-addressed provenance/exposure graph** K (§4). EvidenceRole(E, H, K) is *derived*, never stored or set manually. Roles can only be downgraded by later records (non-exposure evidence counts only if recorded before freeze). Missing provenance fails closed to UNKNOWN_EXPOSURE. The chain W → OOS metric → generator input → H2 mechanically makes W DEVELOPMENT for H2. |

The Phase-10 certification statement and nonclaims are rewritten (§17.1–17.2).
The v2 answers to questions 4, 5, 6, 7 and 10 below are superseded where
marked.

### 0.2 SECOND-PASS REVIEW (v2, changes from v1)

An independent review raised four issues. The resolution of each follows.
Substantive changes are marked **CHANGED**.

| # | v1 position | v2 position |
|---|---|---|
| 1 | "Minimum detectable rank-IC ≈ 0.08–0.09" stated as a fact | **CHANGED.** Now derived explicitly (§7.2). Across plausible assumptions the MDE is **0.069–0.18**; 0.08–0.09 sits at the *optimistic* end. The per-date IC series is not recorded, so the dispersion and serial dependence that drive power **cannot be estimated from repository data**. It is an illustrative diagnosis, not a protocol constant. |
| 1b | "Pilot evidence is structurally incapable of either verdict" | **CHANGED — v1 was wrong.** Low power to *detect* an effect is not inability to *refute* one. Exp 1's holdout rank-IC (−0.046) gives a one-sided 95% upper bound below 0.03 for any long-run SD ≤ ≈0.33. With a pre-registered SESOI of 0.03 on a clean window, NOT_SUPPORTED would have been reachable. The actual blockers are **contamination and no pre-registration**, not power. |
| 1c | Power gate as a hard admissibility condition | **CHANGED.** Power is a *design* property, not a *validity* condition. Minimum version: disclose the pre-study MDE at registration. A hard gate is optional policy (§12). |
| 2 | Mean rank-IC as the default primary endpoint | **CHANGED.** There is no universal endpoint. Each confirmatory hypothesis pre-declares a primary **estimand** from an admissible set, fixed by the research-program policy before exploration (§6). Rank-IC is the *recommended* estimand for "cross-sectional ordering predictability" hypotheses, and diagnostic otherwise. |
| 3 | E-values listed in the "stronger future version" | **CHANGED.** Reclassified as a **FUTURE RESEARCH CANDIDATE** (§8.5). E-BH's validity under arbitrary dependence presupposes valid e-values. Constructing valid e-values for our estimands under serially dependent returns requires a *conditional-mean* null, which differs from the unconditional estimand of interest. |
| 4 | "Exposure ledger" as a supporting component | **CHANGED — now central.** **Knowledge PIT** (§4) is formalized. Evidence role is a *relation* on (evidence, hypothesis, research-history state, exposure history), not a property of a date range. It replaces intrinsic "OOS/holdout" semantics for scientific purposes. |
| 5 | "Historical untouched data" as confirmation | **CHANGED.** Separated *data not previously loaded* from *evidence not previously known* (§5). Historical data can give at most **historical replication with unknown exposure** for LLM-generated hypotheses. Only prospective data give strong confirmation. *[Superseded by v3 C1, §0.1.]* |
| 6 | Holm "valid after adaptive exploration" | **Refined.** It is valid **only** under explicit conditions (§9). Holm controls FWER over the frozen family on the confirmation evidence. It does **not** correct the exploration history, the winner's curse, or cross-study error. |
| 7 | "Canonical class" as one equivalence notion | **CHANGED.** Five equivalence relations are separated (§10). Test counting uses *estimand-specific statistical equivalence*, established by declared invariance rules plus matched missing-data and universe policies. |
| 8 | MDV with seven components incl. robustness/replication axes, a power gate, and development-only partitions | **CHANGED.** A leaner MDV (§12). Robustness and replication axes, the hard power gate and development-only Phase-7 partitions are removed from the minimum. Knowledge-PIT evidence roles, pre-registration, one-time confirmation sources and estimand-specific inference are kept. |
| 9 | Phase 10 named "Scientific Evidence Protocol" | **CHANGED.** Responsibility defined first (§17): **Phase 10 — Knowledge-PIT & Confirmatory Evidence Protocol**. It certifies *refusal and classification*, not discovery. |

Also corrected: the null SD of Spearman ρ for N independent pairs is
**1/√(N−1)** (= 0.20 at N = 26) [T]. v1 used the Fisher-z Pearson form
1/√(N−3).

### Answers to the ten review questions

1. **Is the 0.08–0.09 Pilot power claim defensible?** Only as an optimistic
   illustration. The derived MDE for mean daily rank-IC over T = 52 is
   0.069–0.18, depending on test sidedness, α and the (unestimable from repo
   data) daily-IC dispersion σ ∈ [0.20, 0.30] and AR(1) φ ∈ [0, 0.3]. The
   robust conclusion is qualitative: *the Pilot holdout cannot detect
   IC effects of plausible size (≈0.02–0.05)*. The number itself is not a
   protocol constant (§7.2).
2. **Should rank-IC be the universal primary endpoint?** No. It is the
   recommended estimand for ordering-predictability hypotheses. Each
   confirmatory hypothesis pre-declares its estimand from an admissible menu,
   fixed by program policy before exploration (§6).
3. **Are e-values part of the planned architecture?** No — a future research
   candidate only (§8.5).
4. **Is Knowledge PIT valid?** Yes, as a formal relation over a recorded
   exposure log, with fail-closed handling of unknown exposure. It is
   **complete for recorded machine channels** and **necessarily incomplete for
   human, public and pretraining knowledge**, which are declared, not
   reconstructed (§4). *[v3: formalized as an append-only provenance graph
   with derived, downgrade-only roles.]*
5. **Can historical, previously unloaded data provide confirmation?**
   *[v3, superseding v2:]* Yes, statistically, when the separation holds
   (grades G2/G3). Epistemic strength depends on how much of the separation
   is mechanically recorded. Pretraining and public exposure are disclosed
   residuals that cap claim strength. Only G1 prospective data give the
   strongest, fully auditable separation (§5).
6. **When does OOS evidence become development evidence?** For hypothesis H,
   window W is development evidence for H iff some function of data in W (any
   metric, bit, rank, plot or summary) entered the influence set of H before
   H's freeze time. Machine-precisely: a journaled generator input or a
   recorded human/program change derived from W precedes H's
   `GenerationEvent` (§4.5; mechanically, invariant 4 in §4.4).
7. **When is Holm valid for a promoted set after adaptive exploration?** When
   the family, estimands, nulls, directions, α, analysis plan and
   confirmation source are frozen before any function of the confirmation
   evidence reaches an influencing channel. The confirmation evidence must be
   independent of the selection information, or conditionally valid given it,
   and each marginal p-value must be valid. Holm then controls FWER over that
   family *for the confirmation-period estimands*, and nothing more (§9).
   *[v3: no universal embargo. Each test predeclares its dependence-handling
   design, and Holm does not repair invalid p-values.]*
8. **Which equivalence matters for test counting?** Estimand-specific
   statistical equivalence (§10).
9. **Revised MDV:** see §12. Six components.
10. **What should Phase 10 certify?** That the system classifies evidence
    roles relative to each hypothesis from its recorded exposure history, and
    **refuses** confirmatory conclusions unless evidence is confirmatory for
    that hypothesis under a hash-frozen pre-registration. When permitted, it
    computes the pre-registered inference with Holm over the frozen family and
    records a multi-axis assessment that never upgrades governance ACCEPT,
    development evidence, or unknown-exposure evidence into scientific support
    (§17). *[v3: superseded by the exact statement in §17.1.]*

---

## 1. Executive conclusion

1. **[I] The binding constraints are exposure and data, not statistics.** The
   Gate-B window is exposed (Phase-5A human exposure, and the Pilot report
   showed holdout metrics to humans), and it is short and narrow. No procedure
   applied to it can support a confirmatory claim for a hypothesis frozen
   after that exposure.
2. **[I] Keep Phase-8 ACCEPT/REJECT/DEFER as governance.** Add a separate
   scientific assessment whose axes cannot be collapsed into governance.
3. **[T/I] The only broadly valid design for arbitrary, unmodeled LLM
   adaptivity is sample splitting in the Knowledge-PIT sense.**
   - Exploration may use any evidence.
   - Confirmation uses evidence that no influencing channel had observed when
     the confirmatory family was frozen (Cox 1975 [T] for the simple case).
   - Corrections applied to the exploration history cannot restore validity
     for data that informed selection.
4. **[I] Historical ≠ statistically invalid (v3).** Historical data can
   support valid confirmatory inference when selection was independent of
   them. Prospective data give the strongest, most mechanically auditable
   separation. Claims are graded G1–G5 by auditable separation (§5).
5. **[D] Phase 10 = Knowledge-PIT & Confirmatory Evidence Protocol.**
   Minimal:
   - a Knowledge-PIT evidence-role relation;
   - hash-frozen pre-registration of confirmatory studies with pre-declared
     estimands;
   - one-time confirmation keyed to underlying observations (EvidenceFootprint);
   - estimand-specific inference;
   - Holm;
   - a minimal multi-axis assessment.

   Its value today is *refusal*: on current data it outputs NOT_ASSESSED.

---

## 2. What Pilot 1A exposed

Unchanged from v1, with corrections.

| Observation | Evidence | Consequence |
|---|---|---|
| ACCEPT ≠ support | Exp 1 ACCEPT; holdout IC −0.0020, rank-IC −0.0463, net long-short −0.0014/day, Sharpe −1.80 | No scientific judgment exists; the judge has no performance criterion |
| Only pooled inference recorded | `primary_metrics` t-stats pool IS+OOS+holdout (227 dates, Newey-West lag 6); `FoldResult.metrics` carry `value`/`n_obs` only | No confirmation-window test statistic exists in the record |
| Per-date IC series not recorded | only aggregates; per-date net long-short is recorded (`cost_adjusted_series`, `portfolio_accounting`) | IC dispersion and autocorrelation are unestimable from the repo; net long-short nuisance parameters are estimable |
| Window exposure | Phase-5A artifacts; Pilot report displayed holdout metrics for all 3 experiments | The window is exposed for every hypothesis frozen afterwards |
| OOS used as feedback | P9-B channels `OOS_METRICS` | OOS is development evidence for H2 and H3 |
| Portfolio equivalence | Exp 1 and 2 net long-short series identical (development-dated series byte-equal; identical rank metrics in every fold) | Estimand-specific equivalence is observed; Pearson IC differs |
| Costs dominate | turnover ≈0.65–0.8/day at 10 bps vs gross spread ≈3 bps/day (Exp 1 IS) | The economic estimand must be net of costs |
| LLM pretraining | documented cutoff Jun 2026 | IS/OOS lie before the cutoff: disclosed pretraining residual (§5.2) |

---

## 3. Terminology

- **Signal:** a Phase-6 `FactorSpec` evaluated on PIT inputs.
- **Hypothesis H:** a directional claim about a declared **estimand** θ(H),
  for a signal equivalence class, universe, horizon and construction, with a
  declared SESOI δ(H) in θ-units.
- **Freeze time τ(H):** the time of the hash-frozen record that fixes H's
  specification. For generated hypotheses, the journaled `GenerationEvent` /
  `ResearchProposal` time. For a confirmatory study, the pre-registration hash
  time.
- **Fold role:** Phase 7's mechanical partition label (IS/OOS/WF/HOLDOUT).
  **Unchanged and sealed.**
- **Evidence role:** derived by EvidenceRole(E, H, K) from the immutable
  Knowledge-PIT provenance graph K (§4); never stored or set manually. **This, not fold role, governs scientific use.**
- **Search attempt** (Phase-8 budget unit) vs **statistical test** (Phase-10
  inference unit): distinct.

---

## 4. Knowledge PIT (provenance formalization) — REVISED v3

### 4.1 Motivation

Data PIT answers "what data was knowable at t". Scientific inference
additionally needs "what evidence had the research process observed when H was
frozen". The two are independent. Perfectly PIT-clean data can be
scientifically contaminated for H, because an earlier result derived from the
same data informed H.

**v3 principle [D]:** an evidence role is **never** an intrinsic, stored or
manually editable property of a date range, dataset or fold. It is always
**derived**, by a deterministic function, from an append-only provenance and
exposure history.

### 4.2 The Knowledge-PIT history K [D]

K is an **append-only, content-addressed provenance/exposure graph**.

- Every node carries a content hash, a `recorded_at` time, and (where
  applicable) an `event_time`.
- Every edge is itself an append-only record with `recorded_at`.
- Nothing is edited or deleted.
- K is identified by the hash of its record log, so every role computation is
  reproducible against a named K.

The schema below is indicative only; it is not frozen here.

**Candidate node types:**

| Node | Meaning |
|---|---|
| `EvidenceArtifact` | a physical data source (provenance, packaging); its scientific identity is its **EvidenceFootprint** (§4.7), not its file, hash, vendor or dataset id |
| `DerivedMetric` | any function of evidence: metric, fold result, bit (ACCEPT, rank), plot, summary, DecisionRecord |
| `GeneratorInput` | a journaled generator-visible payload (visible history, feedback snapshot, prompt) |
| `HumanDecision` | a recorded human act: program, prompt or policy change, promotion, or a declared observation |
| `ExposureDeclaration` | a declared exposure or non-exposure for a channel that cannot be observed mechanically (human, public literature, model pretraining) |
| `ResearchProposal` / `Hypothesis` | Phase-9 proposal and the frozen hypothesis specification |
| `PreRegistration` | the hash-frozen confirmatory contract (§6, §9) |
| `ConfirmationStudy` | the execution that consumes a confirmation source under a PreRegistration |
| `AccessRecord` | a machine-logged read of an `EvidenceArtifact` by a component |

**Candidate edge types:**

| Edge | Meaning |
|---|---|
| `derived_from` | DerivedMetric → EvidenceArtifact / DerivedMetric |
| `included_in` | DerivedMetric → GeneratorInput / HumanDecision |
| `observed_by` | node → channel (generator, human:role, program, pretraining:model, public) |
| `influenced` | GeneratorInput / HumanDecision → ResearchProposal / Hypothesis / PreRegistration |
| `available_before` | EvidenceArtifact → time (earliest time the data existed or were obtainable) |
| `frozen_before` | Hypothesis / PreRegistration → time (hash-freeze time τ) |
| `consumed_by` | EvidenceArtifact → ConfirmationStudy |

**Derived sets:**

- **footprint(v)**: the union of the EvidenceFootprints (§4.7) of every
  `EvidenceArtifact` reachable from v through `derived_from*`.
- **Anc(H, K)**: the influence ancestry of H, i.e. every node with a path
  to H through `derived_from` / `included_in` / `influenced` / `observed_by`
  edges whose events precede τ(H), restricted to channels in Ch(H). For an
  LLM-generated H, Ch(H) = generator, program, humans who edit the program or
  prompt, promotion rule, and the generator's pretraining.
- **ExposedFP(H, K)** = ∪ footprint(v) over v ∈ Anc(H, K).

### 4.3 EvidenceRole(E, H, K) [D]

Here E is a candidate confirmation `EvidenceArtifact`, H is a hypothesis
under a `PreRegistration` P with freeze time τ_P, and K is the history.
The rules are applied in order; **the first match wins**.

| # | Role | Condition |
|---|---|---|
| 1 | **DEVELOPMENT** (known-exposed) | footprint(E) ∩ ExposedFP(H, K) ≠ ∅ (any cell overlap, conservatively; also covers `consumed_by` a previous study for H, which makes a re-analysis ROBUSTNESS, not confirmation) |
| 2 | **UNKNOWN_EXPOSURE** | the provenance *required* to decide rule 1 or rules 3–5 is missing (§4.6) |
| 3 | **CONFIRMATION_PROSPECTIVE** | `available_before`(E) > τ_P for every cell. The data did not exist when P was frozen, and no machine path exists. |
| 4 | **CONFIRMATION_HISTORICAL_RECORDED** | E predates τ_P; K contains mechanical separation evidence recorded **before** τ_P: E was hash-sealed and its `AccessRecord`s show no read by any Ch(H) machine component before τ_P, and no path exists. Human non-exposure is declared before τ_P. |
| 5 | **CONFIRMATION_HISTORICAL_DECLARED** | E predates τ_P; no machine path exists, but separation rests on declarations that cannot be mechanically audited, e.g. data existed in the environment before access logging, or separation is by human attestation only |

**Residual-exposure disclosures:** these are separate from the role.
- For every confirmation role, K must hold `ExposureDeclaration`s for the
  channels that cannot be observed mechanically:
  - model pretraining (documented model cutoff vs E's window);
  - public literature on the signal class over E's window;
  - human knowledge.
- These are **attached to the assessment as residuals**. They never upgrade a
  role (§5).

### 4.4 Invariants [D]

1. **Derivation.** A role is a pure function of (E, H, K). It may be cached
   only keyed by hash(K), and nothing may write a role directly.
2. **Append-only.** K is never edited. A correction is a new record.
3. **Downgrade-only monotonicity.** For K ⊆ K′, role(E, H, K′) ≤ role(E, H, K)
   in the order PROSPECTIVE > HISTORICAL_RECORDED > HISTORICAL_DECLARED >
   {UNKNOWN_EXPOSURE, DEVELOPMENT}.
   - Newly recorded *exposure* (including a late human declaration of prior
     exposure) may downgrade a role at any time.
   - Evidence of *non-exposure* counts only if recorded before τ_P (or before
     the first access of E, whichever is earlier). Absence cannot be
     back-filled.
4. **Chain closure.** Take W → `DerivedMetric`(OOS metric for H1)
   `derived_from` W → `included_in` `GeneratorInput` g → g `influenced` H2,
   with g's event before τ(H2). Then W ⊆ ExposedFP(H2, K), so by rule 1 every
   E with footprint overlapping W is DEVELOPMENT for H2. **This holds
   mechanically; no human judgment enters.**
5. **Relativity.** The same E can be CONFIRMATION for H1 (frozen before the
   exposure) and DEVELOPMENT for H2.

### 4.5 When OOS becomes development

Window W becomes development evidence for H at the first recorded event,
timestamped before τ(H), in which any node whose footprint overlaps W
enters Anc(H, K). In practice this is a journaled generator input, or a
recorded human or program decision derived from W.

- For H1 frozen before that event, W's role is unchanged.
- Any re-specification of H1 after the event is a new hypothesis, for which W
  is DEVELOPMENT.
- In Pilot 1A, generator inputs containing IS/OOS summaries preceded H2 and
  H3, so IS/OOS are DEVELOPMENT for them by invariant 4.

### 4.6 Machine-observable vs declared channels; fail-closed rules

What counts as observing is **any function of the data**, including a single
bit (the holdout-bit argument of Phase 9 §7a) [D].

| Channel | Observability | Recorded as |
|---|---|---|
| generator inputs | **machine, exact** | `GeneratorInput` from journals; footprints computed from source records (the TF firewall already computes them) |
| system reads of data | **machine, exact** if logged | `AccessRecord` |
| program / prompt / policy / promotion rules | **machine** (git, hash-frozen artifacts); the evidence *consulted* in making a change must be declared | `HumanDecision` + `derived_from` edges |
| human observation | **declared only** | `ExposureDeclaration`; repository artifacts create *implied* exposure (e.g. committed Phase-5A outputs, Pilot reports) |
| model pretraining | **declared, coarse** | `ExposureDeclaration` with documented cutoff; memorization is unobservable |
| public literature | **declared, coarse** | `ExposureDeclaration` (signal class × period) |

**Fail-closed rules [D]** (each one yields rule 2, UNKNOWN_EXPOSURE):
- E has no footprint, or a footprint that cannot be computed.
- Some `DerivedMetric` or `GeneratorInput` in the relevant history lacks
  `derived_from` edges, so its footprint is unknown.
- E predates τ_P and carries neither access records nor a pre-τ_P
  non-exposure declaration.
- Ch(H) contains a machine channel whose journal is missing or fails its
  integrity check.
- A required residual declaration (human, pretraining or public) is absent.
  Its absence yields UNKNOWN_EXPOSURE; a *present* declaration of exposure
  yields DEVELOPMENT or a downgraded grade (§5).

**[I]** Knowledge PIT is exact for recorded machine channels and conservative
elsewhere. It records declarations about human, public and pretraining
knowledge and never claims to reconstruct them.

### 4.7 EvidenceFootprint and confirmation freshness (v3.1) [D]

**Frozen invariant:** confirmation freshness belongs to the underlying
empirical observations, not to filenames, artifacts, vendors or dataset
identifiers.

An **EvidenceFootprint** identifies the underlying empirical observations a
study's evidence depends on. It is canonicalized along at least these
dimensions (the exact schema is frozen in the Phase-10 spec, not here):
- **population / universe:** vendor-independent canonical security
  identities;
- **time interval:** the observation dates;
- **variables / economic observations:** canonical economic variable class
  (e.g. adjusted close-to-close total return), not vendor field names;
- **prediction horizon:** mapped to the underlying realization interval, so
  an h-day forward return depends on the atomic observations it spans;
- **underlying observation identity:** the atomic (security, variable,
  interval) cells, so that renaming, repackaging, re-vendoring or
  transforming does not change identity.

**Consequences:**

| Case | Status |
|---|---|
| same stocks, dates and returns from a different vendor | same cells ⇒ **not** automatically fresh |
| same file split into two files | same cells ⇒ **not** two independent sources |
| same dates, partially overlapping universe | overlap detected and governed |
| same universe, partially overlapping dates | overlap detected and governed |
| same underlying returns transformed (ranked, standardized, re-horizoned) | derivations share cells ⇒ transformation does not manufacture fresh evidence |

**Conservative rules:**
- Material overlap with an already-consumed confirmation footprint, or with
  ExposedFP(H, K), must never silently receive fresh-confirmation status.
- If overlap cannot be determined reliably (unmappable identity, unknown
  variable class, unknown realization interval), the result **fails closed**
  (§4.6).
- The materiality threshold and the carving rules (e.g. whether a
  pre-registration may declare a footprint that excludes consumed cells)
  are frozen in the Phase-10 spec.

---

## 5. Evidence grades: statistical admissibility vs epistemic assurance — REVISED v3

**v3 correction.** v2 implied that only data not existing at freeze can give
strong confirmation. That conflated two properties:

- **Statistical admissibility:** is the confirmatory p-value valid?
  - It requires the family and test to be frozen before any function of E
    influenced selection.
  - It requires E to be independent of the selection information, or the
    test to be valid conditional on it (§9).
  - Historical data can satisfy this [T: data splitting, Cox 1975]. **Historical
    ≠ statistically invalid.**
- **Epistemic (Knowledge-PIT) assurance:** how much of that separation is
  *mechanically auditable* rather than declared.
  - Prospective data give the strongest, most auditable separation, because
    no channel can have observed data that did not exist.
  - Historical data depend on recorded or declared separation.

"Data not previously loaded" is still **not** "evidence not previously
known". The grade records how much of the separation is known versus
asserted.

### 5.1 Grade model [D]

| Grade (role) | Confirmatory inference admissible? | Epistemic claim strength | Mechanically certifiable | Declaration / nonclaim |
|---|---|---|---|---|
| **G1 PROSPECTIVE_UNEXPOSED** (rule 3) | **Yes** | Strongest: "confirmed on data that did not exist when the contract was frozen" | freeze hash and time; data availability times after τ_P; no machine path; one-time consumption | data-availability timestamps from the vendor are an attested input; stationarity beyond the window is an assumption |
| **G2 HISTORICAL, STRONG RECORDED SEPARATION** (rule 4) | **Yes** | Strong, qualified: "confirmed on historical data sealed and unread by any recorded machine channel before freeze; residual pretraining/public exposure disclosed" | seal hash; absence of `AccessRecord`s before τ_P; no machine path; pre-τ_P non-exposure declarations exist and are ordered | human non-exposure (declared); pretraining and public exposure are residuals, **not** proven absent |
| **G3 HISTORICAL, DECLARED SEPARATION** (rule 5) | **Yes, conditionally**: the inference is computed and reported, but the conclusion is labeled *declaration-dependent* | Moderate: "confirmatory *if* the declared separation holds" | freeze order; no *recorded* machine path; declarations exist and were recorded before τ_P | the separation itself; Phase 10 certifies only that the declaration was recorded, not that it is true |
| **G4 KNOWN-EXPOSED** (rule 1, DEVELOPMENT) | **No** (exploratory only) | None for confirmation | the exposure path itself | — |
| **G5 UNKNOWN_EXPOSURE** (rule 2) | **No** (fail closed) | None for confirmation | that required provenance is missing | — |

**Orthogonal descriptors** (they do not change the grade):
- **cross-market** (asset overlap, common global factors);
- **later vintage or alternate vendor of the same cells**. This is
  *measurement robustness, not new sampling evidence*. It is never a
  confirmation source for H if the original cells are in ExposedFP(H).

### 5.2 Model pretraining for LLM-generated hypotheses [D/I]

- **No impossible requirement.** Nothing requires proving that a historical
  window is absent from pretraining. That cannot be proven, and requiring it
  would make every pre-cutoff window unusable by definition.
- **Why this is a residual rather than disqualifying [I].**
  - Pretraining does not observe *the evaluation of H on E*.
  - At most, it encodes diffuse knowledge of market history and published
    anomaly performance, which may bias *which* hypotheses are generated
    toward ones that worked historically.
  - That is a threat to independence between selection and E, whose size is
    unknown. So it caps **claim strength**; it does not flip admissibility.
- **Conservative handling:**
  1. Each assessment records the model's documented cutoff versus E's window.
     E entirely after the cutoff removes the residual *as documented by the
     provider* (an attested input).
  2. If H is semantically close to a published anomaly documented over E's
     window, the public-literature residual is declared. A reviewer may
     downgrade to G3. Phase 10 does not infer semantic closeness (§10).
  3. Reports never state "unexposed to the model" for pre-cutoff data. They
     state "pretraining exposure: not reconstructable; residual disclosed".
  4. Prefer G1, or G2 with post-cutoff windows, for the strongest claims.

---

## 6. Estimand-first endpoint design

**Chain:** hypothesis → **estimand** θ (a population quantity for a declared
period) → estimator θ̂ → test and interval. Rank-IC is one estimator of one
estimand, not the endpoint of every hypothesis.

**Admissibility of an estimand [D].** An estimand is admissible only if all of
these hold:
1. It is a well-defined population quantity for the declared universe,
   horizon, period and construction.
2. It has a declared **direction** (one-sided hypothesis).
3. It has a declared **SESOI** δ in θ-units, justified from external evidence,
   discounted for expected post-sample decay (McLean-Pontiff 2016 [T for their
   sample]: −26% out-of-sample, −58% post-publication).
4. Its estimator is computed by the Phase-7 authority.
5. There is an inference method whose assumptions are stated and plausible
   for the series: HAC, fixed-b, or block bootstrap.
6. It has a declared **invariance class**, i.e. which signal transforms leave
   θ unchanged (§10).
7. **Economic estimands** are net of the declared cost model and turnover;
   statistical-only estimands are labeled as such.

**Admissible menu (initial) [D]:**

| Estimand | Hypothesis class | Invariance |
|---|---|---|
| mean daily rank-IC | cross-sectional ordering predictability | strictly increasing per-date transforms |
| mean daily Pearson IC | linear predictive association | positive per-date affine maps only |
| mean net long-short return (frozen construction and costs) | tradeable premium after costs | ordering (for quantile sorts with fixed breakpoints and matched missing/universe policy) |
| benchmark-adjusted alpha | marginal premium beyond known factors | construction-dependent; **not admissible until a certified benchmark factor set exists** |

**Primary vs secondary [D]:**
- **exactly one primary** estimand per hypothesis per study, enforced;
- secondary estimands are descriptive and never enter Holm;
- if several estimands are primary (e.g. rank-IC *and* net long-short), each
  is a separate hypothesis in the Holm family.

**Preventing endpoint shopping [D]:**
- The estimand is selected by the **research-program policy**, frozen before
  exploration, via a class → estimand map. The generator cannot choose it.
- It is hash-frozen into the pre-registration before the confirmation source
  exists or is readable.
- Any change after an exposure of the confirmation footprint voids the study
  for confirmatory use.

**Rank-IC status [D]:**
- *recommended* primary for ordering-predictability hypotheses;
- *diagnostic* (secondary) for tradeable-premium hypotheses;
- never universal.

---

## 7. Power and precision

### 7.1 Where N and T enter [T/A]

- **Daily estimator:** ρ̂_t = Spearman correlation across N_t stocks between
  the signal at t and the return over (t, t+h].
- **Cross-sectional uncertainty (N):**
  - under H0 with N independent, exchangeable pairs, Var(ρ̂_t) = 1/(N−1) [T];
    at N = 26, SD 0.20;
  - cross-sectional dependence (common factors, industry clustering) raises
    the effective variance [A/I];
  - true time variation in ρ_t adds a component that does **not** shrink with
    N [A].

  So σ² = Var(ρ̂_t) ≥ 1/(N−1) in practice [I].
- **Time-series inference (T):** the estimand is μ = E[ρ_t] over the period.
  The estimator is ρ̄ = (1/T) Σ ρ̂_t, with Var(ρ̄) ≈ σ²_LR / T, where
  σ²_LR = σ²(1 + 2Σ_k γ_k) is the long-run variance [T under stationarity].
  - Effective sample size T_eff = T σ² / σ²_LR.
  - **N·T is not the sample size**: observations within a date are one
    correlated cross-section summarized by one ρ̂_t, and dates are serially
    dependent.
- **HAC:** a Newey-West estimate of σ²_LR with lag ℓ declared ex ante
  (ℓ ≥ h − 1). In small T, standard critical values over-reject; fixed-b
  critical values (Kiefer & Vogelsang 2005 [T]) are preferable.
- **Missing data [A]:** the Pilot panel is complete (26 names on every date;
  one purged date per boundary). The holdout has T = 52 formation dates.

### 7.2 Pilot-1A MDE — illustrative diagnosis

- **Test:** H0: μ ≤ 0 vs H1: μ = δ > 0, power 0.80.
- **Formula:** MDE = (z_{1−α} + z_{0.80}) · σ_LR / √T, with T = 52 and
  σ_LR = σ·√((1+φ)/(1−φ)) (AR(1)).

| σ (daily rank-IC) | φ | T_eff | one-sided α=.05 | two-sided α=.05 | one-sided α=.05/3 | two-sided α=.05/3 |
|---|---|---|---|---|---|---|
| 0.20 (null floor) | 0.0 | 52.0 | 0.069 | 0.078 | 0.082 | 0.090 |
| 0.20 | 0.2 | 34.7 | 0.084 | 0.095 | 0.101 | 0.110 |
| 0.25 | 0.0 | 52.0 | 0.086 | 0.097 | 0.103 | 0.112 |
| 0.25 | 0.2 | 34.7 | 0.106 | 0.119 | 0.126 | 0.137 |
| 0.30 | 0.0 | 52.0 | 0.103 | 0.117 | 0.124 | 0.135 |
| 0.30 | 0.3 | 28.0 | 0.141 | 0.159 | 0.168 | 0.183 |

- **Unestimable from the repo:** the per-date IC series is not recorded, so σ
  and φ cannot be estimated.
- For the **net long-short** estimand, the development-dated series (T = 175)
  shows small autocorrelations (ACF₁ 0.027 for Exp 1/2 and 0.103 for Exp 3).
  That is evidence of low serial dependence for *that* estimand only.
- **Conclusion [I]:** the MDE is ≥ ≈0.07 even under the null-floor
  assumption, and plausibly 0.09–0.15, against practitioner-scale ICs of
  roughly 0.02–0.05 (a heuristic, not a theorem). Detection power is therefore
  negligible.

**Refutation is a different matter [I].**
- Exp 1's holdout rank-IC of −0.0463 gives one-sided 95% upper bounds of
  −0.001 / +0.011 / +0.022 / +0.034 for σ_LR = 0.20 / 0.25 / 0.30 / 0.35.
- Against a pre-registered δ = 0.03, a TOST-style bound (Schuirmann 1987;
  Lakens 2017 [T/M]) would reject θ ≥ δ for σ_LR up to ≈0.33.
- Caveats: with T = 52, HAC standard errors are imprecise [T: Kiefer &
  Vogelsang], so the bounds are approximate.
- None of this applies to Pilot 1A: nothing was pre-registered, and the window
  is exposed.

### 7.3 Sharpe

- Under i.i.d. normal returns, SE(ŜR_ann) ≈ √252·√((1 + SR_d²/2)/T) (Lo 2002
  [T]) ≈ **2.20** at T = 52. Opdyke (2007) [T] gives the stationary-ergodic,
  non-normal, serially correlated generalization.
- MDE (one-sided α = .05, 80%) ≈ **5.5 annualized**.
- The holdout Sharpe of −1.80 has a 95% interval of roughly [−6.1, +2.5]. It
  is uninformative.
- Sharpe is not recommended as a primary estimand at short T.

### 7.4 Protocol-suitable quantities [D]

- A frozen protocol may not use the Pilot numbers.
- At registration it must estimate σ and σ_LR **from development evidence only**
  for the declared estimand. That requires the exported per-date series (a
  Phase-7 extension; §13).
- It must record the MDE for the declared δ, α and T of the confirmation
  source, together with a sensitivity band (e.g. φ ∈ {0, 0.2}).
- Disclosure is mandatory; a hard gate is optional (§12).

---

## 8. Multiple testing and adaptive search

### 8.1 Roles

- **Search governance:** Phase 8's fixed-m budget. Valid as a *budget*.
- **Confirmatory inference:** Holm over the pre-registered family on
  confirmation evidence (§9).
- **Exploration reporting:** DSR, Romano-Wolf/SPA or PBO where the tried set
  is re-evaluable on a common window. Report only.

### 8.2 Classical results used [T]

- **Holm (1979):** FWER ≤ α under arbitrary dependence among *valid*
  p-values.
- **Benjamini & Hochberg (1995):** FDR under independence or PRDS. **Benjamini
  & Yekutieli (2001):** FDR under arbitrary dependence at a log m cost.
- **White (2000), Hansen (2005), Romano & Wolf (2005):** bootstrap FWER/SPA
  tests for a *pre-specified* set of strategies under stationarity and weak
  dependence.
- **Bailey & López de Prado (2014), DSR; Bailey et al. (2017), PBO:**
  approximations or diagnostics requiring trial counts, trial variance, or a
  common strategy × time matrix.

### 8.3 What does not survive LLM adaptivity [I]

- Any p-value on data in the influence set of the hypothesis.
- BH or online-FDR over exploration p-values: the online-FDR guarantees
  (Javanmard & Montanari 2018; Ramdas et al.) need conditionally valid
  p-values given the past, and reused data breaks that.
- Bonferroni with a data-dependent m.
- DSR as a gate (the effective trial count is unknowable).
- Reality Check / SPA over a set that grew adaptively on the same window.

### 8.4 Fixed-m Bonferroni (Phase 8)

Valid FWER for a pre-declared family of m tests on valid p-values [T]. It is
correctly scoped by Phase 8 as a budget. It is not a scientific correction for
the adaptive exploration history.

### 8.5 E-values — FUTURE RESEARCH CANDIDATE

- **Result [T]:** e-BH (Wang & Ramdas 2022) controls FDR at level α for
  **any** dependence among **valid** e-values (E[e] ≤ 1 under each null).
- **Construction problem [T/A]:**
  - Anytime-valid e-processes and confidence sequences (Howard et al. 2021;
    Waudby-Smith & Ramdas 2024; Ramdas et al. 2023) are nonnegative
    supermartingales under the null with respect to a filtration F_t.
  - For a daily series X_t (e.g. rank-IC, bounded in [−1, 1]), the betting
    construction E_t = Π(1 + λ_s(X_s − m)) with predictable λ_s is valid under
    H0 when **E[X_t | F_{t−1}] = m** (conditional-mean null).
- **Mismatch [I]:**
  - Our estimand is the *unconditional* period mean. Serially dependent
    returns can have unconditional mean ≤ 0 with conditional means sometimes
    positive. The conditional-mean null is then false, so the test "detects"
    something other than the estimand.
  - Conversely, rejecting the conditional null establishes only that the
    conditional mean was positive at some times.
  - Cross-sectional dependence enters within each daily X_t and is not the
    problem. Serial dependence and conditional heteroskedasticity are.
  - Optional stopping is valid only for the conditional null.
  - Adaptive hypothesis generation is harmless **only** if H is frozen before
    the monitored data begin (prospective).
  - Unbounded estimands (net returns) need bounds or truncation that alter the
    estimand.
- **Conclusion:** not an architectural commitment. Revisit if a prospective
  program with a meaningful conditional-mean estimand is defined.

---

## 9. Holm after arbitrary adaptive exploration — REVISED v3

**Holm (1979) [T].** For m hypotheses whose individual p-values are valid,
i.e. P(p_i ≤ u) ≤ u for every true null (exactly or asymptotically), the step-down
procedure controls FWER ≤ α. That holds under **any** dependence among the
p_i. Holm needs no independence among p-values and does nothing to repair an
invalid p-value.

**Conditions for a promoted family after adaptive LLM exploration:**

1. **Frozen contract.** Before τ_P, a `PreRegistration` P hash-freezes:
   - the family F;
   - for each H ∈ F: one primary estimand, direction, null (θ ≤ 0), SESOI δ,
     and test;
   - α_study;
   - the confirmation source;
   - the **dependence-handling design** (condition 4).
2. **Knowledge-PIT admissibility.** EvidenceRole(E, H, K) ∈ {G1, G2, G3} for
   **every** H ∈ F (§4–5). G3 results carry the declaration-dependent label.
3. **Validity conditional on selection.** For each true null H_i ∈ F, p_i is
   valid conditional on the information that selected F. This holds when E is
   independent of the selection information under the null, or when the
   test's validity does not depend on it.
   - Serial dependence between development-period and confirmation-period
     data can violate this for time-adjacent historical windows.
   - That is a **threat to the individual test's validity**, to be addressed
     by the predeclared dependence design (condition 4). It is not a Holm
     requirement.
4. **Valid individual inference under a predeclared dependence design.** Each
   confirmatory analysis predeclares how it handles dependence. Candidates,
   when justified:
   - HAC / Newey-West with a declared lag rule and fixed-b critical values
     (Kiefer & Vogelsang 2005);
   - block or stationary bootstrap with a declared block rule;
   - non-overlapping observations (e.g. sampling at the horizon h);
   - an explicit gap or embargo between development and confirmation data,
     where the dependence structure calls for one;
   - other dependence-aware resampling or validated time-series procedures.

   The design must match:
   - the estimand;
   - the horizon and **overlap structure** (h-day returns overlap at daily
     sampling ⇒ MA(h−1) dependence at least);
   - the serial dependence observed in *development* data;
   - the cross-sectional construction (per-date statistics summarize one
     correlated cross-section; stocks × dates are not independent).

   No design is universally required; none is universally sufficient.
5. **One use.** E's EvidenceFootprint (§4.7) is consumed once, by this study
   (`consumed_by` in K). Freshness is keyed to underlying observations, not to
   artifacts.

**What Holm then controls [T, given 1–5]:** P(≥ 1 false rejection among the
true nulls in F, for the confirmation-period estimands) ≤ α_study. This holds
under any dependence among the p_i, however F was selected, including by an
LLM over arbitrary development history.

**What it does NOT do:**
- make invalid individual financial time-series p-values valid;
- provide any family-wise guarantee for NOT_SUPPORTED conclusions, which are
  hypothesis-local (§11);
- correct errors in the exploration history;
- correct winner's-curse bias (development estimates are biased upward);
- guarantee that F contains the best candidates;
- generalize beyond the confirmation period (that needs stationarity [A]);
- control error across multiple studies or windows (that needs study-level
  α allocation [D]);
- upgrade G3 declarations or pretraining residuals into certified facts.

---

## 10. Equivalence relations

| Relation | Definition | Decidable? |
|---|---|---|
| expression equivalence | identical canonical `FactorSpec` AST | yes (hash) |
| ordering equivalence | identical per-date orderings of scores (incl. ties) on every date and name, under the same missing-data and universe policy | provable by declared invariance rules; empirical check only on observed data |
| portfolio equivalence | identical holdings sequence under a frozen construction | follows from ordering equivalence for quantile sorts with fixed breakpoints |
| estimand-specific statistical equivalence | θ̂ and its inference are identical functions of the data for the declared estimand | yes, for declared invariance classes (§6) |
| semantic hypothesis equivalence | same economic claim | **not decidable; a nonclaim** |

`standardize(ret)` and `rank(ret)`:
- **statistically equivalent for rank-IC and quantile-sort estimands**, with
  identical missing/universe handling (in Pilot 1A, `missing_policy` differed —
  `propagate` vs `drop` — which is harmless only because no data were
  missing);
- **not equivalent for Pearson IC** (the observed Pearson ICs differ).

**Minimum machinery for counting [D]:**
- declared invariance rewrites per admissible estimand;
- a policy-match check (missing policy, universe filter, winsorization);
- applied at **promotion**, so one representative per estimand-specific class
  enters F.

No learned or LLM equivalence.

---

## 11. Scientific assessment (minimal state model)

| Axis | States |
|---|---|
| governance_validity | VALID / INVALID (from Phase 8 and provenance) |
| evidence_role | derived: DEVELOPMENT / UNKNOWN_EXPOSURE / CONFIRMATION_PROSPECTIVE / CONFIRMATION_HISTORICAL_RECORDED / CONFIRMATION_HISTORICAL_DECLARED / ROBUSTNESS (§4), with the K hash |
| statistical | SUPPORTED / NOT_SUPPORTED / INCONCLUSIVE / NOT_ASSESSED |
| economic | same states when an economic estimand is primary or declared secondary; otherwise NOT_ASSESSED |
| production_readiness | NOT_CERTIFIED (constant) |

**Statistical rule (frozen, v3.1):**
- **SUPPORTED:** the confirmatory primary test rejects its null in the
  declared direction after the frozen family's Holm procedure.
  - Multiplicity guarantee: Holm FWER control over the frozen confirmatory
    family, conditional on valid individual p-values.
- **NOT_SUPPORTED:** a predeclared hypothesis-local inferential bound (e.g. a
  one-sided (1−α) upper bound, TOST-style, Schuirmann 1987) excludes the
  predeclared SESOI, per the frozen analysis contract.
  - This is a **hypothesis-local** conclusion. Phase 10 claims no family-wise
    refutation or error control for NOT_SUPPORTED unless a future protocol
    explicitly adds and preregisters such a procedure.
- **INCONCLUSIVE:** neither the SUPPORTED condition nor the hypothesis-local
  NOT_SUPPORTED condition is established.
- **NOT_ASSESSED:** the evidence or protocol is not admissible for scientific
  assessment. Examples:
  - DEVELOPMENT or UNKNOWN_EXPOSURE evidence;
  - a non-preregistered study;
  - an already-consumed (overlapping) confirmation footprint;
  - invalid or incomplete required provenance.

**Grade qualifier:**
- G1, G2 or G3 per §5, with the attached residual declarations (human,
  public literature, pretraining);
- G3 conclusions are labeled *declaration-dependent*.

Robustness and replication are recorded as **links to other studies**, not
states, in the minimum version.

---

## 12. REVISED MINIMUM DEFENSIBLE VERSION

| # | Component | Why necessary | Failure prevented | Repo data today | Deterministic? |
|---|---|---|---|---|---|
| 1 | **Knowledge-PIT append-only provenance graph + derived EvidenceRole(E, H, K)** (machine channels exact from journals and access records; human, pretraining and public declared; missing provenance fails closed; downgrade-only) | Confirmatory status depends on what influenced H | Calling development or exposed data "confirmation" (e.g. Pilot OOS, the Phase-5A window) | **Partial:** generator exposures fully journaled (Pilot/H6); Phase-5A and Pilot-report human exposures declarable; model cutoffs documented | yes, given the log |
| 2 | **Hash-frozen confirmatory pre-registration:** family, one primary estimand per H from the admissible menu, direction, δ, α_study, inference method and **predeclared dependence-handling design** (e.g. HAC + fixed-b, block bootstrap, non-overlapping sampling, gap where justified), confirmation source; estimand chosen by program policy | Validity of the confirmatory test requires everything frozen before confirmation evidence | Endpoint shopping, forking paths, post-hoc SESOI | **Missing** (new object) | yes |
| 3 | **One-time confirmation consumption keyed to EvidenceFootprint** (§4.7; study-level, batch; underlying observations, not artifacts) | Reuse reintroduces adaptivity | Repeated testing on the same confirmation data | **Partial:** Phase-8 single-use `holdout_id` mechanics exist but are experiment-level | yes |
| 4 | **Estimand-specific inference on per-date series** under the predeclared dependence design | A confirmation-window test statistic must exist | Using pooled or holdout-mixed statistics (today's only t-stats) | **Partial:** net long-short per-date series recorded; rank/Pearson-IC per-date series **missing** (Phase-7 extension) | yes |
| 5 | **Holm over the frozen family** (SUPPORTED) plus a hypothesis-local TOST-style NOT_SUPPORTED against δ (no family-wise claim) | FWER over the promoted family for SUPPORTED; refutation distinct from non-significance | Multiple-candidate false positives; "not significant ⇒ refuted" | computable from item 4 | yes |
| 6 | **Minimal assessment record** (§11), separate from DecisionRecord; estimand-specific equivalence check at promotion | Governance ACCEPT must never read as support; duplicate tests must not appear as independent support | ACCEPT-as-evidence; double-counted support | trivially constructible | yes |

**Removed from v1's minimum** (still recommended later):
- robustness and replication axes (now study links);
- the hard power gate (now MDE **disclosure**, which requires item 4's
  series);
- development-only Phase-7 partitions. Unnecessary: exploration is capped
  before the confirmation footprint, as the Pilot G1 cap already does, and
  its folds are simply development evidence.
- the general canonical-class machinery (reduced to estimand-specific
  invariance rules at promotion).

**Expected output on today's data:** evidence_role = DEVELOPMENT or
UNKNOWN_EXPOSURE for every available window, so statistical = NOT_ASSESSED.
That is correct.

---

## 13. Required changes to the current architecture

**Sealed guarantees that must remain intact:**
- Phase 6: admission and trust.
- Phase 7: purge rules and sole metric authority.
- Phase 8: append-only registry, identities, exact single-use mechanics,
  budget locks.
- Phase 9: write-ahead, proposal-before-evidence, structural firewall.
- The Pilot harness TF firewall.
- DecisionRecord keeps its governance meaning.

**Placement [D]:**
1. **New layer after Phase 8 (Phase 10):** Knowledge-PIT provenance graph and derived EvidenceRole,
   pre-registration, confirmation-source consumption, inference from recorded
   series, Holm/TOST, and the assessment record. Read-only over Phases 6–9.
2. **Phase-7 versioned extension:** export per-date series per fold for each
   admissible estimand. Fold-level HAC may live in Phase 10 as *inference on
   recorded series* rather than a metric recomputation, which avoids
   duplicating Phase-7 metric authority. **Sealed change — separate
   authorization.**
3. **Phase-8 versioned extension:** study-level (batch) confirmation-source
   consumption, and a persistent cross-run ledger (merges deferred item 7).
   It could alternatively live wholly in Phase 10 as a new governance object,
   leaving Phase 8 untouched. **To decide at the Phase-10 design barrier.**
4. **Phase 9:** feed generator-input journals into the provenance graph (already
   journaled), document OOS feedback as development evidence, and apply FIX B.
5. **Data:** prospective accrual (G1) and/or a broader universe (power);
   sealed, access-logged historical sources (G2) are a valid, weaker-assurance
   complement.

---

## 14. Data and metadata requirements

| Requirement | Status |
|---|---|
| Hypothesis and experiment history, lineage, freeze times | **available** (Phase 8/9, journals) |
| Generator-exposure footprints | **available** (journaled inputs + record partitions) |
| Human exposure (Phase-5A, Pilot report) | **declarable**; general human knowledge **fundamentally unobservable** |
| Pretraining exposure | **coarse** (cutoff dates); memorization **fundamentally unobservable** |
| Public-literature exposure | **coarse declaration** |
| Per-date rank/Pearson-IC series per fold | **missing** |
| Per-date net long-short, turnover | **partially available** (primary construction) |
| Fold-level SEs | **missing** (derivable once series exist) |
| Estimand-specific invariance rules | **missing** (small, declarable) |
| Effective number of independent tests | **fundamentally difficult** (not required by the MDV) |
| Benchmark factor-model residuals | **missing** (alpha estimand not admissible yet) |
| Prospective confirmation data | **missing** (accrues after freeze) |
| Replication datasets | **missing** |

---

## 15. Failure modes

1. **Promotion reads confirmation data.** Guard: provenance-graph check at
   τ_P (access records, footprint overlap).
2. **Pre-registration edited after peeking.** Guard: hash plus provenance
   ordering.
3. **Undeclared human exposure.** Guard: attestations. Residual risk is
   stated.
4. **SESOI gaming.** Guard: policy bounds and external justification.
5. **Incomplete invariance rules.** Effect: conservative over-counting
   (Holm remains valid).
6. **Serial dependence across the development/confirmation boundary.**
   Guard: the predeclared dependence-handling design (a gap is one option).
   Residual risk: approximate conditional validity of the individual test.
7. **Short-T HAC over-rejection.** Guard: fixed-b critical values and
   bootstrap sensitivity.
8. **Regime change.** Prospective failure is reported as such, not
   overwritten.
9. **Repeated underpowered studies on successive windows.** Guard:
   study-level α allocation plus MDE disclosure.
10. **LLM memorization.** Guard: pretraining is a disclosed residual that caps
    claim strength (§5.2). Prefer G1 or post-cutoff G2 windows for the
    strongest claims.
11. **Back-filled non-exposure.** Guard: non-exposure evidence counts only if
    recorded before τ_P (§4.4, invariant 3).
12. **Repackaged or re-vendored confirmation data.** Guard: freshness keyed to
    the EvidenceFootprint (§4.7); unknown overlap fails closed.

---

## 16. Explicit nonclaims

This framework does not claim:
- reconstruction of human, public or pretraining knowledge, or proof that a
  historical window is absent from pretraining;
- the truth of recorded declarations (G3 separation, human non-exposure);
- that Holm repairs invalid individual p-values;
- family-wise error control for NOT_SUPPORTED (hypothesis-local only);
- data equivalence beyond what footprint canonicalization establishes;
- general semantic-equivalence detection;
- correction of the adaptive exploration history;
- valid inference on development or unknown-exposure evidence;
- generalization beyond the confirmation period without stationarity;
- a reliable effective-number-of-tests estimate;
- e-value validity for our estimands;
- that any Pilot-1A factor is supported or refuted;
- production readiness.

---

## 17. Phase 10 — responsibility, name and certification — REVISED v3

**Responsibility:** decide *which evidence may support which scientific
conclusion about which hypothesis*, from immutable provenance, and refuse
otherwise.

- It is **not** a decision layer (decisions and governance are Phase 8).
- It is **not** a statistics library.
- It is a **knowledge-provenance and confirmation protocol**.

**Name:** **Phase 10 — Knowledge-PIT & Confirmatory Evidence Protocol.**

### 17.1 Candidate certification statement (exact, for review)

> **Phase 10 certifies, mechanically and deterministically, that:**
>
> 1. **Immutable provenance.** The Knowledge-PIT history K is append-only and
>    content-addressed. Every generator input, derived metric, recorded human
>    or program decision, system access record, exposure declaration,
>    hypothesis, pre-registration and confirmation study used by the protocol
>    is a hash-identified record in K. No record is edited or deleted, and
>    every result names the hash of the K it was computed against.
> 2. **Derived, hypothesis-relative evidence roles.** For every (evidence
>    source E, hypothesis H), the evidence role is computed by the frozen
>    function EvidenceRole(E, H, K) and is never stored or set manually.
>    - Any recorded provenance path from data overlapping E's footprint into
>      H's influence ancestry before H's freeze yields DEVELOPMENT.
>    - Missing required provenance yields UNKNOWN_EXPOSURE.
>    - Evidence of non-exposure counts only if recorded before freeze, so
>      roles can only be downgraded by later records.
> 3. **Pre-registration before confirmatory evidence.** A confirmatory
>    conclusion is produced only for a hypothesis whose pre-registration was
>    hash-frozen before any recorded observation of its confirmation evidence
>    by an influencing channel.
> 4. **One primary estimand and test contract.** Each pre-registered
>    hypothesis has exactly one primary estimand, direction, null, SESOI, test
>    and predeclared dependence-handling design. Its inference is computed
>    exactly as declared from the Phase-7 per-date series of the confirmation
>    source. Secondary quantities are never inferential.
> 5. **One-use confirmation footprints.** Confirmation freshness is keyed to
>    the EvidenceFootprint of underlying observations, not to files, artifact
>    hashes, vendors or dataset ids. Each footprint is consumed by at most one
>    confirmatory study. Any overlapping later analysis is classified
>    ROBUSTNESS or DEVELOPMENT, never confirmation, and undeterminable overlap
>    fails closed.
> 6. **Predeclared individual inference and multiplicity control.**
>    Individual p-values and bounds are computed by the predeclared
>    procedure.
>    - SUPPORTED = Holm rejection in the declared direction over exactly the
>      frozen confirmatory family (FWER control conditional on valid
>      individual p-values).
>    - NOT_SUPPORTED = a hypothesis-local predeclared bound excludes SESOI,
>      with no family-wise claim.
>    - INCONCLUSIVE otherwise.
>    - All of this is conditional on the predeclared procedure's validity
>      assumptions, which are recorded, not certified.
> 7. **Refusal.** The statistical assessment is NOT_ASSESSED whenever the
>    role is DEVELOPMENT, UNKNOWN_EXPOSURE or ROBUSTNESS, the study is not
>    pre-registered, or its confirmation footprint overlaps an already-consumed
>    footprint, or required provenance is invalid or incomplete.
> 8. **Graded, disclosed claims.** Every assessment records its evidence
>    grade (G1–G3) and attaches the recorded human, public-literature and
>    model-pretraining exposure declarations as disclosed residuals.
> 9. **Separation from governance.** The scientific assessment is a record
>    distinct from the Phase-8 DecisionRecord. A governance ACCEPT,
>    development evidence and unknown-exposure evidence are never converted
>    into scientific support, and production readiness is always
>    NOT_CERTIFIED.

### 17.2 Phase-10 nonclaims

Phase 10 does **not** certify or claim:
- that any factor is true, or that any alpha or premium exists;
- production readiness or tradeability;
- complete reconstruction of human knowledge or exposure;
- complete reconstruction of LLM pretraining knowledge, or absence of any
  window from pretraining;
- the truth of declarations: G3 separations, human non-exposure, vendor
  availability timestamps, model cutoffs;
- universal validity of any statistical test. Validity of the individual
  p-values rests on the predeclared dependence design's recorded
  assumptions;
- that Holm repairs invalid individual p-values, corrects the exploration
  history, or controls error across studies;
- family-wise error control for NOT_SUPPORTED;
- semantic equivalence in general (only declared, estimand-specific
  invariance rules);
- reusable confirmation data;
- generalization beyond the confirmation period.

### 17.3 Later, optional (not Phase 10)

- exploration reports (DSR, SPA/Romano-Wolf, PBO);
- robustness and replication axes;
- marginal-contribution tests vs benchmark factors;
- hierarchical shrinkage;
- e-value research;
- reusable-holdout research;
- a preregistered family-wise procedure for NOT_SUPPORTED (refutation).

### 17.4 Recommended Phase-10 content

- **(a)** A design barrier freezing:
  - the Knowledge-PIT schema and EvidenceRole function;
  - the grade model;
  - the pre-registration contract, including the dependence-design menu;
  - the admissible estimand menu;
  - the invariance rules;
  - the assessment record.
- **(b)** The Phase-7 per-date series export.
- **(c)** The confirmation-source consumption object and cross-run ledger
  (placement decided at the barrier), merged with FIX B.
- **(d)** The minimum layer (§12).
- **(e)** A separate prospective-accrual and/or broader-universe data track.
- **(f)** P1A-SV before any further real run.

---

## 18. Pilot-1A worked example (revised)

| | `standardize(ret)` | `rank(ret)` | `rank(rolling_mean(ret,5))` |
|---|---|---|---|
| governance_validity | VALID | VALID | VALID |
| estimand-specific class (rank-IC) | C1 | C1 (equivalent; Pearson IC not) | C2 |
| evidence_role of IS/OOS | DEVELOPMENT (known human exposure of the window via Phase-5A artifacts; the program was designed with it) | DEVELOPMENT (additionally: generator input with C1's IS/OOS preceded τ, invariant 4) | DEVELOPMENT |
| evidence_role of holdout | DEVELOPMENT (Phase-5A human exposure; now also Pilot-report exposure) | same | same |
| pre-registered estimand / δ | none | none | none |
| statistical | **NOT_ASSESSED** | **NOT_ASSESSED** | **NOT_ASSESSED** |
| economic (exploratory note) | net spread IS −0.0003/day, OOS ≈ 0; turnover ≈70%/day | identical portfolios | IS negative, OOS positive (development) |
| production | NOT_CERTIFIED | NOT_CERTIFIED | NOT_CERTIFIED |

**Correction to v1:** had C1 been pre-registered with rank-IC, direction +,
δ = 0.03, and a clean window, the observed −0.046 would probably have yielded
NOT_SUPPORTED (§7.2). The Pilot's NOT_ASSESSED is due to exposure and the
absence of pre-registration, **not** to power alone. Detection of a true
δ ≈ 0.03 would still have been impossible.

---

## Sources

**Primary methodological sources verified for this revision:**
- Cox (1975), Biometrika 62 — https://academic.oup.com/biomet/article-abstract/62/2/441/337164
- Kiefer & Vogelsang (2005), Econometric Theory 21(6) — https://ideas.repec.org/a/cup/etheor/v21y2005i06p1130-1164_05.html
- Howard, Ramdas, McAuliffe & Sekhon (2021), Ann. Stat. 49(2) — https://projecteuclid.org/journals/annals-of-statistics/volume-49/issue-2/Time-uniform-nonparametric-nonasymptotic-confidence-sequences/10.1214/20-AOS1991.full
- Waudby-Smith & Ramdas (2024), JRSS-B 86(1) — https://academic.oup.com/jrsssb/article/86/1/1/7043257
- Wang & Ramdas (2022), JRSS-B 84(3) — https://academic.oup.com/jrsssb/article/84/3/822/7056146
- Schuirmann (1987) — https://www.semanticscholar.org/paper/A-comparison-of-the-Two-One-Sided-Tests-Procedure-Schuirmann/053b97e316fc43588e6235f88a1a7a4077342de7
- Lakens (2017) — https://journals.sagepub.com/doi/10.1177/1948550617697177
- Opdyke (2007) — https://www.semanticscholar.org/paper/Comparing-Sharpe-ratios:-So-where-are-the-p-values-Opdyke/77448b71402c706a687fe86231d6d895d67252cc
- Ledoit & Wolf (2008) — http://www.ledoit.net/Robust_Sharpe_2008.pdf

**Verified in v1:**
- Harvey, Liu & Zhu (2016) — https://academic.oup.com/rfs/article/29/1/5/1843824
- Hou, Xue & Zhang (2020) — https://academic.oup.com/rfs/article-abstract/33/5/2019/5236964
- Jensen, Kelly & Pedersen (2023) — https://onlinelibrary.wiley.com/doi/full/10.1111/jofi.13249
- Chen, Lopez-Lira & Zimmermann — https://arxiv.org/abs/2212.10317
- McLean & Pontiff (2016) — https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.12365
- Giglio, Liao & Xiu (2021) — https://academic.oup.com/rfs/article-abstract/34/7/3456/5911131
- Dwork et al. (2015) — https://www.science.org/doi/10.1126/science.aaa9375
- Bailey & López de Prado (2014) — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551
- Bailey et al. (2017) — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253

**Classical, cited from their published versions (not re-fetched):** White
(2000); Hansen (2005); Romano & Wolf (2005); Holm (1979); Benjamini & Hochberg
(1995); Benjamini & Yekutieli (2001); Javanmard & Montanari (2018); Ramdas et
al. (2023, Stat. Sci.); Lo (2002); Newey & West (1987).

**LLM factor-mining papers** (AlphaAgent, Chain-of-Alpha, QuantaAlpha,
arXiv 2507.17211) are cited only as evidence that the field recognizes
feedback-loop overfitting and pretraining leakage. **They are not authority
for any statistical validity claim in this document.**

**Computations** (§7.2 table; development-dated autocorrelations) were
performed on recorded Pilot-1A series restricted to dates before 2026-07-01.
No holdout-dated observation was used as a protocol input.
