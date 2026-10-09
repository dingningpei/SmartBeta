"""Phase 10 Track-P inference procedures (P10-P owned package surface).

This package holds separately validated :class:`~smart_beta.science.inference.
InferenceProcedure` implementations. The first (and only) member is
``NONOVERLAP_T_ONE_SIDED`` v1.0.0. Each procedure registers through the frozen
P10-F public API only; nothing here mutates the module-level production
registry, appends to a Knowledge-PIT log or performs I/O.

Importing this package is side-effect free apart from deriving the contracts
from the implementation source bytes.
"""

from __future__ import annotations

from smart_beta.science.procedures.nonoverlap_t_one_sided import (
    DEPENDENCE_ASSUMPTIONS,
    DEPENDENCE_ASSUMPTIONS_HASH,
    FAILURE_CONDITIONS,
    MIN_PERIODS,
    PARAM_SCHEMA,
    PROCEDURE_ID,
    SAMPLE_REQUIREMENTS,
    VERSION,
    NonOverlapTOneSided,
    build_contract,
    build_procedure,
    register,
)

__all__ = [
    "PROCEDURE_ID",
    "VERSION",
    "MIN_PERIODS",
    "DEPENDENCE_ASSUMPTIONS",
    "DEPENDENCE_ASSUMPTIONS_HASH",
    "FAILURE_CONDITIONS",
    "PARAM_SCHEMA",
    "SAMPLE_REQUIREMENTS",
    "NonOverlapTOneSided",
    "build_contract",
    "build_procedure",
    "register",
]
