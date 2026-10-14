"""P-Q v2.5 — terminal-stress scenario TD (new-modules-only, frozen design).

This package implements the P-Q v2.5 frozen design
(``docs/discovery_dataset_v1_2_pq_v2_5_terminal_td_design_draft.md``) and its
erratum E1.  Nothing here modifies any file in :data:`pqv2_core.PINNED_CODE` or
in the three Campaign-3-bound file lists; the frozen modules are imported
read-only.

PQ25-A owns :mod:`smart_beta.dataset_v1.pqv25.authority`.
"""

from __future__ import annotations

from smart_beta.dataset_v1.pqv25.authority import (
    AUTHORITY_V2_5,
    C3_BOUND_RELATIVE,
    C3_BOUND_SHA256,
    C3_BOUND_SHA256_FILE_SHA256,
    ERRATA_V2_5,
    TD_D,
    verify_authority_v2_5,
)

__all__ = [
    "AUTHORITY_V2_5",
    "ERRATA_V2_5",
    "TD_D",
    "C3_BOUND_RELATIVE",
    "C3_BOUND_SHA256_FILE_SHA256",
    "C3_BOUND_SHA256",
    "verify_authority_v2_5",
]
