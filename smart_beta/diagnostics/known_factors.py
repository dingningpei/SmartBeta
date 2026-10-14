"""C4M0-2 -- known-factor replication on the IS fold (descriptive, no claims).

Authority: the frozen ``worker_tasks/campaign4_m0/campaign4-m0-plan.md``
section 3 (task C4M0-2), on top of the M0 programme draft
``docs/campaign4_method_program_draft.md`` section 1 and decision K2.

What this module owns
---------------------

The diagnostic **orchestration and reporting** only.  It composes the frozen,
already-sealed components and adds no factor mathematics and no P-Q rules:

* the factor panels come from ``ProductionAssembly.fast_panel_for`` (the
  Campaign-1 production panel realization);
* the IS evaluation comes from
  :func:`smart_beta.campaign.campaign3_eval.evaluate_ta_diagnostics` (the
  fail-closed T-A bridge, which enforces the IS-only firewall);
* the per-period B/L/gross/net series come from the frozen
  :func:`smart_beta.dataset_v1.pqv2_series.export_series`;
* the HAC t comes from the frozen Phase-7 estimator
  :func:`smart_beta.evaluation.metrics.long_short_mean_tstat` and the
  annualized Sharpe from :func:`smart_beta.evaluation.metrics.sharpe_ratio`;
* the ``EWC_T23_ONE_SIDED`` t comes from the admitted Phase-10 procedure
  implementation (called directly, for a *descriptive* statistic only -- the
  diagnostic makes no admission-gated claim);
* the group profile uses the frozen portfolio-sort engine
  (:func:`smart_beta.engines.portfolio_sort.sort_portfolios`), the exact
  grouping the D7b pipeline uses (``_frozen_groups`` calls ``assign_groups``).

Firewall
--------

The IS fold is the only fold this module evaluates.  ``assert_is_fold`` is
called **before** any panel is built, so a non-IS fold raises
:class:`~smart_beta.campaign.campaign3_eval.Campaign3FirewallError` with no
data access.  The module never persists a diagnostics artifact, never appends
to the Knowledge-PIT (diagnostics are not registrations) and never opens a
network path.  Outputs are written only to a caller-supplied directory outside
the repository data tiers.

Spec sources (frozen literals)
------------------------------

KF-MOM12 / KF-MOM6 / KF-REV1 / KF-VSMOM come from
``docs/campaign1_factorspecs.json`` (specs 0 / 1 / 2 / 5, hash-verified);
KF-LVOL is the new ``-1 * rolling_std(ret, 252)`` spec defined here; KF-EAR is
the Campaign-3 positive control
(:func:`smart_beta.campaign.campaign3_launch.control_factor_spec`).  Every spec
is classified by :func:`~smart_beta.campaign.campaign3_eval.certify_candidate`
before evaluation; a ``NOT_CERTIFIED`` spec is reported untouched, never
altered.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import scipy.stats
from smart_beta.campaign import campaign3_eval as _eval
from smart_beta.campaign import campaign3_launch as _launch
from smart_beta.dataset_v1 import delisting_bridge as _d7b
from smart_beta.dataset_v1 import pqv2_envelope as _envelope
from smart_beta.dataset_v1 import pqv2_series as _series
from smart_beta.engines import portfolio_sort as _sort
from smart_beta.evaluation import metrics as _metrics
from smart_beta.evaluation.partition import Fold
from smart_beta.evaluation.spec import Series
from smart_beta.science.contracts import Direction
from smart_beta.science.inference import ProcedureFailure
from smart_beta.science.procedures.ewc_t23_one_sided import EWCT23OneSided
from smart_beta.spec.expression import Literal, Mul, field, rolling_std
from smart_beta.spec.factor_spec import FactorInput, FactorSpec, factor_spec_hash
from smart_beta.spec.requirements import (
    DataRequirement,
    Frequency,
    ObservationPeriod,
)

__all__ = [
    "SCHEMA",
    "REPORT_JSON_NAME",
    "REPORT_MARKDOWN_NAME",
    "CAMPAIGN1_FACTORSPECS_PATH",
    "DEFAULT_BOUND_ALPHA",
    "KnownFactorError",
    "KnownFactorSpecError",
    "KnownFactor",
    "CellCoverage",
    "CellStats",
    "GroupProfile",
    "TurnoverProfile",
    "FactorDiagnostic",
    "KnownFactorsReport",
    "build_kf_lvol_spec",
    "build_known_factors",
    "evaluate_known_factor",
    "run_known_factors",
    "render_markdown",
    "write_report",
]

#: The canonical output schema tag.
SCHEMA = "campaign4-m0-2-known-factors-v1"

#: The frozen output file names (plan section 3.2).
REPORT_JSON_NAME = "known_factors.json"
REPORT_MARKDOWN_NAME = "known_factors.md"

#: The frozen Campaign-1 spec register (plan section 3.1).
CAMPAIGN1_FACTORSPECS_PATH = (
    Path(__file__).resolve().parents[2] / "docs" / "campaign1_factorspecs.json"
)

#: The EWC one-sided bound / p-value alpha.  It does not change the EWC ``t``;
#: it is carried for the one-sided p-value and the closed parameter range.
DEFAULT_BOUND_ALPHA = 0.05


class KnownFactorError(RuntimeError):
    """Base class for every C4M0-2 fail-closed error."""


class KnownFactorSpecError(KnownFactorError):
    """A frozen factor spec is missing, malformed or not as declared."""


@dataclass(frozen=True)
class KnownFactor:
    """One known factor and its frozen, certified P-Q v2.3 spec.

    ``literature_direction`` is ``+1`` when the literature predicts a positive
    mean for the spec's *oriented* ``B-T0`` long-short series (all six M0-2
    factors).  ``certified_class`` is the
    :func:`~smart_beta.campaign.campaign3_eval.certify_candidate` class, or
    ``None`` with ``not_certified_reason`` set when the spec is not admitted.
    """

    factor_id: str
    title: str
    source: str
    spec: FactorSpec
    literature_direction: int
    certified_class: str | None
    not_certified_reason: str | None

    def __post_init__(self) -> None:
        if not self.factor_id or not self.title or not self.source:
            raise KnownFactorSpecError(
                "factor_id, title and source must be non-empty text"
            )
        if not isinstance(self.spec, FactorSpec):
            raise KnownFactorSpecError("spec must be a FactorSpec")
        if self.literature_direction not in (1, -1):
            raise KnownFactorSpecError("literature_direction must be +1 or -1")
        if self.certified_class is None and not self.not_certified_reason:
            raise KnownFactorSpecError(
                "a NOT_CERTIFIED factor must carry a reason"
            )
        if self.certified_class is not None and self.not_certified_reason:
            raise KnownFactorSpecError(
                "a CERTIFIED factor must not carry a not-certified reason"
            )

    @property
    def spec_sha256(self) -> str:
        return factor_spec_hash(self.spec)


# ---------------------------------------------------------------------------
# spec construction (frozen literals)
# ---------------------------------------------------------------------------


def build_kf_lvol_spec() -> FactorSpec:
    """The new frozen KF-LVOL spec: ``-1 * rolling_std(ret, 252)``.

    The SIMPLE_G grammar admits ``rolling_std`` over the D7c
    ``daily_total_return`` input; the ``-1`` factor (inside the expression,
    ``sign = +1``, the Campaign-1 convention) makes a high oriented value the
    low-volatility side, so the literature direction is ``+``.
    """

    ret = FactorInput(
        alias="ret",
        requirement=DataRequirement(
            semantic_id="daily_total_return",
            frequency=Frequency.DAILY,
            observation_period=ObservationPeriod.PERIOD,
        ),
    )
    return FactorSpec(
        id="c4_kf_lvol_252",
        description=(
            "Low-volatility effect: minus the 252-session rolling standard "
            "deviation of daily total returns"
        ),
        expression=Mul(Literal(-1.0), rolling_std(field("ret"), 252)),
        inputs=(ret,),
        frequency="daily",
        missing_policy="propagate",
        sign=1,
    )


def _load_campaign1_specs(path: Path | str) -> tuple[dict[str, Any], ...]:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if document.get("schema") != "campaign1-factorspecs-v1":
        raise KnownFactorSpecError(
            f"unexpected campaign1 spec schema {document.get('schema')!r}"
        )
    specs = document.get("specs")
    if not isinstance(specs, list):
        raise KnownFactorSpecError("campaign1 spec register has no 'specs' list")
    return tuple(specs)


def _campaign1_factor(
    entries: Sequence[Mapping[str, Any]],
    index: int,
    factor_id: str,
    title: str,
) -> KnownFactor:
    entry = entries[index]
    spec = FactorSpec.from_dict(entry["factor_spec_canonical_json"])
    declared_hash = str(entry["factor_spec_canonical_sha256"])
    if factor_spec_hash(spec) != declared_hash:
        raise KnownFactorSpecError(
            f"{factor_id} canonical hash {factor_spec_hash(spec)!r} != the "
            f"campaign1 register's {declared_hash!r}"
        )
    classification = _eval.certify_candidate(spec)
    certified = _eval.is_certified(classification)
    return KnownFactor(
        factor_id=factor_id,
        title=title,
        source=(
            "docs/campaign1_factorspecs.json#"
            f"{index}:{entry['hypothesis_label']}"
        ),
        spec=spec,
        literature_direction=1,
        certified_class=str(classification) if certified else None,
        not_certified_reason=None if certified else str(classification),
    )


def build_known_factors(
    campaign1_specs_path: Path | str | None = None,
) -> tuple[KnownFactor, ...]:
    """Build the six frozen M0-2 factors, each classify-checked by P-Q v2.3.

    The Campaign-1 specs are re-read from the frozen register and must match
    their declared canonical hashes (a tampered register fails closed).  The
    returned tuple is in the frozen plan order.
    """

    path = (
        CAMPAIGN1_FACTORSPECS_PATH
        if campaign1_specs_path is None
        else campaign1_specs_path
    )
    entries = _load_campaign1_specs(path)
    factors: list[KnownFactor] = [
        _campaign1_factor(entries, 0, "KF-MOM12", "12-1 momentum (C1-H1)"),
        _campaign1_factor(entries, 1, "KF-MOM6", "6-1 momentum (C1-H2)"),
        _campaign1_factor(entries, 2, "KF-REV1", "1-month reversal (C1-H3)"),
        _campaign1_factor(entries, 5, "KF-VSMOM", "volatility-scaled momentum (C1-H6)"),
    ]

    lvol_spec = build_kf_lvol_spec()
    lvol_class = _eval.certify_candidate(lvol_spec)
    lvol_certified = _eval.is_certified(lvol_class)
    factors.append(
        KnownFactor(
            factor_id="KF-LVOL",
            title="low-volatility effect (-1 * rolling_std(ret, 252))",
            source="worker_tasks/campaign4_m0/campaign4-m0-plan.md#3.1",
            spec=lvol_spec,
            literature_direction=1,
            certified_class=str(lvol_class) if lvol_certified else None,
            not_certified_reason=None if lvol_certified else str(lvol_class),
        )
    )

    ear_spec = _launch.control_factor_spec()
    ear_class = _eval.certify_candidate(ear_spec)
    ear_certified = _eval.is_certified(ear_class)
    factors.append(
        KnownFactor(
            factor_id="KF-EAR",
            title="Campaign-3 positive control (event_ear_k_kp1, 1-month hold)",
            source="smart_beta.campaign.campaign3_launch.control_factor_spec()",
            spec=ear_spec,
            literature_direction=1,
            certified_class=str(ear_class) if ear_certified else None,
            not_certified_reason=None if ear_certified else str(ear_class),
        )
    )
    return tuple(factors)


# ---------------------------------------------------------------------------
# result value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CellCoverage:
    """The assessed / NOT_ASSESSED period counts and causes of one cell."""

    n_periods: int
    n_assessed: int
    n_not_assessed: int
    causes: tuple[tuple[str, int], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_periods": int(self.n_periods),
            "n_assessed": int(self.n_assessed),
            "n_not_assessed": int(self.n_not_assessed),
            "causes": [{"reason": reason, "n": int(n)} for reason, n in self.causes],
        }


@dataclass(frozen=True)
class CellStats:
    """The descriptive long-short statistics of one B/L x scenario cell."""

    cell: str
    scenario: str
    event_scenario: str | None
    estimand: str
    status: str | None
    reason: str | None
    n_periods: int
    net_mean: float | None
    hac_t: float | None
    annualized_sr: float | None
    periods_per_year: int
    ewc_t: float | None
    ewc_p_one_sided: float | None
    ewc_status: str
    coverage: CellCoverage

    def to_dict(self) -> dict[str, Any]:
        return {
            "cell": self.cell,
            "scenario": self.scenario,
            "event_scenario": self.event_scenario,
            "estimand": self.estimand,
            "status": self.status,
            "reason": self.reason,
            "n_periods": int(self.n_periods),
            "net_mean": _opt_float(self.net_mean),
            "hac_t": _opt_float(self.hac_t),
            "annualized_sr": _opt_float(self.annualized_sr),
            "periods_per_year": int(self.periods_per_year),
            "ewc_t": _opt_float(self.ewc_t),
            "ewc_p_one_sided": _opt_float(self.ewc_p_one_sided),
            "ewc_status": self.ewc_status,
            "coverage": self.coverage.to_dict(),
        }


@dataclass(frozen=True)
class GroupProfile:
    """The observed T0 quintile profile of one factor.

    ``mean_returns[i]`` is group ``i + 1`` (1 = lowest oriented factor value).
    ``monotonicity`` is the Spearman rank correlation of the group index with
    the group mean return over the finite groups.
    """

    scenario: str
    n_groups: int
    mean_returns: tuple[float | None, ...]
    monotonicity: float | None
    n_periods: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "n_groups": int(self.n_groups),
            "mean_returns": [_opt_float(value) for value in self.mean_returns],
            "monotonicity": _opt_float(self.monotonicity),
            "n_periods": int(self.n_periods),
        }


@dataclass(frozen=True)
class TurnoverProfile:
    """One-way turnover: the frozen B portfolio and the long / short legs."""

    mean_b: float | None
    mean_long: float | None
    mean_short: float | None
    n_periods: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "mean_b": _opt_float(self.mean_b),
            "mean_long": _opt_float(self.mean_long),
            "mean_short": _opt_float(self.mean_short),
            "n_periods": int(self.n_periods),
        }


@dataclass(frozen=True)
class FactorDiagnostic:
    """One known factor's IS diagnostic (descriptive, no claims)."""

    factor_id: str
    title: str
    source: str
    literature_direction: int
    certified_class: str | None
    not_certified_reason: str | None
    spec_sha256: str
    assessed: bool
    not_assessed_reason: str | None
    declaration_hash: str | None
    diagnostics_hash: str | None
    factor_panel_sha256: str | None
    n_groups: int
    cost_bps: float
    cells: tuple[CellStats, ...]
    group_profile: GroupProfile | None
    turnover: TurnoverProfile | None

    def cell(self, name: str) -> CellStats | None:
        for item in self.cells:
            if item.cell == name:
                return item
        return None

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
            "declaration_hash": self.declaration_hash,
            "diagnostics_hash": self.diagnostics_hash,
            "factor_panel_sha256": self.factor_panel_sha256,
            "n_groups": int(self.n_groups),
            "cost_bps": float(self.cost_bps),
            "cells": [item.to_dict() for item in self.cells],
            "group_profile": (
                None if self.group_profile is None else self.group_profile.to_dict()
            ),
            "turnover": None if self.turnover is None else self.turnover.to_dict(),
        }


@dataclass(frozen=True)
class KnownFactorsReport:
    """The frozen C4M0-2 machine-readable report."""

    fold_role: str
    fold_start: str
    fold_end: str
    universe: str
    n_groups: int
    cost_bps: float
    bound_alpha: float
    factors: tuple[FactorDiagnostic, ...]

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
            "bound_alpha": float(self.bound_alpha),
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


# ---------------------------------------------------------------------------
# evaluation
# ---------------------------------------------------------------------------


def _default_bindings() -> Any:
    return _d7b.DEFAULT_BINDINGS


def _export_bindings(assembly: Any) -> Any:
    declared = getattr(assembly, "export_bindings", None)
    if declared is not None:
        return declared
    evaluator = getattr(assembly, "ta_evaluator", None)
    bound = getattr(evaluator, "bindings", None)
    return bound if bound is not None else _default_bindings()


def _cell_coverage(cell: Any) -> CellCoverage:
    statuses = tuple(cell.period_statuses)
    reasons = tuple(cell.period_reasons)
    counter: Counter[str] = Counter()
    n_assessed = 0
    for status, reason in zip(statuses, reasons):
        if status == _envelope.NOT_ASSESSED:
            if reason:
                counter[str(reason)] += 1
        else:
            n_assessed += 1
    causes = tuple(sorted(counter.items()))
    return CellCoverage(
        n_periods=len(statuses),
        n_assessed=n_assessed,
        n_not_assessed=len(statuses) - n_assessed,
        causes=causes,
    )


def _ewc_statistic(
    periods: Sequence[date],
    values: Sequence[float],
    *,
    bound_alpha: float,
) -> tuple[float | None, float | None, str]:
    """The frozen ``EWC_T23_ONE_SIDED`` t / p for one oriented series.

    Called on the procedure implementation directly: the M0-2 statistic is
    descriptive and is not an admission-gated confirmation claim.  A declared
    :class:`ProcedureFailure` (insufficient sample, non-finite input, degenerate
    variance) is recorded as its ``condition_id`` and never repaired.
    """

    if len(values) == 0:
        return None, None, "NOT_ASSESSED"
    procedure = EWCT23OneSided()
    try:
        output = procedure.infer(
            series=Series(
                name="campaign4_m0_2:long_short",
                index=tuple(periods),
                values=tuple(float(value) for value in values),
            ),
            direction=Direction.POSITIVE,
            params=None,
            bound_alpha=float(bound_alpha),
        )
    except ProcedureFailure as exc:
        return None, None, str(exc.condition_id or "INFERENCE_INVALID")
    return (
        _opt_float(output.statistic),
        _opt_float(output.p_one_sided),
        "VALID",
    )


def _cell_stats(
    cell: Any,
    record: Any,
    *,
    periods_per_year: int,
    bound_alpha: float,
) -> CellStats:
    coverage = _cell_coverage(cell)
    if record is None or not record.assessed:
        return CellStats(
            cell=str(cell.cell),
            scenario=str(cell.scenario),
            event_scenario=(
                None if cell.event_scenario is None else str(cell.event_scenario)
            ),
            estimand=str(cell.estimand),
            status=cell.status if cell.status is not None else "NOT_ASSESSED",
            reason=cell.reason,
            n_periods=0,
            net_mean=None,
            hac_t=None,
            annualized_sr=None,
            periods_per_year=int(periods_per_year),
            ewc_t=None,
            ewc_p_one_sided=None,
            ewc_status="NOT_ASSESSED",
            coverage=coverage,
        )
    series = (
        record.b_series if str(record.estimand) == "B" else record.l_series
    )
    values = tuple(float(value) for value in series)
    summary = _metrics.long_short_mean_tstat(values)
    mean = _opt_float(summary.value)
    hac_t = _opt_float(summary.t_stat)
    sr = _opt_float(
        _metrics.sharpe_ratio(values, periods_per_year=int(periods_per_year)).value
    )
    ewc_t, ewc_p, ewc_status = _ewc_statistic(
        record.periods, values, bound_alpha=bound_alpha
    )
    return CellStats(
        cell=str(cell.cell),
        scenario=str(cell.scenario),
        event_scenario=(
            None if cell.event_scenario is None else str(cell.event_scenario)
        ),
        estimand=str(cell.estimand),
        status=None,
        reason=None,
        n_periods=len(values),
        net_mean=mean,
        hac_t=hac_t,
        annualized_sr=sr,
        periods_per_year=int(periods_per_year),
        ewc_t=ewc_t,
        ewc_p_one_sided=ewc_p,
        ewc_status=ewc_status,
        coverage=coverage,
    )


def _mean_b_turnover(periods: Sequence[Any]) -> tuple[float | None, int]:
    values = [
        float(period.TO_b)
        for period in periods
        if _opt_float(period.TO_b) is not None
    ]
    if not values:
        return None, 0
    return float(sum(values) / len(values)), len(values)


def _leg_turnover(
    scenario_panel: Any,
    *,
    n_groups: int,
    sign: int,
) -> tuple[float | None, float | None]:
    """Mean one-way turnover of the long and short legs.

    The grouping is the frozen ``assign_groups`` grouping (the same call D7b's
    ``_frozen_groups`` makes over the winsorized panel).  Per formation the leg
    is group ``G`` (long, ``sign = +1``) / group ``1`` (short); turnover is the
    symmetric-difference size over the union size, exactly the membership
    turnover definition, and the first formation contributes ``0.0``.
    """

    frame = scenario_panel[["date", "stock_id", "value"]]
    memberships: list[tuple[set[str], set[str]]] = []
    for _formation, sub in frame.groupby("date", sort=True):
        assigned = _sort.assign_groups(sub["value"], n_groups)
        long_group = n_groups if int(sign) == 1 else 1
        short_group = 1 if int(sign) == 1 else n_groups
        long_set: set[str] = set()
        short_set: set[str] = set()
        for key, group in zip(sub["stock_id"], assigned):
            value = _opt_float(group)
            if value is None:
                continue
            if int(value) == long_group:
                long_set.add(str(key))
            elif int(value) == short_group:
                short_set.add(str(key))
        memberships.append((long_set, short_set))

    def _mean_leg(index: int) -> float | None:
        turnovers: list[float] = []
        previous: set[str] | None = None
        for legs in memberships:
            current = legs[index]
            if previous is None:
                turnovers.append(0.0)
            else:
                union = previous | current
                turnovers.append(
                    float(len(previous ^ current) / len(union)) if union else 0.0
                )
            previous = current
        if not turnovers:
            return None
        return float(sum(turnovers) / len(turnovers))

    return _mean_leg(0), _mean_leg(1)


def _group_profile(
    scenario_panel: Any,
    *,
    n_groups: int,
) -> GroupProfile:
    """The observed equal-weight return of each of the ``n_groups`` quintiles."""

    frame = scenario_panel[["date", "value", "forward_return"]].copy()
    frame["weight"] = 1.0
    sorted_frame = _sort.sort_portfolios(
        frame,
        char_col="value",
        ret_col="forward_return",
        weight_col="weight",
        date_col="date",
        n_groups=int(n_groups),
    )
    means: list[float | None] = []
    n_periods = 0
    for group in range(1, int(n_groups) + 1):
        rows = sorted_frame[sorted_frame["group"] == group]
        valid = rows["ew_return"].dropna()
        n_periods = max(n_periods, int(valid.shape[0]))
        means.append(float(valid.mean()) if valid.shape[0] else None)
    indexed = [
        (index, value)
        for index, value in enumerate(means, start=1)
        if value is not None
    ]
    monotonicity: float | None = None
    if len(indexed) >= 2:
        result = scipy.stats.spearmanr(
            [item[0] for item in indexed], [item[1] for item in indexed]
        )
        monotonicity = _opt_float(result.statistic)
    return GroupProfile(
        scenario="T0",
        n_groups=int(n_groups),
        mean_returns=tuple(means),
        monotonicity=monotonicity,
        n_periods=n_periods,
    )


def evaluate_known_factor(
    assembly: Any,
    factor: KnownFactor,
    *,
    fold: Fold | None = None,
    panel: Any | None = None,
    bound_alpha: float = DEFAULT_BOUND_ALPHA,
) -> FactorDiagnostic:
    """Evaluate one certified known factor on the IS fold.

    ``panel`` overrides the production panel realization (used by the
    direction-oracle fixture to inject a panel that is *not* built through the
    grammar).  The IS firewall is asserted **before** any panel is built.
    """

    selected_fold = fold if fold is not None else assembly.is_fold
    _eval.assert_is_fold(selected_fold)

    spec = factor.spec
    if factor.certified_class is None:
        return FactorDiagnostic(
            factor_id=factor.factor_id,
            title=factor.title,
            source=factor.source,
            literature_direction=int(factor.literature_direction),
            certified_class=None,
            not_certified_reason=factor.not_certified_reason,
            spec_sha256=factor.spec_sha256,
            assessed=False,
            not_assessed_reason=factor.not_certified_reason,
            declaration_hash=None,
            diagnostics_hash=None,
            factor_panel_sha256=None,
            n_groups=0,
            cost_bps=0.0,
            cells=(),
            group_profile=None,
            turnover=None,
        )

    chosen_panel = (
        assembly.fast_panel_for(spec, selected_fold) if panel is None else panel
    )
    declaration = assembly.declaration_builder(spec, chosen_panel, selected_fold)
    evaluator = getattr(assembly, "ta_evaluator", None)
    roles = _eval.event_roles(spec)
    event_layer = getattr(assembly, "event_layer", None)
    if roles and event_layer is None:
        raise KnownFactorError(
            f"{factor.factor_id} is an event candidate but the assembly binds "
            "no event layer"
        )
    # A non-event candidate is evaluated without an event layer (the warm
    # ``TaEvaluator`` dispatch); passing the layer would require an event
    # scenario.  An event candidate keeps the sealed K0/K1 dispatch.
    diagnostics = _eval.evaluate_ta_diagnostics(
        assembly.dataset_root,
        declaration,
        spec,
        chosen_panel,
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

    export = None
    if diagnostics.not_assessed is None:
        export = assembly.export_series_fn(
            assembly.dataset_root,
            declaration,
            spec,
            chosen_panel,
            assembly.partition,
            diagnostics=diagnostics,
            overlay=assembly.overlay,
            inputs=assembly.inputs,
            event_layer=event_layer if roles else None,
            bindings=_export_bindings(assembly),
        )

    periods_per_year = int(declaration.base.periods_per_year)
    records = {str(record.cell): record for record in export.records} if export else {}
    cells = tuple(
        _cell_stats(
            cell,
            records.get(str(cell.cell)),
            periods_per_year=periods_per_year,
            bound_alpha=bound_alpha,
        )
        for cell in diagnostics.cells
    )

    group_profile: GroupProfile | None = None
    turnover: TurnoverProfile | None = None
    if diagnostics.not_assessed is None:
        scenarios, _formations = _series._reconstruct_non_event(
            assembly.dataset_root,
            declaration,
            spec,
            chosen_panel,
            assembly.partition,
            _export_bindings(assembly),
        )
        scenario_panel = scenarios[0].panel
        group_profile = _group_profile(
            scenario_panel, n_groups=int(declaration.base.n_groups)
        )
        mean_b, n_b = _mean_b_turnover(diagnostics.periods)
        mean_long, mean_short = _leg_turnover(
            scenario_panel,
            n_groups=int(declaration.base.n_groups),
            sign=int(spec.sign),
        )
        turnover = TurnoverProfile(
            mean_b=mean_b,
            mean_long=mean_long,
            mean_short=mean_short,
            n_periods=n_b,
        )

    return FactorDiagnostic(
        factor_id=factor.factor_id,
        title=factor.title,
        source=factor.source,
        literature_direction=int(factor.literature_direction),
        certified_class=factor.certified_class,
        not_certified_reason=None,
        spec_sha256=factor.spec_sha256,
        assessed=diagnostics.not_assessed is None,
        not_assessed_reason=(
            None
            if diagnostics.not_assessed is None
            else str(diagnostics.not_assessed)
        ),
        declaration_hash=str(diagnostics.declaration_hash),
        diagnostics_hash=str(diagnostics.diagnostics_hash),
        factor_panel_sha256=diagnostics.factor_panel_sha256,
        n_groups=int(declaration.base.n_groups),
        cost_bps=float(declaration.base.transaction_cost_bps),
        cells=cells,
        group_profile=group_profile,
        turnover=turnover,
    )


# ---------------------------------------------------------------------------
# orchestration / reporting
# ---------------------------------------------------------------------------


def _select_factors(
    factors: Sequence[KnownFactor] | None,
    factor_ids: Iterable[str] | None,
) -> tuple[KnownFactor, ...]:
    chosen = tuple(build_known_factors() if factors is None else factors)
    if factor_ids is None:
        return chosen
    wanted = list(factor_ids)
    known = {factor.factor_id for factor in chosen}
    unknown = [item for item in wanted if item not in known]
    if unknown:
        raise KnownFactorSpecError(f"unknown factor id(s): {unknown!r}")
    return tuple(factor for factor in chosen if factor.factor_id in set(wanted))


def run_known_factors(
    assembly: Any,
    *,
    output_dir: Path | str | None = None,
    fold: Fold | None = None,
    factors: Sequence[KnownFactor] | None = None,
    factor_ids: Iterable[str] | None = None,
    bound_alpha: float = DEFAULT_BOUND_ALPHA,
) -> KnownFactorsReport:
    """Run the C4M0-2 diagnostic and (optionally) write the two reports.

    The report is returned in all cases; ``output_dir`` only controls whether
    ``known_factors.json`` / ``known_factors.md`` are written.  No diagnostics
    artifact is persisted and no K-PIT record is appended.
    """

    selected_fold = fold if fold is not None else assembly.is_fold
    # The IS firewall is asserted before building any panel (plan section 3.3).
    _eval.assert_is_fold(selected_fold)
    chosen = _select_factors(factors, factor_ids)
    diagnostics = tuple(
        evaluate_known_factor(
            assembly,
            factor,
            fold=selected_fold,
            bound_alpha=bound_alpha,
        )
        for factor in chosen
    )
    report = KnownFactorsReport(
        fold_role=str(selected_fold.role.value),
        fold_start=_date_text(selected_fold.start),
        fold_end=_date_text(selected_fold.end),
        universe=str(_eval.TA_UNIVERSE),
        n_groups=_first_int(diagnostics, "n_groups", int(_eval.TA_N_GROUPS)),
        cost_bps=_first_float(diagnostics, "cost_bps", float(_eval.TA_COST_BPS)),
        bound_alpha=float(bound_alpha),
        factors=diagnostics,
    )
    if output_dir is not None:
        write_report(report, output_dir)
    return report


def _date_text(value: Any) -> str:
    if hasattr(value, "date"):
        return value.date().isoformat()
    return str(value)


def _first_int(
    diagnostics: Sequence[FactorDiagnostic], attribute: str, fallback: int
) -> int:
    for diagnostic in diagnostics:
        value = getattr(diagnostic, attribute, 0)
        if value:
            return int(value)
    return int(fallback)


def _first_float(
    diagnostics: Sequence[FactorDiagnostic], attribute: str, fallback: float
) -> float:
    for diagnostic in diagnostics:
        value = getattr(diagnostic, attribute, 0.0)
        if value:
            return float(value)
    return float(fallback)


def write_report(
    report: KnownFactorsReport,
    output_dir: Path | str,
) -> tuple[Path, Path]:
    """Write the canonical JSON and the rendered Markdown into ``output_dir``."""

    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / REPORT_JSON_NAME
    markdown_path = directory / REPORT_MARKDOWN_NAME
    json_path.write_text(_canonical_json(report.to_dict()) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, markdown_path


def _fmt(value: float | None, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def render_markdown(report: KnownFactorsReport) -> str:
    """Render the frozen C4M0-2 report as Markdown."""

    lines: list[str] = [
        "# Campaign-4 M0-2 -- known-factor replication (IS only)",
        "",
        f"- schema: `{SCHEMA}`",
        (
            f"- fold: `{report.fold_role}` "
            f"[{report.fold_start}, {report.fold_end})"
        ),
        f"- universe: `{report.universe}`, groups: {report.n_groups}, "
        f"cost: {report.cost_bps:g} bps",
        f"- EWC bound alpha: {report.bound_alpha:g}",
        f"- content SHA-256: `{report.content_sha256}`",
        "",
        "## Cells (B/L x scenario)",
        "",
        "| factor | cell | class | n | net mean | HAC t | EWC t | EWC p | ann SR |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for factor in report.factors:
        if not factor.cells:
            continue
        for cell in factor.cells:
            lines.append(
                "| {fid} | {cell} | {cls} | {n} | {mean} | {hac} | {ewc} | "
                "{p} | {sr} |".format(
                    fid=factor.factor_id,
                    cell=cell.cell,
                    cls=factor.certified_class or "NOT_CERTIFIED",
                    n=cell.n_periods,
                    mean=_fmt(cell.net_mean),
                    hac=_fmt(cell.hac_t),
                    ewc=_fmt(cell.ewc_t),
                    p=_fmt(cell.ewc_p_one_sided),
                    sr=_fmt(cell.annualized_sr),
                )
            )
    lines.extend(["", "## Group profile (observed T0)", ""])
    lines.append("| factor | g1 | g2 | g3 | g4 | g5 | monotonicity |")
    lines.append("|---|---|---|---|---|---|---|")
    for factor in report.factors:
        profile = factor.group_profile
        if profile is None:
            lines.append(f"| {factor.factor_id} | n/a | n/a | n/a | n/a | n/a | n/a |")
            continue
        values = [_fmt(item) for item in profile.mean_returns]
        values.extend(["n/a"] * (5 - len(values)))
        lines.append(
            f"| {factor.factor_id} | "
            + " | ".join(values[:5])
            + f" | {_fmt(profile.monotonicity)} |"
        )
    lines.extend(["", "## Turnover (one-way)", ""])
    lines.append("| factor | B | long leg | short leg |")
    lines.append("|---|---|---|---|")
    for factor in report.factors:
        turnover = factor.turnover
        if turnover is None:
            lines.append(f"| {factor.factor_id} | n/a | n/a | n/a |")
            continue
        lines.append(
            f"| {factor.factor_id} | {_fmt(turnover.mean_b)} | "
            f"{_fmt(turnover.mean_long)} | {_fmt(turnover.mean_short)} |"
        )
    lines.append("")
    return "\n".join(lines)
