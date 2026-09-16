"""Vendor-specific data adapters (Phase 4B+).

This is a new multi-vendor package: each vendor gets its own subpackage
(``smart_beta.vendors.tiingo`` now; a future SEC/EDGAR adapter would live
beside it). Like :mod:`smart_beta.pit`, this package has multiple independent
owners across tasks/waves, so callers import directly from submodules
(e.g. ``from smart_beta.vendors.tiingo.client import TiingoClient``) rather
than through this file, which stays an empty marker.
"""
