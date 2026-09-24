"""Pilot 1A P1A-G2: the experiment-design provider.

This module owns **only** the frozen experiment-design provider of the
Pilot-1A harness introduced by ``worker_tasks/pilot1/pilot1-plan.md`` section
13 (the P1A-G2 row of section 18's task table). It composes the sealed
authorities -- never redefining them -- to turn one admitted
:class:`~smart_beta.research.proposal.ResearchProposal`/:class:`~smart_beta.spec.factor_spec.FactorSpec`
plus the P1A-G1 :class:`~smart_beta.pilot.data.PilotData` and the frozen
configuration into the sealed Phase-7/8 authorities P9-E forwards to the
Phase-8 orchestrator::

    admitted FactorSpec + G1 TrustedInput
        -> spec.engine.evaluate_factor  (Phase 6 admission + evaluation)
        -> EvaluationSpec               (frozen template + EngineResult hash)
        -> evaluation.partition.Partition (from the configured dates)
        -> evaluation.engine.evaluate   (Phase 7, periods_per_year=252)
        -> experiment.holdout.HoldoutIdentity (frozen dataset/universe/target)
        -> research.loop.ExperimentDesign

Frozen surface (plan section 13)
--------------------------------

* :func:`build_evaluation_spec` -- clone the frozen template varying **only**
  ``factor_provenance_hash`` (and assert no other field varies);
* :func:`build_partition` -- build the sealed
  :class:`~smart_beta.evaluation.partition.Partition` from the configured
  dates;
* :func:`build_holdout_identity` -- the frozen holdout identity;
* :func:`build_design` -- the provider returning :class:`ProposalDesign`;
* :func:`make_design_provider` -- the
  ``Callable[[ResearchProposal], ExperimentDesign]`` G5 wires into
  :meth:`smart_beta.research.loop.ResearchLoop.run_cycle`;
* :func:`build_frozen_evaluation_spec_template` /
  :func:`subperiod_boundaries` / :func:`frozen_partition_dates` -- the
  section-16 declaration builders (see the executability notes below).

The section-26b freeze correction (H6-v1 adversarial evidence) changed the
frozen template in three ways:

1. ``MetricKey.PARAMETER_SENSITIVITY`` is removed and ``parameter_grid`` is
   the single primary point ``(n_groups=3, horizon=1, cost_bps=C,
   winsorization=0.01)``;
2. the subperiod boundaries are derived **exclusively** from the supplied
   authorized development window as ``(is_start, 2026-01-02, holdout_start)``
   -- the real-run holdout start is never hardcoded into the template, so the
   dry-run template ends its last subperiod at the dry-run holdout start
   (``2026-05-01``);
3. an unusable development window (the cut not strictly inside it, or any
   boundary outside ``[is_start, holdout_start]``) is refused with the typed
   :class:`SubperiodBoundaryError`.

Data certification is **derived**, never defaulted
--------------------------------------------------

:attr:`ProposalDesign.required_data_certified` is exactly the sealed Phase-6
admission outcome: ``True`` iff every referenced alias admitted. When
admission fails closed, the provider returns
``required_data_certified=False`` together with every structured admission
reason and **does not** fabricate an :class:`EvaluationRecord` or a sealed
:class:`ExperimentDesign` (an ``EvaluationRecord`` cannot exist without a
successful evaluation, and fabricating one would be exactly the kind of
defaulted certification convention the plan forbids).

Boundary (plan section 13 "Must NOT")
-------------------------------------

This module **never** judges, counts attempts, touches
``HoldoutGovernance``/``SearchLedger``, varies any spec field by result,
defaults ``required_data_certified``, or calls the Phase-8 orchestrator.
``tests/test_pilot_design.py`` enforces this statically with an AST scan.

Section-16 executability notes (pre-freeze fixes)
-------------------------------------------------

The frozen plan section 16 describes the evaluation template declaratively.
Two of its literal values are not representable by the sealed P7-C contracts
(plan section 16 already anticipates such "executability fixes discovered ...
before any real-data evaluation and recorded as a pre-freeze fix").
:func:`build_frozen_evaluation_spec_template` applies the minimal
constructive fixes and documents each one:

* ``universe_variants=()`` is rejected by
  :class:`~smart_beta.evaluation.spec.EvaluationSpec` (non-empty required),
  so the single inert variant ``"all"`` is declared;
* the plan leaves ``walk_forward_fold_length``/``holdout_length`` implicit;
  the sealed :class:`~smart_beta.evaluation.spec.SplitRule` requires positive
  integers, so ``walk_forward_fold_length=1`` (unused, ``folds=0``) and the
  inclusive holdout length are declared.

The subperiod boundaries are no longer a literal executability fix: section
26b derives them from the supplied development window (the first and last are
``is_start``/``holdout_start``; the middle is the frozen ``2026-01-02``
development cut), which :class:`SubperiodRule` accepts as three distinct,
ordered boundaries. No real-run holdout date is embedded in the builder.

No provider SDK, network, credential or file I/O is used.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from smart_beta.evaluation.engine import evaluate
from smart_beta.evaluation.partition import Fold, FoldRole, Partition
from smart_beta.evaluation.spec import (
    BenchmarkKind,
    BenchmarkRef,
    CostMode,
    CostModel,
    EvaluationSpec,
    MetricKey,
    ParameterPoint,
    SplitRule,
    SubperiodRule,
)
from smart_beta.experiment.holdout import HoldoutIdentity
from smart_beta.experiment.policy import DecisionPolicy, SearchPolicy
from smart_beta.pilot.contracts import content_hash
from smart_beta.pilot.data import DAILY_TOTAL_RETURN, PilotData
from smart_beta.research.loop import ExperimentDesign
from smart_beta.research.proposal import FactorTemplateRef, ResearchProposal
from smart_beta.spec.engine import (
    AdmissionError,
    AdmissionResult,
    EngineResult,
    evaluate_factor,
)
from smart_beta.spec.factor_spec import FactorSpec

__all__ = [
    # errors
    "DesignError",
    "DesignInputError",
    "DesignTemplateIntegrityError",
    "SubperiodBoundaryError",
    "DataNotCertifiedError",
    # frozen constants
    "PILOT_PERIODS_PER_YEAR",
    "PILOT_TRANSACTION_COST_BPS",
    "PILOT_SPLIT_RULE_ID",
    "DAILY_RETURN_TARGET",
    "PILOT_HOLDOUT_HORIZON",
    # configured dates
    "PilotPartitionDates",
    "frozen_partition_dates",
    "subperiod_boundaries",
    "build_frozen_evaluation_spec_template",
    # provider surface
    "ProposalDesign",
    "build_evaluation_spec",
    "build_partition",
    "build_holdout_identity",
    "build_design",
    "resolve_factor_spec",
    "make_design_provider",
]

#: The frozen annualization factor (plan section 16): Phase 7 is called with
#: ``periods_per_year=252``.
PILOT_PERIODS_PER_YEAR = 252

#: The frozen transaction-cost convention (user freeze 3): ``10`` bps, one-way.
PILOT_TRANSACTION_COST_BPS = 10.0

#: The frozen partition split-rule identifier (plan sections 13/16).
PILOT_SPLIT_RULE_ID = "pilot1a/is-oos-holdout-v1"

#: The single frozen realized target identity (plan sections 5/16).
DAILY_RETURN_TARGET = DAILY_TOTAL_RETURN

#: The frozen holdout horizon (plan section 13 step 5): one realized-return row.
PILOT_HOLDOUT_HORIZON = 1

#: The section-16 warm-up window (in no fold).
_WARMUP_START = date(2025, 9, 8)
_WARMUP_END = date(2025, 10, 14)
#: The section-16 IS/OOS/final-holdout windows (inclusive calendar endpoints).
_IS_START = date(2025, 10, 15)
_IS_END = date(2026, 3, 31)
_OOS_START = date(2026, 4, 1)
_OOS_END = date(2026, 6, 30)
_HOLDOUT_START = date(2026, 7, 1)
_HOLDOUT_END = date(2026, 9, 15)
#: The section-16 frozen subperiod cut date.
_SUBPERIOD_CUT = date(2026, 1, 2)


# ---------------------------------------------------------------------------
# fail-closed errors
# ---------------------------------------------------------------------------


class DesignError(ValueError):
    """Base class for every P1A-G2 experiment-design provider violation."""


class DesignInputError(DesignError):
    """A provider input is malformed or inconsistent with the frozen config."""


class SubperiodBoundaryError(DesignInputError):
    """The supplied development window cannot carry the frozen subperiod cut.

    Raised when the section-26b frozen cut is not strictly inside the
    authorized development interval ``(is_start, holdout_start)``, or when a
    derived boundary would fall outside ``[is_start, holdout_start]``. The
    refusal is fail-closed: an unusable window never becomes a template.
    """


class DesignTemplateIntegrityError(DesignError):
    """Cloning the frozen template varied a field other than the provenance hash."""


class DataNotCertifiedError(DesignError):
    """Admission failed closed, so no sealed ``ExperimentDesign`` exists.

    Carries the :class:`ProposalDesign` (with ``required_data_certified``
    ``False`` and every structured admission reason) so the runner can emit
    the typed ``DATA_NOT_PIT_CERTIFIED`` stop without a fabricated record.
    """

    def __init__(self, design: "ProposalDesign") -> None:
        if not isinstance(design, ProposalDesign):
            raise DesignError(
                "DataNotCertifiedError requires a ProposalDesign, got "
                f"{type(design).__name__}"
            )
        self.design = design
        reasons = "; ".join(design.admission_reasons) or "no structured reason"
        super().__init__(
            f"factor {design.admission_result.factor_id!r} is not PIT-certified "
            f"-- {reasons}"
        )


# ---------------------------------------------------------------------------
# configured dates
# ---------------------------------------------------------------------------


def _as_calendar_date(value: Any, *, field_name: str) -> date:
    if isinstance(value, bool):
        raise DesignInputError(f"{field_name} must be a date, got {value!r}")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise DesignInputError(
                f"{field_name} must be an ISO date (YYYY-MM-DD), got {value!r}"
            ) from exc
    raise DesignInputError(
        f"{field_name} must be a date or ISO date string, got {type(value).__name__}"
    )


@dataclass(frozen=True)
class PilotPartitionDates:
    """The frozen, configured partition dates (plan sections 13 and 16).

    Every ``*_end`` is an **inclusive** calendar date; :func:`build_partition`
    translates each interval to the sealed half-open ``[start, end + 1 day)``
    fold convention. ``warmup_start``/``warmup_end`` are provenance-only and
    are never a fold (the plan's warm-up window belongs to no fold).
    """

    is_start: date
    is_end: date
    oos_start: date
    oos_end: date
    holdout_start: date
    holdout_end: date
    warmup_start: date | None = None
    warmup_end: date | None = None
    split_rule: str = PILOT_SPLIT_RULE_ID

    def __post_init__(self) -> None:
        for name in (
            "is_start",
            "is_end",
            "oos_start",
            "oos_end",
            "holdout_start",
            "holdout_end",
        ):
            object.__setattr__(
                self, name, _as_calendar_date(getattr(self, name), field_name=name)
            )
        for name in ("warmup_start", "warmup_end"):
            raw = getattr(self, name)
            object.__setattr__(
                self,
                name,
                None if raw is None else _as_calendar_date(raw, field_name=name),
            )
        if not isinstance(self.split_rule, str) or not self.split_rule.strip():
            raise DesignInputError("split_rule must be a non-empty string")

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_start": self.is_start.isoformat(),
            "is_end": self.is_end.isoformat(),
            "oos_start": self.oos_start.isoformat(),
            "oos_end": self.oos_end.isoformat(),
            "holdout_start": self.holdout_start.isoformat(),
            "holdout_end": self.holdout_end.isoformat(),
            "warmup_start": (
                None if self.warmup_start is None else self.warmup_start.isoformat()
            ),
            "warmup_end": (
                None if self.warmup_end is None else self.warmup_end.isoformat()
            ),
            "split_rule": self.split_rule,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PilotPartitionDates":
        keys = frozenset(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        if not isinstance(payload, Mapping):
            raise DesignInputError(
                "serialized partition dates must be a mapping, got "
                f"{type(payload).__name__}"
            )
        unknown = sorted(set(payload) - keys)
        missing = sorted(
            {
                "is_start",
                "is_end",
                "oos_start",
                "oos_end",
                "holdout_start",
                "holdout_end",
            }
            - set(payload)
        )
        if missing:
            raise DesignInputError(
                f"serialized partition dates are missing required keys {missing}"
            )
        if unknown:
            raise DesignInputError(
                f"serialized partition dates have unsupported keys {unknown}"
            )
        return cls(**{key: payload[key] for key in keys if key in payload})  # type: ignore[arg-type]


def frozen_partition_dates() -> PilotPartitionDates:
    """The section-16 real-run partition dates (inclusive endpoints)."""
    return PilotPartitionDates(
        is_start=_IS_START,
        is_end=_IS_END,
        oos_start=_OOS_START,
        oos_end=_OOS_END,
        holdout_start=_HOLDOUT_START,
        holdout_end=_HOLDOUT_END,
        warmup_start=_WARMUP_START,
        warmup_end=_WARMUP_END,
        split_rule=PILOT_SPLIT_RULE_ID,
    )


# ---------------------------------------------------------------------------
# section-16/26b frozen EvaluationSpec template
# ---------------------------------------------------------------------------


def _coerce_partition_dates(
    value: PilotPartitionDates | Mapping[str, Any],
) -> PilotPartitionDates:
    if isinstance(value, PilotPartitionDates):
        return value
    if isinstance(value, Mapping):
        return PilotPartitionDates.from_dict(value)
    raise DesignInputError(
        "partition_dates must be a PilotPartitionDates or its serialized "
        f"mapping form, got {type(value).__name__}"
    )


def subperiod_boundaries(
    partition_dates: PilotPartitionDates | Mapping[str, Any],
) -> tuple[date, date, date]:
    """The section-26b subperiod boundaries of the supplied development window.

    Returns ``(is_start, cut, holdout_start)`` where ``cut`` is the frozen
    ``2026-01-02`` development split. The cut must lie strictly inside the
    authorized development interval ``(is_start, holdout_start)`` and every
    boundary must lie inside ``[is_start, holdout_start]``; otherwise the
    supplied window is refused with :class:`SubperiodBoundaryError`.

    Nothing here references the real-run holdout start: the last boundary is
    exactly the ``holdout_start`` of the window the caller supplied, so the
    dry-run template ends its last subperiod at the dry-run holdout start.
    """
    dates = _coerce_partition_dates(partition_dates)
    boundaries = (dates.is_start, _SUBPERIOD_CUT, dates.holdout_start)
    if not (dates.is_start < _SUBPERIOD_CUT < dates.holdout_start):
        raise SubperiodBoundaryError(
            "the frozen subperiod cut "
            f"{_SUBPERIOD_CUT.isoformat()} must lie strictly inside the "
            "authorized development window "
            f"({dates.is_start.isoformat()}, {dates.holdout_start.isoformat()}); "
            "refusing the supplied partition dates"
        )
    for boundary in boundaries:
        if boundary < dates.is_start or boundary > dates.holdout_start:
            raise SubperiodBoundaryError(
                f"subperiod boundary {boundary.isoformat()} lies outside the "
                "authorized development interval "
                f"[{dates.is_start.isoformat()}, "
                f"{dates.holdout_start.isoformat()}]; refusing the supplied "
                "partition dates"
            )
    return boundaries


def build_frozen_evaluation_spec_template(
    partition_dates: PilotPartitionDates | Mapping[str, Any],
    *,
    transaction_cost_bps: float = PILOT_TRANSACTION_COST_BPS,
) -> EvaluationSpec:
    """The frozen evaluation-spec template for one authorized dev window.

    The template is derived from the *supplied* partition dates: the split
    rule and the subperiod boundaries both come from that development window,
    so the real-run holdout start is never hardcoded here. Per section-26b
    correction 1 ``PARAMETER_SENSITIVITY`` is absent and ``parameter_grid`` is
    the single primary point ``(n_groups=3, horizon=1, cost_bps=C,
    winsorization=0.01)``. ``factor_provenance_hash`` remains the all-zero
    placeholder substituted by :func:`build_evaluation_spec`.

    ``SubperiodBoundaryError`` is raised when the window cannot carry the
    frozen ``2026-01-02`` cut. The remaining executability fixes are
    documented in the module docstring. ``C = 10`` bps ``ONE_WAY`` is the
    user freeze.
    """
    dates = _coerce_partition_dates(partition_dates)
    boundaries = subperiod_boundaries(dates)
    holdout_length = (dates.holdout_end - dates.holdout_start).days + 1
    return EvaluationSpec(
        metrics=(
            MetricKey.IC,
            MetricKey.RANK_IC,
            MetricKey.LONG_SHORT,
            MetricKey.SHARPE,
            MetricKey.MAX_DRAWDOWN,
            MetricKey.TURNOVER_COST_ADJUSTED,
            MetricKey.SUBPERIOD,
        ),
        horizons=(1,),
        split_rule=SplitRule(
            is_start=dates.is_start,
            is_end=dates.is_end,
            oos_start=dates.oos_start,
            oos_end=dates.oos_end,
            walk_forward_folds=0,
            walk_forward_fold_length=1,
            holdout_length=holdout_length,
        ),
        subperiod_rule=SubperiodRule(boundaries=boundaries),
        parameter_grid=(
            ParameterPoint(
                n_groups=3,
                horizon=1,
                cost_bps=transaction_cost_bps,
                winsorization=0.01,
            ),
        ),
        universe_variants=("all",),
        cost_model=CostModel(
            transaction_cost_bps=transaction_cost_bps, mode=CostMode.ONE_WAY
        ),
        benchmark=BenchmarkRef(kind=BenchmarkKind.NAMED, key="zero"),
        factor_provenance_hash="0" * 64,
    )


# ---------------------------------------------------------------------------
# frozen template -> per-proposal EvaluationSpec
# ---------------------------------------------------------------------------


def _require_sha256_text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not re.match(r"^[0-9a-f]{64}$", value):
        raise DesignInputError(
            f"{field_name} must be a 64-char lowercase hex SHA-256, got {value!r}"
        )
    return value


def _coerce_template(value: EvaluationSpec | Mapping[str, Any]) -> EvaluationSpec:
    if isinstance(value, EvaluationSpec):
        return value
    if isinstance(value, Mapping):
        return EvaluationSpec.from_dict(value)
    raise DesignInputError(
        "evaluation_spec_template must be an EvaluationSpec or its serialized "
        f"mapping form, got {type(value).__name__}"
    )


def build_evaluation_spec(
    template: EvaluationSpec | Mapping[str, Any],
    *,
    factor_provenance_hash: str,
) -> EvaluationSpec:
    """Clone the frozen template varying **only** ``factor_provenance_hash``.

    The Phase-6 ``EngineResult.content_hash`` is the only per-proposal field;
    every other field must equal the template exactly, which is asserted here
    (a template whose canonical form changes in any other field fails closed
    with :class:`DesignTemplateIntegrityError`).
    """
    resolved = _coerce_template(template)
    provenance = _require_sha256_text(
        factor_provenance_hash, field_name="factor_provenance_hash"
    )
    spec = dataclasses.replace(resolved, factor_provenance_hash=provenance)

    before = resolved.to_dict()
    after = spec.to_dict()
    for payload in (before, after):
        payload.pop("spec_hash", None)
        payload.pop("factor_provenance_hash", None)
    if before != after:
        raise DesignTemplateIntegrityError(
            "the per-proposal EvaluationSpec varied a field other than "
            "factor_provenance_hash"
        )
    if spec.factor_provenance_hash != provenance:
        raise DesignTemplateIntegrityError(
            "the per-proposal EvaluationSpec did not carry the requested "
            "factor_provenance_hash"
        )
    return spec


# ---------------------------------------------------------------------------
# configured dates -> sealed Partition
# ---------------------------------------------------------------------------


def _fold(role: FoldRole, start: date, end_inclusive: date) -> Fold:
    return Fold(role=role, start=start, end=end_inclusive + timedelta(days=1))


def build_partition(
    partition_dates: PilotPartitionDates | Mapping[str, Any],
) -> Partition:
    """Build the frozen sealed :class:`Partition` from configured dates.

    The configured endpoints are inclusive; the sealed :class:`Fold` contract
    is half-open ``[start, end)``, so each inclusive end is advanced by one
    calendar day. The final holdout fold is the chronologically last fold, as
    the sealed contract requires.
    """
    dates = (
        partition_dates
        if isinstance(partition_dates, PilotPartitionDates)
        else PilotPartitionDates.from_dict(partition_dates)
    )
    return Partition(
        folds=(
            _fold(FoldRole.IS, dates.is_start, dates.is_end),
            _fold(FoldRole.OOS, dates.oos_start, dates.oos_end),
            _fold(FoldRole.HOLDOUT, dates.holdout_start, dates.holdout_end),
        ),
        split_rule=dates.split_rule,
    )


# ---------------------------------------------------------------------------
# frozen holdout identity
# ---------------------------------------------------------------------------


def _default_dataset_provenance(pilot_data: PilotData) -> str:
    return pilot_data.provenance.content_hash()


def _default_universe_id(pilot_data: PilotData) -> str:
    return content_hash({"universe": list(pilot_data.provenance.universe)})


def build_holdout_identity(
    *,
    partition: Partition,
    pilot_data: PilotData,
    dataset_provenance: str | None = None,
    universe_id: str | None = None,
) -> HoldoutIdentity:
    """Build the frozen :class:`HoldoutIdentity` (plan section 13 step 5).

    The identity is a pure function of the frozen dataset provenance, the
    universe identity, the partition's final-holdout interval, the single
    target ``daily_total_return``, the horizon ``1`` and the partition id. It
    is therefore stable across proposals that share the frozen config, and
    the final holdout must be present (Phase 7 fails closed otherwise).
    """
    if not isinstance(partition, Partition):
        raise DesignInputError(
            f"partition must be a Partition, got {type(partition).__name__}"
        )
    if not isinstance(pilot_data, PilotData):
        raise DesignInputError(
            f"pilot_data must be a PilotData, got {type(pilot_data).__name__}"
        )
    holdout = partition.holdout_fold
    if holdout is None:
        raise DesignInputError(
            "the frozen partition defines no final-holdout fold; the holdout "
            "identity cannot be built"
        )
    return HoldoutIdentity(
        dataset_provenance=(
            _default_dataset_provenance(pilot_data)
            if dataset_provenance is None
            else dataset_provenance
        ),
        universe_id=(
            _default_universe_id(pilot_data)
            if universe_id is None
            else universe_id
        ),
        start_date=holdout.start.date(),
        end_date=holdout.end.date(),
        target_id=DAILY_RETURN_TARGET,
        horizon=PILOT_HOLDOUT_HORIZON,
        partition_id=partition.partition_id,
        label=PILOT_SPLIT_RULE_ID,
    )


# ---------------------------------------------------------------------------
# the provider
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProposalDesign:
    """The deterministic P1A-G2 design outcome for one admitted factor.

    ``experiment_design``/``engine_result``/``evaluation_spec`` are ``None``
    exactly when admission failed closed; in that case
    ``required_data_certified`` is ``False`` and ``admission_result`` carries
    every structured reason. No evidence is fabricated on the failure path.
    """

    admission_result: AdmissionResult
    engine_result: EngineResult | None
    evaluation_spec: EvaluationSpec | None
    partition: Partition
    holdout_identity: HoldoutIdentity
    required_data_certified: bool
    experiment_design: ExperimentDesign | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.admission_result, AdmissionResult):
            raise DesignInputError(
                "admission_result must be an AdmissionResult, got "
                f"{type(self.admission_result).__name__}"
            )
        if not isinstance(self.partition, Partition):
            raise DesignInputError(
                f"partition must be a Partition, got {type(self.partition).__name__}"
            )
        if not isinstance(self.holdout_identity, HoldoutIdentity):
            raise DesignInputError(
                "holdout_identity must be a HoldoutIdentity, got "
                f"{type(self.holdout_identity).__name__}"
            )
        if not isinstance(self.required_data_certified, bool):
            raise DesignInputError("required_data_certified must be a bool")
        if self.required_data_certified != self.admission_result.admitted:
            raise DesignInputError(
                "required_data_certified must equal the sealed admission "
                "outcome (never defaulted)"
            )
        if self.required_data_certified:
            if not isinstance(self.experiment_design, ExperimentDesign):
                raise DesignInputError(
                    "a certified admission must carry a sealed ExperimentDesign"
                )
            if not isinstance(self.engine_result, EngineResult):
                raise DesignInputError(
                    "a certified admission must carry its EngineResult"
                )
            if not isinstance(self.evaluation_spec, EvaluationSpec):
                raise DesignInputError(
                    "a certified admission must carry its EvaluationSpec"
                )
        elif (
            self.experiment_design is not None
            or self.engine_result is not None
            or self.evaluation_spec is not None
        ):
            raise DesignInputError(
                "a failed admission must not fabricate evaluation evidence"
            )

    @property
    def admission_reasons(self) -> tuple[str, ...]:
        """Every structured, de-duplicated admission reason, in alias order."""
        reasons: list[str] = []
        for admission in self.admission_result.aliases:
            reasons.extend(admission.reasons)
        return tuple(dict.fromkeys(reasons))

    @property
    def is_certified(self) -> bool:
        return self.required_data_certified

    def as_tuple(
        self,
    ) -> tuple[
        ExperimentDesign | None,
        AdmissionResult,
        EngineResult | None,
        EvaluationSpec | None,
        Partition,
        HoldoutIdentity,
    ]:
        """The frozen section-13 output tuple."""
        return (
            self.experiment_design,
            self.admission_result,
            self.engine_result,
            self.evaluation_spec,
            self.partition,
            self.holdout_identity,
        )

    def require_experiment_design(self) -> ExperimentDesign:
        """Return the sealed design, or raise the typed certification stop."""
        if self.experiment_design is None:
            raise DataNotCertifiedError(self)
        return self.experiment_design


def resolve_factor_spec(
    proposal: ResearchProposal | FactorSpec,
) -> FactorSpec:
    """Resolve the admitted :class:`FactorSpec` a design is built for.

    Accepts a :class:`ResearchProposal` whose ``proposed_factor_spec`` is a
    resolved :class:`FactorSpec`, or a bare :class:`FactorSpec`. An unresolved
    :class:`~smart_beta.research.proposal.FactorTemplateRef` fails closed: the
    research loop resolves templates before the design provider is called, and
    G2 never expands a template itself.
    """
    if isinstance(proposal, FactorSpec):
        return proposal
    if not isinstance(proposal, ResearchProposal):
        raise DesignInputError(
            "expected a ResearchProposal or FactorSpec, got "
            f"{type(proposal).__name__}"
        )
    spec = proposal.proposed_factor_spec
    if isinstance(spec, FactorTemplateRef):
        raise DesignInputError(
            "the proposal carries an unresolved FactorTemplateRef; the research "
            "loop must admit a concrete FactorSpec before design"
        )
    if not isinstance(spec, FactorSpec):  # pragma: no cover - defensive
        raise DesignInputError(
            f"proposal carries no usable FactorSpec, got {type(spec).__name__}"
        )
    return spec


def _bind_inputs(factor_spec: FactorSpec, pilot_data: PilotData):
    """Bind the single admitted G1 input to every referenced alias.

    The sealed Phase-6 admission boundary decides whether each alias's
    requirement is satisfied; this function never selects or substitutes.
    """
    return {
        alias: pilot_data.trusted_input
        for alias in factor_spec.referenced_roles
    }


def build_design(
    *,
    factor_spec: FactorSpec,
    pilot_data: PilotData,
    partition_dates: PilotPartitionDates | Mapping[str, Any],
    decision_policy: DecisionPolicy,
    search_policy: SearchPolicy,
    evaluation_spec_template: EvaluationSpec | Mapping[str, Any] | None = None,
    dataset_provenance: str | None = None,
    universe_id: str | None = None,
    periods_per_year: int = PILOT_PERIODS_PER_YEAR,
) -> ProposalDesign:
    """Build the sealed per-proposal experiment design (plan section 13).

    Order of operations (frozen): data admission + factor evaluation through
    the sealed Phase-6 facade; the per-proposal ``EvaluationSpec`` from the
    frozen template; the frozen ``Partition``; Phase-7 ``evaluate`` with
    ``periods_per_year=252``; the ``HoldoutIdentity``; then the sealed
    ``ExperimentDesign`` with the derived ``required_data_certified``.

    When Phase-6 admission fails closed the function returns a
    :class:`ProposalDesign` with ``required_data_certified=False`` and the
    structured admission reasons; it never defaults the flag and never
    fabricates an ``EvaluationRecord``.
    """
    if not isinstance(factor_spec, FactorSpec):
        raise DesignInputError(
            f"factor_spec must be a FactorSpec, got {type(factor_spec).__name__}"
        )
    if not isinstance(pilot_data, PilotData):
        raise DesignInputError(
            f"pilot_data must be a PilotData, got {type(pilot_data).__name__}"
        )
    if not isinstance(decision_policy, DecisionPolicy):
        raise DesignInputError(
            "decision_policy must be a DecisionPolicy, got "
            f"{type(decision_policy).__name__}"
        )
    if not isinstance(search_policy, SearchPolicy):
        raise DesignInputError(
            f"search_policy must be a SearchPolicy, got {type(search_policy).__name__}"
        )
    if periods_per_year != PILOT_PERIODS_PER_YEAR:
        raise DesignInputError(
            "the frozen G2 provider calls evaluation.engine.evaluate with "
            f"periods_per_year={PILOT_PERIODS_PER_YEAR}; got {periods_per_year!r}"
        )

    dates = _coerce_partition_dates(partition_dates)
    template = (
        build_frozen_evaluation_spec_template(dates)
        if evaluation_spec_template is None
        else _coerce_template(evaluation_spec_template)
    )
    partition = build_partition(dates)
    holdout_identity = build_holdout_identity(
        partition=partition,
        pilot_data=pilot_data,
        dataset_provenance=dataset_provenance,
        universe_id=universe_id,
    )

    inputs = _bind_inputs(factor_spec, pilot_data)
    try:
        engine_result = evaluate_factor(factor_spec, inputs)
    except AdmissionError as exc:
        # Phase 6 failed closed: derive the certification signal from the
        # actual admission outcome and never fabricate evaluation evidence.
        return ProposalDesign(
            admission_result=exc.result,
            engine_result=None,
            evaluation_spec=None,
            partition=partition,
            holdout_identity=holdout_identity,
            required_data_certified=False,
            experiment_design=None,
        )

    evaluation_spec = build_evaluation_spec(
        template, factor_provenance_hash=engine_result.content_hash
    )
    record = evaluate(
        engine_result.evaluation.panel,
        evaluation_spec,
        pilot_data.realized_returns,
        partition,
        periods_per_year=periods_per_year,
    )
    experiment_design = ExperimentDesign(
        evaluation_spec=evaluation_spec,
        decision_policy=decision_policy,
        search_policy=search_policy,
        record=record,
        holdout_identity=holdout_identity,
        required_data_certified=engine_result.admission.admitted,
    )
    return ProposalDesign(
        admission_result=engine_result.admission,
        engine_result=engine_result,
        evaluation_spec=evaluation_spec,
        partition=partition,
        holdout_identity=holdout_identity,
        required_data_certified=engine_result.admission.admitted,
        experiment_design=experiment_design,
    )


def make_design_provider(
    *,
    pilot_data: PilotData,
    partition_dates: PilotPartitionDates | Mapping[str, Any],
    decision_policy: DecisionPolicy,
    search_policy: SearchPolicy,
    evaluation_spec_template: EvaluationSpec | Mapping[str, Any] | None = None,
    dataset_provenance: str | None = None,
    universe_id: str | None = None,
) -> Callable[[ResearchProposal], ExperimentDesign]:
    """Return the ``design_provider`` the P9 loop consumes.

    The returned callable resolves each admitted proposal's concrete
    ``FactorSpec`` and builds the sealed design. When admission fails closed
    it raises :class:`DataNotCertifiedError` (carrying the derived ``False``
    signal and every admission reason) rather than returning a fabricated
    design; the runner maps that to the typed ``DATA_NOT_PIT_CERTIFIED`` stop.
    """

    def provider(proposal: ResearchProposal) -> ExperimentDesign:
        factor_spec = resolve_factor_spec(proposal)
        design = build_design(
            factor_spec=factor_spec,
            pilot_data=pilot_data,
            partition_dates=partition_dates,
            decision_policy=decision_policy,
            search_policy=search_policy,
            evaluation_spec_template=evaluation_spec_template,
            dataset_provenance=dataset_provenance,
            universe_id=universe_id,
        )
        return design.require_experiment_design()

    return provider
