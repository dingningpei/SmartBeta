# Phase 6 — Wave 1 Integration Note and Wave-2 Contract Freeze

**Status: integration record + frozen contract sheet.** This document records
the integration of the approved Phase 6 Wave 1 task commits and freezes the
contracts that the Wave 2 tasks (**P6-A** and **P6-E**) may rely on. It does
**not** amend the canonical plan (`phase6-plan.md`), does not cross Barrier 1,
and does not authorize Wave 2 execution.

## 1. Integrated Wave 1 commits

Master was advanced from `6fb771707953b81da47d23179b1d634b2738ea64` by two
`--no-ff` merge commits, following the established repository convention
(task commits preserved, never squashed or rewritten):

| merge | task commit (unchanged) | task |
|---|---|---|
| `4c473537e44132c96099540fffda267458fa2789` | `f148501bf4680c02436c0b67d94f148e465743e1` | P6-B — data-requirement contract |
| `f03f8d32b979ea7fbb3f10a634350032676e56e6` | `54373b5012952a524a251c9ff7ab4be401cef60f` | P6-C — expression AST + parser + validator |

One further integration-amendment commit follows these (the findings in §3).
`smart_beta/spec/__init__.py` was **not** created: per the plan it is owned by
**P6-A**, and Wave 1 modules remain directly importable through the implicit
namespace package until then.

## 2. Frozen Wave-2 contract surface

### 2.1 `smart_beta.spec.requirements` (P6-B)

- `DataRequirement` — frozen dataclass with exactly: `semantic_id`,
  `frequency`, `observation_period`, `units`, `lookback`, `revision_policy`,
  `require_knowledge_date` (default `True`),
  `require_positive_vintage_identity` (default `False`). No field can choose a
  knowledge date, vintage, provider, universe, formation date, or future
  return.
- `DataCapability` — adapter-side dual; every field defaults to the
  fail-closed value.
- `check_satisfiable(requirement, capability) -> SatisfactionResult` and
  `require_satisfiable(...)` — fail closed, with named
  `UnsatisfactionReason` values; unknown history is **not** evidence of
  sufficient history.
- `SatisfactionResult.to_dict()` / `DataRequirement.to_dict()` — deterministic
  provenance records emitted even (especially) on failure.
- Fail-closed error: **`DataRequirementUnsatisfiableError`**, which carries the
  full `SatisfactionResult`.

### 2.2 `smart_beta.spec.expression` (P6-C)

- Nodes (frozen, immutable): `Field`, `Literal`, `Add`, `Sub`, `Mul`, `Div`,
  `Lag`, `Rolling`, `CrossSectional`. Nothing else is a legal node.
- Entry points: `parse_expression`, `from_dict`, `to_dict`,
  `validate_expression`, `referenced_roles`, `canonical_json`,
  `expression_hash`, plus the constructor helpers (`field`, `literal`, `lag`,
  `rolling_mean|sum|std|min|max`, `rank`, `winsorize`, `standardize`, `ratio`,
  `difference`).
- `validate_expression(expr, *, allowed_roles, role_types, role_frequencies,
  vintage_certified_roles, vintage_identity_roles, max_depth)` is the
  declared-context check P6-A must call, passing the alias set derived from
  its own inputs.
- Trailing (never centred/future) rolling windows; non-negative lag only;
  cross-sectional transforms must be outermost (alignment-order rule).
- Bounds: `MAX_DEPTH` 32, `MAX_LAG_PERIODS`/`MAX_ROLLING_WINDOW` 10 000,
  `MAX_TEXT_LENGTH` 4096.

### 2.3 Canonical exception ownership (integration finding A)

`RequirementUnsatisfiableError` was defined **independently by both Wave 1
tasks** as two unrelated classes — an artifact of parallel isolated worktrees,
not of the frozen design. Resolution:

- **The canonical spec-layer `RequirementUnsatisfiableError` is the expression
  stack's** (`smart_beta.spec.expression`, subclass of `ExpressionError`),
  because that name is the one frozen in Phase 6 plan §7 and that module
  declares itself the shared error namespace of the trusted-expression stack.
- The requirement-contract error was **renamed to
  `DataRequirementUnsatisfiableError`**, preserving its fail-closed semantics
  and its recordable `SatisfactionResult` payload. This is a rename only — no
  new error framework, no cross-module import, no change to the Wave 1
  dependency graph (B and C still import neither each other nor `smart_beta.pit`
  nor `smart_beta.vendors`).
- **Rule for P6-A / P6-D / P6-E / P6-F:** `smart_beta.spec` must re-export
  exactly **one** `RequirementUnsatisfiableError` (the expression-stack one).
  A provider/capability failure surfaces as `DataRequirementUnsatisfiableError`
  and must be translated explicitly (attaching its `SatisfactionResult` as
  provenance) where a canonical stack-level error is required — never by
  silently assuming the two names are interchangeable.

### 2.4 Semantic identity vs expression role (integration finding B)

These are **deliberately different vocabularies** and must not be conflated:

| concept | owner | vocabulary | example |
|---|---|---|---|
| *semantic identity* | `DataRequirement.semantic_id` | vendor-free economic quantity, broader grammar (letters, digits, `_`, `.`; may begin uppercase) | `turnover`, `market_cap`, `Return.Total` |
| *expression role* (alias) | `FactorSpec.inputs` ↔ `expression.Field(role)` | frozen strict grammar `^[a-z][a-z0-9_]{0,62}$` | `turnover_short` |

**Composition rule P6-A must implement:** every `Field` role appearing in an
expression must be one of the FactorSpec's declared input **aliases**; each
alias is *bound* to exactly one `DataRequirement` that supplies the semantic
identity, frequency and PIT/vintage admissibility. Validation composes as
`validate_expression(expr, allowed_roles=<aliases>, role_types=<alias→numeric>,
role_frequencies=<alias→bound requirement frequency>)`, with
`vintage_certified_roles`/`vintage_identity_roles` derived from the bound
requirements. A role that violates the strict role grammar is rejected by
P6-C; a vendor-named semantic identity is rejected by P6-B. Do **not** tighten
`semantic_id` to the role grammar.

### 2.5 Unchanged trust constraints carried into Wave 2

- No vendor names anywhere in the spec layer; provider quirks stay outside it.
- No trusted temporal operation is expressible: no `eval`/`exec`/`compile`,
  no callables, no provider/network access, no knowledge-date or vintage
  selection, no alignment/formation/universe construction, no future or
  negative-lag reference, no unbounded recursion.
- Negative certification stays representable as a typed, recordable failure.

## 3. Integration finding C (P6-C documentation)

The module documentation claimed a cyclic **or over-deep** tree raises
`AmbiguousExpressionError`, while the implementation raises the plain
`InvalidExpressionError` for over-depth (cycles do raise
`AmbiguousExpressionError`). The frozen plan mandates a *typed* error taxonomy
and fail-closed behaviour but does **not** mandate which member applies to an
over-deep graph, so the **documentation was corrected to match the
implementation**; fail-closed runtime behaviour was left untouched. Both
members are in the §7 frozen taxonomy. See
`tests/test_spec_wave1_integration.py::test_over_deep_structure_raises_invalid_expression_error`.
(If a later task wants over-depth to raise `AmbiguousExpressionError` — a
subclass, so equally fail-closed — that is a deliberate decision to make and
record, not a silent drift.)

## 4. Barrier status

Wave 1 delivers B and C only. **Barrier 1 is NOT eligible**: it requires
Wave 2 (`FactorSpec` schema/serialization **P6-A** and transform library
**P6-E**) to exist and E to be independently passing. Nothing in this note
should be read as a Barrier 1 pass, and no Barrier 1 claim is made.

## 5. Worker-launch mandate for the next wave

From Phase 6 Wave 2 onward, **every Pi implementation worker must be launched
through Herdr** (`/opt/homebrew/bin/herdr`, v0.8.0) and must remain visible and
manageable there. Direct hidden `pi -p` child-process launches are forbidden
unless the user explicitly authorizes them for a specific run. Phase 6 Wave 1
(direct `pi --print`) is grandfathered. The exact Herdr launch form must be
confirmed read-only before the first Herdr-launched wave, not guessed.
