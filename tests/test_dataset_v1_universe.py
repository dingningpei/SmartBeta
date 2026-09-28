"""D6 development PIT-universe tests.

Every test builds a synthetic ``tmp_path`` store with injected
:class:`~smart_beta.dataset_v1.universe.Bindings`; none touches the real
``data_store``, ``reserve_sealed`` or any credential.  An autouse socket guard
and credential sentinel mirror the D5/D-SC suites.  The test-to-item mapping is
recorded in the D6 completion report.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import socket
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd
import pyarrow as pa
import pytest

from smart_beta.dataset_v1 import universe
from smart_beta.vendors.tiingo.calendar_source import build_nyse_calendar

# ---------------------------------------------------------------------------
# Constants and shared calendar
# ---------------------------------------------------------------------------

CALENDAR = build_nyse_calendar(universe.CALENDAR_START, universe.CALENDAR_END)
CAL_DATES = [ts.date() for ts in CALENDAR.dates]
RESERVE_START = universe.RESERVE_START
F1 = date(2016, 1, 29)
FL = date(2017, 1, 31)  # later formation with >300 preceding sessions
CREDENTIAL_SENTINEL = "d6-credential-sentinel-7a1e"

D3_ID = "n-d6test"
D4_ID = "i-d6test"
D5_ID = "q-d6test"
DSC_ID = "c-d6test"

_STRING = pd.ArrowDtype(pa.string())
_DATE32 = pd.ArrowDtype(pa.date32())

_DISPOSITION: dict[str, str] = {
    "OHLC_INCONSISTENT": "CORRUPT",
    "NONPOSITIVE_PRICE": "CORRUPT",
    "MALFORMED_CA": "CORRUPT",
    "DUPLICATE_DATE": "CORRUPT",
    "OFF_CALENDAR_ROW": "CORRUPT",
    "INTERVAL_MISMATCH": "UNCERTIFIED_SEMANTICS",
    "CORP_ACTION_SUSPECT": "UNCERTIFIED_SEMANTICS",
    "VENDOR_NULL": "INSUFFICIENT_COVERAGE",
}

_VALUE_COLUMN_NAMES = {
    "close",
    "open",
    "high",
    "low",
    "volume",
    "adjClose",
    "adjHigh",
    "adjLow",
    "adjOpen",
    "adjVolume",
    "divCash",
    "splitFactor",
}


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch):
    def _refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("D6 tests must never open a socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield


@pytest.fixture(autouse=True)
def _credential_sentinel(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TIINGO_API_KEY", CREDENTIAL_SENTINEL)
    yield


# ---------------------------------------------------------------------------
# Calendar helpers
# ---------------------------------------------------------------------------


def _sessions(start: date, end: date) -> list[date]:
    return [d for d in CAL_DATES if start <= d <= end]


def _cutoff(f: date) -> date:
    return CALENDAR.previous_trading_day(f).date()


def _start_for_age(f: date, age: int) -> date:
    """The first session of an ``age``-session window ending at ``f-1``."""
    return CAL_DATES[CAL_DATES.index(f) - age]


def _window(f: date) -> list[date]:
    i = CAL_DATES.index(f)
    return CAL_DATES[i - universe.LIQUIDITY_WINDOW : i]


# ---------------------------------------------------------------------------
# Synthetic store
# ---------------------------------------------------------------------------


def _canon(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write_parquet(root: Path, rel: str, frame: pd.DataFrame) -> str:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, engine="pyarrow", index=False, compression="zstd")
    return _sha256_file(path)


def _write_manifest(root: Path, rel: str, field_name: str, files: dict) -> str:
    content = {"files_sha256": files}
    content[field_name] = hashlib.sha256(_canon(files).encode("utf-8")).hexdigest()
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((_canon(content) + "\n").encode("utf-8"))
    return content[field_name]


@dataclass
class _Store:
    tmp_path: Path
    name: str = "store"
    keys: list[dict] = field(default_factory=list)
    obs_rows: list[tuple] = field(default_factory=list)
    flag_rows: list[tuple] = field(default_factory=list)
    class_rows: list[tuple] = field(default_factory=list)
    policy_rows: list[tuple] = field(default_factory=list)
    _next: dict = field(default_factory=dict)
    bindings: universe.Bindings | None = None

    def __post_init__(self) -> None:
        self.root = self.tmp_path / self.name
        self.root.mkdir(parents=True, exist_ok=True)

    # -- declaration -----------------------------------------------------

    def key(
        self,
        ticker: str,
        *,
        listed_start: date,
        listed_end: date = date(2099, 1, 1),
        exchange: str = "NASDAQ",
        security_key: str | None = None,
        state: str = "INTERVAL_ASSIGNED",
        class_state: str | None = "COMMON_STOCK",
        class_knowledge: date = date(2000, 1, 1),
        class_reason: str = "COMMON_EVIDENCE",
        policy_state: str | None = None,
        policy_knowledge: date = date(2000, 1, 1),
        class_segments: Sequence[tuple] = (),
        policy_segments: Sequence[tuple] = (),
    ) -> str:
        sk = security_key or f"TIINGO:{ticker}:{listed_start.isoformat()}"
        self.keys.append(
            {
                "security_key": sk,
                "ticker": ticker,
                "exchange": exchange,
                "listed_start": listed_start,
                "listed_end": listed_end,
                "state": state,
            }
        )
        if class_state is not None:
            self.class_rows.append(
                (sk, class_knowledge, class_state, class_reason, "[]")
            )
        for segment in class_segments:
            self.class_rows.append((sk, segment[0], segment[1], segment[2], "[]"))
        if policy_state is not None:
            self.policy_rows.append(
                (sk, policy_knowledge, policy_state, "[]", "[]")
            )
        for segment in policy_segments:
            self.policy_rows.append((sk, segment[0], segment[1], "[]", "[]"))
        return sk

    def obs(
        self,
        ticker: str,
        d: date,
        close: float | None = 10.0,
        volume: int | None = 100,
        *,
        flag: str | None = None,
        disposition: str | None = None,
    ) -> int:
        idx = self._next.get(ticker, 0)
        self._next[ticker] = idx + 1
        self.obs_rows.append((ticker, d, close, volume, idx))
        if flag is not None:
            self.flag(ticker, d, idx, flag, disposition)
        return idx

    def flag(
        self,
        ticker: str,
        d: date,
        idx: int,
        klass: str,
        disposition: str | None = None,
    ) -> None:
        self.flag_rows.append(
            (ticker, d, idx, klass, disposition or _DISPOSITION[klass])
        )

    def add_sessions(
        self,
        ticker: str,
        start: date,
        end: date,
        *,
        close: float | None = 10.0,
        volume: int | None = 100,
        skip: Iterable[date] = (),
        flag: str | None = None,
        disposition: str | None = None,
    ) -> None:
        skipped = set(skip)
        for d in _sessions(start, end):
            if d in skipped:
                continue
            self.obs(
                ticker,
                d,
                close,
                volume,
                flag=flag,
                disposition=disposition,
            )

    def healthy(self, ticker: str, f: date = F1, *, age: int = 270) -> str:
        """A key with usable rows on every session of its ``age``-session window."""
        start = _start_for_age(f, age)
        self.key(ticker, listed_start=start)
        self.add_sessions(ticker, start, _cutoff(f))
        return ticker

    # -- materialise -----------------------------------------------------

    def build(self) -> universe.Bindings:
        root = self.root
        obs = pd.DataFrame(
            {
                "ticker": pd.Series([r[0] for r in self.obs_rows], dtype=_STRING),
                "date": pd.Series([r[1] for r in self.obs_rows], dtype=_DATE32),
                "close": pd.Series([r[2] for r in self.obs_rows], dtype="float64"),
                "volume": pd.Series([r[3] for r in self.obs_rows], dtype="Int64"),
                "raw_row_index": pd.Series(
                    [r[4] for r in self.obs_rows], dtype="int64"
                ),
                "adjClose": pd.Series(
                    [999999.0] * len(self.obs_rows), dtype="float64"
                ),
            }
        )
        ident = pd.DataFrame(
            {
                "security_key": pd.Series(
                    [k["security_key"] for k in self.keys], dtype=_STRING
                ),
                "ticker": pd.Series([k["ticker"] for k in self.keys], dtype=_STRING),
                "exchange": pd.Series(
                    [k["exchange"] for k in self.keys], dtype=_STRING
                ),
                "listed_start": pd.Series(
                    [k["listed_start"] for k in self.keys], dtype=_DATE32
                ),
                "listed_end": pd.Series(
                    [k["listed_end"] for k in self.keys], dtype=_DATE32
                ),
                "state": pd.Series([k["state"] for k in self.keys], dtype=_STRING),
            }
        )
        flags = pd.DataFrame(
            {
                "ticker": pd.Series([r[0] for r in self.flag_rows], dtype=_STRING),
                "date": pd.Series([r[1] for r in self.flag_rows], dtype=_DATE32),
                "raw_row_index": pd.Series(
                    [r[2] for r in self.flag_rows], dtype="int64"
                ),
                "class_or_flag": pd.Series(
                    [r[3] for r in self.flag_rows], dtype=_STRING
                ),
                "disposition": pd.Series(
                    [r[4] for r in self.flag_rows], dtype=_STRING
                ),
            }
        )
        class_tl = pd.DataFrame(
            {
                "security_key": pd.Series(
                    [r[0] for r in self.class_rows], dtype=_STRING
                ),
                "knowledge_date": pd.Series(
                    [r[1] for r in self.class_rows], dtype=_DATE32
                ),
                "state": pd.Series([r[2] for r in self.class_rows], dtype=_STRING),
                "reason": pd.Series([r[3] for r in self.class_rows], dtype=_STRING),
                "evidence_ids": pd.Series(
                    [r[4] for r in self.class_rows], dtype=_STRING
                ),
            }
        )
        policy_tl = pd.DataFrame(
            {
                "security_key": pd.Series(
                    [r[0] for r in self.policy_rows], dtype=_STRING
                ),
                "knowledge_date": pd.Series(
                    [r[1] for r in self.policy_rows], dtype=_DATE32
                ),
                "policy_state": pd.Series(
                    [r[2] for r in self.policy_rows], dtype=_STRING
                ),
                "active_sources": pd.Series(
                    [r[3] for r in self.policy_rows], dtype=_STRING
                ),
                "evidence_refs": pd.Series(
                    [r[4] for r in self.policy_rows], dtype=_STRING
                ),
            }
        )

        d3_obs = f"normalized/{D3_ID}/observations.parquet"
        d4_file = f"derived/identity/{D4_ID}/identity_assignment.parquet"
        d5_file = f"derived/qc/{D5_ID}/day_flags.parquet"
        dsc_class = f"derived/classification/{DSC_ID}/class_timeline.parquet"
        dsc_policy = f"derived/classification/{DSC_ID}/policy_timeline.parquet"

        sha_obs = _write_parquet(root, d3_obs, obs)
        sha_ident = _write_parquet(root, d4_file, ident)
        sha_flags = _write_parquet(root, d5_file, flags)
        sha_class = _write_parquet(root, dsc_class, class_tl)
        sha_policy = _write_parquet(root, dsc_policy, policy_tl)

        d3_hash = _write_manifest(
            root, f"normalized/{D3_ID}/build.json", "normalized_hash",
            {d3_obs: sha_obs},
        )
        d4_hash = _write_manifest(
            root, f"derived/identity/{D4_ID}/identity.json", "identity_hash",
            {d4_file: sha_ident},
        )
        d5_hash = _write_manifest(
            root, f"derived/qc/{D5_ID}/qc_report.json", "qc_hash",
            {d5_file: sha_flags},
        )
        dsc_hash = _write_manifest(
            root, f"derived/classification/{DSC_ID}/dsc.json", "dsc_hash",
            {dsc_class: sha_class, dsc_policy: sha_policy},
        )

        documents = []
        for name in sorted(universe._DOCUMENT_NAMES):
            path = root / "docs" / f"{name}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"normative document {name}\n", encoding="utf-8")
            documents.append(
                universe.DocumentBinding(name, str(path), _sha256_file(path))
            )

        self.bindings = universe.Bindings(
            d3=universe.StageBinding(
                "D3", D3_ID, f"normalized/{D3_ID}/build.json",
                "normalized_hash", d3_hash,
                (universe.FileBinding(d3_obs, sha_obs),),
            ),
            d4=universe.StageBinding(
                "D4", D4_ID, f"derived/identity/{D4_ID}/identity.json",
                "identity_hash", d4_hash,
                (universe.FileBinding(d4_file, sha_ident),),
            ),
            d5=universe.StageBinding(
                "D5", D5_ID, f"derived/qc/{D5_ID}/qc_report.json",
                "qc_hash", d5_hash,
                (universe.FileBinding(d5_file, sha_flags),),
            ),
            dsc=universe.StageBinding(
                "DSC", DSC_ID, f"derived/classification/{DSC_ID}/dsc.json",
                "dsc_hash", dsc_hash,
                (
                    universe.FileBinding(dsc_class, sha_class),
                    universe.FileBinding(dsc_policy, sha_policy),
                ),
            ),
            documents=tuple(documents),
        )
        return self.bindings

    def run(self) -> dict:
        assert self.bindings is not None
        return universe.run_universe(
            self.root, bindings=self.bindings, calendar=CALENDAR
        )


def _build(tmp_path: Path, name: str = "store") -> _Store:
    return _Store(tmp_path, name=name)


def _out_dir(store: _Store, content: dict) -> Path:
    return store.root / "derived" / "universe" / content["universe_id"]


def _read(store: _Store, content: dict, filename: str) -> pd.DataFrame:
    return pd.read_parquet(_out_dir(store, content) / filename)


def _audit_for(store: _Store, content: dict, f: date) -> pd.DataFrame:
    audit = _read(store, content, "audit_development.parquet")
    return audit[audit["formation_date"] == f].reset_index(drop=True)


def _row_for(frame: pd.DataFrame, security_key: str):
    rows = frame[frame["security_key"] == security_key]
    assert len(rows) == 1, f"expected one row for {security_key}, got {len(rows)}"
    return rows.iloc[0]


# ---------------------------------------------------------------------------
# Reserve firewall (items 25, 26, 27, 28)
# ---------------------------------------------------------------------------


def test_reserve_formation_date_refused(tmp_path: Path) -> None:
    store = _build(tmp_path)
    store.healthy("AAA")
    store.build()
    resolver = universe.Resolver(store.root, store.bindings)
    allowlisted = store.bindings.d3.files[0].relative_path
    resolver.resolve(allowlisted, formation_date=date(2023, 6, 30))
    with pytest.raises(universe.ReserveFirewallError):
        resolver.resolve(allowlisted, formation_date=date(2023, 7, 3))
    with pytest.raises(universe.ReserveFirewallError):
        resolver.resolve(allowlisted, formation_date=date(2023, 7, 31))


def test_reserve_sealed_path_component_refused(tmp_path: Path) -> None:
    store = _build(tmp_path)
    store.build()
    reserved = universe.StageBinding(
        "D3",
        D3_ID,
        "reserve_sealed/n-d6test/build.json",
        "normalized_hash",
        store.bindings.d3.manifest_hash,
        (universe.FileBinding("reserve_sealed/n-d6test/observations.parquet", "0"),),
    )
    bindings = universe.Bindings(
        d3=reserved,
        d4=store.bindings.d4,
        d5=store.bindings.d5,
        dsc=store.bindings.dsc,
        documents=store.bindings.documents,
    )
    resolver = universe.Resolver(store.root, bindings)
    with pytest.raises(universe.ReserveFirewallError):
        resolver.resolve("reserve_sealed/n-d6test/observations.parquet")


def test_non_allowlisted_path_refused(tmp_path: Path) -> None:
    store = _build(tmp_path)
    store.healthy("AAA")
    store.build()
    resolver = universe.Resolver(store.root, store.bindings)
    with pytest.raises(universe.ReserveFirewallError):
        resolver.resolve("normalized/other/observations.parquet")
    with pytest.raises(universe.ReserveFirewallError):
        resolver.resolve("derived/qc/other/day_flags.parquet")
    with pytest.raises(universe.UniverseInputError):
        resolver.resolve("/tmp/absolute.parquet")
    with pytest.raises(universe.UniverseInputError):
        resolver.resolve("../escape.parquet")


def test_reserve_dated_observation_row_refused(tmp_path: Path) -> None:
    store = _build(tmp_path)
    store.healthy("AAA")
    store.obs("AAA", RESERVE_START, 10.0, 100)
    store.build()
    with pytest.raises(universe.ReserveFirewallError):
        store.run()
    # A failed run writes no artifact.
    assert not (store.root / "derived" / "universe").exists()


def test_reserve_dated_day_flag_refused(tmp_path: Path) -> None:
    store = _build(tmp_path)
    ticker = store.healthy("AAA")
    store.flag(ticker, RESERVE_START, 0, "OHLC_INCONSISTENT")
    store.build()
    with pytest.raises(universe.ReserveFirewallError):
        store.run()
    assert not (store.root / "derived" / "universe").exists()


# ---------------------------------------------------------------------------
# Future-data invariance (items 1, 2, 3, 4, 21, 22)
# ---------------------------------------------------------------------------


def _baseline(tmp_path: Path, name: str) -> tuple[_Store, dict, str]:
    store = _build(tmp_path, name=name)
    start = _start_for_age(F1, 270)
    sk = store.key(
        "AAA",
        listed_start=start,
        class_state="COMMON_STOCK",
    )
    store.add_sessions("AAA", start, _cutoff(F1))
    store.build()
    content = store.run()
    return store, content, sk


def test_future_valid_row_after_f_does_not_change_u_f(tmp_path: Path) -> None:
    base_store, base, sk = _baseline(tmp_path, "base")
    mut_store = _build(tmp_path, "mut")
    start = _start_for_age(F1, 270)
    mut_store.key("AAA", listed_start=start)
    mut_store.add_sessions("AAA", start, _cutoff(F1))
    mut_store.obs("AAA", F1, 10.0, 100)  # dated on the formation session
    mut_store.obs("AAA", CAL_DATES[CAL_DATES.index(F1) + 3], 10.0, 100)
    mut_store.build()
    mutated = mut_store.run()
    pd.testing.assert_frame_equal(
        _audit_for(base_store, base, F1), _audit_for(mut_store, mutated, F1)
    )
    assert _row_for(_audit_for(base_store, base, F1), sk)["reason"] == "ELIGIBLE"


def test_future_corrupt_row_after_f_does_not_change_u_f(tmp_path: Path) -> None:
    base_store, base, sk = _baseline(tmp_path, "base")
    mut_store = _build(tmp_path, "mut")
    start = _start_for_age(F1, 270)
    mut_store.key("AAA", listed_start=start)
    mut_store.add_sessions("AAA", start, _cutoff(F1))
    future = CAL_DATES[CAL_DATES.index(F1) + 2]
    mut_store.obs("AAA", future, 10.0, 100, flag="OHLC_INCONSISTENT")
    mut_store.build()
    mutated = mut_store.run()
    pd.testing.assert_frame_equal(
        _audit_for(base_store, base, F1), _audit_for(mut_store, mutated, F1)
    )


def test_future_delisting_after_f_does_not_change_u_f(tmp_path: Path) -> None:
    base_store, base, sk = _baseline(tmp_path, "base")
    mut_store = _build(tmp_path, "mut")
    start = _start_for_age(F1, 270)
    later_end = CAL_DATES[CAL_DATES.index(F1) + 20]
    mut_store.key("AAA", listed_start=start, listed_end=later_end)
    mut_store.add_sessions("AAA", start, _cutoff(F1))
    mut_store.build()
    mutated = mut_store.run()
    pd.testing.assert_frame_equal(
        _audit_for(base_store, base, F1), _audit_for(mut_store, mutated, F1)
    )


def test_future_dsc_evidence_does_not_change_u_f(tmp_path: Path) -> None:
    base_store, base, sk = _baseline(tmp_path, "base")
    mut_store = _build(tmp_path, "mut")
    start = _start_for_age(F1, 270)
    mut_store.key(
        "AAA",
        listed_start=start,
        class_segments=[(date(2020, 1, 2), "NON_COMMON", "BDC")],
        policy_segments=[(date(2020, 1, 2), "EXCLUDED")],
    )
    mut_store.add_sessions("AAA", start, _cutoff(F1))
    mut_store.build()
    mutated = mut_store.run()
    pd.testing.assert_frame_equal(
        _audit_for(base_store, base, F1), _audit_for(mut_store, mutated, F1)
    )


def test_future_d5_flags_do_not_change_u_f(tmp_path: Path) -> None:
    base_store, base, sk = _baseline(tmp_path, "base")
    mut_store = _build(tmp_path, "mut")
    start = _start_for_age(F1, 270)
    mut_store.key("AAA", listed_start=start)
    mut_store.add_sessions("AAA", start, _cutoff(F1))
    future = CAL_DATES[CAL_DATES.index(F1) + 5]
    idx = mut_store.obs("AAA", future, 10.0, None)
    mut_store.flag("AAA", future, idx, "VENDOR_NULL")
    mut_store.build()
    mutated = mut_store.run()
    pd.testing.assert_frame_equal(
        _audit_for(base_store, base, F1), _audit_for(mut_store, mutated, F1)
    )


# ---------------------------------------------------------------------------
# Listing age and population boundaries (items 5, 6, 7, 23, 32)
# ---------------------------------------------------------------------------


def test_listing_age_251_ineligible(tmp_path: Path) -> None:
    store = _build(tmp_path)
    start = _start_for_age(F1, 251)
    sk = store.key("AAA", listed_start=start)
    store.add_sessions("AAA", start, _cutoff(F1))
    store.build()
    content = store.run()
    row = _row_for(_audit_for(store, content, F1), sk)
    assert row["listing_age_sessions"] == 251
    assert row["reason"] == "LISTING_AGE_INSUFFICIENT"


def test_listing_age_252_eligible(tmp_path: Path) -> None:
    store = _build(tmp_path)
    start = _start_for_age(F1, 252)
    sk = store.key("AAA", listed_start=start)
    store.add_sessions("AAA", start, _cutoff(F1))
    store.build()
    content = store.run()
    row = _row_for(_audit_for(store, content, F1), sk)
    assert row["listing_age_sessions"] == 252
    assert row["reason"] == "ELIGIBLE"


def test_key_listed_shortly_before_f_ineligible(tmp_path: Path) -> None:
    store = _build(tmp_path)
    start = CAL_DATES[CAL_DATES.index(F1) - 5]
    sk = store.key("AAA", listed_start=start)
    store.add_sessions("AAA", start, _cutoff(F1))
    store.build()
    content = store.run()
    assert _row_for(_audit_for(store, content, F1), sk)["reason"] == (
        "LISTING_AGE_INSUFFICIENT"
    )


def test_listed_end_on_f_admitted(tmp_path: Path) -> None:
    store = _build(tmp_path)
    start = _start_for_age(F1, 270)
    sk = store.key("AAA", listed_start=start, listed_end=F1)
    store.add_sessions("AAA", start, _cutoff(F1))
    store.build()
    content = store.run()
    row = _row_for(_audit_for(store, content, F1), sk)
    assert row["reason"] == "ELIGIBLE"


def test_listed_end_boundaries(tmp_path: Path) -> None:
    store = _build(tmp_path)
    start = _start_for_age(F1, 270)
    excluded = store.key(
        "AAA", security_key="TIINGO:AAA:x", listed_start=start,
        listed_end=CAL_DATES[CAL_DATES.index(F1) - 1],
    )
    on_f = store.key(
        "BBB", security_key="TIINGO:BBB:x", listed_start=start, listed_end=F1
    )
    after_f = store.key(
        "CCC", security_key="TIINGO:CCC:x", listed_start=start,
        listed_end=CAL_DATES[CAL_DATES.index(F1) + 3],
    )
    for ticker in ("AAA", "BBB", "CCC"):
        store.add_sessions(ticker, start, _cutoff(F1))
    store.build()
    content = store.run()
    audit = _audit_for(store, content, F1)
    keys = set(audit["security_key"])
    assert excluded not in keys
    assert on_f in keys and after_f in keys
    forms = _read(store, content, "formations.parquet")
    f1 = forms[forms["formation_date"] == F1].iloc[0]
    assert f1["n_population"] == 2
    summary = [
        r for r in content["formations"] if r["formation_date"] == F1.isoformat()
    ][0]
    assert summary["n_not_listed_at_formation"] == 1


# ---------------------------------------------------------------------------
# Liquidity window (items 8, 9, 10, 11, 12, 33, 34)
# ---------------------------------------------------------------------------


def _omit_sessions(
    store: _Store, ticker: str, f: date, count: int, *, age: int = 300,
    flag: str | None = None, disposition: str | None = None,
    close: float | None = 10.0, volume: int | None = 100,
) -> str:
    start = _start_for_age(f, age)
    sk = store.key(ticker, listed_start=start)
    window = _window(f)
    omitted = set(window[-count:]) if count else set()
    store.add_sessions(
        ticker, start, _cutoff(f), skip=omitted,
        flag=flag, disposition=disposition, close=close, volume=volume,
    )
    return sk


def test_49_liquidity_sessions_ineligible(tmp_path: Path) -> None:
    store = _build(tmp_path)
    sk = _omit_sessions(store, "AAA", FL, 14)
    store.build()
    content = store.run()
    row = _row_for(_audit_for(store, content, FL), sk)
    assert row["trailing_valid_sessions"] == 49
    assert row["missing_numerator"] == 14
    assert row["missing_denominator"] == 300
    assert row["reason"] == "TRAILING_LIQUIDITY_INSUFFICIENT"


def test_50_liquidity_sessions_eligible(tmp_path: Path) -> None:
    store = _build(tmp_path)
    sk = _omit_sessions(store, "AAA", FL, 13)
    store.build()
    content = store.run()
    row = _row_for(_audit_for(store, content, FL), sk)
    assert row["trailing_valid_sessions"] == 50
    assert row["reason"] == "ELIGIBLE"


def test_zero_volume_row_counts_as_liquidity_valid(tmp_path: Path) -> None:
    store = _build(tmp_path)
    start = _start_for_age(F1, 260)
    sk = store.key("AAA", listed_start=start)
    window = _window(F1)
    omitted = set(window[-13:])
    for d in _sessions(start, _cutoff(F1)):
        if d in omitted:
            continue
        if d in window:
            store.obs("AAA", d, 10.0, 0)  # zero volume, valid
        else:
            store.obs("AAA", d, 10.0, 100)
    store.build()
    content = store.run()
    row = _row_for(_audit_for(store, content, F1), sk)
    assert row["missing_numerator"] == 13
    assert row["missing_denominator"] == 260
    assert row["trailing_valid_sessions"] == 50
    assert row["median_dv"] == 0.0
    assert row["reason"] == "ELIGIBLE"


def test_corrupt_only_session_counts_as_missing(tmp_path: Path) -> None:
    store = _build(tmp_path)
    start = _start_for_age(F1, 260)
    sk = store.key("AAA", listed_start=start)
    window = _window(F1)
    corrupt = set(window[-13:])
    for d in _sessions(start, _cutoff(F1)):
        if d in corrupt:
            store.obs("AAA", d, 10.0, 100, flag="OHLC_INCONSISTENT")
        else:
            store.obs("AAA", d, 10.0, 100)
    store.build()
    content = store.run()
    row = _row_for(_audit_for(store, content, F1), sk)
    assert row["missing_numerator"] == 13
    assert row["trailing_valid_sessions"] == 50
    assert row["reason"] == "ELIGIBLE"


def test_vendor_null_session_counts_as_missing(tmp_path: Path) -> None:
    store = _build(tmp_path)
    start = _start_for_age(F1, 260)
    sk = store.key("AAA", listed_start=start)
    window = _window(F1)
    vnull = set(window[-13:])
    for d in _sessions(start, _cutoff(F1)):
        if d in vnull:
            store.obs("AAA", d, 10.0, None, flag="VENDOR_NULL")
        else:
            store.obs("AAA", d, 10.0, 100)
    store.build()
    content = store.run()
    row = _row_for(_audit_for(store, content, F1), sk)
    assert row["missing_numerator"] == 13
    assert row["trailing_valid_sessions"] == 50
    assert row["reason"] == "ELIGIBLE"


def test_off_calendar_corrupt_row_enters_neither_count(tmp_path: Path) -> None:
    base_store, base, sk = _baseline(tmp_path, "base")
    mut_store = _build(tmp_path, "mut")
    start = _start_for_age(F1, 270)
    mut_store.key("AAA", listed_start=start)
    mut_store.add_sessions("AAA", start, _cutoff(F1))
    off = date(2016, 1, 16)  # Saturday: not an NYSE session
    assert off not in CAL_DATES
    mut_store.obs("AAA", off, 10.0, 100, flag="OFF_CALENDAR_ROW")
    mut_store.build()
    mutated = mut_store.run()
    pd.testing.assert_frame_equal(
        _audit_for(base_store, base, F1), _audit_for(mut_store, mutated, F1)
    )


def test_corrupt_row_with_valid_close_volume_not_liquidity_valid(
    tmp_path: Path,
) -> None:
    store = _build(tmp_path)
    start = _start_for_age(F1, 260)
    sk = store.key("AAA", listed_start=start)
    window = _window(F1)
    corrupt = set(window[-13:])
    for d in _sessions(start, _cutoff(F1)):
        if d in corrupt:
            store.obs("AAA", d, 10.0, 100, flag="OHLC_INCONSISTENT")
        else:
            store.obs("AAA", d, 10.0, 100)
    store.build()
    content = store.run()
    row = _row_for(_audit_for(store, content, F1), sk)
    # The 13 corrupt sessions have valid close/volume but must not count.
    assert row["trailing_valid_sessions"] == 50
    assert row["reason"] == "ELIGIBLE"


def test_corp_action_suspect_remains_usable(tmp_path: Path) -> None:
    store = _build(tmp_path)
    start = _start_for_age(F1, 260)
    sk = store.key("AAA", listed_start=start)
    window = _window(F1)
    suspect = set(window[-13:])
    for d in _sessions(start, _cutoff(F1)):
        if d in suspect:
            store.obs("AAA", d, 10.0, 100, flag="CORP_ACTION_SUSPECT")
        else:
            store.obs("AAA", d, 10.0, 100)
    store.build()
    content = store.run()
    row = _row_for(_audit_for(store, content, F1), sk)
    assert row["missing_numerator"] == 0
    assert row["trailing_valid_sessions"] == 63
    assert row["reason"] == "ELIGIBLE"


# ---------------------------------------------------------------------------
# Class / policy checks (items 14, 15, 16, 17)
# ---------------------------------------------------------------------------


def test_class_unresolved_ineligible(tmp_path: Path) -> None:
    store = _build(tmp_path)
    start = _start_for_age(F1, 270)
    sk = store.key("AAA", listed_start=start, class_state=None)
    store.add_sessions("AAA", start, _cutoff(F1))
    store.build()
    content = store.run()
    row = _row_for(_audit_for(store, content, F1), sk)
    assert row["class_state"] == "CLASS_UNRESOLVED"
    assert row["reason"] == "NOT_COMMON_STOCK"


def test_policy_excluded_ineligible(tmp_path: Path) -> None:
    store = _build(tmp_path)
    start = _start_for_age(F1, 270)
    sk = store.key(
        "AAA", listed_start=start,
        policy_state="EXCLUDED", policy_knowledge=date(2010, 1, 4),
    )
    store.add_sessions("AAA", start, _cutoff(F1))
    store.build()
    content = store.run()
    row = _row_for(_audit_for(store, content, F1), sk)
    assert row["policy_state"] == "EXCLUDED"
    assert row["reason"] == "ISSUER_POLICY_EXCLUDED"


def test_policy_unresolved_ineligible(tmp_path: Path) -> None:
    store = _build(tmp_path)
    start = _start_for_age(F1, 270)
    sk = store.key(
        "AAA", listed_start=start,
        policy_state="ISSUER_STATUS_UNRESOLVED",
        policy_knowledge=date(2010, 1, 4),
    )
    store.add_sessions("AAA", start, _cutoff(F1))
    store.build()
    content = store.run()
    row = _row_for(_audit_for(store, content, F1), sk)
    assert row["policy_state"] == "ISSUER_STATUS_UNRESOLVED"
    assert row["reason"] == "ISSUER_POLICY_UNRESOLVED"


def test_policy_applied_point_in_time(tmp_path: Path) -> None:
    store = _build(tmp_path)
    start = _start_for_age(F1, 270)
    # The exclusion becomes knowable only well after F1's cutoff.
    sk = store.key(
        "AAA", listed_start=start,
        policy_segments=[(date(2018, 1, 2), "EXCLUDED")],
    )
    # Cover the full development span so later formations stay populated.
    store.add_sessions("AAA", start, date(2023, 6, 29))
    store.build()
    content = store.run()
    f1_row = _row_for(_audit_for(store, content, F1), sk)
    assert f1_row["policy_state"] == "NO_REGULATORY_EXCLUSION_OBSERVED"
    assert f1_row["reason"] == "ELIGIBLE"
    later = date(2019, 6, 28)
    later_row = _row_for(_audit_for(store, content, later), sk)
    assert later_row["policy_state"] == "EXCLUDED"
    assert later_row["reason"] == "ISSUER_POLICY_EXCLUDED"


# ---------------------------------------------------------------------------
# Missingness (items 35, 36)
# ---------------------------------------------------------------------------


def test_missingness_exactly_five_percent_passes(tmp_path: Path) -> None:
    store = _build(tmp_path)
    start = _start_for_age(F1, 260)
    sk = store.key("AAA", listed_start=start)
    # Omit 13 early sessions after the first usable one (outside W_f).
    first = _start_for_age(F1, 260)
    omitted = set(_sessions(first, _cutoff(F1))[1:14])
    store.add_sessions("AAA", start, _cutoff(F1), skip=omitted)
    store.build()
    content = store.run()
    row = _row_for(_audit_for(store, content, F1), sk)
    assert row["missing_numerator"] == 13
    assert row["missing_denominator"] == 260
    assert row["reason"] == "ELIGIBLE"


def test_missingness_just_over_five_percent_fails(tmp_path: Path) -> None:
    store = _build(tmp_path)
    start = _start_for_age(F1, 260)
    sk = store.key("AAA", listed_start=start)
    first = _start_for_age(F1, 260)
    omitted = set(_sessions(first, _cutoff(F1))[1:15])
    store.add_sessions("AAA", start, _cutoff(F1), skip=omitted)
    store.build()
    content = store.run()
    row = _row_for(_audit_for(store, content, F1), sk)
    assert row["missing_numerator"] == 14
    assert row["missing_denominator"] == 260
    assert row["reason"] == "PIT_MISSINGNESS_EXCEEDED"


def test_even_n_median_convention(tmp_path: Path) -> None:
    store = _build(tmp_path)
    start = _start_for_age(F1, 260)
    sk = store.key("AAA", listed_start=start)
    window = _window(F1)
    omitted = window[-1]
    # 62 liquidity-valid sessions: 30 x DV=1, DV=5, DV=7, 30 x DV=100.
    # Sorted middle pair is (5, 7); the median is 6.0, the mean ~49.06, and
    # neither middle element equals the median.
    values = [1] * 30 + [5, 7] + [100] * 30
    assert len(values) == 62
    for d, volume in zip(window[:-1], values):
        store.obs("AAA", d, 1.0, volume)
    for d in _sessions(start, _cutoff(F1)):
        if d in window:
            continue
        store.obs("AAA", d, 1.0, 1000)
    store.build()
    content = store.run()
    row = _row_for(_audit_for(store, content, F1), sk)
    assert row["trailing_valid_sessions"] == 62
    assert row["median_dv"] == 6.0


def test_sessions_before_first_usable_excluded(tmp_path: Path) -> None:
    store = _build(tmp_path)
    start = _start_for_age(F1, 260)
    sk = store.key("AAA", listed_start=start)
    sessions = _sessions(start, _cutoff(F1))
    # No observation for the first 20 sessions; the first usable is sessions[20].
    store.add_sessions("AAA", sessions[20], _cutoff(F1))
    store.build()
    content = store.run()
    row = _row_for(_audit_for(store, content, F1), sk)
    # Sessions before first_f are in neither D_f nor M_f.
    assert row["missing_denominator"] == 240
    assert row["missing_numerator"] == 0
    assert row["listing_age_sessions"] == 260
    assert row["reason"] == "ELIGIBLE"


# ---------------------------------------------------------------------------
# Ordering, ties and top sets (items 18, 30) -- heavy, shared fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def tied_store(tmp_path_factory: pytest.TempPathFactory):
    tmp_path = tmp_path_factory.mktemp("tied")
    store = _Store(tmp_path, name="tied")
    start = _start_for_age(F1, 260)
    # Insert in reverse so a stable sort without the security_key tie-break
    # would produce a visibly different order.
    for i in reversed(range(501)):
        ticker = f"T{i:04d}"
        store.key(ticker, listed_start=start)
        store.add_sessions(ticker, start, _cutoff(F1))
    store.build()
    content = store.run()
    return store, content, start


def test_dv_tie_resolved_by_security_key(tied_store) -> None:
    store, content, start = tied_store
    development = _read(store, content, "universe_development.parquet")
    f1 = development[
        (development["formation_date"] == F1) & (development["top_500"])
    ]
    ordered = f1.sort_values("rank")
    assert len(ordered) == 500
    assert ordered.iloc[499]["rank"] == 500
    assert ordered.iloc[499]["security_key"] == f"TIINGO:T0499:{start.isoformat()}"
    # Rank 501 is the next key in code-point order; it is outside top-500.
    all_f1 = development[development["formation_date"] == F1].sort_values("rank")
    assert all_f1.iloc[500]["rank"] == 501
    assert all_f1.iloc[500]["security_key"] == f"TIINGO:T0500:{start.isoformat()}"
    assert bool(all_f1.iloc[500]["top_500"]) is False
    assert bool(all_f1.iloc[500]["top_1000"]) is True


def test_top_sets_are_nested_and_rank_thresholds(tied_store) -> None:
    store, content, start = tied_store
    development = _read(store, content, "universe_development.parquet")
    f1 = development[development["formation_date"] == F1]
    assert len(f1) == 501
    ranks = sorted(f1["rank"].tolist())
    assert ranks == list(range(1, 502))
    assert int(f1["top_200"].sum()) == 200
    assert int(f1["top_500"].sum()) == 500
    assert int(f1["top_1000"].sum()) == 501
    assert bool((f1.loc[f1["top_200"], "top_500"]).all())
    assert bool((f1.loc[f1["top_500"], "top_1000"]).all())
    assert bool((f1.loc[f1["top_200"], "top_1000"]).all())
    assert bool((~f1.loc[f1["rank"] > 200, "top_200"]).all())
    assert bool((~f1.loc[f1["rank"] > 500, "top_500"]).all())


def test_ranking_is_descending_by_median_dv(tmp_path: Path) -> None:
    store = _build(tmp_path)
    start = _start_for_age(F1, 260)
    low = store.key("AAA", listed_start=start)
    high = store.key("BBB", listed_start=start)
    store.add_sessions("AAA", start, _cutoff(F1), volume=100)  # DV 1000
    store.add_sessions("BBB", start, _cutoff(F1), volume=200)  # DV 2000
    store.build()
    content = store.run()
    development = _read(store, content, "universe_development.parquet")
    f1 = development[development["formation_date"] == F1].set_index(
        "security_key"
    )
    assert f1.loc[high, "rank"] == 1
    assert f1.loc[low, "rank"] == 2


# ---------------------------------------------------------------------------
# Eligible thresholds 499 / 500 (item 24)
# ---------------------------------------------------------------------------


def _many_keys(tmp_path: Path, name: str, count: int, *, one_short: bool) -> tuple:
    store = _build(tmp_path, name=name)
    start = _start_for_age(F1, 260)
    for i in range(count):
        ticker = f"K{i:04d}"
        listed_start = start
        if one_short and i == 0:
            listed_start = _start_for_age(F1, 251)
        store.key(ticker, listed_start=listed_start)
        store.add_sessions(ticker, listed_start, _cutoff(F1))
    store.build()
    return store, store.run()


def test_eligible_count_500_ok(tmp_path: Path) -> None:
    store, content = _many_keys(tmp_path, "five-hundred", 500, one_short=False)
    summary = [
        r for r in content["formations"] if r["formation_date"] == F1.isoformat()
    ][0]
    assert summary["n_eligible"] == 500
    assert summary["status"] == "OK"
    assert summary["gate11"] == "PASS"
    formations = _read(store, content, "formations.parquet")
    f1 = formations[formations["formation_date"] == F1].iloc[0]
    assert f1["status"] == "OK"
    # The overall gate aggregates the per-formation statuses consistently.
    all_ok = all(
        r["status"] == "OK"
        for r in content["formations"]
        if r["partition"] == "DEVELOPMENT"
    )
    assert content["gate_11"]["development"] == ("PASS" if all_ok else "FAIL")


def test_eligible_count_499_shortfall(tmp_path: Path) -> None:
    store, content = _many_keys(tmp_path, "four-ninety-nine", 500, one_short=True)
    summary = [
        r for r in content["formations"] if r["formation_date"] == F1.isoformat()
    ][0]
    assert summary["n_eligible"] == 499
    assert summary["status"] == "UNIVERSE_SHORTFALL"
    assert summary["gate11"] == "FAIL"
    assert content["gate_11"]["development"] == "FAIL"
    # A shortfall is never rescued by reducing N or dropping top-500 rows.
    assert summary["n_population"] == 500
    assert summary["top_1000_shortfall"] is True
    development = _read(store, content, "universe_development.parquet")
    f1 = development[development["formation_date"] == F1]
    assert len(f1) == 499
    assert bool(f1["top_500"].all()) is True


# ---------------------------------------------------------------------------
# Identity / exchange checks (reason precedence)
# ---------------------------------------------------------------------------


def test_non_interval_state_ineligible(tmp_path: Path) -> None:
    store = _build(tmp_path)
    start = _start_for_age(F1, 270)
    sk = store.key("AAA", listed_start=start, state="IDENTITY_AMBIGUOUS")
    store.add_sessions("AAA", start, _cutoff(F1))
    store.build()
    content = store.run()
    assert _row_for(_audit_for(store, content, F1), sk)["reason"] == (
        "IDENTITY_NOT_ADMISSIBLE"
    )


def test_ineligible_exchange_ineligible(tmp_path: Path) -> None:
    store = _build(tmp_path)
    start = _start_for_age(F1, 270)
    sk = store.key("AAA", listed_start=start, exchange="ARCA")
    store.add_sessions("AAA", start, _cutoff(F1))
    store.build()
    content = store.run()
    assert _row_for(_audit_for(store, content, F1), sk)["reason"] == (
        "EXCHANGE_NOT_ELIGIBLE"
    )


def test_eligible_exchanges_admitted(tmp_path: Path) -> None:
    store = _build(tmp_path)
    start = _start_for_age(F1, 270)
    expected = set()
    for i, exchange in enumerate(("NYSE", "NASDAQ", "NYSE MKT", "AMEX")):
        ticker = f"E{i}"
        sk = store.key(ticker, listed_start=start, exchange=exchange)
        store.add_sessions(ticker, start, _cutoff(F1))
        expected.add(sk)
    store.build()
    content = store.run()
    audit = _audit_for(store, content, F1)
    eligible = set(audit[audit["reason"] == "ELIGIBLE"]["security_key"])
    assert expected <= eligible


# ---------------------------------------------------------------------------
# Reserve outputs (item 29)
# ---------------------------------------------------------------------------


def test_reserve_formations_deferred_and_no_market_fields(tmp_path: Path) -> None:
    store = _build(tmp_path)
    store.healthy("AAA")
    store.build()
    content = store.run()

    formations = _read(store, content, "formations.parquet")
    reserve = formations[formations["partition"] == "RESERVE"]
    assert len(reserve) == universe.N_RESERVE
    assert (reserve["status"] == "DEFERRED").all()
    for column in ("n_population", "n_eligible", "top_1000_shortfall"):
        assert reserve[column].isna().all()

    development = _read(store, content, "universe_development.parquet")
    audit = _read(store, content, "audit_development.parquet")
    assert development["formation_date"].max() <= universe.DEVELOPMENT_END
    assert audit["formation_date"].max() <= universe.DEVELOPMENT_END

    for frame in (development, formations, audit):
        assert _VALUE_COLUMN_NAMES.isdisjoint(set(frame.columns))

    summary = [r for r in content["formations"] if r["partition"] == "RESERVE"]
    assert len(summary) == universe.N_RESERVE
    assert all(r["status"] == "DEFERRED" for r in summary)
    assert all(r["n_eligible"] is None for r in summary)
    assert content["gate_11"]["reserve_gate11"] == "DEFERRED / NOT ASSESSED"


# ---------------------------------------------------------------------------
# Schedule (item 31)
# ---------------------------------------------------------------------------


def test_schedule_counts_and_boundaries() -> None:
    schedule = universe.build_schedule(CALENDAR)
    assert len(schedule.full) == 128
    assert schedule.full[0] == date(2016, 1, 29)
    assert schedule.full[-1] == date(2026, 8, 31)
    assert len(schedule.development) == 90
    assert schedule.development[-1] == date(2023, 6, 30)
    assert len(schedule.reserve) == 38
    assert schedule.reserve[0] == date(2023, 7, 31)
    assert schedule.reserve[-1] == date(2026, 8, 31)


# ---------------------------------------------------------------------------
# Determinism and invariance (items 19, 20, 38, property)
# ---------------------------------------------------------------------------


def test_shuffled_input_rows_invariant(tmp_path: Path) -> None:
    def build(name: str, reverse: bool) -> tuple:
        store = _build(tmp_path, name=name)
        start = _start_for_age(F1, 260)
        for i in range(3):
            ticker = f"S{i}"
            store.key(ticker, listed_start=start)
            store.add_sessions(ticker, start, _cutoff(F1))
        if reverse:
            store.obs_rows = list(reversed(store.obs_rows))
            store.flag_rows = list(reversed(store.flag_rows))
            store.class_rows = list(reversed(store.class_rows))
            store.policy_rows = list(reversed(store.policy_rows))
            store.keys = list(reversed(store.keys))
        store.build()
        return store, store.run()

    ordered, ordered_content = build("ordered", False)
    shuffled, shuffled_content = build("shuffled", True)
    for filename in (
        "universe_development.parquet",
        "formations.parquet",
        "audit_development.parquet",
    ):
        assert (
            _out_dir(ordered, ordered_content).joinpath(filename).read_bytes()
            == _out_dir(shuffled, shuffled_content).joinpath(filename).read_bytes()
        )


def test_timezone_invariant(tmp_path: Path) -> None:
    store_a = _build(tmp_path, "tz-utc")
    store_a.healthy("AAA")
    store_a.build()
    store_b = _build(tmp_path, "tz-ny")
    store_b.healthy("AAA")
    store_b.build()
    original = os.environ.get("TZ")
    try:
        os.environ["TZ"] = "UTC"
        time.tzset()
        utc = store_a.run()
        os.environ["TZ"] = "America/New_York"
        time.tzset()
        ny = store_b.run()
    finally:
        if original is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original
        time.tzset()
    assert utc["universe_id"] == ny["universe_id"]
    for filename in (
        "universe_development.parquet",
        "formations.parquet",
        "audit_development.parquet",
    ):
        assert (
            _out_dir(store_a, utc).joinpath(filename).read_bytes()
            == _out_dir(store_b, ny).joinpath(filename).read_bytes()
        )


def test_determinism_two_builds_byte_identical(tmp_path: Path) -> None:
    store_a = _build(tmp_path, "a")
    store_a.healthy("AAA")
    store_a.healthy("BBB", age=260)
    store_a.build()
    first = store_a.run()
    second = universe.run_universe(
        store_a.root, bindings=store_a.bindings, calendar=CALENDAR
    )
    assert first["universe_id"] == second["universe_id"]
    assert first["universe_hash"] == second["universe_hash"]
    assert first["files_sha256"] == second["files_sha256"]

    store_b = _build(tmp_path, "b")
    store_b.healthy("AAA")
    store_b.healthy("BBB", age=260)
    store_b.build()
    other = store_b.run()
    assert other["universe_id"] == first["universe_id"]
    for filename in (
        "universe_development.parquet",
        "formations.parquet",
        "audit_development.parquet",
    ):
        assert (
            _out_dir(store_a, first).joinpath(filename).read_bytes()
            == _out_dir(store_b, other).joinpath(filename).read_bytes()
        )


def test_future_data_invariance_property(tmp_path: Path) -> None:
    rng = random.Random(20260928)
    schedule = universe.build_schedule(CALENDAR)
    formations = rng.sample(list(schedule.development), 3)
    for index, f in enumerate(formations):
        start = _start_for_age(f, 260)

        base = _build(tmp_path, f"prop-base-{index}")
        for i in range(3):
            ticker = f"P{i}"
            base.key(ticker, listed_start=start)
            base.add_sessions(ticker, start, _cutoff(f))
        base.build()
        base_content = base.run()

        mutated = _build(tmp_path, f"prop-mut-{index}")
        future = CAL_DATES[CAL_DATES.index(f) + 3]
        for i in range(3):
            ticker = f"P{i}"
            mutated.key(
                ticker,
                listed_start=start,
                listed_end=CAL_DATES[CAL_DATES.index(f) + 5],
                class_segments=[(date(2030, 1, 2), "NON_COMMON", "FUTURE")],
                policy_segments=[(date(2030, 1, 2), "EXCLUDED")],
            )
            mutated.add_sessions(ticker, start, _cutoff(f))
        # D3/D5 inputs dated after f-1.
        mutated.obs("P0", future, 10.0, 100)
        idx = mutated.obs("P1", future, 10.0, None)
        mutated.flag("P1", future, idx, "VENDOR_NULL")
        mutated.build()
        mutated_content = mutated.run()

        pd.testing.assert_frame_equal(
            _audit_for(base, base_content, f),
            _audit_for(mutated, mutated_content, f),
        )
        base_dev = _read(base, base_content, "universe_development.parquet")
        mut_dev = _read(mutated, mutated_content, "universe_development.parquet")
        pd.testing.assert_frame_equal(
            base_dev[base_dev["formation_date"] == f].reset_index(drop=True),
            mut_dev[mut_dev["formation_date"] == f].reset_index(drop=True),
        )


# ---------------------------------------------------------------------------
# Binding / document fail-closed (item 37)
# ---------------------------------------------------------------------------


def test_binding_file_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    store = _build(tmp_path)
    store.healthy("AAA")
    store.build()
    data = store.root / store.bindings.d4.files[0].relative_path
    data.write_bytes(data.read_bytes() + b"tamper")
    with pytest.raises(universe.UniverseBindingError):
        store.run()
    assert not (store.root / "derived" / "universe").exists()


def test_binding_manifest_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    store = _build(tmp_path)
    store.healthy("AAA")
    store.build()
    manifest = store.root / store.bindings.d3.manifest_relative_path
    content = json.loads(manifest.read_text())
    content["normalized_hash"] = "0" * 64
    manifest.write_bytes((_canon(content) + "\n").encode())
    with pytest.raises(universe.UniverseBindingError):
        store.run()


def test_document_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    store = _build(tmp_path)
    store.healthy("AAA")
    store.build()
    document = Path(store.bindings.documents[0].path)
    document.write_text("tampered normative document\n", encoding="utf-8")
    with pytest.raises(universe.UniverseBindingError):
        store.run()


# ---------------------------------------------------------------------------
# Value / credential safety
# ---------------------------------------------------------------------------


def test_outputs_never_contain_credential_sentinel(tmp_path: Path) -> None:
    store = _build(tmp_path)
    store.healthy("AAA")
    store.build()
    content = store.run()
    for path in _out_dir(store, content).iterdir():
        assert CREDENTIAL_SENTINEL.encode() not in path.read_bytes()


def test_adjusted_close_never_used(tmp_path: Path) -> None:
    store = _build(tmp_path)
    start = _start_for_age(F1, 260)
    sk = store.key("AAA", listed_start=start)
    store.add_sessions("AAA", start, _cutoff(F1), close=10.0, volume=100)
    store.build()
    content = store.run()
    row = _row_for(_audit_for(store, content, F1), sk)
    # adjClose is 999999.0 in the fixture; DV must use raw close (10 * 100).
    assert row["median_dv"] == 1000.0
