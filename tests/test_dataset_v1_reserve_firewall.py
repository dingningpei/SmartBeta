"""D8 development-accessor (``reserve_firewall.py``) tests, items 1-24.

Every test builds a synthetic ``tmp_path`` store with injected bindings; none
opens the real ``data_store`` or a reserve file, makes a provider call or reads
a credential.  An autouse socket guard and credential sentinel mirror the
D5/D6/D7 suites.

The functions are named ``test_itemNN_*`` so the report's test-to-item map
(items of the frozen spec section 7) is mechanical.
"""

from __future__ import annotations

import datetime as dt
import socket
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "dataset_v1" / "d8"))

import synth  # noqa: E402

from smart_beta.dataset_v1 import reserve_firewall as F  # noqa: E402


CREDENTIAL_SENTINEL = "d8-credential-sentinel-2c9f"


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch):
    def _refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("D8 tests must never open a socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield


@pytest.fixture(autouse=True)
def _credential_sentinel(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TIINGO_API_KEY", CREDENTIAL_SENTINEL)
    yield


def _make_store(
    tmp_path: Path,
    obs_rows: list[tuple[str, dt.date]] | None = None,
) -> tuple[Path, dict[str, F.FileBinding]]:
    if obs_rows is None:
        obs_rows = [
            ("AAA", dt.date(2023, 6, 30)),
            ("AAA", dt.date(2023, 6, 29)),
            ("BBB", dt.date(2023, 6, 30)),
        ]
    store = synth.build_store(tmp_path, obs_rows=obs_rows)
    return store.root, synth.accessor_bindings(store.root)


def _accessor(tmp_path: Path, obs_rows=None) -> F.DevelopmentAccessor:
    root, bindings = _make_store(tmp_path, obs_rows)
    return F.DevelopmentAccessor(root, bindings=bindings)


def _spy(calls: list) -> object:
    original = pq.read_table

    def wrapper(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return original(*args, **kwargs)

    return wrapper


# ---------------------------------------------------------------------------
# Items 1-11: argument and boundary validation
# ---------------------------------------------------------------------------


def test_item01_range_before_boundary_returns_rows(tmp_path):
    accessor = _accessor(tmp_path)
    frame = accessor.read(
        "observations",
        start=dt.date(2023, 6, 29),
        end=dt.date(2023, 6, 30),
        columns=["ticker", "date"],
    )
    assert len(frame) == 3
    assert set(frame["ticker"]) == {"AAA", "BBB"}


def test_item02_end_june30_accepted(tmp_path):
    accessor = _accessor(tmp_path)
    frame = accessor.read(
        "observations",
        start=dt.date(2023, 6, 1),
        end=dt.date(2023, 6, 30),
        columns=["ticker"],
    )
    assert len(frame) == 3


def test_item03_end_july3_rejected(tmp_path):
    accessor = _accessor(tmp_path)
    with pytest.raises(F.ReserveFirewallError):
        accessor.read(
            "observations",
            start=dt.date(2023, 6, 1),
            end=dt.date(2023, 7, 3),
            columns=["ticker"],
        )


def test_item04_start_on_or_after_july3_rejected(tmp_path):
    accessor = _accessor(tmp_path)
    for start in (dt.date(2023, 7, 3), dt.date(2023, 8, 1)):
        with pytest.raises(F.ReserveFirewallError):
            accessor.read(
                "observations",
                start=start,
                end=dt.date(2023, 8, 31),
                columns=["ticker"],
            )


def test_item05_crossing_boundary_rejected(tmp_path):
    accessor = _accessor(tmp_path)
    with pytest.raises(F.ReserveFirewallError):
        accessor.read(
            "observations",
            start=dt.date(2023, 6, 30),
            end=dt.date(2023, 7, 5),
            columns=["ticker"],
        )


def test_item06_end_inside_reserve_rejected(tmp_path):
    accessor = _accessor(tmp_path)
    with pytest.raises(F.ReserveFirewallError):
        accessor.read(
            "observations",
            start=dt.date(2023, 7, 4),
            end=dt.date(2023, 7, 5),
            columns=["ticker"],
        )


def test_item07_start_after_end_rejected(tmp_path):
    accessor = _accessor(tmp_path)
    with pytest.raises(F.AccessorInputError):
        accessor.read(
            "observations",
            start=dt.date(2023, 6, 30),
            end=dt.date(2023, 6, 29),
            columns=["ticker"],
        )


def test_item08_start_equals_end_single_day(tmp_path):
    accessor = _accessor(tmp_path)
    frame = accessor.read(
        "observations",
        start=dt.date(2023, 6, 30),
        end=dt.date(2023, 6, 30),
        columns=["ticker", "date"],
    )
    assert len(frame) == 2
    assert set(frame["ticker"]) == {"AAA", "BBB"}


def test_item09_malformed_string_rejected(tmp_path):
    accessor = _accessor(tmp_path)
    for bad in ("2023/06/30", "2023-6-30", "not-a-date", ""):
        with pytest.raises(F.AccessorInputError):
            accessor.read(
                "observations",
                start=bad,
                end=dt.date(2023, 6, 30),
                columns=["ticker"],
            )


def test_item10_datetime_and_tz_timestamp_rejected(tmp_path):
    import pandas as pd

    accessor = _accessor(tmp_path)
    for value in (
        dt.datetime(2023, 6, 30, 12, 0),
        dt.datetime(2023, 6, 30, 12, 0, tzinfo=dt.timezone.utc),
        pd.Timestamp("2023-06-30", tz="UTC"),
    ):
        with pytest.raises(F.AccessorInputError):
            accessor.read(
                "observations",
                start=value,
                end=dt.date(2023, 6, 30),
                columns=["ticker"],
            )


def test_item11_naive_timestamp_and_other_types_rejected(tmp_path):
    import pandas as pd

    accessor = _accessor(tmp_path)
    for value in (
        pd.Timestamp("2023-06-30"),
        20230630,
        None,
        ["2023-06-30"],
        b"2023-06-30",
    ):
        with pytest.raises(F.AccessorInputError):
            accessor.read(
                "observations",
                start=value,
                end=dt.date(2023, 6, 30),
                columns=["ticker"],
            )


# ---------------------------------------------------------------------------
# Items 12-15: no read before rejection, filters, poisoning, components
# ---------------------------------------------------------------------------


def test_item12_no_read_before_rejection(tmp_path, monkeypatch):
    accessor = _accessor(tmp_path)
    calls: list = []
    monkeypatch.setattr(F.pq, "read_table", _spy(calls))

    rejections = [
        dict(start=dt.date(2023, 6, 30), end=dt.date(2023, 6, 29), columns=["ticker"]),
        dict(start=dt.date(2023, 6, 1), end=dt.date(2023, 7, 3), columns=["ticker"]),
        dict(start=dt.date(2023, 7, 3), end=dt.date(2023, 7, 4), columns=["ticker"]),
        dict(start="bad", end=dt.date(2023, 6, 30), columns=["ticker"]),
        dict(start=dt.date(2023, 6, 1), end=dt.date(2023, 6, 30), columns=["nope"]),
        dict(
            start=dt.date(2023, 6, 1),
            end=dt.date(2023, 6, 30),
            columns=["ticker"],
            tickers=[],
        ),
    ]
    for kwargs in rejections:
        with pytest.raises(F.AccessorError):
            accessor.read("observations", **kwargs)
    assert calls == []

    # A valid read does call read_table exactly once.
    accessor.read(
        "observations",
        start=dt.date(2023, 6, 1),
        end=dt.date(2023, 6, 30),
        columns=["ticker"],
    )
    assert len(calls) == 1


def test_item13_read_uses_filters(tmp_path, monkeypatch):
    accessor = _accessor(tmp_path)
    calls: list = []
    monkeypatch.setattr(F.pq, "read_table", _spy(calls))
    accessor.read(
        "observations",
        start=dt.date(2023, 6, 1),
        end=dt.date(2023, 6, 30),
        columns=["ticker"],
    )
    assert len(calls) == 1
    kwargs = calls[0]["kwargs"]
    filters = kwargs["filters"]
    assert ("date", ">=", dt.date(2023, 6, 1)) in filters
    assert ("date", "<=", dt.date(2023, 6, 30)) in filters
    # The read never asks for the whole table (it projects columns).
    assert set(kwargs["columns"]) < set(F.OBSERVATION_SCHEMA)


def test_item14_poisoned_file_post_load_assertion(tmp_path, monkeypatch):
    accessor = _accessor(tmp_path)
    poisoned = pa.table(
        {
            "ticker": ["AAA"],
            "date": [dt.date(2023, 7, 5)],
        }
    )

    def fake_read_table(*args, **kwargs):
        return poisoned

    monkeypatch.setattr(F.pq, "read_table", fake_read_table)
    with pytest.raises(F.ReserveFirewallError):
        accessor.read(
            "observations",
            start=dt.date(2023, 6, 1),
            end=dt.date(2023, 6, 30),
            columns=["ticker", "date"],
        )


def test_item15_each_component_enforced(tmp_path):
    accessor = _accessor(tmp_path)
    for component in ("observations", "obs_flags", "ca_events"):
        with pytest.raises(F.ReserveFirewallError):
            accessor.read(
                component,
                start=dt.date(2023, 6, 1),
                end=dt.date(2023, 7, 3),
                columns=[],
            )
    # ca_events is guarded through its effective_date column.
    frame = accessor.read(
        "ca_events",
        start=dt.date(2023, 6, 1),
        end=dt.date(2023, 6, 30),
        columns=["stock_id", "effective_date"],
    )
    assert list(frame.columns) == ["stock_id", "effective_date"]


# ---------------------------------------------------------------------------
# Items 16-21: schema, bindings, symlinks, audit
# ---------------------------------------------------------------------------


def test_item16_unknown_column_and_no_metadata_method(tmp_path):
    accessor = _accessor(tmp_path)
    with pytest.raises(F.AccessorInputError):
        accessor.read(
            "observations",
            start=dt.date(2023, 6, 1),
            end=dt.date(2023, 6, 30),
            columns=["not_a_column"],
        )
    assert not hasattr(accessor, "metadata")
    # An unknown component is an input error too.
    with pytest.raises(F.AccessorInputError):
        accessor.read(
            "nope",
            start=dt.date(2023, 6, 1),
            end=dt.date(2023, 6, 30),
            columns=[],
        )


def test_item17_tampered_bindings_refused(tmp_path):
    root, bindings = _make_store(tmp_path)
    for component, path in (
        ("observations", "reserve_sealed/n-f874d3ecb61374d4/observations.parquet"),
        ("obs_flags", "raw/b-1/obs_flags.parquet"),
        ("ca_events", "dsc_evidence/ca_events.parquet"),
        ("observations", "normalized/../reserve_sealed/observations.parquet"),
    ):
        tampered = dict(bindings)
        tampered[component] = F.FileBinding(path, "0" * 64)
        accessor = F.DevelopmentAccessor(root, bindings=tampered)
        with pytest.raises(F.ReserveFirewallError):
            accessor.read(
                component,
                start=dt.date(2023, 6, 1),
                end=dt.date(2023, 6, 30),
                columns=[],
            )


def test_item18_symlink_escape_refused(tmp_path):
    root, bindings = _make_store(tmp_path)
    outside = tmp_path / "outside.parquet"
    synth.write_observations(outside, [("AAA", dt.date(2023, 6, 30))])
    link = root / "normalized" / F.D3_BUILD_ID / "escape.parquet"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside)
    tampered = dict(bindings)
    tampered["observations"] = F.FileBinding(
        f"normalized/{F.D3_BUILD_ID}/escape.parquet",
        synth.sha256_file(outside),
    )
    accessor = F.DevelopmentAccessor(root, bindings=tampered)
    with pytest.raises(F.ReserveFirewallError):
        accessor.read(
            "observations",
            start=dt.date(2023, 6, 1),
            end=dt.date(2023, 6, 30),
            columns=[],
        )


def test_item19_binding_hash_mismatch_fails_closed(tmp_path):
    root, bindings = _make_store(tmp_path)
    tampered = dict(bindings)
    tampered["observations"] = F.FileBinding(
        bindings["observations"].relative_path, "0" * 64
    )
    accessor = F.DevelopmentAccessor(root, bindings=tampered)
    with pytest.raises(F.ReserveFirewallError):
        accessor.read(
            "observations",
            start=dt.date(2023, 6, 1),
            end=dt.date(2023, 6, 30),
            columns=["ticker"],
        )


def test_item20_ticker_filter_correct(tmp_path):
    accessor = _accessor(tmp_path)
    frame = accessor.read(
        "observations",
        start=dt.date(2023, 6, 1),
        end=dt.date(2023, 6, 30),
        columns=["ticker", "date"],
        tickers=["AAA"],
    )
    assert set(frame["ticker"]) == {"AAA"}


def test_item21_audit_hook_no_reserve_sealed_open(tmp_path):
    accessor = _accessor(tmp_path)
    opened: list[str] = []
    active = {"on": False}

    def hook(event: str, args: tuple) -> None:
        if active["on"] and event == "open" and args:
            opened.append(str(args[0]))

    sys.addaudithook(hook)
    active["on"] = True
    try:
        accessor.read(
            "observations",
            start=dt.date(2023, 6, 1),
            end=dt.date(2023, 6, 30),
            columns=["ticker"],
        )
    finally:
        active["on"] = False

    assert opened, "the development file must have been opened"
    assert not any("reserve_sealed" in path for path in opened)
    assert not any("/raw/" in path for path in opened)


# ---------------------------------------------------------------------------
# Items 22-24: determinism, projection, no firewall imports
# ---------------------------------------------------------------------------


def test_item22_output_order_deterministic_permuted(tmp_path):
    rows = [
        ("AAA", dt.date(2023, 6, 30)),
        ("AAA", dt.date(2023, 6, 29)),
        ("BBB", dt.date(2023, 6, 30)),
    ]
    first = _accessor(tmp_path / "one", rows)
    second = _accessor(tmp_path / "two", list(reversed(rows)))
    frame_one = first.read(
        "observations",
        start=dt.date(2023, 6, 29),
        end=dt.date(2023, 6, 30),
        columns=["ticker", "date"],
    )
    frame_two = second.read(
        "observations",
        start=dt.date(2023, 6, 29),
        end=dt.date(2023, 6, 30),
        columns=["ticker", "date"],
    )
    assert frame_one.to_dict("records") == frame_two.to_dict("records")


def test_item23_column_projection_honored(tmp_path):
    accessor = _accessor(tmp_path)
    frame = accessor.read(
        "observations",
        start=dt.date(2023, 6, 1),
        end=dt.date(2023, 6, 30),
        columns=["ticker"],
    )
    assert list(frame.columns) == ["ticker"]


def test_item24_d6_d7a_d7b_do_not_import_firewall():
    import smart_beta.dataset_v1.universe as universe
    import smart_beta.dataset_v1.returns as returns
    import smart_beta.dataset_v1.delisting_bridge as delisting_bridge

    for module in (universe, returns, delisting_bridge):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "reserve_firewall" not in source
