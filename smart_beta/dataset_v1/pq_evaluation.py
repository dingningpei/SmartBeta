"""PQB — P-Q dual-estimand (A/B x T0/TS) development evaluation bridge.

Dataset v1.2 adds two research-layer estimands over the same declared
development fold, FactorSpec and factor panel:

* **B = ORIGINAL** — exactly D7b, computed by the unmodified
  :mod:`smart_beta.dataset_v1.delisting_bridge` code;
* **A = QUARANTINED** — B masked at portfolio-period and IC-period
  granularity using the PQA quarantine overlay.

This module evaluates one declared fold under both estimands and the two
delisting scenarios, classifies ``QUARANTINE_SENSITIVE`` and advancement, and
returns an in-memory, hash-bound, ``to_dict``-serialisable record.  It builds
no ``EvaluationRecord`` or ``DecisionRecord`` and calls no Phase-8/9/10 writer.

Authority (frozen), in precedence order, is ``worker_tasks/dataset_v1/
task-pqb-dual-estimator.md`` (PQB), the P-Q rules Q5-Q13 and amendment,
the D7b clarification, and the frozen PQA/PQC APIs.  B is never recomputed
from anything but the unmodified D7b helpers; A is never produced by any
other route than masking B's per-period series.

Every test here is offline and synthetic: provider/network calls = 0,
reserve reads = 0, final-holdout consumption = 0, Knowledge-PIT appends = 0.
"""

from __future__ import annotations

import hashlib
import math
import struct
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from smart_beta.dataset_v1 import delisting_bridge as _d7b
from smart_beta.dataset_v1 import quarantine as _pqa
from smart_beta.engines.gap_hac import GapAwareHACResult, gap_aware_mean_tstat
from smart_beta.evaluation import metrics as _metrics
from smart_beta.evaluation import spec as _spec
from smart_beta.spec.factor_spec import FactorSpec

__all__ = [
    # constants
    "DATASET_ID",
    "AMENDMENT_SHA256",
    "QUARANTINE_RULE_HASH",
    "ESTIMAND_QUARANTINED",
    "ESTIMAND_ORIGINAL",
    "ESTIMANDS",
    "SCENARIO_T0",
    "SCENARIO_TS",
    "KAPPA",
    "SCOPE",
    "ADMITTED_KEYS",
    # statuses
    "STATUS_QUARANTINE_SENSITIVE",
    "STATUS_NOT_SENSITIVE",
    "STATUS_NOT_ASSESSED",
    "DELISTING_NOT_SENSITIVE",
    "DELISTING_SENSITIVE",
    "DELISTING_NOT_ASSESSED",
    "PQ_DECLARATION_INVALID",
    "DATASET_IDENTITY_MISMATCH",
    "QUARANTINE_RULE_HASH_MISMATCH",
    "OVERLAY_MISMATCH",
    "FACTOR_PANEL_MISMATCH",
    "B_RECONCILIATION_FAILED",
    "A_STATISTIC_NOT_ASSESSED",
    "A_SCREEN_NOT_ASSESSED",
    "SCREEN_MISSING",
    "SCREEN_MISMATCH",
    "PATH_GAP",
    "NO_BENCHMARK_AUTHORITY",
    "UNSUPPORTED_ORIENTATION",
    # errors
    "PQEvaluationError",
    "PQDeclarationError",
    "PQBindingError",
    "PQReconciliationError",
    # dataclasses
    "PQEvaluationDeclaration",
    "PQCell",
    "PQDiagnostics",
    "PQScreenOutcome",
    "PQDevelopmentSensitivityRecord",
    # api
    "build_pq_diagnostics",
    "classify_pq",
    "mask_series",
    "complement_series",
    "interior_gaps",
    "b_series_on_index",
    "verify_pinned_code",
]

# ---------------------------------------------------------------------------
# Frozen constants
# ---------------------------------------------------------------------------

DATASET_ID = "discovery_dataset_v1_2"
AMENDMENT_SHA256 = _pqa.AMENDMENT_SHA256
QUARANTINE_RULE_HASH = _pqa.QUARANTINE_RULE_HASH

ESTIMAND_QUARANTINED = "QUARANTINED"
ESTIMAND_ORIGINAL = "ORIGINAL"
ESTIMANDS: tuple[str, str] = (ESTIMAND_QUARANTINED, ESTIMAND_ORIGINAL)

SCENARIO_T0 = "T0"
SCENARIO_TS = "TS"
_CELL_NAMES = ("B-T0", "B-TS", "A-T0", "A-TS")

KAPPA = 2
SCOPE = "DEVELOPMENT_ONLY"

STATUS_QUARANTINE_SENSITIVE = "QUARANTINE_SENSITIVE"
STATUS_NOT_SENSITIVE = "NOT_SENSITIVE"
STATUS_NOT_ASSESSED = "NOT_ASSESSED"

DELISTING_NOT_SENSITIVE = _d7b.STATUS_NOT_SENSITIVE
DELISTING_SENSITIVE = _d7b.STATUS_DELISTING_SENSITIVE
DELISTING_NOT_ASSESSED = _d7b.STATUS_NOT_ASSESSED

#: A fail-closed reasons.
PQ_DECLARATION_INVALID = "PQ_DECLARATION_INVALID"
DATASET_IDENTITY_MISMATCH = "DATASET_IDENTITY_MISMATCH"
QUARANTINE_RULE_HASH_MISMATCH = "QUARANTINE_RULE_HASH_MISMATCH"
OVERLAY_MISMATCH = "OVERLAY_MISMATCH"
FACTOR_PANEL_MISMATCH = "FACTOR_PANEL_MISMATCH"
B_RECONCILIATION_FAILED = "B_RECONCILIATION_FAILED"
A_STATISTIC_NOT_ASSESSED = "A_STATISTIC_NOT_ASSESSED"
A_SCREEN_NOT_ASSESSED = "A_SCREEN_NOT_ASSESSED"
SCREEN_MISSING = "SCREEN_MISSING"
SCREEN_MISMATCH = "SCREEN_MISMATCH"
PATH_GAP = "PATH_GAP"
NO_BENCHMARK_AUTHORITY = _d7b.NOT_ASSESSED_NO_BENCHMARK_AUTHORITY
UNSUPPORTED_ORIENTATION = _d7b.NOT_ASSESSED_UNSUPPORTED_ORIENTATION

_NOT_ASSESSED_PREFIX = "NOT_ASSESSED"

#: The D7b-admitted primary keys that the sign -1 orientation may negate.
ADMITTED_KEYS: tuple[_spec.MetricKey, ...] = _d7b.SIGN_MINUS_ONE_ADMITTED

_HAC_METRICS = (
    _spec.MetricKey.IC,
    _spec.MetricKey.RANK_IC,
    _spec.MetricKey.LONG_SHORT,
    _spec.MetricKey.TURNOVER_COST_ADJUSTED,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


#: Pinned read-only code (fail closed on mismatch).
PINNED_CODE: dict[str, str] = {
    "smart_beta/dataset_v1/delisting_bridge.py": (
        "125893bec3eb9db61d6678aaabb0d9e16bce5b03a576eb3a718d18c20aaf2efc"
    ),
    "smart_beta/evaluation/metrics.py": (
        "747282a2ee7cc7b63d0511d29ab3bdf8c3c9aceea9fb965dd64de543d76831a8"
    ),
    "smart_beta/evaluation/portfolio.py": (
        "5dea8adc581ae1bfe2bc7b771db37f36a71c2331cf702d9c8b7174039c3ed2dc"
    ),
    "smart_beta/evaluation/engine.py": (
        "4ef0caa7d453a27a35c23fc3c7d7ece4b17c132cff651efa6dd79cb2a39d31c8"
    ),
    "smart_beta/evaluation/forward_returns.py": (
        "e7d261a4096ac84f9ea3a522d82e3bf48fd84c3111894c28bb94bf5beebd45de"
    ),
    "smart_beta/evaluation/partition.py": (
        "36bb418b6c5662c671c6a869ec9b015df75e47a994395a530addd0b53783be72"
    ),
    "smart_beta/evaluation/spec.py": (
        "a0e151356a3435c2a3ce98bb4d4cfce25a78111c561419aefb2c1cb77f2ae4de"
    ),
    "smart_beta/engines/portfolio_sort.py": (
        "d9776fdca3186795b87b3825f2c43b77e1ed79c66bf4ec4e05cb2a00f2178b57"
    ),
    "smart_beta/spec/factor_spec.py": (
        "ab6c65d312d6fe1e421c489e6ad8045a50e265c8f73dbe6a07738e1bf5458c56"
    ),
    # The two merged task modules this bridge depends on, pinned to their
    # merged commits' bytes.
    "smart_beta/dataset_v1/quarantine.py": (
        "490eb0a3be0a0ddeebff81ad27ac422c1d7a86c9cc63269d8fb8935aaddc391d"
    ),
    "smart_beta/engines/gap_hac.py": (
        "d0dd6298aabfeb82e0ce7d7ed62937616cc47e5a40e4a2e38bfea0ea9ee85424"
    ),
}


# ---------------------------------------------------------------------------
# Typed errors
# ---------------------------------------------------------------------------


class PQEvaluationError(Exception):
    """Base class for every PQB fail-closed error."""


class PQDeclarationError(PQEvaluationError, ValueError):
    """The declaration is malformed or carries a forbidden per-estimand field."""


class PQBindingError(PQEvaluationError, ValueError):
    """A pinned code hash or identity binding does not match."""


class PQReconciliationError(PQEvaluationError):
    """The recomputed B statistics disagree with the frozen D7b diagnostics."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _bits(value: float) -> bytes:
    return struct.pack("d", float(value))


def _same_float(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return left is None and right is None
    if math.isnan(left) and math.isnan(right):
        return True
    return _bits(left) == _bits(right)


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def _finite_or_none(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _sgn(value: float | None) -> int | None:
    if value is None or not math.isfinite(value):
        return None
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _mask_hash(mask: np.ndarray) -> str:
    packed = np.packbits(np.asarray(mask, dtype=bool)).tobytes()
    return hashlib.sha256(struct.pack("<I", int(mask.size)) + packed).hexdigest()


def _tag(status: str) -> str:
    """The reporting spelling ``NOT_ASSESSED(<status>)``."""
    return f"{_NOT_ASSESSED_PREFIX}({status})"


# ---------------------------------------------------------------------------
# Pinned-code verification
# ---------------------------------------------------------------------------


def verify_pinned_code(repo_root: Path | str | None = None) -> dict[str, str]:
    """Verify the frozen read-only code hashes; fail closed on mismatch."""
    root = Path(repo_root) if repo_root is not None else _REPO_ROOT
    verified: dict[str, str] = {}
    for relative, expected in sorted(PINNED_CODE.items()):
        path = root / relative
        try:
            actual = _sha256_file(path)
        except OSError as exc:
            raise PQBindingError(f"cannot read pinned code {relative}: {exc}") from exc
        if actual != expected:
            raise PQBindingError(
                f"pinned code {relative} hash {actual} != bound {expected}"
            )
        verified[relative] = actual
    return verified


# ---------------------------------------------------------------------------
# Declaration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PQEvaluationDeclaration:
    """The one hash-bound declaration serving both estimands.

    ``base`` is the D7b :class:`CampaignEvaluationDeclaration`; it is the
    **only** place evaluation parameters live.  There is no per-estimand
    parameter, override or second base declaration.
    """

    base: _d7b.CampaignEvaluationDeclaration
    dataset_id: str
    dataset_identity_hash: str
    amendment_sha256: str
    quarantine_rule_hash: str
    pq_id: str
    pq_hash: str
    estimands: tuple[str, str] = ESTIMANDS

    def __post_init__(self) -> None:
        if not isinstance(self.base, _d7b.CampaignEvaluationDeclaration):
            raise PQDeclarationError("base must be a CampaignEvaluationDeclaration")
        if tuple(self.estimands) != ESTIMANDS:
            raise PQDeclarationError(
                f"estimands must be exactly {ESTIMANDS}, got {self.estimands!r}; "
                "there is no per-estimand parameter override"
            )
        for attribute in (
            "dataset_id",
            "dataset_identity_hash",
            "amendment_sha256",
            "quarantine_rule_hash",
            "pq_id",
            "pq_hash",
        ):
            value = getattr(self, attribute)
            if not isinstance(value, str) or not value:
                raise PQDeclarationError(f"{attribute} must be a non-empty string")

    def to_dict(self) -> dict[str, Any]:
        return {
            "base": self.base.declaration_hash,
            "dataset_id": self.dataset_id,
            "dataset_identity_hash": self.dataset_identity_hash,
            "amendment_sha256": self.amendment_sha256,
            "quarantine_rule_hash": self.quarantine_rule_hash,
            "pq_id": self.pq_id,
            "pq_hash": self.pq_hash,
            "estimands": list(self.estimands),
        }

    @property
    def declaration_hash(self) -> str:
        return _d7b.content_hash(self.to_dict())


# ---------------------------------------------------------------------------
# Cell / diagnostics / screen / record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PQCell:
    """One (estimand, scenario) cell with its content hash."""

    cell: str
    scenario: str
    estimand: str
    value: float | None
    raw_value: float | None
    t_stat: float | None
    status: str | None
    n_obs: int
    n_index: int
    availability_mask_hash: str
    declaration_hash: str

    def _body(self) -> dict[str, Any]:
        return {
            "cell": self.cell,
            "scenario": self.scenario,
            "estimand": self.estimand,
            "value": self.value,
            "raw_value": self.raw_value,
            "t_stat": self.t_stat,
            "status": self.status,
            "n_obs": int(self.n_obs),
            "n_index": int(self.n_index),
            "availability_mask_hash": self.availability_mask_hash,
            "declaration_hash": self.declaration_hash,
        }

    def to_dict(self) -> dict[str, Any]:
        body = self._body()
        body["cell_hash"] = self.cell_hash
        return body

    @property
    def cell_hash(self) -> str:
        return _d7b.content_hash(self._body())


@dataclass(frozen=True)
class PQDiagnostics:
    """In-memory P-Q diagnostics for one declared development fold."""

    not_assessed: str | None
    declaration_hash: str
    primary_metric: str
    basis: str
    sign: int
    orientation_rule: str
    screen_id: str
    screen_policy_hash: str
    index: tuple[date, ...]
    cells: tuple[PQCell, ...]
    b_diagnostics_hash: str | None
    e_s: float | None
    b: float | None
    q_b: float | None
    q_s: float | None
    q_l: float | None
    n_flagged_formations: int
    residual_cs_cells: int
    liquidity_exposed: int
    n_interior_b_missing: int
    support: tuple[tuple[str, int], ...]
    cs3: tuple[tuple[str, float | None], ...]
    cs3_status: str | None
    b_diagnostics: Any = field(default=None, compare=False, repr=False)

    def cell(self, name: str) -> PQCell | None:
        for item in self.cells:
            if item.cell == name:
                return item
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "not_assessed": self.not_assessed,
            "declaration_hash": self.declaration_hash,
            "primary_metric": self.primary_metric,
            "basis": self.basis,
            "sign": int(self.sign),
            "orientation_rule": self.orientation_rule,
            "screen_id": self.screen_id,
            "screen_policy_hash": self.screen_policy_hash,
            "index": list(self.index),
            "cells": [cell.to_dict() for cell in self.cells],
            "b_diagnostics_hash": self.b_diagnostics_hash,
            "e_s": self.e_s,
            "b": self.b,
            "q_b": self.q_b,
            "q_s": self.q_s,
            "q_l": self.q_l,
            "n_flagged_formations": int(self.n_flagged_formations),
            "residual_cs_cells": int(self.residual_cs_cells),
            "liquidity_exposed": int(self.liquidity_exposed),
            "n_interior_b_missing": int(self.n_interior_b_missing),
            "support": list(self.support),
            "cs3": [{"name": name, "value": value} for name, value in self.cs3],
            "cs3_status": self.cs3_status,
        }

    @property
    def diagnostics_hash(self) -> str:
        return _d7b.content_hash(self.to_dict())


@dataclass(frozen=True)
class PQScreenOutcome:
    """One hash-bound Campaign/Phase-8 screen verdict for a cell."""

    cell: str
    cell_hash: str
    screen_id: str
    screen_policy_hash: str
    declaration_hash: str
    passed: bool | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "cell": self.cell,
            "cell_hash": self.cell_hash,
            "screen_id": self.screen_id,
            "screen_policy_hash": self.screen_policy_hash,
            "declaration_hash": self.declaration_hash,
            "passed": None if self.passed is None else bool(self.passed),
        }


@dataclass(frozen=True)
class PQDevelopmentSensitivityRecord:
    """The P-Q development sensitivity classification (no verdict)."""

    declaration_hash: str
    diagnostics_hash: str
    screen_id: str
    screen_policy_hash: str
    status: str
    not_assessed_reason: str | None
    quarantine_sensitive: bool
    clause_a: bool
    clause_b: bool
    clause_c: bool
    clause_a_t0: bool
    clause_a_ts: bool
    clause_b_t0: bool
    clause_b_ts: bool
    delisting_a: str
    delisting_b: str
    economic_a_t0: float | None
    economic_a_ts: float | None
    economic_b_t0: float | None
    economic_b_ts: float | None
    e_s: float | None
    b: float | None
    q_b: float | None
    q_s: float | None
    q_l: float | None
    advancement_blocked: bool
    holdout_authorized: bool
    final_holdout_consumed: bool
    confirmation_evidence: bool
    scope: str
    cell_hashes: tuple[tuple[str, str], ...] = ()
    delisting_return_status: str = _d7b.DELISTING_RETURN_NOT_CERTIFIED

    def to_dict(self) -> dict[str, Any]:
        return {
            "declaration_hash": self.declaration_hash,
            "diagnostics_hash": self.diagnostics_hash,
            "screen_id": self.screen_id,
            "screen_policy_hash": self.screen_policy_hash,
            "status": self.status,
            "not_assessed_reason": self.not_assessed_reason,
            "quarantine_sensitive": bool(self.quarantine_sensitive),
            "clause_a": bool(self.clause_a),
            "clause_b": bool(self.clause_b),
            "clause_c": bool(self.clause_c),
            "clause_a_t0": bool(self.clause_a_t0),
            "clause_a_ts": bool(self.clause_a_ts),
            "clause_b_t0": bool(self.clause_b_t0),
            "clause_b_ts": bool(self.clause_b_ts),
            "delisting_a": self.delisting_a,
            "delisting_b": self.delisting_b,
            "economic_a_t0": self.economic_a_t0,
            "economic_a_ts": self.economic_a_ts,
            "economic_b_t0": self.economic_b_t0,
            "economic_b_ts": self.economic_b_ts,
            "e_s": self.e_s,
            "b": self.b,
            "q_b": self.q_b,
            "q_s": self.q_s,
            "q_l": self.q_l,
            "advancement_blocked": bool(self.advancement_blocked),
            "holdout_authorized": bool(self.holdout_authorized),
            "final_holdout_consumed": bool(self.final_holdout_consumed),
            "confirmation_evidence": bool(self.confirmation_evidence),
            "scope": self.scope,
            "cell_hashes": [
                {"cell": cell, "cell_hash": digest}
                for cell, digest in self.cell_hashes
            ],
            "delisting_return_status": self.delisting_return_status,
        }

    @property
    def record_hash(self) -> str:
        return _d7b.content_hash(self.to_dict())


# ---------------------------------------------------------------------------
# Pure series helpers
# ---------------------------------------------------------------------------


def mask_series(b_series: np.ndarray, available: np.ndarray) -> np.ndarray:
    """A[i] = B[i] where ``available[i]``, else NaN (never any other value)."""
    b = np.asarray(b_series, dtype="float64")
    avail = np.asarray(available, dtype=bool)
    if b.shape != avail.shape:
        raise PQEvaluationError("availability mask must match the series shape")
    out = np.full(b.shape, float("nan"), dtype="float64")
    np.copyto(out, b, where=avail)
    return out


def complement_series(b_series: np.ndarray, a_series: np.ndarray) -> np.ndarray:
    """C[i] = B[i] where A is unavailable and B finite, else NaN."""
    b = np.asarray(b_series, dtype="float64")
    a = np.asarray(a_series, dtype="float64")
    include = ~np.isfinite(a) & np.isfinite(b)
    out = np.full(b.shape, float("nan"), dtype="float64")
    np.copyto(out, b, where=include)
    return out


def interior_gaps(series: np.ndarray) -> int:
    """Non-finite positions strictly between the first and last finite ones."""
    arr = np.asarray(series, dtype="float64")
    finite = np.isfinite(arr)
    positions = np.flatnonzero(finite)
    if positions.size == 0:
        return 0
    first = int(positions[0])
    last = int(positions[-1])
    return (last - first + 1) - int(positions.size)


def b_series_on_index(values: pd.Series, index: Sequence[date]) -> np.ndarray:
    """Map a formation-indexed B series onto the original index (NaN elsewhere)."""
    lookup: dict[date, float] = {}
    for key, value in values.items():
        parsed = _d7b._as_date(key)
        if parsed is not None:
            lookup[parsed] = float(value)
    return np.array(
        [lookup.get(formation, float("nan")) for formation in index],
        dtype="float64",
    )


# ---------------------------------------------------------------------------
# Internal B reconstruction
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
    declaration: PQEvaluationDeclaration,
    factor_spec: FactorSpec,
    factor_panel: pd.DataFrame,
    partition: Any,
    bindings: _d7b.Bindings,
) -> tuple[tuple[_ScenarioB, _ScenarioB], Mapping[tuple[date, str], float]]:
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
    return (scenarios[0], scenarios[1]), groups


def _reconcile_b(
    scenarios: tuple[_ScenarioB, _ScenarioB],
    diagnostics: _d7b.DevelopmentDiagnostics,
    sign: int,
) -> None:
    """Recomputed B primary value/t/n_obs must equal diag_B bit-for-bit.

    ``diag_B.value_t0`` is the raw pre-orientation value; ``diag_B.t_t0`` is
    the D7b-oriented t (negated for sign -1).  The recomputed scenario t is
    raw, so it is oriented the same way before comparison.
    """
    expected = ((diagnostics.value_t0, diagnostics.t_t0, diagnostics.n_obs_t0),
                (diagnostics.value_ts, diagnostics.t_ts, diagnostics.n_obs_ts))
    for scenario_b, (value, t_stat, n_obs) in zip(scenarios, expected):
        if not _same_float(scenario_b.value, value):
            raise PQReconciliationError(
                f"B value {scenario_b.value!r} != diag_B {value!r}"
            )
        oriented_t = scenario_b.t_stat
        if sign == -1 and oriented_t is not None:
            oriented_t = -oriented_t
        if not _same_float(oriented_t, t_stat):
            raise PQReconciliationError(
                f"B t {oriented_t!r} != diag_B {t_stat!r}"
            )
        if int(scenario_b.n_obs) != int(n_obs):
            raise PQReconciliationError(
                f"B n_obs {scenario_b.n_obs!r} != diag_B {n_obs!r}"
            )


# ---------------------------------------------------------------------------
# Member flags and availability
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Availability:
    formations: tuple[date, ...]
    gross: dict[date, bool]
    net: dict[date, bool]
    ic: dict[date, bool]
    q_b: float | None
    q_s: float | None
    q_l: float | None
    flagged_formations: int


def _members_by_formation(
    groups: Mapping[tuple[date, str], float],
) -> dict[date, dict[str, int]]:
    by: dict[date, dict[str, int]] = {}
    for (formation, key), group in groups.items():
        if group is None or (isinstance(group, float) and math.isnan(group)):
            continue
        by.setdefault(formation, {})[str(key)] = int(group)
    return by


def _flag_maps(
    states: pd.DataFrame,
    label_states: pd.DataFrame,
    horizon: int,
) -> tuple[set[tuple[date, str]], set[tuple[date, str]]]:
    input_flag: set[tuple[date, str]] = set()
    for row in states.itertuples(index=False):
        if int(row.state) == _pqa.STATE_PROPAGATED:
            input_flag.add((_d7b._as_date(row.date), str(row.security_key)))
    label_flag: set[tuple[date, str]] = set()
    if label_states is not None and len(label_states):
        for row in label_states.itertuples(index=False):
            if int(row.h) != horizon:
                continue
            if int(row.state) == _pqa.STATE_PROPAGATED:
                label_flag.add(
                    (_d7b._as_date(row.formation_date), str(row.security_key))
                )
    return input_flag, label_flag


def _compute_availability(
    groups: Mapping[tuple[date, str], float],
    input_flag: set[tuple[date, str]],
    label_flag: set[tuple[date, str]],
    n_groups: int,
    sign: int,
) -> _Availability:
    by = _members_by_formation(groups)
    formations = tuple(sorted(by))
    gross: dict[date, bool] = {}
    net: dict[date, bool] = {}
    ic: dict[date, bool] = {}
    q_b_ratios: list[float] = []
    q_s_ratios: list[float] = []
    q_l_ratios: list[float] = []
    flagged_formations = 0
    previous_members: set[str] = set()
    for formation in formations:
        members = by[formation]
        all_members = set(members)
        any_flag = lambda keys: any(
            (formation, key) in input_flag or (formation, key) in label_flag
            for key in keys
        )
        long_group = n_groups if sign == 1 else 1
        short_group = 1 if sign == 1 else n_groups
        long_members = [key for key, group in members.items() if group == long_group]
        short_members = [key for key, group in members.items() if group == short_group]
        long_available = not any_flag(long_members)
        short_available = not any_flag(short_members)
        gross[formation] = long_available and short_available
        composition_members = all_members | previous_members
        composition_clean = not any(
            (formation, key) in input_flag for key in composition_members
        )
        net[formation] = gross[formation] and composition_clean
        ic[formation] = not any_flag(all_members)
        flagged = sum(
            1
            for key in all_members
            if (formation, key) in input_flag or (formation, key) in label_flag
        )
        if all_members:
            q_b_ratios.append(flagged / len(all_members))
        flagged_long = sum(
            1 for key in long_members if (formation, key) in input_flag or (formation, key) in label_flag
        )
        flagged_short = sum(
            1 for key in short_members if (formation, key) in input_flag or (formation, key) in label_flag
        )
        if short_members:
            q_s_ratios.append(flagged_short / len(short_members))
        if long_members:
            q_l_ratios.append(flagged_long / len(long_members))
        if any((formation, key) in input_flag for key in all_members):
            flagged_formations += 1
        previous_members = all_members
    return _Availability(
        formations=formations,
        gross=gross,
        net=net,
        ic=ic,
        q_b=_mean_or_none(q_b_ratios),
        q_s=_mean_or_none(q_s_ratios),
        q_l=_mean_or_none(q_l_ratios),
        flagged_formations=flagged_formations,
    )


def _mean_or_none(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return float(math.fsum(values) / len(values))


# ---------------------------------------------------------------------------
# A-statistics
# ---------------------------------------------------------------------------


@dataclass
class _AStat:
    value: float | None
    raw_value: float | None
    t_stat: float | None
    status: str | None
    n_obs: int
    mask: np.ndarray


def _estimate_a(
    b_series: np.ndarray,
    available: np.ndarray,
    metric: _spec.MetricKey,
    *,
    sign: int,
    periods_per_year: int,
    lags: int | None,
) -> _AStat:
    a = mask_series(b_series, available)
    mask = np.asarray(available, dtype=bool) & np.isfinite(b_series)
    if metric in _HAC_METRICS:
        result: GapAwareHACResult = gap_aware_mean_tstat(a, lags=lags)
        value = _finite_or_none(result.mean)
        if result.status != "OK":
            if sign == -1 and value is not None:
                return _AStat(
                    -value, _finite_or_none(result.mean), None, result.status,
                    int(result.n_obs), mask,
                )
            return _AStat(
                value, _finite_or_none(result.mean), None, result.status,
                int(result.n_obs), mask,
            )
        t_stat = float(result.t_stat)
        if sign == -1:
            if value is not None:
                value = -value
            t_stat = -t_stat
        return _AStat(value, float(result.mean), t_stat, None, int(result.n_obs), mask)
    if metric is _spec.MetricKey.SHARPE:
        if sign == -1 and metric not in ADMITTED_KEYS:
            return _AStat(None, None, None, UNSUPPORTED_ORIENTATION, 0, mask)
        result = _metrics.sharpe_ratio(a, periods_per_year=periods_per_year)
        value = _finite_or_none(result.value)
        if value is None:
            return _AStat(None, None, None, "NONFINITE", int(result.n_obs), mask)
        raw = value
        if sign == -1:
            value = -value
        return _AStat(value, raw, None, None, int(result.n_obs), mask)
    if metric is _spec.MetricKey.MAX_DRAWDOWN:
        if sign == -1 and metric not in ADMITTED_KEYS:
            return _AStat(None, None, None, UNSUPPORTED_ORIENTATION, 0, mask)
        if interior_gaps(a) > 0:
            return _AStat(None, None, None, PATH_GAP, int(np.isfinite(a).sum()), mask)
        result = _metrics.max_drawdown(a)
        value = _finite_or_none(result.value)
        if value is None:
            return _AStat(None, None, None, "NONFINITE", int(result.n_obs), mask)
        return _AStat(value, value, None, None, int(result.n_obs), mask)
    if metric is _spec.MetricKey.BENCHMARK_RELATIVE:
        return _AStat(None, None, None, NO_BENCHMARK_AUTHORITY, 0, mask)
    return _AStat(None, None, None, STATUS_NOT_ASSESSED, 0, mask)


def _b_series_for_metric(scenario: _ScenarioB, metric: _spec.MetricKey) -> pd.Series:
    if metric in (
        _spec.MetricKey.LONG_SHORT,
        _spec.MetricKey.SHARPE,
        _spec.MetricKey.MAX_DRAWDOWN,
    ):
        return scenario.gross
    if metric is _spec.MetricKey.TURNOVER_COST_ADJUSTED:
        return scenario.net
    if metric is _spec.MetricKey.IC:
        return _metrics.information_coefficient(scenario.panel).per_date
    if metric is _spec.MetricKey.RANK_IC:
        return _metrics.rank_information_coefficient(scenario.panel).per_date
    return pd.Series(dtype="float64")


def _available_for_metric(
    availability: _Availability, metric: _spec.MetricKey
) -> dict[date, bool]:
    if metric in (_spec.MetricKey.IC, _spec.MetricKey.RANK_IC):
        return availability.ic
    if metric is _spec.MetricKey.TURNOVER_COST_ADJUSTED:
        return availability.net
    return availability.gross


# ---------------------------------------------------------------------------
# Factor-panel identity
# ---------------------------------------------------------------------------


def _factor_panel_matches(
    factor_panel: pd.DataFrame,
    values: pd.DataFrame,
    formations: Sequence[date],
) -> bool:
    allowed = set(formations)
    lookup: dict[tuple[date, str], float] = {}
    for column in values.columns:
        for row_date, value in values[column].items():
            parsed = _d7b._as_date(row_date)
            lookup[(parsed, str(column))] = float(value)
    for row in factor_panel.itertuples(index=False):
        parsed = _d7b._as_date(row.date)
        if parsed not in allowed:
            continue
        key = (parsed, str(row.stock_id))
        expected = lookup.get(key)
        if not _same_float(_finite_or_none(row.value), _finite_or_none(expected)):
            return False
    return True


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def _declaration_reason(declaration: PQEvaluationDeclaration) -> str | None:
    if declaration.dataset_id != DATASET_ID:
        return PQ_DECLARATION_INVALID
    if declaration.amendment_sha256 != AMENDMENT_SHA256:
        return PQ_DECLARATION_INVALID
    if declaration.quarantine_rule_hash != QUARANTINE_RULE_HASH:
        return QUARANTINE_RULE_HASH_MISMATCH
    return None


def _blank_diagnostics(
    declaration: PQEvaluationDeclaration,
    factor_spec: FactorSpec,
    reason: str,
) -> PQDiagnostics:
    cells = tuple(
        PQCell(
            cell=name,
            scenario=(SCENARIO_T0 if name.endswith("T0") else SCENARIO_TS),
            estimand=(ESTIMAND_ORIGINAL if name.startswith("B") else ESTIMAND_QUARANTINED),
            value=None,
            raw_value=None,
            t_stat=None,
            status=reason,
            n_obs=0,
            n_index=0,
            availability_mask_hash=_mask_hash(np.zeros(0, dtype=bool)),
            declaration_hash=declaration.declaration_hash,
        )
        for name in _CELL_NAMES
    )
    return PQDiagnostics(
        not_assessed=reason,
        declaration_hash=declaration.declaration_hash,
        primary_metric=declaration.base.primary_metric.value,
        basis=declaration.base.basis,
        sign=int(factor_spec.sign),
        orientation_rule=_d7b._orientation_rule(
            int(factor_spec.sign), declaration.base.primary_metric
        ),
        screen_id=declaration.base.screen_id,
        screen_policy_hash=declaration.base.screen_policy_hash,
        index=(),
        cells=cells,
        b_diagnostics_hash=None,
        e_s=None,
        b=None,
        q_b=None,
        q_s=None,
        q_l=None,
        n_flagged_formations=0,
        residual_cs_cells=0,
        liquidity_exposed=0,
        n_interior_b_missing=0,
        support=(),
        cs3=(),
        cs3_status=None,
    )


def build_pq_diagnostics(
    root: Path | str,
    declaration: PQEvaluationDeclaration,
    factor_spec: FactorSpec,
    factor_panel: pd.DataFrame,
    partition: Any,
    *,
    overlay: _pqa.Overlay,
    inputs: Mapping[str, Any],
    bindings: _d7b.Bindings | None = None,
    pq_bindings: _pqa.Bindings | None = None,
    pq_repo_root: Path | str | None = None,
    pq_knowledge_log_path: Path | str | None = None,
    pq_read_records_fn: Any | None = None,
    lags: int | None = None,
) -> PQDiagnostics:
    """Evaluate one declared development fold under A/B x T0/TS."""
    if not isinstance(declaration, PQEvaluationDeclaration):
        raise PQDeclarationError("declaration must be a PQEvaluationDeclaration")
    bindings = bindings if bindings is not None else _d7b.DEFAULT_BINDINGS

    # Step 1 - bindings.
    verify_pinned_code(_REPO_ROOT)
    reason = _declaration_reason(declaration)
    if reason is not None:
        return _blank_diagnostics(declaration, factor_spec, reason)
    try:
        identity_hash = _pqa.verify_dataset_v1_2_identity(
            root,
            bindings=pq_bindings,
            repo_root=pq_repo_root,
            knowledge_log_path=pq_knowledge_log_path,
            read_records_fn=pq_read_records_fn,
        )
    except _pqa.DatasetIdentityError:
        return _blank_diagnostics(declaration, factor_spec, DATASET_IDENTITY_MISMATCH)
    if identity_hash != declaration.dataset_identity_hash:
        return _blank_diagnostics(declaration, factor_spec, DATASET_IDENTITY_MISMATCH)
    if overlay.pq_id != declaration.pq_id or overlay.pq_hash != declaration.pq_hash:
        return _blank_diagnostics(declaration, factor_spec, OVERLAY_MISMATCH)

    # Step 2 - B exactly as D7b.
    diag_b = _d7b.build_development_diagnostics(
        root,
        declaration.base,
        factor_spec,
        factor_panel,
        partition,
        bindings=bindings,
    )
    if diag_b.not_assessed is not None:
        return _blank_diagnostics(declaration, factor_spec, diag_b.not_assessed)

    # Step 3 - factor-panel identity against the PQA calculus.
    states = _pqa.factor_input_states(
        factor_spec, overlay=overlay, inputs=inputs
    )
    values = states.attrs.get("values")
    if values is None or not _factor_panel_matches(
        factor_panel, values, diag_b.formations
    ):
        return _blank_diagnostics(declaration, factor_spec, FACTOR_PANEL_MISMATCH)

    # Step 4 - reconstruct B's per-period series and reconcile.
    try:
        (scenario_t0, scenario_ts), groups = _reconstruct_b(
            root, declaration, factor_spec, factor_panel, partition, bindings
        )
    except PQEvaluationError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        raise PQReconciliationError(f"B reconstruction failed: {exc}") from exc
    _reconcile_b((scenario_t0, scenario_ts), diag_b, sign=int(factor_spec.sign))

    metric = declaration.base.primary_metric
    sign = int(factor_spec.sign)

    # Step 5 - original index (evaluated + purged formations, schedule order).
    purged_dates = {cell.formation_date for cell in diag_b.purged_cells}
    index = tuple(sorted(set(diag_b.formations) | purged_dates))

    # Step 6 - flags.
    input_flag, label_flag = _flag_maps(
        states, overlay.label_states, declaration.base.horizon
    )
    availability = _compute_availability(
        groups, input_flag, label_flag, declaration.base.n_groups, sign
    )

    # Steps 7-10 - A and B series and cells.
    cells: list[PQCell] = []
    b_series_map: dict[tuple[str, str], np.ndarray] = {}
    a_series_map: dict[tuple[str, str], np.ndarray] = {}
    a_status: dict[tuple[str, str], str | None] = {}
    for scenario_name, scenario, diag_value, diag_t, diag_nobs in (
        (SCENARIO_T0, scenario_t0, diag_b.value_t0, diag_b.t_t0, diag_b.n_obs_t0),
        (SCENARIO_TS, scenario_ts, diag_b.value_ts, diag_b.t_ts, diag_b.n_obs_ts),
    ):
        b_raw = b_series_on_index(_b_series_for_metric(scenario, metric), index)
        b_series_map[(ESTIMAND_ORIGINAL, scenario_name)] = b_raw
        b_value = diag_b.economic_t0 if scenario_name == SCENARIO_T0 else diag_b.economic_ts
        b_mask = np.isfinite(b_raw)
        cells.append(
            PQCell(
                cell=f"B-{scenario_name}",
                scenario=scenario_name,
                estimand=ESTIMAND_ORIGINAL,
                value=b_value,
                raw_value=(diag_b.value_t0 if scenario_name == SCENARIO_T0 else diag_b.value_ts),
                t_stat=diag_t,
                status=None,
                n_obs=int(diag_nobs) if diag_nobs is not None else 0,
                n_index=len(index),
                availability_mask_hash=_mask_hash(b_mask),
                declaration_hash=declaration.declaration_hash,
            )
        )
        available = np.array(
            [_available_for_metric(availability, metric).get(f, False) for f in index],
            dtype=bool,
        )
        stat = _estimate_a(
            b_raw,
            available,
            metric,
            sign=sign,
            periods_per_year=declaration.base.periods_per_year,
            lags=lags,
        )
        a_series_map[(ESTIMAND_QUARANTINED, scenario_name)] = mask_series(b_raw, available)
        a_status[(ESTIMAND_QUARANTINED, scenario_name)] = stat.status
        cells.append(
            PQCell(
                cell=f"A-{scenario_name}",
                scenario=scenario_name,
                estimand=ESTIMAND_QUARANTINED,
                value=stat.value,
                raw_value=stat.raw_value,
                t_stat=stat.t_stat,
                status=stat.status,
                n_obs=stat.n_obs,
                n_index=len(index),
                availability_mask_hash=_mask_hash(stat.mask),
                declaration_hash=declaration.declaration_hash,
            )
        )

    # Step 11 - CS-3 (report-only).
    a_primary = a_series_map[(ESTIMAND_QUARANTINED, SCENARIO_T0)]
    b_primary = b_series_map[(ESTIMAND_ORIGINAL, SCENARIO_T0)]
    complement = complement_series(b_primary, a_primary)
    cs3: list[tuple[str, float | None]] = []
    cs3_status: str | None = None
    if metric in _HAC_METRICS:
        comp_result = gap_aware_mean_tstat(complement, lags=lags)
        cs3 = [
            ("mean", _finite_or_none(comp_result.mean)),
            ("t_stat", _finite_or_none(comp_result.t_stat)),
            ("n_obs", float(comp_result.n_obs)),
        ]
        cs3_status = comp_result.status
    else:
        cs3_status = STATUS_NOT_ASSESSED

    n_portfolio = len(index)
    b_portfolio = int(np.isfinite(b_primary).sum())
    a_portfolio = int(np.isfinite(a_primary).sum())
    ic_map = _available_for_metric(availability, _spec.MetricKey.IC)
    b_ic_series = b_series_on_index(
        _b_series_for_metric(scenario_t0, _spec.MetricKey.IC), index
    )
    b_ic = int(np.isfinite(b_ic_series).sum())
    a_ic = int(
        np.isfinite(
            mask_series(
                b_ic_series,
                np.array([ic_map.get(f, False) for f in index], dtype=bool),
            )
        ).sum()
    )

    # Step 12 - residual cross-sectional influence.
    residual_total = 0
    residual_map = states.attrs.get("residual_cs_cells", {})
    for value in residual_map.values():
        residual_total += int(value)

    # Step 13 - liquidity exposure (report-only).
    liquidity = overlay.liquidity_exposure
    liquidity_exposed = 0
    if liquidity is not None and len(liquidity):
        liquidity_exposed = int(liquidity["liquidity_window_exposed"].sum())

    return PQDiagnostics(
        not_assessed=None,
        declaration_hash=declaration.declaration_hash,
        primary_metric=metric.value,
        basis=declaration.base.basis,
        sign=sign,
        orientation_rule=_d7b._orientation_rule(sign, metric),
        screen_id=declaration.base.screen_id,
        screen_policy_hash=declaration.base.screen_policy_hash,
        index=index,
        cells=tuple(cells),
        b_diagnostics_hash=diag_b.diagnostics_hash,
        e_s=diag_b.e_s,
        b=diag_b.b,
        q_b=availability.q_b,
        q_s=availability.q_s,
        q_l=availability.q_l,
        n_flagged_formations=availability.flagged_formations,
        residual_cs_cells=residual_total,
        liquidity_exposed=liquidity_exposed,
        n_interior_b_missing=interior_gaps(b_primary),
        support=(
            ("a_portfolio", a_portfolio),
            ("b_portfolio", b_portfolio),
            ("n_index", n_portfolio),
            ("a_ic", a_ic),
            ("b_ic", b_ic),
        ),
        cs3=tuple(cs3),
        cs3_status=cs3_status,
        b_diagnostics=diag_b,
    )


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _delisting_status(
    economic_t0: float | None,
    economic_ts: float | None,
    e_s: float | None,
    b: float | None,
    passed_t0: bool | None,
    passed_ts: bool | None,
) -> str:
    """The D7b §9.6 clauses applied to one estimand's T0/TS comparison."""
    if economic_t0 is None or economic_ts is None:
        return DELISTING_NOT_ASSESSED
    sgn_t0 = _sgn(economic_t0)
    sgn_ts = _sgn(economic_ts)
    if sgn_t0 is None or sgn_ts is None:
        return DELISTING_NOT_ASSESSED
    clause_a = sgn_t0 != sgn_ts
    clause_b = bool(passed_t0) and not bool(passed_ts)
    if e_s is None:
        return DELISTING_NOT_ASSESSED
    if b is not None and e_s > 2.0 * b:
        clause_c = True
    elif b == 0.0 and e_s > 0.0:
        clause_c = True
    else:
        clause_c = False
    if clause_a or clause_b or clause_c:
        return DELISTING_SENSITIVE
    return DELISTING_NOT_SENSITIVE


def _quarantine_clauses(
    diagnostics: PQDiagnostics,
    screens: Mapping[str, PQScreenOutcome],
) -> tuple[bool, bool, bool, bool, bool, bool, bool]:
    a_t0 = diagnostics.cell("A-T0")
    a_ts = diagnostics.cell("A-TS")
    b_t0 = diagnostics.cell("B-T0")
    b_ts = diagnostics.cell("B-TS")
    assert a_t0 and a_ts and b_t0 and b_ts
    clause_a_t0 = _sgn(a_t0.value) != _sgn(b_t0.value)
    clause_a_ts = _sgn(a_ts.value) != _sgn(b_ts.value)
    clause_b_t0 = bool(screens["A-T0"].passed) != bool(screens["B-T0"].passed)
    clause_b_ts = bool(screens["A-TS"].passed) != bool(screens["B-TS"].passed)
    q_b = diagnostics.q_b
    q_s = diagnostics.q_s
    q_l = diagnostics.q_l
    if q_b is None:
        clause_c = False
    else:
        clause_c = (
            (q_s is not None and q_s > KAPPA * q_b)
            or (q_l is not None and q_l > KAPPA * q_b)
            or (q_b == 0.0 and ((q_s or 0.0) > 0.0 or (q_l or 0.0) > 0.0))
        )
    clause_a = clause_a_t0 or clause_a_ts
    clause_b = clause_b_t0 or clause_b_ts
    return clause_a, clause_b, clause_c, clause_a_t0, clause_a_ts, clause_b_t0, clause_b_ts


def classify_pq(
    diagnostics: PQDiagnostics,
    screens: Sequence[PQScreenOutcome],
) -> PQDevelopmentSensitivityRecord:
    """Apply the P-Q sensitivity clauses and return the development record."""
    by_cell = {screen.cell: screen for screen in screens}
    required = set(_CELL_NAMES)

    def finish(
        status: str,
        reason: str | None,
        *,
        quarantine_sensitive: bool = False,
        clause_a: bool = False,
        clause_b: bool = False,
        clause_c: bool = False,
        clause_a_t0: bool = False,
        clause_a_ts: bool = False,
        clause_b_t0: bool = False,
        clause_b_ts: bool = False,
        delisting_a: str = DELISTING_NOT_ASSESSED,
        delisting_b: str = DELISTING_NOT_ASSESSED,
    ) -> PQDevelopmentSensitivityRecord:
        a_t0 = diagnostics.cell("A-T0")
        a_ts = diagnostics.cell("A-TS")
        b_t0 = diagnostics.cell("B-T0")
        b_ts = diagnostics.cell("B-TS")
        advancement_blocked = not (
            status != STATUS_NOT_ASSESSED
            and not quarantine_sensitive
            and delisting_a == DELISTING_NOT_SENSITIVE
            and delisting_b == DELISTING_NOT_SENSITIVE
        )
        return PQDevelopmentSensitivityRecord(
            declaration_hash=diagnostics.declaration_hash,
            diagnostics_hash=diagnostics.diagnostics_hash,
            screen_id=diagnostics.screen_id,
            screen_policy_hash=diagnostics.screen_policy_hash,
            status=status,
            not_assessed_reason=reason,
            quarantine_sensitive=bool(quarantine_sensitive),
            clause_a=bool(clause_a),
            clause_b=bool(clause_b),
            clause_c=bool(clause_c),
            clause_a_t0=bool(clause_a_t0),
            clause_a_ts=bool(clause_a_ts),
            clause_b_t0=bool(clause_b_t0),
            clause_b_ts=bool(clause_b_ts),
            delisting_a=delisting_a,
            delisting_b=delisting_b,
            economic_a_t0=None if a_t0 is None else a_t0.value,
            economic_a_ts=None if a_ts is None else a_ts.value,
            economic_b_t0=None if b_t0 is None else b_t0.value,
            economic_b_ts=None if b_ts is None else b_ts.value,
            e_s=diagnostics.e_s,
            b=diagnostics.b,
            q_b=diagnostics.q_b,
            q_s=diagnostics.q_s,
            q_l=diagnostics.q_l,
            advancement_blocked=advancement_blocked,
            holdout_authorized=False,
            final_holdout_consumed=False,
            confirmation_evidence=False,
            scope=SCOPE,
            cell_hashes=tuple((cell.cell, cell.cell_hash) for cell in diagnostics.cells),
        )

    if diagnostics.not_assessed is not None:
        return finish(STATUS_NOT_ASSESSED, diagnostics.not_assessed)
    if set(by_cell) != required:
        return finish(STATUS_NOT_ASSESSED, SCREEN_MISSING)
    for name in _CELL_NAMES:
        screen = by_cell[name]
        cell = diagnostics.cell(name)
        if (
            cell is None
            or screen.screen_id != diagnostics.screen_id
            or screen.screen_policy_hash != diagnostics.screen_policy_hash
            or screen.declaration_hash != diagnostics.declaration_hash
            or screen.cell_hash != cell.cell_hash
        ):
            return finish(STATUS_NOT_ASSESSED, SCREEN_MISMATCH)

    a_t0 = diagnostics.cell("A-T0")
    a_ts = diagnostics.cell("A-TS")
    assert a_t0 is not None and a_ts is not None
    if a_t0.status is not None or a_ts.status is not None:
        return finish(STATUS_NOT_ASSESSED, A_STATISTIC_NOT_ASSESSED)
    if a_t0.value is None or a_ts.value is None:
        return finish(STATUS_NOT_ASSESSED, A_STATISTIC_NOT_ASSESSED)
    if by_cell["A-T0"].passed is None or by_cell["A-TS"].passed is None:
        return finish(STATUS_NOT_ASSESSED, A_SCREEN_NOT_ASSESSED)

    clause_a, clause_b, clause_c, ca_t0, ca_ts, cb_t0, cb_ts = _quarantine_clauses(
        diagnostics, by_cell
    )
    quarantine_sensitive = clause_a or clause_b or clause_c

    delisting_a = _delisting_status(
        a_t0.value,
        a_ts.value,
        diagnostics.e_s,
        diagnostics.b,
        by_cell["A-T0"].passed,
        by_cell["A-TS"].passed,
    )
    diag_b = diagnostics.b_diagnostics
    if diag_b is None:
        delisting_b = DELISTING_NOT_ASSESSED
    else:
        screen_b_t0 = _d7b.ScreenOutcome(
            screen_id=by_cell["B-T0"].screen_id,
            screen_policy_hash=by_cell["B-T0"].screen_policy_hash,
            declaration_hash=by_cell["B-T0"].declaration_hash,
            diagnostics_hash=diag_b.diagnostics_hash,
            scenario=SCENARIO_T0,
            passed=bool(by_cell["B-T0"].passed),
        )
        screen_b_ts = _d7b.ScreenOutcome(
            screen_id=by_cell["B-TS"].screen_id,
            screen_policy_hash=by_cell["B-TS"].screen_policy_hash,
            declaration_hash=by_cell["B-TS"].declaration_hash,
            diagnostics_hash=diag_b.diagnostics_hash,
            scenario=SCENARIO_TS,
            passed=bool(by_cell["B-TS"].passed),
        )
        delisting_b = _d7b.classify_delisting_sensitivity(
            diag_b, screen_b_t0, screen_b_ts
        ).status

    if quarantine_sensitive:
        status = STATUS_QUARANTINE_SENSITIVE
    else:
        status = STATUS_NOT_SENSITIVE
    return finish(
        status,
        None,
        quarantine_sensitive=quarantine_sensitive,
        clause_a=clause_a,
        clause_b=clause_b,
        clause_c=clause_c,
        clause_a_t0=ca_t0,
        clause_a_ts=ca_ts,
        clause_b_t0=cb_t0,
        clause_b_ts=cb_ts,
        delisting_a=delisting_a,
        delisting_b=delisting_b,
    )
