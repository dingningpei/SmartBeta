"""Campaign-4 M0 method diagnostics (IS-only, descriptive, no K-PIT writes).

Authority: the frozen ``worker_tasks/campaign4_m0/campaign4-m0-plan.md``.
Every module in this package is read-only with respect to the dataset tiers:
it consumes the IS fold only, never opens a reserve / OOS / sealed path, never
appends to the Knowledge-PIT and never performs a network call.
"""

from __future__ import annotations

__all__ = ["known_factors"]
