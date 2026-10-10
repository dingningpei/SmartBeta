"""Campaign-3 P7 task R7-X — the authorized reserve confirmation executor.

Authority: ``docs/campaign3_p7_implementation_spec.md`` §3 (row R7-X) / §4 and
``docs/campaign3_p7_reserve_path_design.md`` (frozen decisions: the last
reserve formation is <= C - 21 trusted sessions; confirmation requires **both**
B and the P-Q L envelope to pass under all T0/TS x K0/K1 combinations, using
the admitted ``EWC_T23_ONE_SIDED``).

The executor runs the design's pipeline **once**, for exactly one hypothesis:

1. **ACCESS check.** :meth:`AccessGrant.from_kpit` reads and verifies the
   authorizing K-PIT ``ACCESS`` record (hypothesis, admitted procedure, exact
   ``reserve_chain`` module source hashes, reserve fold window).
2. **Write-ahead consumption.** The single-use marker is appended to the K-PIT
   ledger *before* any reserve path is derived, so a crashed or repeated run
   cannot derive a second time (K-PIT is the ledger).
3. **Derive.** The reserve ``FoldWindow`` from the grant is handed to an
   injected ``derive_inputs`` callable, which wires the R7-U universe, R7-T
   trusted-input and R7-R returns/label fold builders (P6 supplies the reserve
   bindings/accessors; tests inject a synthetic derivation).
4. **P-Q v2.4.** An injected ``build_diagnostics`` callable runs
   ``build_pqv2_diagnostics`` on the reserve ``Fold`` for the one hypothesis.
5. **Inference.** Each required B/L x T0/TS (x K0/K1) cell's per-period net
   long-short series is dispatched through ``science.inference.run_inference``
   with the admitted procedure.  There is no substitute procedure and no
   averaging across combinations.
6. **Assessment.** The per-cell inference results and the confirmation verdict
   are persisted (write-ahead state + assessment) if a store is supplied.

The executor computes nothing for any other hypothesis: the diagnostics must
name exactly the authorized hypothesis, and only that hypothesis's cells are
read.  This module reads no data path itself; every path-opening step is
injected.  It opens no reserve path in tests.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from smart_beta.dataset_v1.reserve_chain.access import (
    ADMITTED_PROCEDURE_CONTRACT_HASH,
    ADMITTED_PROCEDURE_ID,
    ADMITTED_PROCEDURE_VERSION,
    AccessGrant,
    build_consumption_payload,
)
from smart_beta.dataset_v1.reserve_chain.fold import FoldWindow
from smart_beta.evaluation.spec import Series
from smart_beta.science.contracts import (
    Channel,
    Direction,
    EstimandKind,
    MissingnessPolicy,
    RecordKind,
    canonical_json,
    content_hash,
)
from smart_beta.science.inference import (
    InferenceRequest,
    InferenceResult,
    InferenceStatus,
    run_inference,
)

__all__ = [
    "B_CELLS",
    "L_CELLS",
    "CONFIRMATION_CELLS",
    "ConfirmationError",
    "ConfirmationRefused",
    "CellInference",
    "ConfirmationAssessment",
    "ConfirmationStore",
    "ReserveDerivation",
    "ReserveFoldInputs",
    "derive_reserve_fold_inputs",
    "pqv2_reserve_builder",
    "to_pqv2_fold",
    "required_cell_names",
    "run_confirmation",
]

#: The observed net long-short (B) cells and the P-Q L lower-envelope cells.
B_CELLS: tuple[str, ...] = ("B-T0", "B-TS")
L_CELLS: tuple[str, ...] = ("L-T0", "L-TS")
CONFIRMATION_CELLS: tuple[str, ...] = B_CELLS + L_CELLS


class ConfirmationError(RuntimeError):
    """Base class for every reserve-confirmation error (fail closed)."""


class ConfirmationRefused(ConfirmationError):
    """The confirmation was refused before, or instead of, an assessment."""


# ---------------------------------------------------------------------------
# the required-cell policy (both B and the L envelope)
# ---------------------------------------------------------------------------


def _requires_lower_envelope() -> bool:
    """The frozen decision: confirmation requires the P-Q L envelope too."""
    return True


def required_cell_names(diagnostics: Any) -> tuple[str, ...]:
    """The B/L cells the confirmation must dispatch, scenario-qualified.

    The P-Q v2.3/v2.4 event path qualifies every cell with ``-K0`` / ``-K1``;
    the v2.1/v2.2 path has no event scenarios and keeps the bare names.
    """
    bases = CONFIRMATION_CELLS if _requires_lower_envelope() else B_CELLS
    scenarios = tuple(getattr(diagnostics, "event_scenarios", ()) or ())
    if not scenarios:
        return bases
    return tuple(
        f"{base}-{scenario}" for scenario in scenarios for base in bases
    )


# ---------------------------------------------------------------------------
# value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CellInference:
    """One required cell's EWC dispatch outcome."""

    cell: str
    result: InferenceResult | None
    passed: bool | None

    def to_content(self) -> dict[str, Any]:
        return {
            "cell": self.cell,
            "passed": None if self.passed is None else bool(self.passed),
            "status": None if self.result is None else self.result.status.value,
            "p_one_sided": (
                None if self.result is None else self.result.p_one_sided
            ),
        }


@dataclass(frozen=True)
class ConfirmationAssessment:
    """The reserve confirmation's per-cell assessment and overall verdict."""

    hypothesis_id: str
    access_record_hash: str
    fold_start: date
    fold_end: date
    diagnostics_hash: str
    procedure_ref: Mapping[str, str]
    cells: tuple[CellInference, ...]
    confirmation: bool
    consumed_record_hash: str

    def to_content(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "access_record_hash": self.access_record_hash,
            "fold": {
                "start": self.fold_start.isoformat(),
                "end": self.fold_end.isoformat(),
            },
            "diagnostics_hash": self.diagnostics_hash,
            "procedure_ref": dict(self.procedure_ref),
            "cells": [cell.to_content() for cell in self.cells],
            "confirmation": bool(self.confirmation),
            "consumed_record_hash": self.consumed_record_hash,
        }

    @property
    def record_hash(self) -> str:
        return content_hash(self.to_content())


# ---------------------------------------------------------------------------
# write-ahead persistence
# ---------------------------------------------------------------------------


class ConfirmationStore:
    """A tiny atomic JSON store for write-ahead confirmation state.

    The single-use ledger is K-PIT; this store is the executor's own durable
    write-ahead state, so a crashed confirmation can be inspected without
    re-reading the reserve.
    """

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def _path(self, kind: str, content_hash_value: str) -> Path:
        if not kind or "/" in kind:
            raise ConfirmationError(f"invalid object kind {kind!r}")
        return self._root / kind / f"{content_hash_value}.json"

    def write_object(
        self, *, kind: str, content_hash_value: str, body: Mapping[str, Any]
    ) -> Path:
        path = self._path(kind, content_hash_value)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(canonical_json(body), encoding="utf-8")
        os.replace(tmp, path)
        return path

    def read_object(
        self, *, kind: str, content_hash_value: str
    ) -> Mapping[str, Any]:
        path = self._path(kind, content_hash_value)
        return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# reserve derivation (injected; P6 supplies the real bindings/accessors)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReserveDerivation:
    """The P6-supplied inputs a reserve fold derivation needs.

    Tests never build this; they inject a synthetic ``derive_inputs`` instead.
    It exists so the real executor has a documented wiring point for the R7-U
    universe file, the R7-T trusted inputs and the R7-R returns/labels.
    """

    root: Path
    universe_bindings: Any
    lookback_bindings: Any
    trusted_bindings: Any
    trusted_reserve_bindings: Any
    calendar: Any
    observations: Any
    ca_events: Any
    identity: Any
    day_flags: Any
    accessor: Any = None
    reserve_accessor: Any = None
    hmax: int = 21


@dataclass(frozen=True)
class ReserveFoldInputs:
    """The derived reserve fold inputs handed to the P-Q diagnostics builder."""

    universe: Any
    trusted: Any
    returns: Any


def to_pqv2_fold(fold: FoldWindow) -> Any:
    """Convert the P7 :class:`FoldWindow` to the P-Q ``Fold`` value.

    A reserve fold becomes ``Fold("reserve", 2023-07-03, C)`` (P-Q v2.4 plan
    §1); a development fold becomes the frozen development ``Fold``.
    """
    from smart_beta.dataset_v1 import pqv2_core as _core

    if fold.source == "development":
        return _core.DEVELOPMENT_FOLD
    return _core.Fold(name=fold.source, start=fold.start, end=fold.end)


def pqv2_reserve_builder(
    *,
    root: Any,
    declaration: Any,
    factor_spec: Any,
    factor_panel: Any,
    partition: Any,
    overlay: Any,
    inputs: Mapping[str, Any],
    pq_kwargs: Mapping[str, Any] | None = None,
) -> Callable[[FoldWindow, Any], Any]:
    """Return the real P-Q v2.4 ``build_diagnostics`` callable for a fold.

    This is the only place the executor touches the frozen P-Q evaluation:
    the returned callable converts the reserve :class:`FoldWindow` with
    :func:`to_pqv2_fold` and calls ``build_pqv2_diagnostics`` exactly once.
    """
    from smart_beta.dataset_v1.pqv2_evaluation import build_pqv2_diagnostics

    extras = dict(pq_kwargs or {})

    def _build(fold: FoldWindow, _derived: Any) -> Any:
        return build_pqv2_diagnostics(
            root,
            declaration,
            factor_spec,
            factor_panel,
            partition,
            overlay=overlay,
            inputs=inputs,
            fold=to_pqv2_fold(fold),
            **extras,
        )

    return _build


def derive_reserve_fold_inputs(
    fold: FoldWindow, derivation: ReserveDerivation
) -> ReserveFoldInputs:
    """Wire the R7-U/R7-T/R7-R fold builders for a reserve ``fold``.

    The reserve bindings and accessors are the objects constructed by P6 from
    the authorized grant; this function performs no path opening of its own
    beyond delegating to the fold builders.
    """
    import pandas as pd

    from smart_beta.dataset_v1.reserve_chain.returns_fold import build_returns_fold
    from smart_beta.dataset_v1.reserve_chain.trusted_fold import build_trusted_fold
    from smart_beta.dataset_v1.reserve_chain.universe_fold import run_universe_fold

    universe_content = run_universe_fold(
        derivation.root,
        fold=fold,
        bindings=derivation.universe_bindings,
        lookback_bindings=derivation.lookback_bindings,
        calendar=derivation.calendar,
    )
    # The membership frame the returns builder consumes.
    universe = pd.read_parquet(
        Path(derivation.root)
        / "derived"
        / "universe"
        / str(universe_content["universe_id"])
        / "universe_development.parquet"
    )
    trusted = build_trusted_fold(
        derivation.root,
        fold=fold,
        bindings=derivation.trusted_bindings,
        reserve_bindings=derivation.trusted_reserve_bindings,
        accessor=derivation.accessor,
        reserve_accessor=derivation.reserve_accessor,
        calendar=derivation.calendar,
    )
    returns = build_returns_fold(
        observations=derivation.observations,
        ca_events=derivation.ca_events,
        identity=derivation.identity,
        day_flags=derivation.day_flags,
        universe=universe,
        fold=fold,
        calendar=derivation.calendar,
        hmax=derivation.hmax,
    )
    return ReserveFoldInputs(universe=universe, trusted=trusted, returns=returns)


# ---------------------------------------------------------------------------
# cell dispatch
# ---------------------------------------------------------------------------


def _as_series(cell: Any) -> Series:
    return Series(
        name=f"reserve_confirm:{cell.cell}",
        index=tuple(cell.period_identities),
        values=tuple(cell.period_values),
    )


def _member_contract(
    *, procedure_ref: Mapping[str, str], direction: Direction, bound_alpha: float
) -> dict[str, Any]:
    return {
        "procedure_ref": dict(procedure_ref),
        "estimand_kind": EstimandKind.MEAN_NET_LONG_SHORT,
        "direction": direction,
        "params": {"bound_alpha": float(bound_alpha)},
        "missingness_policy": MissingnessPolicy.COMPLETE_REQUIRED,
        "bound_alpha": float(bound_alpha),
    }


def _dispatch_cell(
    cell: Any,
    *,
    procedure_ref: Mapping[str, str],
    direction: Direction,
    bound_alpha: float,
    registry: Any,
    admission_records: Sequence[Mapping[str, Any]],
    inference_runner: Callable[..., InferenceResult],
    series_record_hash: str,
) -> InferenceResult:
    request = InferenceRequest(
        member=_member_contract(
            procedure_ref=procedure_ref,
            direction=direction,
            bound_alpha=bound_alpha,
        ),
        series=_as_series(cell),
        series_record_hash=series_record_hash,
        expected_fold_index=tuple(cell.period_identities),
    )
    return inference_runner(request, registry, admission_records)


def _cell_passed(result: InferenceResult, bound_alpha: float) -> bool:
    """The one-sided EWC gate: a VALID dispatch significant at ``bound_alpha``."""
    if result.status is not InferenceStatus.VALID:
        return False
    if result.p_one_sided is None:
        return False
    return bool(float(result.p_one_sided) <= float(bound_alpha))


def _authorized_hypothesis(diagnostics: Any, hypothesis_id: str) -> str:
    """Refuse any diagnostics that name a different (second) hypothesis."""
    certified = getattr(diagnostics, "certified_hypothesis", None)
    if certified is None:
        raise ConfirmationRefused(
            "the reserve diagnostics do not name a frozen hypothesis"
        )
    if certified != hypothesis_id:
        raise ConfirmationRefused(
            f"the reserve diagnostics name hypothesis {certified!r}, not the "
            f"authorized {hypothesis_id!r}"
        )
    return certified


def _require_assessed_reserve_fold(diagnostics: Any) -> None:
    """Refuse a not-assessed diagnostics or a development-fold evaluation."""
    if getattr(diagnostics, "not_assessed", None) is not None:
        raise ConfirmationRefused(
            "the reserve diagnostics are not assessed: "
            f"{diagnostics.not_assessed}"
        )
    diag_fold = getattr(diagnostics, "fold", None)
    if diag_fold is not None and getattr(diag_fold, "is_development", False):
        raise ConfirmationRefused(
            "the reserve confirmation cannot run on the development fold"
        )


def _diagnostics_hash(diagnostics: Any) -> str:
    value = getattr(diagnostics, "diagnostics_hash", None)
    if value is None:
        value = content_hash(getattr(diagnostics, "to_dict")())
    return str(value)


# ---------------------------------------------------------------------------
# the executor
# ---------------------------------------------------------------------------


def run_confirmation(
    *,
    log: Any,
    access_record_hash: str,
    hypothesis_id: str,
    direction: Direction,
    bound_alpha: float,
    registry: Any,
    admission_records: Sequence[Mapping[str, Any]],
    derive_inputs: Callable[[FoldWindow], Any],
    build_diagnostics: Callable[[FoldWindow, Any], Any],
    store: ConfirmationStore | None = None,
    inference_runner: Callable[..., InferenceResult] = run_inference,
    program_id: str | None = None,
) -> ConfirmationAssessment:
    """Execute the authorized reserve confirmation for exactly one hypothesis.

    ``log`` is the K-PIT ledger (a disposable one in tests).  ``derive_inputs``
    and ``build_diagnostics`` are the injected reserve-derivation and P-Q v2.4
    steps; ``registry``/``admission_records`` are the admitted procedure's
    gates.  The function fails closed with :class:`ConfirmationRefused` or
    :class:`ReserveAccessDenied`.
    """
    if not isinstance(hypothesis_id, str) or not hypothesis_id:
        raise ConfirmationRefused("hypothesis_id must be a non-empty string")
    if not isinstance(bound_alpha, (int, float)) or isinstance(bound_alpha, bool):
        raise ConfirmationRefused("bound_alpha must be a number in (0, 0.5)")
    if not (0.0 < float(bound_alpha) < 0.5):
        raise ConfirmationRefused("bound_alpha must be in (0, 0.5)")
    if not isinstance(direction, Direction):
        raise ConfirmationRefused("direction must be a Direction")

    # 1. ACCESS gate (before any reserve path is opened).
    grant = AccessGrant.from_kpit(log, access_record_hash)
    if grant.hypothesis_id != hypothesis_id:
        raise ConfirmationRefused(
            f"the access grant authorizes hypothesis {grant.hypothesis_id!r}, "
            f"not {hypothesis_id!r}"
        )
    fold = FoldWindow.reserve(grant.fold_end, grant=grant)

    # 2. Write-ahead single-use consumption, before any derivation.
    consumed = log.append(
        kind=RecordKind.ACCESS,
        channel=Channel.SYSTEM,
        program_id=program_id,
        payload=build_consumption_payload(
            artifact_record_hash=grant.artifact_record_hash,
            hypothesis_id=hypothesis_id,
        ),
    )
    consumed_record_hash = consumed.record_hash

    if store is not None:
        state = {
            "phase": "authorized",
            "hypothesis_id": hypothesis_id,
            "access_record_hash": access_record_hash,
            "consumed_record_hash": consumed_record_hash,
            "fold": {
                "start": fold.start.isoformat(),
                "end": fold.end.isoformat(),
                "source": fold.source,
            },
        }
        store.write_object(
            kind="confirmation_state",
            content_hash_value=content_hash(state),
            body=state,
        )

    # 3. Derive the reserve fold inputs, then 4. P-Q v2.4 diagnostics.
    inputs = derive_inputs(fold)
    diagnostics = build_diagnostics(fold, inputs)

    # 5. Exactly the authorized hypothesis; never a second one.
    _authorized_hypothesis(diagnostics, hypothesis_id)
    _require_assessed_reserve_fold(diagnostics)

    procedure_ref = {
        "procedure_id": ADMITTED_PROCEDURE_ID,
        "version": ADMITTED_PROCEDURE_VERSION,
        "contract_hash": ADMITTED_PROCEDURE_CONTRACT_HASH,
    }
    series_record_hash = _diagnostics_hash(diagnostics)

    cell_assessments: list[CellInference] = []
    for name in required_cell_names(diagnostics):
        cell = diagnostics.cell(name)
        if cell is None:
            cell_assessments.append(CellInference(name, None, None))
            continue
        result = _dispatch_cell(
            cell,
            procedure_ref=procedure_ref,
            direction=direction,
            bound_alpha=float(bound_alpha),
            registry=registry,
            admission_records=admission_records,
            inference_runner=inference_runner,
            series_record_hash=series_record_hash,
        )
        cell_assessments.append(
            CellInference(name, result, _cell_passed(result, float(bound_alpha)))
        )

    confirmation = bool(cell_assessments) and all(
        item.passed is True for item in cell_assessments
    )

    assessment = ConfirmationAssessment(
        hypothesis_id=hypothesis_id,
        access_record_hash=access_record_hash,
        fold_start=fold.start,
        fold_end=fold.end,
        diagnostics_hash=series_record_hash,
        procedure_ref=procedure_ref,
        cells=tuple(cell_assessments),
        confirmation=confirmation,
        consumed_record_hash=consumed_record_hash,
    )

    # 6. Persist the assessment after the work (state was written ahead).
    if store is not None:
        store.write_object(
            kind="confirmation_assessment",
            content_hash_value=assessment.record_hash,
            body=assessment.to_content(),
        )

    return assessment
