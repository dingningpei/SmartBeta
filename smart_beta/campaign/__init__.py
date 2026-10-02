"""Campaign-1 development ledger and executor (C1-LX).

Effective authority: ``worker_tasks/campaign1/task-c1-ledger-executor.md``
plus errata C1-LX-E1 and C1-LX-E2. See
:mod:`smart_beta.campaign.campaign1` for the executor and
:mod:`smart_beta.campaign.ledger` for the append-only hash chain.
"""

from __future__ import annotations

from smart_beta.campaign import campaign1 as campaign1
from smart_beta.campaign import ledger as ledger

__all__ = ["campaign1", "ledger"]
