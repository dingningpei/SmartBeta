"""Deterministic Monte-Carlo validation study for ``NONOVERLAP_T_ONE_SIDED``.

This module implements exactly the PA-0 section 4 validation design, frozen by
``docs/phase10/procedures/pa0_nonoverlap_t_candidate_spec.md``:

* seed ``numpy.random.Generator(numpy.random.PCG64(20261009))``;
* ``R = 20_000`` replications per cell;
* grid ``T in {24, 30, 36, 48, 60}`` x ``rho_1 in {0, 0.1, 0.2, 0.3}`` x
  ``innovations in {Normal, t5, t3}`` x ``N in {200, 1000}`` names.

Data-generating process (the estimand's construction, reduced exactly)
---------------------------------------------------------------------

The per-formation series is the net long-short return of two equal quintile
legs of ``N`` names. Each name obeys a one-factor model with a common factor
``f`` and idiosyncratic noise ``eps ~ N(0, 1)``; the long-short legs carry a
unit net exposure to ``f`` and the idiosyncratic noise averages over the
``N/5`` names of each leg. The long-short return is therefore

    LS_t = (delta + f_t + u_t) / sqrt(1 + v),   u_t ~ N(0, v),  v = 10 / N,

where ``f`` is a stationary unit-variance AR(1) with coefficient ``rho_1`` and
the cell's innovation distribution, and ``v = 10/N`` is the exact variance of
(mean of one leg - mean of the other leg) of unit-variance idiosyncratic noise.
The ``1/sqrt(1+v)`` factor normalizes the series to unit variance, so
``delta = sharpe / sqrt(12)`` is the mean that realises an annual Sharpe of
``sharpe`` with unit monthly volatility.

The reduction of the quintile long-short to ``f + u`` is exact in distribution
(the leg means of i.i.d. Gaussian idiosyncratic noise are Gaussian). It is
verified against a literal ``N``-name simulation in the accompanying test.

The innovations are standardized to unit variance: Normal is standard normal;
Student-t with ``df`` degrees of freedom is divided by ``sqrt(df/(df-2))``.

This module performs no I/O, opens no socket and reads no data store.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np
import scipy.stats

__all__ = [
    "MC_SEED",
    "MC_REPLICATIONS",
    "ALPHA",
    "COVERAGE_LEVEL",
    "GRID_T",
    "GRID_RHO",
    "GRID_INNOV",
    "GRID_N",
    "POWER_SHARPES",
    "NULL_ITERATION_ORDER",
    "POWER_ITERATION_ORDER",
    "run_study",
    "size_acceptance",
    "coverage_acceptance",
]

#: Frozen seed literal (PA-0 section 4).
MC_SEED = 20261009

#: Frozen replications per cell (PA-0 section 4).
MC_REPLICATIONS = 20_000

#: Nominal one-sided size.
ALPHA = 0.05

#: Nominal upper-bound coverage level.
COVERAGE_LEVEL = 0.95

GRID_T: tuple[int, ...] = (24, 30, 36, 48, 60)
GRID_RHO: tuple[float, ...] = (0.0, 0.1, 0.2, 0.3)
GRID_INNOV: tuple[str, ...] = ("normal", "t5", "t3")
GRID_N: tuple[int, ...] = (200, 1000)
POWER_SHARPES: tuple[float, ...] = (0.5, 1.0, 1.5)

#: The frozen iteration order in which the single PCG64 stream is consumed.
NULL_ITERATION_ORDER = "T, rho, innovations, N (the PA-0 section 4 grid order)"
POWER_ITERATION_ORDER = "T, rho, innovations, N, annual Sharpe"

#: In-region acceptance thresholds (PA-0 section 4, user-approved).
SIZE_TOLERANCE = 0.065
COVERAGE_TOLERANCE = 0.935
#: Out-of-region prominence threshold (PA-0 section 4).
SIZE_PROMINENCE = 0.10


def _standardised_innovations(
    generator: np.random.Generator, distribution: str, size: tuple[int, ...]
) -> np.ndarray:
    """Draw unit-variance innovations for the named distribution."""
    if distribution == "normal":
        return generator.standard_normal(size)
    if distribution == "t5":
        return generator.standard_t(5, size) / math.sqrt(5.0 / 3.0)
    if distribution == "t3":
        return generator.standard_t(3, size) / math.sqrt(3.0)
    raise ValueError(f"unknown innovation distribution {distribution!r}")


def _ar1_factor(
    generator: np.random.Generator,
    t_periods: int,
    rho: float,
    distribution: str,
    replications: int,
) -> np.ndarray:
    """A stationary, unit-variance AR(1) factor, shape ``(replications, T)``."""
    if rho == 0.0:
        return _standardised_innovations(
            generator, distribution, (replications, t_periods)
        )
    factor = np.empty((replications, t_periods), dtype=np.float64)
    factor[:, 0] = _standardised_innovations(generator, distribution, (replications,))
    scale = math.sqrt(1.0 - rho * rho)
    for column in range(1, t_periods):
        factor[:, column] = (
            rho * factor[:, column - 1]
            + scale
            * _standardised_innovations(generator, distribution, (replications,))
        )
    return factor


def _simulate_cell(
    generator: np.random.Generator,
    *,
    t_periods: int,
    rho: float,
    distribution: str,
    n_names: int,
    delta: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Simulate one cell; return ``(xbar, sd, statistic, p, upper_bound)``."""
    v = 10.0 / n_names
    factor = _ar1_factor(generator, t_periods, rho, distribution, MC_REPLICATIONS)
    idio = generator.standard_normal((MC_REPLICATIONS, t_periods)) * math.sqrt(v)
    series = (delta + factor + idio) / math.sqrt(1.0 + v)
    xbar = series.mean(axis=1)
    sd = series.std(axis=1, ddof=1)
    statistic = math.sqrt(t_periods) * xbar / sd
    p_value = 1.0 - scipy.stats.t.cdf(statistic, t_periods - 1)
    upper_bound = xbar + scipy.stats.t.ppf(
        COVERAGE_LEVEL, t_periods - 1
    ) * sd / math.sqrt(t_periods)
    return xbar, sd, statistic, p_value, upper_bound


def run_study() -> dict[str, list[dict[str, Any]]]:
    """Run the full frozen study and return the size/coverage and power tables.

    ``null`` rows carry the size and upper-bound coverage under ``theta' = 0``;
    ``power`` rows carry the rejection rate under ``theta'`` for the three
    annual-Sharpe values. Deterministic: the same seed and iteration order
    always yield the same numbers.
    """
    generator = np.random.Generator(np.random.PCG64(MC_SEED))
    null_rows: list[dict[str, Any]] = []
    for t_periods in GRID_T:
        for rho in GRID_RHO:
            for distribution in GRID_INNOV:
                for n_names in GRID_N:
                    _, _, _, p_value, upper_bound = _simulate_cell(
                        generator,
                        t_periods=t_periods,
                        rho=rho,
                        distribution=distribution,
                        n_names=n_names,
                        delta=0.0,
                    )
                    detections = int((p_value <= ALPHA).sum())
                    covered = int((upper_bound >= 0.0).sum())
                    null_rows.append(
                        {
                            "T": t_periods,
                            "rho": rho,
                            "innovations": distribution,
                            "N": n_names,
                            "detections": detections,
                            "size": detections / MC_REPLICATIONS,
                            "covered": covered,
                            "coverage": covered / MC_REPLICATIONS,
                        }
                    )

    power_rows: list[dict[str, Any]] = []
    for t_periods in GRID_T:
        for rho in GRID_RHO:
            for distribution in GRID_INNOV:
                for n_names in GRID_N:
                    for sharpe in POWER_SHARPES:
                        delta = sharpe / math.sqrt(12.0)
                        _, _, _, p_value, _ = _simulate_cell(
                            generator,
                            t_periods=t_periods,
                            rho=rho,
                            distribution=distribution,
                            n_names=n_names,
                            delta=delta,
                        )
                        detections = int((p_value <= ALPHA).sum())
                        power_rows.append(
                            {
                                "T": t_periods,
                                "rho": rho,
                                "innovations": distribution,
                                "N": n_names,
                                "sharpe": sharpe,
                                "detections": detections,
                                "power": detections / MC_REPLICATIONS,
                            }
                        )
    return {"null": null_rows, "power": power_rows}


def _in_assumption_region(rho: float, distribution: str) -> bool:
    """``|rho_1| <= 0.2`` and Normal or t5 innovations (PA-0 section 4)."""
    return abs(rho) <= 0.2 and distribution in ("normal", "t5")


def size_acceptance(null_rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Evaluate the frozen in-region size acceptance criterion."""
    violations = [
        row
        for row in null_rows
        if _in_assumption_region(row["rho"], row["innovations"])
        and row["size"] > SIZE_TOLERANCE
    ]
    prominent = [
        row
        for row in null_rows
        if row["size"] > SIZE_PROMINENCE
    ]
    return {
        "tolerance": SIZE_TOLERANCE,
        "in_region_cells": sum(
            1 for row in null_rows if _in_assumption_region(row["rho"], row["innovations"])
        ),
        "violations": violations,
        "passes": not violations,
        "prominent": prominent,
    }


def coverage_acceptance(null_rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Evaluate the frozen in-region upper-bound coverage criterion."""
    violations = [
        row
        for row in null_rows
        if _in_assumption_region(row["rho"], row["innovations"])
        and row["coverage"] < COVERAGE_TOLERANCE
    ]
    return {
        "tolerance": COVERAGE_TOLERANCE,
        "violations": violations,
        "passes": not violations,
    }
