"""Fold window and reserve-access gate (Campaign-3 P7, task R7-U).

Authority: ``docs/campaign3_p7_reserve_path_design.md`` §2 (fold parameter, gate)
and ``docs/campaign3_p7_implementation_spec.md`` §2/§4, as amended by
``docs/campaign3_p7_amendment_a1.md``.

The frozen D-chain builders hard-code the development boundary and refuse
reserve dates by design (the firewall).  The P7 reserve chain is new,
fold-generic code: every derivation is parameterized by a :class:`FoldWindow`
naming the formation window and the package source.

A fold window decides only four things:

* which formations are evaluated (``start <= formation <= end``);
* which package source supplies the data (``development`` / ``reserve``);
* which partition label the evaluated rows carry;
* the latest D-SC query cutoff the fold may use.

Constructing a **reserve** fold window requires an :class:`AccessGrant`.
Without one, construction fails closed with :class:`ReserveAccessDenied`
*before any path is opened*.  This module carries only the stub grant; the
real K-PIT ``ACCESS``-record grant and its single-use ledger are task R7-X.

``smart_beta/dataset_v1/universe.py`` is consulted **import-only** for its
frozen constants; it is never modified.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from smart_beta.dataset_v1 import universe as _universe

__all__ = [
    "DEVELOPMENT",
    "RESERVE",
    "SOURCES",
    "ReserveAccessDenied",
    "AccessGrant",
    "FoldWindow",
]

#: The two package sources a fold may select (design §2).
DEVELOPMENT = "development"
RESERVE = "reserve"
SOURCES = frozenset({DEVELOPMENT, RESERVE})


class ReserveAccessDenied(RuntimeError):
    """A reserve fold was requested without a valid :class:`AccessGrant`."""


@dataclass(frozen=True)
class AccessGrant:
    """Stub for the K-PIT ``ACCESS`` grant.

    Task R7-X replaces this with a grant read from a K-PIT ``ACCESS`` record
    that names the hypothesis, the admitted procedure, the exact
    ``reserve_chain`` module SHA-256s and the fold window.  R7-U only needs the
    object's presence: a reserve :class:`FoldWindow` is refused unless an
    ``AccessGrant`` instance is supplied.
    """

    hypothesis_id: str = ""
    procedure_id: str = ""
    fold_start: date | None = None
    fold_end: date | None = None
    module_sha256: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class FoldWindow:
    """A closed formation window ``[start, end]`` and its package source.

    ``source`` selects the D3/D5/EDGAR packages (design §2):

    * ``development`` -> ``normalized/…``, ``derived/qc/…``, ``derived/edgar-…``;
    * ``reserve`` -> ``reserve_sealed/…`` for the same artifact ids.

    Lookback rows before ``start`` always come from the development packages;
    the caller supplies those bindings separately to
    :func:`smart_beta.dataset_v1.reserve_chain.universe_fold.run_universe_fold`.
    """

    start: date
    end: date
    source: str = DEVELOPMENT
    grant: AccessGrant | None = None

    def __post_init__(self) -> None:
        if self.source not in SOURCES:
            raise ValueError(
                f"source must be one of {sorted(SOURCES)}, got {self.source!r}"
            )
        if self.start > self.end:
            raise ValueError(
                f"fold start {self.start} is after end {self.end}"
            )
        if self.source == RESERVE and not isinstance(self.grant, AccessGrant):
            raise ReserveAccessDenied(
                "a reserve FoldWindow requires an AccessGrant; refusing before "
                "any path is opened"
            )

    # -- constructors ----------------------------------------------------

    @classmethod
    def development(cls) -> "FoldWindow":
        """The frozen development fold: 2016-01-04 … 2023-06-30."""
        return cls(
            start=_universe.SCHEDULE_START,
            end=_universe.DEVELOPMENT_END,
            source=DEVELOPMENT,
        )

    @classmethod
    def reserve(cls, end: date, *, grant: AccessGrant) -> "FoldWindow":
        """The reserve fold: 2023-07-03 … ``end`` (design §2; end = C)."""
        return cls(
            start=_universe.RESERVE_START,
            end=end,
            source=RESERVE,
            grant=grant,
        )

    # -- derived properties ----------------------------------------------

    @property
    def partition(self) -> str:
        """The partition label the evaluated formations carry."""
        if self.source == DEVELOPMENT:
            return _universe.PARTITION_DEVELOPMENT
        return _universe.PARTITION_RESERVE

    @property
    def other_partition(self) -> str:
        """The partition label for formations outside the window (DEFERRED)."""
        if self.source == DEVELOPMENT:
            return _universe.PARTITION_RESERVE
        return _universe.PARTITION_DEVELOPMENT

    @property
    def dsc_max_cutoff(self) -> date:
        """Latest D-SC PIT query cutoff this fold may use.

        Development folds inherit the frozen development firewall cutoff
        (2023-06-29); reserve folds may query up to the fold end.
        """
        if self.source == DEVELOPMENT:
            return _universe.DSC_MAX_CUTOFF
        return self.end

    def contains(self, formation: date) -> bool:
        """Whether ``formation`` is evaluated by this fold."""
        return self.start <= formation <= self.end
