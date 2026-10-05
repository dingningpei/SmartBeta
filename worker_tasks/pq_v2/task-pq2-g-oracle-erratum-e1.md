# PQ2-G-E1 — Oracle MISDATED alternatives are exclusive with the S2 correction of the same flag (FROZEN)

- **Status:** FROZEN. Authorized by "P-Q v2 WAVE-2 RECOVERY — PQ2-G-E1 ERRATUM
  AUTHORIZED", on master `f1938f92badf839845eac4e30ae02a47957eb546`.
- **Amends:** `worker_tasks/pq_v2/task-pq2-g-oracle.md` (committed in `b2db30b`, SHA-256
  `cf034f410a415518556cb54ec76570c4fb7f2930b272b39d31637e79a20784c7`), §3 only. All
  other G requirements are unchanged.
- **Nature:** a **task-spec / oracle erratum only**. The frozen methodology
  `docs/discovery_dataset_v1_2_pq_v2_methodology.md` (SHA-256 `a31ed9c4…a56d`) and
  CS-ID / ED-ID / CE-ID are **unchanged**.
- **Defect:** G §3 let one correction state choose a branch, a λ **and** a MISDATED
  alternative for the same flag, so the oracle reached factors φ_d^{±1}·λ.
  - **Frozen authority:** M§7 makes each candidate a discrete *alternative* hypothesis,
    and M§8 defines **Λ_s^tot = Λ_s ∪ {candidate factors}**, a union and not a product.
  - **Consequence:** the joint state lies outside the frozen Ω. P-Q v2 certifies
    robustness over the frozen declared model, not over a wider superset, so the oracle
    must enumerate exactly the frozen Ω.
  - **Found by:** PQ2-B in Wave 2.

## E1.1 Replacement rule (replaces the G §3 "a correction picks" bullets)

For each flag s, the per-flag options are **exactly**:
- **(a) Ordinary S2 alternative:** `misdated = None`, with branch ∈ {NONE, CA,
  CLOSE_PREV, CLOSE_S} and λ on the Q_s grid (unchanged); or
- **(b) MISDATED alternative:** for each recorded split/dividend candidate d (|pos(d) −
  pos(s)| ≤ 5, d ≠ s): `misdated = d` with **branch = NONE and λ = 1**. The recorded
  combined factor φ_d moves from d to s, as before.

**No state combines a MISDATED choice with λ ≠ 1 or a non-NONE branch for the same
flag.**

**Unchanged:**
- the Cartesian product over **different** flags;
- the optional `single_misdating` (global one-misdating) filter;
- the split/dividend filter;
- the ±5 trusted-session rule;
- every other oracle semantic.

## E1.2 Required tests (added to `tests/test_pqv2_oracle_self.py`)

1. **Structural:** for every world in `CASES` and for dedicated worlds with candidates,
   no enumerated state has `misdated ≠ None` together with branch ≠ NONE or λ ≠ 1.
2. **Realistic counterexample regression:**
   - setup: a label (f, e*) with the flag s inside (f, e*], its paired recorded dividend
     d **outside** (f, e*], m_s = 2.0 (a genuine D5 suspect) and φ_d = 0.9;
   - assert: `label_extremes` equals the hand-computed extremes of
     (1 + R_obs)·(Q_s ∪ {φ_d}) (the union);
   - assert: the multiset of per-flag label factors the oracle enumerates contains no
     value φ_d·λ with λ ≠ 1.
3. **Opposite positional / inversion case:**
   - (i) d **inside** (f, e*] and s outside, giving the alternative factor φ_d⁻¹, union
     with the flag's own Λ (1 when s has no label dependency);
   - (ii) a flag at an inverted position (s = f or s = next(e*), Λ_s = Q_s⁻¹) with a
     candidate, giving extremes from Q_s⁻¹ ∪ {φ_d^{±1}}.
   - Both are hand-computed. Both assert that no product state exists.
4. **Census, independence and determinism:** unchanged and still passing.

## E1.3 Mutation (added to `tests/fixtures/pqv2/oracle/mutations.json`)

- **G-M7:** a MISDATED state again allows a non-NONE branch / λ ≠ 1 (simultaneous
  MISDATED × S2 composition). It **must be killed**.

The existing G-M1…G-M6 remain and must stay killed.

## E1.4 Consequences for other tasks (no spec change)

- **PQ2-B:** continues its frozen **union** `label_envelope` unchanged. It is verified
  against the repaired oracle.
- **PQ2-C** (`1dc6fc7`): must add a **new corrective commit** on the same branch that
  restores hull(per-flag set ∪ alternatives) (C §3), and re-validate against the
  repaired oracle. `1dc6fc7` is preserved, not amended.
- **Wider is not safe:** the argument "the product rule is wider, therefore safe" is
  rejected. Implementations must conform to the frozen Ω.

## E1.5 Ownership and scope

- **Repair task files:** only the PQ2-G files: `tests/pqv2_oracle/**`,
  `tests/test_pqv2_oracle_self.py`, `tests/fixtures/pqv2/oracle/**`.
- **Branch:** a new repair branch `pq2/task-pq2-g-e1` from the post-E1 master.
- **Stop conditions:** G plan §13 plus this erratum; any need to touch another file is a
  STOP.
