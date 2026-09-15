"""Orchestration layer: composes DataSource, universe, factors, engines and
benchmarks into public pipeline entry points.

Every pipeline threads a single ``settings: Settings = DEFAULT_SETTINGS``
parameter rather than introducing a second configuration mechanism, and
enforces the beta/characteristic lag-before-pairing-with-a-return
invariant documented in :mod:`smart_beta.data.align` as part of its own
contract, not as a caller convention.
"""
from smart_beta.pipelines.beta_portfolio import (
    BetaPortfolioResult,
    build_beta_sorted_portfolios,
    spanning_test,
)
from smart_beta.pipelines.fama_macbeth_premium import (
    FamaMacBethPipelineResult,
    build_fama_macbeth_premium,
)

__all__ = [
    "BetaPortfolioResult",
    "build_beta_sorted_portfolios",
    "spanning_test",
    "FamaMacBethPipelineResult",
    "build_fama_macbeth_premium",
]
