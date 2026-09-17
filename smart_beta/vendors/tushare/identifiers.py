"""Tushare ``stock_id`` resolution policy (Phase 4D-B, task P4DB-2).

Baseline policy
---------------
Tushare's ``ts_code`` (e.g. ``"000001.SZ"``) is Tushare's own namespaced
identifier: exchange suffix + numeric code.  Unlike Tiingo's mutable,
reusable ticker, an exchange-suffixed A-share code is a vendor-asserted,
security-level key, so the common case is a direct passthrough:

    ``stock_id = ts_code`` with ``is_permanent = True``.

Identifier-continuity investigation (bounded)
---------------------------------------------
The Phase 4D-A live spike left a frozen instruction: a corporate
restructuring specimen (``000024.SZ``, superficially resembling
``001914.SZ``) turned out on inspection to be an *unrelated entity*, and
naive brand/name-similarity continuity assumptions are actively wrong.
This task re-examined whether Tushare exposes a **direct, vendor-asserted**
old-code -> new-code continuity signal, as opposed to a heuristic.

Findings (see ``tests/fixtures/tushare/identifiers/README.md`` for the
fixture provenance and the exact recorded shapes):

* ``stock_basic`` exposes ``ts_code, name, list_date, delist_date,
  list_status``.  It carries **no** field naming a successor/predecessor
  ``ts_code``; two records are either the same code or two independent
  securities.  There is no old-code -> new-code join key.
* ``namechange`` exposes ``ts_code, name, start_date, end_date, ann_date,
  change_reason``.  It records *name* changes **for the same** ``ts_code``
  -- the code is the key on every row, so it cannot express a code change
  at all.  A rename within one code leaves ``ts_code`` untouched; using
  it to forge a mapping between two different codes would be exactly the
  name-similarity heuristic the spike already falsified.
* No other reachable field in the bounded investigation provides a
  vendor-asserted old-code -> new-code mapping.

No direct signal was found, so -- per the task's stop condition -- **no
heuristic rename mapping is implemented**.  ``resolve_stock_id`` is a
direct ``ts_code`` passthrough.

``is_permanent``
----------------
``is_permanent`` means "this ``stock_id`` is backed by a vendor-asserted
stable identifier that is valid for the full horizon", never "a ticker
guess".  Because ``ts_code`` is stable, an *ongoing* security resolves
with ``is_permanent=True`` even when no ``stock_basic`` row is supplied.

For a security whose ``stock_basic`` row shows it is delisted
(``list_status == "D"``), paused/suspended (``"P"``), or carries a
non-empty ``delist_date``, the code is still Tushare's own key for that
security, but its continuity *past its own listing end* -- e.g. to any
successor -- is not asserted by any Tushare field.  Continuing to treat
such an identifier as a permanently stable key across that terminal event
would require guessing, so those rows resolve with
``is_permanent=False``.  This is the concrete, tested form of the frozen
``IDENTIFIER CONTINUITY = NOT CERTIFIED`` disposition; it is a
fail-closed signal, not a mapping.

What this module never does
---------------------------
It never matches names, never reads a listing-history field as if it were
an identifier, never fabricates a successor code, and never performs I/O.
It takes an already-fetched row (or ``None``) as a plain ``dict``.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "DELISTED_LIST_STATUSES",
    "NAME_CHANGE_FIELD",
    "ResolvedIdentifier",
    "resolve_stock_id",
]

#: ``stock_basic.list_status`` values meaning the security is no longer an
#: ongoing listing: ``"D"`` = delisted, ``"P"`` = paused/suspended.  These
#: are the only two statuses Tushare's own documentation assigns a terminal
#: (non-``"L"``) meaning; ``"L"`` (listed) is the ongoing case.  Anything
#: else is treated as unknown/ongoing (baseline ``is_permanent=True``).
DELISTED_LIST_STATUSES = frozenset({"D", "P"})

#: The ``namechange`` endpoint's field set (confirmed shape).  It is
#: recorded here only so the module documents *why* it is not consulted: a
#: name is not an identifier, and every ``namechange`` row is keyed by the
#: same ``ts_code``, so this endpoint cannot express an old-code -> new-code
#: mapping.
NAME_CHANGE_FIELD = "name"


def _clean_ts_code(ts_code: object) -> str:
    """Return a non-empty, whitespace-trimmed ``ts_code`` string.

    Fail-closed: an empty or non-string ``ts_code`` raises rather than
    producing an empty or coerced ``stock_id``.
    """
    if not isinstance(ts_code, str):
        raise TypeError(
            f"ts_code must be a str, got {type(ts_code).__name__}"
        )
    cleaned = ts_code.strip()
    if not cleaned:
        raise ValueError("ts_code must be a non-empty string")
    return cleaned


def _row_is_terminal(stock_basic_row: dict) -> bool:
    """Return ``True`` when a ``stock_basic`` row shows a terminal listing.

    A row is terminal when ``list_status`` is delisted/paused (``"D"``/
    ``"P"``, case-insensitive) or when ``delist_date`` carries a non-empty
    value.  Missing/blank fields are treated as non-terminal (the baseline
    ongoing case).
    """
    status = stock_basic_row.get("list_status")
    if isinstance(status, str) and status.strip().upper() in DELISTED_LIST_STATUSES:
        return True
    delist_date = stock_basic_row.get("delist_date")
    if delist_date is None:
        return False
    if isinstance(delist_date, str):
        return bool(delist_date.strip())
    # A non-string, non-None delist_date (e.g. a number) is still an
    # explicit terminal marker; never silently ignore it.
    return True


@dataclass(frozen=True)
class ResolvedIdentifier:
    """The outcome of resolving one security's ``stock_id``.

    Attributes:
        stock_id: The resolved, opaque security key (the cleaned
            ``ts_code`` under the frozen passthrough policy).
        is_permanent: ``True`` iff the key is backed by a vendor-asserted
            stable identifier that is valid for the full horizon (an
            ongoing listing, or no ``stock_basic`` evidence of a terminal
            event); ``False`` when the security's own row shows a
            delisting/pausing and continuity past that point is not
            vendor-asserted.  Never ``True`` for a heuristic guess.
        raw_ts_code: The exact (cleaned) ``ts_code`` the caller passed, kept
            verbatim for provenance/auditability.
    """

    stock_id: str
    is_permanent: bool
    raw_ts_code: str


def resolve_stock_id(
    ts_code: str,
    stock_basic_row: dict | None = None,
) -> ResolvedIdentifier:
    """Resolve one Tushare ``ts_code`` to a ``stock_id``.

    Policy (frozen, see module docstring): ``stock_id = ts_code`` --
    Tushare's exchange-suffixed code is itself the stable, vendor-asserted
    key.  No name-based or other heuristic continuation between two
    different codes is ever performed, because no Tushare field asserts
    one.

    ``is_permanent`` is ``True`` by default.  When ``stock_basic_row`` is
    supplied and shows the security is delisted/paused (or carries a
    non-empty ``delist_date``), it is ``False``: the identifier is not
    certified to remain a stable key *across that terminal event*, and any
    successor mapping would be a guess.

    Args:
        ts_code: Tushare's namespaced identifier, e.g. ``"000001.SZ"``.
        stock_basic_row: The optional ``stock_basic`` record for this
            ``ts_code`` (a plain mapping, as returned by the API).  It is
            used **only** for the terminal-status signal above -- never to
            remap ``stock_id``.

    Returns:
        A frozen :class:`ResolvedIdentifier`.

    Raises:
        TypeError: If ``ts_code`` is not a string, or ``stock_basic_row``
            is not a mapping/``None``.
        ValueError: If ``ts_code`` is empty, or if ``stock_basic_row``
            carries a non-empty ``ts_code`` that disagrees with the input
            (fail-closed: a mismatched row must never be silently applied
            to the wrong security).
    """
    raw = _clean_ts_code(ts_code)

    if stock_basic_row is not None and not isinstance(stock_basic_row, dict):
        raise TypeError(
            "stock_basic_row must be a dict or None, got "
            f"{type(stock_basic_row).__name__}"
        )

    is_permanent = True
    if stock_basic_row is not None:
        row_ts_code = stock_basic_row.get("ts_code")
        if row_ts_code is not None:
            if not isinstance(row_ts_code, str):
                raise TypeError(
                    "stock_basic_row['ts_code'] must be a str when present, "
                    f"got {type(row_ts_code).__name__}"
                )
            if row_ts_code.strip() and row_ts_code.strip() != raw:
                raise ValueError(
                    f"stock_basic_row is for {row_ts_code!r} but ts_code "
                    f"{raw!r} was requested; refusing to apply a row to the "
                    "wrong security"
                )
        if _row_is_terminal(stock_basic_row):
            is_permanent = False

    return ResolvedIdentifier(
        stock_id=raw,
        is_permanent=is_permanent,
        raw_ts_code=raw,
    )
