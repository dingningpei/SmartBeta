# PQA — P-Q quarantine overlay, state calculus and Dataset-v1.2 identity (R3) — FROZEN BEFORE IMPLEMENTATION

- **Status:** FROZEN BEFORE IMPLEMENTATION. Implementation requires a separate
  launch authorization. Master plan: `worker_tasks/dataset_v1/dataset-v1-2-pq-plan.md`.
- **Authority, in precedence order** (on any conflict, stop and report):
  1. `docs/discovery_dataset_v1_2_pq_quarantine_rules.md` §§Q1–Q4, Q12, Q13 —
     `df5bec69103eca5ad9196b3342e5013fab30a3b85cdb3a5332b033dcac5db886`
     (`quarantine_rule_hash`);
  2. `docs/discovery_dataset_v1_2_pq_amendment.md` §§1, 2, 4, 6–8 —
     `9261a55b9b82a5708dded65bfd3083d5a960cfa4375681b1b23390da62fb3ed0`
     (this file's hash is the `discovery_dataset_v1_2` identity hash);
  3. `docs/discovery_dataset_v1_contract.md` §§8, 10, 11.1, 12 —
     `d03f35549777e5a94046f06dd0621acfc3a1dedb7a4b40adc55fdae911fa80e3`;
  4. `docs/discovery_dataset_v1_1_d7c_clarification.md` §§1–6 —
     `508d5f1ac3da3c68233c72b4b31ea75d0ec1d88be9241e6dd018cd54467b4a89`;
     its addendum —
     `75806604cfb10bc9790126c5e833255497c5883c4370490ca593039595aaf05d`;
  5. `docs/discovery_dataset_v1_1_d7_clarification.md` §§5–10 —
     `bc8e6920d31dd429beb62030123a721a7293558ba57fe8e3d4d6cd6368a96946`;
  6. `docs/discovery_dataset_v1_1_d8_clarification.md` —
     `9a3f218d67c7e7456b2a5c9439a2d1fffe202e5beca3299afb31439b919b24c0`.
- **Owned files (new only):**
  - `smart_beta/dataset_v1/quarantine.py`;
  - `tests/test_dataset_v1_2_quarantine.py`;
  - `tests/fixtures/dataset_v1_2/pqa/**`, including `mutations.json`.
- **Must not modify any existing file.** No D3, D4, D5, D-SC, D6, D7a, D7b, D7c,
  D8, Phase-6, Phase-7 or Phase-10 file. `trusted_inputs.py` is untouchable,
  because its `ti_id` depends on its own code hash.
- **Provider or network calls: 0.** **Reserve reads: 0.** **Knowledge-PIT appends:
  0** (read-only verification only, §8).

## 1. Scope

PQA owns three things:
1. **the static quarantine overlay artifact**: suspect source sets, primitive
   states, lagged input-state panels, label states and liquidity exposure;
2. **the factor-input state calculus**: a pure function giving per-(row, key) states
   for any admitted FactorSpec;
3. **the Dataset-v1.2 identity record and verifier** (R3).

PQA computes **no** returns statistic, factor value summary, group assignment,
metric, screen or estimand. It defines no `CampaignEvaluationDeclaration`.

## 2. Inputs (exact bindings; fail closed; never "latest")

| Object | Binding |
|---|---|
| D5 `day_flags.parquet` (development) | `q-5d1028655f8c82e1`, `f4010ebeabf5cfae2684f3658a5f19f922f15e2c8898410049f2148440cf7891`; manifest `qc_report.json` `16c582900b77a328ffaee8fdb78b8bc4b7920a0b48ebbc98f7a2e7e62c769ae1`; `qc_hash` `3d63e51b983096f0ae579cd1e382c613d9329d239c3f848cc785dee2d61cdfc3` |
| D4 `identity_assignment.parquet` | `i-a21f1e46621dee94`, `fee4614009095773f75fb832343a5fa9b87b9de1cf363125567222b08fad3f8b`; manifest `identity.json` `b8ed3d23979f8cbb9f320fae96a8cea968b16332fd1000fa1cdedca15df43aa4` |
| D7c files (`ti-aa23fabd3db4e595`, `ti_hash` `30ee0c51602cf792e8cb7654c2501f52c7d664675dbf78845c903df6683c2ae6`) | `daily_total_return.parquet` `511bcf4ac4ea2bd02158438732e0fdf19c04d783b88ca7a01ff5a364ec8e688c`; `dollar_volume.parquet` `c8a632ef455ecec5f3caa95816ec0bb71789e3ed51b9447765021c697fa27780`; `total_return_index.parquet` `53a48a65a70d16b80374b926b56c50922ebfc521d3f8c1677380343a8ccd5e8e`; `segments.parquet` `4ea4df04c3e581f1750229c255ed1f5c5ec5055ad6fe954483cc7ae5b76cc384`; `trusted_inputs.json` `d8d40099ba978604727a7d316274a2d3e7f8210baf8cb35e4ca687435fd8f8f0`. Loaded for the calculus via `trusted_inputs.load_trusted_inputs` (read-only) |
| D7a files (`r-34565f074d64ccda`, `returns_hash` `4c487e47fd613f3f272109d14b43a27e061f9cc31e5b6d6bcefd4abc97994861`) | `labels.parquet` `262b8b1d445dfb329d33359966a9deba67bd5999ccdc01dd8183eda6f4f2645e`; `members.parquet` `fcb2c9df2918a7bd43399b220f9595fc94d8a5dc49542219698ecafb7c6b4488`; `horizons.parquet` `61a2ac310021f153676445de7f0122ef74f67910e6506a6e5ef1e286f3d1ccbb`; `returns.json` `6093bec9730e5bd0a6e9e1410db165fca148e3be1b01ef2534db12e34370886e` |
| D6 (liquidity report only) | `u-6150bc6ac2866b7f`, `universe_development.parquet` `f933a67ff8c83ad9f6c7937a76136d93419cedb0ec79cec617df8205b3d38d8b`; `universe.json` `8e5e4ecd1729c2bbf75b1ac1bdd5d497f9f2a360061edb407fc323feb433d737`; `universe_hash` `6e8539b7acb4ed4559846234b0b26e6f1d4e6d34f7e0a5d1e2290134d563ed87` |
| Trusted NYSE calendar | `smart_beta/vendors/tiingo/calendar_source.py` `79bca35ddd46f87102e90f6ce0c2d51a8b0d16f1fe9caf56b7674b43fce8e23f` (as used by D7c) |

**Pinned read-only code (SHA-256; fail closed):**

| File | SHA-256 |
|---|---|
| `smart_beta/dataset_v1/trusted_inputs.py` | `c5e30824f8323e0d9a9622f41f7f53861cda02f515a04d562090fd8306b30b74` |
| `smart_beta/dataset_v1/reserve_firewall.py` | `4b8a74b5f950bd3bdf58155bbfae32401753f2bed45d5031860d6edb1d493e15` |
| `smart_beta/spec/engine.py` | `eea3bc30dd50121865009d0bab4999dc76068c1e2fbf9f7c965413c2a443b46a` |
| `smart_beta/spec/evaluator.py` | `aecdeba642ca23f3285b8f55565afb6edaf255322783345e22a50c20c405bcce` |
| `smart_beta/spec/expression.py` | `9b027c5da83f0778d72c236327c09967172a5314d60c47bd54ea6337fe46d6f4` |
| `smart_beta/spec/transforms.py` | `15708149406017a6c970d42aebef7b0e2ea9ed6b0be48182f7769006a9ff5a66` |
| `smart_beta/spec/factor_spec.py` | `ab6c65d312d6fe1e421c489e6ad8045a50e265c8f73dbe6a07738e1bf5458c56` |
| `smart_beta/science/knowledge.py` | `7a4ab759ab20d8acf945a1de3246653a572069696a2f2bf791d155c6b6932fec` |
| `smart_beta/vendors/tiingo/calendar_source.py` | `79bca35ddd46f87102e90f6ce0c2d51a8b0d16f1fe9caf56b7674b43fce8e23f` |

**Bound authority documents (SHA-256 verified at build):** the six above, plus the
rules file and the amendment.

**Guarded resolver:**
- Allowlist only. It raises `ReserveFirewallError` for a `reserve_sealed`, `raw`
  or `dsc_evidence` component, for `..`, for a symlink escape, or for any
  non-allowlisted path.
- **Post-load assertion:** every loaded date is ≤ 2023-06-30.

## 3. Suspect source sets (rules Q1)

- **Flags:** the rows of D5 `day_flags` with `class_or_flag == "CORP_ACTION_SUSPECT"`.
  Exactly 17,469 rows are expected. A count or hash mismatch fails closed.
- **Q(s):** for each flag (K, s), Q(s) = {close_K(prev(s)), close_K(s),
  CA_K(prev(s) → s)}, where prev and next are trusted NYSE sessions **inside K's D4
  interval**.
  - An element whose session lies outside the interval, or does not exist, is
    omitted. There is no value to quarantine.
  - Every flag is applied. None is skipped as irrelevant.
- **Reserve boundary:**
  - A session ≥ 2023-07-03 is never read, materialized or used as a data key.
  - Where a rule needs a flag on a session ≥ 2023-07-03, which is unknowable
    without a reserve read, the dependent state is **`QUARANTINE_PROPAGATED`
    with reason `UNDETERMINABLE_RESERVE_BOUNDARY`** (rules Q2.2).
  - This arises only for labels whose observed end e′ = 2023-06-30 (§6). D7c
    primitives are dated ≤ 2023-06-29, so no input state needs a reserve-dated
    flag.

## 4. Primitive states (rules Q3.1; unlagged, dated by own session u)

State codes (int8 in storage):

| Code | State |
|---|---|
| 0 | `CLEAN` |
| 1 | `QUARANTINED_CA_SUSPECT` |
| 2 | `QUARANTINE_PROPAGATED` |
| 3 | `NOT_APPLICABLE` |

**Rules:**
- `daily_total_return`(K, u) is 1 iff u ∈ {prev(s), s, next(s)} for some flag s of
  K.
- `dollar_volume`(K, u) is 1 iff u ∈ {prev(s), s} for some flag s of K.
- `total_return_index`(K, u), within D7c's admitted segment 1 only (`segments`,
  `admitted == true`), is 2 iff some 1-state `daily_total_return`(K, v) has
  anchor < v ≤ u.
  - TRI(anchor) = 1.0 is never propagated by itself.
  - Non-admitted segments are NaN in D7c, so they are 3.
- **Presence:** a primitive cell absent from D7c (NaN or masked) is 3
  (`NOT_APPLICABLE`) regardless of Q(s). Quarantine never overwrites 3.

## 5. Lagged input-state panels and the factor-input calculus (rules Q3.2–Q3.4)

**Lagged panels:** for each of the three inputs, state rows align **exactly** to
D7c's long rows `(row_date, security_key, source_session)`. The state of a row is
the §4 state of the primitive dated `source_session`, which equals prev(row_date)
by D7c construction (assert it). This is the §11.1 lag applied once.

**`factor_input_states(factor_spec, *, overlay, inputs) -> pandas.DataFrame`** (pure)
returns the long frame `(date, security_key, state)` on the Phase-6 output grid of
`factor_spec`. Normative method (**NaN-shadow**):
1. Evaluate `factor_spec` with the **unmodified** Phase-6 engine
   (`smart_beta.spec.engine.evaluate_factor`) on the D7c `TrustedInput`s → V.
2. Build shadow inputs. Every input cell whose lagged state is 1 or 2 is set to NaN;
   everything else is identical, including the capability metadata. Evaluate again
   → V′.
3. For each output cell:
   - V NaN → 3 (`NOT_APPLICABLE`);
   - V finite and V′ NaN → 2 (`QUARANTINE_PROPAGATED`);
   - V finite and V′ finite → 0 (`CLEAN`), **even if the two values differ**.
     A difference can arise only through a cross-sectional op (`rank`,
     `winsorize`, `standardize`) whose row contains another security's shadowed
     cell. That is the residual cross-sectional influence (rules Q3.3 own-cell
     rule; U3). It is **counted and reported** as `residual_cs_cells` per date,
     never converted to quarantine.
   - The oracle (below) must classify those cells CLEAN as well.

**Why this is exact:** Phase-6 NaN semantics (D7c clarification §1) implement the
structural rules of Q3.3. `lag` shifts NaN; rolling ops require a full window;
arithmetic propagates NaN; `rank`, `winsorize` and `standardize` keep NaN in its own
cell. So the shadow method's propagation is exactly the Q3.3 calculus. It uses the
same FactorSpec lag and grid alignment as the values themselves.

**Independent oracle (tests only):** an AST-walk implementation of Q3.3 over the
closed node set {`Field`, `Literal`, `Add`, `Sub`, `Mul`, `Div`, `Lag`, `Rolling`,
`CrossSectional`}, plus the FactorSpec-level lag.
- `Literal` is 0.
- Any node type outside the set raises `UnsupportedQuarantineNode`.
- It must agree with the NaN-shadow result cell-for-cell on every fixture
  expression.

**Prohibitions (enforced by tests):** the factor values V are never altered or
returned modified; there is no lookback shortening, skipping, filling or
substitution.

## 6. Label states (rules Q4)

For every D7a label row (f, K, h) whose horizon is not reserve-crossing
(`horizons.crosses_reserve == false`):
- e′ = the D7a `observed_segment_end` when present.
  - For `EMPTY_OBSERVED_PATH_IDENTITY`, e′ = f.
  - If `observed_segment_end` is null for any other state, e′ = `endpoint`, with
    `listed_end` clipping as recorded in D7a. Assert consistency with D7a; any
    inconsistency fails closed.
- **State 2 (`QUARANTINE_PROPAGATED`) iff** there is a flag s of K with
  s ∈ (f, e′], **or** prev(s) ∈ [f, e′]. Reasons:
  - `FLAG_IN_PATH`: s ∈ (f, e′];
  - `ENDPOINT_OR_FORMATION_CLOSE`: only the prev(s) condition holds;
  - `UNDETERMINABLE_RESERVE_BOUNDARY`: e′ = 2023-06-30, and determining the flag
    on next(e′) would require a reserve session.
- Reserve-crossing labels keep `NOT_APPLICABLE` with reason `RESERVE_PURGE`.
- No D7a value, state or path is altered.

## 7. Liquidity exposure (rules Q3.5; report-only)

- For every D6 member row (f, K): `liquidity_window_exposed` = any
  `dollar_volume`(K, u) state 1 for u in the D6 63-session window f−63 … f−1 on
  trusted sessions.
- Report only. D6 membership is never changed, recomputed or filtered.

## 8. Dataset-v1.2 identity record and verifier (R3)

**`dataset_identity.json`** (canonical JSON: sorted keys, UTF-8, no timestamps,
no paths):

```json
{
  "dataset_id": "discovery_dataset_v1_2",
  "amendment": {"path": "docs/discovery_dataset_v1_2_pq_amendment.md",
                "sha256": "9261a55b9b82a5708dded65bfd3083d5a960cfa4375681b1b23390da62fb3ed0"},
  "quarantine_rule_hash": "df5bec69103eca5ad9196b3342e5013fab30a3b85cdb3a5332b033dcac5db886",
  "parent_dataset_id": "discovery_dataset_v1_1",
  "parent_authority": {"<path>": "<sha256>", "...": "every row of amendment §1"},
  "inherited_artifacts": {"b-c21b48190918f21f": null,
                          "n-f874d3ecb61374d4": "f93093a8b8fa07521191d687560c2c26ab16630ebeed35d68aa12867def47fb3",
                          "i-a21f1e46621dee94": null,
                          "q-5d1028655f8c82e1": "3d63e51b983096f0ae579cd1e382c613d9329d239c3f848cc785dee2d61cdfc3",
                          "c-27b6dabd0db0cb15": "2ee4940c14a85048fddd9e3e675c3e9a6f40a11c8887192a5e61939adfc62b60",
                          "u-6150bc6ac2866b7f": "6e8539b7acb4ed4559846234b0b26e6f1d4e6d34f7e0a5d1e2290134d563ed87",
                          "r-34565f074d64ccda": "4c487e47fd613f3f272109d14b43a27e061f9cc31e5b6d6bcefd4abc97994861",
                          "ti-aa23fabd3db4e595": "30ee0c51602cf792e8cb7654c2501f52c7d664675dbf78845c903df6683c2ae6",
                          "g6-2264dbcac2a09a3d": null},
  "d8": {"genesis_seq": 0,
         "genesis_record_hash": "984d7bec4fbb159b3eba3385b2a0af24a54c2c707478af91b5ba461684da4eea",
         "source_label": "discovery_dataset_v1:reserve:n-f874d3ecb61374d4",
         "semantics": "INHERITED_NOT_REREGISTERED"},
  "knowledge_pit": {"v1_2_records": "NONE_AUTHORIZED",
                    "rule": "development P-Q artifacts need no Knowledge-PIT record (D7c clarification §6 precedent); any future Phase-10 record citing Dataset-v1.2 evidence must commit to identity_hash, under a separate authorization"},
  "scope": "DEVELOPMENT_ONLY"
}
```

- `identity_hash` = SHA-256 of the canonical JSON bytes. `null` means that stage
  records no single content hash, and the id itself is the content-derived binding.
- **`verify_dataset_v1_2_identity(root) -> str`** returns the `identity_hash` after
  checking, read-only:
  1. the amendment, rules and parent-authority files have the bound hashes
     (repository bytes);
  2. every inherited manifest hash matches its artifact's own manifest field;
  3. the Knowledge-PIT log (`data_store/knowledge_pit/knowledge.jsonl`), read **only**
     through `smart_beta.science.knowledge.read_records`:
     - the seq-0 record has `record_hash = 984d7bec4fbb159b3eba3385b2a0af24a54c2c707478af91b5ba461684da4eea` and kind `ARTIFACT`;
     - its payload `source_label` equals the bound label;
     - **no record anywhere references `discovery_dataset_v1_2`**.
  4. The verifier never opens the log for writing and never constructs a
     `KnowledgeLog` writer.
- **Any mismatch raises `DatasetIdentityError`.** Callers (PQB) must treat it as
  fail closed.

## 9. Outputs and artifact

**Location:** `data_store/discovery_dataset_v1/derived/quarantine/<pq_id>/`.
- `pq_id` = `"pq-"` + the first 16 hex characters of SHA-256 over the canonical
  JSON of {all §2 bindings, the authority document hashes,
  `quarantine_rule_hash`, the code SHA-256 of `quarantine.py`}.
- It is not built in the repository.

| File | Content (sorted; deterministic) |
|---|---|
| `flags.parquet` | `security_key, flag_session, prev_session (nullable), next_session (nullable), next_beyond_development (bool)`; sorted by (key, flag_session) |
| `primitive_states.parquet` | **sparse**: every cell with state ∈ {1, 2} for the three primitives: `primitive, security_key, session, state, reason`. A D7c-present cell not listed is CLEAN; an absent cell is NOT_APPLICABLE |
| `input_states.parquet` | **sparse** lagged rows with state ∈ {1, 2}: `input, row_date, security_key, source_session, state` |
| `label_states.parquet` | **sparse** labels with state 2: `formation_date, security_key, h, state, reason`; plus a `label_state_counts` table in the manifest by (h, tier, reason) |
| `liquidity_exposure.parquet` | `formation_date, security_key, liquidity_window_exposed` (all D6 member rows) |
| `dataset_identity.json` | §8 |
| `pq.json` | manifest: `pq_id`, `pq_hash` (SHA-256 of the canonical JSON of every file's SHA-256 and the row counts), bindings, code SHA-256, counts (flags, Q elements, states by primitive and code, labels by reason, liquidity exposed), `identity_hash`, `scope: DEVELOPMENT_ONLY` |

- **Serialization:** pyarrow (pinned version) with fixed schemas, no pandas index,
  sorted keys, and no timestamp anywhere.
- **Rebuild semantics:** never overwrite. An existing `<pq_id>` directory with a
  differing `pq_hash` raises `NondeterministicOverlayError`. An identical rebuild is
  a no-op that verifies.
- **CLI:** `python -m smart_beta.dataset_v1.quarantine build --root <R>` and
  `verify --root <R>`. Production build only under the planner's authorized
  barrier.

## 10. Failure modes (all fail closed; typed exceptions)

- binding or code hash mismatch (`QuarantineBindingError`);
- a reserve path or date (`ReserveFirewallError`);
- a flag count other than 17,469 (`QuarantineInputError`);
- a D7c `source_session` ≠ prev(row_date) (`QuarantineConsistencyError`);
- a NaN-shadow vs oracle disagreement in tests or the barrier
  (`QuarantineConsistencyError`);
- an unsupported expression node (`UnsupportedQuarantineNode`);
- identity verification failure (`DatasetIdentityError`);
- a nondeterministic rebuild (`NondeterministicOverlayError`).

## 11. Required tests (synthetic fixtures; socket guard; credential sentinel)

1. Q(s) with interior s; s at the interval start (no prev); s at the interval end;
   s = last development session.
2. `daily_total_return` states exactly at {prev(s), s, next(s)}. The neighbours
   prev(prev(s)) and next(next(s)) stay CLEAN.
3. `dollar_volume` states exactly at {prev(s), s}.
4. TRI: CLEAN before the first quarantined return; 2 from it onward through segment
   end; the anchor stays CLEAN; non-admitted segments are 3.
5. Absent or masked D7c cells are 3, even if they are in Q(s) (**counterexample
   12**).
6. Lag alignment: the state on row d equals the state of the primitive at prev(d);
   no double lag (**counterexample 9**).
7. NaN-shadow vs AST oracle, cell-for-cell, on lag, rolling mean/sum/std/min/max,
   +, −, ×, ÷, literal, rank, winsorize, standardize, nesting, and the FactorSpec lag
   (**counterexamples 9, 10, 11**).
8. Cross-sectional own-cell rule: a quarantined cell does not taint other cells in
   its row. Those cells may change value in the shadow but are CLEAN, and are
   counted in `residual_cs_cells`.
9. The factor values V are byte-identical before and after state computation.
10. Label rules: the s ∈ (f, e′] boundary (s = f excluded, s = e′ included);
    prev(s) = f; prev(s) = e′; empty-path identity; e′ = 2023-06-30 →
    `UNDETERMINABLE_RESERVE_BOUNDARY`; a reserve-crossing label keeps
    NOT_APPLICABLE.
11. Liquidity exposure computed; D6 membership is unchanged by construction. The
    API exposes no membership mutation (**counterexample 13**).
12. Resolver rejects `reserve_sealed`, `raw`, `dsc_evidence`, `..`, symlink escape;
    post-load date assertion (**counterexample 14**).
13. Identity record canonical bytes; `identity_hash` stable.
    - A wrong amendment hash, wrong `quarantine_rule_hash`, wrong parent or artifact
      hash, wrong dataset id, wrong D8 genesis hash or label, or a K-log record
      mentioning `discovery_dataset_v1_2` each raise `DatasetIdentityError`
      (**counterexamples 17, 18, 19, 20**).
14. The K-log is opened read-only through `read_records` only (spy). No writer is
    constructed.
15. Determinism: a second build under `TZ=Asia/Tokyo LC_ALL=C` is byte-identical;
    rebuild is a no-op; a differing existing directory raises.
16. Pinned-code and authority-document hash checks.
17. Flag-count guard (17,469 in production bindings; a synthetic count in fixtures).
18. No timestamps or absolute paths in any output (byte scan).
19. Sparse completeness: (listed + implied CLEAN + implied NOT_APPLICABLE)
    reconciles with the D7c row counts per input.

## 12. Required mutations (`tests/fixtures/dataset_v1_2/pqa/mutations.json`; planner re-executes all)

- A1 one-endpoint rule (close(s) only)
- A2 omit next(s) for `daily_total_return`
- A3 include next(s) for `dollar_volume`
- A4 prev/next by calendar day
- A5 prev/next ignoring the D4 interval
- A6 TRI clears after segment re-anchor or after N sessions
- A7 TRI propagation starts at v ≥ anchor (anchor tainted)
- A8 TRI ignores the admitted flag
- A9 double lag on state panels
- A10 zero lag
- A11 rolling window off by one
- A12 lag shift sign reversed
- A13 cross-sectional op taints the whole row
- A14 cross-sectional op clears the own-cell state
- A15 Literal treated as quarantined
- A16 an unsupported node silently CLEAN
- A17 quarantine overwrites NOT_APPLICABLE
- A18 a finite-but-changed shadow cell classified as quarantined
  (cross-sectional over-taint), or the residual count dropped
- A19 factor values modified (shadow leaked)
- A20 label s ∈ [f, e′] (formation flag included)
- A21 label s ∈ (f, e′) (endpoint excluded)
- A22 drop the prev(s) label condition
- A23 the boundary label treated as CLEAN
- A24 a reserve-crossing label quarantined (overwrite)
- A25 the liquidity exposure filters membership
- A26 the resolver allows `reserve_sealed`
- A27 skip the post-load date assertion
- A28 skip the binding hash check
- A29 resolve the latest artifact
- A30 the flag count guard is disabled
- A31 skip flags outside current factors
- A32 the identity omits `quarantine_rule_hash`
- A33 the identity uses the c-d386… D-SC id
- A34 the identity skips the K-log check
- A35 the K-log opened for append
- A36 a timestamp in the manifest
- A37 unsorted output rows
- A38 overwrite an existing `pq_id` directory
- A39 `source_session` assertion skipped
- A40 the sparse file includes CLEAN rows with a wrong code

**40 mutations.** Any surviving non-equivalent mutation means STOP BEFORE MERGE.

## 13. Barrier (planner)

**Software:**
- review;
- all tests, plus 40 mutations re-executed by the planner, plus planner-independent
  conditional forms and a no-op control;
- a credential-scrubbed full suite under a network-denial guard.

**Production build** (development data only; authorized barrier step):
- `pq_id` and `pq_hash`; the 17,469-flag reconciliation; the state counts.
- A file-open audit: allowlisted files only; no reserve, raw or dsc_evidence;
  the K-log read-only.
- A second clean build under `TZ=Asia/Tokyo LC_ALL=C` that is byte-identical.

**Cross-checks against the committed path-comparison diagnostics (counts only):**
- the narrower definition s ∈ (f, endpoint], with reserve-crossing horizons
  excluded, must reproduce **1,700 of 2,772,000** top-500 labels and **110** suspect
  rows in member return paths;
- a mismatch is a HOLD for planner investigation, not an automatic pass or fail.

**NaN-shadow vs oracle on the real D7c grid**, for a fixed probe set of
expressions: `daily_total_return`, `lag(daily_total_return, 5)`,
`rolling_mean(dollar_volume, 63)`, `total_return_index / lag(total_return_index, 21)`,
and `rank(rolling_std(daily_total_return, 20))`.
- No grouping, returns or metric is computed.
- Counts only are reported.

**Research firewall:** no factor-performance quantity is computed or reported.

## 14. Certification and nonclaims

**Proposed certification:** PQA certifies the deterministic, development-only
construction of the P-Q quarantine overlay exactly per rules Q1–Q4:
- the two-endpoint suspect source sets; primitive, lagged-input and label states;
- conservative TRI propagation; NOT_APPLICABLE preservation;
- report-only liquidity exposure;
- a factor-input state calculus equal to Q3.3 (NaN-shadow, oracle-verified);
- a Dataset-v1.2 identity record verifiably bound to its parent authority,
  inherited artifacts, the D8 genesis record and `quarantine_rule_hash`, with no
  Knowledge-PIT write.

**Not claimed:**
- Gate-6 PASS, or the correctness of any suspect observation;
- any factor or estimand result;
- that quarantine removes residual cross-sectional influence;
- reserve or confirmation eligibility.

## Report

- files changed;
- test and mutation counts, with test-to-item and mutation-to-test maps;
- the commit SHA;
- the line `sealed files modified: NONE`.
