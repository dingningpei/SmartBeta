# E-2 — Dataset v1.3 EDGAR event layer (FROZEN 2026-10-08)

- **Authority:**
  - the design `docs/discovery_dataset_v1_3_edgar_events_design.md` (`c9daa54b…b269`);
  - the plan `docs/discovery_dataset_v1_3_implementation_plan.md`, §1 (row E-2), §2
    security, §3 interfaces, §4 tests (owner E-2), §5 mutations (prefix E2), §7
    stops, **and the §8 IM-P evidence-sufficiency rule (binding)**.
- **Branch / owned files / dependencies:** exactly the plan §1 row E-2. Anything outside →
  STOP.
- **Constraints:**
  - synthetic fixtures only; never read `data_store/`;
  - no network (an injected/fake transport only);
  - import existing modules read-only;
  - scrubbed credentials plus the network guard for every test process.
- **Deliverable:** one commit. Report the tests, every plan §5 mutation for E-2
  (killed/survived) and the exact SHA.
