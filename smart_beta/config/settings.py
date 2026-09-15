"""Named, overridable constants for the smart_beta pipelines.

The original notebooks embedded these as bare literals in loop bounds and
slice expressions (e.g. a rolling beta window hardcoded as ``i - 24 : i``,
a momentum lookback hardcoded as ``i - 11``, loops starting at the literal
index ``85`` or ``98`` with no explanation of what that index meant). That
made the code impossible to audit or reuse with different parameters.
Every such constant now lives here, named, with a documented default.

Pass a modified :class:`Settings` instance to a pipeline to override
defaults (e.g. in tests); do not hardcode alternates inline.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # --- Beta estimation ---
    beta_rolling_window_months: int = 24
    beta_min_valid_obs: int = 20  # min non-NaN months required in the window

    # --- Momentum ---
    momentum_lookback_months: int = 11
    momentum_skip_months: int = 1  # skip the most recent month (standard practice)

    # --- Universe construction ---
    min_listing_age_months: int = 12
    bottom_mcap_exclude_pct: float = 0.30  # CH-3 style small-cap exclusion

    # --- Cross-sectional regression / winsorization ---
    winsorize_lower_pct: float = 0.01
    winsorize_upper_pct: float = 0.99

    # --- Portfolio sorts ---
    n_portfolio_groups: int = 5

    # --- Benchmark factor construction ---
    # Arity of the size x characteristic sort used by the FF3/FF5/CH-3/CH-4
    # benchmark portfolios (2 size legs x 3 characteristic legs).
    benchmark_size_legs: int = 2
    benchmark_char_legs: int = 3
    # Trailing window, in months, used to estimate "normal" turnover for the
    # abnormal-turnover sentiment proxy (CH-4).
    turnover_abnormal_window_months: int = 6

    # --- Inference ---
    newey_west_lags: int = 6
    fama_macbeth_min_obs: int = 3  # min cross-sectional obs per regression period

    # --- Implementability ---
    transaction_cost_bps: float = 30.0


DEFAULT_SETTINGS = Settings()
