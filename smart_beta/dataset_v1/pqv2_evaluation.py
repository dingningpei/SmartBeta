"""PQ2-H — P-Q v2 evaluation entry: bindings, B reuse, L statistics, classification.

This module is the PQ2-H deliverable of the frozen P-Q v2 implementation plan
(``docs/discovery_dataset_v1_2_pq_v2_implementation_plan.md`` §2, §6.4, §7, §9)
and implements the evaluation/reporting layer of the frozen methodology
(``docs/discovery_dataset_v1_2_pq_v2_methodology.md`` M§13-M§18).

It is a **composition** layer only.  It consumes the frozen P-Q v2 building
blocks unchanged:

* :mod:`smart_beta.dataset_v1.pqv2_core` (identities, typed statuses);
* :mod:`smart_beta.dataset_v1.pqv2_sources` (the source set);
* :mod:`smart_beta.dataset_v1.pqv2_labels` (Candidate-E label envelopes);
* :mod:`smart_beta.dataset_v1.pqv2_dependency` (certification + uncertainty);
* :mod:`smart_beta.dataset_v1.pqv2_assign_simple` / ``pqv2_assign_sandwich``;
* :mod:`smart_beta.dataset_v1.pqv2_envelope` (``compose_series``).

B is *exactly* the unmodified D7b pipeline
(:func:`delisting_bridge.build_development_diagnostics`), reconstructed through
the D7b private helpers and reconciled bit-for-bit.  No new uncertainty,
dependency, assignment, envelope, screen, advancement or confirmation semantics
are invented here.

The L statistic uses **exactly** B's ordered finite-period index and the same
Phase-7 estimator (:func:`smart_beta.evaluation.metrics.long_short_mean_tstat`)
restricted to that index.  Any NOT_ASSESSED period inside that index makes the
statistic unavailable (``PQV2_L_PERIOD_NOT_ASSESSED``); no period is ever
dropped, bridged or averaged away, and the P-Q v1 gap-aware HAC is never used.

Nothing here reads a dataset artifact directly: the D3/D4/D7A/D7c paths are
opened only through the frozen guarded resolvers and tests inject synthetic
bindings.  No network call is made and no credential value is read.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from smart_beta.dataset_v1 import delisting_bridge as _d7b
from smart_beta.dataset_v1 import pqv2_assign_sandwich as _sandwich
from smart_beta.dataset_v1 import pqv2_assign_simple as _simple
from smart_beta.dataset_v1 import pqv2_core as _core
from smart_beta.dataset_v1 import pqv2_dependency as _dependency
from smart_beta.dataset_v1 import pqv2_envelope as _envelope
from smart_beta.dataset_v1 import pqv2_labels as _labels
from smart_beta.dataset_v1 import pqv2_sources as _sources
from smart_beta.dataset_v1 import quarantine as _pq
from smart_beta.evaluation import metrics as _metrics
from smart_beta.evaluation import spec as _spec
from smart_beta.spec import evaluator as _evaluator
from smart_beta.spec import transforms as _transforms
from smart_beta.spec.factor_spec import FactorSpec

__all__ = [
    "PQ_METHODOLOGY",
    "CELL_NAMES",
    "SCENARIO_T0",
    "SCENARIO_TS",
    "ESTIMAND_B",
    "ESTIMAND_L",
    "ESTIMATOR_IDENTITY",
    "NON_CLAIMS",
    "PQv2DeclarationError",
    "PQv2InvariantError",
    "PQv2EvaluationDeclaration",
    "PQv2Cell",
    "PQv2Period",
    "PQv2Diagnostics",
    "PQv2ScreenOutcome",
    "PQv2Record",
    "build_pqv2_diagnostics",
    "classify_pqv2",
]

#: The v2 methodology tag a declaration must carry.
PQ_METHODOLOGY = "P-Q-v2"

#: The four frozen diagnostics cells.
CELL_NAMES = ("B-T0", "B-TS", "L-T0", "L-TS")

SCENARIO_T0 = "T0"
SCENARIO_TS = "TS"
_SCENARIOS = (SCENARIO_T0, SCENARIO_TS)

ESTIMAND_B = "B"
ESTIMAND_L = "L"

#: The Phase-7 estimator identity applied to the L series (M§13/H §2).
ESTIMATOR_IDENTITY = "metrics.long_short_mean_tstat"

#: The frozen M§18 non-claims tuple (safe wording: the exact prohibited claim
#: substrings never appear, because each is negated with a different word).
NON_CLAIMS: tuple[str, ...] = (
    "The model is not truth containment; robustness to every possible correction is not claimed.",
    "P-Q v2 does not bound the true return, does not certify data quality, and does not imply Gate-6 passed (Gate-6 stays INCONCLUSIVE).",
    "P-Q v2 does not sensitivity-test the correctness of security identity.",
    "The MISDATED model is one misdating at a time.",
    "The DV uncertainty model is a D7c-semantic statement.",
    "The H4/H5 sandwich is a superset, not exact.",
    "Outcomes: no confirmation, holdout or reserve validation.",
    "The correction model is declared, not empirically certified containment.",
)

#: The two certified metric bases (plan §6.3); any other metric is NOT_ASSESSED.
_NET = "TURNOVER_COST_ADJUSTED"
_GROSS = "LONG_SHORT"
_METRIC_BY_NAME = {
    "LONG_SHORT": _GROSS,
    "TURNOVER_COST_ADJUSTED": _NET,
}
_METRIC_BY_VALUE = {
    "long_short": _GROSS,
    "turnover_cost_adjusted": _NET,
}

_INVARIANT_TOL = 1e-12


class PQv2DeclarationError(_core.PQv2Error):
    """A declaration is not a well-formed :class:`PQv2EvaluationDeclaration`."""


class PQv2InvariantError(_core.PQv2InvariantError):
    """A frozen P-Q v2 evaluation invariant is violated."""


# ---------------------------------------------------------------------------
# Declaration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PQv2EvaluationDeclaration:
    """The hash-bound P-Q v2 evaluation declaration (H §1)."""

    base: _d7b.CampaignEvaluationDeclaration
    dataset_id: str
    dataset_identity_hash: str
    pq_id: str
    pq_hash: str
    methodology_sha256: str
    cs_id: str
    ed_id: str
    ce_id: str
    pq_methodology: str = PQ_METHODOLOGY

    def __post_init__(self) -> None:
        if not isinstance(self.base, _d7b.CampaignEvaluationDeclaration):
            raise PQv2DeclarationError(
                "base must be a CampaignEvaluationDeclaration"
            )
        for attribute in (
            "dataset_id",
            "dataset_identity_hash",
            "pq_id",
            "pq_hash",
            "methodology_sha256",
            "cs_id",
            "ed_id",
            "ce_id",
            "pq_methodology",
        ):
            value = getattr(self, attribute)
            if not isinstance(value, str) or not value:
                raise PQv2DeclarationError(
                    f"{attribute} must be a non-empty string"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "base": self.base.declaration_hash,
            "dataset_id": self.dataset_id,
            "dataset_identity_hash": self.dataset_identity_hash,
            "pq_id": self.pq_id,
            "pq_hash": self.pq_hash,
            "methodology_sha256": self.methodology_sha256,
            "cs_id": self.cs_id,
            "ed_id": self.ed_id,
            "ce_id": self.ce_id,
            "pq_methodology": self.pq_methodology,
        }

    @property
    def declaration_hash(self) -> str:
        return _d7b.content_hash(self.to_dict())


def _declaration_reason(declaration: PQv2EvaluationDeclaration) -> str | None:
    """The typed identity-mismatch reason, or ``None`` when the declaration binds."""

    if declaration.pq_methodology != PQ_METHODOLOGY:
        return _core.NotAssessedReason.IDENTITY_MISMATCH
    if declaration.methodology_sha256 != _core.METHODOLOGY_SHA256:
        return _core.NotAssessedReason.IDENTITY_MISMATCH
    if declaration.cs_id != _core.CS_ID:
        return _core.NotAssessedReason.IDENTITY_MISMATCH
    if declaration.ed_id != _core.ED_ID:
        return _core.NotAssessedReason.IDENTITY_MISMATCH
    if declaration.ce_id != _core.CE_ID:
        return _core.NotAssessedReason.IDENTITY_MISMATCH
    if declaration.dataset_id != _pq.DATASET_ID:
        return _core.NotAssessedReason.IDENTITY_MISMATCH
    return None


# ---------------------------------------------------------------------------
# Diagnostics value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PQv2Cell:
    """One ``(estimand, scenario)`` cell (plan §7 / H §3)."""

    cell: str
    scenario: str
    estimand: str
    value: float | None
    t_stat: float | None
    n_obs: int
    status: str | None
    reason: str | None
    n_index: int
    support: int
    estimator: str | None
    period_identities: tuple[date, ...]
    period_statuses: tuple[str, ...]
    period_values: tuple[float, ...]
    period_reasons: tuple[str | None, ...]
    declaration_hash: str
    period_causes: tuple[tuple[str, ...], ...] = ()
    cause_union: tuple[str, ...] = ()

    def _body(self) -> dict[str, Any]:
        return {
            "cell": self.cell,
            "scenario": self.scenario,
            "estimand": self.estimand,
            "value": self.value,
            "t_stat": self.t_stat,
            "n_obs": int(self.n_obs),
            "status": self.status,
            "reason": self.reason,
            "n_index": int(self.n_index),
            "support": int(self.support),
            "estimator": self.estimator,
            "period_identities": list(self.period_identities),
            "period_statuses": list(self.period_statuses),
            "period_values": list(self.period_values),
            "period_reasons": list(self.period_reasons),
            "declaration_hash": self.declaration_hash,
            "period_causes": [list(item) for item in self.period_causes],
            "cause_union": list(self.cause_union),
        }

    def to_dict(self) -> dict[str, Any]:
        body = self._body()
        body["cell_hash"] = self.cell_hash
        return body

    @property
    def cell_hash(self) -> str:
        return _d7b.content_hash(self._body())


@dataclass(frozen=True)
class PQv2Period:
    """One formation period's frozen M§7/§12/§13 reporting record."""

    period: date
    rule: str | None
    winsor_ok: bool | None
    D_t: int | None
    TO_b: float
    TO_hi: float
    changed_b: int
    union: int
    LS_b_t0: float
    LS_b_ts: float
    L_t0: float
    L_ts: float
    status_t0: str
    status_ts: str
    reason_t0: str | None
    reason_ts: str | None
    exposure: Mapping[str, Any]
    label_counts: Mapping[str, int]
    n_unknown_reserve: int
    sigma_ok: bool | None
    position_counts: Mapping[str, int]
    n_misdated: int
    causes_t0: tuple[str, ...] = ()
    causes_ts: tuple[str, ...] = ()
    n_unknown_source: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "period": self.period,
            "rule": self.rule,
            "winsor_ok": self.winsor_ok,
            "sigma_ok": self.sigma_ok,
            "D_t": self.D_t,
            "TO_b": self.TO_b,
            "TO_hi": self.TO_hi,
            "changed_b": int(self.changed_b),
            "union": int(self.union),
            "LS_b_t0": self.LS_b_t0,
            "LS_b_ts": self.LS_b_ts,
            "L_t0": self.L_t0,
            "L_ts": self.L_ts,
            "status_t0": self.status_t0,
            "status_ts": self.status_ts,
            "reason_t0": self.reason_t0,
            "reason_ts": self.reason_ts,
            "exposure": dict(self.exposure),
            "label_counts": dict(self.label_counts),
            "position_counts": dict(self.position_counts),
            "n_misdated": int(self.n_misdated),
            "n_unknown_reserve": int(self.n_unknown_reserve),
            "causes_t0": list(self.causes_t0),
            "causes_ts": list(self.causes_ts),
            "n_unknown_source": int(self.n_unknown_source),
        }


@dataclass(frozen=True)
class PQv2Diagnostics:
    """In-memory P-Q v2 diagnostics for one declared development fold."""

    not_assessed: str | None
    declaration_hash: str
    dataset_identity_hash: str
    overlay_pq_hash: str
    methodology_sha256: str
    cs_id: str
    ed_id: str
    ce_id: str
    pqv2_code_sha256: str
    primary_metric: str
    metric_basis: str
    basis: str
    sign: int
    orientation_rule: str
    screen_id: str
    screen_policy_hash: str
    certified_class: str | None
    certified_hypothesis: str | None
    assignment_rule: str | None
    relaxations: tuple[str, ...]
    factor_panel_sha256: str | None
    index: tuple[date, ...]
    cells: tuple[PQv2Cell, ...]
    periods: tuple[PQv2Period, ...]
    b_periods_t0: tuple[date, ...]
    b_periods_ts: tuple[date, ...]
    l_periods_t0: tuple[date, ...]
    l_periods_ts: tuple[date, ...]
    estimator_identity: str | None
    cost_bps: float
    cost_rate: float
    n_groups: int
    q: float
    support: tuple[tuple[str, int], ...]
    non_claims: tuple[str, ...]
    b_diagnostics_hash: str | None
    b_diagnostics: Any = field(default=None, compare=False, repr=False)

    def cell(self, name: str) -> PQv2Cell | None:
        for item in self.cells:
            if item.cell == name:
                return item
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "not_assessed": self.not_assessed,
            "declaration_hash": self.declaration_hash,
            "dataset_identity_hash": self.dataset_identity_hash,
            "overlay_pq_hash": self.overlay_pq_hash,
            "methodology_sha256": self.methodology_sha256,
            "cs_id": self.cs_id,
            "ed_id": self.ed_id,
            "ce_id": self.ce_id,
            "pqv2_code_sha256": self.pqv2_code_sha256,
            "primary_metric": self.primary_metric,
            "metric_basis": self.metric_basis,
            "basis": self.basis,
            "sign": int(self.sign),
            "orientation_rule": self.orientation_rule,
            "screen_id": self.screen_id,
            "screen_policy_hash": self.screen_policy_hash,
            "certified_class": self.certified_class,
            "certified_hypothesis": self.certified_hypothesis,
            "assignment_rule": self.assignment_rule,
            "relaxations": list(self.relaxations),
            "factor_panel_sha256": self.factor_panel_sha256,
            "index": list(self.index),
            "cells": [cell.to_dict() for cell in self.cells],
            "periods": [period.to_dict() for period in self.periods],
            "b_periods_t0": list(self.b_periods_t0),
            "b_periods_ts": list(self.b_periods_ts),
            "l_periods_t0": list(self.l_periods_t0),
            "l_periods_ts": list(self.l_periods_ts),
            "estimator_identity": self.estimator_identity,
            "cost_bps": self.cost_bps,
            "cost_rate": self.cost_rate,
            "n_groups": int(self.n_groups),
            "q": self.q,
            "support": [{"cell": name, "n": int(n)} for name, n in self.support],
            "non_claims": list(self.non_claims),
            "b_diagnostics_hash": self.b_diagnostics_hash,
        }

    @property
    def diagnostics_hash(self) -> str:
        return _d7b.content_hash(self.to_dict())


@dataclass(frozen=True)
class PQv2ScreenOutcome:
    """One Campaign/Phase-8 screen verdict for a P-Q v2 cell."""

    cell: str
    passed: bool | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "cell": self.cell,
            "passed": None if self.passed is None else bool(self.passed),
        }


@dataclass(frozen=True)
class PQv2Record:
    """The M§14 per-scenario classification record (never an ACCEPT)."""

    declaration_hash: str
    diagnostics_hash: str
    screen_id: str
    screen_policy_hash: str
    status: tuple[str, ...]
    not_assessed_reason: tuple[str | None, ...]
    b_screen: tuple[bool | None, ...]
    l_screen: tuple[bool | None, ...]
    advancement_permitted: tuple[bool, ...]
    accept: bool
    confirmation: bool
    scenario: tuple[str, ...]
    certified_class: str | None
    assignment_rule: str | None
    non_claims: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "declaration_hash": self.declaration_hash,
            "diagnostics_hash": self.diagnostics_hash,
            "screen_id": self.screen_id,
            "screen_policy_hash": self.screen_policy_hash,
            "status": list(self.status),
            "not_assessed_reason": list(self.not_assessed_reason),
            "b_screen": [
                None if item is None else bool(item) for item in self.b_screen
            ],
            "l_screen": [
                None if item is None else bool(item) for item in self.l_screen
            ],
            "advancement_permitted": [bool(item) for item in self.advancement_permitted],
            "accept": bool(self.accept),
            "confirmation": bool(self.confirmation),
            "scenario": list(self.scenario),
            "certified_class": self.certified_class,
            "assignment_rule": self.assignment_rule,
            "non_claims": list(self.non_claims),
        }

    @property
    def record_hash(self) -> str:
        return _d7b.content_hash(self.to_dict())


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _bits(value: float) -> bytes:
    return struct.pack("<d", float(value))


def _same_float(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return left is None and right is None
    if math.isnan(left) and math.isnan(right):
        return True
    return _bits(left) == _bits(right)


def _finite_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _is_finite(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _metric_kind(metric_basis: Any) -> str | None:
    name = getattr(metric_basis, "name", None)
    if isinstance(name, str) and name in _METRIC_BY_NAME:
        return _METRIC_BY_NAME[name]
    if isinstance(metric_basis, str) and metric_basis in _METRIC_BY_NAME:
        return _METRIC_BY_NAME[metric_basis]
    value = getattr(metric_basis, "value", None)
    if isinstance(value, str) and value in _METRIC_BY_VALUE:
        return _METRIC_BY_VALUE[value]
    if isinstance(metric_basis, str) and metric_basis in _METRIC_BY_VALUE:
        return _METRIC_BY_VALUE[metric_basis]
    return None


# ---------------------------------------------------------------------------
# B reuse: reconstruction and bit-exact reconciliation
# ---------------------------------------------------------------------------


@dataclass
class _ScenarioB:
    portfolio: Any
    panel: pd.DataFrame
    gross: pd.Series
    net: pd.Series
    value: float | None
    t_stat: float | None
    n_obs: int


def _reconstruct_b(
    root: Path | str,
    declaration: PQv2EvaluationDeclaration,
    factor_spec: FactorSpec,
    factor_panel: pd.DataFrame,
    partition: Any,
    bindings: _d7b.Bindings,
) -> tuple[tuple[_ScenarioB, _ScenarioB], Mapping[tuple[date, str], float], Any]:
    """Re-run the unmodified D7b pipeline and keep the per-period series."""

    base = declaration.base
    resolver = _d7b._Resolver(root, bindings)
    fold = _d7b._resolve_fold(base, partition)
    loaded = _d7b._load_d7a(resolver, bindings, base, partition, fold)
    panel = _d7b._validate_factor_panel(
        factor_panel,
        declaration=base,
        formations=loaded.formations,
        universe_by_formation=loaded.universe_by_formation,
    )
    base_panel = _d7b._base_panel(
        panel,
        formations=loaded.formations,
        universe_by_formation=loaded.universe_by_formation,
    )
    winsorized = _d7b._winsorize(base_panel, base.winsorization)
    groups = _d7b._frozen_groups(winsorized, base.n_groups)
    observed = _d7b._observed_panel(loaded.daily)
    observed_returns = _d7b._admissible_returns(
        factor_panel=panel,
        observed=observed,
        fold=fold,
        declaration=base,
        loaded=loaded,
    )
    r_obs = _d7b._terminal_obs_returns(
        factor_panel=panel,
        observed=observed,
        fold=fold,
        declaration=base,
        loaded=loaded,
    )
    panel_t0, panel_ts, _records = _d7b._scenario_panels(
        winsorized=winsorized,
        groups=groups,
        r_obs=r_obs,
        observed_returns=observed_returns,
        declaration=base,
        loaded=loaded,
        sign=int(factor_spec.sign),
    )
    scenarios: list[_ScenarioB] = []
    for scenario_panel in (panel_t0, panel_ts):
        portfolio, value, t_stat, n_obs = _d7b._evaluate_scenario(
            scenario_panel, base
        )
        scenarios.append(
            _ScenarioB(
                portfolio=portfolio,
                panel=scenario_panel,
                gross=portfolio.gross_returns,
                net=portfolio.net_returns,
                value=_finite_or_none(value),
                t_stat=_finite_or_none(t_stat),
                n_obs=int(n_obs),
            )
        )
    return (scenarios[0], scenarios[1]), groups, loaded


def _reconcile_b(
    scenarios: tuple[_ScenarioB, _ScenarioB],
    diagnostics: _d7b.DevelopmentDiagnostics,
    sign: int,
) -> None:
    expected = (
        (diagnostics.value_t0, diagnostics.t_t0, diagnostics.n_obs_t0),
        (diagnostics.value_ts, diagnostics.t_ts, diagnostics.n_obs_ts),
    )
    for scenario_b, (value, t_stat, n_obs) in zip(scenarios, expected):
        if not _same_float(scenario_b.value, value):
            raise PQv2InvariantError(
                f"B value {scenario_b.value!r} != diag_B {value!r}"
            )
        oriented_t = scenario_b.t_stat
        if sign == -1 and oriented_t is not None:
            oriented_t = -oriented_t
        if not _same_float(oriented_t, t_stat):
            raise PQv2InvariantError(
                f"B t {oriented_t!r} != diag_B {t_stat!r}"
            )
        if int(scenario_b.n_obs) != int(n_obs):
            raise PQv2InvariantError(
                f"B n_obs {scenario_b.n_obs!r} != diag_B {n_obs!r}"
            )


def _b_series_for_metric(scenario: _ScenarioB, metric: str) -> pd.Series:
    if metric == _NET:
        return scenario.net
    return scenario.gross


def _series_lookup(series: pd.Series) -> dict[date, float]:
    lookup: dict[date, float] = {}
    for key, value in series.items():
        parsed = _d7b._as_date(key)
        if parsed is None:
            continue
        lookup[parsed] = float(value)
    return lookup


# ---------------------------------------------------------------------------
# STD_X_RANK sub-expression realization (plan §6.4 step 6)
# ---------------------------------------------------------------------------


def _alias_frames(
    factor_spec: FactorSpec,
    inputs: Mapping[str, Any],
    overlay: _pq.Overlay,
) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    for item in factor_spec.inputs:
        trusted = inputs.get(item.requirement.semantic_id)
        if trusted is None:
            raise _core.PQv2InputError(
                f"factor spec references {item.requirement.semantic_id!r} "
                "but it is not supplied"
            )
        values = trusted.values
        frames[item.alias] = values.mask(
            overlay.mask(item.requirement.semantic_id, values)
        )
    return frames


def _evaluate_subexpression(
    factor_spec: FactorSpec,
    expression: Any,
    frames: Mapping[str, pd.DataFrame],
    *,
    suffix: str,
) -> pd.DataFrame:
    sub = FactorSpec(
        id=f"{factor_spec.id}_{suffix}",
        description=f"{factor_spec.description} ({suffix} operand)",
        expression=expression,
        inputs=factor_spec.inputs,
        frequency=factor_spec.frequency,
        missing_policy=factor_spec.missing_policy,
        sign=1,
    )
    result = _evaluator.evaluate(sub, frames)
    panel = result.panel
    if len(panel) == 0:
        return pd.DataFrame(dtype="float64")
    wide = (
        panel.pivot(index="date", columns="stock_id", values="value")
        .sort_index()
        .sort_index(axis=1)
    )
    return wide.astype("float64")


def _realize_std_x_rank(
    factor_spec: FactorSpec,
    windows: _dependency.Windows,
    inputs: Mapping[str, Any],
    overlay: _pq.Overlay,
    factor_panel: pd.DataFrame,
    formations: Sequence[date],
    universe_by_formation: Mapping[date, tuple[str, ...]],
) -> tuple[dict[date, pd.DataFrame], dict[date, pd.DataFrame]]:
    """Realize the M and V operands and assert the F decomposition per formation."""

    if windows.m_operand is None or windows.v_operand is None:
        raise PQv2InvariantError(
            "STD_X_RANK expression has no M/V operand to realize"
        )
    frames = _alias_frames(factor_spec, inputs, overlay)
    m_wide = _evaluate_subexpression(
        factor_spec, windows.m_operand, frames, suffix="m"
    )
    v_wide = _evaluate_subexpression(
        factor_spec, windows.v_operand, frames, suffix="v"
    )

    panel_lookup: dict[tuple[date, str], float] = {}
    for row in factor_panel.itertuples(index=False):
        parsed = _d7b._as_date(row.date)
        if parsed is None:
            continue
        panel_lookup[(parsed, str(row.stock_id))] = float(row.value)

    for formation in formations:
        m_raw = _row_or_none(m_wide, formation)
        v_raw = _row_or_none(v_wide, formation)
        if m_raw is None or v_raw is None:
            continue
        m_row = pd.DataFrame([m_raw])
        v_row = pd.DataFrame([v_raw])
        z = _transforms.cross_sectional_standardize(m_row, ddof=0)
        r = _transforms.cross_sectional_rank(v_row)
        f_calc = (z * r).iloc[0]
        for key in universe_by_formation.get(formation, ()):
            if key not in f_calc.index:
                continue
            expected = panel_lookup.get((formation, key))
            actual = f_calc[key]
            if expected is None or not math.isfinite(expected):
                continue
            if not math.isfinite(float(actual)):
                raise PQv2InvariantError(
                    "sub-expression realization produced no finite value for "
                    f"finite factor cell {key} at {formation}"
                )
            tolerance = _INVARIANT_TOL * max(1.0, abs(expected))
            if abs(float(actual) - expected) > tolerance:
                raise PQv2InvariantError(
                    "factor cell does not equal standardize(M)*rank(V): "
                    f"{key} at {formation}: {actual!r} != {expected!r}"
                )
    return _wide_by_formation(m_wide, formations), _wide_by_formation(
        v_wide, formations
    )


def _row_or_none(wide: pd.DataFrame, formation: date) -> pd.Series | None:
    if wide.empty:
        return None
    stamp = pd.Timestamp(formation)
    if stamp not in wide.index:
        return None
    return wide.loc[stamp]


def _wide_by_formation(
    wide: pd.DataFrame, formations: Sequence[date]
) -> dict[date, pd.Series]:
    out: dict[date, pd.Series] = {}
    for formation in formations:
        row = _row_or_none(wide, formation)
        if row is not None:
            out[formation] = row
    return out


# ---------------------------------------------------------------------------
# Per-formation evaluation
# ---------------------------------------------------------------------------


def _formation_positions(
    sessions: Sequence[date], formation: date
) -> dict[date, int]:
    base = None
    for index, session in enumerate(sessions):
        if session == formation:
            base = index
            break
    if base is None:
        return {}
    return {session: index - base for index, session in enumerate(sessions)}


def _dv_offsets(
    windows: _dependency.Windows, formation_pos: Mapping[date, int]
) -> tuple[int, ...]:
    cells: set[int] = set()
    for window in tuple(windows.numerical_dv_short) + tuple(
        windows.numerical_dv_long
    ):
        cells.update(range(window[0] + 1, window[1] + 1))
    return tuple(sorted(cells))


def _observed_values(
    key: str,
    m_row: pd.Series | None,
    dv_frame: pd.DataFrame | None,
    session_at_offset: Mapping[int, date],
    offsets: Sequence[int],
) -> _dependency.ObservedValues:
    m_obs = None
    if m_row is not None and key in m_row.index:
        value = m_row[key]
        if _is_finite(value):
            m_obs = float(value)
    dv: dict[int, float] = {}
    if dv_frame is not None:
        for offset in offsets:
            session = session_at_offset.get(offset)
            if session is None:
                continue
            stamp = pd.Timestamp(session)
            if stamp not in dv_frame.index or key not in dv_frame.columns:
                continue
            value = dv_frame.at[stamp, key]
            if _is_finite(value):
                dv[offset] = float(value)
    return _dependency.ObservedValues(M_obs=m_obs, dv=dv)


def _labels_for_formation(
    formation: date,
    universe: Sequence[str],
    source_set: _core.SourceSet,
    label_by_key: Mapping[tuple[date, str], Mapping[str, Any]],
    scenario_t0: Mapping[tuple[date, str], float],
    scenario_ts: Mapping[tuple[date, str], float],
) -> dict[str, _labels.LabelEnvelope]:
    labels: dict[str, _labels.LabelEnvelope] = {}
    for key in sorted(universe):
        record = label_by_key.get((formation, key))
        if record is None:
            raise PQv2InvariantError(
                f"no D7a label record for universe member {key!r} at {formation}"
            )
        transitions = tuple(source_set.transitions_by_key.get(key, ()))
        candidates = tuple(source_set.candidates_by_key.get(key, ()))
        unknown = source_set.unknown_by_key.get(key)
        labels[key] = _labels.label_envelope(
            record,
            transitions,
            candidates,
            unknown,
            t0_value=scenario_t0.get((formation, key)),
            ts_value=scenario_ts.get((formation, key)),
        )
    return labels


def _assignment_for_formation(
    formation: date,
    universe: Sequence[str],
    values: Mapping[str, float],
    source_set: _core.SourceSet,
    windows: _dependency.Windows,
    cls: str,
    G: int,
    sessions: Sequence[date],
    m_row: pd.Series | None,
    v_row: pd.Series | None,
    dv_frame: pd.DataFrame | None,
    labels: Mapping[str, _labels.LabelEnvelope],
    b_groups: Mapping[str, int],
    cross_keys: Sequence[str],
) -> Any:
    formation_pos = _formation_positions(sessions, formation)
    long_values = {
        key: float(labels[key].t0_lo) if key in labels else math.nan
        for key in universe
    }
    short_values = {
        key: float(labels[key].t0_hi) if key in labels else math.nan
        for key in universe
    }
    row_order = {key: index for index, key in enumerate(sorted(universe))}
    uncertainty_source = _session_only_source_set(source_set)
    session_at_offset = {offset: session for session, offset in formation_pos.items()}

    if cls == _dependency.SIMPLE:
        uncertainty = _dependency.input_uncertainty(
            _dependency.SIMPLE,
            windows,
            uncertainty_source,
            formation_pos,
            tuple(sorted(universe)),
            None,
        )
        return _simple.simple_assignment(
            tuple(sorted(universe)),
            values,
            row_order,
            uncertainty.uncertain,
            uncertainty.unknown,
            G,
            long_values,
            short_values,
            cls=_dependency.SIMPLE,
            causes=uncertainty.causes,
        )

    offsets = _dv_offsets(windows, formation_pos)
    observed = {
        key: _observed_values(
            key, m_row, dv_frame, session_at_offset, offsets
        )
        for key in sorted(cross_keys)
    }
    uncertainty = _dependency.input_uncertainty(
        _dependency.STD_X_RANK,
        windows,
        uncertainty_source,
        formation_pos,
        tuple(sorted(cross_keys)),
        observed,
    )
    m_obs = {
        key: (m_row[key] if m_row is not None and key in m_row.index else math.nan)
        for key in cross_keys
    }
    v_obs = {
        key: (v_row[key] if v_row is not None and key in v_row.index else math.nan)
        for key in cross_keys
    }
    return _sandwich.sandwich_assignment(
        tuple(sorted(cross_keys)),
        m_obs,
        uncertainty.M_interval,
        v_obs,
        uncertainty.V_interval,
        uncertainty.unknown,
        tuple(sorted(universe)),
        b_groups,
        G,
        long_values,
        short_values,
        causes=uncertainty.causes,
    )


def _session_only_source_set(source_set: _core.SourceSet) -> _core.SourceSet:
    """A view of ``source_set`` whose ``unknown_by_key`` entries are flagged.

    P-Q v2.1 forbids the legacy conversion of an arbitrary entry (a bare
    session, or a ``(session, prev_session, reason)`` triple) into a session.
    Every entry must already be A's :class:`pqv2_core.UnresolvedFlag`; it is
    passed through unchanged so C sees the exact source state (and hence the
    exact M§8A.2 cause).  Anything else fails closed with
    :class:`pqv2_core.PQv2InputError`.
    """

    unknown: dict[str, tuple[_core.UnresolvedFlag, ...]] = {}
    for key, value in source_set.unknown_by_key.items():
        if isinstance(value, _core.UnresolvedFlag):
            entries: tuple[Any, ...] = (value,)
        elif isinstance(value, (tuple, list)):
            entries = tuple(value)
        else:
            raise _core.PQv2InputError(
                f"unknown_by_key[{key!r}] must be a tuple of UnresolvedFlag, "
                f"got {value!r}"
            )
        for entry in entries:
            if not isinstance(entry, _core.UnresolvedFlag):
                raise _core.PQv2InputError(
                    f"unknown_by_key[{key!r}] entry must be UnresolvedFlag, "
                    f"got {entry!r}"
                )
        unknown[key] = entries
    return _core.SourceSet(
        transitions_by_key=source_set.transitions_by_key,
        candidates_by_key=source_set.candidates_by_key,
        unknown_by_key=unknown,
    )


# ---------------------------------------------------------------------------
# L statistics
# ---------------------------------------------------------------------------


def _period_causes_of(period: Any, scenario: str) -> tuple[str, ...]:
    """The canonical period cause list PQ21-F attached to *period* (M§8A.5)."""

    raw = period.causes_t0 if scenario == SCENARIO_T0 else period.causes_ts
    return tuple(str(item) for item in raw)


def _estimate_l(
    envelope: _envelope.EnvelopeSeries,
    b_lookup: Mapping[date, float],
    scenario: str,
) -> tuple[
    float | None,
    float | None,
    int,
    str | None,
    str | None,
    tuple[tuple[str, ...], ...],
    tuple[str, ...],
]:
    """Apply the Phase-7 estimator to the L series on B's finite-period index.

    Returns ``(value, t_stat, n_obs, status, reason, period_causes,
    cause_union)``.  ``period_causes`` is the per-period canonical cause list
    over B's finite-period index (in period order); ``cause_union`` is the
    canonical union list of all of them (M§8A.5: the persisted causes stay
    auditable under the same-period rule).
    """

    periods = tuple(period.period for period in envelope.periods)
    if len(set(periods)) != len(periods):
        raise PQv2InvariantError("composed L series has duplicate formation periods")
    if tuple(periods) != tuple(b_lookup):
        raise PQv2InvariantError(
            "B and L period identities or order differ: "
            f"B={list(b_lookup)} L={list(periods)}"
        )
    by_period = {period.period: period for period in envelope.periods}
    t_b = tuple(period for period in periods if _is_finite(b_lookup[period]))
    period_causes = tuple(
        _period_causes_of(by_period[period], scenario) for period in t_b
    )
    cause_union = _core.CauseSet.from_iterable(
        reason for causes in period_causes for reason in causes
    )
    cause_union_list = tuple(cause_union.to_list())

    def _fail(reason: str) -> tuple[
        None,
        None,
        int,
        str,
        str,
        tuple[tuple[str, ...], ...],
        tuple[str, ...],
    ]:
        if (
            reason == _core.NotAssessedReason.L_PERIOD_NOT_ASSESSED
            and not cause_union_list
        ):
            raise PQv2InvariantError(
                "L is NOT_ASSESSED for a period with no persisted cause "
                f"(scenario={scenario})"
            )
        return (
            None,
            None,
            len(t_b),
            _envelope.NOT_ASSESSED,
            reason,
            period_causes,
            cause_union_list,
        )

    values: list[float] = []
    for period in t_b:
        item = by_period[period]
        if item.status(scenario) != _envelope.ASSESSED:
            return _fail(_core.NotAssessedReason.L_PERIOD_NOT_ASSESSED)
        value = item.value(scenario)
        if not _is_finite(value):
            return _fail(_core.NotAssessedReason.L_PERIOD_NOT_ASSESSED)
        values.append(float(value))
    result = _metrics.long_short_mean_tstat(np.asarray(values, dtype="float64"))
    if result.value is None or result.t_stat is None:
        return (
            None,
            None,
            int(result.n_obs),
            _envelope.NOT_ASSESSED,
            _core.NotAssessedReason.L_STATISTIC_UNAVAILABLE,
            period_causes,
            cause_union_list,
        )
    if not (math.isfinite(float(result.value)) and math.isfinite(float(result.t_stat))):
        return (
            None,
            None,
            int(result.n_obs),
            _envelope.NOT_ASSESSED,
            _core.NotAssessedReason.L_STATISTIC_UNAVAILABLE,
            period_causes,
            cause_union_list,
        )
    return (
        float(result.value),
        float(result.t_stat),
        int(result.n_obs),
        None,
        None,
        period_causes,
        cause_union_list,
    )


def _period_cell_arrays(
    envelope: _envelope.EnvelopeSeries, scenario: str
) -> tuple[tuple[date, ...], tuple[str, ...], tuple[float, ...], tuple[str | None, ...]]:
    identities: list[date] = []
    statuses: list[str] = []
    values: list[float] = []
    reasons: list[str | None] = []
    for period in envelope.periods:
        identities.append(period.period)
        statuses.append(period.status(scenario))
        values.append(period.value(scenario))
        reasons.append(period.reason(scenario))
    return tuple(identities), tuple(statuses), tuple(values), tuple(reasons)


# ---------------------------------------------------------------------------
# Blank diagnostics
# ---------------------------------------------------------------------------


def _blank_diagnostics(
    declaration: PQv2EvaluationDeclaration,
    factor_spec: FactorSpec,
    reason: str,
    *,
    metric_basis: str | None = None,
    certified_class: Any = None,
) -> PQv2Diagnostics:
    declaration_hash = declaration.declaration_hash
    cells = tuple(
        PQv2Cell(
            cell=name,
            scenario=(SCENARIO_T0 if name.endswith("T0") else SCENARIO_TS),
            estimand=(ESTIMAND_B if name.startswith("B") else ESTIMAND_L),
            value=None,
            t_stat=None,
            n_obs=0,
            status=reason,
            reason=reason,
            n_index=0,
            support=0,
            estimator=None,
            period_identities=(),
            period_statuses=(),
            period_values=(),
            period_reasons=(),
            declaration_hash=declaration_hash,
        )
        for name in CELL_NAMES
    )
    return PQv2Diagnostics(
        not_assessed=reason,
        declaration_hash=declaration_hash,
        dataset_identity_hash=declaration.dataset_identity_hash,
        overlay_pq_hash=declaration.pq_hash,
        methodology_sha256=declaration.methodology_sha256,
        cs_id=declaration.cs_id,
        ed_id=declaration.ed_id,
        ce_id=declaration.ce_id,
        pqv2_code_sha256=_core.pqv2_code_sha256(),
        primary_metric=declaration.base.primary_metric.value,
        metric_basis=metric_basis or "",
        basis=declaration.base.basis,
        sign=int(factor_spec.sign),
        orientation_rule=_orientation_rule(
            int(factor_spec.sign), declaration.base.primary_metric
        ),
        screen_id=declaration.base.screen_id,
        screen_policy_hash=declaration.base.screen_policy_hash,
        certified_class=None
        if certified_class is None
        else str(certified_class),
        certified_hypothesis=None
        if certified_class is None
        else getattr(certified_class, "hypothesis", None),
        assignment_rule=None,
        relaxations=(),
        factor_panel_sha256=declaration.base.factor_panel_sha256,
        index=(),
        cells=cells,
        periods=(),
        b_periods_t0=(),
        b_periods_ts=(),
        l_periods_t0=(),
        l_periods_ts=(),
        estimator_identity=None,
        cost_bps=float(declaration.base.transaction_cost_bps),
        cost_rate=float(declaration.base.transaction_cost_bps) / 1.0e4,
        n_groups=int(declaration.base.n_groups),
        q=float(declaration.base.winsorization),
        support=(),
        non_claims=NON_CLAIMS,
        b_diagnostics_hash=None,
        b_diagnostics=None,
    )


def _orientation_rule(sign: int, metric: Any) -> str:
    return _d7b._orientation_rule(sign, metric)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build_pqv2_diagnostics(
    root: Path | str,
    declaration: PQv2EvaluationDeclaration,
    factor_spec: FactorSpec,
    factor_panel: pd.DataFrame,
    partition: Any,
    *,
    overlay: _pq.Overlay,
    inputs: Mapping[str, Any],
    bindings: _d7b.Bindings | None = None,
    source_bindings: Any | None = None,
    pq_bindings: _pq.Bindings | None = None,
    pq_repo_root: Path | str | None = None,
    pq_knowledge_log_path: Path | str | None = None,
    pq_read_records_fn: Any | None = None,
    sessions: Sequence[date] | None = None,
    repo_root: Path | str | None = None,
) -> PQv2Diagnostics:
    """Evaluate one declared development fold under the frozen P-Q v2 model."""

    if not isinstance(declaration, PQv2EvaluationDeclaration):
        raise PQv2DeclarationError(
            "declaration must be a PQv2EvaluationDeclaration"
        )
    bindings = bindings if bindings is not None else _d7b.DEFAULT_BINDINGS

    # Step 1 - authority and declaration binding, before any data access.
    _core.verify_authority(repo_root)
    reason = _declaration_reason(declaration)
    if reason is not None:
        return _blank_diagnostics(declaration, factor_spec, reason)

    # Step 2 - dataset identity and overlay.
    try:
        identity_hash = _pq.verify_dataset_v1_2_identity(
            root,
            bindings=pq_bindings,
            repo_root=pq_repo_root,
            knowledge_log_path=pq_knowledge_log_path,
            read_records_fn=pq_read_records_fn,
        )
    except _pq.DatasetIdentityError:
        return _blank_diagnostics(
            declaration,
            factor_spec,
            _core.NotAssessedReason.DATASET_IDENTITY_MISMATCH,
        )
    if identity_hash != declaration.dataset_identity_hash:
        return _blank_diagnostics(
            declaration,
            factor_spec,
            _core.NotAssessedReason.DATASET_IDENTITY_MISMATCH,
        )
    if overlay.pq_id != declaration.pq_id or overlay.pq_hash != declaration.pq_hash:
        return _blank_diagnostics(
            declaration,
            factor_spec,
            _core.NotAssessedReason.OVERLAY_MISMATCH,
        )

    # Step 3 - expression, sign and metric gates.
    certified = _dependency.certify(factor_spec)
    if isinstance(certified, _core.NotAssessed):
        return _blank_diagnostics(declaration, factor_spec, certified.reason)
    windows = _dependency.dependency_windows(factor_spec.expression)
    if isinstance(windows, _core.NotAssessed):
        return _blank_diagnostics(declaration, factor_spec, windows.reason)
    metric_basis = _metric_kind(declaration.base.primary_metric)
    if metric_basis is None:
        return _blank_diagnostics(
            declaration,
            factor_spec,
            _core.NotAssessedReason.METRIC_NOT_CERTIFIED,
            certified_class=certified,
        )

    # Step 4 - B exactly as D7b, reconstructed and reconciled bit-exactly.
    diag_b = _d7b.build_development_diagnostics(
        root,
        declaration.base,
        factor_spec,
        factor_panel,
        partition,
        bindings=bindings,
    )
    if diag_b.not_assessed is not None:
        return _blank_diagnostics(
            declaration,
            factor_spec,
            diag_b.not_assessed,
            metric_basis=metric_basis,
            certified_class=certified,
        )
    (scenario_t0, scenario_ts), groups, loaded = _reconstruct_b(
        root, declaration, factor_spec, factor_panel, partition, bindings
    )
    _reconcile_b((scenario_t0, scenario_ts), diag_b, sign=int(factor_spec.sign))

    formations = tuple(loaded.formations)
    universe_by_formation = {
        formation: tuple(sorted(loaded.universe_by_formation.get(formation, ())))
        for formation in formations
    }

    # Step 5 - source set.
    session_list = (
        tuple(sessions)
        if sessions is not None
        else _sessions_from_inputs(inputs)
    )
    source_set = _sources.load_source_set(
        root,
        overlay=overlay,
        d7a_bindings=source_bindings
        if source_bindings is not None
        else bindings,
        ti_inputs=inputs,
        sessions=session_list,
    )

    # Step 6 - STD_X_RANK operand realization and consistency assertion.
    m_by_formation: dict[date, pd.DataFrame] = {}
    v_by_formation: dict[date, pd.DataFrame] = {}
    if str(certified) == _dependency.STD_X_RANK:
        m_by_formation, v_by_formation = _realize_std_x_rank(
            factor_spec,
            windows,
            inputs,
            overlay,
            factor_panel,
            formations,
            universe_by_formation,
        )

    dv_frame = _dv_frame(inputs, overlay)

    # Step 7 - per-formation uncertainty, labels and assignment.
    assignment_by_period: dict[date, Any] = {}
    labels_by_period: dict[date, dict[str, _labels.LabelEnvelope]] = {}
    b_groups_by_period: dict[date, dict[str, int]] = {}
    cross_by_period: dict[date, tuple[str, ...]] = {}
    factor_values = _factor_lookup(factor_panel)
    scenario_t0_lookup = _scenario_lookup(scenario_t0.panel)
    scenario_ts_lookup = _scenario_lookup(scenario_ts.panel)

    group_by_formation: dict[date, dict[str, int]] = {}
    for (formation, key), group in groups.items():
        if group is None or (isinstance(group, float) and math.isnan(group)):
            continue
        group_by_formation.setdefault(formation, {})[str(key)] = int(group)

    for formation in formations:
        universe = universe_by_formation[formation]
        values = {
            key: factor_values.get((formation, key), math.nan) for key in universe
        }
        b_groups = group_by_formation.get(formation, {})
        b_groups_by_period[formation] = b_groups
        labels = _labels_for_formation(
            formation,
            universe,
            source_set,
            loaded.label_by_key,
            scenario_t0_lookup,
            scenario_ts_lookup,
        )
        labels_by_period[formation] = labels
        cross_keys = _cross_section_keys(m_by_formation, v_by_formation, formation)
        cross_by_period[formation] = cross_keys
        assignment_by_period[formation] = _assignment_for_formation(
            formation,
            universe,
            values,
            source_set,
            windows,
            str(certified),
            int(declaration.base.n_groups),
            session_list,
            m_by_formation.get(formation),
            v_by_formation.get(formation),
            dv_frame,
            labels,
            b_groups,
            cross_keys,
        )

    # Step 8 - composition (with the frozen gates).
    envelope = _envelope.compose_series(
        formations,
        declaration.base.primary_metric,
        int(factor_spec.sign),
        declaration.base.transaction_cost_bps,
        int(declaration.base.n_groups),
        float(declaration.base.winsorization),
        assignment_by_period,
        labels_by_period,
        b_groups_by_period,
        cls=str(certified),
        repo_root=repo_root,
    )
    if envelope.gate_reason is not None:
        return _blank_diagnostics(
            declaration,
            factor_spec,
            envelope.gate_reason,
            metric_basis=metric_basis,
            certified_class=certified,
        )

    # Step 9 - the L statistics on B's ordered finite-period index.
    cells: list[PQv2Cell] = []
    b_series_t0 = _series_lookup(_b_series_for_metric(scenario_t0, metric_basis))
    b_series_ts = _series_lookup(_b_series_for_metric(scenario_ts, metric_basis))
    b_periods_t0 = tuple(
        formation for formation in formations if _is_finite(b_series_t0.get(formation))
    )
    b_periods_ts = tuple(
        formation for formation in formations if _is_finite(b_series_ts.get(formation))
    )

    b_values = (
        (SCENARIO_T0, diag_b.economic_t0, diag_b.t_t0, diag_b.n_obs_t0, b_series_t0),
        (SCENARIO_TS, diag_b.economic_ts, diag_b.t_ts, diag_b.n_obs_ts, b_series_ts),
    )
    l_results: dict[
        str,
        tuple[
            float | None,
            float | None,
            int,
            str | None,
            str | None,
            tuple[tuple[str, ...], ...],
            tuple[str, ...],
        ],
    ] = {}
    for scenario, value, t_stat, n_obs, b_series in b_values:
        b_available = value is not None and t_stat is not None
        identities, statuses, values_arr, reasons_arr = _period_cell_arrays(
            envelope, scenario
        )
        if not b_available:
            l_results[scenario] = (
                None,
                None,
                0,
                _envelope.NOT_ASSESSED,
                _core.NotAssessedReason.B_STATISTIC_UNAVAILABLE,
                (),
                (),
            )
        else:
            ordered_b = {
                formation: b_series.get(formation, float("nan"))
                for formation in formations
            }
            l_results[scenario] = _estimate_l(envelope, ordered_b, scenario)
        cells.append(
            PQv2Cell(
                cell=f"B-{scenario}",
                scenario=scenario,
                estimand=ESTIMAND_B,
                value=value,
                t_stat=t_stat,
                n_obs=int(n_obs) if n_obs is not None else 0,
                status=None if b_available else _envelope.NOT_ASSESSED,
                reason=None
                if b_available
                else _core.NotAssessedReason.B_STATISTIC_UNAVAILABLE,
                n_index=len(identities),
                support=int(n_obs) if n_obs is not None else 0,
                estimator=ESTIMATOR_IDENTITY,
                period_identities=identities,
                period_statuses=statuses,
                period_values=values_arr,
                period_reasons=reasons_arr,
                declaration_hash=declaration.declaration_hash,
            )
        )
        l_value, l_t, l_n, l_status, l_reason, l_period_causes, l_cause_union = (
            l_results[scenario]
        )
        cells.append(
            PQv2Cell(
                cell=f"L-{scenario}",
                scenario=scenario,
                estimand=ESTIMAND_L,
                value=l_value,
                t_stat=l_t,
                n_obs=int(l_n),
                status=l_status,
                reason=l_reason,
                n_index=len(identities),
                support=int(l_n),
                estimator=ESTIMATOR_IDENTITY,
                period_identities=identities,
                period_statuses=statuses,
                period_values=values_arr,
                period_reasons=reasons_arr,
                declaration_hash=declaration.declaration_hash,
                period_causes=l_period_causes,
                cause_union=l_cause_union,
            )
        )

    periods = tuple(
        _period_record(
            period,
            labels_by_period.get(period.period, {}),
            _sigma_ok(assignment_by_period.get(period.period)),
        )
        for period in envelope.periods
    )
    support = tuple(
        (cell.cell, int(cell.support)) for cell in cells
    )
    assignment_rule = envelope.rule
    relaxations = _relaxations(str(certified), assignment_by_period)

    return PQv2Diagnostics(
        not_assessed=None,
        declaration_hash=declaration.declaration_hash,
        dataset_identity_hash=identity_hash,
        overlay_pq_hash=overlay.pq_hash,
        methodology_sha256=declaration.methodology_sha256,
        cs_id=declaration.cs_id,
        ed_id=declaration.ed_id,
        ce_id=declaration.ce_id,
        pqv2_code_sha256=_core.pqv2_code_sha256(repo_root),
        primary_metric=declaration.base.primary_metric.value,
        metric_basis=metric_basis,
        basis=declaration.base.basis,
        sign=int(factor_spec.sign),
        orientation_rule=_orientation_rule(
            int(factor_spec.sign), declaration.base.primary_metric
        ),
        screen_id=declaration.base.screen_id,
        screen_policy_hash=declaration.base.screen_policy_hash,
        certified_class=str(certified),
        certified_hypothesis=getattr(certified, "hypothesis", None),
        assignment_rule=assignment_rule,
        relaxations=relaxations,
        factor_panel_sha256=declaration.base.factor_panel_sha256,
        index=formations,
        cells=tuple(cells),
        periods=periods,
        b_periods_t0=b_periods_t0,
        b_periods_ts=b_periods_ts,
        l_periods_t0=b_periods_t0,
        l_periods_ts=b_periods_ts,
        estimator_identity=ESTIMATOR_IDENTITY,
        cost_bps=float(declaration.base.transaction_cost_bps),
        cost_rate=float(declaration.base.transaction_cost_bps) / 1.0e4,
        n_groups=int(declaration.base.n_groups),
        q=float(declaration.base.winsorization),
        support=support,
        non_claims=NON_CLAIMS,
        b_diagnostics_hash=diag_b.diagnostics_hash,
        b_diagnostics=diag_b,
    )


# ---------------------------------------------------------------------------
# Input plumbing helpers
# ---------------------------------------------------------------------------


def _sessions_from_inputs(inputs: Mapping[str, Any]) -> tuple[date, ...]:
    from smart_beta.dataset_v1 import trusted_inputs as _d7c

    trusted = inputs.get(_d7c.INPUT_DAILY_TOTAL_RETURN)
    if trusted is None:
        raise _core.PQv2InputError(
            "inputs does not carry the daily_total_return TrustedInput"
        )
    values = trusted.values
    sessions = []
    for label in values.index:
        parsed = _d7b._as_date(label)
        if parsed is not None:
            sessions.append(parsed)
    return tuple(sorted(set(sessions)))


def _dv_frame(
    inputs: Mapping[str, Any], overlay: _pq.Overlay
) -> pd.DataFrame | None:
    from smart_beta.dataset_v1 import trusted_inputs as _d7c

    semantic = getattr(_d7c, "INPUT_DOLLAR_VOLUME", "dollar_volume")
    trusted = inputs.get(semantic)
    if trusted is None:
        return None
    values = trusted.values
    return values.mask(overlay.mask(semantic, values))


def _factor_lookup(factor_panel: pd.DataFrame) -> dict[tuple[date, str], float]:
    out: dict[tuple[date, str], float] = {}
    for row in factor_panel.itertuples(index=False):
        parsed = _d7b._as_date(row.date)
        if parsed is None:
            continue
        out[(parsed, str(row.stock_id))] = float(row.value)
    return out


def _scenario_lookup(panel: pd.DataFrame) -> dict[tuple[date, str], float]:
    from smart_beta.evaluation import forward_returns as _fr

    out: dict[tuple[date, str], float] = {}
    if panel is None or len(panel) == 0:
        return out
    column = _fr.FORWARD_RETURN_COL
    for row in panel.itertuples(index=False):
        parsed = _d7b._as_date(row.date)
        if parsed is None:
            continue
        value = getattr(row, column)
        if value is None:
            continue
        out[(parsed, str(row.stock_id))] = float(value)
    return out


def _cross_section_keys(
    m_by_formation: Mapping[date, pd.DataFrame],
    v_by_formation: Mapping[date, pd.DataFrame],
    formation: date,
) -> tuple[str, ...]:
    keys: set[str] = set()
    m_row = m_by_formation.get(formation)
    if m_row is not None:
        for key, value in m_row.items():
            if _is_finite(value):
                keys.add(str(key))
    v_row = v_by_formation.get(formation)
    if v_row is not None:
        for key, value in v_row.items():
            if _is_finite(value):
                keys.add(str(key))
    return tuple(sorted(keys))


def _period_record(
    period: _envelope.EnvelopePeriod,
    labels: Mapping[str, _labels.LabelEnvelope],
    sigma_ok: bool | None,
) -> PQv2Period:
    positions: dict[str, int] = {
        _labels.FORMATION_ENDPOINT: 0,
        _labels.INTERIOR: 0,
        _labels.ENDING_ENDPOINT: 0,
    }
    n_misdated = 0
    for label in labels.values():
        for name, count in label.positions().items():
            positions[name] = positions.get(name, 0) + int(count)
        n_misdated += int(getattr(label, "n_misdated", 0))
    return PQv2Period(
        period=period.period,
        rule=period.rule,
        winsor_ok=period.winsor_ok,
        D_t=period.D_t,
        TO_b=period.TO_b,
        TO_hi=period.TO_hi,
        changed_b=period.changed_b,
        union=period.union,
        LS_b_t0=period.LS_b_t0,
        LS_b_ts=period.LS_b_ts,
        L_t0=period.L_t0,
        L_ts=period.L_ts,
        status_t0=period.status_t0,
        status_ts=period.status_ts,
        reason_t0=period.reason_t0,
        reason_ts=period.reason_ts,
        exposure=dict(period.exposure),
        label_counts=dict(period.label_counts),
        n_unknown_reserve=period.n_unknown_reserve,
        sigma_ok=sigma_ok,
        position_counts=positions,
        n_misdated=n_misdated,
        causes_t0=tuple(period.causes_t0),
        causes_ts=tuple(period.causes_ts),
        n_unknown_source=int(period.n_unknown_source),
    )


def _sigma_ok(assignment: Any) -> bool | None:
    """The M§11.2 σ-support outcome for the period, or ``None`` for SIMPLE."""

    if isinstance(assignment, _core.NotAssessed):
        if assignment.reason == _core.NotAssessedReason.SIGMA_SUPPORT:
            return False
        return None
    if isinstance(assignment, _sandwich.SandwichAssignment):
        return True
    return None


def _relaxations(
    certified: str, assignment_by_period: Mapping[date, Any]
) -> tuple[str, ...]:
    for assignment in assignment_by_period.values():
        if isinstance(assignment, _core.NotAssessed):
            continue
        relaxations = getattr(assignment, "relaxations", None)
        if relaxations:
            return tuple(str(item) for item in relaxations)
        return ()
    if certified == _dependency.STD_X_RANK:
        return ()
    return ()


# ---------------------------------------------------------------------------
# Classification (M§14)
# ---------------------------------------------------------------------------


def _classify_scenario(
    b_passed: bool | None,
    l_passed: bool | None,
    b_available: bool,
    l_available: bool,
) -> tuple[str, str | None, bool]:
    if not b_available or not l_available:
        return (
            _core.ClassificationStatus.NOT_ASSESSED,
            _core.NotAssessedReason.B_STATISTIC_UNAVAILABLE
            if not b_available
            else _core.NotAssessedReason.L_STATISTIC_UNAVAILABLE,
            False,
        )
    if b_passed is None or l_passed is None:
        return _core.ClassificationStatus.NOT_ASSESSED, None, False
    if b_passed and l_passed:
        return _core.ClassificationStatus.CORRECTION_SENSITIVITY_SCREEN_PASS, None, True
    if b_passed and not l_passed:
        return _core.ClassificationStatus.QUARANTINE_SENSITIVE, None, False
    if not b_passed and l_passed:
        return _core.ClassificationStatus.INVARIANT_VIOLATION_FAIL_CLOSED, None, False
    return _core.ClassificationStatus.DEVELOPMENT_SCREEN_FAIL, None, False


def classify_pqv2(
    diagnostics: PQv2Diagnostics,
    screens: Sequence[PQv2ScreenOutcome],
) -> PQv2Record:
    """Apply the M§14 table per scenario; ``INVARIANT_VIOLATION_FAIL_CLOSED``
    is a typed status and never raises."""

    by_cell = {screen.cell: screen for screen in screens}
    statuses: list[str] = []
    reasons: list[str | None] = []
    b_screens: list[bool | None] = []
    l_screens: list[bool | None] = []
    advancement: list[bool] = []
    for scenario in _SCENARIOS:
        b_cell = diagnostics.cell(f"B-{scenario}")
        l_cell = diagnostics.cell(f"L-{scenario}")
        b_screen = by_cell.get(f"B-{scenario}")
        l_screen = by_cell.get(f"L-{scenario}")
        b_passed = None if b_screen is None else b_screen.passed
        l_passed = None if l_screen is None else l_screen.passed
        b_available = (
            b_cell is not None
            and b_cell.status is None
            and b_cell.value is not None
            and b_cell.t_stat is not None
        )
        l_available = (
            l_cell is not None
            and l_cell.status is None
            and l_cell.value is not None
            and l_cell.t_stat is not None
        )
        status, reason, permitted = _classify_scenario(
            b_passed, l_passed, b_available, l_available
        )
        statuses.append(status)
        reasons.append(reason)
        b_screens.append(b_passed)
        l_screens.append(l_passed)
        advancement.append(permitted)

    return PQv2Record(
        declaration_hash=diagnostics.declaration_hash,
        diagnostics_hash=diagnostics.diagnostics_hash,
        screen_id=diagnostics.screen_id,
        screen_policy_hash=diagnostics.screen_policy_hash,
        status=tuple(statuses),
        not_assessed_reason=tuple(reasons),
        b_screen=tuple(b_screens),
        l_screen=tuple(l_screens),
        advancement_permitted=tuple(advancement),
        accept=False,
        confirmation=False,
        scenario=_SCENARIOS,
        certified_class=diagnostics.certified_class,
        assignment_rule=diagnostics.assignment_rule,
        non_claims=diagnostics.non_claims,
    )
