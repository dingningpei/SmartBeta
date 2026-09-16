"""Tests for :mod:`smart_beta.vendors.tiingo.identifiers`.

These tests exercise the frozen ``stock_id`` policy against the offline
Tiingo specimens in ``tests/fixtures/tiingo/identifiers/``:

- an active security (AAPL) and a delisted one (TWTR) both resolve from
  the permanent ``permaTicker`` field;
- the permanent field is stable across the FB -> META ticker rename;
- the ticker fallback fires only when ``permaTicker`` is absent or empty;
- a CIK-shaped field is never returned as ``stock_id``.

No test here performs network I/O: the fixtures are read straight from
disk and ``resolve_stock_id`` takes a plain dict.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from smart_beta.vendors.tiingo.identifiers import (
    PERMANENT_ID_FIELD,
    TICKER_FIELD,
    ResolvedIdentifier,
    resolve_stock_id,
)

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "tiingo" / "identifiers"


def _load_meta(filename: str) -> dict:
    """Load one recorded Tiingo metadata specimen from disk."""
    return json.loads((_FIXTURE_DIR / filename).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Permanent field present: active security
# ---------------------------------------------------------------------------


def test_aapl_resolves_from_perma_ticker() -> None:
    meta = _load_meta("aapl_meta.json")

    resolved = resolve_stock_id(meta)

    assert resolved == ResolvedIdentifier(
        stock_id="US0000000001",
        is_permanent=True,
        source_field=PERMANENT_ID_FIELD,
    )
    # The permanent id is genuinely distinct from the mutable ticker; the
    # resolver is not merely echoing ``ticker``.
    assert resolved.stock_id != meta[TICKER_FIELD]
    assert resolved.source_field == "permaTicker"


# ---------------------------------------------------------------------------
# Permanent field present: delisted security
# ---------------------------------------------------------------------------


def test_twtr_delisted_resolves_from_perma_ticker() -> None:
    meta = _load_meta("twtr_meta.json")

    resolved = resolve_stock_id(meta)

    assert resolved == ResolvedIdentifier(
        stock_id="US0000000002",
        is_permanent=True,
        source_field=PERMANENT_ID_FIELD,
    )
    assert resolved.is_permanent is True
    assert resolved.stock_id != meta[TICKER_FIELD]


# ---------------------------------------------------------------------------
# Permanent field stable across a real ticker rename
# ---------------------------------------------------------------------------


def test_fb_to_meta_rename_keeps_the_same_stock_id() -> None:
    fb = _load_meta("fb_meta.json")
    meta = _load_meta("meta_meta.json")

    # Sanity-check the premise: same company, different ticker strings.
    assert fb[TICKER_FIELD] == "FB"
    assert meta[TICKER_FIELD] == "META"
    assert fb[TICKER_FIELD] != meta[TICKER_FIELD]

    resolved_fb = resolve_stock_id(fb)
    resolved_meta = resolve_stock_id(meta)

    assert resolved_fb.stock_id == resolved_meta.stock_id
    assert resolved_fb.is_permanent is True
    assert resolved_meta.is_permanent is True
    assert resolved_fb.source_field == PERMANENT_ID_FIELD
    assert resolved_meta.source_field == PERMANENT_ID_FIELD


# ---------------------------------------------------------------------------
# Ticker fallback: permanent field absent or empty
# ---------------------------------------------------------------------------


def test_falls_back_to_ticker_when_perma_ticker_absent() -> None:
    meta = {
        "ticker": "ZZZZ",
        "name": "Fallback Example Inc",
        "exchangeCode": "NASDAQ",
    }

    resolved = resolve_stock_id(meta)

    assert resolved == ResolvedIdentifier(
        stock_id="ZZZZ",
        is_permanent=False,
        source_field=TICKER_FIELD,
    )


@pytest.mark.parametrize("empty_value", [None, "", "   ", "\t\n"])
def test_falls_back_to_ticker_when_perma_ticker_empty(empty_value: object) -> None:
    meta = {"ticker": "ZZZZ", "permaTicker": empty_value}

    resolved = resolve_stock_id(meta)

    assert resolved.stock_id == "ZZZZ"
    assert resolved.is_permanent is False
    assert resolved.source_field == TICKER_FIELD


# ---------------------------------------------------------------------------
# CIK is never used as stock_id
# ---------------------------------------------------------------------------


def test_cik_shaped_field_is_never_returned_as_stock_id() -> None:
    """With a CIK present but no ``permaTicker``, the ticker must win."""
    meta = {
        "ticker": "AAPL",
        "cik": "0000320193",
        "name": "Apple Inc",
    }

    resolved = resolve_stock_id(meta)

    assert resolved.stock_id == "AAPL"
    assert resolved.stock_id != meta["cik"]
    assert resolved.is_permanent is False
    assert resolved.source_field == TICKER_FIELD


def test_cik_only_metadata_is_fail_closed() -> None:
    """A CIK-only record must raise, never resolve to the issuer-level id."""
    meta = {"cik": "0000320193", "name": "Issuer With No Ticker Or PermaTicker"}

    with pytest.raises(ValueError):
        resolve_stock_id(meta)


# ---------------------------------------------------------------------------
# Contract details
# ---------------------------------------------------------------------------


def test_non_dict_input_raises_type_error() -> None:
    with pytest.raises(TypeError):
        resolve_stock_id(["not", "a", "dict"])  # type: ignore[arg-type]


def test_resolved_identifier_is_frozen() -> None:
    resolved = resolve_stock_id({"ticker": "AAPL"})

    with pytest.raises(Exception):
        resolved.stock_id = "MUTATED"  # type: ignore[misc]
