"""Campaign-4 M1 contract core (task C4M1-1).

Authority: ``worker_tasks/campaign4_m1/task-c4m1-1-contract-core.md`` on top of
the frozen ``worker_tasks/campaign4_m1/campaign4-m1-plan.md``.

This package contains the information contract that the Campaign-4 M1
walk-forward harness (C4M1-2) composes:

* :mod:`~smart_beta.campaign4.contract` -- A2 declaration validation and the
  C3-compatible strip;
* :mod:`~smart_beta.campaign4.diversity` -- the A4 near-duplicate gate, the
  family cap and the A6 restatement report;
* :mod:`~smart_beta.campaign4.ranking` -- the training-window rank statistic
  over the v2.5 terminal-stress (TD) cells;
* :mod:`~smart_beta.campaign4.feedback` -- the treatment / control renderers.

Every module is import-only with respect to the sealed Campaign-3 / P-Q v2.3 /
v2.5 code and reads no real data.
"""

# The shared firewall error is defined in the contract module so the gate and
# the renderers can raise one C4 error type without importing each other.
from smart_beta.campaign4.contract import (
    Campaign4Error,
    C4ContractError,
    C4FirewallError,
)

__all__ = ["Campaign4Error", "C4ContractError", "C4FirewallError"]
