# Discovery Dataset v1.1 — D6 Universe-Construction Clarification (NORMATIVE)

- **Status:** normative clarification of D6 hold items H1, H2 and H3,
  decided by the user on 2026-09-28.
- **Documentation only:** it changes no completed stage (D1–D5, D-SC).
- **It clarifies, without modifying:**
  - `docs/discovery_dataset_v1_contract.md` (§3, §5, §12, §16 gate 11), SHA-256
    `d03f35549777e5a94046f06dd0621acfc3a1dedb7a4b40adc55fdae911fa80e3`;
  - `docs/discovery_dataset_v1_1_classification_amendment.md` (b6225b6);
  - `docs/discovery_dataset_v1_1_identity_binding_clarification.md` (b9d9e88).

## H1 — Development-only D6; reserve D6 deferred

- D6 constructs **only the 90 development formations**, 2016-01-29 to
  2023-06-30.
  - The final one is f = 2023-06-30 with cutoff 2023-06-29.
  - No development formation requires any session ≥ 2023-07-03, the reserve
    start (§12).
- **D6 must not construct, rank, inspect or materialize the reserve
  universe.** For the 38 reserve formations (2023-07-31 to 2026-08-31) it
  computes no dollar volume, trailing statistics, ranks, top-200/500/1000
  membership, or eligibility counts derived from reserve market data.
- Reserve formations are **DEFERRED**. They may be built only inside a
  separately authorized confirmation execution that satisfies the Phase-10
  preregistration, ACCESS and CONSUMPTION rules.
  - Physical possession of the raw reserve (`reserve_sealed/`) is not
    authorization.
- **Gate 11 (§16)** applies at the discovery stage to the development
  formations only: N ≥ 500 at each of the 90.
  - **RESERVE D6 GATE 11: DEFERRED / NOT ASSESSED.** This is not a failure;
    it follows from the reserve firewall.
- **Disclosure:**
  - D5 and D-SC read reserve-period rows structurally (flags, dates, names)
    under their frozen, value-free output contracts.
  - The exploratory v4 structural measurement produced reserve structural
    eligibility counts, which are held outside Git and are not a D6 input.
  - Neither counts as reserve D6 construction or confirmation consumption.

## H2 — Existence at formation

- A security is admissible at formation f only if `listed_start ≤ f−1`
  **and** `listed_end ≥ f`, using the retrospectively resolved D4 listing
  interval.
- A security whose listing ended before f cannot enter U_f, even if its
  trailing window has enough historical observations.
- **listed_end is used retrospectively only to establish that the security
  existed on formation session f.**
  - D6 uses no delisting outcome after f to decide eligibility at f.
  - Termination or delisting outcomes after f have zero effect on U_f.
- This inherits Dataset v1's retrospective identity and listing-interval
  limitations.
  - Historical delisting-knowledge timing is not claimed to be universally
    PIT-certified.
  - Issuer-identity knowledge timing and continuity remain **NOT CERTIFIED**.

## H3 — Corrupt and unusable rows in liquidity

- **Liquidity-valid row** = D5-usable (Q-D6-1: a trusted-session row that is
  not CORRUPT, not INTERVAL_MISMATCH and not VENDOR_NULL) **and** raw
  close > 0 **and** raw volume not null.
- CORRUPT and INTERVAL_MISMATCH rows enter neither the trailing usable count
  nor the median.
- An OFF_CALENDAR_ROW is not a trusted session and never enters a window or
  the liquidity calculation.
- **A valid zero-volume row stays liquidity-valid**, with DV = raw close × 0 =
  0, and counts toward the ≥ 50 threshold. Missing or null volume is not
  zero.
- CORP_ACTION_SUSPECT alone does not make a row unusable. The row takes part
  in missingness and liquidity, and its flag remains available for audit.

## Certification boundary (D6)

D6 may certify only **development** PIT-universe construction under the
frozen Dataset-v1 assumptions.

- **Reserve D6:** DEFERRED / NOT ASSESSED.
- **Issuer-identity knowledge timing/continuity:** NOT CERTIFIED.
