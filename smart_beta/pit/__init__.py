"""Vendor-independent point-in-time (PIT) data foundation (Phase 3).

A trusted boundary: every entity here distinguishes effective/event time
(when an economic fact applies) from knowledge/availability time (when a
researcher could actually have known it), and no query on this boundary can
return information from after the date it is asked about.

Empty in this initial scaffold. Populated across Phase 3's waves:
calendar.py, schema.py (Wave 1); corporate_actions.py, fundamentals.py,
source.py (Wave 2); view.py, synthetic.py (Wave 3); compliance.py (Wave 4).

This package is deliberately independent of smart_beta.data.sources.base's
DataSource -- existing research engines (smart_beta.data, .factors,
.engines, .benchmarks, .pipelines) are not migrated to consume this until
Phase 4. Phase 3 alone establishes and compliance-tests the trusted
boundary; it does not yet make PIT usage package-wide.

Like smart_beta.engines and smart_beta.benchmarks, this package has
multiple independent owners across tasks/waves, so callers import directly
from submodules (e.g. ``from smart_beta.pit.calendar import
TradingCalendar``) rather than through this file, which stays an empty
marker.
"""
