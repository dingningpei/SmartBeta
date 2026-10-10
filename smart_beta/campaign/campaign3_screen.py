"""C3-S -- Campaign-3 IS ranking, T-B selection with a floor, and forwarding.

Authority: the frozen ``docs/campaign3_p6_implementation_spec.md`` (task row
C3-S) implementing the frozen ``docs/campaign3_p5_precommitment.md`` decisions
D8-D12 (plus D11 / E1) and the frozen P4 ranking design (D6, D9).  This module
owns the P6 selection tier only:

* **D9 -- IS ranking.**  For every ASSESSED + CERTIFIED candidate, take the
  per-period **L-envelope** series from its verified
  :mod:`smart_beta.dataset_v1.pqv2_series` export (the envelope restricted to
  B's finite-period index), for every ``{T0, TS} x {K0, K1}`` scenario,
  multiply it by the candidate's declared ``FactorSpec.sign``, and dispatch it
  through the admitted ``EWC_T23_ONE_SIDED`` procedure with
  :func:`smart_beta.science.inference.run_inference`.  The rank statistic is
  the **minimum** t over the four scenarios.  Any INVALID or NOT_ASSESSED
  scenario makes the candidate ineligible.  Ties are broken by the frozen
  canonical key and the top ``k = 5`` are selected.  For non-event candidates
  ``K0 == K1`` by construction, so the single evaluation is used for both.
* **TB_SELECTION.**  The five selected hypothesis ids, the positive control
  and the ranking-table content hash are handed to an **injected** writer that
  must succeed **before** any T-B evaluation (the P6 K-PIT ordering).
* **T-B evaluation.**  An **injected** evaluator is driven for exactly those
  six hypotheses; any other hypothesis id is refused (the T-B firewall), and
  the returned diagnostics must name the requested hypothesis.
* **D8 floor.**  The one-sided EWC p-value must be ``<= 0.05`` for **both** the
  observed declaration-metric B series and the L envelope under **all four**
  scenarios; the threshold is **unadjusted** (never Holm).
* **D10 forwarding.**  The floor passer with the largest minimum-scenario L
  EWC t is forwarded; the control (D11) is **never** forwardable.  If none
  passes, the decision is ``NO_RESERVE_USE``.
* **D12 break-even.**  ``10 bps * gross / (gross - net)`` per T-B result, from
  the export's observed gross/net per-period series; reported non-gating and
  labelled ``ASSUMED``.  If those series are not available, the run stops with
  the available fields named.

**Erratum P6-E1 (item 2).**  Every per-period series is consumed from the
verified ``pqv2_series`` export records -- the B series is the observed
**net** (declaration-metric) series and the L series is the envelope restricted
to B's finite-period index.  ``cell.period_values`` is **never** read (it is
the composed envelope array for every cell, including ``B-*``); the
consumer-side export binding check is fail-closed and the primary metric is
asserted to be NET (``TURNOVER_COST_ADJUSTED``).

The report (JSON + Markdown) carries every candidate's IS rank row, the
DESCRIPTIVE, NOT CLAIMS T-B table, the control's pipeline verdict, the
forwarding decision and the frozen P5 section 2 power table verbatim.  There
is no T-C computation anywhere in this module.

**Reuse.**  The admitted-procedure reference, the required-cell policy and the
inference member contract are imported read-only from
:mod:`smart_beta.dataset_v1.reserve_chain.confirm` and
:mod:`smart_beta.science.inference`; the procedure registry and admission
records are supplied by the caller.  No data path is opened, no network is
reached and no credential is read.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from smart_beta.dataset_v1.pqv2_series import (
    PQv2SeriesExport,
    PQv2SeriesRecord,
)
from smart_beta.dataset_v1.reserve_chain.access import (
    ADMITTED_PROCEDURE_CONTRACT_HASH,
    ADMITTED_PROCEDURE_ID,
    ADMITTED_PROCEDURE_VERSION,
)
from smart_beta.dataset_v1.reserve_chain.confirm import (
    _member_contract,
    required_cell_names,
)
from smart_beta.evaluation.spec import Series
from smart_beta.science.contracts import Direction, content_hash
from smart_beta.science.inference import (
    InferenceRequest,
    InferenceResult,
    InferenceStatus,
    run_inference,
)

__all__ = [
    # frozen literals
    "RANK_K",
    "FLOOR_ALPHA",
    "ONE_WAY_COST_BPS",
    "SCENARIOS",
    "EVENT_SCENARIOS",
    "POSITIVE_CONTROL_ID",
    "POSITIVE_CONTROL_HOLD_MONTHS",
    "POSITIVE_CONTROL_SIGN",
    "RANK_FAMILY",
    "FLOOR_FAMILIES",
    "NET_METRIC_BASIS",
    "P5_POWER_TABLE_MARKDOWN",
    "TB_CLAIM_LABEL",
    "ADMITTED_PROCEDURE_REF",
    # errors
    "ScreenError",
    "ScreenConfigurationError",
    "TbFirewallError",
    "SeriesExportError",
    "SeriesRecordUnavailable",
    "BreakEvenUnavailable",
    # value objects
    "ScreeningCandidate",
    "ScenarioInference",
    "RankRow",
    "RankingTable",
    "TbSelection",
    "TbDiagnostics",
    "FloorScenario",
    "TbHypothesisResult",
    "ForwardingDecision",
    "ScreenReport",
    # functions
    "rank_candidates",
    "write_tb_selection",
    "evaluate_tb",
    "forward_decision",
    "break_even_cost_bps",
    "run_screen",
    "render_markdown",
    "write_report",
]

# ---------------------------------------------------------------------------
# frozen literals
# ---------------------------------------------------------------------------

#: D6 / D9: the IS top-k forwarded to T-B.
RANK_K: int = 5

#: D8: the unadjusted one-sided EWC floor for B and L under all scenarios.
FLOOR_ALPHA: float = 0.05

#: D12: the frozen one-way TOP_1000 cost in bps (the P4/T-A literal).
ONE_WAY_COST_BPS: float = 10.0

#: The two P-Q return scenarios.
SCENARIOS: tuple[str, ...] = ("T0", "TS")

#: The two P-Q v2.3 event-timing scenarios.
EVENT_SCENARIOS: tuple[str, ...] = ("K0", "K1")

#: The P4/P5 HUMAN-channel positive control (D11): ``event_ear_k_kp1``,
#: 1-month hold, sign ``+1``.
POSITIVE_CONTROL_ID: str = "event_ear_k_kp1"
POSITIVE_CONTROL_HOLD_MONTHS: int = 1
POSITIVE_CONTROL_SIGN: int = 1

#: D9: the IS rank statistic is the L-envelope (never B).
RANK_FAMILY: str = "L"

#: D8: the floor requires B **and** the L envelope.
FLOOR_FAMILIES: tuple[str, ...] = ("B", "L")

#: E1: the campaign's primary metric is NET (the turnover-cost-adjusted
#: long-short return), so the exported B series is the net series.
NET_METRIC_BASIS: str = "TURNOVER_COST_ADJUSTED"

#: P5 section 2's power table, reproduced verbatim in every report.
P5_POWER_TABLE_MARKDOWN: str = (
    "| Stage | α | SR for 50% power | SR for 80% power | "
    "Power at SR = 0.5 / 1.0 / 1.5 |\n"
    "|---|---|---|---|---|\n"
    "| T-B, Holm first step over 6 | 0.0083 | **1.59** | 2.16 | "
    "0.05 / 0.19 / 0.45 |\n"
    "| T-B, unadjusted | 0.05 | 1.01 | 1.53 | 0.20 / 0.49 / 0.78 |\n"
    "| T-C, m = 1 | 0.05 | **1.00** | 1.51 | 0.21 / 0.50 / 0.79 |"
)

#: The mandatory label on every T-B number (D8): descriptive, not a claim.
TB_CLAIM_LABEL: str = "DESCRIPTIVE, NOT CLAIMS"

#: The admitted procedure the ranking and floor dispatch (frozen EWC v1.0.0).
ADMITTED_PROCEDURE_REF: Mapping[str, str] = {
    "procedure_id": ADMITTED_PROCEDURE_ID,
    "version": ADMITTED_PROCEDURE_VERSION,
    "contract_hash": ADMITTED_PROCEDURE_CONTRACT_HASH,
}


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------


class ScreenError(RuntimeError):
    """Base class for every C3-S fail-closed error."""


class ScreenConfigurationError(ScreenError):
    """The caller supplied an inconsistent screening configuration."""


class TbFirewallError(ScreenError):
    """A hypothesis other than the five selected plus the control touched T-B."""


class SeriesExportError(ScreenError):
    """The ``pqv2_series`` export is missing, mismatched or unbound."""


class SeriesRecordUnavailable(SeriesExportError):
    """One required export record is missing or not assessed (ineligible)."""


class BreakEvenUnavailable(ScreenError):
    """The D12 gross/net series are not available from the T-B export."""


# ---------------------------------------------------------------------------
# value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScreeningCandidate:
    """One IS candidate handed to the ranking.

    ``diagnostics`` is the persisted P-Q IS diagnostics record (used only for
    eligibility, the primary-metric assertion and the cell/export binding);
    ``series_export`` is its verified :mod:`pqv2_series` export, and **all**
    per-period B/L series are consumed from the export records, never from
    ``cell.period_values``.  Eligibility is ASSESSED (``not_assessed is None``)
    **and** CERTIFIED (``certified_class`` is set).  The control candidate
    carries ``diagnostics=None``/``series_export=None``; it never ranks.
    """

    hypothesis_id: str
    canonical_key: str
    sign: int
    diagnostics: Any = None
    series_export: PQv2SeriesExport | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.hypothesis_id, str) or not self.hypothesis_id:
            raise ScreenConfigurationError("hypothesis_id must be non-empty text")
        if not isinstance(self.canonical_key, str) or not self.canonical_key:
            raise ScreenConfigurationError("canonical_key must be non-empty text")
        if self.sign not in (1, -1):
            raise ScreenConfigurationError("sign must be +1 or -1")

    @property
    def assessed(self) -> bool:
        return getattr(self.diagnostics, "not_assessed", None) is None

    @property
    def certified(self) -> bool:
        return getattr(self.diagnostics, "certified_class", None) is not None

    @property
    def eligible(self) -> bool:
        return (
            self.diagnostics is not None
            and self.series_export is not None
            and self.assessed
            and self.certified
        )


@dataclass(frozen=True)
class ScenarioInference:
    """One ``{scenario} x {K}`` L-envelope EWC dispatch outcome."""

    scenario: str
    cell: str
    status: str | None
    statistic: float | None
    p_one_sided: float | None
    valid: bool

    def to_content(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "cell": self.cell,
            "status": self.status,
            "statistic": self.statistic,
            "p_one_sided": self.p_one_sided,
            "valid": bool(self.valid),
        }


@dataclass(frozen=True)
class RankRow:
    """One candidate's IS rank row (D9)."""

    hypothesis_id: str
    canonical_key: str
    sign: int
    eligible: bool
    ineligible_reason: str | None
    scenarios: tuple[ScenarioInference, ...]
    min_t: float | None
    selected: bool = False

    def to_content(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "canonical_key": self.canonical_key,
            "sign": int(self.sign),
            "eligible": bool(self.eligible),
            "ineligible_reason": self.ineligible_reason,
            "scenarios": [item.to_content() for item in self.scenarios],
            "min_t": self.min_t,
            "selected": bool(self.selected),
        }


@dataclass(frozen=True)
class RankingTable:
    """The full IS ranking table plus the top-k selection."""

    rows: tuple[RankRow, ...]
    selected: tuple[str, ...]
    k: int

    def to_content(self) -> dict[str, Any]:
        return {
            "schema": "campaign3-is-ranking-v1",
            "k": int(self.k),
            "selected": list(self.selected),
            "rows": [row.to_content() for row in self.rows],
        }

    @property
    def content_hash(self) -> str:
        return content_hash(self.to_content())


@dataclass
class TbSelection:
    """The write-ahead TB_SELECTION payload (five ids + control + rank hash)."""

    selected_ids: tuple[str, ...]
    control_id: str
    ranking_table_hash: str
    written: bool = False
    record_hash: str | None = None

    def to_content(self) -> dict[str, Any]:
        return {
            "schema": "campaign3-tb-selection-v1",
            "selected_ids": list(self.selected_ids),
            "control_id": self.control_id,
            "ranking_table_hash": self.ranking_table_hash,
        }


@dataclass(frozen=True)
class TbDiagnostics:
    """The injected T-B evaluation of exactly one hypothesis.

    ``diagnostics`` carries the P-Q cells for the OOS + DEVELOPMENT_SEALED
    folds (36 formations); ``series_export`` is its verified
    :mod:`pqv2_series` export.  Every per-period B/L/gross/net series is read
    from the export records, never from ``cell.period_values``.  If the export
    is ``None`` (or lacks assessed B records) the D12 break-even is
    unavailable and the run stops.
    """

    hypothesis_id: str
    diagnostics: Any
    series_export: PQv2SeriesExport | None = None


@dataclass(frozen=True)
class FloorScenario:
    """One B/L floor dispatch: its EWC p and the unadjusted D8 verdict."""

    cell: str
    family: str
    scenario: str
    status: str | None
    statistic: float | None
    p_one_sided: float | None
    passed: bool

    def to_content(self) -> dict[str, Any]:
        return {
            "cell": self.cell,
            "family": self.family,
            "scenario": self.scenario,
            "status": self.status,
            "statistic": self.statistic,
            "p_one_sided": self.p_one_sided,
            "passed": bool(self.passed),
        }


@dataclass(frozen=True)
class TbHypothesisResult:
    """One T-B hypothesis's floor, min-L-t and break-even result."""

    hypothesis_id: str
    sign: int
    scenarios: tuple[FloorScenario, ...]
    floor_pass: bool
    min_l_t: float | None
    break_even_by_scenario: Mapping[str, float | None]
    break_even_cost_bps: float | None
    break_even_label: str = "ASSUMED"
    is_control: bool = False

    def to_content(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "sign": int(self.sign),
            "is_control": bool(self.is_control),
            "scenarios": [item.to_content() for item in self.scenarios],
            "floor_pass": bool(self.floor_pass),
            "min_l_t": self.min_l_t,
            "break_even_by_scenario": dict(self.break_even_by_scenario),
            "break_even_cost_bps": self.break_even_cost_bps,
            "break_even_label": self.break_even_label,
        }


@dataclass(frozen=True)
class ForwardingDecision:
    """The D10 forwarding decision (``FORWARD`` or ``NO_RESERVE_USE``)."""

    decision: str
    hypothesis_id: str | None
    min_l_t: float | None

    def to_content(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "hypothesis_id": self.hypothesis_id,
            "min_l_t": self.min_l_t,
        }


@dataclass(frozen=True)
class ScreenReport:
    """The C3-S report payload (JSON + Markdown)."""

    ranking: RankingTable
    selection: TbSelection
    tb_results: tuple[TbHypothesisResult, ...]
    control_verdict: str
    forwarding: ForwardingDecision

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "campaign3-screen-report-v1",
            "tb_label": TB_CLAIM_LABEL,
            "p5_power_table_markdown": P5_POWER_TABLE_MARKDOWN,
            "break_even_label": "ASSUMED",
            "ranking": self.ranking.to_content(),
            "tb_selection": self.selection.to_content(),
            "tb_results": [item.to_content() for item in self.tb_results],
            "control_verdict": self.control_verdict,
            "forwarding": self.forwarding.to_content(),
        }


# ---------------------------------------------------------------------------
# scenario / cell policy
# ---------------------------------------------------------------------------


def _event_scenarios(diagnostics: Any) -> tuple[str, ...]:
    return tuple(getattr(diagnostics, "event_scenarios", ()) or ())


def _scenario_cells(
    diagnostics: Any, family: str
) -> tuple[tuple[str, str], ...]:
    """The ``(scenario_label, cell_name)`` slots for one cell family.

    The admitted-cell policy is :func:`required_cell_names` (the R7-X
    confirmation's frozen B/L x scenario policy); an event diagnostics has
    four distinct ``{T0, TS} x {K0, K1}`` cells, while the non-event path has
    only two bare cells and by construction ``K0 == K1``, so the single
    evaluation is used for both event scenarios.
    """
    names = [
        name
        for name in required_cell_names(diagnostics)
        if name.startswith(f"{family}-")
    ]
    if _event_scenarios(diagnostics):
        return tuple(
            (f"{name.split('-')[1]}-{name.split('-')[2]}", name)
            for name in names
        )
    slots: list[tuple[str, str]] = []
    for name in names:
        _, scenario = name.split("-")
        for event in EVENT_SCENARIOS:
            slots.append((f"{scenario}-{event}", name))
    return tuple(slots)


def _orientation_factor(family: str, sign: int) -> int:
    """The D9/D8 orientation: the L envelope is multiplied by the sign.

    The P-Q B statistics are already orientation-adjusted, so B is used as-is.
    """
    if family == "L":
        return int(sign)
    return 1


def _diagnostics_hash(diagnostics: Any) -> str:
    value = getattr(diagnostics, "diagnostics_hash", None)
    if value is None:
        to_dict = getattr(diagnostics, "to_dict", None)
        value = content_hash(to_dict()) if callable(to_dict) else content_hash({})
    return str(value)


def _declaration_hash(diagnostics: Any) -> str:
    return str(getattr(diagnostics, "declaration_hash", ""))


def _bind_export(
    diagnostics: Any, export: PQv2SeriesExport | None
) -> PQv2SeriesExport:
    """The consumer-side ``pqv2_series`` binding/export check (fail closed).

    The export must be present, must have been produced from exactly this
    diagnostics record, and the diagnostics' primary metric must be NET
    (``TURNOVER_COST_ADJUSTED``) so the exported B series is the net series the
    D8 floor consumes.  A missing/mismatched export is a hard stop: the screen
    never falls back to ``cell.period_values``.
    """
    if export is None:
        raise SeriesExportError("no pqv2_series export is bound to this evaluation")
    diagnostics_hash = _diagnostics_hash(diagnostics)
    if export.diagnostics_hash != diagnostics_hash:
        raise SeriesExportError(
            f"the pqv2_series export is bound to {export.diagnostics_hash!r}, "
            f"not to the diagnostics {diagnostics_hash!r}"
        )
    metric = str(getattr(diagnostics, "metric_basis", "") or "")
    if metric != NET_METRIC_BASIS:
        raise ScreenConfigurationError(
            f"the primary metric must be NET ({NET_METRIC_BASIS!r}), "
            f"got {metric!r}"
        )
    return export


def _checked_record(
    diagnostics: Any, export: PQv2SeriesExport | None, cell_name: str
) -> PQv2SeriesRecord:
    """The bound, assessed export record for ``cell_name`` (or fail closed)."""
    bound = _bind_export(diagnostics, export)
    record = bound.get(cell_name)
    if record is None:
        raise SeriesRecordUnavailable(
            f"the pqv2_series export has no record for {cell_name!r}"
        )
    if record.declaration_hash != _declaration_hash(diagnostics):
        raise SeriesRecordUnavailable(
            f"the pqv2_series record {cell_name!r} names declaration "
            f"{record.declaration_hash!r}, not {_declaration_hash(diagnostics)!r}"
        )
    if not record.assessed:
        raise SeriesRecordUnavailable(
            f"the pqv2_series record {cell_name!r} is not assessed"
        )
    return record


def _b_series(
    diagnostics: Any, export: PQv2SeriesExport | None, cell_name: str
) -> tuple[tuple[Any, ...], tuple[float, ...]]:
    """The observed declaration-metric (NET) B series from the export record."""
    record = _checked_record(diagnostics, export, cell_name)
    return record.periods, record.b_series


def _l_series(
    diagnostics: Any, export: PQv2SeriesExport | None, cell_name: str
) -> tuple[tuple[Any, ...], tuple[float, ...]]:
    """The L envelope restricted to B's finite index, from the export record."""
    record = _checked_record(diagnostics, export, cell_name)
    return record.periods, record.l_series


def _rank_series(
    diagnostics: Any, export: PQv2SeriesExport | None, cell_name: str
) -> tuple[tuple[Any, ...], tuple[float, ...]]:
    """The D9 ranking series: the L envelope (never the B series)."""
    if RANK_FAMILY == "B":
        return _b_series(diagnostics, export, cell_name)
    return _l_series(diagnostics, export, cell_name)


def _inference_series(
    name: str,
    periods: Sequence[Any],
    values: Sequence[float],
    sign: int,
) -> Series:
    """One oriented export series as a Phase-10 :class:`Series`."""
    return Series(
        name=name,
        index=tuple(periods),
        values=tuple(float(value) * int(sign) for value in values),
    )


def _dispatch_series(
    series: Series,
    *,
    procedure_ref: Mapping[str, str],
    bound_alpha: float,
    registry: Any,
    admission_records: Sequence[Mapping[str, Any]],
    inference_runner: Callable[..., InferenceResult],
    series_record_hash: str,
) -> InferenceResult:
    request = InferenceRequest(
        member=_member_contract(
            procedure_ref=dict(procedure_ref),
            direction=Direction.POSITIVE,
            bound_alpha=float(bound_alpha),
        ),
        series=series,
        series_record_hash=series_record_hash,
        expected_fold_index=tuple(series.index),
    )
    return inference_runner(request, registry, admission_records)


# ---------------------------------------------------------------------------
# D9 -- the IS ranking
# ---------------------------------------------------------------------------


def _rank_statistic(statistics: Sequence[float]) -> float:
    """D9: the minimum EWC t over the four scenarios."""
    return min(statistics)


def _rank_candidate(
    candidate: ScreeningCandidate,
    *,
    procedure_ref: Mapping[str, str],
    bound_alpha: float,
    registry: Any,
    admission_records: Sequence[Mapping[str, Any]],
    inference_runner: Callable[..., InferenceResult],
) -> RankRow:
    diagnostics = candidate.diagnostics
    export = candidate.series_export
    cache: dict[str, InferenceResult] = {}
    scenarios: list[ScenarioInference] = []
    if diagnostics is not None and export is not None:
        series_record_hash = export.export_hash
        for scenario, cell_name in _scenario_cells(diagnostics, RANK_FAMILY):
            try:
                periods, values = _rank_series(diagnostics, export, cell_name)
            except SeriesRecordUnavailable:
                scenarios.append(
                    ScenarioInference(scenario, cell_name, None, None, None, False)
                )
                continue
            factor = _orientation_factor(RANK_FAMILY, candidate.sign)
            result = cache.get(cell_name)
            if result is None:
                series = _inference_series(
                    f"campaign3_screen:{cell_name}", periods, values, factor
                )
                result = _dispatch_series(
                    series,
                    procedure_ref=procedure_ref,
                    bound_alpha=bound_alpha,
                    registry=registry,
                    admission_records=admission_records,
                    inference_runner=inference_runner,
                    series_record_hash=series_record_hash,
                )
                cache[cell_name] = result
            valid = (
                result.status is InferenceStatus.VALID
                and result.statistic is not None
            )
            scenarios.append(
                ScenarioInference(
                    scenario,
                    cell_name,
                    result.status.value,
                    None if result.statistic is None else float(result.statistic),
                    None
                    if result.p_one_sided is None
                    else float(result.p_one_sided),
                    valid,
                )
            )

    if not candidate.eligible:
        reason = "NOT_ASSESSED" if not candidate.assessed else "NOT_CERTIFIED"
        if candidate.diagnostics is None:
            reason = "NO_DIAGNOSTICS"
        return RankRow(
            candidate.hypothesis_id,
            candidate.canonical_key,
            candidate.sign,
            False,
            reason,
            tuple(scenarios),
            None,
        )

    if not scenarios or not all(item.valid for item in scenarios):
        invalid = next(
            (item.cell for item in scenarios if not item.valid), None
        )
        reason = (
            "MISSING_SCENARIO" if not scenarios else f"SCENARIO_INVALID:{invalid}"
        )
        return RankRow(
            candidate.hypothesis_id,
            candidate.canonical_key,
            candidate.sign,
            False,
            reason,
            tuple(scenarios),
            None,
        )

    statistics = [float(item.statistic) for item in scenarios]
    return RankRow(
        candidate.hypothesis_id,
        candidate.canonical_key,
        candidate.sign,
        True,
        None,
        tuple(scenarios),
        float(_rank_statistic(statistics)),
    )


def _rank_sort_key(row: RankRow) -> tuple[int, float, str]:
    # Eligible candidates first, by descending min-t, then canonical key.
    rank_t = row.min_t if row.min_t is not None else float("-inf")
    return (0 if row.eligible else 1, -rank_t, row.canonical_key)


def rank_candidates(
    candidates: Sequence[ScreeningCandidate],
    *,
    registry: Any,
    admission_records: Sequence[Mapping[str, Any]],
    procedure_ref: Mapping[str, str] | None = None,
    bound_alpha: float = FLOOR_ALPHA,
    k: int = RANK_K,
    inference_runner: Callable[..., InferenceResult] = run_inference,
) -> RankingTable:
    """D9: rank every ASSESSED + CERTIFIED candidate and select the top ``k``."""
    if k <= 0:
        raise ScreenConfigurationError("k must be a positive integer")
    ref = dict(procedure_ref or ADMITTED_PROCEDURE_REF)
    rows = [
        _rank_candidate(
            candidate,
            procedure_ref=ref,
            bound_alpha=bound_alpha,
            registry=registry,
            admission_records=admission_records,
            inference_runner=inference_runner,
        )
        for candidate in candidates
    ]
    ordered = sorted(rows, key=_rank_sort_key)
    selected = tuple(row.hypothesis_id for row in ordered if row.eligible)[:k]
    selected_set = set(selected)
    ordered = tuple(
        dataclasses.replace(row, selected=row.hypothesis_id in selected_set)
        for row in ordered
    )
    return RankingTable(rows=ordered, selected=selected, k=k)


# ---------------------------------------------------------------------------
# TB_SELECTION -- write-ahead, before any T-B evaluation
# ---------------------------------------------------------------------------


def _evaluation_ids(selection: TbSelection) -> tuple[str, ...]:
    """The exactly-six T-B ids: the five IS-selected candidates plus the control."""
    return tuple(selection.selected_ids) + (selection.control_id,)


def write_tb_selection(
    writer: Callable[[TbSelection], Any], selection: TbSelection
) -> TbSelection:
    """Write the TB_SELECTION record and mark the selection as written.

    The injected ``writer`` must succeed; if it raises, the exception
    propagates and no T-B evaluation may follow.
    """
    if not isinstance(selection, TbSelection):
        raise ScreenConfigurationError("selection must be a TbSelection")
    if selection.written:
        raise ScreenConfigurationError("the TB_SELECTION record is already written")
    record = writer(selection)
    if record is None:
        raise TbFirewallError("the TB_SELECTION writer returned no record")
    selection.written = True
    selection.record_hash = (
        record if isinstance(record, str) else getattr(record, "record_hash", None)
    )
    return selection


# ---------------------------------------------------------------------------
# T-B evaluation -- the firewall + D8 floor + D12 break-even
# ---------------------------------------------------------------------------


def _floor_significant(result: InferenceResult, alpha: float) -> bool:
    """D8: the unadjusted one-sided EWC floor (VALID and p <= alpha)."""
    if result.status is not InferenceStatus.VALID:
        return False
    if result.p_one_sided is None:
        return False
    return bool(float(result.p_one_sided) <= float(alpha))


def _available_fields(diagnostics: Any) -> tuple[str, ...]:
    fields: set[str] = set()
    if diagnostics is not None:
        to_dict = getattr(diagnostics, "to_dict", None)
        if callable(to_dict):
            try:
                fields.update(str(key) for key in to_dict())
            except Exception:  # pragma: no cover - defensive
                pass
        fields.update(
            name
            for name in dir(diagnostics)
            if not name.startswith("_")
        )
    return tuple(sorted(fields))


def break_even_cost_bps(
    *,
    gross_mean: float,
    net_mean: float,
    cost_bps: float = ONE_WAY_COST_BPS,
) -> float | None:
    """D12: ``cost_bps * gross / (gross - net)`` (non-gating, ASSUMED).

    Returns ``None`` when ``gross == net`` (the break-even is undefined and
    must not be invented).
    """
    gross = float(gross_mean)
    net = float(net_mean)
    if gross == net:
        return None
    return float(cost_bps) * gross / (gross - net)


def _floor_scenarios(
    diagnostics: Any,
    export: PQv2SeriesExport | None,
    sign: int,
    *,
    procedure_ref: Mapping[str, str],
    alpha: float,
    registry: Any,
    admission_records: Sequence[Mapping[str, Any]],
    inference_runner: Callable[..., InferenceResult],
) -> tuple[FloorScenario, ...]:
    scenarios: list[FloorScenario] = []
    if diagnostics is None:
        return ()
    series_record_hash = export.export_hash if export is not None else ""
    cache: dict[tuple[str, int], InferenceResult] = {}
    for family in FLOOR_FAMILIES:
        factor = _orientation_factor(family, sign)
        for scenario, cell_name in _scenario_cells(diagnostics, family):
            try:
                if family == "B":
                    periods, values = _b_series(diagnostics, export, cell_name)
                else:
                    periods, values = _l_series(diagnostics, export, cell_name)
            except SeriesRecordUnavailable:
                scenarios.append(
                    FloorScenario(cell_name, family, scenario, None, None, None, False)
                )
                continue
            key = (cell_name, factor)
            result = cache.get(key)
            if result is None:
                series = _inference_series(
                    f"campaign3_screen:{cell_name}", periods, values, factor
                )
                result = _dispatch_series(
                    series,
                    procedure_ref=procedure_ref,
                    bound_alpha=alpha,
                    registry=registry,
                    admission_records=admission_records,
                    inference_runner=inference_runner,
                    series_record_hash=series_record_hash,
                )
                cache[key] = result
            scenarios.append(
                FloorScenario(
                    cell_name,
                    family,
                    scenario,
                    result.status.value,
                    None if result.statistic is None else float(result.statistic),
                    None
                    if result.p_one_sided is None
                    else float(result.p_one_sided),
                    _floor_significant(result, alpha),
                )
            )
    return tuple(scenarios)


def _break_even(
    tb: TbDiagnostics,
) -> tuple[dict[str, float | None], float | None]:
    diagnostics = tb.diagnostics
    export = _bind_export(diagnostics, tb.series_export)
    by_scenario: dict[str, float | None] = {}
    for scenario, cell_name in _scenario_cells(diagnostics, "B"):
        record = export.get(cell_name)
        if (
            record is None
            or not record.assessed
            or not record.gross_series
            or not record.net_series
        ):
            available = _available_fields(record)
            raise BreakEvenUnavailable(
                "the D12 gross/net series are not available from the T-B export "
                f"for {tb.hypothesis_id!r} ({cell_name!r}); available fields: "
                f"{', '.join(available) if available else '(none)'}"
            )
        gross_values = tuple(float(value) for value in record.gross_series)
        net_values = tuple(float(value) for value in record.net_series)
        by_scenario[str(scenario)] = break_even_cost_bps(
            gross_mean=sum(gross_values) / len(gross_values),
            net_mean=sum(net_values) / len(net_values),
        )
    finite = [value for value in by_scenario.values() if value is not None]
    return by_scenario, (min(finite) if finite else None)


def evaluate_tb(
    *,
    selection: TbSelection,
    sign_by_id: Mapping[str, int],
    control_id: str,
    tb_evaluator: Callable[[str], TbDiagnostics],
    registry: Any,
    admission_records: Sequence[Mapping[str, Any]],
    procedure_ref: Mapping[str, str] | None = None,
    alpha: float = FLOOR_ALPHA,
    inference_runner: Callable[..., InferenceResult] = run_inference,
) -> tuple[TbHypothesisResult, ...]:
    """Drive the injected T-B evaluator for exactly the five + control.

    The TB_SELECTION record must already exist (``selection.written``); any
    hypothesis outside the allowed six is a firewall refusal, and the returned
    diagnostics must name the requested hypothesis.
    """
    if not selection.written:
        raise TbFirewallError(
            "T-B evaluation refused: the TB_SELECTION record has not been written"
        )
    if selection.control_id != control_id:
        raise TbFirewallError("the selection's control does not match the control")
    ref = dict(procedure_ref or ADMITTED_PROCEDURE_REF)
    allowed = _evaluation_ids(selection)
    if len(set(allowed)) != len(allowed):
        raise TbFirewallError("the T-B evaluation ids are not distinct")

    results: list[TbHypothesisResult] = []
    for hypothesis_id in allowed:
        if hypothesis_id not in sign_by_id:
            raise TbFirewallError(
                f"no declared sign is available for T-B hypothesis {hypothesis_id!r}"
            )
        tb = tb_evaluator(hypothesis_id)
        if not isinstance(tb, TbDiagnostics):
            raise TbFirewallError(
                f"the T-B evaluator returned {type(tb).__name__}, not TbDiagnostics"
            )
        if tb.hypothesis_id != hypothesis_id:
            raise TbFirewallError(
                f"the T-B evaluator returned diagnostics for {tb.hypothesis_id!r} "
                f"when {hypothesis_id!r} was requested"
            )
        named = getattr(tb.diagnostics, "certified_hypothesis", None)
        if named is not None and named != hypothesis_id:
            raise TbFirewallError(
                f"the T-B diagnostics name {named!r}, not {hypothesis_id!r}"
            )
        scenarios = _floor_scenarios(
            tb.diagnostics,
            tb.series_export,
            sign_by_id[hypothesis_id],
            procedure_ref=ref,
            alpha=alpha,
            registry=registry,
            admission_records=admission_records,
            inference_runner=inference_runner,
        )
        floor_pass = bool(scenarios) and all(item.passed for item in scenarios)
        l_statistics = [
            item.statistic
            for item in scenarios
            if item.family == "L" and item.statistic is not None
        ]
        min_l_t = float(min(l_statistics)) if l_statistics else None
        by_scenario, break_even = _break_even(tb)
        results.append(
            TbHypothesisResult(
                hypothesis_id=hypothesis_id,
                sign=int(sign_by_id[hypothesis_id]),
                scenarios=scenarios,
                floor_pass=floor_pass,
                min_l_t=min_l_t,
                break_even_by_scenario=by_scenario,
                break_even_cost_bps=break_even,
                is_control=hypothesis_id == control_id,
            )
        )
    return tuple(results)


# ---------------------------------------------------------------------------
# D10 forwarding
# ---------------------------------------------------------------------------


def _forwardable(result: TbHypothesisResult, control_id: str) -> bool:
    if result.hypothesis_id == control_id:
        return False
    return result.floor_pass


def forward_decision(
    results: Sequence[TbHypothesisResult], *, control_id: str
) -> ForwardingDecision:
    """D10: the largest min-scenario L t among floor passers, control excluded."""
    candidates = [
        result
        for result in results
        if _forwardable(result, control_id) and result.min_l_t is not None
    ]
    if not candidates:
        return ForwardingDecision("NO_RESERVE_USE", None, None)
    best = max(
        candidates,
        key=lambda item: (float(item.min_l_t), item.hypothesis_id),
    )
    return ForwardingDecision("FORWARD", best.hypothesis_id, float(best.min_l_t))


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------


def _control_verdict(results: Sequence[TbHypothesisResult], control_id: str) -> str:
    for result in results:
        if result.hypothesis_id != control_id:
            continue
        return (
            "PIPELINE_OK" if result.floor_pass else "PIPELINE_OR_POWER_WARNING"
        )
    return "PIPELINE_OR_POWER_WARNING"


def run_screen(
    *,
    candidates: Sequence[ScreeningCandidate],
    control: ScreeningCandidate,
    registry: Any,
    admission_records: Sequence[Mapping[str, Any]],
    selection_writer: Callable[[TbSelection], Any],
    tb_evaluator: Callable[[str], TbDiagnostics],
    procedure_ref: Mapping[str, str] | None = None,
    k: int = RANK_K,
    alpha: float = FLOOR_ALPHA,
    inference_runner: Callable[..., InferenceResult] = run_inference,
    report_dir: str | Path | None = None,
) -> ScreenReport:
    """Run the C3-S selection tier: rank, write TB_SELECTION, T-B, floor, forward."""
    candidates = tuple(candidates)
    ids = [candidate.hypothesis_id for candidate in candidates]
    if len(set(ids)) != len(ids):
        raise ScreenConfigurationError("candidate hypothesis ids must be distinct")
    if control.hypothesis_id in ids:
        raise ScreenConfigurationError(
            "the positive control must be supplied separately, not among candidates"
        )

    ranking = rank_candidates(
        candidates,
        registry=registry,
        admission_records=admission_records,
        procedure_ref=procedure_ref,
        bound_alpha=alpha,
        k=k,
        inference_runner=inference_runner,
    )
    selection = TbSelection(
        selected_ids=ranking.selected,
        control_id=control.hypothesis_id,
        ranking_table_hash=ranking.content_hash,
    )
    write_tb_selection(selection_writer, selection)

    sign_by_id: dict[str, int] = {
        candidate.hypothesis_id: candidate.sign for candidate in candidates
    }
    sign_by_id[control.hypothesis_id] = control.sign
    tb_results = evaluate_tb(
        selection=selection,
        sign_by_id=sign_by_id,
        control_id=control.hypothesis_id,
        tb_evaluator=tb_evaluator,
        registry=registry,
        admission_records=admission_records,
        procedure_ref=procedure_ref,
        alpha=alpha,
        inference_runner=inference_runner,
    )
    forwarding = forward_decision(tb_results, control_id=control.hypothesis_id)
    report = ScreenReport(
        ranking=ranking,
        selection=selection,
        tb_results=tb_results,
        control_verdict=_control_verdict(tb_results, control.hypothesis_id),
        forwarding=forwarding,
    )
    if report_dir is not None:
        write_report(report, report_dir)
    return report


# ---------------------------------------------------------------------------
# report rendering
# ---------------------------------------------------------------------------


def _fmt(value: float | None, digits: int = 4) -> str:
    if value is None:
        return "-"
    return f"{float(value):.{digits}f}"


def render_markdown(report: ScreenReport) -> str:
    """Render the C3-S report as Markdown (T-B labelled DESCRIPTIVE)."""
    lines: list[str] = []
    lines.append("# Campaign-3 P6 C3-S selection report")
    lines.append("")
    lines.append(f"- T-B results are **{TB_CLAIM_LABEL}**.")
    lines.append("- D12 break-even cost is labelled **ASSUMED** and is not a gate.")
    lines.append("")
    lines.append("## IS ranking (D9)")
    lines.append("")
    lines.append(
        "| rank | hypothesis_id | eligible | min L t | sign | selected |"
    )
    lines.append("|---|---|---|---|---|---|")
    rank = 0
    for row in report.ranking.rows:
        if row.eligible:
            rank += 1
            rank_label = str(rank)
        else:
            rank_label = "-"
        lines.append(
            f"| {rank_label} | {row.hypothesis_id} | {row.eligible} | "
            f"{_fmt(row.min_t)} | {row.sign} | {row.selected} |"
        )
    lines.append("")
    lines.append("## TB_SELECTION")
    lines.append("")
    lines.append(
        f"- selected: {', '.join(report.selection.selected_ids)}"
    )
    lines.append(f"- control: {report.selection.control_id}")
    lines.append(f"- ranking table hash: `{report.selection.ranking_table_hash}`")
    lines.append("")
    lines.append(f"## T-B results ({TB_CLAIM_LABEL})")
    lines.append("")
    lines.append(
        "| hypothesis_id | control | floor_pass | min L t | break-even bps |"
    )
    lines.append("|---|---|---|---|---|")
    for result in report.tb_results:
        lines.append(
            f"| {result.hypothesis_id} | {result.is_control} | "
            f"{result.floor_pass} | {_fmt(result.min_l_t)} | "
            f"{_fmt(result.break_even_cost_bps)} |"
        )
    lines.append("")
    lines.append(f"- control verdict: **{report.control_verdict}**")
    lines.append("")
    lines.append("## Forwarding (D10/D11)")
    lines.append("")
    lines.append(f"- decision: **{report.forwarding.decision}**")
    lines.append(
        f"- hypothesis: {report.forwarding.hypothesis_id or '(none)'}"
    )
    lines.append("")
    lines.append("## P5 section 2 power table (verbatim)")
    lines.append("")
    lines.append(P5_POWER_TABLE_MARKDOWN)
    lines.append("")
    return "\n".join(lines)


def write_report(report: ScreenReport, directory: str | Path) -> tuple[Path, Path]:
    """Write ``campaign3_screen_report.json`` and ``.md`` into ``directory``."""
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "campaign3_screen_report.json"
    md_path = root / "campaign3_screen_report.md"
    json_path.write_text(
        json.dumps(report.to_dict(), indent=2, sort_keys=True, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path
