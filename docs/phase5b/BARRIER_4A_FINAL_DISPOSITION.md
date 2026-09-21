# Phase 5B Barrier 4a — Final Disposition (post Contract-A / profit_dedt vintage investigation)

**STRICTLY OFFLINE GOVERNANCE REVIEW.** Live calls: **0**. Production-code
changes: **none**. Test changes: **none**. No Stage 3 rerun, no P5B-7
authorization.

- Master before: `1b4a196d07bb6a6fcc3d7d5f8939d86e11a4b2bb`
- This document **supersedes the blocker framing** of
  `docs/phase5b/BARRIER_4A_DISPOSITION.md` while preserving that document as
  the historical record. No historical evidence is rewritten.

## 1. Final verdict

```
BARRIER 4A:  BARRIER-4A-FAIL-STRUCTURAL-PIT-LIMITATION
P5B-7:       P5B7-BLOCKED
```

## 2. Evidence chain (chronology preserved)

| step | commit / branch | outcome |
|---|---|---|
| 1. P5B-6 capture | `phase5b/task-p5b-6-ch3-pilot` @ `24b7d08` | 80/84; 4 frozen B2 identities unresolved (transport) |
| 2. Original Barrier-4a disposition | `docs/phase5b/BARRIER_4A_DISPOSITION.md` | D4A-HOLD |
| 3. CA1 / CA1-T1 capability | `57fc53a` (TLS stop) → `97e082e` (3/3 SERVES-DATA) | Contract A serves `income` + `fina_indicator` |
| 4. D4A recovery eligibility | `cbc36ca` | `D4A-CA-ELIGIBLE` |
| 5. B2R-CA four-identity recovery | `1ef3a4b` (spec) → `2566fecc` (evidence) | 4/4 `SERVES-DATA` |
| 6. Semantic/vintage review | `c30bcda` | 3/4 admissible; B2-CA-2 blocked |
| 7. B2-CA-2 anchor forensics | `48cc8d2` | original vintage found; frozen gate still fails closed |
| 8. PIT restatement architecture review | `09d7935` | gate is conservative, not fundamental |
| 9. `profit_dedt` vintage audit | `1b4a196` | `NOT-CERTIFIED`; `STRUCTURAL-LIMITATION` |

## 3. Corrected characterization of the blocker

The original four-identity **transport / data-availability** blocker is
**RESOLVED**: Contract A (a D4A-qualifying materially distinct path)
retrieved all four identities (`4/4 SERVES-DATA`). Three of four are
semantically admissible.

The sole remaining blocker is **not** an unretrieved record. It is a
**structural PIT vintage limitation**:

- `000858.SZ::income::20250930` has two preserved vintages — original
  (`knowledge_date=20251031`) and restatement (`knowledge_date=20260430`) —
  which both share `ann_date=20251031`.
- The required `fina_indicator.profit_dedt=6458238128.8` exists but cannot
  be assigned to either vintage: `fina_indicator` exposes no `report_type`,
  no `f_ann_date`, and no usable `update_flag`; `ann_date` alone is proven
  insufficient.
- The `profit_dedt` vintage-semantics audit (58 files, 3 schema variants,
  4 multi-row groups) returned `PROFIT-DEDT-VINTAGE-IDENTITY-NOT-CERTIFIED`
  with `STRUCTURAL-LIMITATION`, and `AMENDMENT-NOT-JUSTIFIED`.

## 4. Software vs empirical certification (stated explicitly)

- **Software implementation status:** complete. The adapter, the append-only
  bitemporal PIT engine (`latest_known_value`), and the CH3 join (including
  its conservative provider-safety gate) are implemented and behave as
  frozen. **No software defect is established.**
- **Empirical benchmark certification status:** `NOT CERTIFIED`. The frozen
  CH3 benchmark requires `ni_ex_nonrecurring` with positive vintage identity;
  the current provider/data contract cannot supply that vintage identity for
  the one remaining period. This is an **empirical PIT certification
  failure**, not a software failure.

## 5. Why FAIL rather than HOLD

`BARRIER-4A-FAIL-STRUCTURAL-PIT-LIMITATION` (not HOLD) because the
investigation is now sufficient to establish that the blocker **cannot be
resolved within the existing contract** without changing
architecture/provider semantics: the evidence shows `fina_indicator`
structurally lacks a certified vintage-identity field, and no
architecture amendment is justified by existing evidence. There is no
concrete unresolved evidence question that the current frozen contract can
still answer.

## 6. Evidence branch recommendation

**`PRESERVE-UNMERGED`** — the Contract-A evidence branches
(`phase5b/task-p5b-ca1-t1-basic-capability-probe` @ `97e082e`,
`phase5b/task-p5b-b2r-contract-a-recovery` @ `2566fecc`,
`phase5b/task-p5b-ca1-contract-a-capability-probe` @ `57fc53a`) are
preserved as historical certification evidence with their SHAs recorded in
the master governance documents, matching the existing repository convention
(the P5B-6 evidence branch is likewise preserved unmerged). They carry raw
provider bodies and recorder scripts, not production code, and merging them
would add no certified value while churning master.

## 7. Future research (separate from Phase 5B completion)

None of the following is completion of the frozen benchmark; each is a
distinct, separately authorized future direction:

- authoritative provider documentation establishing a real
  `fina_indicator` revision/vintage identity;
- a different source supplying non-recurring-profit values with
  independently observable publication/vintage timestamps;
- a separately specified benchmark variant using another PIT-safe
  profitability definition.

## 8. Status block

```
ORIGINAL TRANSPORT BLOCKER:     RESOLVED
CONTRACT-A FOUR-IDENTITY:       4/4 SERVES-DATA
SEMANTICALLY ADMISSIBLE:        3/4
SOLE REMAINING BLOCKER:         000858.SZ::20250930 profit_dedt vintage identity
SOFTWARE DEFECT:                NONE ESTABLISHED
STRUCTURAL PIT LIMITATION:      ESTABLISHED
CURRENT FROZEN CH3 EMPIRICALLY CERTIFIED: NO
BARRIER 4A:                     BARRIER-4A-FAIL-STRUCTURAL-PIT-LIMITATION
P5B-7:                          P5B7-BLOCKED
```

## 9. Repository action

Documentation only, committed to `master`. No production change, no test
change, no live call, no evidence-branch merge, no push, no tag.
