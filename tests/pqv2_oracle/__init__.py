"""Independent brute-force P-Q v2 oracle (test-only).

This package intentionally imports **nothing** from
``smart_beta.dataset_v1.pqv2_*`` (or any production module).  It recomputes
production-equivalent semantics from primitive synthetic data (closes,
corporate-action events, volumes) using its own numpy/pandas kernels.  The
independence is enforced by an AST test in
``tests/test_pqv2_oracle_self.py`` and again at the PQ2-I9 barrier.
"""

from .oracle import (
    CASES,
    BRANCH_CA,
    BRANCH_CLOSE_PREV,
    BRANCH_CLOSE_S,
    BRANCH_NONE,
    CaEvent,
    Expr,
    FlagState,
    Stock,
    World,
    assign_groups,
    enumerate_states,
    factor_values,
    label_extremes,
    lag_frame,
    ls_min,
    rank_average,
    reachable_assignments,
    rolling_mean,
    rolling_std,
    standardize,
    s2_relative,
    turnover_max,
    turnover_series,
    winsorize,
)

__all__ = [
    "CASES",
    "BRANCH_CA",
    "BRANCH_CLOSE_PREV",
    "BRANCH_CLOSE_S",
    "BRANCH_NONE",
    "CaEvent",
    "Expr",
    "FlagState",
    "Stock",
    "World",
    "assign_groups",
    "enumerate_states",
    "factor_values",
    "label_extremes",
    "lag_frame",
    "ls_min",
    "rank_average",
    "reachable_assignments",
    "rolling_mean",
    "rolling_std",
    "standardize",
    "s2_relative",
    "turnover_max",
    "turnover_series",
    "winsorize",
]
