"""C4M0-4 -- TS terminal-stress decomposition (IS, B estimand, descriptive).

Authority: the frozen ``worker_tasks/campaign4_m0/task-c4m0-4-ts-decomposition.md``
on top of the M0 plan ``worker_tasks/campaign4_m0/campaign4-m0-plan.md`` section
2 (binding constraints).

What this module owns
---------------------

The **orchestration and reporting** of the T0 -> TS gap decomposition only.  It
adds no factor mathematics, no P-Q rule and no scenario semantics: every input
is the frozen D7b/P-Q realization.

* the factor panels come from ``ProductionAssembly.fast_panel_for``;
* the IS evaluation comes from
  :func:`smart_beta.campaign.campaign3_eval.evaluate_ta_diagnostics` (which
  asserts the IS-only firewall first);
* the per-member accounting (:class:`~smart_beta.dataset_v1.delisting_bridge.FormationAccounting`
  and the per-member :class:`~smart_beta.dataset_v1.delisting_bridge.AccountingRecord`
  list) comes from the D7b diagnostics reached through
  ``diagnostics.b_diagnostics`` -- never re-derived here;
* the T0/TS scenario panels come from the frozen
  :func:`smart_beta.dataset_v1.pqv2_series._reconstruct_non_event`.

The six M0-2 factors are taken from
:func:`smart_beta.diagnostics.known_factors.build_known_factors`; the
Campaign-3 TB_SELECTION candidates are rebuilt from their persisted
``<digest>.provenance.json`` FactorSpecs
(:func:`load_persisted_factor_spec`), which must reproduce their recorded
``factor_spec_hash`` or fail closed.

Firewall
--------

IS only.  ``assert_is_fold`` is called **before** any panel is built, so a
non-IS fold raises
:class:`~smart_beta.campaign.campaign3_eval.Campaign3FirewallError` with no
data access.  The module never persists a diagnostics artifact, never appends
to the Knowledge-PIT (diagnostics are not registrations) and never opens a
network path.  Outputs are written only to a caller-supplied directory outside
the repository data tiers.

Decomposition (frozen identity)
-------------------------------

For one formation ``f`` and scenario ``S`` the economic equal-weight leg
returns are the finite-return means of the D7b economic long/short membership
(the exact membership :func:`assign_groups` froze and
:func:`_formation_accounting_from_records` counted), and

``gap_gross(f) = LS_TS(f) - LS_T0(f)`` with ``LS_S = EW_long(S) - EW_short(S)``.

The decomposition is

``pred(f) = -(1/n_long) * sum_{affected long}(1 + r)
            -(1/n_short) * sum_{affected short}(1 + r)``

where ``r`` is each affected member's T0 value and ``n_long``/``n_short`` are
the frozen D7b accounting counts.  ``gap_gross`` and ``pred`` are reported
separately; the frozen reading rule flags ``DECOMPOSITION_BROKEN`` when their
real-data means differ by more than ``1e-9``.  ``net_gap`` is reported for
reference only: it is the difference of this factor's raw pipeline ``net``
series (``net_ts - net_t0``), the series a ``PQv2SeriesRecord`` exposes, with
no re-orientation.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from smart_beta.campaign import campaign3_eval as _eval
from smart_beta.campaign import campaign3_launch as _launch
from smart_beta.dataset_v1 import delisting_bridge as _d7b
from smart_beta.dataset_v1 import pqv2_series as _series
from smart_beta.diagnostics import known_factors as _known
from smart_beta.evaluation.partition import Fold
from smart_beta.spec.factor_spec import FactorSpec
from smart_beta.spec.factor_spec import factor_spec_hash as _factor_spec_hash
from smart_beta.spec.factor_spec import from_dict as _factor_spec_from_dict

__all__ = [
    "SCHEMA",
    "REPORT_JSON_NAME",
    "REPORT_MARKDOWN_NAME",
    "BROKEN_TOLERANCE",
    "STRUCTURAL_RATIO",
    "STRUCTURAL_MIN_FACTORS",
    "TSDecompositionError",
    "TSDecompositionSpecError",
    "FormationTS",
    "ReturnDistribution",
    "UniverseAffectedFraction",
    "FactorTSDiagnostic",
    "TSReading",
    "TSDecompositionReport",
    "load_persisted_factor_spec",
    "build_c3_candidate_factors",
    "evaluate_factor_decomposition",
    "run_ts_decomposition",
    "render_markdown",
    "write_report",
]

#: The canonical output schema tag.
SCHEMA = "campaign4-m0-4-ts-decomposition-v1"

#: The frozen output file names.
REPORT_JSON_NAME = "ts_decomposition.json"
REPORT_MARKDOWN_NAME = "ts_decomposition.md"

#: Frozen reading thresholds (task spec sections 3 and 5).
BROKEN_TOLERANCE = 1e-9
STRUCTURAL_RATIO = 0.25
STRUCTURAL_MIN_FACTORS = 9


class TSDecompositionError(RuntimeError):
    """Base class for every C4M0-4 fail-closed error."""


class TSDecompositionSpecError(TSDecompositionError):
    """A persisted provenance spec is missing, malformed or hash-mismatched."""


# ---------------------------------------------------------------------------
# result value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FormationTS:
    """One formation's T0/TS leg returns and the frozen decomposition."""

    formation: str
    n_long: int
    n_short: int
    n_affected_long: int
    n_affected_short: int
    universe_affected_fraction: float | None
    long_t0: float | None
    short_t0: float | None
    long_ts: float | None
    short_ts: float | None
    ls_t0: float | None
    ls_ts: float | None
    gap_gross: float | None
    pred: float | None
    net_t0: float | None
    net_ts: float | None
    net_gap: float | None

    @property
    def affected_long_fraction(self) -> float | None:
        if self.n_long == 0:
            return None
        return self.n_affected_long / self.n_long

    @property
    def affected_short_fraction(self) -> float | None:
        if self.n_short == 0:
            return None
        return self.n_affected_short / self.n_short

    def to_dict(self) -> dict[str, Any]:
        return {
            "formation": self.formation,
            "n_long": int(self.n_long),
            "n_short": int(self.n_short),
            "n_affected_long": int(self.n_affected_long),
            "n_affected_short": int(self.n_affected_short),
            "universe_affected_fraction": _opt_float(
                self.universe_affected_fraction
            ),
            "affected_long_fraction": _opt_float(self.affected_long_fraction),
            "affected_short_fraction": _opt_float(self.affected_short_fraction),
            "long_t0": _opt_float(self.long_t0),
            "short_t0": _opt_float(self.short_t0),
            "long_ts": _opt_float(self.long_ts),
            "short_ts": _opt_float(self.short_ts),
            "ls_t0": _opt_float(self.ls_t0),
            "ls_ts": _opt_float(self.ls_ts),
            "gap_gross": _opt_float(self.gap_gross),
            "pred": _opt_float(self.pred),
            "net_t0": _opt_float(self.net_t0),
            "net_ts": _opt_float(self.net_ts),
            "net_gap": _opt_float(self.net_gap),
        }


@dataclass(frozen=True)
class ReturnDistribution:
    """The descriptive distribution of affected members' T0 returns."""

    n: int
    q1: float | None
    median: float | None
    q3: float | None
    share_gt_10pct: float | None
    share_lt_minus30pct: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "n": int(self.n),
            "q1": _opt_float(self.q1),
            "median": _opt_float(self.median),
            "q3": _opt_float(self.q3),
            "share_gt_10pct": _opt_float(self.share_gt_10pct),
            "share_lt_minus30pct": _opt_float(self.share_lt_minus30pct),
        }


@dataclass(frozen=True)
class UniverseAffectedFraction:
    """The factor-independent share of universe members that are affected."""

    per_formation: tuple[tuple[str, float | None], ...]
    mean: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mean": _opt_float(self.mean),
            "per_formation": [
                {"formation": formation, "fraction": _opt_float(fraction)}
                for formation, fraction in self.per_formation
            ],
        }


@dataclass(frozen=True)
class FactorTSDiagnostic:
    """One factor's IS TS decomposition (descriptive, no claim)."""

    factor_id: str
    title: str
    source: str
    literature_direction: int
    certified_class: str | None
    not_certified_reason: str | None
    spec_sha256: str
    assessed: bool
    not_assessed_reason: str | None
    n_groups: int
    cost_bps: float
    n_formations: int
    mean_gap_gross: float | None
    mean_pred: float | None
    mean_affected_long_fraction: float | None
    mean_affected_short_fraction: float | None
    mean_universe_affected_fraction: float | None
    excess_exposure: float | None
    r_distribution: ReturnDistribution
    formations: tuple[FormationTS, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "factor_id": self.factor_id,
            "title": self.title,
            "source": self.source,
            "literature_direction": int(self.literature_direction),
            "certified_class": self.certified_class,
            "not_certified_reason": self.not_certified_reason,
            "spec_sha256": self.spec_sha256,
            "assessed": bool(self.assessed),
            "not_assessed_reason": self.not_assessed_reason,
            "n_groups": int(self.n_groups),
            "cost_bps": float(self.cost_bps),
            "n_formations": int(self.n_formations),
            "mean_gap_gross": _opt_float(self.mean_gap_gross),
            "mean_pred": _opt_float(self.mean_pred),
            "mean_affected_long_fraction": _opt_float(
                self.mean_affected_long_fraction
            ),
            "mean_affected_short_fraction": _opt_float(
                self.mean_affected_short_fraction
            ),
            "mean_universe_affected_fraction": _opt_float(
                self.mean_universe_affected_fraction
            ),
            "excess_exposure": _opt_float(self.excess_exposure),
            "r_distribution": self.r_distribution.to_dict(),
            "formations": [item.to_dict() for item in self.formations],
        }


@dataclass(frozen=True)
class TSReading:
    """The frozen section-5 reading applied to the report."""

    label: str
    decomposition_broken_factors: tuple[str, ...]
    n_factor_specific_structural: int
    n_assessed_factors: int
    universe_mean: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "decomposition_broken_factors": list(
                self.decomposition_broken_factors
            ),
            "n_factor_specific_structural": int(
                self.n_factor_specific_structural
            ),
            "n_assessed_factors": int(self.n_assessed_factors),
            "universe_mean": _opt_float(self.universe_mean),
        }


@dataclass(frozen=True)
class TSDecompositionReport:
    """The frozen C4M0-4 machine-readable report."""

    fold_role: str
    fold_start: str
    fold_end: str
    universe: str
    n_groups: int
    cost_bps: float
    universe_affected_fraction: UniverseAffectedFraction
    factors: tuple[FactorTSDiagnostic, ...]

    def to_dict(self) -> dict[str, Any]:
        body = {
            "schema": SCHEMA,
            "fold": {
                "role": self.fold_role,
                "start": self.fold_start,
                "end": self.fold_end,
            },
            "universe": self.universe,
            "n_groups": int(self.n_groups),
            "cost_bps": float(self.cost_bps),
            "universe_affected_fraction": (
                self.universe_affected_fraction.to_dict()
            ),
            "reading": classify_reading(self).to_dict(),
            "factors": [item.to_dict() for item in self.factors],
        }
        body["content_sha256"] = _content_hash(body)
        return body

    @property
    def content_sha256(self) -> str:
        return _content_hash(self._body())

    def _body(self) -> dict[str, Any]:
        payload = self.to_dict()
        payload.pop("content_sha256", None)
        return payload


# ---------------------------------------------------------------------------
# serialization helpers
# ---------------------------------------------------------------------------


def _opt_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _canonical_json(obj: Any) -> str:
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _content_hash(obj: Any) -> str:
    return hashlib.sha256(_canonical_json(obj).encode("utf-8")).hexdigest()


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return pd.Timestamp(value).date()


def _mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return float(math.fsum(values) / len(values))


# ---------------------------------------------------------------------------
# persisted C3 provenance (FactorSpec reload, fail closed)
# ---------------------------------------------------------------------------


def load_persisted_factor_spec(
    path: Path | str,
    *,
    expected_diagnostics_hash: str | None = None,
) -> FactorSpec:
    """Load one ``<digest>.provenance.json`` and verify its ``factor_spec_hash``.

    Fail closed on a missing/malformed record, a schema mismatch, a
    ``diagnostics_hash`` mismatch (when an expected digest is given), or a
    ``factor_spec`` that does not reproduce the recorded ``factor_spec_hash``.
    This is the C3-candidate reload the task spec section 2 requires.
    """

    provenance_path = Path(path)
    try:
        payload = json.loads(provenance_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TSDecompositionSpecError(
            f"no readable persisted T-A provenance at {provenance_path}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise TSDecompositionSpecError(
            f"persisted provenance {provenance_path.name!r} is not an object"
        )
    if payload.get("schema") != _launch.TA_PROVENANCE_SCHEMA:
        raise TSDecompositionSpecError(
            f"persisted provenance {provenance_path.name!r} has schema "
            f"{payload.get('schema')!r}, not {_launch.TA_PROVENANCE_SCHEMA!r}"
        )
    digest = str(payload.get("diagnostics_hash", ""))
    if expected_diagnostics_hash is not None and digest != str(
        expected_diagnostics_hash
    ):
        raise TSDecompositionSpecError(
            f"persisted provenance {provenance_path.name!r} names "
            f"diagnostics_hash {digest!r}, not {expected_diagnostics_hash!r}"
        )
    try:
        spec = _factor_spec_from_dict(payload.get("factor_spec"))
    except Exception as exc:  # noqa: BLE001 - re-typed, fail closed
        raise TSDecompositionSpecError(
            "persisted provenance "
            f"{provenance_path.name!r} is not a valid FactorSpec "
            f"({type(exc).__name__})"
        ) from exc
    declared = str(payload.get("factor_spec_hash", ""))
    if _factor_spec_hash(spec) != declared:
        raise TSDecompositionSpecError(
            f"persisted provenance {provenance_path.name!r} declares "
            f"factor_spec_hash {declared!r}, which does not match its "
            "FactorSpec"
        )
    return spec


def build_c3_candidate_factors(
    provenance_paths: Iterable[Path | str],
) -> tuple[_known.KnownFactor, ...]:
    """Build the C3 TB_SELECTION candidates from persisted provenance files.

    Each entry becomes ``C3-IS-<rank>`` in the supplied order (the planner
    supplies IS ranks 1..5).  Every spec is classified by the frozen
    ``certify_candidate``; a NOT_CERTIFIED candidate is returned untouched.
    """

    factors: list[_known.KnownFactor] = []
    for rank, path in enumerate(provenance_paths, start=1):
        spec = load_persisted_factor_spec(path)
        classification = _eval.certify_candidate(spec)
        certified = _eval.is_certified(classification)
        factors.append(
            _known.KnownFactor(
                factor_id=f"C3-IS-{rank}",
                title=f"Campaign-3 TB_SELECTION IS rank {rank}",
                source=f"persisted provenance {Path(path).name}",
                spec=spec,
                literature_direction=1,
                certified_class=(
                    str(classification) if certified else None
                ),
                not_certified_reason=(
                    None if certified else str(classification)
                ),
            )
        )
    return tuple(factors)


# ---------------------------------------------------------------------------
# evaluation
# ---------------------------------------------------------------------------


def _export_bindings(assembly: Any) -> Any:
    declared = getattr(assembly, "export_bindings", None)
    if declared is not None:
        return declared
    evaluator = getattr(assembly, "ta_evaluator", None)
    bound = getattr(evaluator, "bindings", None)
    return bound if bound is not None else _d7b.DEFAULT_BINDINGS


def _raw_diagnostics(
    assembly: Any,
    factor: _known.KnownFactor,
    selected_fold: Fold,
) -> tuple[Any, Any, Any]:
    """Run the frozen T-A evaluation.

    Returns ``(panel, declaration, diagnostics)`` so the caller reuses the
    exact panel the evaluation consumed.
    """

    spec = factor.spec
    panel = assembly.fast_panel_for(spec, selected_fold)
    declaration = assembly.declaration_builder(spec, panel, selected_fold)
    evaluator = getattr(assembly, "ta_evaluator", None)
    roles = _eval.event_roles(spec)
    event_layer = getattr(assembly, "event_layer", None)
    if roles and event_layer is None:
        raise TSDecompositionError(
            f"{factor.factor_id} is an event candidate but the assembly binds "
            "no event layer"
        )
    diagnostics = _eval.evaluate_ta_diagnostics(
        assembly.dataset_root,
        declaration,
        spec,
        panel,
        assembly.partition,
        fold=selected_fold,
        overlay=assembly.overlay,
        inputs=assembly.inputs,
        bindings=getattr(evaluator, "bindings", None),
        source_bindings=getattr(evaluator, "source_bindings", None),
        pq_bindings=getattr(evaluator, "pq_bindings", None),
        pq_read_records_fn=getattr(evaluator, "pq_read_records_fn", None),
        sessions=getattr(evaluator, "sessions", None),
        event_layer=event_layer if roles else None,
    )
    return panel, declaration, diagnostics


def _panel_lookup(panel: pd.DataFrame) -> dict[tuple[date, str], float | None]:
    lookup: dict[tuple[date, str], float | None] = {}
    for row in panel.itertuples(index=False):
        value = getattr(row, _d7b.fr.FORWARD_RETURN_COL)
        parsed = _opt_float(value)
        lookup[(_as_date(row.date), str(row.stock_id))] = (
            None if parsed is None else parsed
        )
    return lookup


def _series_lookup(series: pd.Series) -> dict[date, float | None]:
    lookup: dict[date, float | None] = {}
    for key, value in series.items():
        parsed = _opt_float(value)
        lookup[_as_date(key)] = None if parsed is None else parsed
    return lookup


def _leg_values(
    lookup: Mapping[tuple[date, str], float | None],
    formation: date,
    stock_ids: Sequence[str],
) -> list[float]:
    values: list[float] = []
    for stock_id in stock_ids:
        value = lookup.get((formation, stock_id))
        if value is not None:
            values.append(float(value))
    return values


def _formation_decomposition(
    formation: date,
    records: Sequence[Any],
    accounting: Any,
    t0_lookup: Mapping[tuple[date, str], float | None],
    ts_lookup: Mapping[tuple[date, str], float | None],
    net_t0: float | None,
    net_ts: float | None,
) -> FormationTS:
    long_ids = [
        str(row.stock_id)
        for row in records
        if row.economic_leg == _d7b.ECONOMIC_LONG
    ]
    short_ids = [
        str(row.stock_id)
        for row in records
        if row.economic_leg == _d7b.ECONOMIC_SHORT
    ]
    affected_long = [
        str(row.stock_id)
        for row in records
        if row.economic_leg == _d7b.ECONOMIC_LONG and row.terminal_affected
    ]
    affected_short = [
        str(row.stock_id)
        for row in records
        if row.economic_leg == _d7b.ECONOMIC_SHORT and row.terminal_affected
    ]

    long_t0 = _mean(_leg_values(t0_lookup, formation, long_ids))
    short_t0 = _mean(_leg_values(t0_lookup, formation, short_ids))
    long_ts = _mean(_leg_values(ts_lookup, formation, long_ids))
    short_ts = _mean(_leg_values(ts_lookup, formation, short_ids))
    ls_t0 = None if long_t0 is None or short_t0 is None else long_t0 - short_t0
    ls_ts = None if long_ts is None or short_ts is None else long_ts - short_ts
    gap_gross = None if ls_t0 is None or ls_ts is None else ls_ts - ls_t0

    n_long = int(accounting.n_long)
    n_short = int(accounting.n_short)
    universe_fraction: float | None = None
    if accounting.n_universe:
        universe_fraction = accounting.n_affected / accounting.n_universe
    pred: float | None = None
    if n_long > 0 and n_short > 0:
        affected_long_returns = _leg_values(t0_lookup, formation, affected_long)
        affected_short_returns = _leg_values(t0_lookup, formation, affected_short)
        pred = -(
            math.fsum(1.0 + value for value in affected_long_returns) / n_long
        ) - (
            math.fsum(1.0 + value for value in affected_short_returns) / n_short
        )

    net_gap = (
        None if net_t0 is None or net_ts is None else net_ts - net_t0
    )
    return FormationTS(
        formation=_as_date(formation).isoformat(),
        n_long=n_long,
        n_short=n_short,
        n_affected_long=len(affected_long),
        n_affected_short=len(affected_short),
        universe_affected_fraction=universe_fraction,
        long_t0=long_t0,
        short_t0=short_t0,
        long_ts=long_ts,
        short_ts=short_ts,
        ls_t0=ls_t0,
        ls_ts=ls_ts,
        gap_gross=gap_gross,
        pred=pred,
        net_t0=net_t0,
        net_ts=net_ts,
        net_gap=net_gap,
    )


def _return_distribution(returns: Sequence[float]) -> ReturnDistribution:
    values = np.asarray(list(returns), dtype="float64")
    if values.size == 0:
        return ReturnDistribution(
            n=0,
            q1=None,
            median=None,
            q3=None,
            share_gt_10pct=None,
            share_lt_minus30pct=None,
        )
    q1, median, q3 = np.percentile(values, [25.0, 50.0, 75.0])
    return ReturnDistribution(
        n=int(values.size),
        q1=float(q1),
        median=float(median),
        q3=float(q3),
        share_gt_10pct=float(np.mean(values > 0.10)),
        share_lt_minus30pct=float(np.mean(values < -0.30)),
    )


def _unassessed(
    factor: _known.KnownFactor, reason: str | None
) -> FactorTSDiagnostic:
    return FactorTSDiagnostic(
        factor_id=factor.factor_id,
        title=factor.title,
        source=factor.source,
        literature_direction=int(factor.literature_direction),
        certified_class=factor.certified_class,
        not_certified_reason=factor.not_certified_reason,
        spec_sha256=factor.spec_sha256,
        assessed=False,
        not_assessed_reason=reason,
        n_groups=0,
        cost_bps=0.0,
        n_formations=0,
        mean_gap_gross=None,
        mean_pred=None,
        mean_affected_long_fraction=None,
        mean_affected_short_fraction=None,
        mean_universe_affected_fraction=None,
        excess_exposure=None,
        r_distribution=_return_distribution(()),
        formations=(),
    )


def evaluate_factor_decomposition(
    assembly: Any,
    factor: _known.KnownFactor,
    *,
    fold: Fold | None = None,
) -> FactorTSDiagnostic:
    """Decompose one factor's T0 -> TS gap on the IS fold.

    The IS firewall is asserted before any panel is built.  A NOT_CERTIFIED
    factor is reported untouched (unassessed).
    """

    selected_fold = fold if fold is not None else assembly.is_fold
    _eval.assert_is_fold(selected_fold)

    if factor.certified_class is None:
        return _unassessed(factor, factor.not_certified_reason)

    panel, declaration, diagnostics = _raw_diagnostics(
        assembly, factor, selected_fold
    )
    if diagnostics.not_assessed is not None:
        return _unassessed(factor, str(diagnostics.not_assessed))

    raw = diagnostics.b_diagnostics
    accounting_by_formation = {
        _as_date(item.formation_date): item
        for item in raw.formation_accounting
    }
    t0_records: dict[date, list[Any]] = {}
    for record in raw.accounting_records:
        if record.scenario != _d7b.SCENARIO_T0:
            continue
        t0_records.setdefault(_as_date(record.formation_date), []).append(record)

    scenarios, formations = _series._reconstruct_non_event(
        assembly.dataset_root,
        declaration,
        factor.spec,
        panel,
        assembly.partition,
        _export_bindings(assembly),
    )
    t0_lookup = _panel_lookup(scenarios[0].panel)
    ts_lookup = _panel_lookup(scenarios[1].panel)
    net_t0 = _series_lookup(scenarios[0].net)
    net_ts = _series_lookup(scenarios[1].net)

    formation_items: list[FormationTS] = []
    universe_fractions: list[float] = []
    pool: list[float] = []
    for formation in formations:
        key = _as_date(formation)
        accounting = accounting_by_formation.get(key)
        if accounting is None:
            continue
        records = t0_records.get(key, [])
        item = _formation_decomposition(
            key,
            records,
            accounting,
            t0_lookup,
            ts_lookup,
            net_t0.get(key),
            net_ts.get(key),
        )
        formation_items.append(item)
        if item.universe_affected_fraction is not None:
            universe_fractions.append(float(item.universe_affected_fraction))
        for record in records:
            if not record.terminal_affected:
                continue
            if record.economic_leg not in (
                _d7b.ECONOMIC_LONG,
                _d7b.ECONOMIC_SHORT,
            ):
                continue
            value = t0_lookup.get((key, str(record.stock_id)))
            if value is not None:
                pool.append(float(value))

    gaps = [item.gap_gross for item in formation_items if item.gap_gross is not None]
    preds = [item.pred for item in formation_items if item.pred is not None]
    long_fractions = [
        item.affected_long_fraction
        for item in formation_items
        if item.affected_long_fraction is not None
    ]
    short_fractions = [
        item.affected_short_fraction
        for item in formation_items
        if item.affected_short_fraction is not None
    ]
    mean_universe = _mean(universe_fractions)
    mean_gap = _mean(gaps)
    mean_pred = _mean(preds)
    mean_long_fraction = _mean(long_fractions)
    mean_short_fraction = _mean(short_fractions)
    summed_fractions = [
        item.affected_long_fraction + item.affected_short_fraction
        for item in formation_items
        if item.affected_long_fraction is not None
        and item.affected_short_fraction is not None
    ]
    mean_summed_fraction = _mean(summed_fractions)
    excess: float | None = None
    if mean_summed_fraction is not None and mean_universe is not None:
        excess = mean_summed_fraction - 2.0 * mean_universe

    return FactorTSDiagnostic(
        factor_id=factor.factor_id,
        title=factor.title,
        source=factor.source,
        literature_direction=int(factor.literature_direction),
        certified_class=factor.certified_class,
        not_certified_reason=None,
        spec_sha256=factor.spec_sha256,
        assessed=True,
        not_assessed_reason=None,
        n_groups=int(declaration.base.n_groups),
        cost_bps=float(declaration.base.transaction_cost_bps),
        n_formations=len(formation_items),
        mean_gap_gross=mean_gap,
        mean_pred=mean_pred,
        mean_affected_long_fraction=mean_long_fraction,
        mean_affected_short_fraction=mean_short_fraction,
        mean_universe_affected_fraction=mean_universe,
        excess_exposure=excess,
        r_distribution=_return_distribution(pool),
        formations=tuple(formation_items),
    )


# ---------------------------------------------------------------------------
# reading / report
# ---------------------------------------------------------------------------


def classify_reading(report: TSDecompositionReport) -> TSReading:
    """Apply the frozen section-5 reading rules to a report."""

    broken: list[str] = []
    for factor in report.factors:
        if not factor.assessed:
            continue
        if factor.mean_gap_gross is None or factor.mean_pred is None:
            continue
        if abs(factor.mean_gap_gross - factor.mean_pred) > BROKEN_TOLERANCE:
            broken.append(factor.factor_id)

    universe_mean = report.universe_affected_fraction.mean
    assessed = [factor for factor in report.factors if factor.assessed]
    n_structural = 0
    if universe_mean is not None:
        threshold = STRUCTURAL_RATIO * 2.0 * universe_mean
        for factor in assessed:
            if factor.excess_exposure is None:
                continue
            if abs(factor.excess_exposure) <= threshold:
                n_structural += 1

    if broken:
        label = "DECOMPOSITION_BROKEN"
    elif n_structural >= STRUCTURAL_MIN_FACTORS:
        label = "TS_STRUCTURAL"
    else:
        label = "TS_FACTOR_SPECIFIC"
    return TSReading(
        label=label,
        decomposition_broken_factors=tuple(broken),
        n_factor_specific_structural=n_structural,
        n_assessed_factors=len(assessed),
        universe_mean=universe_mean,
    )


def _empty_universe_fraction() -> UniverseAffectedFraction:
    return UniverseAffectedFraction(per_formation=(), mean=None)


def _first_int(
    diagnostics: Sequence[FactorTSDiagnostic], attribute: str, fallback: int
) -> int:
    for item in diagnostics:
        if getattr(item, attribute, 0):
            return int(getattr(item, attribute))
    return int(fallback)


def _first_float(
    diagnostics: Sequence[FactorTSDiagnostic], attribute: str, fallback: float
) -> float:
    for item in diagnostics:
        if getattr(item, attribute, 0.0):
            return float(getattr(item, attribute))
    return float(fallback)


def run_ts_decomposition(
    assembly: Any,
    *,
    output_dir: Path | str | None = None,
    fold: Fold | None = None,
    factors: Sequence[_known.KnownFactor] | None = None,
) -> TSDecompositionReport:
    """Run the C4M0-4 decomposition and (optionally) write the two reports.

    ``factors`` defaults to the six M0-2 known factors; the script appends the
    C3 candidates rebuilt from persisted provenance.
    """

    selected_fold = fold if fold is not None else assembly.is_fold
    # The IS firewall is asserted before building any panel (section 2).
    _eval.assert_is_fold(selected_fold)
    chosen = tuple(
        _known.build_known_factors() if factors is None else factors
    )
    diagnostics = tuple(
        evaluate_factor_decomposition(assembly, factor, fold=selected_fold)
        for factor in chosen
    )

    universe_fraction = _empty_universe_fraction()
    for item in diagnostics:
        if item.assessed and item.formations:
            universe_fraction = UniverseAffectedFraction(
                per_formation=tuple(
                    (formation.formation, formation.universe_affected_fraction)
                    for formation in item.formations
                ),
                mean=item.mean_universe_affected_fraction,
            )
            break

    report = TSDecompositionReport(
        fold_role=str(selected_fold.role.value),
        fold_start=_date_text(selected_fold.start),
        fold_end=_date_text(selected_fold.end),
        universe=str(_eval.TA_UNIVERSE),
        n_groups=int(
            _first_int(diagnostics, "n_groups", int(_eval.TA_N_GROUPS))
        ),
        cost_bps=float(
            _first_float(diagnostics, "cost_bps", float(_eval.TA_COST_BPS))
        ),
        universe_affected_fraction=universe_fraction,
        factors=diagnostics,
    )
    if output_dir is not None:
        write_report(report, output_dir)
    return report


def _date_text(value: Any) -> str:
    if hasattr(value, "date"):
        return value.date().isoformat()
    return str(value)


def write_report(
    report: TSDecompositionReport,
    output_dir: Path | str,
) -> tuple[Path, Path]:
    """Write the canonical JSON and the rendered Markdown into ``output_dir``."""

    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / REPORT_JSON_NAME
    markdown_path = directory / REPORT_MARKDOWN_NAME
    json_path.write_text(
        _canonical_json(report.to_dict()) + "\n", encoding="utf-8"
    )
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, markdown_path


def _fmt(value: float | None, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def render_markdown(report: TSDecompositionReport) -> str:
    """Render the frozen C4M0-4 report as Markdown."""

    reading = classify_reading(report)
    lines: list[str] = [
        "# Campaign-4 M0-4 -- TS terminal-stress decomposition (IS only)",
        "",
        f"- schema: `{SCHEMA}`",
        (
            f"- fold: `{report.fold_role}` "
            f"[{report.fold_start}, {report.fold_end})"
        ),
        f"- universe: `{report.universe}`, groups: {report.n_groups}, "
        f"cost: {report.cost_bps:g} bps",
        f"- content SHA-256: `{report.content_sha256}`",
        f"- reading: **{reading.label}**",
        (
            "- universe-level affected fraction (mean): "
            f"{_fmt(report.universe_affected_fraction.mean)}"
        ),
        "",
        "## Per-factor summary",
        "",
        "| factor | class | n | mean gap | mean pred | a_long | a_short | "
        "a_universe | excess | broken |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for factor in report.factors:
        lines.append(
            "| {fid} | {cls} | {n} | {gap} | {pred} | {al} | {ash} | "
            "{au} | {e} | {broken} |".format(
                fid=factor.factor_id,
                cls=factor.certified_class or "NOT_CERTIFIED",
                n=factor.n_formations,
                gap=_fmt(factor.mean_gap_gross),
                pred=_fmt(factor.mean_pred),
                al=_fmt(factor.mean_affected_long_fraction),
                ash=_fmt(factor.mean_affected_short_fraction),
                au=_fmt(factor.mean_universe_affected_fraction),
                e=_fmt(factor.excess_exposure),
                broken=(
                    "BROKEN"
                    if factor.factor_id in reading.decomposition_broken_factors
                    else ""
                ),
            )
        )
    lines.extend(
        [
            "",
            "## Affected-member T0 return distribution (both legs pooled)",
            "",
            "| factor | n | q1 | median | q3 | share r>+10% | share r<-30% |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for factor in report.factors:
        dist = factor.r_distribution
        lines.append(
            f"| {factor.factor_id} | {dist.n} | {_fmt(dist.q1)} | "
            f"{_fmt(dist.median)} | {_fmt(dist.q3)} | "
            f"{_fmt(dist.share_gt_10pct)} | {_fmt(dist.share_lt_minus30pct)} |"
        )
    lines.append("")
    return "\n".join(lines)
