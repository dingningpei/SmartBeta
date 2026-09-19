# GATE-A-2 compromised artifacts (preserved, NOT Gate A evidence)

**These files are NOT certified Gate A output.** They are preserved
byte-for-byte for the historical record only.

These are the real Artifacts A/B/C produced by running the real,
live-recorded AAPL/MSFT/JPM fixtures (2026-06-15..2026-09-15) through
`run_capm_pilot` with **unmodified `DEFAULT_SETTINGS`**
(`bottom_mcap_exclude_pct = 0.30`), before Finding GATE-A-2 was
diagnosed.

* `universe_count = 2` on every one of the 64 dates: JPM — despite real
  multi-million-share daily volume and a ~$0.86-0.90T market
  capitalization — is marked non-tradable on 64/64 dates.
* Root cause (see `worker_tasks/phase5a/phase5a-plan.md`'s "Gate-A-only
  settings amendment"): `DEFAULT_SETTINGS.bottom_mcap_exclude_pct = 0.30`
  drives a per-date cross-sectional quantile screen that the smallest
  member of any 2-3 name cross-section can never mathematically satisfy,
  regardless of its real economic size. This is a configuration defect
  in how Gate A's orchestration reused a CH-3-tuned default, not a defect
  in the underlying vendor data, the CAPM computation, or the risk-free
  provider.
* The underlying live Tiingo fixtures these artifacts were computed from
  are genuine, valid, and unaffected by this finding — see
  `tests/fixtures/tiingo/phase5a_gate_a/manifest.json`.

The certified Gate A artifacts (produced with the amended,
Gate-A-only `bottom_mcap_exclude_pct = 0.0` settings, from the exact
same underlying fixtures, no new Tiingo request) live at
`docs/phase5a/gate_a/artifact_*.{csv,json}` (the normal artifact paths,
one directory up).
