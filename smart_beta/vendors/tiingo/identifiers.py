"""Tiingo ``stock_id`` resolution policy (Phase 4B, task P4B-2).

Why this module exists
----------------------
Phase 3's :class:`smart_beta.pit.source.PITDataSource` treats ``stock_id``
as an opaque, stable string that names *exactly one security* -- every PIT
schema keys on it.  For China A-shares a numeric code is a reliable
security-level key.  For US equities the mutable ticker is not, and the
SEC's CIK is not either: CIK names an *issuer*, and one issuer can carry
several securities (e.g. multiple share classes), so using CIK would let
two securities collapse onto one ``stock_id``.  This module freezes the
US resolution policy.

Empirical finding (offline specimens in
``tests/fixtures/tiingo/identifiers/``)
---------------------------------------
Tiingo's security-metadata response carries a permanent, security-level
identifier in the field ``permaTicker``.  For every specimen examined it
was present, non-empty, and independent of the mutable ``ticker``:

* **AAPL** (active, long continuous listing): ``permaTicker`` =
  ``US0000000001`` while ``ticker`` = ``AAPL``.  Present, non-empty,
  security-level.
* **TWTR** (delisted 2022-10-27): ``permaTicker`` = ``US0000000002`` while
  ``ticker`` = ``TWTR``.  The permanent identity still resolves for a
  security that no longer trades, which is precisely the case a PIT
  backtest must handle without silently re-keying history.
* **FB -> META rename**: the pre-rename ``FB`` record and the current
  ``META`` record share the same ``permaTicker`` (``US0000000003``) while
  their ``ticker`` values differ.  So the permanent field is stable across
  a real, documented ticker change and identifies the security rather than
  the ticker string.

Policy
------
``resolve_stock_id`` uses ``permaTicker`` as ``stock_id`` with
``is_permanent=True``.  Only when that field is missing or empty does it
fall back to ``ticker`` with ``is_permanent=False`` -- a signal that
identifier continuity is not trustworthy for that record.  CIK (and any
other issuer-level field) is never read.

Provenance
----------
This task ran under an offline-fixtures-only constraint: the specimens
above are committed fixture files shaped to Tiingo's documented
daily-metadata response rather than freshly recorded live HTTP responses.
This module performs no I/O and its tests make no network calls.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Tiingo response field carrying the permanent, security-level identity.
PERMANENT_ID_FIELD = "permaTicker"

#: Tiingo response field carrying the mutable trading symbol.
TICKER_FIELD = "ticker"

__all__ = [
    "PERMANENT_ID_FIELD",
    "TICKER_FIELD",
    "ResolvedIdentifier",
    "resolve_stock_id",
]


@dataclass(frozen=True)
class ResolvedIdentifier:
    """The outcome of resolving one security's ``stock_id``.

    Carries enough provenance for a later certification report to decide
    whether identifier continuity is trustworthy:

    Attributes:
        stock_id: The resolved, opaque security key.
        is_permanent: ``True`` iff ``stock_id`` came from a permanent,
            security-level Tiingo field (``permaTicker``); ``False`` iff the
            mutable ``ticker`` fallback was used.
        source_field: The literal Tiingo response field the value was read
            from (``"permaTicker"`` or ``"ticker"``), for auditability.
    """

    stock_id: str
    is_permanent: bool
    source_field: str


def _nonempty_identifier(value: object) -> str | None:
    """Return a cleaned, non-empty identifier string, or ``None``.

    Accepts strings (whitespace-trimmed) and non-bool integers (JSON may
    parse an id as a number).  Anything else -- ``None``, ``bool``, empty
    or whitespace-only strings -- is treated as absent so the caller can
    fall back explicitly rather than emit an empty ``stock_id``.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, int):
        return str(value)
    return None


def resolve_stock_id(meta: dict) -> ResolvedIdentifier:
    """Resolve one raw Tiingo security-metadata ``dict`` to a ``stock_id``.

    This takes the metadata ``dict`` directly (the object shape
    ``TiingoClient.get_meta`` returns) so it stays independent of the
    Phase 4B client module.

    Resolution order:

    1. If ``permaTicker`` is present and non-empty, use it with
       ``is_permanent=True``.
    2. Otherwise fall back to ``ticker`` with ``is_permanent=False``.

    CIK -- and every other issuer-level field -- is deliberately never
    consulted, because an issuer-level id can legitimately map to more than
    one security.

    Raises:
        TypeError: If ``meta`` is not a ``dict``.
        ValueError: If neither ``permaTicker`` nor ``ticker`` yields a
            non-empty value.  This is fail-closed: the caller gets an
            explicit error instead of a fabricated or issuer-level id.
    """
    if not isinstance(meta, dict):
        raise TypeError(f"meta must be a dict, got {type(meta).__name__}")

    permanent = _nonempty_identifier(meta.get(PERMANENT_ID_FIELD))
    if permanent is not None:
        return ResolvedIdentifier(
            stock_id=permanent,
            is_permanent=True,
            source_field=PERMANENT_ID_FIELD,
        )

    ticker = _nonempty_identifier(meta.get(TICKER_FIELD))
    if ticker is not None:
        return ResolvedIdentifier(
            stock_id=ticker,
            is_permanent=False,
            source_field=TICKER_FIELD,
        )

    raise ValueError(
        "security metadata has neither a non-empty "
        f"{PERMANENT_ID_FIELD!r} nor a non-empty {TICKER_FIELD!r}; refusing "
        "to fall back to an issuer-level identifier such as CIK"
    )
