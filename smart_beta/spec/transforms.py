"""Numeric transform library for the Phase 6 trusted expression engine (P6-E).

This module is the *execution* half of the frozen Phase 6 transform
vocabulary. :mod:`smart_beta.spec.expression` (P6-C) defines the immutable
AST and rejects everything outside the section 6 whitelist; this module
applies exactly those whitelisted transforms to inputs that the trusted
infrastructure has **already supplied and already aligned**.

Scope boundary (frozen)
-----------------------

The transforms operate **only** on already-supplied, already-aligned,
trusted inputs. This module performs *no* point-in-time work of any kind:

* no knowledge-date or vintage selection;
* no universe construction or screening;
* no formation-date alignment or return alignment;
* no temporal join, merge, or reindex across two differently-indexed panels;
* no provider, vendor, network, filesystem, or environment access;
* no ``eval``/``exec``/``compile`` and no user-supplied executable code.

If two operands are not aligned on exactly the same observation dates and the
same stock identifiers, :func:`add`/:func:`subtract`/:func:`multiply`/
:func:`divide` **fail closed** with :class:`TransformError`. They never
silently reindex, forward-fill, or substitute a value; alignment is owned by
the trusted boundary (Phase 6 plan, section 8).

Value-frame representation
--------------------------

A *value frame* is a :class:`pandas.DataFrame` whose

* index is the unique, ordered sequence of **observation dates** (one row per
  date), and
* columns are the unique **stock identifiers** (one column per stock),

with a numeric ``float64`` value in every cell. A missing observation is
``NaN`` -- never ``0`` and never a silently dropped row/column. This is the
rectangular, already-aligned form of the long ``date | stock_id | value``
panel used elsewhere in the repository; :func:`value_frame_from_long` and
:func:`value_frame_to_long` are pure reshaping helpers for that panel.

Cross-sectional versus time-series boundary (frozen, explicit)
--------------------------------------------------------------

The two transform families act on **different axes** and are never
interchangeable:

* **time-series** transforms (``lag``, rolling ``mean/sum/std/min/max``) act
  down the date axis, independently per stock (``axis=0``);
* **cross-sectional** transforms (``rank``, ``winsorize``, ``standardize``)
  act across the stock axis, independently per date (``axis=1``).

The structural rule that a cross-sectional transform may never feed a
time-series transform (alignment order) is enforced earlier by
:func:`smart_beta.spec.expression.validate_expression`; this library
implements each family on its own axis so that a validated tree executes
with the intended semantics.

Frozen numeric semantics (Phase 6 plan, section 7)
--------------------------------------------------

* **Trailing windows only.** Every rolling window ends at the observation
  date and looks strictly backwards. Windows are never centred and never
  include a future observation. A full window of ``window`` observations is
  required before a value is produced (``min_periods == window``); a partial
  window yields ``NaN``. This is the conservative reading of "over N
  periods" and avoids presenting a short-window estimate as if it satisfied
  the declared lookback.
* **NaN propagation.** ``NaN`` propagates through arithmetic, lag, rolling
  and cross-sectional transforms. A rolling window containing a ``NaN`` does
  not silently shrink, and a cross-sectional statistic ignores ``NaN``
  members but is itself ``NaN`` when it is undefined.
* **Division by zero yields ``NaN``** (and never ``inf``/``-inf`` and never
  an exception that aborts execution): ``x / 0``, ``0 / 0`` and any
  non-finite division result all map to ``NaN``. The declared
  ``missing_policy`` (``propagate`` / ``drop``) is applied by the trusted
  evaluator at the output stage, not here.
* **Deterministic ordering.** Every kernel validates, then normalizes its
  input to a ``float64`` frame with the index (dates) and columns (stocks)
  sorted ascending, so equal inputs always produce bit-identical output
  regardless of input row/column order.
* **Defined statistics.** ``rolling std`` is the sample standard deviation
  (``ddof=1``, pandas' default) and cross-sectional ``standardize`` uses the
  population standard deviation (``ddof=0``); ties in ``rank`` receive the
  average rank; ``winsorize`` uses linear quantile interpolation. These
  conventions are explicit parameters with the documented defaults rather
  than implicit pandas behaviour.

Typed failure
-------------

Every malformed input or unsupported application raises
:class:`TransformError`, a subclass of the shared
:class:`smart_beta.spec.expression.EvaluationError`, so the transform layer
stays inside the frozen section 7 error taxonomy.
"""

from __future__ import annotations

import math
import operator
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from smart_beta.data.schema import DATE_COL, STOCK_COL, VALUE_COL
from smart_beta.spec.expression import (
    Add,
    CrossSectional,
    CrossSectionalOp,
    Div,
    EvaluationError,
    Expr,
    Field,
    Lag,
    Literal,
    Mul,
    Rolling,
    RollingOp,
    Sub,
)

__all__ = [
    # typed error
    "TransformError",
    # value-frame helpers
    "normalize_value_frame",
    "value_frame_from_long",
    "value_frame_to_long",
    # arithmetic
    "add",
    "subtract",
    "multiply",
    "divide",
    # time-series
    "lag_values",
    "rolling",
    "rolling_mean",
    "rolling_sum",
    "rolling_std",
    "rolling_min",
    "rolling_max",
    # cross-sectional
    "cross_sectional",
    "cross_sectional_rank",
    "cross_sectional_winsorize",
    "cross_sectional_standardize",
    # single-node dispatch
    "apply_transform",
    # frozen kernel registries
    "ROLLING_KERNELS",
    "CROSS_SECTIONAL_KERNELS",
]


# ---------------------------------------------------------------------------
# typed error
# ---------------------------------------------------------------------------
class TransformError(EvaluationError):
    """An already-aligned input frame or a transform application is invalid.

    Subclass of the shared :class:`~smart_beta.spec.expression.EvaluationError`
    so the transform layer uses the frozen section 7 error namespace. It is
    fail-closed: an unusable operand is reported, never patched over.
    """


# ---------------------------------------------------------------------------
# internal constants / small guards
# ---------------------------------------------------------------------------
_NUMERIC_DTYPE = "float64"


def _is_scalar(value: Any) -> bool:
    """True for a real Python/numpy scalar (never a ``bool``)."""
    if isinstance(value, (bool, np.bool_)):
        return False
    return isinstance(value, (int, float, np.integer, np.floating))


def _ensure_finite_scalar(value: Any, what: str) -> float:
    if isinstance(value, (bool, np.bool_)):
        raise TransformError(f"{what} must be a real number, got {value!r}")
    if not isinstance(value, (int, float, np.integer, np.floating)):
        raise TransformError(
            f"{what} must be a real number, got {type(value).__name__}"
        )
    return float(value)


def _finite_or_nan(value: Any) -> Any:
    """Map every non-finite entry to ``NaN`` (never ``inf``/``-inf``)."""
    if isinstance(value, pd.DataFrame):
        frame = value.astype(_NUMERIC_DTYPE)
        finite = np.isfinite(frame.to_numpy(dtype=_NUMERIC_DTYPE))
        return frame.where(finite)
    number = float(value)
    return number if math.isfinite(number) else float("nan")


def _rolling_op(op: Any) -> RollingOp:
    if isinstance(op, RollingOp):
        return op
    if isinstance(op, str):
        try:
            return RollingOp(op)
        except ValueError as exc:
            raise TransformError(
                f"rolling operation {op!r} is not in the frozen section 6 "
                f"whitelist (allowed: {sorted(m.value for m in RollingOp)})"
            ) from exc
    raise TransformError(
        "rolling operation must be a RollingOp or a string, got "
        f"{type(op).__name__}"
    )


def _cross_sectional_op(op: Any) -> CrossSectionalOp:
    if isinstance(op, CrossSectionalOp):
        return op
    if isinstance(op, str):
        try:
            return CrossSectionalOp(op)
        except ValueError as exc:
            raise TransformError(
                f"cross-sectional operation {op!r} is not in the frozen "
                "section 6 whitelist (allowed: "
                f"{sorted(m.value for m in CrossSectionalOp)})"
            ) from exc
    raise TransformError(
        "cross-sectional operation must be a CrossSectionalOp or a string, "
        f"got {type(op).__name__}"
    )


def _check_window(window: Any) -> int:
    if isinstance(window, (bool, np.bool_)) or not isinstance(
        window, (int, np.integer)
    ):
        raise TransformError(
            f"rolling window must be an integer, got {type(window).__name__}"
        )
    window = int(window)
    if window < 1:
        raise TransformError(f"rolling window must be >= 1, got {window}")
    return window


def _check_ddof(ddof: Any) -> int:
    if isinstance(ddof, (bool, np.bool_)) or not isinstance(ddof, (int, np.integer)):
        raise TransformError(f"ddof must be an integer, got {type(ddof).__name__}")
    ddof = int(ddof)
    if ddof < 0:
        raise TransformError(f"ddof must be non-negative, got {ddof}")
    return ddof


def _check_periods(periods: Any) -> int:
    if isinstance(periods, (bool, np.bool_)) or not isinstance(
        periods, (int, np.integer)
    ):
        raise TransformError(
            f"lag periods must be an integer, got {type(periods).__name__}"
        )
    periods = int(periods)
    if periods < 0:
        raise TransformError(
            "lag periods must be non-negative; a negative lag is a "
            "future/look-ahead reference and is not expressible (section 6)"
        )
    return periods


# ---------------------------------------------------------------------------
# value-frame normalization and long/wide reshaping
# ---------------------------------------------------------------------------
def normalize_value_frame(
    frame: Any, *, name: str = "value frame"
) -> pd.DataFrame:
    """Validate an already-aligned value frame and return a sorted ``float64`` copy.

    The result has a unique, ascending-sorted index (observation dates) and
    unique, ascending-sorted columns (stock identifiers), so downstream
    kernels are deterministic regardless of the caller's row/column order.
    Duplicate keys, non-numeric columns, non-frame inputs and unsortable
    axes fail closed with :class:`TransformError`.
    """
    if not isinstance(frame, pd.DataFrame):
        raise TransformError(
            f"{name} must be a pandas DataFrame, got {type(frame).__name__}"
        )
    if frame.index.has_duplicates:
        raise TransformError(
            f"{name} index has duplicate observation dates; an aligned value "
            "frame has exactly one row per date"
        )
    if frame.columns.has_duplicates:
        raise TransformError(
            f"{name} columns have duplicate stock identifiers; an aligned "
            "value frame has exactly one column per stock"
        )
    non_numeric = [
        column
        for column in frame.columns
        if not pd.api.types.is_numeric_dtype(frame[column].dtype)
    ]
    if non_numeric:
        raise TransformError(
            f"{name} has non-numeric columns {non_numeric!r}; every frozen "
            "section 6 transform is numeric-only"
        )
    try:
        normalized = frame.astype(_NUMERIC_DTYPE)
        normalized = normalized.sort_index(axis=0).sort_index(axis=1)
    except TypeError as exc:  # unsortable / uncastable axis
        raise TransformError(
            f"{name} could not be normalized to a numeric, sorted value "
            f"frame: {exc}"
        ) from exc
    return normalized


def value_frame_from_long(
    long: Any,
    *,
    date_col: str = DATE_COL,
    stock_col: str = STOCK_COL,
    value_col: str = VALUE_COL,
) -> pd.DataFrame:
    """Reshape a long ``date | stock_id | value`` panel into a value frame.

    This is a pure reshaping helper -- it performs no alignment, no
    forward-fill and no temporal selection. Duplicate ``(date, stock_id)``
    keys fail closed, as does a non-numeric value column.
    """
    if not isinstance(long, pd.DataFrame):
        raise TransformError(
            f"long panel must be a pandas DataFrame, got {type(long).__name__}"
        )
    missing = [
        column
        for column in (date_col, stock_col, value_col)
        if column not in long.columns
    ]
    if missing:
        raise TransformError(f"long panel is missing required columns {missing}")
    if long.duplicated(subset=[date_col, stock_col]).any():
        raise TransformError(
            f"long panel has duplicate rows on ({date_col!r}, {stock_col!r}); "
            "an aligned panel has one value per (date, stock_id)"
        )
    wide = long.pivot(index=date_col, columns=stock_col, values=value_col)
    # The value-frame convention uses unnamed axes; the caller's long-panel
    # column names are not part of the frame's identity.
    wide = wide.rename_axis(index=None, columns=None)
    return normalize_value_frame(wide, name="long panel")


def value_frame_to_long(
    wide: Any,
    *,
    date_col: str = DATE_COL,
    stock_col: str = STOCK_COL,
    value_col: str = VALUE_COL,
) -> pd.DataFrame:
    """Reshape a value frame into a long ``date | stock_id | value`` panel.

    The result is deterministically ordered by ``(date, stock_id)`` with a
    fresh ``RangeIndex``. ``NaN`` cells are kept (a not-estimable observation
    is represented by ``NaN``, never by a dropped row).
    """
    frame = normalize_value_frame(wide, name="value frame")
    if len(frame.columns) == 0:
        # No stocks -> the long panel is empty (the date axis alone carries no
        # observations).
        return pd.DataFrame(
            {
                date_col: pd.Series(dtype=frame.index.dtype),
                stock_col: pd.Series(dtype="object"),
                value_col: pd.Series(dtype=_NUMERIC_DTYPE),
            }
        )
    stacked = frame.stack()
    stacked.index = stacked.index.set_names([date_col, stock_col])
    out = stacked.rename(value_col).reset_index()
    out = out.sort_values([date_col, stock_col], kind="mergesort")
    return out.reset_index(drop=True)


# ---------------------------------------------------------------------------
# arithmetic (elementwise, aligned operands only)
# ---------------------------------------------------------------------------
def _binary_operands(left: Any, right: Any) -> tuple[Any, Any]:
    """Return two operands that are scalars or frames sharing one axis grid."""
    left_scalar = _is_scalar(left)
    right_scalar = _is_scalar(right)
    if left_scalar:
        left = _ensure_finite_scalar(left, "left operand")
    else:
        left = normalize_value_frame(left, name="left operand")
    if right_scalar:
        right = _ensure_finite_scalar(right, "right operand")
    else:
        right = normalize_value_frame(right, name="right operand")
    if not left_scalar and not right_scalar:
        if not left.index.equals(right.index):
            raise TransformError(
                "binary operands must share exactly the same observation "
                "dates; alignment is owned by the trusted layer and is never "
                "performed (or silently reindexed) by a transform"
            )
        if not left.columns.equals(right.columns):
            raise TransformError(
                "binary operands must share exactly the same stock "
                "identifiers; alignment is owned by the trusted layer and is "
                "never performed (or silently reindexed) by a transform"
            )
    return left, right


def _combine(op: Any, left: Any, right: Any) -> Any:
    left, right = _binary_operands(left, right)
    if op is operator.truediv:
        with np.errstate(divide="ignore", invalid="ignore"):
            result = left / right
        return _finite_or_nan(result)
    return op(left, right)


def add(left: Any, right: Any) -> Any:
    """Whitelisted ``left + right`` over aligned numeric operands."""
    return _combine(operator.add, left, right)


def subtract(left: Any, right: Any) -> Any:
    """Whitelisted ``left - right`` (the "difference") over aligned operands."""
    return _combine(operator.sub, left, right)


def multiply(left: Any, right: Any) -> Any:
    """Whitelisted ``left * right`` over aligned numeric operands."""
    return _combine(operator.mul, left, right)


def divide(numerator: Any, denominator: Any) -> Any:
    """Whitelisted ``numerator / denominator``; division by zero yields ``NaN``.

    ``x / 0``, ``0 / 0`` and any non-finite quotient are all mapped to
    ``NaN`` -- never ``inf`` and never an exception (section 7).
    """
    return _combine(operator.truediv, numerator, denominator)


# ---------------------------------------------------------------------------
# time-series transforms (axis=0: down dates, independently per stock)
# ---------------------------------------------------------------------------
def lag_values(frame: Any, periods: int) -> pd.DataFrame:
    """Explicit lag-by-N of an aligned value frame.

    The shift is by ``periods`` observation dates (not calendar units) and is
    strictly trailing: a positive lag can only ever reference an *earlier*
    date. ``periods == 0`` is the identity. A negative lag fails closed.
    """
    periods = _check_periods(periods)
    operand = normalize_value_frame(frame, name="lag operand")
    if periods == 0:
        return operand
    return operand.shift(periods, axis=0)


def rolling(
    frame: Any,
    op: Any,
    window: int,
    *,
    min_periods: int | None = None,
    ddof: int = 1,
) -> pd.DataFrame:
    """Trailing rolling transform over ``window`` observation dates.

    The window ends at the observation date and looks strictly backwards
    (``center=False``); it never includes a future observation and is never
    centred. ``min_periods`` defaults to ``window``, so a partial window
    yields ``NaN`` rather than a short-window estimate.
    """
    operand = normalize_value_frame(frame, name="rolling operand")
    op = _rolling_op(op)
    window = _check_window(window)
    if min_periods is None:
        min_periods = window
    if isinstance(min_periods, (bool, np.bool_)) or not isinstance(
        min_periods, (int, np.integer)
    ):
        raise TransformError(
            f"min_periods must be an integer, got {type(min_periods).__name__}"
        )
    min_periods = int(min_periods)
    if min_periods < 1 or min_periods > window:
        raise TransformError(
            f"min_periods must be between 1 and the window {window}, got "
            f"{min_periods}"
        )
    rolled = operand.rolling(window=window, min_periods=min_periods, center=False)
    if op is RollingOp.MEAN:
        return rolled.mean()
    if op is RollingOp.SUM:
        return rolled.sum()
    if op is RollingOp.MIN:
        return rolled.min()
    if op is RollingOp.MAX:
        return rolled.max()
    return rolled.std(ddof=_check_ddof(ddof))


def rolling_mean(frame: Any, window: int, **kwargs: Any) -> pd.DataFrame:
    """Trailing rolling mean over ``window`` periods."""
    return rolling(frame, RollingOp.MEAN, window, **kwargs)


def rolling_sum(frame: Any, window: int, **kwargs: Any) -> pd.DataFrame:
    """Trailing rolling sum over ``window`` periods."""
    return rolling(frame, RollingOp.SUM, window, **kwargs)


def rolling_std(frame: Any, window: int, **kwargs: Any) -> pd.DataFrame:
    """Trailing rolling sample standard deviation over ``window`` periods."""
    return rolling(frame, RollingOp.STD, window, **kwargs)


def rolling_min(frame: Any, window: int, **kwargs: Any) -> pd.DataFrame:
    """Trailing rolling minimum over ``window`` periods."""
    return rolling(frame, RollingOp.MIN, window, **kwargs)


def rolling_max(frame: Any, window: int, **kwargs: Any) -> pd.DataFrame:
    """Trailing rolling maximum over ``window`` periods."""
    return rolling(frame, RollingOp.MAX, window, **kwargs)


# ---------------------------------------------------------------------------
# cross-sectional transforms (axis=1: across stocks, independently per date)
# ---------------------------------------------------------------------------
def cross_sectional_rank(frame: Any) -> pd.DataFrame:
    """Per-date cross-sectional rank across stocks (average rank for ties).

    ``NaN`` cells are excluded from the ranking and remain ``NaN`` in the
    output (``na_option="keep"``); they are never ranked as if they were a
    small/large value.
    """
    operand = normalize_value_frame(frame, name="rank operand")
    ranked = operand.rank(axis=1, method="average", na_option="keep")
    return ranked.astype(_NUMERIC_DTYPE)


def cross_sectional_winsorize(
    frame: Any, lower: float, upper: float
) -> pd.DataFrame:
    """Per-date cross-sectional winsorization at explicit quantile bounds.

    Values below the ``lower`` quantile are set to that quantile and values
    above the ``upper`` quantile to that quantile; interior values are
    unchanged. ``NaN`` cells do not contribute to the quantiles and stay
    ``NaN``. Bounds are required, explicit and validated
    (``0 <= lower < upper <= 1``).
    """
    operand = normalize_value_frame(frame, name="winsorize operand")
    lower = _ensure_finite_scalar(lower, "winsorize lower bound")
    upper = _ensure_finite_scalar(upper, "winsorize upper bound")
    if not 0.0 <= lower < upper <= 1.0:
        raise TransformError(
            "winsorize bounds must satisfy 0 <= lower < upper <= 1, got "
            f"({lower}, {upper})"
        )
    low = operand.quantile(lower, axis=1, interpolation="linear")
    high = operand.quantile(upper, axis=1, interpolation="linear")
    return operand.clip(lower=low, upper=high, axis=0)


def cross_sectional_standardize(frame: Any, *, ddof: int = 0) -> pd.DataFrame:
    """Per-date cross-sectional z-score across stocks (population std).

    Computes ``(x - row mean) / (row std)`` per date. When the row standard
    deviation is zero or undefined (a degenerate or single-name
    cross-section) the result is ``NaN``, never ``inf`` -- the same
    division-by-zero policy as :func:`divide`. ``ddof`` defaults to ``0``
    (population standard deviation).
    """
    ddof = _check_ddof(ddof)
    operand = normalize_value_frame(frame, name="standardize operand")
    mean = operand.mean(axis=1)
    std = operand.std(axis=1, ddof=ddof)
    centered = operand.sub(mean, axis=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        result = centered.div(std, axis=0)
    return _finite_or_nan(result)


def cross_sectional(
    frame: Any,
    op: Any,
    *,
    bounds: tuple[float, float] | None = None,
    ddof: int = 0,
) -> pd.DataFrame:
    """Dispatch a frozen cross-sectional operation on an aligned value frame."""
    op = _cross_sectional_op(op)
    if op is CrossSectionalOp.RANK:
        if bounds is not None:
            raise TransformError("rank does not accept bounds")
        return cross_sectional_rank(frame)
    if op is CrossSectionalOp.WINSORIZE:
        if bounds is None:
            raise TransformError(
                "winsorize requires explicit (lower, upper) quantile bounds"
            )
        return cross_sectional_winsorize(frame, bounds[0], bounds[1])
    if bounds is not None:
        raise TransformError("standardize does not accept bounds")
    return cross_sectional_standardize(frame, ddof=ddof)


# ---------------------------------------------------------------------------
# single-node dispatch (one step; the evaluator P6-D owns recursion)
# ---------------------------------------------------------------------------
ROLLING_KERNELS: Mapping[RollingOp, Any] = {
    RollingOp.MEAN: rolling_mean,
    RollingOp.SUM: rolling_sum,
    RollingOp.STD: rolling_std,
    RollingOp.MIN: rolling_min,
    RollingOp.MAX: rolling_max,
}
"""Every frozen rolling op mapped to its kernel (the vocabulary is total)."""

CROSS_SECTIONAL_KERNELS: Mapping[CrossSectionalOp, Any] = {
    CrossSectionalOp.RANK: cross_sectional_rank,
    CrossSectionalOp.WINSORIZE: cross_sectional_winsorize,
    CrossSectionalOp.STANDARDIZE: cross_sectional_standardize,
}
"""Every frozen cross-sectional op mapped to its kernel."""


def _operands(node: Expr, operands: Sequence[Any], expected: int) -> tuple[Any, ...]:
    operands = tuple(operands)
    if len(operands) != expected:
        raise TransformError(
            f"{type(node).__name__} expects {expected} operand(s), got "
            f"{len(operands)}"
        )
    return operands


def apply_transform(node: Any, operands: Sequence[Any] = ()) -> Any:
    """Apply a single frozen transform node to already-evaluated operands.

    ``operands`` are the values of ``node.children()`` in order: a
    :class:`~smart_beta.spec.expression.Literal` yields a scalar and every
    other node yields a value frame. This is deliberately **one step** -- the
    trusted evaluator (P6-D) owns recursion, input lookup, the declared
    ``missing_policy`` and output shaping. A :class:`Field` operand is
    resolved by the evaluator, so :func:`apply_transform` rejects it.
    """
    if not isinstance(node, Expr):
        raise TransformError(
            f"transform node must be one of the frozen section 6 node types, "
            f"got {type(node).__name__}"
        )
    if isinstance(node, Literal):
        _operands(node, operands, 0)
        return float(node.value)
    if isinstance(node, Field):
        raise TransformError(
            "a raw field reference is resolved by the trusted evaluator, not "
            "by the transform library"
        )
    if isinstance(node, Add):
        left, right = _operands(node, operands, 2)
        return add(left, right)
    if isinstance(node, Sub):
        left, right = _operands(node, operands, 2)
        return subtract(left, right)
    if isinstance(node, Mul):
        left, right = _operands(node, operands, 2)
        return multiply(left, right)
    if isinstance(node, Div):
        left, right = _operands(node, operands, 2)
        return divide(left, right)
    if isinstance(node, Lag):
        (operand,) = _operands(node, operands, 1)
        return lag_values(operand, node.periods)
    if isinstance(node, Rolling):
        (operand,) = _operands(node, operands, 1)
        return ROLLING_KERNELS[node.op](operand, node.window)
    if isinstance(node, CrossSectional):
        (operand,) = _operands(node, operands, 1)
        return cross_sectional(operand, node.op, bounds=node.bounds)
    raise TransformError(  # pragma: no cover - frozen node set is exhaustive
        f"node type {type(node).__name__} is not a frozen section 6 transform"
    )
