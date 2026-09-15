"""Beta estimators (Phase 1, Task B).

The legacy ``Beta.ipynb`` matrix code had three concrete defects, each of
which this module deliberately fixes:

1. **In-place mutation / zero-filled missing months.** The notebook wrote
   ``rankrt_none = rankrt`` (an alias, not a copy) and then
   ``rankrt_none[np.isnan(rankrt_none)] = 0``, corrupting the shared return
   matrix and treating pre-listing/suspended months as a 0% return. Every
   estimator here copies its inputs before touching them and simply
   *ignores* missing months when fitting.

2. **Zero-initialised output.** The notebook pre-allocated
   ``np.zeros((nr, nc))``, so a stock with no estimate got ``beta = 0`` --
   indistinguishable from a genuinely near-zero beta. Here an un-estimable
   cell is ``NaN``.

3. **Brittle "complete window" requirement.** The notebook only estimated
   when the trailing window had *zero* missing months. Here a window is
   accepted as soon as it contains at least ``settings.beta_min_valid_obs``
   valid ``(stock excess, market excess)`` pairs, counted over whatever
   non-NaN months actually exist in the window.

All public functions return a long-format ``(date, stock_id, value)`` panel
(see :mod:`smart_beta.factors.base`) and never mutate their arguments.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from smart_beta.config.settings import DEFAULT_SETTINGS, Settings
from smart_beta.data.schema import DATE_COL, RETURN_COL, STOCK_COL, validate_panel
from smart_beta.factors.base import FACTOR_PANEL_SCHEMA, VALUE_COL

__all__ = ["rolling_ols_beta", "shrink_beta", "dimson_beta"]


# ---------------------------------------------------------------------------
# Input coercion helpers
# ---------------------------------------------------------------------------


def _as_returns_panel(returns: pd.DataFrame) -> pd.DataFrame:
    """Copy ``returns`` into a minimal, date-normalised long-format frame."""
    if not isinstance(returns, pd.DataFrame):
        raise TypeError(
            f"returns must be a pandas.DataFrame, got {type(returns).__name__}"
        )
    required = {DATE_COL, STOCK_COL, RETURN_COL}
    missing = required - set(returns.columns)
    if missing:
        raise ValueError(f"returns is missing required columns: {sorted(missing)}")

    out = returns[[DATE_COL, STOCK_COL, RETURN_COL]].copy(deep=True)
    out[DATE_COL] = pd.to_datetime(out[DATE_COL])
    out[RETURN_COL] = out[RETURN_COL].astype("float64")
    return out


def _as_date_series(
    obj: pd.Series | pd.DataFrame,
    *,
    value_col: str | None = None,
    name: str = "series",
) -> pd.Series:
    """Coerce a per-date series given as either a ``Series`` or long frame.

    Accepted forms:

    - a ``pandas.Series`` indexed by date (e.g. the synthetic fixture's
      ``ground_truth.market_return``);
    - a long-format ``pandas.DataFrame`` with a ``date`` column and exactly
      one value column (e.g. the frame returned by ``get_risk_free``).

    The result is a float ``Series`` indexed by sorted ``DatetimeIndex``.
    """
    if isinstance(obj, pd.Series):
        s = obj.copy(deep=True).astype("float64")
        s.index = pd.to_datetime(s.index)
        return s.sort_index()

    if isinstance(obj, pd.DataFrame):
        if DATE_COL not in obj.columns:
            raise ValueError(
                f"{name} DataFrame must contain a {DATE_COL!r} column"
            )
        if value_col is None:
            candidates = [c for c in obj.columns if c != DATE_COL]
            if len(candidates) != 1:
                raise ValueError(
                    f"{name} DataFrame must have exactly one value column; "
                    f"found {candidates}"
                )
            value_col = candidates[0]
        if value_col not in obj.columns:
            raise ValueError(f"{name} DataFrame has no column {value_col!r}")
        s = obj.set_index(DATE_COL)[value_col].copy(deep=True).astype("float64")
        s.index = pd.to_datetime(s.index)
        return s.sort_index()

    raise TypeError(
        f"{name} must be a pandas.Series or DataFrame, got {type(obj).__name__}"
    )


def _resolve_settings(settings: Settings) -> tuple[int, int]:
    """Extract and validate the (window, min_valid_obs) pair."""
    window = int(settings.beta_rolling_window_months)
    min_valid = int(settings.beta_min_valid_obs)
    if window < 1:
        raise ValueError("beta_rolling_window_months must be >= 1")
    if min_valid < 1:
        raise ValueError("beta_min_valid_obs must be >= 1")
    if min_valid > window:
        raise ValueError(
            "beta_min_valid_obs cannot exceed beta_rolling_window_months "
            f"({min_valid} > {window})"
        )
    return window, min_valid


def _betas_to_panel(keys: pd.DataFrame, beta_wide: pd.DataFrame) -> pd.DataFrame:
    """Melt a wide ``(date x stock)`` beta matrix back onto the input keys."""
    long = (
        beta_wide.rename_axis(index=DATE_COL, columns=STOCK_COL)
        .reset_index()
        .melt(id_vars=DATE_COL, var_name=STOCK_COL, value_name=VALUE_COL)
    )
    out = keys[[DATE_COL, STOCK_COL]].drop_duplicates().merge(
        long, on=[DATE_COL, STOCK_COL], how="left"
    )
    out = out.sort_values([DATE_COL, STOCK_COL]).reset_index(drop=True)
    validate_panel(out, FACTOR_PANEL_SCHEMA, name="beta panel")
    return out


def _rolling_univariate_beta(
    stock_returns: np.ndarray,
    market_excess: np.ndarray,
    window: int,
    min_valid: int,
) -> np.ndarray:
    """Vectorised trailing-window OLS slope of stock excess on market excess.

    ``stock_returns`` is ``(T, N)`` and ``market_excess`` is ``(T,)``; the
    window for row ``i`` spans the last ``window`` rows up to and including
    ``i`` (an expanding window near the start of the sample). A slope is
    produced for stock ``j`` only when the window contains at least
    ``min_valid`` rows where both series are non-NaN; otherwise it is ``NaN``.
    An intercept is included in the regression, matching the legacy
    ``sm.add_constant`` / ``np.polyfit(deg=1)`` usage.
    """
    n_rows, n_stocks = stock_returns.shape
    betas = np.full((n_rows, n_stocks), np.nan, dtype="float64")

    for i in range(n_rows):
        lo = max(0, i - window + 1)
        xw = market_excess[lo : i + 1]
        yw = stock_returns[lo : i + 1, :]

        valid = np.isfinite(yw) & np.isfinite(xw)[:, None]
        counts = valid.sum(axis=0)
        if not np.any(counts >= min_valid):
            continue

        denom = np.maximum(counts, 1).astype("float64")
        # Masked means: invalid entries contribute nothing.
        x_masked = np.where(valid, xw[:, None], 0.0)
        y_masked = np.where(valid, yw, 0.0)
        x_bar = x_masked.sum(axis=0) / denom
        y_bar = y_masked.sum(axis=0) / denom

        x_dev = np.where(valid, xw[:, None] - x_bar, 0.0)
        y_dev = np.where(valid, yw - y_bar, 0.0)
        cov_xy = (x_dev * y_dev).sum(axis=0)
        var_x = (x_dev * x_dev).sum(axis=0)

        with np.errstate(invalid="ignore", divide="ignore"):
            slope = cov_xy / var_x

        ok = (counts >= min_valid) & (var_x > 0.0) & np.isfinite(slope)
        betas[i, ok] = slope[ok]

    return betas


# ---------------------------------------------------------------------------
# Public estimators
# ---------------------------------------------------------------------------


def rolling_ols_beta(
    returns: pd.DataFrame,
    market_return: pd.Series | pd.DataFrame,
    risk_free: pd.Series | pd.DataFrame,
    settings: Settings = DEFAULT_SETTINGS,
) -> pd.DataFrame:
    """Rolling-window OLS market beta with intercept.

    For every observation ``(date, stock_id)`` in ``returns``, regress the
    stock's excess return on the market's excess return over the trailing
    ``settings.beta_rolling_window_months`` panel dates and report the slope.

    Parameters
    ----------
    returns:
        Long-format return panel with columns ``date, stock_id, ret``.
    market_return:
        Market return per date, as a date-indexed ``Series`` or a long frame
        with a ``date`` column and one value column.
    risk_free:
        Risk-free rate per date, same accepted forms as ``market_return``.
        Must be in the same return units as ``returns``.
    settings:
        Supplies ``beta_rolling_window_months`` and ``beta_min_valid_obs``.

    Returns
    -------
    pandas.DataFrame
        Long-format ``(date, stock_id, value)`` panel with one row per input
        observation. ``value`` is the estimated beta, or ``NaN`` when the
        window had fewer than ``settings.beta_min_valid_obs`` valid pairs.
    """
    window, min_valid = _resolve_settings(settings)

    ret = _as_returns_panel(returns)
    market = _as_date_series(market_return, name="market_return")
    rf = _as_date_series(risk_free, name="risk_free")

    # Align on the market's own calendar; missing rf dates become NaN and
    # simply drop out of the window counts.
    market_excess = (market - rf).rename("market_excess")

    dates = pd.DatetimeIndex(sorted(pd.unique(ret[DATE_COL])))
    stocks = pd.Index(sorted(pd.unique(ret[STOCK_COL])), name=STOCK_COL)

    wide = ret.pivot(index=DATE_COL, columns=STOCK_COL, values=RETURN_COL).reindex(
        index=dates, columns=stocks
    )
    stock_returns = wide.to_numpy(dtype="float64")
    x = market_excess.reindex(dates).to_numpy(dtype="float64")

    beta_matrix = _rolling_univariate_beta(stock_returns, x, window, min_valid)
    beta_wide = pd.DataFrame(beta_matrix, index=dates, columns=stocks)
    return _betas_to_panel(ret, beta_wide)


def shrink_beta(
    beta_panel: pd.DataFrame,
    shrinkage: float = 0.6,
    target: float = 1.0,
) -> pd.DataFrame:
    """Shrink betas toward ``target`` (Frazzini-Pedersen style).

    Applies ``shrunk = shrinkage * beta + (1 - shrinkage) * target``. The
    default ``shrinkage=0.6, target=1.0`` reproduces the Frazzini & Pedersen
    (2014) "Betting Against Beta" shrinkage ``0.6 * beta_hat + 0.4 * 1``.

    Parameters
    ----------
    beta_panel:
        Long-format beta panel with a ``value`` (or ``beta``) column.
    shrinkage:
        Weight placed on the raw estimate, in ``[0, 1]``. ``1`` leaves the
        panel unchanged, ``0`` replaces every estimate with ``target``.
    target:
        Cross-sectional shrinkage target (``1.0`` for market beta).

    Returns
    -------
    pandas.DataFrame
        A copy of ``beta_panel`` with the value column shrunk. ``NaN``
        observations stay ``NaN``.
    """
    shrinkage = float(shrinkage)
    if not 0.0 <= shrinkage <= 1.0:
        raise ValueError(f"shrinkage must be in [0, 1], got {shrinkage}")

    out = beta_panel.copy(deep=True)
    value_col = _value_column(out)
    out[value_col] = shrinkage * out[value_col] + (1.0 - shrinkage) * float(target)
    return out


def dimson_beta(
    returns: pd.DataFrame,
    market_return: pd.Series | pd.DataFrame,
    risk_free: pd.Series | pd.DataFrame,
    lags: int = 1,
    settings: Settings = DEFAULT_SETTINGS,
) -> pd.DataFrame:
    """Dimson (1979) beta: sum of contemporaneous and lagged market slopes.

    Adds ``lags`` lagged market-excess regressors to the rolling regression
    to absorb non-synchronous trading, then reports the sum of the
    contemporaneous and lagged market coefficients.

    Parameters
    ----------
    returns, market_return, risk_free, settings:
        As in :func:`rolling_ols_beta`.
    lags:
        Number of lagged market terms (>= 1).

    Returns
    -------
    pandas.DataFrame
        Long-format ``(date, stock_id, value)`` panel; ``NaN`` where the
        window could not support the regression.

    Notes
    -----
    The synthetic fixture is monthly and its market return is essentially
    i.i.d., so the estimated lag coefficients are near zero and the Dimson
    beta tracks :func:`rolling_ols_beta` closely. The estimator is still
    exercised as a regression test against the same ground truth.
    """
    if int(lags) < 1:
        raise ValueError("lags must be >= 1")
    lags = int(lags)
    window, min_valid = _resolve_settings(settings)
    min_needed = max(min_valid, lags + 2)  # intercept + (lags + 1) regressors

    ret = _as_returns_panel(returns)
    market = _as_date_series(market_return, name="market_return")
    rf = _as_date_series(risk_free, name="risk_free")
    market_excess = (market - rf).rename("market_excess")

    dates = pd.DatetimeIndex(sorted(pd.unique(ret[DATE_COL])))
    stocks = pd.Index(sorted(pd.unique(ret[STOCK_COL])), name=STOCK_COL)

    wide = ret.pivot(index=DATE_COL, columns=STOCK_COL, values=RETURN_COL).reindex(
        index=dates, columns=stocks
    )
    stock_returns = wide.to_numpy(dtype="float64")

    # Lagged market excess on the panel calendar: shift(0) is contemporaneous.
    lagged = [
        market_excess.reindex(dates).shift(k).to_numpy(dtype="float64")
        for k in range(lags + 1)
    ]

    beta_matrix = np.full(stock_returns.shape, np.nan, dtype="float64")
    n_rows = len(dates)
    for i in range(n_rows):
        lo = max(0, i - window + 1)
        yw = stock_returns[lo : i + 1, :]
        xw = np.column_stack([x[lo : i + 1] for x in lagged])
        finite_x = np.isfinite(xw).all(axis=1)
        design = np.column_stack([np.ones(len(yw)), xw])

        valid = finite_x[:, None] & np.isfinite(yw)
        counts = valid.sum(axis=0)
        idx = np.nonzero(counts >= min_needed)[0]
        if idx.size == 0:
            continue

        design_masked = np.where(finite_x[:, None], design, 0.0)
        xtx = np.einsum("wp,wq,wn->npq", design_masked, design_masked, valid.astype("float64"))
        xty = np.einsum("wp,wn->np", design_masked, np.where(valid, yw, 0.0))
        coefs = _batch_solve(xtx[idx], xty[idx])

        # Dimson beta is the sum of the market coefficients, excluding the
        # intercept in column 0.
        dimson = coefs[:, 1:].sum(axis=1)
        good = np.isfinite(dimson)
        beta_matrix[i, idx[good]] = dimson[good]

    beta_wide = pd.DataFrame(beta_matrix, index=dates, columns=stocks)
    return _betas_to_panel(ret, beta_wide)


# ---------------------------------------------------------------------------
# Small internal utilities
# ---------------------------------------------------------------------------


def _value_column(df: pd.DataFrame) -> str:
    """Return the factor value column name, accepting ``value`` or ``beta``."""
    for candidate in (VALUE_COL, "beta"):
        if candidate in df.columns:
            return candidate
    raise ValueError(
        f"beta_panel must contain a {VALUE_COL!r} or 'beta' column; "
        f"got columns {list(df.columns)}"
    )


def _batch_solve(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Solve ``a @ x = b`` for a stack of small systems, robust to singularity."""
    out = np.full((a.shape[0], b.shape[1]), np.nan, dtype="float64")
    if a.shape[0] == 0:
        return out
    try:
        # ``b[..., None]`` makes each right-hand side an explicit column so
        # numpy's batched ``solve`` gufunc signature (m, m), (m, n) is met.
        out[:] = np.linalg.solve(a, b[..., None])[..., 0]
        return out
    except (np.linalg.LinAlgError, ValueError):
        pass
    for j in range(a.shape[0]):
        try:
            out[j] = np.linalg.solve(a[j], b[j])
        except np.linalg.LinAlgError:
            out[j] = np.linalg.lstsq(a[j], b[j], rcond=None)[0]
    return out
