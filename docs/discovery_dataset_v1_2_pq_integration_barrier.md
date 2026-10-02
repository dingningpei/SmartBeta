# Discovery Dataset v1.2 — P-Q Integration Barrier (I1–I20) — Evidence Record (DATA-FREE)

- **Result:** **P-Q INTEGRATION BARRIER PASS — DATASET v1.2 ADMISSION NOT YET RUN.**
- **Authorization:** the user's "P-Q INTEGRATION BARRIER — I1–I20 AUTHORIZED".
- **Master evaluated:** `c4125b72616da8f25429e184eaff2ccb67079dde`. This is after
  two barrier-driven repair cycles, both authorized:
  - **I2 stop → erratum E2** (complete, fail-closed parent bindings), merged `fac5266`;
  - **I5 stop → erratum E3** (post-load reserve-assertion scope), merged `c4125b7`.
- **What a PASS means:** the P-Q implementation is integrated and structurally
  validated for the authorized development artifacts. **It does not mean** Dataset
  v1.2 is admitted, Gate 6 passed, alpha or factor evidence exists, or Campaign-1 or
  confirmation is authorized.
- **Content:** this record holds hashes, identifiers and structural counts only. It
  contains no factor values, returns, IC, Sharpe, t-statistics, screen verdicts or
  any performance quantity.

## Bound implementation and authority (I1)

| Item | SHA-256 / commit |
|---|---|
| `docs/discovery_dataset_v1_2_pq_amendment.md` (Dataset-v1.2 authority) | `9261a55b9b82a5708dded65bfd3083d5a960cfa4375681b1b23390da62fb3ed0` |
| `docs/discovery_dataset_v1_2_pq_quarantine_rules.md` (`quarantine_rule_hash`) | `df5bec69103eca5ad9196b3342e5013fab30a3b85cdb3a5332b033dcac5db886` |
| `worker_tasks/dataset_v1/dataset-v1-2-pq-plan.md` | `a196d4f31822a1909f590947d97ad038e5fe70f74064a3b6814ad2643067a0a1` |
| PQA spec / E2 / E3 | `92e175f568b7c9c2257418cf0ad294cf21c079dfb9e9628ed3be3cbbf9743f44` / `d440ac098f6c4850110e456ea8649117ee25b7f570831c2e0f1298a42762ee9d` / `66322fbedc67a5f7ea44d62491db391e42c200e6b946dfb413c191693f9f74ff` |
| PQB spec | `d49110ee2d314c4e129a94ee42a07a0d99d609ab048763e35728e386f01e8cb7` |
| PQC spec / E1 | `820f8d45d44c8cd69e353dc88b3d8fa93704cbf9b5b13fbd0cbc1e440217dfef` / `c5ab26b92bd749f076c7f7e1fac83b1b1bff48fc358df63164de7a14514d475d` |
| `smart_beta/dataset_v1/quarantine.py` | `3d3bff02294b452d797c262f8232a4abaa51d55d05b521f2635156dec3323a1c` (worker commits 41b820c, 4332574, 5ba64e6, b097914) |
| `smart_beta/dataset_v1/pq_evaluation.py` | `8ec4d18cf863baa5730d4719c856ed06628b3fbf25f43d0d1781ef0766a0bdbd` (worker commits 71e01ce, d3537f6, f26a966, b6c7e9a); pins `quarantine.py` = `3d3bff02…3a1c` |
| `smart_beta/engines/gap_hac.py` | `d0dd6298aabfeb82e0ce7d7ed62937616cc47e5a40e4a2e38bfea0ea9ee85424` (worker commit d157521) |

## Production development overlay (I5, I6) — INTEGRATION EVIDENCE ARTIFACT

This artifact is **not** an admitted, certified or Campaign-ready dataset.

| Item | Value |
|---|---|
| Location (outside Git) | `data_store/discovery_dataset_v1/derived/quarantine/pq-59c3bd7abe08eef5/` |
| `pq_id` | `pq-59c3bd7abe08eef5` |
| `pq_hash` | `baa920ddfc52805c70445abfb8650e2969d341ae9383e1b1a0c6a788fcc95f2c` |
| `identity_hash` | `a065493d295883f425d499816a98ac55a03cd67a1086bd7b64249b90fe76b9da` |
| `quarantine_rule_hash` | `df5bec69103eca5ad9196b3342e5013fab30a3b85cdb3a5332b033dcac5db886` |
| `pq.json` | `a8846ff23f26dc79b0cd3f4e1245d9864999b66b7080aab47346d615ee320aab` |
| `flags.parquet` | `116d388f7ce8eb32daa2c806ffa3fd27e1de03fc570de8bdabaf72a771249c19` |
| `primitive_states.parquet` | `fa87161a51d4fcb3dc67263a550d9a9060359154dcd4c2d582fb7792b8cf11a0` |
| `input_states.parquet` | `17c5dca83e983f8dc0e19cd5c37f2ba64a60d52f22963662512f24d5dcdafa98` |
| `label_states.parquet` | `ca245317c3bc3faa69f61b48724db9ddf0cfbee7dd4e7fe4f2010f7d6a6252e6` |
| `liquidity_exposure.parquet` | `95f52147edc6adc8801c8897d7aa6120ad717eb705e0fa24f6a3bd1f6af52c1d` |
| `dataset_identity.json` | `95ed102ae81ddcf32ff8ca99e2e939edd2377ee4ff240883904eb1d613f2b550` |
| Domain | development only. Flags ≤ 2023-06-30; no session after 2023-06-30 is used as a data key |
| Build | 209 s, peak resident memory 4.1 GB |

- **Build 1 vs build 2:** build 2 ran from scratch under `TZ=Asia/Tokyo LC_ALL=C`.
  **All 7 files are byte-identical** (same `pq_hash`).
- **Parent artifacts (I2), all verified before any read:** each stage's manifest-file
  SHA-256, manifest hash field and data-file SHA-256 were checked per E2.2:
  - D5 `q-5d1028655f8c82e1` (`qc_hash` `3d63e51b…dfc3`);
  - D4 `i-a21f1e46621dee94` (`identity_hash` `1023dbca…e730`);
  - D7c `ti-aa23fabd3db4e595` (`ti_hash` `30ee0c51…c2ae6`);
  - D7a `r-34565f074d64ccda` (`returns_hash` `4c487e47…4861`);
  - D6 `u-6150bc6ac2866b7f` (`universe_hash` `6e8539b7…ed87`).
  - Inherited, identity-bound: D3 `n-f874d3ecb61374d4`, D-SC `c-27b6dabd0db0cb15`.

## Structural counts and independent reconciliation (I7–I11, I14)

Every count below was reproduced exactly by a planner-independent vectorized
derivation from the parent artifacts. That derivation does not use the PQA logic.

| Check | Overlay | Independent | Historical | Result |
|---|---|---|---|---|
| I7 development suspect days | 17,469 (2,674 keys) | 17,469; identical (key, date) set | 17,469 | **reconciled** |
| I8 suspect rows in member daily-return paths | — | 110 (72 keys); equals the D5 flags inside member paths | 110 | **reconciled** |
| I8 affected top-500 forward labels | — | 1,700 of 2,772,000 (20 keys) | 1,700 / 2,772,000 | **reconciled** |
| I10 `daily_total_return` quarantined primitives (state 1) | 7,560 | 7,560, set-equal | — | PASS |
| I10 `dollar_volume` quarantined primitives (state 1) | 5,193 | 5,193, set-equal | — | PASS |
| I10 lagged input-state rows (`daily_total_return` / `dollar_volume` / `total_return_index`) | 7,560 / 5,193 / 651,408 | identical | — | PASS |
| I11 TRI propagated cells (state 2) | 651,408 over 1,019 admitted segments | 651,408, set-equal | — | PASS; **0** recovery violations |
| I14 quarantined labels (state 2) | 10,971: flag in path 7,761; endpoint/formation close 224; reserve boundary 2,986 | 10,971, set-equal | — | PASS; 126,000 reserve-crossing labels excluded (`RESERVE_PURGE`) |
| Liquidity exposure (report-only) | 331 of 90,000 member rows | — | — | membership unchanged |

- **Flag panel:** 17,469 flags have prev; 17,367 have next; 17 have next beyond the
  development window, so no next is used.
- **I9:** sparse files store only states {1, 2} (labels {2}). Absent present cells are
  CLEAN; absent non-present cells are NOT_APPLICABLE. No quarantine state overwrote
  NOT_APPLICABLE, and there is no conversion to generic missingness.

## Factor-input calculus on the real grid (I12, I13)

- **Method:** the frozen probe set was evaluated **only** to obtain dependency/NaN
  patterns. No value is reported, joined to returns, grouped or scored.
- **Agreement:** NaN-shadow vs syntax-tree oracle **agree cell by cell** on
  22,134,714 cells for every probe.

| Probe | QUARANTINE_PROPAGATED cells |
|---|---|
| `daily_total_return` | 7,560 |
| `lag(daily_total_return, 5)` | 7,531 |
| `rolling_mean(dollar_volume, 63)` | 112,634 |
| `total_return_index / lag(total_return_index, 21)` | 650,945 |
| `rank(rolling_std(daily_total_return, 20))` | 45,804 (= own-cell state of `rolling_std`, 45,804) |

- **I13:** lag-lost states = 0. The cross-sectional own-cell rule holds exactly. There
  is no third estimand.

## Recorded observations (not I-criterion failures; for the admission review)

- **O1 — the `residual_cs_cells` diagnostic over-counts (PQA reporting accuracy).**
  - The counter counts every finite cell whose value differs between V and the shadow
    V′, including floating-point drift from pandas' sliding-window accumulators in
    **time-series** rolling ops. Example: `rolling_mean`, 97,691 cells, maximum
    relative difference 5.7e-13.
  - The PQA spec defines the counter as changes due to cross-sectional ops only.
  - States are unaffected, and the counter enters no decision. Q12 reporting would
    overstate residual cross-sectional influence.
  - **Recommended:** a narrow repair before any Campaign reporting.
- **O2 — a numerical channel in the pinned Phase-6 rolling variance.**
  - For `rolling_std`, 967 cells differ by more than 1e-12 absolute. The maximum
    absolute difference is 6.5e-7, on windows whose true standard deviation is about 0.
    Round-off in the accumulator from earlier, mathematically excluded (quarantined)
    observations carries forward.
  - This is a property of the frozen Phase-6 engine, not of the P-Q structural
    dependency model. The model is exact for the stated Q3.3 rules.
  - It can only matter at exact ties near zero, and it affects A and B identically.
  - **Proposed:** record it as a nonclaim at admission. No Phase-6 change is
    authorized.

## Firewalls (I3, I4) and anti-substitution / inference semantics (I15–I18)

- **I3:**
  - The file-open audit covered both builds, the probe run and the identity check.
  - Only bound development artifacts and the produced overlay were opened.
  - **0** opens under `reserve_sealed`, `raw` or `dsc_evidence`; **0** reserve
    content reads.
  - The K-log was opened in mode `r` only, and its file hash was unchanged before and
    after.
- **I4:**
  - Every child process scrubbed the full credential set process-locally.
  - The network-denial guard recorded **0** attempts.
  - Provider / data API calls: 0.
- **I15–I18 (fixtures, no real-data evaluation):**
  - PQB item tests pass, covering A ≡ B bitwise, no drop / renormalization / cash /
    zero substitution / regrouping, IC period-level with no pairwise deletion or
    re-ranking, cost composition at t / t−1, CS-3, κ = 2, NOT_ASSESSED blocking,
    DELISTING coexistence and holdout refusal.
  - Planner-re-run mutations (exact merged code), each with a passing no-op control:
    - PQB 42 / 42 killed;
    - PQA 60 / 60 killed;
    - PQC 18 / 18 non-equivalent killed, with G8 and G10 equivalent per E1.
  - PQC tests cover original-index lags, no compression or fallback, the frozen L,
    typed statuses and E1.
  - `MAX_DRAWDOWN` is NOT_ASSESSED across interior gaps. Other path metrics follow
    rules Q9.4.

## I19 — R3 identity

- `verify_dataset_v1_2_identity(root)` on the real bindings returns
  `a065493d…b9da`, equal to the overlay's `identity_hash`.
- **Bound:** `discovery_dataset_v1_2` ← parent `discovery_dataset_v1_1`; amendment
  `9261a55b…3ed0`; `quarantine_rule_hash` `df5bec69…b886`; inherited artifact ids and
  hashes as above.
- **D8:** genesis seq 0 `984d7bec4fbb159b3eba3385b2a0af24a54c2c707478af91b5ba461684da4eea`,
  `source_label` `discovery_dataset_v1:reserve:n-f874d3ecb61374d4`,
  `INHERITED_NOT_REREGISTERED`.
- **Knowledge-PIT:** v1.2 records `NONE_AUTHORIZED`. The K-log is READ ONLY, and
  nothing was appended.

## I20 — aggregate

- **Scoped integration suite:** 371 passed (PQA, PQB, PQC, D7b, D7c).
- **Full suite on `c4125b7`:** 4,923 passed, 1 skipped, credential-scrubbed and
  network-denied.
- **No unexpected regressions.**

## Claim boundary and status

- Gate-6 v1 = **INCONCLUSIVE**. Gate-6 data quality = **NOT_ASSESSED**.
- G6-v2B = **OPEN — OPERATIONALLY PAUSED**.
- Dataset-v1.2 development admission = **NOT RUN** (separate authorization required).
- Campaign-1 = **NOT STARTED**.
- **Zero counts:**
  - real factor evaluations: 0;
  - real performance metrics: 0;
  - screen verdicts computed: 0;
  - reserve reads: 0;
  - final-holdout consumption: 0;
  - provider / network calls: 0;
  - Knowledge-PIT appends: 0.
