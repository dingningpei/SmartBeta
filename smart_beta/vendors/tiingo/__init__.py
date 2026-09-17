"""Tiingo (US equities) vendor adapter (Phase 4B).

Multiple Phase 4B tasks add modules under this package across Waves 1-3
(``client``, ``identifiers``, ``calendar_source``, ``returns_and_market_cap``,
``corporate_actions``, ``fundamentals``, ``listing``, ``source``). As with
:mod:`smart_beta.pit`, callers import directly from the concrete submodule
rather than through this file, which stays an empty marker and exports
nothing.
"""
