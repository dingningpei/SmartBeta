"""Vendor-agnostic identifier-continuity guard (Phase 4C, task P4C-4).

Phase 4B's certification recorded ``IDENTIFIER CONTINUITY = NOT CERTIFIED``:
Tiingo's ``permaTicker`` is a real permanent, security-level identifier,
but the operational ``get_meta`` path never carries it, so every configured
ticker resolves through the mutable-ticker fallback with
``is_permanent=False`` -- see
:mod:`smart_beta.vendors.tiingo.identifiers`.  A long-horizon backtest
crossing a real ticker rename/reuse event could silently misattribute
history if nothing checks that fact.

This module does **not** detect or repair that limitation, and it does not
compute identity.  It turns a collection of *already-resolved* identifiers
into an explicit policy decision plus a persistent, machine-visible
:class:`IdentifierContinuityEvidence` record.  The signal it consumes
(``is_permanent``, ``source_field``) is carried by the caller's identifier
resolution result; this module only reads it.

Vendor neutrality
-----------------
The module deliberately does **not** import
:mod:`smart_beta.vendors.tiingo.identifiers` (or any other vendor module).
It types its input against :class:`ResolvedIdentifierLike`, a structural
protocol describing any source's resolved-identifier result that exposes
``stock_id``, ``is_permanent`` and ``source_field``.  A plain, hand-built
object satisfying those three attributes works identically to the Tiingo
dataclass.

What this module never does
---------------------------
It never computes ``is_permanent``, never reads any listing-history
field, never infers or reconstructs identity, never wires
``permaTicker``, and never prints or logs.  Under
``"warn"`` it returns the same complete evidence as every other mode; the
caller decides how to surface a message from that evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, Sequence

__all__ = [
    "IdentifierContinuityError",
    "IdentifierContinuityEvidence",
    "ResolvedIdentifierLike",
    "check_identifier_continuity",
]


class ResolvedIdentifierLike(Protocol):
    """Structural -- matches :class:`smart_beta.vendors.tiingo.identifiers.
    ResolvedIdentifier` without importing it, and matches any future
    vendor's equivalent result with the same three fields."""

    stock_id: str
    is_permanent: bool
    source_field: str


@dataclass(frozen=True)
class IdentifierContinuityEvidence:
    """Machine-visible record of exactly what was found and what was
    decided -- this is the object that must survive into a later
    experiment/audit layer; no policy mode may produce a result that
    lacks this.

    Attributes:
        certified: ``True`` iff every identifier in the input is permanent.
        non_permanent_stock_ids: The ``stock_id`` of every input identifier
            whose ``is_permanent`` was ``False``, in input order.  Never
            omitted or falsified under any policy mode.
        policy_applied: The literal policy mode that produced this
            evidence (``"fail"``, ``"warn"`` or ``"allow"``).  The only
            field that legitimately differs between the ``"warn"`` and
            ``"allow"`` outcomes for the same input; all continuity
            information is identical.
        proceeded_under_override: ``True`` iff execution continued despite
            ``certified`` being ``False``.  Always ``False`` when
            ``certified`` is ``True``.
    """

    certified: bool
    non_permanent_stock_ids: tuple[str, ...]
    policy_applied: Literal["fail", "warn", "allow"]
    proceeded_under_override: bool


class IdentifierContinuityError(Exception):
    """Raised under ``policy='fail'`` when continuity is not certified."""

    def __init__(self, evidence: IdentifierContinuityEvidence) -> None:
        self.evidence = evidence
        super().__init__(
            f"identifier continuity not certified for: "
            f"{evidence.non_permanent_stock_ids}"
        )


def check_identifier_continuity(
    resolved_identifiers: Sequence[ResolvedIdentifierLike],
    *,
    policy: Literal["fail", "warn", "allow"] = "fail",
) -> IdentifierContinuityEvidence:
    """Decide whether a run may proceed given its resolved identifiers.

    The function reads only ``is_permanent`` and ``stock_id`` from each
    input.  It never computes ``is_permanent`` itself, never reads a
    listing-history field, and never infers identity.

    Frozen behavior:

    ===================  =====================================  ==========================================
    ``policy``           ``certified=True`` (all permanent)      ``certified=False`` (any non-permanent)
    ===================  =====================================  ==========================================
    ``"fail"`` (default) returns evidence, execution proceeds     **raises** ``IdentifierContinuityError(evidence)``
    ``"warn"``           returns evidence, execution proceeds     returns evidence with ``proceeded_under_override=True``; caller is expected to surface a visible warning using the returned evidence (this function does not itself print/log -- that is the caller's presentation choice)
    ``"allow"``          returns evidence, execution proceeds     returns evidence with ``proceeded_under_override=True`` -- silent only in the sense that nothing is *raised*; the evidence object is exactly as complete and explicit as every other mode
    ===================  =====================================  ==========================================

    No mode ever returns evidence with a missing or falsified
    ``non_permanent_stock_ids`` list.  ``certified=False`` information is
    never harder to find under ``"allow"`` than under ``"fail"`` -- only
    whether an exception is raised changes.

    Args:
        resolved_identifiers: Already-resolved identifiers from any source,
            each exposing ``stock_id``, ``is_permanent`` and
            ``source_field``.  Empty input is vacuously certified.
        policy: ``"fail"`` (default), ``"warn"`` or ``"allow"``.

    Returns:
        An :class:`IdentifierContinuityEvidence` describing exactly what was
        found and what was decided.

    Raises:
        IdentifierContinuityError: Under ``policy="fail"`` when any input
            identifier is non-permanent.
        ValueError: If ``policy`` is not one of the three frozen modes.
    """
    if policy not in ("fail", "warn", "allow"):
        raise ValueError(f"unknown identifier-continuity policy: {policy!r}")

    non_permanent_stock_ids = tuple(
        resolved.stock_id
        for resolved in resolved_identifiers
        if not resolved.is_permanent
    )
    certified = not non_permanent_stock_ids

    if certified:
        return IdentifierContinuityEvidence(
            certified=True,
            non_permanent_stock_ids=(),
            policy_applied=policy,
            proceeded_under_override=False,
        )

    evidence = IdentifierContinuityEvidence(
        certified=False,
        non_permanent_stock_ids=non_permanent_stock_ids,
        policy_applied=policy,
        proceeded_under_override=True,
    )
    if policy == "fail":
        raise IdentifierContinuityError(evidence)
    return evidence
