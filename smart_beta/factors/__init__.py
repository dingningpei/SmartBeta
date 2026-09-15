"""Factor and characteristic construction (beta, size, value, momentum, ...).

Phase 1 populates this package with the minimal :class:`Factor` interface
(:mod:`smart_beta.factors.base`) and the concrete beta estimators
(:mod:`smart_beta.factors.beta`). Later phases add size/value/momentum and
other characteristics implementing the same interface.
"""

from smart_beta.factors.base import FACTOR_PANEL_SCHEMA, VALUE_COL, Factor
from smart_beta.factors.beta import dimson_beta, rolling_ols_beta, shrink_beta

__all__ = [
    "FACTOR_PANEL_SCHEMA",
    "VALUE_COL",
    "Factor",
    "dimson_beta",
    "rolling_ols_beta",
    "shrink_beta",
]
