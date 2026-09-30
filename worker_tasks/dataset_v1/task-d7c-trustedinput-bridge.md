# D7c — Dataset-v1 → Phase-6 TrustedInput bridge (Dataset v1.1) — FROZEN BEFORE IMPLEMENTATION

- **Status:** FROZEN BEFORE IMPLEMENTATION (user decisions of 2026-09-29).
  Implementation requires a separate launch authorization.
- **Authority, in precedence order** (on any conflict, stop and report):
  1. `docs/discovery_dataset_v1_contract.md` §§3, 5, 7, 8, 10, 11.1, 16 —
     `d03f35549777e5a94046f06dd0621acfc3a1dedb7a4b40adc55fdae911fa80e3`;
  2. `docs/discovery_dataset_v1_1_d7c_clarification.md` (839d4b0) —
     `508d5f1ac3da3c68233c72b4b31ea75d0ec1d88be9241e6dd018cd54467b4a89`;
  3. `docs/discovery_dataset_v1_1_d7_clarification.md` (8efbd96) —
     `bc8e6920d31dd429beb62030123a721a7293558ba57fe8e3d4d6cd6368a96946`;
  4. `docs/discovery_dataset_v1_1_d6_clarification.md` (5238303; H2, H3) —
     `e3cb52a892f533c41c5c0d1d927cd54d7e200ef2a28e56e76efdf01227aa17ea`;
  5. `docs/discovery_dataset_v1_1_classification_amendment.md` (b6225b6) —
     `f89a7128bb8113c6978fc6bca8bcaf6048cd0c7db1fe5ac2aaffa1c7d519c623`;
  6. `docs/discovery_dataset_v1_1_identity_binding_clarification.md` (b9d9e88)
     — `57da1603ff0c77f19460293802d34f2b2383800e0528746dcb8fbb6b9ceedfa9`;
  7. `worker_tasks/dataset_v1/task-d6-universe.md` §5 —
     `25927941d9ec4860edbd3a9e5511d6ef4f27d5b5189a183c6bca8e972653fe18`.
- **Owned files (new):**
  - `smart_beta/dataset_v1/trusted_inputs.py`;
  - `tests/test_dataset_v1_trusted_inputs.py`;
  - `tests/fixtures/dataset_v1/d7c/**`, including `mutations.json`.
- **Must not modify** any existing file. In particular: no Phase-6, D6, D7a,
  D7b, D8 or D-SC file.
- **Provider or network calls: 0.**

**Pinned read-only code (SHA-256; fail closed on mismatch):**

| File | SHA-256 |
|---|---|
| `smart_beta/spec/engine.py` | `eea3bc30dd50121865009d0bab4999dc76068c1e2fbf9f7c965413c2a443b46a` |
| `smart_beta/spec/requirements.py` | `e0b99194cbdb8f1c907a03958ca2b0c1ba7b877790ef562570c83b52324c57dc` |
| `smart_beta/spec/transforms.py` | `15708149406017a6c970d42aebef7b0e2ea9ed6b0be48182f7769006a9ff5a66` |
| `smart_beta/spec/expression.py` | `9b027c5da83f0778d72c236327c09967172a5314d60c47bd54ea6337fe46d6f4` |
| `smart_beta/spec/evaluator.py` | `aecdeba642ca23f3285b8f55565afb6edaf255322783345e22a50c20c405bcce` |
| `smart_beta/spec/factor_spec.py` | `ab6c65d312d6fe1e421c489e6ad8045a50e265c8f73dbe6a07738e1bf5458c56` |
| `smart_beta/pit/corporate_actions.py` | `5d57126174e42415ad967d415195c95b9f535e8a9d6a2e263bf321b3408ceffb` |
| `smart_beta/vendors/tiingo/calendar_source.py` | `79bca35ddd46f87102e90f6ce0c2d51a8b0d16f1fe9caf56b7674b43fce8e23f` |
| `smart_beta/dataset_v1/classification.py` | `e03f5611bc91419c34429619ab62c8d89218dce1d693075c0fec3f1ad6a6bfda` |
| `smart_beta/dataset_v1/reserve_firewall.py` | `4b8a74b5f950bd3bdf58155bbfae32401753f2bed45d5031860d6edb1d493e15` |

## 1. Scope

D7c builds the three contract-§7 inputs for the **development** partition
and delivers them as Phase-6 `TrustedInput`s under the §11.1 one-session lag
(clarification §§1–2).

D7c defines **no**:
- factor, `EvaluationSpec`, `CampaignEvaluationDeclaration`, screen,
  threshold or holdout;
- delisting scenario;
- reserve access;
- Knowledge-PIT record.

## 2. Bindings and access (exact; fail closed; never "latest")

| Object | Binding |
|---|---|
| D3 development `observations` / `ca_events` | read **only** through `reserve_firewall.DevelopmentAccessor` (bound hashes `557001d0…`, `afd4a298…`; build `n-f874d3ecb61374d4`, `normalized_hash f93093a8…`) with `end ≤ 2023-06-29` |
| D4 `identity_assignment.parquet` | `i-a21f1e46621dee94`, `fee4614009095773f75fb832343a5fa9b87b9de1cf363125567222b08fad3f8b`; manifest `identity.json` `b8ed3d23…` |
| D5 `day_flags.parquet` (development) | `q-5d1028655f8c82e1`, `f4010ebeabf5cfae2684f3658a5f19f922f15e2c8898410049f2148440cf7891`; manifest `qc_report.json` `16c58290…` |
| D-SC `class_timeline.parquet` / `policy_timeline.parquet` | `c-27b6dabd0db0cb15` (`dsc_hash 2ee4940c…`), `e70d5c91b3f7bce6b5dee3337d47ec3f0c5e7700b35300d4ef3fc8e48b82caeb` / `988087c5c0a8daf5a2674eb4b4b8db7a0e2fce141478e34b2d63d20bfb651c8e`; manifest `dsc.json` `23877643018f28b4776e092be1c53d048f0c51a810544f073f9a68f57abcc030` |

- The D4, D5 and D-SC files are opened only through a guarded resolver with
  an allowlist. It raises `ReserveFirewallError` for a `reserve_sealed`,
  `raw` or `dsc_evidence` component, for `..`, or for any non-allowlisted
  path.
- **Post-load assertion:** every loaded date is < 2023-07-03.
- **D7a** (`r-34565f074d64ccda`) is read **only** by the planner barrier's
  byte-equality check (§9), never by the builder.
- **The six authority documents are hash-bound.**
- **Bindings are a frozen value;** tests inject synthetic bindings. The
  accessor is used unmodified.

## 3. Grid (clarification §§2–3)

- **Rows:** `build_nyse_calendar(2015-01-02, 2027-12-31)` sessions `d` with
  `2015-01-02 ≤ d ≤ 2023-06-30`.
  - The row count is derived, never hard-coded.
  - `prev(d)` is the previous element of the calendar.
- **Columns:** sorted unique D4 `INTERVAL_ASSIGNED` keys (keys appearing
  exactly once in D4).
- **Values:** `float64`; missing is NaN.
- **The three frames share this exact grid.**

## 4. Primitives (unlagged, dated s ≤ 2023-06-29)

For key K with ticker T and interval [listed_start, listed_end]:
- **Usable row:** per D6 §5.
  - On a trusted session and inside the interval.
  - No D5 day flag with disposition CORRUPT (OHLC_INCONSISTENT,
    NONPOSITIVE_PRICE, MALFORMED_CA, DUPLICATE_DATE, OFF_CALENDAR_ROW), class
    INTERVAL_MISMATCH or class VENDOR_NULL.
  - `close > 0`, with exactly one usable row per session (otherwise
    `D7cConsistencyError`).
- **`r(s)` VALID** iff s and prev(s) both have usable rows.
  - `raw = close(s)/close(prev(s)) − 1`.
  - Then `compute_adjusted_returns(raw_panel, ca_panel, as_of=2023-06-30,
    calendar=None, settings=replace(DEFAULT_SETTINGS,
    pit_availability_buffer_days=0))`, where the corporate-action events are
    those of T inside the interval.
  - **Preconditions** (each fails closed with `D7cConsistencyError`): the
    event is on a trusted session; `is_superseded` is false;
    `knowledge_date == effective_date`; the factor is finite and > 0.
  - **Guard:** the result is finite and > −1.
- **`dv(s)`:** `float64(close) × float64(volume)` on a usable row with volume
  not null. Zero volume gives 0.
- **Segments (TRI):** anchor a, recursion and first break b as in
  clarification §5, with `TRI(a) = 1.0`.
  - Later segments carry distinct internal segment ids.
  - Only segment 1 is admissible.

## 5. Economic mask (P1c)

Cell (d, K) is admitted only if all of these hold:
1. The primitive dated `s = prev(d)` exists and is valid for K:
   - `r(s)` VALID for `daily_total_return`;
   - `dv(s)` defined for `dollar_volume`;
   - `s ∈ [a, b−1]` of segment 1 for `total_return_index`.
2. `classification.class_state_at(class_timeline, K, prev(d))[0] ==
   "COMMON_STOCK"`.
3. `classification.policy_state_at(policy_timeline, K, prev(d))[0] ==
   "NO_REGULATORY_EXCLUSION_OBSERVED"`.

- The D-SC functions are used as imported. Memoizing identical
  (key, cutoff) calls is allowed; re-deriving timelines is **not**.
- Every other cell is NaN.

## 6. Delivery to Phase 6

`load_trusted_inputs(root, *, bindings=None) -> dict[str, TrustedInput]`
returns exactly three entries, keyed `daily_total_return`,
`total_return_index` and `dollar_volume`.

- **Values:** the §3 grid, where row d holds the admitted primitive dated
  prev(d) and everything else is NaN. **The lag is applied exactly once.**
- **Capabilities:**

  | Input | Frequency / period / units |
  |---|---|
  | `daily_total_return` | DAILY / PERIOD / FRACTION |
  | `total_return_index` | DAILY / INSTANT / RATIO |
  | `dollar_volume` | DAILY / PERIOD / CURRENCY |

  All three: `history` = grid row count, `has_knowledge_date=True`,
  `has_positive_vintage_identity=False`,
  `revision_policies={POINT_IN_TIME}`.
- **Evidence class:** `EvidenceClass.LIVE_RECORDED`. `vintage_evidence=None`.
- **Knowledge-date rule** (recorded in the manifest): "row d holds the
  end-of-day observation of prev(d); values as served at C (vendor revision
  history not certified)".
- **The loader reads only the D7c artifact** (§7), never the raw sources.

## 7. Artifact

Under `<root>/derived/trusted_inputs/<ti_id>/`, where `ti_id = "ti-" +` the
first 16 hex characters of SHA-256(canonical_json(bindings, document hashes,
pinned code hashes, SHA-256 of `trusted_inputs.py`)).

| File | Columns | Rows / order |
|---|---|---|
| `daily_total_return.parquet` | `row_date` date32, `security_key` string, `source_session` date32, `value` float64 | admitted non-NaN cells only; sorted by (row_date, security_key) |
| `total_return_index.parquet` | same | same |
| `dollar_volume.parquet` | same | same |
| `segments.parquet` (audit only, not a Phase-6 input) | `security_key`, `segment_id` int64, `anchor_session`, `last_session`, `admitted` bool | sorted by (security_key, segment_id) |
| `trusted_inputs.json` | canonical JSON, no timestamps: bindings, documents, pinned code, grid (first/last row, row count, SHA-256 of the column list), capabilities, the knowledge-date rule, mask counts (masked by class / policy / invalid / segment), `files_sha256`, `ti_hash = SHA-256(canonical_json(files_sha256))` | — |

- **Invariant:** `source_session == prev(row_date)` on every row.
- **Output assertions:** no date ≥ 2023-07-03 in any output.
- **Determinism:**
  - stable sorts; independence from input order, timezone and locale;
  - float64 values through pyarrow;
  - byte-identical rebuilds; an existing output with different bytes raises
    an error and is never overwritten;
  - atomic writes.
- **CLI:** `python -m smart_beta.dataset_v1.trusted_inputs --root <root>`
  prints `ti_id` and `ti_hash` only.

## 8. Required tests (synthetic; socket guard; credential sentinel)

**Timing and gate 13**
1. Row d holds the primitive dated prev(d) for all three inputs.
2. The lag is applied once: the return dated s appears at row next(s), not
   next(next(s)) and not s.
3. Friday→Monday: Monday's row holds Friday's primitive, not Sunday's.
4. NYSE holiday: the row after a holiday holds the pre-holiday session.
5. Month boundary.
6. Year boundary.
7. The first grid row is NaN.
8. Row 2023-06-30 holds 2023-06-29 primitives, with no read ≥ 2023-07-03.
9. Future-data invariance (property test): mutating or removing any primitive
   input dated ≥ f (rows, flags, corporate actions, D-SC evidence) leaves
   every row-≤f cell byte-identical, for random f including the 2023-06-30
   case.
10. Changing the primitive at prev(f) changes the row-f cell when it is
    admitted.
11. Real Phase-6 path: `evaluate_factor` with FactorSpecs using `ret`, `lag`,
    `mean/std/max` and `rank`/`standardize` gives row-f factor values
    invariant to data dated ≥ f.
12. `source_session == prev(row_date)` on every output row.
13. Previous-calendar-day semantics are rejected (a naive-lag fixture
    differs).

**Primitives and QC**

14. `daily_total_return` equals the §8 reference (dividend, split, both
    together).
15. The corporate-action preconditions fail closed (non-session event,
    superseded event, knowledge date ≠ effective date, factor ≤ 0).
16. A non-finite or ≤ −1 return raises an error.
17. A CORRUPT row gives NaN for the return at s and at next(s).
18. INTERVAL_MISMATCH gives NaN.
19. VENDOR_NULL gives NaN (and `dv` NaN).
20. A missing row gives NaN; nothing is filled.
21. A CA-suspect row is usable.
22. Zero volume gives `dv = 0`, not NaN.
23. A null-volume row is D5 `VENDOR_NULL`, so it is unusable: `dv` NaN and
    the return at s and next(s) NaN (D6 §5 usable-row rule).
24. Duplicate usable rows on one session raise an error.
25. An off-calendar row is never a session.
26. `adj*` fields are never read (changing them leaves the output
    byte-identical).

**Identity and population**

27. Only unique `INTERVAL_ASSIGNED` keys are columns; ambiguous, unreachable
    and duplicated keys are absent.
28. A row outside the interval is not used.
29. A reused ticker's two keys have separate histories (no stitching).
30. Future D6 membership never affects the column set (grid independent of
    D6).

**P1c mask**

31. A key that is not COMMON at prev(d) is NaN at row d.
32. Policy `EXCLUDED` gives NaN; `ISSUER_STATUS_UNRESOLVED` gives NaN.
33. `COMMON` with `NO_REGULATORY_EXCLUSION_OBSERVED` is admitted.
34. The cutoff is prev(d), not d: an evidence change dated d leaves row d
    unchanged.
35. A future class or policy transition leaves earlier rows unchanged.
36. Adding a security that only becomes COMMON later leaves earlier
    cross-sectional factor values unchanged (via `evaluate_factor`).
37. An ineligible NaN column does not affect `rank`, `winsorize` or
    `standardize` of eligible columns (with and without the column,
    identical).
38. There is no final-class or ever-COMMON mask (a later-COMMON key is NaN
    before it becomes COMMON).

**`total_return_index`**

39. Anchor = 1.0, and the recursion matches the product of (1+r).
40. The break session b is NaN, and so is every later session (unlagged).
41. Row b carries `TRI(b−1)` (admissible); row next(b) is NaN.
42. The first valid return after a gap stays NaN (suppressed).
43. `TRI / lag(TRI, N)` is NaN at every row where [endpoint−N, endpoint]
    crosses b, for N ∈ {1, 2, 251, 252, 253, 1000}.
44. Nested `lag(lag(TRI, 200), 200)` across b is NaN.
45. `max(TRI, 252)`, and `mean(TRI, N)`, across b is NaN.
46. Within segment 1, ratios equal compounded returns exactly.
47. An identity boundary breaks the segment.
48. Later segments are recorded in `segments.parquet` with distinct ids and
    `admitted=false`.
49. No forward fill, no zero substitution, no carried level (fixtures).

**Delivery and Phase-6 contract**

50. Exactly three `TrustedInput`s, on one identical grid.
51. The capabilities are exact, and a requirement for DAILY/PERIOD/FRACTION
    `daily_total_return` is admitted.
52. The evidence class is `LIVE_RECORDED`; it is never upgraded.
53. The Phase-6 grid check passes (a mismatched grid raises in a
    negative-control test).
54. There are no delisting or scenario values (no τ/T0/TS column or value
    path).
55. The loader reads only the artifact files.

**Firewall, bindings and determinism**

56. The D3 reads go through `DevelopmentAccessor` (spy) with `end ≤
    2023-06-29`.
57. A `reserve_sealed`, `raw` or `dsc_evidence` path is refused, as is a
    non-allowlisted path.
58. An injected reserve-dated row raises `ReserveFirewallError`.
59. A binding hash mismatch (each file) fails closed; so does a document or
    pinned-code mismatch.
60. A decoy newer artifact directory is ignored.
61. Permuted inputs give byte-identical output.
62. Changing `TZ` and `LC_ALL` gives byte-identical output.
63. Two builds are byte-identical; an existing output with different bytes
    raises an error.
64. No output date is ≥ 2023-07-03.
65. No network (socket guard) and no credential read.
66. No hard-coded production counts; the manifest mask counts are computed.

## 9. Required mutations (`tests/fixtures/dataset_v1/d7c/mutations.json`; planner re-executes all)

M1 zero lag (row d holds s = d) · M2 double lag · M3 reverse shift · M4 previous calendar day instead of trusted session · M5 include row f data · M6 read `end ≥ 2023-07-03` · M7 bypass `DevelopmentAccessor` · M8 skip the post-load reserve assertion · M9 forward-fill missing values · M10 treat a missing row as return 0 · M11 bridge a gap with the last available close · M12 allow a CORRUPT row · M13 allow INTERVAL_MISMATCH · M14 allow VENDOR_NULL · M15 zero volume → NaN · M16 use adjClose or adjusted fields · M17 a forked corporate-action formula (apply the event to the next session) · M18 ignore unmatched corporate actions · M19 drop the > −1 / finite guard · M20 ticker stitching across keys · M21 include IDENTITY_AMBIGUOUS keys · M22 use rows outside the interval · M23 grid columns from the D6 top-N union · M24 final-class mask (ever-COMMON) · M25 D-SC cutoff = d instead of prev(d) · M26 admit ISSUER_STATUS_UNRESOLVED · M27 admit EXCLUDED · M28 skip the class mask · M29 TRI restart visible after a break · M30 TRI 252-session suppression instead of segment-1-only · M31 TRI carries the level across a gap · M32 TRI anchor ≠ 1.0 or anchor off by one · M33 TRI ignores an identity boundary · M34 TRI break session admitted · M35 capability evidence class upgraded · M36 wrong capability units or period · M37 frames on different grids · M38 sorting-dependent shift (shift before sort) · M39 accept duplicate usable rows · M40 timezone-dependent session interpretation · M41 skip the binding hash check · M42 skip the pinned-code check · M43 resolve the latest artifact · M44 allow a reserve path in the resolver · M45 include a τ/scenario value · M46 hard-code the grid row count · M47 `source_session ≠ prev(row_date)` · M48 loader reads raw sources · M49 an ineligible column given 0 instead of NaN · M50 nondeterministic manifest (timestamp)

**50 mutations.** Any surviving non-equivalent mutation means STOP BEFORE
MERGE.

## 10. Barrier (planner)

**Software:**
- review;
- 66 tests;
- all 50 mutations run by the planner, plus planner-independent conditional
  forms and a no-op control;
- a credential-scrubbed full suite.

**Production build (development data only):**
- `ti_id` and `ti_hash`; the grid rows and columns; the mask counts.
- A file-open audit: only allowlisted files are opened, D3 goes through the
  accessor, and no reserve read occurs.
- **Byte-equality:** D7c's unlagged `daily_total_return` equals D7a
  (`r-34565f074d64ccda`) on **every** overlapping VALID (key, session) cell,
  before D7c's mask.

**Real-data gate-13 invariance:** for formations 2016-01-29, a Monday after a
weekend, a session after an NYSE holiday, a month boundary, a year boundary
and 2023-06-30:
- build with source data truncated at prev(f);
- require every row-≤f cell to be byte-identical to the full build.

**Determinism:** a second clean build under `TZ=Asia/Tokyo LC_ALL=C` is
byte-identical.

**Research firewall:** the planner reports counts only. It computes no factor
performance, IC or returns summary.

## 11. Certification and nonclaims

**Proposed certification:**
> D7c certifies the deterministic, development-only construction and Phase-6
> delivery of the contract-required `daily_total_return` (the §8 total
> return, byte-equal to D7a), `total_return_index` (segment-1-only, exact
> within one uninterrupted valid-return segment and fail-closed across any
> break for every legal Phase-6 lookback) and `dollar_volume` (raw close ×
> raw volume) TrustedInputs.
>
> Each is masked to the known-at-the-time D-SC common-stock cross-section,
> uses the D4 identity intervals, and is delivered under the frozen
> one-trusted-session lag, with gate-13 timing and future-data invariance
> demonstrated.

**Not claimed:**
- factor efficacy, alpha or predictive validity;
- any Campaign-1 result;
- confirmation eligibility or reserve evidence;
- identity continuity beyond D4, or issuer-identity timing;
- delisting proceeds (`DELISTING_RETURN_NOT_CERTIFIED`);
- survivorship completeness;
- volume semantics;
- vendor revision history;
- production readiness.

## Report

- files changed;
- test and mutation counts, with test-to-item and mutation-to-test maps;
- the commit SHA;
- the line `sealed files modified: NONE`.
