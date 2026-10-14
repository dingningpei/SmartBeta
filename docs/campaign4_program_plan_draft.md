# Campaign-4 — program plan (DRAFT for review; nothing frozen or authorized)

- **Status:** planner draft, 2026-10-14. Written after the Campaign-3 closeout
  (`docs/campaign3_closeout_report.md`, `ee4a17e`). Nothing here freezes a design,
  authorizes data acquisition, a vendor account, a K-PIT append, a generator call or a
  reserve access.
- **Anti-outcome-bias:** this draft uses only what the closeout already reports. The
  Campaign-3 IS evidence is generator-visible tier evidence. No OOS, development-sealed or
  reserve statistic was read for this draft.

## 0. What Campaign-3 taught (the inputs to this plan)

| # | Lesson | Source |
|---|---|---|
| L1 | **Power is the binding constraint, not the pipeline.** The whole discovery chain ran end to end. But the IS top-1 of 99 candidates reached a minimum-scenario L-envelope t of only 0.19, and only rank 1 was positive. With 4.5 IS years, detecting an effect at 50% power needs an annual net Sharpe of about 1.2. Realistic large/mid-cap price-anomaly Sharpes (about 0–0.5) are not detectable on Dataset v1.2 | closeout §2; `dataset_v2_history_feasibility.md` §1 |
| L2 | **The T-B fold design was infeasible from the start.** The DEVELOPMENT_SEALED fold carries partition role `holdout`, and D7b refuses every holdout fold. OOS alone has 18 periods, below the EWC requirement of T ≥ 24 | closeout §3; P5-E2 |
| L3 | **The TS scenario failed almost universally.** Under TS, almost every candidate's L-envelope t was negative in its declared sign. TS sets τ = −1 on long-leg and +1 on short-leg terminal (delisting) returns (P-Q v2.1 §9). So the confirmation rule "B and L under all T0/TS × K0/K1" is very hard to pass for any factor whose legs carry INCOMPLETE terminal returns | closeout §2, §6.8 |
| L4 | **Process.** Errata to a hash-bound document must be separate documents. Every frozen-design dependency on a sealed module must be checked against that module **before** the freeze | closeout §6.7 |
| L5 | **Engineering.** The forwarding step is single-core. Panels are rebuilt for verification. `build_event_inputs` is not vectorized. The full suite takes about 805 s, against the 900 s Phase-5A bound | closeout §6.1–6.6 |

**What is still clean:**
- **Reserve:** 2023-07-03 … C = 2026-08-31, 37 usable formations. **Zero opens**, no ACCESS
  record.
- **DEVELOPMENT_SEALED fold:** 2022-01-31 … 2023-06-30. Never evaluated, because every
  cell was NOT_ASSESSED before any value was computed.
- **OOS fold:** read by Campaign-1's screen. During Campaign-3 the first selected
  candidate's partial OOS diagnostics were computed but not used for any decision.
  **Disclosure carried:** OOS is not pristine.
- **Post-C data:** sessions after 2026-08-31 lie outside every Dataset v1 tier. They
  accrue at about 12 formations per year.

## 1. Strategic choice (decision S1; everything else depends on it)

| Path | What it is | Gain | Cost / risk |
|---|---|---|---|
| **A. Campaign-4 on Dataset v1.2** | repeat the discovery chain with a fixed T-B (§2) and generator feedback on TS (§3) | fast; reuses all Campaign-3 code | the power problem (L1) is unchanged. The joint probability of a confirmed claim at a true SR of 1.0 is about 0.25, and about 0.04 at SR 0.5 (P5 §2, upper bounds). A second null is the most likely outcome and would tell us little |
| **B. Dataset v2 first** (survivorship-free, about 1998+; Sharadar preferred, Norgate fallback) | vendor verification → Dataset v2 contract → rebuild the D-chain → Campaign-4 on v2 | IS of about 15 years; SR 0.5 becomes detectable at about 50–80% power with m ≤ 3. Fundamentals open genuinely new hypothesis families | paid subscription; licence review; a large engineering programme (several phases); vendor facts are unverified |
| **C. Hybrid (recommended)** | do the Path-B vendor verification **now** (document-only), run the engineering wave E (§4) in parallel, and **defer** the Campaign-4 precommitment until the S1-B decision gate | it spends no reserve and no clean development data on an underpowered run; it keeps Path A open if Path B fails verification | Campaign-4 starts later |

**Planner recommendation: C.** The reserve and the DEVELOPMENT_SEALED fold are the
project's only clean data on v1.2. Spending them on a run whose own power table predicts
a null is a poor trade.

**Note for Path B:** a Dataset v2 covering 1998 onward also covers 2023-07-03 … C. The v2
contract must keep that window **sealed as the reserve**. If v2 data for those dates were
generator-visible, the v1 reserve would be contaminated, and so would every later v1
confirmation.

## 2. T-B repair (needed by Path A; designed now, frozen only if A is chosen)

**Preliminary finding** (planner read of sealed code, 2026-10-14; it must be re-verified
at freeze):
- **D7b refusals:** `delisting_bridge._domain_reason` refuses a fold if:
  - (a) its role is `holdout`;
  - (b) the partition has no holdout fold;
  - (c) it overlaps the holdout fold;
  - (d) its end is after `DEVELOPMENT_END` = 2023-06-30.
- **Phase-7 `Partition` rules:** the holdout fold must be the final fold, must be
  non-empty and must be calendar-aligned (`evaluation/partition.py`).
- **Candidate repair (R1):**
  - a **new** partition with IS unchanged, `[2016-01-29, 2020-07-31)`;
  - one OOS fold `[2020-07-31, 2023-06-30)` (= old OOS + DEVELOPMENT_SEALED, minus the
    final formation);
  - a structural stub holdout fold `[2023-06-30, 2023-07-03)` that is never evaluated.
  - T-B would then have about 35 formations, minus the labels purged at the fold
    boundary. The exact count is computed at freeze.
- **What must be verified before freezing R1:**
  1. the new `partition_id` does not break any pinned P-Q / K-PIT binding (P-Q v2.3/v2.4
     authorities name a "default development fold");
  2. the label-purge rule at the new OOS end (formation 2023-05-31; the 2023-06-30 labels
     cross into the reserve);
  3. the stub holdout is accepted by every consumer, not just D7b and `Partition`;
  4. T-B power at the actual T, using `EWC_T23_ONE_SIDED` (ν = round(T^{2/3})).
- **The Campaign-1 OOS read is disclosed** in any T-B result. The T-B stays a selection
  floor with no claims (P5 D8(b) logic).
- **Alternative (R2):** drop T-B again and select on IS only. This is simpler and needs
  no partition change, but loses the floor that protects the single reserve use.

## 3. Generator and evaluation design inputs (Path A or B)

1. **TS in the feedback.** The generator already sees IS results. It should also see
   each candidate's per-scenario IS t (T0/TS × K0/K1), not only the summary. IS is the
   generator-visible tier, so this changes nothing about exposure.
2. **TS diagnostic, before any rule change.** Before Campaign-4 reconsiders the
   all-scenario confirmation rule (P7 decision 4), run an **IS-only structural
   diagnostic**:
   - the count of INCOMPLETE terminal legs per period and per leg;
   - the share of the TS–T0 gap they explain.

   **Constraint:** any change to the TS gating must be justified on methodological
   grounds (what TS is meant to bound) and disclosed as *informed by Campaign-3 IS
   evidence*. Loosening a confirmation rule after seeing that it fails is exactly the
   outcome bias this project guards against. **Proposed default: keep the rule.**
3. **Fewer, stronger hypotheses.** Power favours m = 1 at T-C. It also favours a
   generator budget concentrated on families with economic priors. N stays about 100,
   because the search size does not hurt T-C error control. It only hurts the
   probability that the top-1 is real.
4. **Forward-tier option** (independent of A/B): register Campaign-3's forwarded
   hypothesis `ed358470` (and/or a Campaign-4 pick) now, for a **post-C forward test**
   on sessions after 2026-08-31. It costs nothing but time. T ≥ 24 needs data to about
   2028-09. This is genuinely out-of-sample, but it needs a Dataset v1.x forward-data
   extension contract.

## 4. Engineering wave E (useful on every path; Pi via Herdr per CLAUDE.md §2)

| Task | Content | Owned files (tentative) |
|---|---|---|
| E1 | `campaign3_forward` and the cold-resume path expose `--screen-workers` | forwarding CLI + its tests |
| E2 | persist panel and claim content hashes with the T-A exports, so verification needs no panel rebuild (schema-additive; old exports still verify via rebuild) | export writer, verifier, tests |
| E3 | vectorize `build_event_inputs`. Equivalence is bit-identical to the reference on the Campaign-3 specs, the same standard as C3-L-R6 | event-input builder, tests |
| E4 | suite-time budget: mark the slow real-data tests and split the barrier run, or raise the bound with your explicit approval. **Never by weakening tests** | `pyproject`/conftest, tests |
| E5 | cross-architecture equality test (laptop vs desktop) as a gate for desktop runs | new test + harness |

- **Constraint:** none of these touches a pinned P-Q file or a sealed phase. If one turns
  out to need that, it stops (CLAUDE.md §5).

## 5. Decisions needed from you

| ID | Decision | Recommendation |
|---|---|---|
| **S1** | path A / B / C | **C** |
| S2 | authorize the Path-B vendor verification: documentation and licence review only (Sharadar non-professional licence; delisting-return availability; point-in-time fundamentals; the `8bcc151` lineage criteria). No account, purchase or API call | yes |
| S3 | write the frozen spec for engineering wave E (E1–E5), then launch it after your separate authorization | yes; E4 needs your bound decision |
| S4 | the forward-tier option (§3.4): draft a forward-data extension contract now, or later | later; after S2 |
| S5 | what to do with `docs/campaign3_discovery_program_plan_draft.md` (untracked; superseded by P4–P7): commit it as a historical record, or delete it | commit as history |

## 6. Immediate next steps (if S1 = C)

1. Vendor verification note (document-only), extending `dataset_v2_history_feasibility.md`
   §6.2. It stops at the S1-B decision gate.
2. The wave-E frozen spec (`worker_tasks/campaign4_e/…`). It stops at the freeze for your
   review.
3. The §3.2 TS diagnostic design (IS-only). It stops before any execution.

## Decisions (user, 2026-10-14: "S1=C，其余按建议")

| ID | Decision |
|---|---|
| S1 | **C (hybrid).** No Campaign-4 precommitment before the S1-B decision gate; the reserve and the DEVELOPMENT_SEALED fold stay untouched |
| S2 | Path-B vendor verification authorized: **documentation and licence review only** (no account, purchase, API call or data download) |
| S3 | Write the wave-E frozen spec; launch needs a separate authorization. E4 default: split the barrier run **without** weakening any test and **without** raising the 900 s bound (a bound change would need its own explicit approval) |
| S4 | The forward-tier contract is deferred until after S2 |
| S5 | `docs/campaign3_discovery_program_plan_draft.md` is committed as a historical record (superseded by P4–P7) |
