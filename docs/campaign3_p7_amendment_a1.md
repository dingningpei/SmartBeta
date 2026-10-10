# Campaign-3 P7 — amendment A1: reserve chain as new fold-generic code, proven by development equivalence (FROZEN 2026-10-09; user-approved)

- **Trigger (a hard stop: evidence contradicts a frozen design assumption).** The frozen
  P7 design (`59fdd21`) assumed the reserve derivation could "reuse the frozen
  development D-chain code, parameterized by fold". Planner inspection shows the
  builders **hard-code the development boundary and refuse reserve dates by design**
  (the firewall):

  | Module | Boundary references |
  |---|---|
  | `universe.run_universe` | 40 |
  | `returns.run_returns` | 44 |
  | `trusted_inputs.build_trusted_inputs` (refuses any date ≥ 2023-07-03) | 31 |
  | `delisting_bridge` | 19 |

  Several of these are **pinned** files (`pqv2_core.PINNED_CODE`). Parameterizing them
  would change pinned or sealed code. That is forbidden without re-certifying everything
  that pins them.

## Proposed amendment

1. **New code.** The reserve chain is implemented as **new, fold-generic modules** that
   re-express the frozen D4 / D6 / D7a / D7c (and D7b-observed) logic with an explicit
   fold window. The existing pinned builders stay the sole authority for development
   artifacts; they are untouched.
2. **Equivalence proof, binding before any reserve use.** Run on the **development**
   window with development inputs, the new modules must reproduce the frozen development
   artifacts **byte-for-byte**:
   - the D6 universe (`u-6150bc6ac2866b7f` membership);
   - the D7a returns and labels (`r-34565f074d64ccda`);
   - the D7c trusted inputs (`ti-aa23fabd3db4e595`, `ti_hash 30ee0c51…`);
   - the D7b observed series for the C1 specs.

   Any mismatch → STOP; never patch the comparison. This replaces the design's
   "dev-sealed-fold rehearsal" with a stronger full-development equivalence.
3. **The only allowed differences** between the dev and reserve runs are:
   - the fold window and input package paths (development `normalized/…` vs
     `reserve_sealed/n-f874d3ecb61374d4`, `q-5d1028655f8c82e1`, `edgar-f0d0ee1b…`);
   - lookback rows before 2023-07-03 come from development data.

   Each is a declared parameter, and the equivalence test covers the code paths
   otherwise.
4. **The ACCESS gate** (unchanged from the design). The reserve run refuses unless a
   K-PIT `ACCESS` record authorizing that exact run (hypothesis, procedure, code hashes)
   is present.
5. **Effort.** This is a sizeable implementation: four or five Pi tasks plus the
   equivalence harness. It is the largest remaining Campaign-3 item.

## Alternative (not recommended)

- **What it does:** modify the pinned builders to accept a fold parameter.
- **Cost:** it invalidates the pinned hashes. That forces a re-certification of P-Q
  v2.1/v2.2/v2.3, D7, and Campaign-1/2 bindings.
