# Discovery Dataset v1.1 — D8 Reserve Firewall and Reserve ARTIFACT Clarification (NORMATIVE)

- **Status:** normative clarification of the D8 HOLD items, decided by the
  user on 2026-09-29 across three resolutions: B1–B3; the footprint consequence
  (Option A); and column coverage.
- **Documentation only:** it changes no completed stage (D1–D7b, D-SC) and no
  sealed phase. Phase 10 is used as it is and is **not** modified.
- **It clarifies, without modifying:**
  - `docs/discovery_dataset_v1_contract.md` §§12, 14, 16 (gate 12), 20 (D8) —
    `d03f35549777e5a94046f06dd0621acfc3a1dedb7a4b40adc55fdae911fa80e3`;
  - `worker_tasks/phase10/phase10-plan.md` §§5.1, 5.2, 5.2a, 5.4, 6.2, 6.5,
    13.1 — `e02d5f7f08aca22dc94e6f6a694bd640e89036d577bfc82c4a365131e11b1b7a`;
  - `docs/phase10_confirmatory_evidence_certification.md` —
    `c8f68029ecf6d3fd59781359094eb3a29aa4eadc6bb5b17db8933594ad4219ed`.

## 1. Scope

D8 has exactly two parts:
- **(i)** a prospective, general Dataset-v1 **development accessor** that
  rejects any request touching a session ≥ 2023-07-03 before any row is
  materialized;
- **(ii)** registration of the already-possessed D3 reserve package as one
  Phase-10 Knowledge-PIT `ARTIFACT`.

**D6, D7a and D7b are not migrated.** Their certified local firewalls stay as
they are.

D8 does not evaluate, confirm, compute T0/TS or E_S/E_L, or inspect reserve
outcomes.

## 2. Knowledge-PIT log (B1)

- **The single research-environment K log** (Phase-10 §13.1) is
  `data_store/knowledge_pit/knowledge.jsonl`, outside Git. It is not private to
  Dataset v1, D8 or Campaign 1. Future programs in this environment use the
  same log.
- **D8's ARTIFACT is its genesis record:** `seq = 0`, `prev_hash` = 64 zeros,
  `program_id = None`. The artifact predates and is independent of any Phase-9
  program. Future program records carry their own program id.
- **If the production log already contains records when D8 is implemented or
  registered,** and none is a matching D8 artifact, the result is
  **D8 IMPLEMENTATION HOLD**. D8 never creates a competing log, overwrites a
  log or forces `seq = 0`.

## 3. The ARTIFACT

- **Identity of the package:** the whole D3 reserve package
  `reserve_sealed/n-f874d3ecb61374d4/` (`observations`, `obs_flags`,
  `ca_events`). D8 does not filter, copy, clean or shrink it.
  - D5's reserve `day_flags` are derived QC output, not source evidence.
  - `raw/` is not registered separately.
- **`packaging_hash`:** `content_hash` (Phase-10 canonical JSON, then SHA-256)
  of the mapping from each of the three reserve file paths to the SHA-256
  already bound in D3's `build.json`:

  | Path | SHA-256 |
  |---|---|
  | `reserve_sealed/n-f874d3ecb61374d4/ca_events.parquet` | `43c3bd8537c2aa59205b4d2b603b98a61eaec29cb71f44daeb4ab389c4fde2d7` |
  | `reserve_sealed/n-f874d3ecb61374d4/obs_flags.parquet` | `3e955395f8c1eb21ae9d192debbe2bfa2c8e6cdf3a6fe9a404759dc8686a9225` |
  | `reserve_sealed/n-f874d3ecb61374d4/observations.parquet` | `f7621c9985eab6eeaed18d40f82d33ad37b0115f7155fcb41e436446f1687586` |

  No economic value is reread to build it. File integrity is checked by
  streaming the bytes through SHA-256, which is a packaging access, disclosed
  in §7.
- **`sealed = true`,** with this operational meaning only: from the D8
  registration boundary onward, the artifact is designated sealed reserve
  evidence and is not authorized for development or evaluation access, except
  through a separately authorized confirmation procedure.
  - It does **not** mean never possessed, parsed, machine-read, structurally
    inspected, value-processed or seen by a human.
  - It does **not** mean historically unexposed.
- **`source_label`** is `discovery_dataset_v1:reserve:n-f874d3ecb61374d4`.
  It is descriptive only and never identity.
- **`available_from`** is the attested date on which the evidence became
  available to this environment: the **earliest** UTC date of
  `retrieved_at_utc` over the qualifying acquisition-manifest attempts (spec
  §6.4). That covers the Starter smoke batch `b-10cd8b2e2ef11a98` and the
  production batch `b-c21b48190918f21f`. It is never the reserve boundary, the
  D3 build date, the registration date or the current time, unless the
  manifest evidence happens to equal one of them.

## 4. Footprint (B3; Option A)

The footprint follows Phase-10 §6 exactly, computed by the unmodified
`smart_beta.science.footprint.footprint_from_panel`.

**SecurityMap:**
- It is flat and hashed: `security_key → "SEC:US:" + security_key`.
- It covers **only** D4 `INTERVAL_ASSIGNED` keys that appear exactly once in
  the bound D4 artifact `i-a21f1e46621dee94`.
- A reserve row (ticker, date) resolves to key K only if K is such a key, K's
  ticker equals the row's ticker, and `listed_start ≤ date ≤ listed_end`,
  with exactly one match.
- Every other row keeps its raw ticker as the subject, which the map does not
  contain, so it is unresolved.
- There is no ticker fallback, no guessed continuity and no mapping of an
  ambiguous, unreachable, unrequestable or no-observation identity.

**VariableMap:** exact D3 column names, frozen and hashed.

| Columns | `derived_variable` | ObservationKind |
|---|---|---|
| `open, high, low, close, adjOpen, adjHigh, adjLow, adjClose` | `PRICE_FIELD` | `PRICE_LEVEL` (§6.2: "open, high, low, close, adjusted or not") |
| `volume, adjVolume` | `ACTIVITY_FIELD` | `TRADING_ACTIVITY` |
| `divCash, splitFactor` | `RETURN_1D` | `PRICE_CHANGE` (§6.2: "including that session's distribution and corporate-action adjustment") |

**Provenance columns.** `raw_row_index` and `raw_sha256` identify the vendor
record and its bytes. They are not observations of any subject on any session,
so no `ObservationKind` applies. They are excluded from the observation frame,
and their provenance stays bound through `packaging_hash`. No fake kind is
created.

**Other D3 schema changes.** Any additional economic column fails closed
(`unmapped_column` → undeterminable). A schema that differs from the frozen
16-column D3 schema is a D8 HOLD.

**Components:**
- `obs_flags` rows (`ticker, date, raw_row_index, flag`) are row-level quality
  annotations: D3 §5 says they depend "on nothing outside the row and the
  calendar". They add no subject, date or kind.
- `ca_events` rows are produced by the Phase-4B mapper from the same raw rows'
  `divCash`/`splitFactor`, with `effective_date = row date`. Their domain is
  (ticker, row date) × `PRICE_CHANGE`, which the mapping above already
  represents.
- This is checked mechanically at registration from key columns only: the
  (ticker, date) keys of `obs_flags` and the (`stock_id`, `effective_date`)
  keys of `ca_events` must be a subset of the `observations` keys. Otherwise
  the result is a D8 HOLD.

**Calendar and MarketSeriesMap:**
- Calendar: `build_nyse_calendar(2015-01-02, 2027-12-31)`, the existing
  certified Dataset-v1 calendar. Phase-10 requires it; without it the result
  is undeterminable.
- MarketSeriesMap: empty (there are no `MKT:` subjects).

**Frozen Phase-10 behavior (accepted):** any unresolved entry makes the
**whole** footprint undeterminable (`determinable = false`, `blocks = ()`,
`unresolved` non-empty).
- Planning evidence from D4 metadata shows reserve-active identities with no
  admissible assigned interval, so the expected honest result is
  **UNDETERMINABLE**. The implementation computes it and does not assume it.
- Consequence, accepted: this historical artifact **cannot support a
  determinable Phase-10 confirmation consumption** under the current frozen
  footprint and identity semantics. Its overlap with anything is
  `UNDETERMINABLE`, so its role is `UNKNOWN_EXPOSURE` or weaker.
- D8 does not rescue this. An undeterminable footprint is a valid D8
  registration outcome, not a failure.
- A narrower identity-certified package would be a separate future
  authorization. It would keep every provenance item and disclosure, and D8
  makes no claim that it would qualify.

## 5. Metadata-only footprint read (the one authorized reserve read)

- **Purpose:** only to compute the ARTIFACT footprint.
- **Materialized:** the reserve `observations` columns `ticker` and `date`;
  the `obs_flags` `ticker` and `date`; the `ca_events` `stock_id` and
  `effective_date`; plus parquet schema metadata.
- **Never materialized:** any price, volume, dividend, split, flag value,
  return or QC outcome.
- **Variable coverage** comes from schema metadata and the frozen VariableMap.
  **Subject coverage** comes from the projected keys and the D4 intervals.
- Under Phase-10 §6.5 this read observes no outcome, so **no ACCESS record** is
  created.
- The read is recorded in the ARTIFACT payload provenance: purpose, projected
  columns, artifact identity, code and spec hashes, with the time given by the
  envelope `recorded_at`.
- It does not establish non-exposure, erase earlier reads, improve
  `UNKNOWN_EXPOSURE` or authorize future reserve reads.

## 6. Evidence role (B2-A)

- **No declarations:** D8 appends **no** `ACCESS` backfill and **no** HUMAN
  `EXPOSED` or `NOT_EXPOSED` declaration.
  - The Phase-10 ACCESS writers (P10-H, P10-I) remain the only ones.
  - Machine processing, aggregate counts and structural measurements are not
    turned into a HUMAN EXPOSED claim. A declaration is a factual human
    attestation that D8 neither makes nor pre-fills.
- **Current honest role:** `UNKNOWN_EXPOSURE`, or the weaker result the frozen
  machinery gives. D8 never upgrades it.
- **Absence of an ACCESS record is not proof of no access.**

## 7. Known pre-registration history (permanent disclosure; not exhaustive)

| Stage | Interaction |
|---|---|
| D2 | acquired raw source bodies spanning development and reserve (both batches) |
| D3 | parsed every raw row, including reserve values; created the reserve partition |
| D5 (gate 10) | rebuild re-read raw data, including reserve values |
| D5 (L1) | reserve-period OHLC and value checks and CORP_ACTION_SUSPECT calculations |
| D5 (outputs) | persisted reserve-derived `day_flags.parquet` and reserve counts in `qc_report.json` |
| D-SC | read reserve-period structural fields, flags, dates and names; produced the structural eligibility measurements disclosed in D6 clarification H1 |
| D6, D7a, D7b | no production reserve read (file-open audits) |
| Contract §12 | LLM pre-training exposure disclosed |
| D8 | packaging hash verification (bytes) and the one metadata-only key-column read (§5) |

This list does not prove there were no other historical reads.

## 7a. Test and production separation

- **Tests:** tmp logs only. A test fails closed if it is pointed at the
  production path, including through a symlink or a relative path.
- **Dry run:** runs against a tmp copy only.
- **Production:** exactly one intentional append, under `KnowledgeLog` lock
  semantics, and only under a separate authorization.
- **Reruns:** a rerun with an identical artifact is a no-op that returns the
  existing record.
  - A different `packaging_hash`, or any payload or footprint difference for
    the same `source_label`, fails closed.
  - Nothing is ever overwritten, and a `TruncatedTail` is never repaired.

## 8. Nonclaims

The ARTIFACT record establishes:
- the package identity;
- the packaging metadata;
- the footprint under the frozen mapping (currently expected
  **UNDETERMINABLE**);
- the prospective sealed governance state;
- the availability provenance.

It does **not** establish:
- historical non-exposure, or the absence of machine or human reads;
- confirmation eligibility, `CONFIRMATION_HISTORICAL_RECORDED`, or G1/G2/G3
  quality;
- hypothesis independence.

Also not claimed:
- Campaign-1 readiness by itself;
- alpha, predictive validity or production readiness.

All Dataset-v1 identity, delisting and survivorship nonclaims remain.
