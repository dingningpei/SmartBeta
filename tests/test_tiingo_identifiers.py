"""Tests for :mod:`smart_beta.vendors.tiingo.identifiers`.

These tests exercise the frozen ``stock_id`` policy against live-captured
Tiingo specimens in ``tests/fixtures/tiingo/identifiers/``:

- daily get_meta bodies for AAPL and delisted TWTR have no permanent
  identity field, so the ticker fallback fires with ``is_permanent=False``;
- fundamentals-meta bodies for the same tickers *do* carry a real
  ``permaTicker``, and the resolver uses it when that field is present;
- the FB daily-meta body is a later ETF that reused the ticker, so the
  FB -> META rename does not share a permanent id on this account;
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

# Observed on GET /tiingo/fundamentals/meta for this account (2026-09-16).
_AAPL_PERMA = "US000000000038"
_TWTR_PERMA = "US000000000041"
_META_PERMA = "US000000000059"


def _load_meta(filename: str) -> dict:
    """Load one recorded Tiingo metadata specimen from disk."""
    return json.loads((_FIXTURE_DIR / filename).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Daily get_meta shape: no permanent field (operational path)
# ---------------------------------------------------------------------------


def test_aapl_daily_meta_has_no_permanent_identity_field() -> None:
    meta = _load_meta("aapl_meta.json")
    assert meta["ticker"] == "AAPL"
    assert PERMANENT_ID_FIELD not in meta
    assert "cik" not in meta


def test_aapl_daily_meta_falls_back_to_ticker() -> None:
    meta = _load_meta("aapl_meta.json")

    resolved = resolve_stock_id(meta)

    assert resolved == ResolvedIdentifier(
        stock_id="AAPL",
        is_permanent=False,
        source_field=TICKER_FIELD,
    )


def test_twtr_daily_meta_has_no_permanent_identity_field() -> None:
    meta = _load_meta("twtr_meta.json")
    assert meta["ticker"] == "TWTR"
    assert meta["endDate"] == "2022-10-28"
    assert PERMANENT_ID_FIELD not in meta
    assert "cik" not in meta


def test_twtr_delisted_daily_meta_falls_back_to_ticker() -> None:
    meta = _load_meta("twtr_meta.json")

    resolved = resolve_stock_id(meta)

    assert resolved == ResolvedIdentifier(
        stock_id="TWTR",
        is_permanent=False,
        source_field=TICKER_FIELD,
    )


# ---------------------------------------------------------------------------
# Fundamentals-meta shape: genuine observed permaTicker
# ---------------------------------------------------------------------------


def test_aapl_fundamentals_meta_resolves_from_perma_ticker() -> None:
    meta = _load_meta("aapl_fundamentals_meta.json")
    assert meta[PERMANENT_ID_FIELD] == _AAPL_PERMA

    resolved = resolve_stock_id(meta)

    assert resolved == ResolvedIdentifier(
        stock_id=_AAPL_PERMA,
        is_permanent=True,
        source_field=PERMANENT_ID_FIELD,
    )
    assert resolved.stock_id != meta[TICKER_FIELD]
    assert resolved.source_field == "permaTicker"


def test_twtr_delisted_fundamentals_meta_resolves_from_perma_ticker() -> None:
    meta = _load_meta("twtr_fundamentals_meta.json")
    assert meta["isActive"] is False
    assert meta[PERMANENT_ID_FIELD] == _TWTR_PERMA

    resolved = resolve_stock_id(meta)

    assert resolved == ResolvedIdentifier(
        stock_id=_TWTR_PERMA,
        is_permanent=True,
        source_field=PERMANENT_ID_FIELD,
    )
    assert resolved.stock_id != meta[TICKER_FIELD]


# ---------------------------------------------------------------------------
# Rename investigation: FB ticker reuse, no shared permanent id
# ---------------------------------------------------------------------------


def test_fb_daily_meta_is_not_meta_platforms() -> None:
    fb = _load_meta("fb_meta.json")
    meta = _load_meta("meta_meta.json")

    assert fb[TICKER_FIELD] == "FB"
    assert meta[TICKER_FIELD] == "META"
    assert "Meta" not in fb["name"]
    assert "PROSHARES" in fb["name"].upper()
    assert meta["name"].startswith("Meta Platforms")
    assert PERMANENT_ID_FIELD not in fb
    assert PERMANENT_ID_FIELD not in meta

    resolved_fb = resolve_stock_id(fb)
    resolved_meta = resolve_stock_id(meta)

    # Continuity cannot be established: the two daily-meta records are
    # different securities that share no permanent id, and they resolve
    # to different ticker fallbacks.
    assert resolved_fb.stock_id != resolved_meta.stock_id
    assert resolved_fb.is_permanent is False
    assert resolved_meta.is_permanent is False


def test_fb_and_meta_fundamentals_do_not_establish_rename_continuity() -> None:
    """fundamentals-meta has META's permaTicker but no FB row was returned."""
    meta = _load_meta("meta_fundamentals_meta.json")
    assert meta[PERMANENT_ID_FIELD] == _META_PERMA
    assert not (_FIXTURE_DIR / "fb_fundamentals_meta.json").exists()

    resolved = resolve_stock_id(meta)
    assert resolved.stock_id == _META_PERMA
    assert resolved.is_permanent is True


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


def test_cik_in_sec_filing_website_is_not_scraped() -> None:
    """AAPL fundamentals-meta embeds a CIK in secFilingWebsite; ignore it."""
    meta = _load_meta("aapl_fundamentals_meta.json")
    assert "CIK=0000320193" in meta["secFilingWebsite"]

    resolved = resolve_stock_id(meta)

    assert resolved.stock_id == _AAPL_PERMA
    assert "0000320193" not in resolved.stock_id


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
