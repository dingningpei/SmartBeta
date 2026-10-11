"""Independent oracle for the ``STD_X_RANK`` volume box (no ``smart_beta`` imports).

Written from the frozen methodology only: P-Q v2.2 amendment Props. 5-6 say that
``V`` is a ratio of rolling ``dv`` means whose windows both end at session
``f - 1``, and Dataset v1 contract §11 says the D7c bridge applies a mandatory
one-session information lag, so **the factor row dated ``d`` carries the
observation of ``prev(d)``**.  The observation of a session is therefore carried
by the *next* row; the realization at factor row ``f`` uses rows
``f - w + 1 .. f`` and so sees observations through session ``f - 1``.

This module deliberately imports **neither** ``smart_beta`` nor any P-Q module.
It works from per-session observation sequences and is the reference the
production V box is checked against.
"""

from __future__ import annotations

import itertools
import math
from typing import Mapping, Sequence


def bridge_rows(series: Sequence[float]) -> list[float]:
    """The D7c ``prev(d)`` frame rows of an observation series.

    ``series[i]`` is the observation of session ``i``; the returned row ``r``
    carries the observation of session ``r - 1``.  The first row has no
    predecessor and reuses the first observation (it is never inside a
    certified window of the fixtures).
    """

    values = [float(value) for value in series]
    if not values:
        return []
    return [values[0]] + values[:-1]


def rolling_mean(
    observations: Sequence[float], end: int, window: int
) -> float | None:
    """Mean of the ``window`` observations ending at session ``end`` (inclusive).

    ``end`` is a session index; the window is ``(end - window, end]``.  Returns
    ``None`` when the window is not available or is non-finite.
    """

    if window < 1 or end - window + 1 < 0:
        return None
    values = observations[end - window + 1 : end + 1]
    if len(values) != window:
        return None
    if any(value is None or not math.isfinite(float(value)) for value in values):
        return None
    return math.fsum(float(value) for value in values) / window


def v_ratio(
    observations: Sequence[float], f_index: int, w1: int, w2: int
) -> float | None:
    """``V`` at formation row ``f_index``: both windows end at ``f_index - 1``."""

    short = rolling_mean(observations, f_index - 1, w1)
    long = rolling_mean(observations, f_index - 1, w2)
    if short is None or long is None or long == 0.0:
        return None
    return short / long


def v_ratio_from_rows(
    rows: Sequence[float], f_index: int, w1: int, w2: int
) -> float | None:
    """``V`` recomputed from bridge rows: rows ``f-w1+1 .. f`` / ``f-w2+1 .. f``."""

    def mean(window: int) -> float | None:
        if f_index - window + 1 < 0:
            return None
        values = rows[f_index - window + 1 : f_index + 1]
        if any(
            value is None or not math.isfinite(float(value)) for value in values
        ):
            return None
        return math.fsum(float(value) for value in values) / window

    short = mean(w1)
    long = mean(w2)
    if short is None or long is None or long == 0.0:
        return None
    return short / long


def clean_v_boxes(
    observations_by_key: Mapping[str, Sequence[float]],
    f_index: int,
    w1: int,
    w2: int,
) -> dict[str, float]:
    """The oracle's point V box per key at one formation row."""

    out: dict[str, float] = {}
    for key, series in observations_by_key.items():
        value = v_ratio(series, f_index, w1, w2)
        if value is not None:
            out[key] = value
    return out


def suspect_v_interval(
    dv_by_offset: Mapping[int, float],
    short_offsets: Sequence[int],
    long_offsets: Sequence[int],
    kappa_by_offset: Mapping[int, tuple[float, float]],
) -> tuple[float, float] | None:
    """Brute-force the extreme ``V`` over the box of per-cell multipliers.

    ``kappa_by_offset`` maps a dv cell offset to its ``(lo, hi)`` correction
    multiplier; every other cell is fixed at ``1``.  All vertex combinations of
    the box are enumerated and the min/max kept.  This is an independent
    enumeration, not the production closed form.
    """

    short = sorted(short_offsets)
    long = sorted(long_offsets)
    offsets = sorted(set(short) | set(long))
    choices: list[tuple[float, ...]] = []
    for offset in offsets:
        interval = kappa_by_offset.get(offset)
        if interval is None or interval[0] == interval[1]:
            choices.append((interval[0] if interval is not None else 1.0,))
        else:
            choices.append((interval[0], interval[1]))
    n_short = len(short)
    n_long = len(long)
    values: list[float] = []
    for combo in itertools.product(*choices):
        kappa = dict(zip(offsets, combo))
        short_sum = math.fsum(
            dv_by_offset.get(offset, 0.0) * kappa[offset] for offset in short
        )
        long_sum = math.fsum(
            dv_by_offset.get(offset, 0.0) * kappa[offset] for offset in long
        )
        if long_sum <= 0.0:
            continue
        values.append((n_long / n_short) * short_sum / long_sum)
    if not values:
        return None
    return min(values), max(values)


def average_rank(values: Sequence[float]) -> list[float]:
    """``rank(method="average")`` of ``values`` (ties share the midpoint)."""

    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    position = 0
    while position < len(order):
        end = position
        while (
            end + 1 < len(order)
            and values[order[end + 1]] == values[order[position]]
        ):
            end += 1
        share = (position + end) / 2.0 + 1.0
        for index in order[position : end + 1]:
            ranks[index] = share
        position = end + 1
    return ranks
