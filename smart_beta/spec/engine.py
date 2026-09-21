"""PIT trust-boundary admission facade for the Phase 6 specification layer.

Phase 6, task **P6-F**. This module is the *admission* half of the trusted
factor stack. It sits between the trusted upstream point-in-time (PIT) layer
(``smart_beta/pit/*``) and the trusted expression evaluator (P6-D,
:mod:`smart_beta.spec.evaluator`)::

    trusted PIT selection            (smart_beta.pit.*)
              |
    already-selected trusted inputs  (this module's TrustedInput)
              |
    admission against the FactorSpec (this module)
              |
    already-admitted value frames
              |
    trusted expression evaluation    (smart_beta.spec.evaluator)

Admission, not selection
------------------------

**Selection** asks *which historical record was knowable at ``t``?*. That
authority belongs to the existing trusted PIT layer --
:class:`~smart_beta.pit.view.PointInTimeView`,
:func:`~smart_beta.pit.fundamentals.latest_known_value`,
:func:`~smart_beta.pit.corporate_actions.compute_adjusted_returns` -- and to
no-one else. **Admission** asks *has the trusted upstream layer supplied
evidence that this already-selected input satisfies the FactorSpec's declared
:class:`~smart_beta.spec.requirements.DataRequirement`?* -- that is this
module's whole job.

This module therefore performs **no temporal selection**. Statically and
dynamically it never: picks a latest row, takes ``max(knowledge_date)``,
sort-and-takes-last, performs ``merge_asof`` to discover a historical record,
searches publication history, infers a vintage from row order, chooses a
revision by formation date, back-fills a missing vintage, forward-fills
knowledge, or queries any provider for an older record. If the facade is
handed raw multi-vintage candidates and would have to choose one, it
**rejects the input shape/contract** instead of selecting: the upstream
trusted layer must already have resolved it.

Admission is fail-closed and per declared alias
-----------------------------------------------

For every alias the FactorSpec's expression references, admission requires
the evidence the frozen contract requires:

* value **semantic identity**, **frequency**, **observation period**,
  declared **units**, and required **history/lookback**;
* **knowledge/publication-date** admissibility and, where declared,
  **positive vintage identity** and the declared **revision policy**;

all of which are checked by reusing P6-B's fail-closed machinery
(:func:`smart_beta.spec.requirements.require_satisfiable` over a
:class:`~smart_beta.spec.requirements.DataCapability`), *not* by a
re-invented checker. In addition this module enforces the two boundary facts
that the requirement contract cannot express: the supplied value frames must
share **one compatible trusted grid**, and where positive vintage identity is
declared the input must carry **positive per-observation vintage evidence**
(see below). Every failure is a typed, recordable member of the frozen
section 7 error namespace.

Positive vintage identity (Phase 5B discipline)
-----------------------------------------------

Stock + period is **not** positive vintage identity. Value existence is
**not** positive vintage identity. Absence of a counterexample is **not**
positive vintage identity. Provider success is **not** positive vintage
identity. When a
:class:`~smart_beta.spec.requirements.DataRequirement` declares
``require_positive_vintage_identity``, admission succeeds only when the
trusted upstream layer positively establishes, *per present observation*, the
vintage/knowledge-date signal it preserves (the
:data:`smart_beta.pit.schema.KNOWLEDGE_DATE_COL` signal). A bare
``has_positive_vintage_identity=True`` flag, unbacked by per-observation
:class:`VintageEvidence`, is rejected. This does **not** reopen Phase 5B's
CH3 certification, makes no live provider call, and creates no workaround for
the Phase 5B structural limitation -- that limitation remains valid evidence
that fail-closed behaviour matters.

No proxy upgrade / evidence class is preserved
----------------------------------------------

Every :class:`TrustedInput` declares an :class:`EvidenceClass`. This module
**never upgrades** it: proxy-observed never becomes official-certified,
constructed never becomes live-recorded, contract-modeled never becomes
empirically-certified, stock+period never becomes positive-vintage-identified,
and assembly success never becomes semantic certification. The declared class
is copied into the admission provenance unchanged. A caller may additionally
express an explicit minimum provenance via ``required_evidence_class``; when
the declared class is below it, admission fails closed. The default minimum
is the weakest class, so admission never invents a certification requirement
the frozen plan did not declare.

Provenance
----------

:class:`AdmissionResult` records, per referenced alias, the bound
:class:`~smart_beta.spec.requirements.DataRequirement`, the supplied
:class:`~smart_beta.spec.requirements.DataCapability`, the preserved
:class:`EvidenceClass`, and the structured
:class:`~smart_beta.spec.requirements.SatisfactionResult` (reused rather than
re-invented). :class:`EngineResult` pairs that admission provenance with the
P6-D :class:`~smart_beta.spec.evaluator.EvaluationResult`, and
:func:`engine_hash` provides a deterministic canonical hash over both.

The upstream :class:`~smart_beta.spec.requirements.DataRequirementUnsatisfiableError`
(raised by ``require_satisfiable``) is translated **explicitly** into the
spec/evaluation stack as :class:`AdmissionError`, carrying the full
:class:`AdmissionResult` and therefore every structured
:class:`SatisfactionResult`. The distinctly named canonical expression-stack
:class:`~smart_beta.spec.expression.RequirementUnsatisfiableError` is never
raised, imported, or conflated here.

No temporal authority; settings stay upstream
--------------------------------------------

This module deliberately imports neither ``smart_beta.pit`` nor
``smart_beta.vendors``: consuming the PIT layer's *already-selected output*
(and its preserved knowledge-date signal) is the whole integration, and not
importing the resolver is the strongest structural guarantee that no
selection can leak in. In particular, ``Settings.pit_availability_buffer_days``
is **not** applied here: the availability buffer moves the *boundary of what
is known*, which is temporal selection and therefore belongs exclusively to
the upstream PIT layer
(:func:`smart_beta.pit.fundamentals.latest_known_value` and
:meth:`smart_beta.pit.view.AsOfSnapshot.fundamentals`). Re-applying it during
admission would be a second, unauthorised selector. The trusted layer applies
it once, upstream; admission consumes the result.

Determinism
-----------

Identical FactorSpec + identical admitted trusted inputs + identical upstream
evidence produce an identical output, ordering and hash. This module depends
only on pandas and the sibling spec-layer modules -- no clock, network,
provider state, mapping insertion order, filesystem discovery or random
state. Frames are normalized to a canonical (sorted) grid, admission
provenance is sorted by alias, and the canonical hash serializes a fully
ordered record.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

import pandas as pd

from smart_beta.spec.evaluator import (
    AmbiguousInputError,
    DuplicateInputError,
    EvaluationResult,
    InputShapeError,
    InputTypeError,
    MissingInputError,
    UndeclaredInputError,
    evaluate as _evaluate,
)
from smart_beta.spec.expression import EvaluationError
from smart_beta.spec.factor_spec import FactorSpec
from smart_beta.spec.requirements import (
    DataCapability,
    DataRequirement,
    DataRequirementUnsatisfiableError,
    SatisfactionResult,
    require_satisfiable,
)
from smart_beta.spec.transforms import TransformError, normalize_value_frame

__all__ = [
    # typed error taxonomy
    "EngineError",
    "TrustBoundaryError",
    "AdmissionError",
    "EvidenceClassError",
    "VintageIdentityError",
    "GridMismatchError",
    # frozen vocabularies / evidence
    "AdmissionReason",
    "EvidenceClass",
    "VintageEvidence",
    "TrustedInput",
    # provenance / results
    "AliasAdmission",
    "AdmissionResult",
    "EngineResult",
    # execution
    "admit",
    "evaluate_factor",
    "FactorEngine",
    # canonical form
    "canonical_json",
    "engine_hash",
]


# ---------------------------------------------------------------------------
# typed error taxonomy (frozen section 7 namespace)
# ---------------------------------------------------------------------------
class EngineError(EvaluationError):
    """Base class for every typed failure raised by the admission facade.

    Subclasses the shared :class:`~smart_beta.spec.expression.EvaluationError`
    so the whole trusted-expression stack keeps one error namespace.
    """


class TrustBoundaryError(EngineError):
    """An already-selected trusted input violates the admission boundary."""


class EvidenceClassError(TrustBoundaryError):
    """The declared evidence/provenance class is missing or unknown."""


class VintageIdentityError(TrustBoundaryError):
    """Positive vintage-identity evidence is malformed or unusable.

    This covers evidence that is not even well formed (wrong type, wrong
    grid, wrong dtype). A *well-formed* evidence frame that fails to
    establish per-observation vintage identity -- missing, incomplete, or
    ambiguous (more than one unresolved candidate vintage) -- is recorded as
    a typed :class:`AdmissionReason` on the :class:`AdmissionResult` and
    surfaced through :class:`AdmissionError`, so the facade fails closed
    without choosing.
    """


class GridMismatchError(TrustBoundaryError):
    """Supplied trusted inputs are not on exactly the same observation grid."""


class AdmissionError(TrustBoundaryError):
    """Admission failed closed; carries the full structured provenance.

    This is the deliberate, explicit translation of the upstream
    :class:`~smart_beta.spec.requirements.DataRequirementUnsatisfiableError`
    into the spec/evaluation stack. It preserves every structured
    :class:`~smart_beta.spec.requirements.SatisfactionResult` (and every
    engine-level trust reason) rather than flattening the failure to a string,
    and it is *not* the canonical expression-stack
    :class:`~smart_beta.spec.expression.RequirementUnsatisfiableError`.
    """

    def __init__(self, result: "AdmissionResult") -> None:
        if not isinstance(result, AdmissionResult):
            raise EngineError(
                "AdmissionError requires an AdmissionResult, got "
                f"{type(result).__name__}"
            )
        self.result = result
        failures = "; ".join(
            f"{admission.alias}: {list(admission.reasons)}"
            for admission in result.failures
        )
        super().__init__(
            f"factor {result.factor_id!r} admission failed closed -- {failures}"
        )


# ---------------------------------------------------------------------------
# frozen vocabularies
# ---------------------------------------------------------------------------
class AdmissionReason(str, Enum):
    """Named, recordable reasons an input was not admitted.

    The first nine values mirror
    :class:`smart_beta.spec.requirements.UnsatisfactionReason` exactly: they
    are produced by P6-B's own
    :func:`~smart_beta.spec.requirements.check_satisfiable` and are carried
    through unchanged. The remaining values are the trust-boundary reasons
    only this facade can observe (evidence class and per-observation vintage
    evidence).
    """

    # -- P6-B requirement-contract reasons (carried through verbatim) ------
    SEMANTIC_IDENTITY_MISMATCH = "semantic_identity_mismatch"
    FREQUENCY_MISMATCH = "frequency_mismatch"
    OBSERVATION_PERIOD_MISMATCH = "observation_period_mismatch"
    UNITS_MISMATCH = "units_mismatch"
    INSUFFICIENT_HISTORY = "insufficient_history"
    HISTORY_UNKNOWN = "history_unknown"
    KNOWLEDGE_DATE_UNAVAILABLE = "knowledge_date_unavailable"
    POSITIVE_VINTAGE_IDENTITY_UNAVAILABLE = "positive_vintage_identity_unavailable"
    REVISION_POLICY_UNSUPPORTED = "revision_policy_unsupported"
    # -- P6-F trust-boundary reasons ---------------------------------------
    EVIDENCE_CLASS_BELOW_REQUIRED = "evidence_class_below_required"
    VINTAGE_IDENTITY_EVIDENCE_MISSING = "vintage_identity_evidence_missing"
    VINTAGE_IDENTITY_EVIDENCE_INCOMPLETE = "vintage_identity_evidence_incomplete"
    VINTAGE_IDENTITY_EVIDENCE_AMBIGUOUS = "vintage_identity_evidence_ambiguous"


class EvidenceClass(str, Enum):
    """Coarse, frozen provenance/certification class of a trusted input.

    Ordered from weakest to strongest. The class is a *label* the upstream
    integration declares about its own evidence; this facade preserves it
    verbatim and never upgrades it. ``required_evidence_class`` compares
    against :attr:`rank`.
    """

    CONSTRUCTED = "constructed"
    CONTRACT_MODELED = "contract_modeled"
    PROXY_OBSERVED = "proxy_observed"
    LIVE_RECORDED = "live_recorded"
    OFFICIAL_VENDOR_CERTIFIED = "official_vendor_certified"

    @property
    def rank(self) -> int:
        """Total order over evidence classes (weakest = 0)."""
        return _EVIDENCE_RANK[self]


_EVIDENCE_RANK: dict[EvidenceClass, int] = {
    EvidenceClass.CONSTRUCTED: 0,
    EvidenceClass.CONTRACT_MODELED: 1,
    EvidenceClass.PROXY_OBSERVED: 2,
    EvidenceClass.LIVE_RECORDED: 3,
    EvidenceClass.OFFICIAL_VENDOR_CERTIFIED: 4,
}


def _coerce_enum(value: Any, enum_cls: type[Enum], *, field_name: str) -> Any:
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str):
        try:
            return enum_cls(value)
        except ValueError:
            pass
    allowed = ", ".join(member.value for member in enum_cls)
    raise EvidenceClassError(
        f"{field_name} must be one of [{allowed}], got {value!r}"
    )


# ---------------------------------------------------------------------------
# per-observation vintage evidence
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class VintageEvidence:
    """Positive, per-observation vintage-identity evidence.

    Supplied by the trusted upstream integration to demonstrate that the
    already-selected value frame preserves the PIT layer's knowledge/vintage
    signal (``smart_beta.pit.schema.KNOWLEDGE_DATE_COL``). It is deliberately
    *positive* evidence -- an actual knowledge/vintage signal on the same grid
    as the values -- not a bare boolean and not the absence of a
    counterexample.

    Parameters
    ----------
    knowledge_dates:
        A frame on exactly the same grid (observation-date index, stock
        identifier columns) as the alias's value frame, carrying the
        knowledge/vintage date the trusted layer resolved each observation
        from. A cell is ``NaT`` only where the corresponding *value* is
        absent; a present value with no knowledge date is incomplete
        evidence and fails closed.
    candidate_counts:
        Optional frame on the same grid stating how many raw candidate
        vintages the upstream layer had to resolve per observation. When
        supplied, any entry greater than 1 means the input is still
        unresolved multi-vintage data: admission fails closed rather than
        choosing. Omitted means the upstream layer has already resolved
        exactly one vintage per observation.
    """

    knowledge_dates: pd.DataFrame
    candidate_counts: pd.DataFrame | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.knowledge_dates, pd.DataFrame):
            raise VintageIdentityError(
                "vintage knowledge_dates evidence must be a pandas DataFrame, "
                f"got {type(self.knowledge_dates).__name__}"
            )
        if self.candidate_counts is not None and not isinstance(
            self.candidate_counts, pd.DataFrame
        ):
            raise VintageIdentityError(
                "vintage candidate_counts evidence must be a pandas DataFrame "
                f"or None, got {type(self.candidate_counts).__name__}"
            )


# ---------------------------------------------------------------------------
# an already-selected, evidence-bearing trusted input
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TrustedInput:
    """One already-selected trusted input bound to a declared alias.

    Parameters
    ----------
    values:
        The already-selected, already-aligned value frame: observation-date
        index, stock-identifier columns, numeric cells. It is produced by the
        trusted upstream layer; this facade never selects or aligns it.
    capability:
        The vendor-free :class:`~smart_beta.spec.requirements.DataCapability`
        the upstream layer can demonstrate for this input. Every field
        defaults, in P6-B, to the fail-closed ("cannot demonstrate") value.
    evidence_class:
        The upstream provenance/certification class. Defaults to
        :attr:`EvidenceClass.CONSTRUCTED`; never upgraded by this facade.
    vintage_evidence:
        Positive per-observation vintage evidence, required only when the
        bound :class:`~smart_beta.spec.requirements.DataRequirement` declares
        ``require_positive_vintage_identity``.
    """

    values: pd.DataFrame
    capability: DataCapability
    evidence_class: EvidenceClass = EvidenceClass.CONSTRUCTED
    vintage_evidence: VintageEvidence | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.values, pd.DataFrame):
            raise TrustBoundaryError(
                "trusted input values must be a pandas DataFrame value frame, "
                f"got {type(self.values).__name__}"
            )
        if not isinstance(self.capability, DataCapability):
            raise TrustBoundaryError(
                "trusted input capability must be a DataCapability, got "
                f"{type(self.capability).__name__}"
            )
        object.__setattr__(
            self,
            "evidence_class",
            _coerce_enum(
                self.evidence_class, EvidenceClass, field_name="evidence_class"
            ),
        )
        if self.vintage_evidence is not None and not isinstance(
            self.vintage_evidence, VintageEvidence
        ):
            raise VintageIdentityError(
                "vintage_evidence must be a VintageEvidence or None, got "
                f"{type(self.vintage_evidence).__name__}"
            )


# ---------------------------------------------------------------------------
# admission provenance
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AliasAdmission:
    """Deterministic, recordable admission outcome for one referenced alias."""

    alias: str
    requirement: DataRequirement
    capability: DataCapability
    evidence_class: EvidenceClass
    satisfaction: SatisfactionResult
    trust_reasons: tuple[str, ...] = ()

    @property
    def reasons(self) -> tuple[str, ...]:
        """Combined P6-B and trust-boundary reasons, de-duplicated in order."""
        ordered = [reason.value for reason in self.satisfaction.reasons]
        ordered.extend(self.trust_reasons)
        return tuple(dict.fromkeys(ordered))

    @property
    def admitted(self) -> bool:
        """Whether this alias satisfies every declared requirement/evidence."""
        return not self.reasons

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe provenance record (deterministic key order)."""
        return {
            "alias": self.alias,
            "semantic_id": self.requirement.semantic_id,
            "evidence_class": self.evidence_class.value,
            "admitted": self.admitted,
            "reasons": list(self.reasons),
            "satisfaction": self.satisfaction.to_dict(),
        }


@dataclass(frozen=True)
class AdmissionResult:
    """The full, deterministic admission provenance for a FactorSpec."""

    factor_id: str
    factor_version: str
    aliases: tuple[AliasAdmission, ...]

    @property
    def admitted(self) -> bool:
        """True iff every referenced alias admitted (fail closed otherwise)."""
        return all(admission.admitted for admission in self.aliases)

    @property
    def admitted_aliases(self) -> tuple[str, ...]:
        """Aliases that admitted, in canonical (alias-sorted) order."""
        return tuple(
            admission.alias
            for admission in self.aliases
            if admission.admitted
        )

    @property
    def failures(self) -> tuple[AliasAdmission, ...]:
        """Aliases that did not admit, in canonical order."""
        return tuple(
            admission
            for admission in self.aliases
            if not admission.admitted
        )

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe provenance record (deterministic key order)."""
        return {
            "factor_id": self.factor_id,
            "factor_version": self.factor_version,
            "admitted": self.admitted,
            "aliases": [admission.to_dict() for admission in self.aliases],
        }


# ---------------------------------------------------------------------------
# engine result
# ---------------------------------------------------------------------------
@dataclass(frozen=True, eq=False)
class EngineResult:
    """Admission provenance plus the P6-D evaluation over the admitted inputs.

    ``evaluation`` is produced by the existing trusted evaluator from exactly
    the admitted value frames, so the upstream observation selection is
    unchanged by this facade.
    """

    admission: AdmissionResult
    evaluation: EvaluationResult

    @property
    def content_hash(self) -> str:
        """Deterministic SHA-256 canonical hash of admission + evaluation."""
        return engine_hash(self)

    def to_dict(self) -> dict[str, Any]:
        """Full deterministic provenance record (values, reasons, hash)."""
        record = _content_dict(self)
        record["content_hash"] = self.content_hash
        return record


# ---------------------------------------------------------------------------
# input-boundary helpers
# ---------------------------------------------------------------------------
def _trusted_pairs(inputs: Any) -> list[tuple[Any, Any]]:
    """Normalise the trusted-input collection into an ordered pair list.

    Accepts a mapping of ``alias -> TrustedInput`` or a sequence of
    ``(alias, TrustedInput)`` pairs. Anything else -- a bare string, a scalar,
    or a malformed entry -- fails closed as an ambiguous/ill-typed boundary.
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
                    "trusted inputs must be a mapping of alias -> TrustedInput "
                    "or a sequence of (alias, TrustedInput) pairs; got a "
                    f"malformed entry {entry!r}"
                )
            pairs.append((entry[0], entry[1]))
        return pairs
    raise InputTypeError(
        "trusted inputs must be a mapping of alias -> TrustedInput or a "
        f"sequence of (alias, TrustedInput) pairs, got {type(inputs).__name__}"
    )


def _normalize_values(item: TrustedInput, alias: str) -> pd.DataFrame:
    """Normalize one alias's value frame, fail closed on any shape defect."""
    try:
        normalized = normalize_value_frame(
            item.values, name=f"trusted input role {alias!r}"
        )
    except TransformError as exc:
        raise InputShapeError(
            f"trusted input for role {alias!r} is not a well-formed value "
            f"frame: {exc}"
        ) from exc
    if not pd.api.types.is_datetime64_any_dtype(normalized.index.dtype):
        raise InputShapeError(
            f"trusted input for role {alias!r} must carry observation dates "
            "on its index (the trusted boundary owns temporal selection); got "
            f"index dtype {normalized.index.dtype}"
        )
    if not pd.api.types.is_string_dtype(normalized.columns.dtype):
        raise InputShapeError(
            f"trusted input for role {alias!r} must carry stock identifiers "
            f"as its columns, got dtype {normalized.columns.dtype}"
        )
    return normalized


def _collect_inputs(
    spec: FactorSpec, inputs: Any
) -> dict[str, TrustedInput]:
    """Validate the collection boundary and normalise the referenced frames."""
    declared = set(spec.aliases)
    supplied: dict[str, TrustedInput] = {}
    for alias, item in _trusted_pairs(inputs):
        if not isinstance(alias, str):
            raise InputTypeError(
                "every trusted-input alias must be a string, got "
                f"{type(alias).__name__}"
            )
        if alias not in declared:
            raise UndeclaredInputError(
                f"trusted input supplied for alias {alias!r}, which the spec "
                f"{spec.id!r} does not declare (declared: {sorted(declared)})"
            )
        if alias in supplied:
            raise DuplicateInputError(
                f"trusted input for alias {alias!r} was supplied more than "
                "once; a role must be bound to exactly one trusted input"
            )
        if not isinstance(item, TrustedInput):
            raise TrustBoundaryError(
                f"trusted input for alias {alias!r} must be a TrustedInput "
                "(already-selected values plus an upstream capability and "
                f"evidence class); got {type(item).__name__}. A bare value "
                "frame carries no admissible evidence and fails closed"
            )
        supplied[alias] = item

    missing = [role for role in spec.referenced_roles if role not in supplied]
    if missing:
        raise MissingInputError(
            f"the expression references declared role(s) {missing} for which "
            "no trusted input was supplied; the facade fails closed rather "
            "than searching for, substituting, or selecting data"
        )
    return supplied


def _ensure_common_grid(frames: Mapping[str, pd.DataFrame], factor_id: str) -> None:
    """Fail closed unless every supplied frame shares one exact grid."""
    aliases = sorted(frames)
    if len(aliases) < 2:
        return
    reference_alias = aliases[0]
    reference = frames[reference_alias]
    for alias in aliases[1:]:
        frame = frames[alias]
        if not frame.index.equals(reference.index):
            raise GridMismatchError(
                f"trusted inputs {reference_alias!r} and {alias!r} are on "
                "different observation-date grids; the facade never aligns, "
                "joins, fills, or reindexes mismatched inputs (the trusted "
                "boundary owns alignment)",
            )
        if not frame.columns.equals(reference.columns):
            raise GridMismatchError(
                f"trusted inputs {reference_alias!r} and {alias!r} cover "
                "different stock identifiers; the facade never reconciles "
                "universes (the trusted boundary owns alignment)",
            )


# ---------------------------------------------------------------------------
# per-alias admission
# ---------------------------------------------------------------------------
def _satisfy(
    requirement: DataRequirement, capability: DataCapability
) -> SatisfactionResult:
    """P6-B's fail-closed check, with its typed error caught explicitly.

    ``require_satisfiable`` is the requirement contract's own fail-closed
    entry point; when it raises
    :class:`~smart_beta.spec.requirements.DataRequirementUnsatisfiableError`,
    the structured :class:`~smart_beta.spec.requirements.SatisfactionResult`
    it carries is preserved verbatim so that *every* alias's reason can be
    recorded rather than only the first. No reason is inferred here.
    """
    try:
        return require_satisfiable(requirement, capability)
    except DataRequirementUnsatisfiableError as exc:
        return exc.result


def _aligned_evidence_frame(
    frame: pd.DataFrame, values: pd.DataFrame, what: str
) -> pd.DataFrame:
    """Return ``frame`` sorted onto the value frame's grid, or fail closed."""
    ordered = frame.sort_index(axis=0).sort_index(axis=1)
    if not ordered.index.equals(values.index) or not ordered.columns.equals(
        values.columns
    ):
        raise VintageIdentityError(
            f"{what} is not on the same observation grid as its value frame; "
            "vintage evidence must be per observation and aligned by the "
            "trusted upstream layer, never re-aligned here"
        )
    return ordered


def _vintage_trust_reasons(
    values: pd.DataFrame, evidence: VintageEvidence | None
) -> tuple[str, ...]:
    """Positive per-observation vintage-identity checks (fail closed).

    Only called when the bound requirement declares
    ``require_positive_vintage_identity``. The value frame is already
    normalized; present values (non-NaN) are the observations that must each
    carry a knowledge/vintage date. Absence of evidence is a reason, never a
    silent pass.
    """
    if evidence is None:
        return (AdmissionReason.VINTAGE_IDENTITY_EVIDENCE_MISSING.value,)

    reasons: list[str] = []
    knowledge_dates = _aligned_evidence_frame(
        evidence.knowledge_dates, values, "vintage knowledge_dates evidence"
    )
    for column in knowledge_dates.columns:
        if not pd.api.types.is_datetime64_any_dtype(knowledge_dates[column].dtype):
            raise VintageIdentityError(
                "vintage knowledge_dates evidence must carry datetimes; got "
                f"dtype {knowledge_dates[column].dtype} for stock "
                f"{column!r}"
            )

    present = values.notna()
    incomplete = present & knowledge_dates.isna()
    if bool(incomplete.to_numpy().any()):
        reasons.append(
            AdmissionReason.VINTAGE_IDENTITY_EVIDENCE_INCOMPLETE.value
        )

    if evidence.candidate_counts is not None:
        counts = _aligned_evidence_frame(
            evidence.candidate_counts, values, "vintage candidate_counts evidence"
        )
        try:
            counts = counts.astype("float64")
        except (TypeError, ValueError) as exc:
            raise VintageIdentityError(
                "vintage candidate_counts evidence must be numeric: "
                f"{exc}"
            ) from exc
        ambiguous = present & (counts > 1)
        if bool(ambiguous.to_numpy().any()):
            reasons.append(
                AdmissionReason.VINTAGE_IDENTITY_EVIDENCE_AMBIGUOUS.value
            )
        unresolved = present & counts.isna()
        if bool(unresolved.to_numpy().any()):
            reasons.append(
                AdmissionReason.VINTAGE_IDENTITY_EVIDENCE_INCOMPLETE.value
            )

    return tuple(dict.fromkeys(reasons))


def _admit_one(
    alias: str,
    requirement: DataRequirement,
    item: TrustedInput,
    values: pd.DataFrame,
    required_evidence_class: EvidenceClass,
) -> AliasAdmission:
    """Admit a single alias, collecting every reason rather than stopping."""
    satisfaction = _satisfy(requirement, item.capability)

    trust_reasons: list[str] = []
    if item.evidence_class.rank < required_evidence_class.rank:
        trust_reasons.append(AdmissionReason.EVIDENCE_CLASS_BELOW_REQUIRED.value)
    if requirement.require_positive_vintage_identity:
        trust_reasons.extend(_vintage_trust_reasons(values, item.vintage_evidence))

    return AliasAdmission(
        alias=alias,
        requirement=requirement,
        capability=item.capability,
        evidence_class=item.evidence_class,
        satisfaction=satisfaction,
        trust_reasons=tuple(dict.fromkeys(trust_reasons)),
    )


def _build(
    spec: FactorSpec, inputs: Any, required_evidence_class: EvidenceClass
) -> tuple[dict[str, pd.DataFrame], AdmissionResult]:
    """Run the full admission boundary; return the frames and the provenance."""
    supplied = _collect_inputs(spec, inputs)

    frames = {
        alias: _normalize_values(item, alias)
        for alias, item in sorted(supplied.items())
    }
    _ensure_common_grid(frames, spec.id)

    admissions = tuple(
        _admit_one(
            alias,
            spec.input_for(alias).requirement,
            supplied[alias],
            frames[alias],
            required_evidence_class,
        )
        for alias in sorted(spec.referenced_roles)
    )

    result = AdmissionResult(
        factor_id=spec.id,
        factor_version=spec.version,
        aliases=admissions,
    )
    return frames, result


# ---------------------------------------------------------------------------
# public entry points
# ---------------------------------------------------------------------------
def admit(
    spec: FactorSpec,
    inputs: Any,
    *,
    required_evidence_class: EvidenceClass = EvidenceClass.CONSTRUCTED,
) -> AdmissionResult:
    """Admit an already-selected trusted input set against ``spec``.

    Parameters
    ----------
    spec:
        A validated :class:`~smart_beta.spec.factor_spec.FactorSpec`. Its
        alias-to-requirement bindings are frozen; this function never
        re-designs them.
    inputs:
        The already-selected trusted inputs, as a mapping of declared alias ->
        :class:`TrustedInput`, or an ordered sequence of ``(alias,
        TrustedInput)`` pairs. A supplied alias must be declared; every role
        the expression references must be supplied; a role may be supplied
        exactly once; all supplied frames must share one exact grid.
    required_evidence_class:
        Explicit minimum provenance class (default: the weakest class, so no
        certification requirement is invented). A supplied input below it
        fails closed with
        :data:`AdmissionReason.EVIDENCE_CLASS_BELOW_REQUIRED`.

    Returns
    -------
    AdmissionResult
        The full, deterministic admission provenance. Admission is fail-closed
        per declared alias using P6-B's machinery; when any alias fails, an
        :class:`AdmissionError` carrying this result is raised instead.

    Raises
    ------
    AdmissionError
        One or more alias requirements/evidence were not satisfied. Carries
        the structured :class:`AdmissionResult`.
    EngineError / TrustBoundaryError / subclasses
        The boundary itself is malformed (undeclared/duplicate/missing/typed
        input, incompatible grid, unusable vintage evidence).
    """
    if not isinstance(spec, FactorSpec):
        raise EngineError(
            f"admit requires a FactorSpec, got {type(spec).__name__}"
        )
    required_evidence_class = _coerce_enum(
        required_evidence_class,
        EvidenceClass,
        field_name="required_evidence_class",
    )
    _, result = _build(spec, inputs, required_evidence_class)
    if not result.admitted:
        raise AdmissionError(result)
    return result


def evaluate_factor(
    spec: FactorSpec,
    inputs: Any,
    *,
    required_evidence_class: EvidenceClass = EvidenceClass.CONSTRUCTED,
) -> EngineResult:
    """Admit ``spec`` over trusted inputs, then evaluate through P6-D.

    Admission runs exactly as :func:`admit` (same fail-closed boundary). On
    success, the *admitted, normalized* value frames are handed unchanged to
    the existing trusted evaluator
    :func:`smart_beta.spec.evaluator.evaluate`, and the resulting factor
    values plus the admission/evaluation provenance are returned. This facade
    never selects, aligns, or transforms the observations itself.
    """
    if not isinstance(spec, FactorSpec):
        raise EngineError(
            f"evaluate_factor requires a FactorSpec, got {type(spec).__name__}"
        )
    required_evidence_class = _coerce_enum(
        required_evidence_class,
        EvidenceClass,
        field_name="required_evidence_class",
    )
    frames, result = _build(spec, inputs, required_evidence_class)
    if not result.admitted:
        raise AdmissionError(result)

    evaluation = _evaluate(spec, frames)
    return EngineResult(admission=result, evaluation=evaluation)


class FactorEngine:
    """Thin, reusable admission/evaluation facade.

    Holds the explicit minimum provenance class so a caller can configure it
    once; delegates to the module-level :func:`admit` / :func:`evaluate_factor`
    functions. It owns no resolution logic and is fully deterministic.
    """

    def __init__(
        self,
        *,
        required_evidence_class: EvidenceClass = EvidenceClass.CONSTRUCTED,
    ) -> None:
        self._required_evidence_class = _coerce_enum(
            required_evidence_class,
            EvidenceClass,
            field_name="required_evidence_class",
        )

    @property
    def required_evidence_class(self) -> EvidenceClass:
        """The minimum evidence class this engine requires of every input."""
        return self._required_evidence_class

    def admit(self, spec: FactorSpec, inputs: Any) -> AdmissionResult:
        """Admit ``spec`` over ``inputs`` (see :func:`admit`)."""
        return admit(
            spec,
            inputs,
            required_evidence_class=self._required_evidence_class,
        )

    def evaluate(self, spec: FactorSpec, inputs: Any) -> EngineResult:
        """Admit then evaluate ``spec`` over ``inputs`` (see
        :func:`evaluate_factor`)."""
        return evaluate_factor(
            spec,
            inputs,
            required_evidence_class=self._required_evidence_class,
        )


# ---------------------------------------------------------------------------
# canonical form / deterministic hash
# ---------------------------------------------------------------------------
def _content_dict(result: EngineResult) -> dict[str, Any]:
    """The hashed content of an :class:`EngineResult` (excludes the hash)."""
    return {
        "admission": result.admission.to_dict(),
        "evaluation": result.evaluation.to_dict(),
    }


def canonical_json(result: EngineResult) -> str:
    """Deterministic canonical JSON of an :class:`EngineResult`.

    Sorted keys, no insignificant whitespace, ASCII-only, finite-JSON safe.
    """
    if not isinstance(result, EngineResult):
        raise EngineError(
            "canonical_json requires an EngineResult, got "
            f"{type(result).__name__}"
        )
    return json.dumps(
        _content_dict(result),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def engine_hash(result: EngineResult) -> str:
    """Deterministic SHA-256 canonical hash of an :class:`EngineResult`."""
    return hashlib.sha256(canonical_json(result).encode("utf-8")).hexdigest()
