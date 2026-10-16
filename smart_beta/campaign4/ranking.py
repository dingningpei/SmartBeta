"""Campaign-4 M1 -- the training-window rank statistic (task C4M1-1).

Authority: ``worker_tasks/campaign4_m1/task-c4m1-1-contract-core.md``
section 4 and ``docs/campaign4_m1a_contract_calibration_draft.md`` section 2.

The rank statistic the Campaign-4 contract ranks candidates on is the
**minimum over the event scenarios {K0, K1}** (non-event: the single ``L-TD``
cell) of the one-sided EWC t of the long-side terminal-stress series, computed
on the **training** periods only (formation dates ``< window_cut``).

The EWC estimator is **not** re-implemented: this module calls the frozen
:func:`smart_beta.diagnostics.known_factors._ewc_statistic`, the same procedure
the v2.5 equivalence path uses.  The import is private; it is deliberate
(C4M1-1 task spec section 4) and is the frozen procedure, not a copy.

Every TD path calls :func:`assert_v25_admitted` first, which calls
:func:`smart_beta.dataset_v1.pqv25.authority.verify_authority_v2_5` and checks
``d == -0.55``.

Nothing here reads a real dataset, opens a network path or modifies a sealed
file.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import pandas as pd

from smart_beta.campaign4.contract import Campaign4Error
from smart_beta.dataset_v1.pqv25.authority import TD_D, verify_authority_v2_5

# Private import of the frozen EWC procedure wrapper (task spec section 4,
# "Its import may be private; record that explicitly in a module comment.").
from smart_beta.diagnostics import known_factors as _known
from smart_beta.science.procedures.ewc_t23_one_sided import MIN_PERIODS

__all__ = [
    "DEFAULT_BOUND_ALPHA",
    "MIN_EWC_PERIODS",
    "TRAINING",
    "TEST",
    "VALID",
    "NOT_ASSESSED",
    "INSUFFICIENT_SAMPLE",
    "RankingError",
    "RankStatistic",
    "CellTestStatistics",
    "TestStatistics",
    "assert_v25_admitted",
    "window_series",
    "rank_statistic",
    "l_t0_ewc_t",
    "test_statistics",
]

#: The one-sided bound alpha, matching the C4M0-13 / v2.5 default.
DEFAULT_BOUND_ALPHA = 0.05

#: The EWC procedure's minimum period count (the frozen 24).
MIN_EWC_PERIODS = int(MIN_PERIODS)

#: The two window sides accepted by :func:`window_series`.
TRAINING = "training"
TEST = "test"

#: The rank-statistic status vocabulary.
VALID = "VALID"
NOT_ASSESSED = "NOT_ASSESSED"
INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"


class RankingError(Campaign4Error):
    """The rank-statistic contract is malformed (fail closed)."""


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    return pd.Timestamp(value).date()


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


# ---------------------------------------------------------------------------
# the v2.5 admission guard
# ---------------------------------------------------------------------------


def assert_v25_admitted() -> float:
    """Verify the frozen v2.5 authority and that ``d == -0.55``.

    Called first by every TD path (:func:`rank_statistic`,
    :func:`test_statistics`).  Any binding mismatch raises the frozen
    :class:`~smart_beta.dataset_v1.pqv2_core.PQv2BindingError`; a ``d`` other
    than ``-0.55`` is a Campaign-4 contract error.
    """

    verify_authority_v2_5()
    d = float(TD_D)
    if d != -0.55:
        raise RankingError(
            f"the v2.5 terminal-stress tau must be -0.55, got {d!r}"
        )
    return d


# ---------------------------------------------------------------------------
# series windowing
# ---------------------------------------------------------------------------


def _record_series(
    record: Any,
) -> tuple[tuple[Any, ...], tuple[float, ...]]:
    if isinstance(record, Mapping):
        return (
            tuple(record.get("periods", ())),
            tuple(float(value) for value in record.get("l_series", ())),
        )
    return (
        tuple(record.periods),
        tuple(float(value) for value in record.l_series),
    )


def window_series(
    record: Any,
    window_cut: date | str | datetime,
    side: str,
) -> tuple[tuple[Any, ...], tuple[float, ...]]:
    """Slice a series record's periods to the training or test window.

    ``side == "training"`` keeps formation dates ``< window_cut``;
    ``side == "test"`` keeps formation dates ``>= window_cut``.  The returned
    values are the record's long-side series (``l_series``), in order.
    """

    cut = _as_date(window_cut)
    if side not in (TRAINING, TEST):
        raise RankingError(
            f"side must be {TRAINING!r} or {TEST!r}, got {side!r}"
        )
    periods, values = _record_series(record)
    if len(periods) != len(values):
        raise RankingError(
            f"series record {getattr(record, 'cell', '?')!r} has "
            f"{len(periods)} periods but {len(values)} values"
        )
    kept_periods: list[Any] = []
    kept_values: list[float] = []
    for period, value in zip(periods, values):
        dated = _as_date(period)
        keep = dated < cut if side == TRAINING else dated >= cut
        if keep:
            kept_periods.append(period)
            kept_values.append(value)
    return tuple(kept_periods), tuple(kept_values)


# ---------------------------------------------------------------------------
# cell discovery / EWC
# ---------------------------------------------------------------------------


def _l_cell_names(export: Any, scenario: str) -> tuple[str, ...]:
    """The long-side cell names of one scenario in an export.

    A non-event export has the single ``L-<scenario>`` cell; an event export
    has ``L-<scenario>-K0`` / ``L-<scenario>-K1``.
    """

    base = f"L-{scenario}"
    if export.get(base) is not None:
        return (base,)
    return tuple(
        f"{base}-{name}"
        for name in ("K0", "K1")
        if export.get(f"{base}-{name}") is not None
    )


def _ewc(
    periods: Sequence[Any],
    values: Sequence[float],
    *,
    bound_alpha: float,
) -> tuple[float | None, str, str | None]:
    """The frozen EWC t plus ``(t, status, reason)``.

    ``status`` is :data:`VALID`, :data:`INSUFFICIENT_SAMPLE` or
    :data:`NOT_ASSESSED`; ``reason`` is the frozen procedure's condition id.
    """

    t_stat, _p, status = _known._ewc_statistic(
        tuple(periods),
        [float(value) for value in values],
        bound_alpha=float(bound_alpha),
    )
    if status == "VALID" and t_stat is not None:
        return _opt_float(t_stat), VALID, None
    if status == "INSUFFICIENT_SAMPLE":
        return None, INSUFFICIENT_SAMPLE, status
    return None, NOT_ASSESSED, status


def _scenario_results(
    export: Any,
    scenario: str,
    window_cut: date | str | datetime,
    side: str,
    *,
    bound_alpha: float,
    sign: int,
) -> tuple[tuple[str, float | None, str, str | None], ...]:
    cells = _l_cell_names(export, scenario)
    if not cells:
        raise RankingError(
            f"the export has no L-{scenario} cell (non-event or event form)"
        )
    results: list[tuple[str, float | None, str, str | None]] = []
    for cell_name in cells:
        record = export.get(cell_name)
        if record is None or not record.assessed:
            reason = (
                "MISSING" if record is None else (record.reason or "NOT_ASSESSED")
            )
            results.append((cell_name, None, NOT_ASSESSED, reason))
            continue
        periods, values = window_series(record, window_cut, side)
        if len(values) < MIN_EWC_PERIODS:
            results.append(
                (
                    cell_name,
                    None,
                    INSUFFICIENT_SAMPLE,
                    "fewer-than-24-periods",
                )
            )
            continue
        oriented = [value * float(int(sign)) for value in values]
        t_stat, status, reason = _ewc(periods, oriented, bound_alpha=bound_alpha)
        results.append((cell_name, t_stat, status, reason))
    return tuple(results)


def _aggregate(
    results: Sequence[tuple[str, float | None, str, str | None]],
) -> tuple[str, float | None, str | None]:
    not_assessed = [item for item in results if item[2] == NOT_ASSESSED]
    if not_assessed:
        return NOT_ASSESSED, None, not_assessed[0][3]
    insufficient = [item for item in results if item[2] == INSUFFICIENT_SAMPLE]
    if insufficient:
        return INSUFFICIENT_SAMPLE, None, insufficient[0][3]
    values = [item[1] for item in results if item[1] is not None]
    if not values:
        return NOT_ASSESSED, None, "no-valid-scenario"
    return VALID, float(min(values)), None


def _latest_used_period(
    export: Any,
    scenario: str,
    window_cut: date,
    side: str,
) -> date | None:
    """The latest formation date the statistic took from the export."""

    latest: date | None = None
    for cell_name in _l_cell_names(export, scenario):
        record = export.get(cell_name)
        if record is None or not record.assessed:
            continue
        periods, _values = window_series(record, window_cut, side)
        for period in periods:
            dated = _as_date(period)
            if latest is None or dated > latest:
                latest = dated
    return latest


# ---------------------------------------------------------------------------
# result value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RankStatistic:
    """The training-window rank statistic of one candidate.

    ``value`` is the minimum one-sided EWC t over the TD scenarios when
    ``status == VALID``; otherwise ``None`` with ``reason`` set.

    ``window_cut`` is the cut the statistic was computed under and
    ``last_period`` is the latest formation date the statistic actually used
    (``None`` when none was used).  Both are the firewall binding the C4M1-1
    erratum E1 R1 requires: a renderer refuses a statistic whose ``window_cut``
    differs from its own or whose ``last_period`` is at/after the cut.
    """

    status: str
    value: float | None
    reason: str | None
    scenarios: tuple[tuple[str, float | None, str, str | None], ...]
    window_cut: date
    last_period: date | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "value": self.value,
            "reason": self.reason,
            "window_cut": self.window_cut.isoformat(),
            "last_period": (
                None if self.last_period is None else self.last_period.isoformat()
            ),
            "scenarios": [
                {
                    "cell": cell,
                    "ewc_t": t_stat,
                    "status": status,
                    "reason": reason,
                }
                for cell, t_stat, status, reason in self.scenarios
            ],
        }


@dataclass(frozen=True)
class CellTestStatistics:
    """The test-window descriptive statistics of one assessed TD cell."""

    cell: str
    net_mean: float | None
    net_sign: int
    positive_share: float | None
    n: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "cell": self.cell,
            "net_mean": self.net_mean,
            "net_sign": self.net_sign,
            "positive_share": self.positive_share,
            "n": self.n,
        }


@dataclass(frozen=True)
class TestStatistics:
    """The test-window descriptive statistics (and EWC t when available).

    ``cells`` carries the per-cell descriptive statistics of **every** assessed
    TD cell (erratum E1 R3).  The headline ``net_mean`` / ``net_sign`` /
    ``positive_share`` / ``n`` fields use the cell with the **lower** mean
    (ties go to the earlier cell, K0 before K1), mirroring the minimum in the
    rank statistic.  ``ewc_t`` is the minimum over cells with at least
    :data:`MIN_EWC_PERIODS` test periods.
    """

    status: str
    ewc_t: float | None
    net_mean: float | None
    net_sign: int
    positive_share: float | None
    n: int
    reason: str | None
    cells: tuple[CellTestStatistics, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "ewc_t": self.ewc_t,
            "net_mean": self.net_mean,
            "net_sign": self.net_sign,
            "positive_share": self.positive_share,
            "n": self.n,
            "reason": self.reason,
            "cells": [cell.to_dict() for cell in self.cells],
        }


# ---------------------------------------------------------------------------
# public rank statistics
# ---------------------------------------------------------------------------


def rank_statistic(
    td_export: Any,
    window_cut: date | str | datetime,
    *,
    bound_alpha: float = DEFAULT_BOUND_ALPHA,
    sign: int = 1,
) -> RankStatistic:
    """The training-window rank statistic (minimum over the TD scenarios).

    The C4 grammar fixes the FactorSpec ``sign`` to ``+1`` (prompt section 0);
    the ``sign`` keyword is the explicit ``l_series x sign`` orientation the
    task spec names and defaults to that frozen value.
    """

    assert_v25_admitted()
    cut = _as_date(window_cut)
    results = _scenario_results(
        td_export, "TD", cut, TRAINING, bound_alpha=bound_alpha, sign=sign
    )
    status, value, reason = _aggregate(results)
    return RankStatistic(
        status=status,
        value=value,
        reason=reason,
        scenarios=results,
        window_cut=cut,
        last_period=_latest_used_period(td_export, "TD", cut, TRAINING),
    )


def l_t0_ewc_t(
    base_export: Any,
    window_cut: date | str | datetime,
    *,
    bound_alpha: float = DEFAULT_BOUND_ALPHA,
    sign: int = 1,
) -> RankStatistic:
    """The same statistic for the L-T0 cells (no terminal stress).

    This is not a TD path, so it does not call
    :func:`assert_v25_admitted`; it is the comparison statistic the C4
    feedback shows next to ``RANK_STAT``.
    """

    results = _scenario_results(
        base_export, "T0", window_cut, TRAINING, bound_alpha=bound_alpha, sign=sign
    )
    cut = _as_date(window_cut)
    status, value, reason = _aggregate(results)
    return RankStatistic(
        status=status,
        value=value,
        reason=reason,
        scenarios=results,
        window_cut=cut,
        last_period=_latest_used_period(base_export, "T0", cut, TRAINING),
    )


def test_statistics(
    td_export: Any,
    window_cut: date | str | datetime,
    *,
    bound_alpha: float = DEFAULT_BOUND_ALPHA,
    sign: int = 1,
) -> TestStatistics:
    """The test-window statistics of one candidate.

    Always: the mean L-TD net return, its sign, the share of positive months
    and ``n``.  When there are at least :data:`MIN_EWC_PERIODS` test periods
    (and the EWC is computable) the test-period one-sided EWC t is included
    (W1).  For an event candidate the EWC t is the minimum over the assessed
    TD scenarios (mirroring :func:`rank_statistic`) and the descriptive
    statistics come from the first assessed scenario cell.
    """

    assert_v25_admitted()
    cell_names = _l_cell_names(td_export, "TD")
    if not cell_names:
        raise RankingError(
            "the export has no L-TD cell (non-event or event form)"
        )

    assessed: list[tuple[str, tuple[Any, ...], tuple[float, ...]]] = []
    for cell_name in cell_names:
        record = td_export.get(cell_name)
        if record is None or not record.assessed:
            continue
        periods, values = window_series(record, window_cut, TEST)
        assessed.append((cell_name, periods, values))

    if not assessed:
        return TestStatistics(
            status=NOT_ASSESSED,
            ewc_t=None,
            net_mean=None,
            net_sign=0,
            positive_share=None,
            n=0,
            reason="no-assessed-td-cell",
            cells=(),
        )

    # Per-cell descriptive statistics of l_series x sign (erratum E1 R3).
    cell_stats: list[CellTestStatistics] = []
    for cell_name, _periods, values in assessed:
        oriented = [value * float(int(sign)) for value in values]
        n = len(oriented)
        if n == 0:
            cell_stats.append(
                CellTestStatistics(
                    cell=cell_name,
                    net_mean=None,
                    net_sign=0,
                    positive_share=None,
                    n=0,
                )
            )
            continue
        mean = float(sum(oriented) / n)
        net_sign = 1 if mean > 0.0 else (-1 if mean < 0.0 else 0)
        share = float(sum(1 for value in oriented if value > 0.0) / n)
        cell_stats.append(
            CellTestStatistics(
                cell=cell_name,
                net_mean=mean,
                net_sign=net_sign,
                positive_share=share,
                n=n,
            )
        )

    # The EWC t is the minimum over cells with at least 24 test periods.
    ewc_t: float | None = None
    for _cell_name, periods, values in assessed:
        if len(values) < MIN_EWC_PERIODS:
            continue
        oriented = [value * float(int(sign)) for value in values]
        t_stat, t_status, _reason = _ewc(
            periods, oriented, bound_alpha=bound_alpha
        )
        if t_status == VALID and t_stat is not None:
            ewc_t = t_stat if ewc_t is None else min(ewc_t, t_stat)

    # The headline is the cell with the lower mean (ties go to K0/earlier).
    headline: CellTestStatistics | None = None
    for candidate in cell_stats:
        if candidate.net_mean is None:
            continue
        if headline is None or candidate.net_mean < headline.net_mean:
            headline = candidate

    if ewc_t is not None:
        status, reason = VALID, None
    elif not any(
        len(values) >= MIN_EWC_PERIODS for _c, _p, values in assessed
    ):
        status, reason = INSUFFICIENT_SAMPLE, "fewer-than-24-test-periods"
    else:
        status, reason = NOT_ASSESSED, "ewc-not-computable"

    return TestStatistics(
        status=status,
        ewc_t=ewc_t,
        net_mean=None if headline is None else headline.net_mean,
        net_sign=0 if headline is None else headline.net_sign,
        positive_share=None if headline is None else headline.positive_share,
        n=0 if headline is None else headline.n,
        reason=reason,
        cells=tuple(cell_stats),
    )
