# Dataset v1.2 — P-Q v2.5 (terminal-delisting scenario TD) Campaign-4 admission evidence (PQ25-ADM)

- **Status:** planner admission evidence, authorized by the user on 2026-10-15 ("好的", on
  the recommendation "先批 v2.5 准入"). Erratum E1.3 deferred admission to this separate step.
- **Software bound:** master `bf8a8a5`. This includes PQ25-A/G/T/H/X (barrier `2d9604a`:
  6865 passed / 1 skipped) and C4E-5 (barrier `960fa55`: 6872 / 1). Every commit after
  `2d9604a` is documentation or the probe only; no v2.5 module changed.
- **Precedent:** the v2.3 admission (`docs/discovery_dataset_v1_2_pq_v2_3_admission.md`),
  items ADM-1…6.

## Identities

| Item | Value |
|---|---|
| Methodology (design, frozen) | `5af8c4bffed24062977b0442cfeaff388e2042eedd173f5300893b45252c4460` |
| ED-ID v2.5 / CE-ID v2.5 | `cfd76733…f4ffe2` / `882725da…bf82` |
| Erratum E1 | `62d9bf32fe0ed57830ae1c469dcdf30c18d350b5d4331315c5779875895fdc2a` |
| d (TD_D) | −0.55 (M1-d B1) |
| C3-bound file table | `worker_tasks/pq_v2_5/c3_bound_sha256.json` (22 files, verified against K-PIT seq 46) |

## Results

**ADM-1 — Authority: PASS.** `verify_authority_v2_5()` passes on `bf8a8a5`. This covers:
- the design and erratum hashes;
- `AUTHORITY_V2_5`;
- the 22 C3-bound file hashes.

The frozen v2.3 chain is unchanged, because `pqv2_core` is not modified.

**ADM-2 — Real-data correctness and determinism: PASS**
- **Equivalence** (PQ25-X, `docs/discovery_dataset_v1_2_pq_v2_5_equivalence_result.md`):
  E1/E2/E3 hold bitwise for 11 real IS factors and 52 TD cells, with 0 E4 violations.
- **Determinism** (this step):
  - **Factors:** KF-REV1 (non-event) and C3-IS-1 (EVENT_SIMPLE, via persisted C3
    provenance).
  - **Runs:** two independent runs, each with its own K-PIT copy and run root.
  - **Result:** the two reports are byte-identical (file SHA-256 `80e3c9ca…9ab9e3`;
    content_sha256 `6466b98c…52296`), and both give PASS.

**ADM-3 — Firewall read audit: PASS**
- **Method:** an audit-hook wrapper records every `open` in the run process (scratch
  `adm25/audit_run.py`; it runs the frozen script unchanged).
- **Volume:** 1,797 distinct paths per run, the same in both runs.
- **Clean:** **0** under `reserve_sealed/`, `raw/`, `dsc_evidence` or a holdout.
- **Data reads:**
  - `discovery_dataset_v1/derived` (25 paths) and `normalized` (3 paths);
  - the one C3 provenance file (`76a2c212…/diagnostics/b7857381….provenance.json`).
- **Disclosed:** the production `knowledge_pit/knowledge.jsonl` is opened by the frozen
  dataset identity check (`quarantine.py:1555`, which defaults to
  `<root>/../knowledge_pit/knowledge.jsonl` to verify the D8 genesis record). It is
  **read-only**, and every earlier dataset-loading run did the same. Its SHA-256 was
  unchanged before and after (ADM-6).

**ADM-4 — Scope of certification: PASS (unchanged)**
- v2.5 adds a scenario. It adds no grammar.
- Certification classes are the frozen v2.3 ones; `certify_candidate` is untouched.

**ADM-5 — Resources: recorded**
- One factor × (1 frozen + 3 v2.5 builds) ≈ 23 min on the laptop. The full 11-factor
  PQ25-X took 15,374 s.
- For Campaign-4 ranking, one build at d = −0.55 per candidate (the frozen base is carried
  inside it).

**ADM-6 — Immutability: PASS**
- Production K-PIT `29f0cabcfa587f18cc5667ffa006371bc7285dccb663ffefd9f4b993693a35fe`
  before and after every real run (PQ25-X and both ADM runs).
- 0 K-PIT writes, 0 attempts, 0 slots.
- C3 artifacts are read-only.

## Scope of the admission

- **Admitted:** P-Q v2.5 at d = −0.55, as the **Campaign-4** scenario authority. It is
  admitted for IS development use (ranking, M1-e walk-forward) and, through the Campaign-4
  confirmation wrapper once that is built and frozen, for confirmation. The rule follows
  M1-d B1: TD is used for ranking and confirmation; TS and B2 are reported only.
- **Enforcement:** `pqv2_core.require_admitted_authority` is unchanged and does not resolve
  v2.5 (erratum E1). The Campaign-4 wrappers must check `verify_authority_v2_5()` and this
  admission themselves.
- **Not affected:** every Campaign-3 record keeps its own frozen rules.

## Non-claims

- **Assumption:** d = −0.55 is an assumption, calibrated adversarially on Shumway & Warther
  (1999). It is not an estimate of this universe's realized delisting returns.
- **Usefulness:** admission certifies correct and deterministic computation. It does not
  certify that any factor is useful. Under TD, no M0 or C3 factor has IS EWC t > 1.0.
- **Folds:** no reserve, DEVELOPMENT_SEALED or OOS fold was read.

## Disposition

**P-Q v2.5 PQ25-ADM PASS — ADMITTED AS THE CAMPAIGN-4 TERMINAL-STRESS AUTHORITY (d = −0.55; enforcement in the Campaign-4 wrappers)**
