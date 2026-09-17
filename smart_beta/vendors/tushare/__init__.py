"""Tushare (China A-share) vendor adapter (Phase 4D-B).

Multiple Phase 4D-B tasks add modules under this package across Waves 1-3
(``client``, ``proxy_client``, ``identifiers``, ``calendar_source``,
``market_data``, ``corporate_actions``, ``fundamentals``, ``listing``,
``source``). As with :mod:`smart_beta.pit` and
:mod:`smart_beta.vendors.tiingo`, callers import directly from the concrete
submodule rather than through this file, which stays an empty marker and
exports nothing.
"""
