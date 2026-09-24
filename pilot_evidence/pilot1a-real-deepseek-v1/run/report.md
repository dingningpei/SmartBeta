# Pilot 1A - Operational End-to-End Harness Validation: Run Report

This is an OPERATIONAL VALIDATION, not a clean scientific discovery experiment. It moves no trust boundary and no scientific authority.

## Operational disposition
- run_id: pilot1a-real-deepseek-v1
- run status: completed_stop
- offline reconstruction: RECONSTRUCTION_EXACT
- post-hoc firewall audit: PASS
- temporal information-flow firewall (section 26b): PASS

## Scientific outcomes

Finding alpha is not a success criterion. Phase-8 ACCEPT carries only the section-3 meaning printed below. No performance threshold, significance statistic or economic-usefulness claim is made.

Section-3 ACCEPT interpretation: Phase-8 ACCEPT is a structural/governance acceptance under the currently sealed DecisionPolicy: the experiment's evidence package is complete, provenance-consistent, search-admissible, holdout-governed and adequately sized. It says nothing about the factor's performance.

DecisionRecords journaled: 3.

### DecisionRecord 92f08135fc7a90f3d5b46196f6411b126952c8d389e3163106eca55d71c791b2

- decision: accept
- reason_codes: []
- ACCEPT interpretation (section 3): Phase-8 ACCEPT is a structural/governance acceptance under the currently sealed DecisionPolicy: the experiment's evidence package is complete, provenance-consistent, search-admissible, holdout-governed and adequately sized. It says nothing about the factor's performance.

### DecisionRecord be084700ada1881191777a71e0742e4a87e5586c8e8d36a2f2f85c480ca70daf

- decision: defer
- reason_codes: [holdout_previously_consumed]
- ACCEPT interpretation (section 3): Phase-8 ACCEPT is a structural/governance acceptance under the currently sealed DecisionPolicy: the experiment's evidence package is complete, provenance-consistent, search-admissible, holdout-governed and adequately sized. It says nothing about the factor's performance.

### DecisionRecord 1b7bc13e5b099f98a3c27d3407ced27133ea097bde5693912c4097b9b4756958

- decision: defer
- reason_codes: [holdout_previously_consumed]
- ACCEPT interpretation (section 3): Phase-8 ACCEPT is a structural/governance acceptance under the currently sealed DecisionPolicy: the experiment's evidence package is complete, provenance-consistent, search-admissible, holdout-governed and adequately sized. It says nothing about the factor's performance.

An ACCEPT must never be reported as any of:
- factor has alpha
- statistically significant factor
- economically useful factor
- scientifically validated factor
- production-worthy factor

## Holdout status (section 4)
- Mechanically governed and hidden from the generator: YES (positive-allowlist projection; HoldoutVisibility.NONE; no DecisionRecord or final outcome reaches the generator).
- Scientifically untouched: NO. Phase 5A computed and committed market-factor and per-name adjusted-return artifacts over the whole window, holdout included (docs/phase5a/gate_b/artifact_a_*, artifact_b_constituent_diagnostics*).
- Model knowledge: a pretrained model may know 2025–2026 market outcomes. Model ignorance of the period is not claimed. The chosen model's published training-data cutoff is recorded against the holdout start (2026-07-01) before the real run (§25).
- Classification: OPERATIONAL PILOT, holdout mechanically governed, not a clean discovery holdout.

## Limitations (section 5)
- the universe is not survivorship-safe (end-of-window membership);
- only 26 names;
- about 252 evaluation dates (258 EOD rows);
- the historical holdout was already observed (§4);
- the manifest lacks per-file hashes (mitigated above);
- US ≠ China;
- DGS3MO/risk-free is unused.

## Certification claim (section 6)

Given the sealed Phase 6–9 architecture at `phase9-complete`, a frozen Pilot-1A configuration, the integrity-verified offline Gate-B fixtures replayed through the trusted Tiingo PIT path as the single admitted `LIVE_RECORDED` input `daily_total_return`, and real invocations of one frozen external model through the Pilot-1A harness, the system executed a governed research loop end to end. Every model invocation was durably recorded (intent before the call, raw response after). Every `GenerationEvent` was durably recorded before normalization, and every materially testable `ResearchProposal` before any empirical evidence for it. Each proposal was bound to the single governed search family. Phase-6 specification validation and data admission, Phase-7 evaluation, and Phase-8 registration, search governance, holdout governance and judgment all ran through their sealed authorities, and holdout-independent `ResearchFeedback` was returned to the generator. The loop ended in a legal next proposal or a typed stop. Throughout, the final holdout was consumed at most once, no reserved holdout evidence or final Phase-8 decision reached the generator, no sealed authority was bypassed or modified, and every authority was reconstructed offline from the persisted artifacts with matching content hashes.

## Nonclaims (section 7)

Pilot 1A does not establish:
- clean scientific discovery;
- an untouched holdout;
- out-of-sample or holdout validity of any factor;
- alpha validity;
- statistical significance (no significance statistic is certified);
- economic usefulness;
- a substantive empirical acceptance criterion (§3);
- production or trading readiness;
- autonomous scientific creativity or optimal hypothesis generation;
- causal discovery;
- deterministic LLM regeneration (no seed or sampling control);
- model ignorance of the sample period;
- semantic-equivalence detection;
- complete adaptive multiple-testing correction;
- survivorship-safe membership;
- provider universality or China A-share applicability;
- total side-channel elimination;
- cross-process resume of any sealed authority.

## Temporal information-flow firewall (section 26b)

The harness temporal information-flow firewall (plan section 26b TF-1..TF-6) derives every generator-visible empirical item's coverage from sealed EvaluationRecord content and requires it to lie inside the config's authorized development interval [is_start, holdout_start).

- temporal firewall status: PASS

FIX B NOT CERTIFIED: the end-to-end temporal information-flow property ('no reserved holdout information reaches the generator') is not guaranteed by the sealed Phase 7 + Phase 9 composition. The Pilot-1A harness temporal firewall (plan section 26b TF-1..TF-6) is a compensating control, not a repair of Phase 9. Until FIX B lands (plan section 26c), any use of the Phase 7 + 9 composition outside a TF-enforcing harness must treat generator-visible robustness aggregates as potentially holdout-dependent.
