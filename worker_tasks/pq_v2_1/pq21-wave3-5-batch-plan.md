# P-Q v2.1 Waves 3–5 batch plan (FROZEN; approved by user 2026-10-07)

- **Authority:**
  - methodology `docs/discovery_dataset_v1_2_pq_v2_1_methodology.md` (`66772bb1…5d1e`);
  - plan `docs/discovery_dataset_v1_2_pq_v2_1_implementation_plan.md` (`007ec880…77f9`);
  - the merged A/B/C interface on master `ddc74e9`.
- **Scope:** PQ21-D, PQ21-E, PQ21-F and PQ21-H, then the planner's full I1–I15
  recertification. **PQ2-ADM is NOT included**; it needs its own authorization.

## 1. Why one batch, with D/E/F/H in parallel

H is the only caller of D, E and F, and these four specs freeze their new signatures.
Merging them one at a time would force one of two bad choices:
- a red master between merges; or
- a compatibility path in D and E ("bool `unknown` with no cause → treat as reserve"),
  which is exactly the SOURCE→RESERVE mislabel v2.1 forbids.

So:
1. the four workers start **in parallel** from the same base (master plus these specs);
2. each worker commit is reviewed on its own;
3. the planner builds a **scratch integration** of all four and runs:
   - the full P-Q v2 suite;
   - every mutation family;
   - the cross-layer cause test (§3);
   - the full repository suite;
4. if all pass, **one `--no-ff` octopus merge** of the four reviewed worker commits
   into master. Every worker commit stays in history, and master is never red;
5. then the **full I1–I15 recertification** on the v2.1 master, with additive evidence
   in `docs/discovery_dataset_v1_2_pq_v2_1_software_barrier.md`;
6. STOP, and report.

## 2. Ownership (no overlap)

| Task | Production file | Tests / fixtures |
|---|---|---|
| D | `pqv2_assign_simple.py` | `tests/test_pqv2_assign_simple.py`, `tests/fixtures/pqv2/simple/**` |
| E | `pqv2_assign_sandwich.py` | `tests/test_pqv2_assign_sandwich.py`, `tests/fixtures/pqv2/sandwich/**` |
| F | `pqv2_envelope.py` | `tests/test_pqv2_envelope.py`, `tests/fixtures/pqv2/envelope/**` |
| H | `pqv2_evaluation.py` | `tests/test_pqv2_evaluation.py`, `tests/fixtures/pqv2/evaluation/**` |

Forbidden for every worker: A, B, C, G, D7a/b/c, the Campaign modules, P-Q v1, and docs.

## 3. Planner cross-layer cause test (mandatory before merge)

**Setup:** synthetic H end-to-end runs through B → C → D/E → F → H, one per case:
- (a) a SOURCE-only factor-input dependency (SIMPLE and STD_X_RANK);
- (b) a RESERVE-only dependency;
- (c) both;
- (d) a SOURCE-only label dependency on a possible-leg member;
- (e) an unrelated unresolved flag.

**Assert:**
- the persisted diagnostics carry the exact cause lists:
  - (a) {SOURCE}, primary SOURCE;
  - (b) {RESERVE};
  - (c) both, primary RESERVE;
  - (d) {SOURCE};
  - (e) the period is unaffected;
- no layer emits `PQV2_RESERVE_UNKNOWN` for a SOURCE-only cell;
- under UNKNOWN, no numeric L and no winsorization evaluation;
- the same-period rule still makes the L statistic NOT_ASSESSED, and the per-period
  causes are auditable.

## 4. Stop conditions

All of the base plan §13 and the v2.1 plan §7 apply. In addition:
- a needed change outside these four files;
- any new cause representation;
- a SOURCE/RESERVE relabel anywhere;
- a surviving non-equivalent mutation;
- I1–I15 failing.
