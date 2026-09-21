"""Trusted expression evaluator for the declarative factor layer (P6-D).

This module is the *execution* half of the Phase 6 specification layer. It
consumes an already-constructed, already-validated
:class:`~smart_beta.spec.factor_spec.FactorSpec` and a mapping of
**already-supplied, already-aligned, trusted** input value frames (one per
declared expression alias), walks the frozen expression AST, dispatches every
node to the numeric transform library of
:mod:`smart_beta.spec.transforms` (P6-E), and returns the factor values in the
frozen long-panel shape ``(date, stock_id, value)`` matching
:data:`smart_beta.data.schema.FACTOR_PANEL_SCHEMA`, together with a
deterministic provenance/diagnostics record and a deterministic canonical
hash.

Scope boundary (frozen)
-----------------------

The evaluator **orchestrates** evaluation; it is not a second transform
library and not a data-selection engine:

* it recurses over the already-approved P6-C AST and dispatches each node to
  :func:`smart_beta.spec.transforms.apply_transform` (deliberately one step);
  it never re-implements transform mathematics;
* it resolves a :class:`~smart_beta.spec.expression.Field` role **only**
  against explicitly supplied trusted input bound to that declared
  :class:`FactorSpec` alias. A role that is absent, undeclared, ambiguous,
  duplicated, of the wrong type, or on an incompatible grid fails closed with
  a typed error; the evaluator never searches for replacement data, never
  falls back to another role, and never looks anything up;
* it never silently aligns mismatched inputs: no inner/outer join, no
  forward/back fill, no reindex, no truncation or expansion of dates, and no
  universe reconciliation. Mismatched trusted inputs fail closed;
* it honours the declared ``missing_policy`` (``propagate`` / ``drop``)
  deterministically;
* it uses P6-E's reshaping helpers (:func:`normalize_value_frame`,
  :func:`value_frame_to_long`) rather than re-implementing panel reshaping.

Trust boundary (Phase 6 plan, section 8; P6-D prohibitions)
--------------------------------------------------------------

The evaluator has **no temporal authority** and makes no provider choice. In
particular it never:

* queries a provider, imports a vendor adapter, or chooses a provider;
* performs as-of lookup, chooses/infers a ``knowledge_date``, chooses/infers
  a publication vintage, searches for "latest available" records, resolves
  revision history, or certifies vintage identity;
* silently substitutes missing data, constructs the investable universe,
  performs formation-date or return alignment, fetches future returns, or
  performs portfolio/benchmark construction;
* executes arbitrary Python -- there is no ``eval``/``exec``/``compile`` of
  user-supplied code and no dynamic import of code described by a
  ``FactorSpec``.

If required input has not already been supplied through the trusted boundary,
evaluation fails closed. The architecture is *trusted upstream PIT selection
-> already-certified input -> evaluator*, never *raw history -> evaluator
deciding what was known*.

Errors (frozen taxonomy)
------------------------

Every failure raised here is a member of the existing expression-stack error
namespace: :class:`EvaluatorError` subclasses
:class:`smart_beta.spec.expression.EvaluationError`. No unrelated exception
framework is introduced. The evaluator deliberately does **not** conflate the
canonical expression-stack
:class:`~smart_beta.spec.expression.RequirementUnsatisfiableError` with the
requirement-contract
:class:`~smart_beta.spec.requirements.DataRequirementUnsatisfiableError`: a
provider/capability failure is a different object carrying a
:class:`~smart_beta.spec.requirements.SatisfactionResult`. The advisory helper
:meth:`RequirementFailureError.from_unsatisfiable` translates such an upstream
failure explicitly and preserves that result as provenance.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pandas as pd

from smart_beta.data.schema import (
    DATE_COL,
    FACTOR_PANEL_SCHEMA,
    STOCK_COL,
    VALUE_COL,
)
from smart_beta.spec.expression import (
    EvaluationError,
    Expr,
    Field,
    expression_hash as _expression_hash,
)
from smart_beta.spec.factor_spec import FactorSpec, MissingPolicy
from smart_beta.spec.requirements import (
    DataRequirementUnsatisfiableError,
    SatisfactionResult,
)
from smart_beta.spec.transforms import (
    TransformError,
    apply_transform,
    normalize_value_frame,
    value_frame_to_long,
)

__all__ = [
    # typed error taxonomy
    "EvaluatorError",
    "InputBoundaryError",
    "MissingInputError",
    "UndeclaredInputError",
    "DuplicateInputError",
    "AmbiguousInputError",
    "InputTypeError",
    "InputShapeError",
    "InputGridMismatchError",
    "OutputShapingError",
    "RequirementFailureError",
    # result / provenance
    "InputDiagnostic",
    "EvaluationResult",
    # execution
    "evaluate",
    # canonical form
    "canonical_json",
    "evaluation_hash",
]


# ---------------------------------------------------------------------------
# typed error taxonomy (frozen section 7 namespace)
# ---------------------------------------------------------------------------
class EvaluatorError(EvaluationError):
    """Base class for every typed failure raised by the trusted evaluator.

    Subclasses the shared :class:`~smart_beta.spec.expression.EvaluationError`
    so the whole trusted-expression stack keeps one error namespace.
    ``context`` carries deterministic, recordable provenance that survives a
    failed evaluation (Phase 6 plan, section 9, item 7).
    """

    def __init__(
        self, message: str, *, context: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.context: dict[str, Any] = dict(context) if context else {}


class InputBoundaryError(EvaluatorError):
    """A trusted input violates the evaluator's fail-closed input boundary."""


class MissingInputError(InputBoundaryError):
    """A role referenced by the expression was not supplied as trusted input."""


class UndeclaredInputError(InputBoundaryError):
    """Trusted input was supplied for a role the spec does not declare."""


class DuplicateInputError(InputBoundaryError):
    """A role was supplied more than once (ambiguous, never disambiguated)."""


class AmbiguousInputError(InputBoundaryError):
    """The trusted-input collection itself is malformed/ambiguous."""


class InputTypeError(InputBoundaryError):
    """A supplied input is not a value frame (wrong type)."""


class InputShapeError(InputBoundaryError):
    """A supplied value frame is malformed (duplicate keys, non-numeric, ...)."""


class InputGridMismatchError(InputBoundaryError):
    """Supplied trusted inputs are not on exactly the same observation grid."""


class OutputShapingError(EvaluatorError):
    """The evaluated values cannot be shaped into the frozen factor panel."""


class RequirementFailureError(EvaluatorError):
    """Explicit translation of an upstream provider/capability requirement failure.

    The upstream error is a
    :class:`~smart_beta.spec.requirements.DataRequirementUnsatisfiableError`,
    which is deliberately *not* the canonical expression-stack
    :class:`~smart_beta.spec.expression.RequirementUnsatisfiableError`. This
    class keeps the two distinct while preserving the recordable
    :class:`~smart_beta.spec.requirements.SatisfactionResult` as provenance.
    """

    def __init__(self, result: SatisfactionResult) -> None:
        if not isinstance(result, SatisfactionResult):
            raise EvaluatorError(
                "RequirementFailureError requires a SatisfactionResult, got "
                f"{type(result).__name__}"
            )
        self.result = result
        reasons = ", ".join(reason.value for reason in result.reasons)
        super().__init__(
            f"input requirement {result.requirement.semantic_id!r} is "
            f"unsatisfiable: {reasons}",
            context={
                "requirement": result.requirement.to_dict(),
                "capability": result.capability.to_dict(),
                "reasons": [reason.value for reason in result.reasons],
            },
        )

    @classmethod
    def from_unsatisfiable(
        cls, exc: DataRequirementUnsatisfiableError
    ) -> "RequirementFailureError":
        """Translate an upstream :class:`DataRequirementUnsatisfiableError`.

        This is a *deliberate*, explicit translation: the distinct upstream
        class is checked, and its ``SatisfactionResult`` payload is carried
        forward verbatim. It never assumes the two requirement-error names
        are interchangeable.
        """
        if not isinstance(exc, DataRequirementUnsatisfiableError):
            raise EvaluatorError(
                "from_unsatisfiable requires a "
                "DataRequirementUnsatisfiableError, got "
                f"{type(exc).__name__}"
            )
        return cls(exc.result)


# ---------------------------------------------------------------------------
# provenance / diagnostics
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class InputDiagnostic:
    """Deterministic per-role diagnostics of the trusted input actually used."""

    alias: str
    semantic_id: str
    frequency: str
    n_dates: int
    n_stocks: int
    n_missing: int

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe, deterministic provenance record."""
        return {
            "alias": self.alias,
            "semantic_id": self.semantic_id,
            "frequency": self.frequency,
            "n_dates": self.n_dates,
            "n_stocks": self.n_stocks,
            "n_missing": self.n_missing,
        }


@dataclass(frozen=True, eq=False)
class EvaluationResult:
    """The frozen factor values plus deterministic provenance/diagnostics.

    ``panel`` is the long ``(date, stock_id, value)`` factor panel matching
    :data:`~smart_beta.data.schema.FACTOR_PANEL_SCHEMA`. The remaining fields
    and :meth:`to_dict` are the deterministic provenance record; the
    deterministic content hash is :attr:`content_hash`.
    """

    factor_id: str
    factor_version: str
    expression_hash: str
    missing_policy: str
    frequency: str
    roles: tuple[str, ...]
    diagnostics: tuple[InputDiagnostic, ...]
    panel: pd.DataFrame

    @property
    def content_hash(self) -> str:
        """Deterministic SHA-256 canonical hash of values + provenance."""
        return evaluation_hash(self)

    def to_dict(self) -> dict[str, Any]:
        """Full deterministic provenance record (values + diagnostics + hash)."""
        record = _content_dict(self)
        record["content_hash"] = self.content_hash
        return record


# ---------------------------------------------------------------------------
# input boundary
# ---------------------------------------------------------------------------
def _input_pairs(inputs: Any) -> list[tuple[Any, Any]]:
    """Normalise the trusted-input collection into an ordered pair list.

    Accepts a mapping of ``role -> frame`` or a sequence of
    ``(role, frame)`` pairs. Anything else -- including a bare string, a
    scalar, or a malformed entry -- fails closed as an ambiguous/ill-typed
    input boundary.
    """
    if isinstance(inputs, Mapping):
        return list(inputs.items())
    if isinstance(inputs, Sequence) and not isinstance(
        inputs, (str, bytes, bytearray)
    ):
        pairs: list[tuple[Any, Any]] = []
        for entry in inputs:
            if (
                isinstance(entry, (str, bytes, bytearray))
                or not isinstance(entry, Sequence)
                or len(entry) != 2
            ):
                raise AmbiguousInputError(
                    "trusted inputs must be a mapping of role -> frame or a "
                    "sequence of (role, frame) pairs; got a malformed entry "
                    f"{entry!r}"
                )
            pairs.append((entry[0], entry[1]))
        return pairs
    raise InputTypeError(
        "trusted inputs must be a mapping of role -> value frame or a "
        f"sequence of (role, frame) pairs, got {type(inputs).__name__}"
    )


def _normalized_inputs(
    spec: FactorSpec, inputs: Any
) -> dict[str, pd.DataFrame]:
    """Validate the input boundary and normalize each supplied value frame."""
    declared = set(spec.aliases)
    frames: dict[str, pd.DataFrame] = {}
    for alias, frame in _input_pairs(inputs):
        if not isinstance(alias, str):
            raise InputTypeError(
                "every trusted-input role must be a string alias, got "
                f"{type(alias).__name__}"
            )
        if alias not in declared:
            raise UndeclaredInputError(
                f"trusted input supplied for role {alias!r}, which the spec "
                f"{spec.id!r} does not declare (declared: {sorted(declared)})",
                context={"factor_id": spec.id, "role": alias},
            )
        if alias in frames:
            raise DuplicateInputError(
                f"trusted input for role {alias!r} was supplied more than "
                "once; a role must be bound to exactly one trusted frame",
                context={"factor_id": spec.id, "role": alias},
            )
        if not isinstance(frame, pd.DataFrame):
            raise InputTypeError(
                f"trusted input for role {alias!r} must be a pandas "
                f"DataFrame value frame, got {type(frame).__name__}",
                context={"factor_id": spec.id, "role": alias},
            )
        try:
            normalized = normalize_value_frame(
                frame, name=f"trusted input role {alias!r}"
            )
        except TransformError as exc:
            raise InputShapeError(
                f"trusted input for role {alias!r} is not a well-formed "
                f"value frame: {exc}",
                context={"factor_id": spec.id, "role": alias},
            ) from exc
        if not pd.api.types.is_datetime64_any_dtype(normalized.index.dtype):
            raise InputShapeError(
                f"trusted input for role {alias!r} must carry observation "
                "dates on its index (the trusted boundary owns temporal "
                "selection); got index dtype "
                f"{normalized.index.dtype}",
                context={"factor_id": spec.id, "role": alias},
            )
        if not pd.api.types.is_string_dtype(normalized.columns.dtype):
            raise InputShapeError(
                f"trusted input for role {alias!r} must carry stock "
                f"identifiers as its columns, got dtype "
                f"{normalized.columns.dtype}",
                context={"factor_id": spec.id, "role": alias},
            )
        frames[alias] = normalized
    return frames


def _ensure_common_grid(
    frames: Mapping[str, pd.DataFrame], factor_id: str
) -> None:
    """Fail closed unless every supplied frame shares one exact grid.

    The evaluator never aligns frames: equality of the observation-date index
    and of the stock-identifier columns is required. A mismatch is a boundary
    defect, not something to reconcile by joining, filling, reindexing, or
    truncating.
    """
    aliases = sorted(frames)
    if len(aliases) < 2:
        return
    reference_alias = aliases[0]
    reference = frames[reference_alias]
    for alias in aliases[1:]:
        frame = frames[alias]
        if not frame.index.equals(reference.index):
            raise InputGridMismatchError(
                f"trusted inputs {reference_alias!r} and {alias!r} are on "
                "different observation-date grids; the evaluator never "
                "aligns, joins, fills, or reindexes mismatched inputs (the "
                "trusted boundary owns alignment)",
                context={
                    "factor_id": factor_id,
                    "roles": [reference_alias, alias],
                    "axis": "date",
                },
            )
        if not frame.columns.equals(reference.columns):
            raise InputGridMismatchError(
                f"trusted inputs {reference_alias!r} and {alias!r} cover "
                "different stock identifiers; the evaluator never "
                "reconciles universes (the trusted boundary owns alignment)",
                context={
                    "factor_id": factor_id,
                    "roles": [reference_alias, alias],
                    "axis": "stock",
                },
            )


# ---------------------------------------------------------------------------
# AST orchestration
# ---------------------------------------------------------------------------
def _evaluate_expression(
    expression: Expr, frames: Mapping[str, pd.DataFrame]
) -> Any:
    """Walk the frozen AST iteratively and dispatch each node to P6-E.

    Evaluation is a post-order, non-recursive traversal (the expression depth
    is already bounded by the P6-C validator). A :class:`Field` is resolved
    only against the supplied trusted frames; every other node is handed to
    :func:`smart_beta.spec.transforms.apply_transform` with its already
    evaluated operands. Transform mathematics and one-step semantics stay in
    P6-E.
    """
    results: dict[int, Any] = {}
    stack: list[tuple[str, Expr]] = [("enter", expression)]
    while stack:
        action, node = stack.pop()
        if action == "exit":
            operands = tuple(results[id(child)] for child in node.children())
            results[id(node)] = apply_transform(node, operands)
            continue
        if isinstance(node, Field):
            if node.role not in frames:
                raise MissingInputError(
                    f"the expression references role {node.role!r} for which "
                    "no trusted input was supplied",
                    context={"role": node.role, "supplied_roles": sorted(frames)},
                )
            results[id(node)] = frames[node.role]
            continue
        stack.append(("exit", node))
        for child in reversed(node.children()):
            stack.append(("enter", child))
    return results[id(expression)]


# ---------------------------------------------------------------------------
# output shaping
# ---------------------------------------------------------------------------
def _shape_panel(panel: pd.DataFrame) -> pd.DataFrame:
    """Return the frozen ``(date, stock_id, value)`` factor panel.

    Ordering is canonical (by date then stock identifier) and the column
    dtypes match :data:`~smart_beta.data.schema.FACTOR_PANEL_SCHEMA`, so the
    canonical hash is input-order independent.
    """
    if not isinstance(panel, pd.DataFrame):
        raise OutputShapingError(
            f"evaluated values must be a value frame, got {type(panel).__name__}"
        )
    missing = [c for c in (DATE_COL, STOCK_COL, VALUE_COL) if c not in panel.columns]
    if missing:
        raise OutputShapingError(
            f"evaluated panel is missing required columns {missing}"
        )
    shaped = panel.loc[:, [DATE_COL, STOCK_COL, VALUE_COL]].copy()
    shaped[DATE_COL] = pd.to_datetime(shaped[DATE_COL])
    shaped[STOCK_COL] = shaped[STOCK_COL].astype("string")
    shaped[VALUE_COL] = shaped[VALUE_COL].astype("float64")
    shaped = shaped.sort_values(
        [DATE_COL, STOCK_COL], kind="mergesort"
    ).reset_index(drop=True)
    try:
        FACTOR_PANEL_SCHEMA.validate(shaped, name="factor panel")
    except ValueError as exc:  # SchemaError
        raise OutputShapingError(
            f"evaluated values do not match the frozen factor-panel schema: {exc}"
        ) from exc
    return shaped


def _diagnostics(
    spec: FactorSpec, frames: Mapping[str, pd.DataFrame]
) -> tuple[InputDiagnostic, ...]:
    """Deterministic per-role diagnostics, sorted by alias."""
    by_alias = {item.alias: item for item in spec.inputs}
    diagnostics: list[InputDiagnostic] = []
    for alias in sorted(frames):
        frame = frames[alias]
        requirement = by_alias[alias].requirement
        diagnostics.append(
            InputDiagnostic(
                alias=alias,
                semantic_id=requirement.semantic_id,
                frequency=requirement.frequency.value,
                n_dates=int(frame.shape[0]),
                n_stocks=int(frame.shape[1]),
                n_missing=int(frame.isna().to_numpy().sum()),
            )
        )
    return tuple(diagnostics)


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------
def evaluate(spec: FactorSpec, inputs: Any) -> EvaluationResult:
    """Evaluate ``spec`` over already-supplied, already-aligned trusted inputs.

    Parameters
    ----------
    spec:
        A validated :class:`~smart_beta.spec.factor_spec.FactorSpec`. Its
        expression and alias-to-requirement bindings are already frozen; the
        evaluator never re-designs them.
    inputs:
        The trusted inputs, as a mapping of declared alias -> value frame (a
        :class:`pandas.DataFrame` indexed by observation date with one column
        per stock identifier), or an ordered sequence of ``(alias, frame)``
        pairs. Every role referenced by the expression must be present; a
        supplied role must be declared and supplied exactly once; all
        supplied frames must share one exact grid.

    Returns
    -------
    EvaluationResult
        Factor values in the frozen long-panel shape plus deterministic
        provenance/diagnostics and a deterministic canonical hash.

    Raises
    ------
    EvaluatorError
        A typed member of the frozen taxonomy. The trusted input boundary is
        fail-closed, and no substitution, fallback, alignment, or provider
        lookup is ever attempted.
    """
    if not isinstance(spec, FactorSpec):
        raise EvaluatorError(
            f"evaluate requires a FactorSpec, got {type(spec).__name__}"
        )

    frames = _normalized_inputs(spec, inputs)

    referenced = spec.referenced_roles
    missing = [role for role in referenced if role not in frames]
    if missing:
        raise MissingInputError(
            f"the expression references declared role(s) {missing} for which "
            "no trusted input was supplied; the evaluator fails closed rather "
            "than searching for or substituting data",
            context={
                "factor_id": spec.id,
                "missing_roles": missing,
                "supplied_roles": sorted(frames),
            },
        )

    _ensure_common_grid(frames, spec.id)

    if not referenced:
        raise EvaluatorError(
            "the expression references no declared field, so no output grid "
            "can be established; a factor must consume at least one declared "
            "input (the evaluator never infers a universe)",
            context={"factor_id": spec.id},
        )

    values = _evaluate_expression(spec.expression, frames)

    if not isinstance(values, pd.DataFrame):
        raise OutputShapingError(
            f"expression evaluated to a constant {values!r} rather than a "
            "value frame; a factor must produce per-observation values over "
            "the trusted input grid",
            context={"factor_id": spec.id},
        )

    try:
        values = normalize_value_frame(values, name="factor values")
    except TransformError as exc:
        raise OutputShapingError(
            f"evaluated values are not a well-formed value frame: {exc}",
            context={"factor_id": spec.id},
        ) from exc

    reference = frames[sorted(frames)[0]]
    if not values.index.equals(reference.index) or not values.columns.equals(
        reference.columns
    ):
        raise OutputShapingError(
            "evaluated values leave the trusted input grid; the evaluator "
            "never expands or truncates the observation grid",
            context={"factor_id": spec.id},
        )

    panel = value_frame_to_long(values)

    if spec.missing_policy is MissingPolicy.DROP:
        panel = panel.loc[panel[VALUE_COL].notna()].reset_index(drop=True)

    shaped = _shape_panel(panel)

    return EvaluationResult(
        factor_id=spec.id,
        factor_version=spec.version,
        expression_hash=_expression_hash(spec.expression),
        missing_policy=spec.missing_policy.value,
        frequency=spec.frequency.value,
        roles=referenced,
        diagnostics=_diagnostics(spec, frames),
        panel=shaped,
    )


# ---------------------------------------------------------------------------
# canonical form / deterministic hash
# ---------------------------------------------------------------------------
def _canonical_number(value: Any) -> str:
    """Deterministic textual encoding of a float, including non-finite values.

    ``NaN`` maps to ``"nan"`` and infinities to ``"inf"``/``"-inf"`` so the
    canonical JSON stays valid without ``allow_nan`` and without broadening
    the frozen division-only non-finite policy (a non-division overflow, e.g.
    ``a * 1e308``, is preserved as ``inf`` rather than silently rewritten).
    """
    if pd.isna(value):
        return "nan"
    return repr(float(value))


def _panel_records(panel: pd.DataFrame) -> list[dict[str, Any]]:
    """Deterministic per-observation records for the canonical provenance hash."""
    dates = panel[DATE_COL]
    stocks = panel[STOCK_COL]
    values = panel[VALUE_COL]
    return [
        {
            "date": pd.Timestamp(date).isoformat(),
            "stock_id": str(stock),
            "value": _canonical_number(value),
        }
        for date, stock, value in zip(dates, stocks, values)
    ]


def _content_dict(result: EvaluationResult) -> dict[str, Any]:
    """The hashed content of a result (excludes the derived content hash)."""
    return {
        "factor_id": result.factor_id,
        "factor_version": result.factor_version,
        "expression_hash": result.expression_hash,
        "missing_policy": result.missing_policy,
        "frequency": result.frequency,
        "roles": list(result.roles),
        "diagnostics": [diagnostic.to_dict() for diagnostic in result.diagnostics],
        "panel": _panel_records(result.panel),
    }


def canonical_json(result: EvaluationResult) -> str:
    """Deterministic canonical JSON of an :class:`EvaluationResult`.

    Sorted keys, no insignificant whitespace, ASCII-only, finite-JSON safe:
    factor values are encoded as canonical numeric strings, so the payload is
    input-order independent and stable across runs and platforms.
    """
    if not isinstance(result, EvaluationResult):
        raise EvaluatorError(
            f"canonical_json requires an EvaluationResult, got "
            f"{type(result).__name__}"
        )
    return json.dumps(
        _content_dict(result),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def evaluation_hash(result: EvaluationResult) -> str:
    """Deterministic SHA-256 canonical hash of an :class:`EvaluationResult`."""
    return hashlib.sha256(canonical_json(result).encode("utf-8")).hexdigest()
