# Dataset v1.3 — implementation plan (SPECIFICATION FROZEN 2026-10-08; W1–W3 batch-authorized)

- **Status:** FROZEN by user approval ("按照你的建议", 2026-10-08).
  - W1–W3 are authorized; each commit is reviewed at its exact SHA.
  - E-1L and ADM-13 are planner barriers.
  - **Q1 is approved** (§8): it amends V§3 IM-P and binds E-2 and ADM-13.
- **Design authority:** `docs/discovery_dataset_v1_3_edgar_events_design.md`
  (`c9daa54bddcd84c40621c97bf002408cf30d1455bd970659ec09acbc7eb9d269`), "V§n" below.
- **M1b:** `docs/discovery_dataset_v1_3_m1b_event_sensitivity_draft.md` is **DEFERRED** by user decision. It will be frozen after ADM-13, together with the first event hypothesis (P-Q v2.3).
  The worker tasks below do **not** depend on M1b. E-4 records the facts that M1b
  consumes.
- **Base:** the v1 contract conventions (§13 provenance, §14 storage, §15 failure
  handling, §12 reserve), `reserve_firewall`, the D3/D7c trusted session calendar, and
  D4 identity.

## 1. Tasks, ownership, dependencies

| Task | Owner | Branch | Owned files (new only) | Depends on |
|---|---|---|---|---|
| **E-1** EDGAR fetch software | Pi | `v13/task-e1-fetch` | `smart_beta/dataset_v1/edgar_fetch.py`, `tests/test_edgar_fetch.py`, `tests/fixtures/edgar/fetch/**` | — |
| **E-1L** live acquisition run | **planner** (user-approved deviation V freeze decision 2) | — | writes `data_store/discovery_dataset_v1/raw/edgar-<id>/` only | E-1 merged |
| **E-2** identity map + IM-P audit | Pi | `v13/task-e2-identity` | `smart_beta/dataset_v1/edgar_identity.py`, `tests/test_edgar_identity.py`, `tests/fixtures/edgar/identity/**` | E-1 (format), not E-1L |
| **E-3** event normalization + timing + firewall split | Pi | `v13/task-e3-events` | `smart_beta/dataset_v1/edgar_events.py`, `tests/test_edgar_events.py`, `tests/fixtures/edgar/events/**` | E-1 (format) |
| **E-4** integration, gates, manifest, deterministic build | Pi | `v13/task-e4-build` | `smart_beta/dataset_v1/edgar_build.py`, `tests/test_edgar_build.py`, `tests/fixtures/edgar/build/**` | E-2, E-3 |
| **ADM-13** production build + admission review | planner | — | evidence document only | E-4 merged, E-1L done |

- **Waves:** W1 = E-1; W2 = E-2 ∥ E-3 (disjoint files); W3 = E-4.
- **Batching:** W1–W3 are batch-authorized by the approval of this plan. Each commit is
  reviewed at its exact SHA.
- **Forbidden to all workers:**
  - every existing `smart_beta/**` file (new modules only; existing modules are imported
    read-only);
  - `data_store/**` (synthetic fixtures only);
  - the network: E-1's transport is injected and tests use a fake.

## 2. Security (binding)

- **SEC contact:**
  - read only from `SEC_CONTACT` at process start (the gate-6 pattern);
  - never logged, written to a manifest or committed; tests assert that it is absent
    from every written byte;
  - for E-1L, the planner passes it per process.
- **Tiingo names** (for E-2's ended route) are fetched in E-1L via the existing Tiingo
  client pattern. The key is referenced only in-process and never printed. Names are
  stored only in `raw/edgar-<id>/tiingo_names.json`, as an internal artifact (Tiingo
  disposition: no redistribution).
- **Credential scrubbing:** every worker test run uses the scrubbed environment (the
  eight credentials) and the network guard.

## 3. Interfaces (frozen at plan approval)

- **E-1 raw layout** (`raw/edgar-<acq_id>/`):
  - `submissions/CIK##########.json` and its paginated `files/*.json`, byte-exact;
  - `company_tickers.json`;
  - `cik-lookup-data.txt`;
  - `tiingo_names.json`;
  - `fetch_log.jsonl` (url, retrieval_utc, http_status, sha256, bytes; no headers);
  - `manifest.json` (acq_id = SHA-256 of the canonical sorted (path, sha256) list).
- **E-1 candidate CIK set:**
  - the union of current-ticker matches for all D4 keys ever in TOP_1000 during
    development;
  - plus name-route candidates (≤ 5 per key) from `cik-lookup-data`;
  - plus `formerNames` follow-ups.

  This is computed offline by E-1 code from D4 + D7a membership + the two SEC reference
  files. E-1L fetches only that set. The request budget is ≤ 20,000 at ≤ 8 requests/s.
- **E-2 output** `identity_map.parquet`: `security_key`, `cik`, `status` (V§3 enum),
  `route`, `interval_start`, `interval_end`, `n_verified_candidates`, `audit_verdict`
  (CONFIRMED / CONTRADICTED / NO_SYMBOL_EVIDENCE / NOT_SAMPLED).
- **E-3 output** `events.parquet`: `security_key`, `cik`, `accession`, `form`,
  `event_type`, `acceptance_utc`, `acceptance_et`, `k_session`, `boundary_flag` (B_e per
  M1b §3, recorded as a fact), `report_period`. Rows with `k_session` ≥ 2023-07-03 go to
  `reserve_sealed/edgar-<id>/` only.
- **E-4:** `edgar_build.build(root, acq_id) -> manifest`. It runs the gates IM-P, EV-C and
  EV-T (V§3–§4) and returns a deterministic manifest (`ev_id`, gate results, counts).
  The EV-T computation reads development returns through the existing D7c accessor, with
  no factor or long-short computation.

## 4. Required tests (owners)

| Area | Requirements | Owner |
|---|---|---|
| Fetcher | rate cap; retry policy (429 / 5xx / timeout only); request cap; resume by SHA-256; contact required, else 0 requests; the contact absent from all output | E-1 |
| Routes | ticker route; name route with the `formerNames` interval overlap; verification (exactly one candidate with periodic filings in the interval); all five statuses; ticker reuse (the D4 B-R1 cases); unmapped keys retained | E-2 |
| IM-P | the seeded sample of 200 (100/100); the symbol-evidence comparison; the one-sided 95% Clopper–Pearson UB | E-2 |
| Timing | 15:59:59 vs 16:00:00 ET; DST transitions; weekend/holiday → the next session; early-close sessions; `8-K/A` excluded; `filingDate` never used | E-3 |
| Event types | the Item 2.02 parse from the `items` string; the fallback rule window; FPI typing | E-3 |
| Firewall | K ≥ 2023-07-03 → reserve only; the development loader fails closed; file-open audit with 0 reserve reads in development paths | E-3, E-4 |
| Gates and determinism | EV-C, EV-T; deterministic rebuild with two `PYTHONHASHSEED`s; manifest hash stability | E-4 |

## 5. Mutations (each killed)

| ID | Mutation | Owner |
|---|---|---|
| E1-M1 | the contact written to the fetch log | E-1 |
| E1-M2 | the rate cap removed | E-1 |
| E1-M3 | 404 retried | E-1 |
| E1-M4 | a request issued without a contact | E-1 |
| E2-M1 | an ambiguous key mapped to the first candidate | E-2 |
| E2-M2 | an unmapped key dropped | E-2 |
| E2-M3 | the name route ignores the interval | E-2 |
| E2-M4 | the IM-P UB uses the normal approximation | E-2 |
| E3-M1 | `filingDate` used for K | E-3 |
| E3-M2 | 16:00:00 treated as same-session | E-3 |
| E3-M3 | `8-K/A` creates an event | E-3 |
| E3-M4 | a reserve row written to the development path | E-3 |
| E3-M5 | the fallback merged into `EA_8K202` | E-3 |
| E4-M1 | a gate failure ignored | E-4 |
| E4-M2 | nondeterministic row order | E-4 |
| E4-M3 | EV-T reads the reserve | E-4 |

## 6. Live run and admission (planner)

- **E-1L** has **two phases.** L2 depends on E-2's sample selection.
  - **L1:** reference files plus submissions.
  - **L2:** symbol evidence for the IM-P sample. For each of the 200 sampled keys: the
    filing index JSON plus the primary document of ≤ 2 periodic filings inside the
    interval, to read the cover-page `dei:TradingSymbol`; ≤ 1,000 requests.
- **E-1L runs, per phase:**
  - under the scrubbed environment except `TIINGO_API_KEY`, plus `SEC_CONTACT` per
    process, network allowed;
  - record the acq_id, request counts and failures;
  - **before any later step**, verify that no file in `raw/edgar-*` contains the contact
    string (grep). Disclose the result.
- **ADM-13:**
  - run the E-4 production build under the network guard;
  - check the gate results against the V thresholds;
  - check the firewall audit, the determinism and the identity bindings;
  - write `docs/discovery_dataset_v1_3_admission.md`.
- **Admission scope:** development use only.
- **K-PIT ARTIFACT registration** of v1.3 needs its own authorization.

## 7. Stop conditions

- the SEC response format differs from the V§2 field list (e.g. `acceptanceDateTime`
  missing);
- the candidate set exceeds the request budget;
- any IM-P / EV-C / EV-T gate fails. Record it as FAIL; never retune thresholds after
  seeing data;
- a reserve row is seen by a development path;
- the contact string is found in any output;
- a worker needs an existing module changed.

## 8. IM-P evidence-sufficiency rule (Q1 — APPROVED 2026-10-08; binding amendment to V§3)

**Q1 — the IM-P evidence gap.**
- **Why there is a gap:** cover-page `dei:TradingSymbol` tagging became mandatory only
  around mid-2019. Ended keys whose listing ended before then will mostly be
  NO_SYMBOL_EVIDENCE.
- **Why it matters:** the frozen IM-P gate bounds only the CONTRADICTED share, so a
  sample that is mostly NO_SYMBOL_EVIDENCE could "pass" on weak evidence.
- **Binding rule** (fixed before any data is seen):
  - IM-P is evaluated on the keys **with** symbol evidence;
  - if fewer than 120 of the 200 sampled keys (or fewer than 50 of the 100 ended keys)
    have evidence, IM-P = **NOT CERTIFIED (insufficient evidence)**, not PASS;
  - in that case v1.3 may still be admitted, but with mapping precision explicitly
    NOT CERTIFIED for the ended stratum, carried as a binding non-claim. Any EA
    hypothesis would then have to report results with and without ended keys.
