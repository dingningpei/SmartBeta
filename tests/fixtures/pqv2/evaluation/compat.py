"""PQ2-H pre-integration test doubles for the parallel PQ21-D/E/F signatures.

The PQ21-H task is executed in parallel with PQ21-D, PQ21-E and PQ21-F, whose
frozen v2.1 behaviour (cause-preserving short-circuits and the per-period cause
fields) is not present on the H branch yet.  H's production code is written
against those *frozen* signatures, so the H tests install faithful test doubles
**only when the real module lacks the new behaviour**:

* ``simple_assignment`` / ``sandwich_assignment`` gain the frozen
  cause-preserving union-and-short-circuit;
* ``compose_series`` is wrapped so an ``EnvelopePeriod`` carries the frozen
  ``causes_t0`` / ``causes_ts`` / ``n_unknown_source`` fields and short-circuits
  UNKNOWN causes before the winsorization / resolved-state checks.

On the integrated v2.1 master the real modules already provide the behaviour and
:func:`install` / :func:`install_raw` are no-ops, so these doubles can never mask
a regression there.  Nothing here reads a dataset artifact, the reserve, holdout
or any credential.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from smart_beta.dataset_v1 import pqv2_assign_sandwich as _sandwich_mod
from smart_beta.dataset_v1 import pqv2_assign_simple as _simple_mod
from smart_beta.dataset_v1 import pqv2_core as C
from smart_beta.dataset_v1 import pqv2_envelope as ENV

__all__ = [
    "CompatPeriod",
    "install",
    "install_raw",
    "needs_doubles",
    "possible_members",
]

ASSESSED = ENV.ASSESSED
NOT_ASSESSED = ENV.NOT_ASSESSED

_SENTINEL = object()


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def _accepts_causes(function: Any) -> bool:
    import inspect

    try:
        return "causes" in inspect.signature(function).parameters
    except (TypeError, ValueError):
        return False


def _period_has_causes() -> bool:
    fields = getattr(ENV.EnvelopePeriod, "__dataclass_fields__", {})
    return "causes_t0" in fields and "causes_ts" in fields


def needs_doubles() -> bool:
    """True when at least one real PQ21-D/E/F signature is pre-integration."""

    return not (
        _accepts_causes(_simple_mod.simple_assignment)
        and _accepts_causes(_sandwich_mod.sandwich_assignment)
        and _period_has_causes()
    )


# ---------------------------------------------------------------------------
# Compat period
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompatPeriod:
    """An ``EnvelopePeriod`` with the frozen v2.1 cause fields added."""

    period: Any
    rule: Any
    winsor_ok: Any
    D_t: Any
    TO_b: Any
    TO_hi: Any
    changed_b: Any
    union: Any
    LS_b_t0: Any
    LS_b_ts: Any
    L_t0: Any
    L_ts: Any
    status_t0: Any
    status_ts: Any
    reason_t0: Any
    reason_ts: Any
    exposure: Any
    label_counts: Any
    n_unknown_reserve: Any
    causes_t0: tuple[str, ...] = ()
    causes_ts: tuple[str, ...] = ()
    n_unknown_source: int = 0

    def status(self, scenario: str) -> str:
        return self.status_t0 if scenario == "T0" else self.status_ts

    def reason(self, scenario: str) -> Any:
        return self.reason_t0 if scenario == "T0" else self.reason_ts

    def value(self, scenario: str) -> float:
        return self.L_t0 if scenario == "T0" else self.L_ts

    def LS_b(self, scenario: str) -> float:
        return self.LS_b_t0 if scenario == "T0" else self.LS_b_ts

    def to_dict(self) -> dict[str, Any]:
        return {
            "period": self.period,
            "rule": self.rule,
            "winsor_ok": self.winsor_ok,
            "D_t": self.D_t,
            "TO_b": self.TO_b,
            "TO_hi": self.TO_hi,
            "changed_b": self.changed_b,
            "union": self.union,
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
            "n_unknown_reserve": self.n_unknown_reserve,
            "causes_t0": list(self.causes_t0),
            "causes_ts": list(self.causes_ts),
            "n_unknown_source": int(self.n_unknown_source),
        }


# ---------------------------------------------------------------------------
# Frozen PQ21-D / PQ21-E cause short-circuits
# ---------------------------------------------------------------------------


def _wrap_simple(original: Any) -> Any:
    def wrapper(
        keys: Sequence[str],
        values_unwinsorized: Any,
        row_order: Any,
        uncertain: Any,
        unknown: Any,
        G: int,
        long_values_lo: Any,
        short_values_hi: Any,
        *,
        cls: str = "SIMPLE",
        causes: Mapping[str, C.CauseSet] | None = None,
    ) -> Any:
        if causes is not None:
            key_list = tuple(keys)
            union = C.EMPTY_CAUSES
            for key in key_list:
                union = union.union(causes.get(key, C.EMPTY_CAUSES))
            if not union.is_empty:
                affected = tuple(
                    sorted(
                        key
                        for key in key_list
                        if not causes.get(key, C.EMPTY_CAUSES).is_empty
                    )
                )
                return C.NotAssessed.from_causes(union, affected)
        return original(
            keys,
            values_unwinsorized,
            row_order,
            uncertain,
            unknown,
            G,
            long_values_lo,
            short_values_hi,
            cls=cls,
        )

    return wrapper


def _wrap_sandwich(original: Any) -> Any:
    def wrapper(
        cs_keys: Any,
        M_obs: Any,
        M_interval: Any,
        V_obs: Any,
        V_interval: Any,
        unknown: Any,
        universe_keys: Any,
        b_groups: Any,
        G: int,
        long_values_lo: Any,
        short_values_hi: Any,
        *,
        causes: Mapping[str, C.CauseSet] | None = None,
    ) -> Any:
        if causes is not None:
            cross = tuple(str(key) for key in cs_keys)
            union = C.EMPTY_CAUSES
            for key in cross:
                union = union.union(causes.get(key, C.EMPTY_CAUSES))
            if not union.is_empty:
                affected = tuple(
                    sorted(
                        key
                        for key in cross
                        if not causes.get(key, C.EMPTY_CAUSES).is_empty
                    )
                )
                return C.NotAssessed.from_causes(union, affected)
        return original(
            cs_keys,
            M_obs,
            M_interval,
            V_obs,
            V_interval,
            unknown,
            universe_keys,
            b_groups,
            G,
            long_values_lo,
            short_values_hi,
        )

    return wrapper


# ---------------------------------------------------------------------------
# Frozen PQ21-F per-period cause composition
# ---------------------------------------------------------------------------


def possible_members(assignment: Any) -> set[str]:
    """The M§8A.5 possible-leg membership set of an assignment."""

    if isinstance(assignment, ENV.SimpleAssignment):
        return set(assignment.possible_long) | set(assignment.possible_short)
    if isinstance(assignment, ENV.SandwichAssignment):
        return set(assignment.P_top) | set(assignment.P_bot)
    return set()


def _base_causes(assignment: Any, labels: Mapping[str, Any]) -> C.CauseSet:
    if isinstance(assignment, C.NotAssessed):
        cause_set = assignment.cause_set
        return cause_set if cause_set is not None else C.EMPTY_CAUSES
    union = C.EMPTY_CAUSES
    for key in possible_members(assignment):
        label = labels.get(key)
        cause_set = getattr(label, "causes", None)
        if cause_set is None:
            continue
        union = union.union(cause_set)
    return union


def _resolved_causes(period: Any, scenario: str) -> C.CauseSet:
    if period.status(scenario) == ASSESSED:
        return C.EMPTY_CAUSES
    reason = period.reason(scenario)
    if reason in C.PERIOD_CAUSE_PRECEDENCE:
        return C.CauseSet.of(reason)
    return C.EMPTY_CAUSES


def _n_unknown_source(assignment: Any, labels: Mapping[str, Any]) -> int:
    count = 0
    for key in possible_members(assignment):
        label = labels.get(key)
        if getattr(label, "status", None) == C.LabelStatus.UNKNOWN_SOURCE:
            count += 1
    return count


def _copy_period(period: Any, **overrides: Any) -> CompatPeriod:
    fields: dict[str, Any] = {
        name: getattr(period, name)
        for name in (
            "period",
            "rule",
            "winsor_ok",
            "D_t",
            "TO_b",
            "TO_hi",
            "changed_b",
            "union",
            "LS_b_t0",
            "LS_b_ts",
            "L_t0",
            "L_ts",
            "status_t0",
            "status_ts",
            "reason_t0",
            "reason_ts",
            "exposure",
            "label_counts",
            "n_unknown_reserve",
        )
    }
    fields.update(overrides)
    return CompatPeriod(**fields)


def _upgrade_period(period: Any, assignment: Any, labels: Mapping[str, Any]) -> CompatPeriod:
    base = _base_causes(assignment, labels)
    if not base.is_empty:
        cause_list = tuple(base.to_list())
        return _copy_period(
            period,
            winsor_ok=None,
            L_t0=math.nan,
            L_ts=math.nan,
            status_t0=NOT_ASSESSED,
            status_ts=NOT_ASSESSED,
            reason_t0=base.primary,
            reason_ts=base.primary,
            causes_t0=cause_list,
            causes_ts=cause_list,
            n_unknown_source=_n_unknown_source(assignment, labels),
        )
    causes_t0 = tuple(_resolved_causes(period, "T0").to_list())
    causes_ts = tuple(_resolved_causes(period, "TS").to_list())
    return _copy_period(
        period,
        causes_t0=causes_t0,
        causes_ts=causes_ts,
        n_unknown_source=0,
    )


def _wrap_compose(original: Any) -> Any:
    def wrapper(
        periods: Sequence[Any],
        metric_basis: Any,
        sign: Any,
        cost_bps: float,
        G: int,
        q: float,
        assignment_by_period: Mapping[Any, Any],
        labels_by_period: Mapping[Any, Mapping[str, Any]],
        b_groups_by_period: Mapping[Any, Mapping[str, Any]],
        *,
        cls: str,
        repo_root: Any = None,
    ) -> Any:
        series = original(
            periods,
            metric_basis,
            sign,
            cost_bps,
            G,
            q,
            assignment_by_period,
            labels_by_period,
            b_groups_by_period,
            cls=cls,
            repo_root=repo_root,
        )
        upgraded = tuple(
            _upgrade_period(
                period,
                assignment_by_period.get(period.period),
                labels_by_period.get(period.period, {}),
            )
            for period in series.periods
        )
        return replace(series, periods=upgraded)

    return wrapper


# ---------------------------------------------------------------------------
# Installation
# ---------------------------------------------------------------------------


def install(monkeypatch: Any) -> None:
    """Install the doubles through a pytest ``monkeypatch`` (scoped teardown)."""

    if not _accepts_causes(_simple_mod.simple_assignment):
        monkeypatch.setattr(
            _simple_mod,
            "simple_assignment",
            _wrap_simple(_simple_mod.simple_assignment),
        )
    if not _accepts_causes(_sandwich_mod.sandwich_assignment):
        monkeypatch.setattr(
            _sandwich_mod,
            "sandwich_assignment",
            _wrap_sandwich(_sandwich_mod.sandwich_assignment),
        )
    if not _period_has_causes():
        monkeypatch.setattr(ENV, "compose_series", _wrap_compose(ENV.compose_series))


def install_raw() -> None:
    """Install the doubles by mutating module globals (subprocess throwaways)."""

    if not _accepts_causes(_simple_mod.simple_assignment):
        _simple_mod.simple_assignment = _wrap_simple(_simple_mod.simple_assignment)
    if not _accepts_causes(_sandwich_mod.sandwich_assignment):
        _sandwich_mod.sandwich_assignment = _wrap_sandwich(
            _sandwich_mod.sandwich_assignment
        )
    if not _period_has_causes():
        ENV.compose_series = _wrap_compose(ENV.compose_series)
