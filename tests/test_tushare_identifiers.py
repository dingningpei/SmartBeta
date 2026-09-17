"""Tests for :mod:`smart_beta.vendors.tushare.identifiers` (P4DB-2).

These tests are fully offline.  They read the ``constructed_*`` specimens
under ``tests/fixtures/tushare/identifiers/`` through a tiny
``fetch(api_name, **params) -> {"fields": [...], "items": [...]}`` stub
defined in this file -- never through ``client.py`` or any real network
transport (P4DB-1 does not exist in this worktree by design).

Important provenance caveat (see the fixture README): ``TUSHARE_PROXY_TOKEN``
was not available, so the fixtures are hand-constructed from the documented
field shapes plus the Phase 4D-A dossier's frozen ``000024.SZ`` /
``001914.SZ`` finding.  They are not fresh live captures, and the fixture
README says so.  The test that asserts the ``000024.SZ`` / ``001914.SZ``
relationship therefore asserts the **carried-forward, frozen** finding,
not a new live re-confirmation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from smart_beta.vendors.tushare.identifiers import (
    DELISTED_LIST_STATUSES,
    ResolvedIdentifier,
    resolve_stock_id,
)

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "tushare" / "identifiers"

# Fixture provenance: constructed from the documented endpoint shapes and
# the Phase 4D-A dossier, not recorded live (credential unavailable).
_SPECIMEN_DELISTED = "000024.SZ"
_SPECIMEN_SIMILAR = "001914.SZ"
_ACTIVE_LARGE_CAPS = ("000001.SZ", "600519.SH", "601318.SH")


# ---------------------------------------------------------------------------
# The "fetch"-shaped stub (raw Tushare response shape)
# ---------------------------------------------------------------------------


class _FixtureFetch:
    """Minimal stand-in for ``TushareClient.fetch`` over the fixtures.

    Returns Tushare's raw ``{"fields": [...], "items": [[...], ...]}``
    payload, filtered by any exact-match ``**params`` (e.g.
    ``ts_code="000024.SZ"``).  This mirrors the shape every later P4DB
    module consumes without importing the real client (which does not exist
    in this worktree).
    """

    def __init__(self, fixture_dir: Path) -> None:
        self._dir = fixture_dir

    def fetch(self, api_name: str, **params: str) -> dict:
        payload = json.loads(
            (self._dir / f"constructed_{api_name}.json").read_text(
                encoding="utf-8"
            )
        )
        fields = list(payload["fields"])
        index = {name: i for i, name in enumerate(fields)}
        items = [
            row
            for row in payload["items"]
            if all(str(row[index[key]]) == str(value) for key, value in params.items())
        ]
        return {"fields": fields, "items": items}


@pytest.fixture
def fetch() -> _FixtureFetch:
    return _FixtureFetch(_FIXTURE_DIR)


def _row(fetch: _FixtureFetch, api_name: str, **params: str) -> dict:
    """Fetch exactly one row and return it as a field->value dict."""
    payload = fetch.fetch(api_name, **params)
    assert len(payload["items"]) == 1, (api_name, params, payload)
    return dict(zip(payload["fields"], payload["items"][0]))


def _rows(fetch: _FixtureFetch, api_name: str, **params: str) -> list[dict]:
    payload = fetch.fetch(api_name, **params)
    return [dict(zip(payload["fields"], row)) for row in payload["items"]]


# ---------------------------------------------------------------------------
# Fixture shape / provenance assertions
# ---------------------------------------------------------------------------


def test_stock_basic_fixture_has_only_documented_fields(fetch: _FixtureFetch) -> None:
    payload = fetch.fetch("stock_basic")
    assert payload["fields"] == [
        "ts_code",
        "name",
        "list_date",
        "delist_date",
        "list_status",
    ]
    # No successor/predecessor code field is exposed for any specimen.
    joined = " ".join(payload["fields"]).lower()
    for forbidden in ("successor", "predecessor", "new_ts_code", "old_ts_code"):
        assert forbidden not in joined


def test_namechange_fixture_shape(fetch: _FixtureFetch) -> None:
    payload = fetch.fetch("namechange")
    assert payload["fields"] == [
        "ts_code",
        "name",
        "start_date",
        "end_date",
        "ann_date",
        "change_reason",
    ]


# ---------------------------------------------------------------------------
# Common case: ts_code passthrough, vendor-asserted, permanent
# ---------------------------------------------------------------------------


def test_common_case_without_row_is_direct_passthrough_and_permanent() -> None:
    resolved = resolve_stock_id("000001.SZ")

    assert resolved == ResolvedIdentifier(
        stock_id="000001.SZ",
        is_permanent=True,
        raw_ts_code="000001.SZ",
    )


def test_active_large_caps_resolve_to_their_own_ts_code(fetch: _FixtureFetch) -> None:
    for ts_code in _ACTIVE_LARGE_CAPS:
        row = _row(fetch, "stock_basic", ts_code=ts_code)
        resolved = resolve_stock_id(ts_code, row)

        assert resolved.stock_id == ts_code
        assert resolved.raw_ts_code == ts_code
        assert resolved.is_permanent is True


def test_active_row_with_blank_delist_date_stays_permanent() -> None:
    row = {
        "ts_code": "600519.SH",
        "name": "贵州茅台",
        "list_date": "20010827",
        "delist_date": "",
        "list_status": "L",
    }

    resolved = resolve_stock_id("600519.SH", row)

    assert resolved.stock_id == "600519.SH"
    assert resolved.is_permanent is True


# ---------------------------------------------------------------------------
# The 000024.SZ / 001914.SZ specimen
# ---------------------------------------------------------------------------


def test_specimen_000024_and_001914_are_independent_codes(
    fetch: _FixtureFetch,
) -> None:
    row_a = _row(fetch, "stock_basic", ts_code=_SPECIMEN_DELISTED)
    row_b = _row(fetch, "stock_basic", ts_code=_SPECIMEN_SIMILAR)

    # Structural fact from the vendor row set: the delisted specimen carries
    # its own ts_code/delist_date/status; the similar-looking code is a
    # separate, ongoing record.  No field links them.
    assert row_a["ts_code"] == _SPECIMEN_DELISTED
    assert row_a["list_status"] in DELISTED_LIST_STATUSES
    assert row_a["delist_date"] == "20151230"
    assert row_b["ts_code"] == _SPECIMEN_SIMILAR
    assert row_b["list_status"] == "L"
    assert row_b["delist_date"] == ""
    assert row_a["name"] != row_b["name"]

    resolved_a = resolve_stock_id(_SPECIMEN_DELISTED, row_a)
    resolved_b = resolve_stock_id(_SPECIMEN_SIMILAR, row_b)

    # The frozen Phase 4D-A finding: these are unrelated securities.  The
    # delisted code does not continue as the similar-looking code.
    assert resolved_a.stock_id != resolved_b.stock_id
    assert resolved_a.stock_id == _SPECIMEN_DELISTED
    assert resolved_b.stock_id == _SPECIMEN_SIMILAR


def test_namechange_never_maps_one_code_to_the_other(fetch: _FixtureFetch) -> None:
    rows = _rows(fetch, "namechange")

    # Every namechange row is keyed by the same ts_code it describes; no
    # row names both specimen codes, so no old-code -> new-code join exists.
    codes = {row["ts_code"] for row in rows}
    assert codes == {_SPECIMEN_DELISTED, _SPECIMEN_SIMILAR}
    for row in rows:
        assert row["ts_code"] in codes
        resolved = resolve_stock_id(row["ts_code"])
        assert resolved.stock_id == row["ts_code"]


def test_specimen_delisted_code_resolves_non_permanent(
    fetch: _FixtureFetch,
) -> None:
    """No direct vendor continuity evidence -> fail-closed, not a guess."""
    row = _row(fetch, "stock_basic", ts_code=_SPECIMEN_DELISTED)

    resolved = resolve_stock_id(_SPECIMEN_DELISTED, row)

    assert resolved.stock_id == _SPECIMEN_DELISTED
    assert resolved.raw_ts_code == _SPECIMEN_DELISTED
    assert resolved.is_permanent is False


def test_delisted_code_does_not_silently_resolve_to_similar_code(
    fetch: _FixtureFetch,
) -> None:
    row = _row(fetch, "stock_basic", ts_code=_SPECIMEN_DELISTED)

    resolved = resolve_stock_id(_SPECIMEN_DELISTED, row)

    assert resolved.stock_id != _SPECIMEN_SIMILAR
    assert resolved.stock_id == _SPECIMEN_DELISTED


# ---------------------------------------------------------------------------
# No-namechange-mapping stop condition: passthrough + non-permanent
# ---------------------------------------------------------------------------


def test_no_rename_mapping_passthrough_and_non_permanent_for_terminal_case(
    fetch: _FixtureFetch,
) -> None:
    """The documented no-signal outcome: passthrough, and non-permanent."""
    row = _row(fetch, "stock_basic", ts_code=_SPECIMEN_DELISTED)

    assert resolve_stock_id(_SPECIMEN_DELISTED, row).stock_id == _SPECIMEN_DELISTED
    assert resolve_stock_id(_SPECIMEN_DELISTED, row).is_permanent is False
    assert resolve_stock_id(_SPECIMEN_DELISTED).is_permanent is True  # no row


def test_paused_status_is_terminal_and_non_permanent() -> None:
    row = {
        "ts_code": "000999.SZ",
        "name": "暂停上市示例",
        "list_date": "20000101",
        "delist_date": "",
        "list_status": "P",
    }

    resolved = resolve_stock_id("000999.SZ", row)

    assert resolved.stock_id == "000999.SZ"
    assert resolved.is_permanent is False


def test_nonempty_delist_date_is_terminal_even_if_status_is_listed() -> None:
    """Defensive: an explicit delist_date is a terminal marker on its own."""
    row = {
        "ts_code": "000998.SZ",
        "name": "状态不一致示例",
        "list_date": "20000101",
        "delist_date": "20200101",
        "list_status": "L",
    }

    resolved = resolve_stock_id("000998.SZ", row)

    assert resolved.is_permanent is False


# ---------------------------------------------------------------------------
# Fail-closed contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["", "   ", "\t\n"])
def test_empty_ts_code_is_fail_closed(bad: str) -> None:
    with pytest.raises(ValueError):
        resolve_stock_id(bad)


@pytest.mark.parametrize("bad", [None, 123, ["000001.SZ"], b"000001.SZ"])
def test_non_string_ts_code_is_fail_closed(bad: object) -> None:
    with pytest.raises(TypeError):
        resolve_stock_id(bad)  # type: ignore[arg-type]


def test_whitespace_is_trimmed() -> None:
    assert resolve_stock_id("  000001.SZ  ").stock_id == "000001.SZ"
    assert resolve_stock_id("  000001.SZ  ").raw_ts_code == "000001.SZ"


def test_non_dict_row_is_fail_closed() -> None:
    with pytest.raises(TypeError):
        resolve_stock_id("000001.SZ", ["not", "a", "dict"])  # type: ignore[arg-type]


def test_mismatched_row_ts_code_is_fail_closed() -> None:
    row = {"ts_code": "600519.SH", "list_status": "L"}

    with pytest.raises(ValueError):
        resolve_stock_id("000001.SZ", row)


def test_resolved_identifier_is_frozen() -> None:
    resolved = resolve_stock_id("000001.SZ")

    with pytest.raises(Exception):
        resolved.stock_id = "MUTATED"  # type: ignore[misc]
