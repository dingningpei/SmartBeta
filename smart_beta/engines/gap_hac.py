"""Gap-aware Newey-West HAC mean inference (PQC, Dataset v1.2).

Dataset-v1.2 P-Q adds exactly one narrow capability at the inference layer:
the mean and Newey-West (Bartlett) t-statistic of a series that lives on an
**original formation-time index with explicit gaps** (PQC, frozen spec
``worker_tasks/dataset_v1/task-pqc-gap-aware-hac.md``).

Design constraints, all frozen:

- The lag policy is unchanged: the fixed ``DEFAULT_SETTINGS.newey_west_lags``
  (currently 6).  The lag is *never* inferred from the horizon ``h``.
- Lag pairs are formed **only** when the two available observations sit at an
  exact original index distance ``k`` (``1 <= k <= L``).  No pair is formed
  by adjacency after dropping gaps.
- The estimator fails closed with a typed status and a NaN ``t_stat`` on
  insufficient support, non-finite values, zero range and non-positive /
  non-finite long-run variance.  There is no fallback estimator, no kernel
  substitution and no truncation.
- The function is pure: it never mutates its input and never reorders it, and
  its output does not depend on timezone or locale.

This module deliberately does **not** import or call
:func:`smart_beta.engines.inference.newey_west_ols`: the compressed-time path
must never produce a result here.  Parity with that estimator for gap-free
input is the caller's/test suite's concern.
"""

from __future__ import annotations

from dataclasses import dataclass
from numbers import Integral
from typing import Sequence

import numpy as np

from smart_beta.config.settings import DEFAULT_SETTINGS

__all__ = ["GapAwareHACResult", "gap_aware_mean_tstat"]

STATUS_OK = "OK"
STATUS_INSUFFICIENT_SUPPORT = "INSUFFICIENT_SUPPORT"
STATUS_ZERO_RANGE = "ZERO_RANGE"
STATUS_NONPOSITIVE_VARIANCE = "NONPOSITIVE_VARIANCE"
STATUS_NONFINITE = "NONFINITE"

# The small-sample factor ``c`` in ``Var = c * S / n**2``.  ``newey_west_ols``
# (statsmodels OLS with ``cov_type="HAC"``) applies ``c = 1`` for a single
# regressor; the frozen parity definition pins this value.
_SMALL_SAMPLE_C = 1.0


@dataclass(frozen=True)
class GapAwareHACResult:
    """Result of :func:`gap_aware_mean_tstat` on a gapped index series."""

    mean: float
    t_stat: float
    n_obs: int
    n_index: int
    n_interior_gaps: int
    status: str
    lags: int


def _resolve_lags(lags: int | None) -> int:
    """Return the effective lag count, validating an explicit override."""
    if lags is None:
        return int(DEFAULT_SETTINGS.newey_west_lags)
    if isinstance(lags, bool) or not isinstance(lags, Integral):
        raise TypeError(f"lags must be an int >= 0 or None, got {lags!r}")
    resolved = int(lags)
    if resolved < 0:
        raise ValueError(f"lags must be non-negative, got {resolved}")
    return resolved


def _as_index_array(values_on_index: Sequence[float]) -> np.ndarray:
    """Coerce to a contiguous 1-D float64 copy, rejecting pandas and 2-D."""
    if type(values_on_index).__module__.split(".")[0] == "pandas":
        raise TypeError(
            "pandas input is not accepted; convert it to a NumPy array first"
        )
    arr = np.asarray(values_on_index, dtype="float64")
    if arr.ndim > 1:
        raise ValueError(
            f"values_on_index must be one-dimensional, got shape {arr.shape}"
        )
    return np.ravel(arr)


def gap_aware_mean_tstat(
    values_on_index: Sequence[float],
    *,
    lags: int | None = None,
) -> GapAwareHACResult:
    """Mean and gap-aware Newey-West t-statistic of an indexed series.

    ``values_on_index[i]`` is the value at original index position ``i``
    (``0 .. T-1``); ``NaN`` or ``+/-inf`` marks a gap.  The caller (PQB)
    builds the array from the original schedule and PQC never reorders it.

    Parameters
    ----------
    values_on_index:
        The series on its original index.
    lags:
        Bartlett lag count.  ``None`` uses
        :data:`smart_beta.config.settings.DEFAULT_SETTINGS.newey_west_lags`.
        An explicit value is allowed only for tests and must be an ``int``
        ``>= 0``.
    """
    resolved_lags = _resolve_lags(lags)
    arr = _as_index_array(values_on_index)
    n_index = int(arr.size)

    finite = np.isfinite(arr)
    positions = np.flatnonzero(finite)
    n_obs = int(positions.size)

    if n_obs == 0:
        n_interior_gaps = 0
    else:
        first = int(positions[0])
        last = int(positions[-1])
        n_interior_gaps = (last - first + 1) - n_obs

    if n_obs < 2:
        return GapAwareHACResult(
            float("nan"),
            float("nan"),
            n_obs,
            n_index,
            n_interior_gaps,
            STATUS_INSUFFICIENT_SUPPORT,
            resolved_lags,
        )

    available = arr[positions]
    mean = float(available.mean())
    if not np.isfinite(mean):
        return GapAwareHACResult(
            float("nan"),
            float("nan"),
            n_obs,
            n_index,
            n_interior_gaps,
            STATUS_NONFINITE,
            resolved_lags,
        )

    if float(np.ptp(available)) == 0.0:
        return GapAwareHACResult(
            mean,
            float("nan"),
            n_obs,
            n_index,
            n_interior_gaps,
            STATUS_ZERO_RANGE,
            resolved_lags,
        )

    # Residuals on the original index; gap positions carry 0 and are excluded
    # by the ``finite`` mask when lag pairs are formed.
    residuals = np.zeros(n_index, dtype="float64")
    residuals[positions] = available - mean

    gamma_0 = float(np.dot(residuals, residuals))
    long_run = gamma_0
    for k in range(1, resolved_lags + 1):
        if k >= n_index:
            break
        weight = 1.0 - k / (resolved_lags + 1.0)
        left = residuals[: n_index - k]
        right = residuals[k:]
        mask = finite[: n_index - k] & finite[k:]
        gamma_k = float(np.dot(left[mask], right[mask]))
        long_run += 2.0 * weight * gamma_k

    variance = _SMALL_SAMPLE_C * long_run / (n_obs**2)
    if long_run <= 0.0 or not np.isfinite(long_run) or not np.isfinite(variance):
        return GapAwareHACResult(
            mean,
            float("nan"),
            n_obs,
            n_index,
            n_interior_gaps,
            STATUS_NONPOSITIVE_VARIANCE,
            resolved_lags,
        )

    t_stat = float(mean / np.sqrt(variance))
    if not np.isfinite(t_stat):
        return GapAwareHACResult(
            mean,
            float("nan"),
            n_obs,
            n_index,
            n_interior_gaps,
            STATUS_NONPOSITIVE_VARIANCE,
            resolved_lags,
        )

    return GapAwareHACResult(
        mean,
        t_stat,
        n_obs,
        n_index,
        n_interior_gaps,
        STATUS_OK,
        resolved_lags,
    )
