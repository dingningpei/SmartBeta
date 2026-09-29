"""D7a development observed-return evidence tests.

Every test builds a synthetic ``tmp_path`` store with injected
:class:`~smart_beta.dataset_v1.returns.Bindings`; none touches the real
``data_store``, ``reserve_sealed`` or any credential.  An autouse socket guard
and credential sentinel mirror the D5/D-SC/D6 suites.

The test functions are named ``test_itemNN_*`` so the completion report's
test-to-item mapping (items 1-60 of the frozen spec §9) is mechanical.  Test
44 additionally exercises the unmodified P7-B ``align_forward_returns``.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Sequence

import pandas as pd
import pyarrow as pa
import pytest

from smart_beta.dataset_v1 import returns as R
from smart_beta.evaluation import forward_returns as fr
from smart_beta.vendors.tiingo.calendar_source import build_nyse_calendar

# ---------------------------------------------------------------------------
# Constants and shared calendar
# ---------------------------------------------------------------------------

CAL = build_nyse_calendar(R.CALENDAR_START, R.CALENDAR_END)
S = [ts.date() for ts in CAL.dates]
POS = {d: i for i, d in enumerate(S)}
F1 = date(2016, 1, 29)
FAR = date(2099, 1, 1)
#: A session strictly beyond endpoint(F1, 63) but inside the development window.
#: Used by the within-development terminal-inference variants.
LD = S[POS[F1] + 100]
CREDENTIAL_SENTINEL = "d7-credential-sentinel-1f2e"

D3_ID = "n-d7test"
D4_ID = "i-d7test"
D5_ID = "q-d7test"
D6_ID = "u-d7test"

_STRING = pd.ArrowDtype(pa.string())
_DATE32 = pd.ArrowDtype(pa.date32())
_INT64 = pd.ArrowDtype(pa.int64())

_DISPOSITION = {
    "OHLC_INCONSISTENT": "CORRUPT",
    "NONPOSITIVE_PRICE": "CORRUPT",
    "MALFORMED_CA": "CORRUPT",
    "DUPLICATE_DATE": "CORRUPT",
    "OFF_CALENDAR_ROW": "CORRUPT",
    "INTERVAL_MISMATCH": "UNCERTIFIED_SEMANTICS",
    "CORP_ACTION_SUSPECT": "UNCERTIFIED_SEMANTICS",
    "VENDOR_NULL": "INSUFFICIENT_COVERAGE",
}

_FORBIDDEN_COLUMN_SUBSTRINGS = (
    "t0",
    "ts",
    "tau",
    "leg",
    "quantile",
    "factor",
    "scenario",
    "forward_return",
)


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch):
    def _refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("D7a tests must never open a socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield


@pytest.fixture(autouse=True)
def _credential_sentinel(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TIINGO_API_KEY", CREDENTIAL_SENTINEL)
    yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sessions(start: date, end: date) -> list[date]:
    return [d for d in S if start <= d <= end]


def _canon(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _date_eq(series: pd.Series, d: date) -> pd.Series:
    return pd.to_datetime(series) == pd.Timestamp(d)


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


class _Store:
    """A synthetic D3/D4/D5/D6 store with injected bindings."""

    def __init__(self, tmp_path: Path, name: str = "store"):
        self.root = tmp_path / name
        self.root.mkdir(parents=True, exist_ok=True)
        self.keys: list[dict[str, Any]] = []
        self.obs_rows: list[tuple] = []
        self.flag_rows: list[tuple] = []
        self.ca_rows: list[tuple] = []
        self.universe_rows: list[dict[str, Any]] = []
        self._next: dict[str, int] = {}
        self._n_eligible: dict[date, int] = {}
        self._explicit_n_eligible: dict[date, int] = {}
        self.bindings: R.Bindings | None = None

    # -- declarations ----------------------------------------------------

    def key(
        self,
        ticker: str,
        *,
        listed_start: date,
        listed_end: date = FAR,
        security_key: str | None = None,
        state: str = "INTERVAL_ASSIGNED",
    ) -> str:
        sk = security_key or f"TIINGO:{ticker}:{listed_start.isoformat()}"
        self.keys.append(
            {
                "security_key": sk,
                "ticker": ticker,
                "listed_start": listed_start,
                "listed_end": listed_end,
                "state": state,
            }
        )
        return sk

    def identity_row(
        self,
        ticker: str,
        *,
        security_key: str,
        state: str = "INTERVAL_ASSIGNED",
        listed_start: date = date(2015, 1, 2),
        listed_end: date = FAR,
    ) -> None:
        self.keys.append(
            {
                "security_key": security_key,
                "ticker": ticker,
                "listed_start": listed_start,
                "listed_end": listed_end,
                "state": state,
            }
        )

    def obs(
        self,
        ticker: str,
        d: date,
        close: float = 10.0,
        volume: int = 100,
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
        close: float = 10.0,
        volume: int = 100,
        skip: Sequence[date] = (),
        flag: str | None = None,
    ) -> None:
        skipped = set(skip)
        for d in _sessions(start, end):
            if d in skipped:
                continue
            self.obs(ticker, d, close, volume, flag=flag)

    def index_of(self, ticker: str, d: date) -> int:
        for row in self.obs_rows:
            if row[0] == ticker and row[1] == d:
                return int(row[4])
        raise AssertionError(f"no observation for ({ticker}, {d})")

    def ca(
        self,
        ticker: str,
        effective_date: date,
        *,
        factor: float = 1.1,
        action_type: str = "dividend",
        knowledge_date: date | None = None,
        is_superseded: bool = False,
    ) -> None:
        self.ca_rows.append(
            (
                ticker,
                effective_date,
                action_type,
                knowledge_date or effective_date,
                factor,
                is_superseded,
            )
        )

    def member(
        self,
        f: date,
        security_key: str,
        rank: int,
        *,
        top_200: bool = False,
        top_500: bool = False,
        top_1000: bool = True,
    ) -> None:
        self.universe_rows.append(
            {
                "formation_date": f,
                "security_key": security_key,
                "rank": rank,
                "top_200": top_200,
                "top_500": top_500,
                "top_1000": top_1000,
            }
        )

    def set_n_eligible(self, f: date, n: int) -> None:
        self._explicit_n_eligible[f] = n

    # -- materialise -----------------------------------------------------

    def _permutation(self) -> list[int] | None:
        return getattr(self, "_perm", None)

    def build(self, *, reverse: bool = False) -> R.Bindings:
        root = self.root
        obs_rows = list(getattr(self, "obs_override", self.obs_rows))
        key_rows = list(self.keys)
        flag_rows = list(self.flag_rows)
        ca_rows = list(self.ca_rows)
        universe_rows = list(self.universe_rows)
        if reverse:
            obs_rows.reverse()
            key_rows.reverse()
            flag_rows.reverse()
            ca_rows.reverse()
            universe_rows.reverse()

        obs = pd.DataFrame(
            {
                "ticker": pd.Series([r[0] for r in obs_rows], dtype=_STRING),
                "date": pd.Series([r[1] for r in obs_rows], dtype=_DATE32),
                "close": pd.Series([r[2] for r in obs_rows], dtype="float64"),
                "volume": pd.Series([r[3] for r in obs_rows], dtype="Int64"),
                "raw_row_index": pd.Series(
                    [r[4] for r in obs_rows], dtype="int64"
                ),
                "adjClose": pd.Series(
                    [999999.0] * len(obs_rows), dtype="float64"
                ),
                "adjHigh": pd.Series(
                    [7.0] * len(obs_rows), dtype="float64"
                ),
            }
        )
        ca = pd.DataFrame(
            {
                "stock_id": pd.Series([r[0] for r in ca_rows], dtype=_STRING),
                "effective_date": pd.Series(
                    [r[1] for r in ca_rows], dtype="datetime64[ns]"
                ),
                "action_type": pd.Series([r[2] for r in ca_rows], dtype=_STRING),
                "knowledge_date": pd.Series(
                    [r[3] for r in ca_rows], dtype="datetime64[ns]"
                ),
                "adjustment_factor": pd.Series(
                    [r[4] for r in ca_rows], dtype="float64"
                ),
                "is_superseded": pd.Series(
                    [r[5] for r in ca_rows], dtype=bool
                ),
            }
        )
        ident = pd.DataFrame(
            {
                "security_key": pd.Series(
                    [k["security_key"] for k in key_rows], dtype=_STRING
                ),
                "ticker": pd.Series([k["ticker"] for k in key_rows], dtype=_STRING),
                "listed_start": pd.Series(
                    [k["listed_start"] for k in key_rows], dtype=_DATE32
                ),
                "listed_end": pd.Series(
                    [k["listed_end"] for k in key_rows], dtype=_DATE32
                ),
                "state": pd.Series([k["state"] for k in key_rows], dtype=_STRING),
            }
        )
        flags = pd.DataFrame(
            {
                "ticker": pd.Series([r[0] for r in flag_rows], dtype=_STRING),
                "date": pd.Series([r[1] for r in flag_rows], dtype=_DATE32),
                "raw_row_index": pd.Series(
                    [r[2] for r in flag_rows], dtype="int64"
                ),
                "class_or_flag": pd.Series(
                    [r[3] for r in flag_rows], dtype=_STRING
                ),
                "disposition": pd.Series(
                    [r[4] for r in flag_rows], dtype=_STRING
                ),
            }
        )
        universe = pd.DataFrame(
            {
                "formation_date": pd.Series(
                    [r["formation_date"] for r in universe_rows], dtype=_DATE32
                ),
                "security_key": pd.Series(
                    [r["security_key"] for r in universe_rows], dtype=_STRING
                ),
                "rank": pd.Series(
                    [r["rank"] for r in universe_rows], dtype="int64"
                ),
                "top_200": pd.Series(
                    [r["top_200"] for r in universe_rows], dtype=bool
                ),
                "top_500": pd.Series(
                    [r["top_500"] for r in universe_rows], dtype=bool
                ),
                "top_1000": pd.Series(
                    [r["top_1000"] for r in universe_rows], dtype=bool
                ),
            }
        )
        counts: dict[date, int] = {}
        for row in universe_rows:
            counts[row["formation_date"]] = counts.get(row["formation_date"], 0) + 1
        formation_dates = sorted(set(counts) | set(self._explicit_n_eligible))
        formations = pd.DataFrame(
            {
                "formation_date": pd.Series(formation_dates, dtype=_DATE32),
                "partition": pd.Series(
                    ["DEVELOPMENT"] * len(formation_dates), dtype=_STRING
                ),
                "n_eligible": pd.Series(
                    [
                        self._explicit_n_eligible.get(f, counts.get(f, 0))
                        for f in formation_dates
                    ],
                    dtype="Int64",
                ),
            }
        )

        d3_obs = f"normalized/{D3_ID}/observations.parquet"
        d3_ca = f"normalized/{D3_ID}/ca_events.parquet"
        d4_file = f"derived/identity/{D4_ID}/identity_assignment.parquet"
        d5_file = f"derived/qc/{D5_ID}/day_flags.parquet"
        d6_dev = f"derived/universe/{D6_ID}/universe_development.parquet"
        d6_form = f"derived/universe/{D6_ID}/formations.parquet"

        sha_obs = _write_parquet(root, d3_obs, obs)
        sha_ca = _write_parquet(root, d3_ca, ca)
        sha_ident = _write_parquet(root, d4_file, ident)
        sha_flags = _write_parquet(root, d5_file, flags)
        sha_dev = _write_parquet(root, d6_dev, universe)
        sha_form = _write_parquet(root, d6_form, formations)

        d3_hash = _write_manifest(
            root,
            f"normalized/{D3_ID}/build.json",
            "normalized_hash",
            {d3_obs: sha_obs, d3_ca: sha_ca},
        )
        d3_msha = _sha256_file(root / f"normalized/{D3_ID}/build.json")
        d4_hash = _write_manifest(
            root,
            f"derived/identity/{D4_ID}/identity.json",
            "identity_hash",
            {d4_file: sha_ident},
        )
        d4_msha = _sha256_file(root / f"derived/identity/{D4_ID}/identity.json")
        d5_hash = _write_manifest(
            root,
            f"derived/qc/{D5_ID}/qc_report.json",
            "qc_hash",
            {d5_file: sha_flags},
        )
        d5_msha = _sha256_file(root / f"derived/qc/{D5_ID}/qc_report.json")
        d6_hash = _write_manifest(
            root,
            f"derived/universe/{D6_ID}/universe.json",
            "universe_hash",
            {d6_dev: sha_dev, d6_form: sha_form},
        )
        d6_msha = _sha256_file(root / f"derived/universe/{D6_ID}/universe.json")

        documents = []
        for name in sorted(R._DOCUMENT_NAMES):
            path = root / "docs" / f"{name}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"normative document {name}\n", encoding="utf-8")
            documents.append(R.DocumentBinding(name, str(path), _sha256_file(path)))

        self.bindings = R.Bindings(
            d3=R.StageBinding(
                "D3",
                D3_ID,
                f"normalized/{D3_ID}/build.json",
                "normalized_hash",
                d3_hash,
                d3_msha,
                (
                    R.FileBinding(d3_obs, sha_obs),
                    R.FileBinding(d3_ca, sha_ca),
                ),
            ),
            d4=R.StageBinding(
                "D4",
                D4_ID,
                f"derived/identity/{D4_ID}/identity.json",
                "identity_hash",
                d4_hash,
                d4_msha,
                (R.FileBinding(d4_file, sha_ident),),
            ),
            d5=R.StageBinding(
                "D5",
                D5_ID,
                f"derived/qc/{D5_ID}/qc_report.json",
                "qc_hash",
                d5_hash,
                d5_msha,
                (R.FileBinding(d5_file, sha_flags),),
            ),
            d6=R.StageBinding(
                "D6",
                D6_ID,
                f"derived/universe/{D6_ID}/universe.json",
                "universe_hash",
                d6_hash,
                d6_msha,
                (R.FileBinding(d6_dev, sha_dev), R.FileBinding(d6_form, sha_form)),
            ),
            documents=tuple(documents),
        )
        return self.bindings

    def run(self) -> dict:
        assert self.bindings is not None
        return R.run_returns(self.root, bindings=self.bindings, calendar=CAL)


# ---------------------------------------------------------------------------
# Shared scenarios
# ---------------------------------------------------------------------------


def _one_member(
    tmp_path: Path,
    name: str = "store",
    *,
    f: date = F1,
    ticker: str = "AAA",
    listed_end: date = FAR,
    listed_start: date | None = None,
    obs_start: date | None = None,
    obs_end: date = R.DEVELOPMENT_END,
    skip: Sequence[date] = (),
    close: float = 10.0,
    rank: int = 1,
    sk: str | None = None,
) -> tuple[_Store, str]:
    store = _Store(tmp_path, name=name)
    ls = listed_start or S[POS[f] - 5]
    start = obs_start or S[POS[f] - 5]
    key = store.key(ticker, listed_start=ls, listed_end=listed_end, security_key=sk)
    store.add_sessions(ticker, start, obs_end, close=close, skip=skip)
    store.member(f, key, rank, top_200=True, top_500=True)
    store.build()
    return store, key


def _out_dir(store: _Store, content: dict) -> Path:
    return store.root / "derived" / "returns" / content["returns_id"]


def _read(store: _Store, content: dict, name: str) -> pd.DataFrame:
    return pd.read_parquet(_out_dir(store, content) / name)


def _label(store: _Store, content: dict, f: date, sk: str, h: int):
    labels = _read(store, content, R.LABELS_FILE)
    rows = labels[(labels["security_key"] == sk) & (labels["h"] == h)]
    rows = rows[_date_eq(rows["formation_date"], f)]
    assert len(rows) == 1, f"expected one label for ({f}, {sk}, {h}), got {len(rows)}"
    return rows.iloc[0]


def _daily(store: _Store, content: dict, sk: str, d: date):
    daily = _read(store, content, R.DAILY_FILE)
    rows = daily[(daily["security_key"] == sk) & _date_eq(daily["date"], d)]
    assert len(rows) == 1, f"expected one daily row for ({sk}, {d}), got {len(rows)}"
    return rows.iloc[0]


def _data_file_bytes(store: _Store, content: dict) -> dict[str, bytes]:
    directory = _out_dir(store, content)
    return {
        name: (directory / name).read_bytes()
        for name in (
            R.MEMBERS_FILE,
            R.HORIZONS_FILE,
            R.DAILY_FILE,
            R.LABELS_FILE,
        )
    }


# ---------------------------------------------------------------------------
# Clock and horizon (items 1-5)
# ---------------------------------------------------------------------------


def test_item01_h1_uses_only_f_plus_1(tmp_path: Path) -> None:
    store, key = _one_member(tmp_path, close=10.0)
    content = store.run()
    row = _label(store, content, F1, key, 1)
    expected_endpoint = S[POS[F1] + 1]
    assert row["endpoint"] == expected_endpoint
    assert int(row["n_observed_sessions"]) == 1
    assert row["observed_segment_end"] == expected_endpoint
    daily = _daily(store, content, key, expected_endpoint)
    assert daily["session_status"] == R.STATUS_VALID
    # The return dated f (used only as the previous session) is never a date.
    assert daily["prev_session"] == F1


def test_item02_hmax_and_horizon_table(tmp_path: Path) -> None:
    store, key = _one_member(tmp_path)
    content = store.run()
    horizons = _read(store, content, R.HORIZONS_FILE)
    assert sorted(horizons["h"].tolist()) == list(range(1, 64))
    assert len(horizons) == 63
    assert R.HMAX == 63


def test_item03_return_dated_f_never_in_a_segment(tmp_path: Path) -> None:
    store, key = _one_member(tmp_path)
    content = store.run()
    daily = _read(store, content, R.DAILY_FILE)
    assert pd.to_datetime(daily["date"]).min().date() == S[POS[F1] + 1]
    labels = _read(store, content, R.LABELS_FILE)
    ends = pd.to_datetime(labels["observed_segment_end"].dropna()).dt.date
    assert (ends > F1).all()


def test_item04_changing_rows_before_f_changes_nothing(tmp_path: Path) -> None:
    base, key = _one_member(tmp_path, "base")
    base_content = base.run()
    mut = _Store(tmp_path, name="mut")
    ls = S[POS[F1] - 5]
    mut.key("AAA", listed_start=ls, listed_end=FAR, security_key=key)
    mut.add_sessions("AAA", ls, R.DEVELOPMENT_END, skip=[S[POS[F1] - 1]])
    mut.obs("AAA", S[POS[F1] - 1], 999.0)
    mut.member(F1, key, 1)
    mut.build()
    mut_content = mut.run()
    pd.testing.assert_frame_equal(
        _read(base, base_content, R.LABELS_FILE),
        _read(mut, mut_content, R.LABELS_FILE),
    )


def test_item05_endpoints_use_trusted_sessions(tmp_path: Path) -> None:
    store, key = _one_member(tmp_path)
    content = store.run()
    labels = _read(store, content, R.LABELS_FILE)
    by_h = {
        int(r["h"]): r["endpoint"]
        for _, r in labels.iterrows()
    }
    assert by_h[1] == S[POS[F1] + 1]
    assert by_h[1] != F1 + timedelta(days=1)  # weekend skipped
    # Good Friday 2017-04-14 must be skipped.
    f = date(2017, 4, 13)
    assert S[POS[f] + 1] == date(2017, 4, 17)


# ---------------------------------------------------------------------------
# Terminal classification (items 6-21)
# ---------------------------------------------------------------------------


def _terminal_store(tmp_path: Path, name: str, listed_end: date):
    store = _Store(tmp_path, name=name)
    key = store.key("AAA", listed_start=S[POS[F1] - 5], listed_end=listed_end)
    store.add_sessions("AAA", S[POS[F1] - 5], R.DEVELOPMENT_END)
    store.member(F1, key, 1)
    store.build()
    return store, key


def test_item06_listed_end_equals_f_empty_identity(tmp_path: Path) -> None:
    store, key = _terminal_store(tmp_path, "s", F1)
    content = store.run()
    labels = _read(store, content, R.LABELS_FILE)
    mine = labels[labels["security_key"] == key]
    assert (mine["label_state"] == R.STATE_EMPTY_OBSERVED_PATH_IDENTITY).all()
    assert mine["terminal_affected"].fillna(False).all()
    assert (mine["n_observed_sessions"] == 0).all()
    assert (mine["n_post_terminal_sessions"] == mine["h"]).all()
    assert mine["observed_path_complete"].fillna(False).all()
    daily = _read(store, content, R.DAILY_FILE)
    assert (daily["session_status"] == R.STATUS_POST_TERMINAL).all()


def test_item07_listed_end_first_session_after_f(tmp_path: Path) -> None:
    store, key = _terminal_store(tmp_path, "s", S[POS[F1] + 1])
    content = store.run()
    labels = _read(store, content, R.LABELS_FILE)
    mine = labels[labels["security_key"] == key]
    assert mine["terminal_affected"].fillna(False).all()
    assert (mine["n_observed_sessions"] == 1).all()
    assert (mine["observed_segment_end"] == S[POS[F1] + 1]).all()


def test_item08_listed_end_at_endpoint_h3(tmp_path: Path) -> None:
    store, key = _terminal_store(tmp_path, "s", S[POS[F1] + 3])
    content = store.run()
    labels = _read(store, content, R.LABELS_FILE)
    mine = labels[labels["security_key"] == key].set_index("h")
    for h in (1, 2):
        assert not bool(mine.loc[h, "terminal_affected"])
        assert int(mine.loc[h, "n_observed_sessions"]) == h
    for h in (3, 4, 10, 63):
        assert bool(mine.loc[h, "terminal_affected"])
        assert int(mine.loc[h, "n_observed_sessions"]) == 3


def test_item09_listed_end_beyond_endpoint_not_affected(tmp_path: Path) -> None:
    store, key = _terminal_store(tmp_path, "s", S[POS[F1] + 2])
    content = store.run()
    row = _label(store, content, F1, key, 1)
    assert not bool(row["terminal_affected"])
    assert int(row["n_observed_sessions"]) == 1


def test_item10_listed_end_before_f_fails(tmp_path: Path) -> None:
    store = _Store(tmp_path, name="s")
    key = store.key("AAA", listed_start=S[POS[F1] - 5], listed_end=F1 - timedelta(days=1))
    store.add_sessions("AAA", S[POS[F1] - 5], R.DEVELOPMENT_END)
    store.member(F1, key, 1)
    store.build()
    with pytest.raises(R.D7ConsistencyError):
        store.run()
    assert not (store.root / "derived" / "returns").exists()


def test_item11_listed_end_non_session_date(tmp_path: Path) -> None:
    store, key = _terminal_store(tmp_path, "s", F1 + timedelta(days=1))  # Saturday
    content = store.run()
    row = _label(store, content, F1, key, 1)
    assert bool(row["terminal_affected"])
    assert int(row["n_observed_sessions"]) == 0
    assert row["label_state"] == R.STATE_EMPTY_OBSERVED_PATH_IDENTITY


def test_item12_cessation_before_listed_end_is_incomplete(tmp_path: Path) -> None:
    store = _Store(tmp_path, name="s")
    key = store.key("AAA", listed_start=S[POS[F1] - 5], listed_end=FAR)
    store.add_sessions("AAA", S[POS[F1] - 5], S[POS[F1] + 2])
    store.member(F1, key, 1)
    store.build()
    content = store.run()
    row = _label(store, content, F1, key, 5)
    assert not bool(row["terminal_affected"])
    assert row["label_state"] == R.STATE_INCOMPLETE

    # Within-development variant: listed_end <= 2023-06-30 but > endpoint.
    wd = _Store(tmp_path, name="s_wd")
    wd_key = wd.key("AAA", listed_start=S[POS[F1] - 5], listed_end=LD)
    wd.add_sessions("AAA", S[POS[F1] - 5], S[POS[F1] + 2])
    wd.member(F1, wd_key, 1)
    wd.build()
    wd_content = wd.run()
    wd_row = _label(wd, wd_content, F1, wd_key, 5)
    assert not bool(wd_row["terminal_affected"])
    assert wd_row["label_state"] == R.STATE_INCOMPLETE


def test_item13_row_after_endpoint_changes_nothing(tmp_path: Path) -> None:
    base, key = _one_member(tmp_path, "base", obs_end=S[POS[F1] + 5])
    base_content = base.run()
    mut, _ = _one_member(tmp_path, "mut", obs_end=S[POS[F1] + 5])
    # Removing the row at endpoint(F1,1)+1 = S[pos+2] changes nothing for h=1.
    mut.obs_rows = [r for r in mut.obs_rows if r[1] != S[POS[F1] + 2]]
    mut.build()
    mut_content = mut.run()
    pd.testing.assert_series_equal(
        _label(base, base_content, F1, key, 1),
        _label(mut, mut_content, F1, key, 1),
    )

    # Within-development variant: removing evidence at endpoint+1 leaves the
    # h=1 label identical (terminal_affected must stay false).
    wd_base, wd_key = _one_member(
        tmp_path, "wd_base", listed_end=LD, obs_end=S[POS[F1] + 5]
    )
    wd_base_content = wd_base.run()
    wd_mut, _ = _one_member(
        tmp_path, "wd_mut", listed_end=LD, obs_end=S[POS[F1] + 5]
    )
    wd_mut.obs_rows = [r for r in wd_mut.obs_rows if r[1] != S[POS[F1] + 2]]
    wd_mut.build()
    wd_mut_content = wd_mut.run()
    wd_b = _label(wd_base, wd_base_content, F1, wd_key, 1)
    wd_m = _label(wd_mut, wd_mut_content, F1, wd_key, 1)
    assert not bool(wd_b["terminal_affected"])
    assert not bool(wd_m["terminal_affected"])
    pd.testing.assert_series_equal(wd_b, wd_m)


def test_item14_temporary_gap_is_incomplete(tmp_path: Path) -> None:
    store, key = _one_member(tmp_path, skip=[S[POS[F1] + 3]])
    content = store.run()
    row = _label(store, content, F1, key, 5)
    assert not bool(row["terminal_affected"])
    assert row["label_state"] == R.STATE_INCOMPLETE
    assert row["first_invalid_session"] == S[POS[F1] + 3]

    # Within-development variant: a gap running up to the endpoint, then a
    # resumption, is INCOMPLETE and not a terminal event.
    wd = _Store(tmp_path, name="s_wd")
    wd_key = wd.key("AAA", listed_start=S[POS[F1] - 5], listed_end=LD)
    wd.add_sessions(
        "AAA",
        S[POS[F1] - 5],
        R.DEVELOPMENT_END,
        skip=[S[POS[F1] + 3], S[POS[F1] + 4]],
    )
    wd.member(F1, wd_key, 1)
    wd.build()
    wd_content = wd.run()
    wd_row = _label(wd, wd_content, F1, wd_key, 4)
    assert not bool(wd_row["terminal_affected"])
    assert wd_row["label_state"] == R.STATE_INCOMPLETE
    assert wd_row["first_invalid_session"] == S[POS[F1] + 3]


def test_item15_missing_row_status_chain(tmp_path: Path) -> None:
    store, key = _one_member(tmp_path, skip=[S[POS[F1] + 2]])
    content = store.run()
    assert _daily(store, content, key, S[POS[F1] + 2])["session_status"] == R.STATUS_NO_ROW
    assert (
        _daily(store, content, key, S[POS[F1] + 3])["session_status"]
        == R.STATUS_PREV_NO_ROW
    )

    # Within-development variant: a trailing missing row at the endpoint is
    # NO_ROW, not a terminal event.
    wd = _Store(tmp_path, name="s_wd")
    wd_key = wd.key("AAA", listed_start=S[POS[F1] - 5], listed_end=LD)
    wd.add_sessions("AAA", S[POS[F1] - 5], R.DEVELOPMENT_END, skip=[S[POS[F1] + 2]])
    wd.member(F1, wd_key, 1)
    wd.build()
    wd_content = wd.run()
    assert _daily(wd, wd_content, wd_key, S[POS[F1] + 2])["session_status"] == R.STATUS_NO_ROW
    assert (
        _daily(wd, wd_content, wd_key, S[POS[F1] + 3])["session_status"]
        == R.STATUS_PREV_NO_ROW
    )
    wd_row = _label(wd, wd_content, F1, wd_key, 2)
    assert not bool(wd_row["terminal_affected"])
    assert wd_row["label_state"] == R.STATE_INCOMPLETE

    # D4 fail-closed: missing session evidence is never treated as VALID.
    mk = R._MemberKey(
        security_key=key,
        ticker="AAA",
        listed_start=S[POS[F1] - 5],
        listed_end=LD,
        listing_status=R.LISTING_ENDS_WITHIN_DEVELOPMENT,
        listed_end_dev=LD,
    )
    with pytest.raises(R.D7ConsistencyError):
        R._label_records(F1, mk, {}, S, POS)


def test_item16_missing_row_at_endpoint_incomplete(tmp_path: Path) -> None:
    store, key = _one_member(tmp_path, skip=[S[POS[F1] + 2]])
    content = store.run()
    row = _label(store, content, F1, key, 2)
    assert row["label_state"] == R.STATE_INCOMPLETE
    assert int(row["n_invalid_sessions"]) == 1

    # Within-development variant: missing session exactly at the endpoint,
    # listed_end still beyond it.
    wd = _Store(tmp_path, name="s_wd")
    wd_key = wd.key("AAA", listed_start=S[POS[F1] - 5], listed_end=LD)
    wd.add_sessions("AAA", S[POS[F1] - 5], R.DEVELOPMENT_END, skip=[S[POS[F1] + 2]])
    wd.member(F1, wd_key, 1)
    wd.build()
    wd_content = wd.run()
    wd_row = _label(wd, wd_content, F1, wd_key, 2)
    assert wd_row["label_state"] == R.STATE_INCOMPLETE
    assert not bool(wd_row["terminal_affected"])
    assert int(wd_row["n_invalid_sessions"]) == 1


def test_item17_missing_pre_terminal_row_affected_and_incomplete(tmp_path: Path) -> None:
    store = _Store(tmp_path, name="s")
    key = store.key("AAA", listed_start=S[POS[F1] - 5], listed_end=S[POS[F1] + 3])
    store.add_sessions(
        "AAA", S[POS[F1] - 5], R.DEVELOPMENT_END, skip=[S[POS[F1] + 1]]
    )
    store.member(F1, key, 1)
    store.build()
    content = store.run()
    row = _label(store, content, F1, key, 3)
    assert bool(row["terminal_affected"])
    assert row["label_state"] == R.STATE_INCOMPLETE
    assert row["first_invalid_session"] == S[POS[F1] + 1]


def test_item18_post_terminal_beats_interval_mismatch(tmp_path: Path) -> None:
    store = _Store(tmp_path, name="s")
    key = store.key("AAA", listed_start=S[POS[F1] - 5], listed_end=S[POS[F1] + 2])
    store.add_sessions("AAA", S[POS[F1] - 5], R.DEVELOPMENT_END)
    d = S[POS[F1] + 3]
    store.flag("AAA", d, store.index_of("AAA", d), "INTERVAL_MISMATCH")
    store.member(F1, key, 1)
    store.build()
    content = store.run()
    assert _daily(store, content, key, d)["session_status"] == R.STATUS_POST_TERMINAL


def test_item19_missing_close_f_no_fill(tmp_path: Path) -> None:
    store, key = _one_member(tmp_path, skip=[F1])
    content = store.run()
    row = _daily(store, content, key, S[POS[F1] + 1])
    assert row["session_status"] == R.STATUS_PREV_NO_ROW
    assert pd.isna(row["daily_total_return"])
    assert _label(store, content, F1, key, 1)["label_state"] == R.STATE_INCOMPLETE

    # Within-development variant: missing close(f) is PREV_NO_ROW, never filled
    # and never a terminal event.
    wd = _Store(tmp_path, name="s_wd")
    wd_key = wd.key("AAA", listed_start=S[POS[F1] - 5], listed_end=LD)
    wd.add_sessions("AAA", S[POS[F1] - 5], R.DEVELOPMENT_END, skip=[F1])
    wd.member(F1, wd_key, 1)
    wd.build()
    wd_content = wd.run()
    wd_daily = _daily(wd, wd_content, wd_key, S[POS[F1] + 1])
    assert wd_daily["session_status"] == R.STATUS_PREV_NO_ROW
    assert pd.isna(wd_daily["daily_total_return"])
    wd_row = _label(wd, wd_content, F1, wd_key, 1)
    assert not bool(wd_row["terminal_affected"])
    assert wd_row["label_state"] == R.STATE_INCOMPLETE


def test_item20_unusable_row_at_f_prev_row_unusable(tmp_path: Path) -> None:
    store = _Store(tmp_path, name="s")
    key = store.key("AAA", listed_start=S[POS[F1] - 5], listed_end=FAR)
    store.add_sessions("AAA", S[POS[F1] - 5], R.DEVELOPMENT_END)
    store.flag("AAA", F1, store.index_of("AAA", F1), "OHLC_INCONSISTENT")
    store.member(F1, key, 1)
    store.build()
    content = store.run()
    assert (
        _daily(store, content, key, S[POS[F1] + 1])["session_status"]
        == R.STATUS_PREV_ROW_UNUSABLE
    )


def test_item21_terminal_and_incomplete_both_recorded(tmp_path: Path) -> None:
    store = _Store(tmp_path, name="s")
    key = store.key("AAA", listed_start=S[POS[F1] - 5], listed_end=S[POS[F1] + 2])
    store.add_sessions(
        "AAA", S[POS[F1] - 5], R.DEVELOPMENT_END, skip=[S[POS[F1] + 1]]
    )
    store.member(F1, key, 1)
    store.build()
    content = store.run()
    row = _label(store, content, F1, key, 2)
    assert bool(row["terminal_affected"])
    assert not bool(row["observed_path_complete"])
    assert row["label_state"] == R.STATE_INCOMPLETE


# ---------------------------------------------------------------------------
# Row QC (items 22-28)
# ---------------------------------------------------------------------------


def _flagged_store(
    tmp_path: Path,
    name: str,
    klass: str,
    d: date,
    *,
    disposition: str | None = None,
) -> tuple[_Store, str]:
    store = _Store(tmp_path, name=name)
    key = store.key("AAA", listed_start=S[POS[F1] - 5], listed_end=FAR)
    store.add_sessions("AAA", S[POS[F1] - 5], R.DEVELOPMENT_END)
    store.flag("AAA", d, store.index_of("AAA", d), klass, disposition)
    store.member(F1, key, 1)
    store.build()
    return store, key


def test_item22_ohlc_inconsistent_row_unusable(tmp_path: Path) -> None:
    d = S[POS[F1] + 2]
    store, key = _flagged_store(tmp_path, "s", "OHLC_INCONSISTENT", d)
    content = store.run()
    assert _daily(store, content, key, d)["session_status"] == R.STATUS_ROW_UNUSABLE


def test_item23_duplicate_date_rows_unusable(tmp_path: Path) -> None:
    store = _Store(tmp_path, name="s")
    key = store.key("AAA", listed_start=S[POS[F1] - 5], listed_end=FAR)
    store.add_sessions("AAA", S[POS[F1] - 5], R.DEVELOPMENT_END)
    d = S[POS[F1] + 2]
    store.flag("AAA", d, store.index_of("AAA", d), "DUPLICATE_DATE")
    store.obs("AAA", d, 10.0, flag="DUPLICATE_DATE")
    store.member(F1, key, 1)
    store.build()
    content = store.run()
    assert _daily(store, content, key, d)["session_status"] == R.STATUS_ROW_UNUSABLE


def test_item24_interval_mismatch_row_unusable(tmp_path: Path) -> None:
    d = S[POS[F1] + 2]
    store, key = _flagged_store(tmp_path, "s", "INTERVAL_MISMATCH", d)
    content = store.run()
    assert _daily(store, content, key, d)["session_status"] == R.STATUS_ROW_UNUSABLE


def test_item25_vendor_null_row_unusable(tmp_path: Path) -> None:
    d = S[POS[F1] + 2]
    store, key = _flagged_store(tmp_path, "s", "VENDOR_NULL", d)
    content = store.run()
    assert _daily(store, content, key, d)["session_status"] == R.STATUS_ROW_UNUSABLE


def test_item26_corp_action_suspect_row_valid(tmp_path: Path) -> None:
    d = S[POS[F1] + 2]
    store, key = _flagged_store(tmp_path, "s", "CORP_ACTION_SUSPECT", d)
    content = store.run()
    row = _daily(store, content, key, d)
    assert row["session_status"] == R.STATUS_VALID
    assert bool(row["corp_action_suspect"])


def test_item27_zero_volume_row_valid(tmp_path: Path) -> None:
    store = _Store(tmp_path, name="s")
    key = store.key("AAA", listed_start=S[POS[F1] - 5], listed_end=FAR)
    store.add_sessions("AAA", S[POS[F1] - 5], R.DEVELOPMENT_END, volume=0)
    store.member(F1, key, 1)
    store.build()
    content = store.run()
    assert _daily(store, content, key, S[POS[F1] + 1])["session_status"] == R.STATUS_VALID


def test_item28_off_calendar_row_never_a_session(tmp_path: Path) -> None:
    store = _Store(tmp_path, name="s")
    key = store.key("AAA", listed_start=S[POS[F1] - 5], listed_end=FAR)
    store.add_sessions("AAA", S[POS[F1] - 5], R.DEVELOPMENT_END)
    weekend = F1 + timedelta(days=1)
    store.obs("AAA", weekend, 10.0)
    store.member(F1, key, 1)
    store.build()
    content = store.run()
    daily = _read(store, content, R.DAILY_FILE)
    parsed = pd.to_datetime(daily["date"]).dt.date
    assert weekend not in set(parsed)
    assert weekend not in set(pd.to_datetime(daily["prev_session"]).dt.date)


# ---------------------------------------------------------------------------
# Corporate actions (items 29-37)
# ---------------------------------------------------------------------------


def test_item29_dividend_exact_date(tmp_path: Path) -> None:
    store = _Store(tmp_path, name="s")
    key = store.key("AAA", listed_start=S[POS[F1] - 5], listed_end=FAR)
    store.add_sessions("AAA", S[POS[F1] - 5], R.DEVELOPMENT_END, close=10.0)
    d = S[POS[F1] + 2]
    store.ca("AAA", d, factor=1.1, action_type="dividend")
    store.member(F1, key, 1)
    store.build()
    content = store.run()
    row = _daily(store, content, key, d)
    assert row["session_status"] == R.STATUS_VALID
    assert row["daily_total_return"] == pytest.approx(0.1)
    assert int(row["ca_event_count"]) == 1
    next_row = _daily(store, content, key, S[POS[F1] + 3])
    assert next_row["daily_total_return"] == pytest.approx(0.0)


def test_item30_split_exact_date(tmp_path: Path) -> None:
    store = _Store(tmp_path, name="s")
    key = store.key("AAA", listed_start=S[POS[F1] - 5], listed_end=FAR)
    store.add_sessions("AAA", S[POS[F1] - 5], R.DEVELOPMENT_END, close=10.0)
    d = S[POS[F1] + 2]
    store.ca("AAA", d, factor=2.0, action_type="split")
    store.member(F1, key, 1)
    store.build()
    content = store.run()
    assert _daily(store, content, key, d)["daily_total_return"] == pytest.approx(1.0)


def test_item31_split_and_dividend_multiply(tmp_path: Path) -> None:
    store = _Store(tmp_path, name="s")
    key = store.key("AAA", listed_start=S[POS[F1] - 5], listed_end=FAR)
    store.add_sessions("AAA", S[POS[F1] - 5], R.DEVELOPMENT_END, close=10.0)
    d = S[POS[F1] + 2]
    store.ca("AAA", d, factor=2.0, action_type="split")
    store.ca("AAA", d, factor=1.1, action_type="dividend")
    store.member(F1, key, 1)
    store.build()
    content = store.run()
    row = _daily(store, content, key, d)
    assert row["daily_total_return"] == pytest.approx(1.2)
    assert int(row["ca_event_count"]) == 2


def test_item32_event_on_non_session_date_fails(tmp_path: Path) -> None:
    store = _Store(tmp_path, name="s")
    key = store.key("AAA", listed_start=S[POS[F1] - 5], listed_end=FAR)
    store.add_sessions("AAA", S[POS[F1] - 5], R.DEVELOPMENT_END)
    store.ca("AAA", F1 + timedelta(days=1))
    store.member(F1, key, 1)
    store.build()
    with pytest.raises(R.D7ConsistencyError):
        store.run()


def test_item33_superseded_or_bad_knowledge_date_fails(tmp_path: Path) -> None:
    for superseded, knowledge in ((True, None), (False, F1 + timedelta(days=1))):
        store = _Store(tmp_path, name=f"s{superseded}{knowledge}")
        key = store.key("AAA", listed_start=S[POS[F1] - 5], listed_end=FAR)
        store.add_sessions("AAA", S[POS[F1] - 5], R.DEVELOPMENT_END)
        store.ca(
            "AAA",
            S[POS[F1] + 2],
            is_superseded=superseded,
            knowledge_date=knowledge,
        )
        store.member(F1, key, 1)
        store.build()
        with pytest.raises(R.D7ConsistencyError):
            store.run()


def test_item34_no_adjustment_leaks_to_next_session(tmp_path: Path) -> None:
    store = _Store(tmp_path, name="s")
    key = store.key("AAA", listed_start=S[POS[F1] - 5], listed_end=FAR)
    store.add_sessions("AAA", S[POS[F1] - 5], R.DEVELOPMENT_END, close=10.0)
    d = S[POS[F1] + 2]
    store.ca("AAA", d, factor=1.1)
    store.member(F1, key, 1)
    store.build()
    content = store.run()
    assert _daily(store, content, key, d)["daily_total_return"] == pytest.approx(0.1)
    assert _daily(store, content, key, S[POS[F1] + 3])[
        "daily_total_return"
    ] == pytest.approx(0.0)


def test_item35_adj_fields_irrelevant(tmp_path: Path) -> None:
    base, key = _one_member(tmp_path, "base")
    base_content = base.run()
    mut, key2 = _one_member(tmp_path, "mut", sk=None)
    # Change one row's adj columns by overwriting the observations parquet.
    obs_path = mut.root / f"normalized/{D3_ID}/observations.parquet"
    frame = pd.read_parquet(obs_path)
    frame.loc[frame.index[2], "adjClose"] = 12345.0
    frame.loc[frame.index[3], "adjHigh"] = 54321.0
    frame.to_parquet(obs_path, engine="pyarrow", index=False, compression="zstd")
    sha = _sha256_file(obs_path)
    manifest_path = mut.root / f"normalized/{D3_ID}/build.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["files_sha256"][f"normalized/{D3_ID}/observations.parquet"] = sha
    manifest["normalized_hash"] = hashlib.sha256(
        _canon(manifest["files_sha256"]).encode()
    ).hexdigest()
    manifest_path.write_bytes((_canon(manifest) + "\n").encode())
    new_msha = _sha256_file(manifest_path)
    # Rebuild bindings pointing at the same files with the new sha.
    mut.bindings = replace(
        mut.bindings,
        d3=replace(
            mut.bindings.d3,
            manifest_hash=manifest["normalized_hash"],
            manifest_sha256=new_msha,
            files=(
                R.FileBinding(f"normalized/{D3_ID}/observations.parquet", sha),
                mut.bindings.d3.files[1],
            ),
        ),
    )
    mut_content = mut.run()
    assert _data_file_bytes(base, base_content) == _data_file_bytes(
        mut, mut_content
    )
    assert key == key2


def test_item36_zero_factor_fails(tmp_path: Path) -> None:
    store = _Store(tmp_path, name="s")
    key = store.key("AAA", listed_start=S[POS[F1] - 5], listed_end=FAR)
    store.add_sessions("AAA", S[POS[F1] - 5], R.DEVELOPMENT_END)
    store.ca("AAA", S[POS[F1] + 2], factor=0.0)
    store.member(F1, key, 1)
    store.build()
    with pytest.raises(R.D7ConsistencyError):
        store.run()


def test_item37_non_finite_return_fails(tmp_path: Path) -> None:
    store = _Store(tmp_path, name="s")
    key = store.key("AAA", listed_start=S[POS[F1] - 5], listed_end=FAR)
    store.add_sessions("AAA", S[POS[F1] - 5], R.DEVELOPMENT_END)
    # Alter the existing row at f+1 to a non-finite close (do not add a
    # duplicate date, which would fail closed for a different reason).
    store.obs_rows = [
        (r[0], r[1], float("inf"), r[3], r[4])
        if r[1] == S[POS[F1] + 1]
        else r
        for r in store.obs_rows
    ]
    store.member(F1, key, 1)
    store.build()
    with pytest.raises(R.D7ConsistencyError):
        store.run()


# ---------------------------------------------------------------------------
# Population and survivorship (items 38-42)
# ---------------------------------------------------------------------------


def test_item38_top1000_preservation(tmp_path: Path) -> None:
    store = _Store(tmp_path, name="s")
    ls = S[POS[F1] - 5]
    k1 = store.key("AAA", listed_start=ls, listed_end=FAR)
    k2 = store.key("BBB", listed_start=ls, listed_end=FAR)
    k3 = store.key("CCC", listed_start=ls, listed_end=FAR)
    for t in ("AAA", "BBB", "CCC"):
        store.add_sessions(t, ls, R.DEVELOPMENT_END)
    store.member(F1, k1, 1, top_200=True, top_500=True)
    store.member(F1, k2, 2, top_500=True)
    store.member(F1, k3, 3)
    store.build()
    content = store.run()
    members = _read(store, content, R.MEMBERS_FILE)
    assert set(members["security_key"]) == {k1, k2, k3}
    r1 = members[members["security_key"] == k1].iloc[0]
    assert bool(r1["top_200"]) and bool(r1["top_500"]) and bool(r1["top_1000"])
    r2 = members[members["security_key"] == k2].iloc[0]
    assert not bool(r2["top_200"]) and bool(r2["top_500"])
    r3 = members[members["security_key"] == k3].iloc[0]
    assert not bool(r3["top_200"]) and not bool(r3["top_500"])


def test_item39_disappeared_security_kept(tmp_path: Path) -> None:
    store = _Store(tmp_path, name="s")
    key = store.key("AAA", listed_start=S[POS[F1] - 5], listed_end=FAR)
    store.add_sessions("AAA", S[POS[F1] - 5], F1)  # no rows after f
    store.member(F1, key, 1)
    store.build()
    content = store.run()
    assert len(_read(store, content, R.LABELS_FILE)) == 63
    assert key in set(_read(store, content, R.MEMBERS_FILE)["security_key"])

    # Within-development variant: listed_end inside development but beyond
    # every endpoint; the security is still kept and not terminal.
    wd = _Store(tmp_path, name="s_wd")
    wd_key = wd.key("AAA", listed_start=S[POS[F1] - 5], listed_end=LD)
    wd.add_sessions("AAA", S[POS[F1] - 5], F1)
    wd.member(F1, wd_key, 1)
    wd.build()
    wd_content = wd.run()
    assert len(_read(wd, wd_content, R.LABELS_FILE)) == 63
    assert wd_key in set(_read(wd, wd_content, R.MEMBERS_FILE)["security_key"])
    wd_row = _label(wd, wd_content, F1, wd_key, 1)
    assert not bool(wd_row["terminal_affected"])
    assert wd_row["label_state"] == R.STATE_INCOMPLETE


def test_item40_survivor_intersection_adversary(tmp_path: Path) -> None:
    store, key = _one_member(tmp_path, obs_end=F1)
    content = store.run()
    assert key in set(_read(store, content, R.MEMBERS_FILE)["security_key"])
    assert key in set(_read(store, content, R.LABELS_FILE)["security_key"])
    assert key in set(_read(store, content, R.DAILY_FILE)["security_key"])

    # Within-development variant: still present, still not terminal.
    wd, wd_key = _one_member(tmp_path, "wd", listed_end=LD, obs_end=F1)
    wd_content = wd.run()
    assert wd_key in set(_read(wd, wd_content, R.MEMBERS_FILE)["security_key"])
    assert wd_key in set(_read(wd, wd_content, R.LABELS_FILE)["security_key"])
    assert wd_key in set(_read(wd, wd_content, R.DAILY_FILE)["security_key"])
    wd_row = _label(wd, wd_content, F1, wd_key, 1)
    assert not bool(wd_row["terminal_affected"])
    assert wd_row["label_state"] == R.STATE_INCOMPLETE


def test_item41_bad_identity_fails(tmp_path: Path) -> None:
    # Missing from D4.
    store = _Store(tmp_path, name="missing")
    key = store.key("AAA", listed_start=S[POS[F1] - 5], listed_end=FAR)
    store.add_sessions("AAA", S[POS[F1] - 5], R.DEVELOPMENT_END)
    store.member(F1, key, 1)
    store.keys = []  # drop the identity row
    store.build()
    with pytest.raises(R.D7ConsistencyError):
        store.run()

    # Duplicated in D4.
    store = _Store(tmp_path, name="dup")
    key = store.key("AAA", listed_start=S[POS[F1] - 5], listed_end=FAR)
    store.identity_row("AAA", security_key=key)
    store.add_sessions("AAA", S[POS[F1] - 5], R.DEVELOPMENT_END)
    store.member(F1, key, 1)
    store.build()
    with pytest.raises(R.D7ConsistencyError):
        store.run()

    # Not INTERVAL_ASSIGNED.
    store = _Store(tmp_path, name="state")
    key = store.key(
        "AAA", listed_start=S[POS[F1] - 5], listed_end=FAR, state="NOT_ACQUIRED"
    )
    store.add_sessions("AAA", S[POS[F1] - 5], R.DEVELOPMENT_END)
    store.member(F1, key, 1)
    store.build()
    with pytest.raises(R.D7ConsistencyError):
        store.run()

    # Null listed_start: fail closed, never substitute listed_end.
    store = _Store(tmp_path, name="null_start")
    key = store.key("AAA", listed_start=S[POS[F1] - 5], listed_end=FAR)
    store.keys[-1]["listed_start"] = None
    store.add_sessions("AAA", S[POS[F1] - 5], R.DEVELOPMENT_END)
    store.member(F1, key, 1)
    store.build()
    with pytest.raises(R.D7ConsistencyError):
        store.run()


def test_item42_member_count_and_rank_failures(tmp_path: Path) -> None:
    # Count mismatch: n_eligible says 2, one member.
    store = _Store(tmp_path, name="count")
    key = store.key("AAA", listed_start=S[POS[F1] - 5], listed_end=FAR)
    store.add_sessions("AAA", S[POS[F1] - 5], R.DEVELOPMENT_END)
    store.member(F1, key, 1)
    store.set_n_eligible(F1, 2)
    store.build()
    with pytest.raises(R.D7ConsistencyError):
        store.run()

    # Non-contiguous ranks.
    store = _Store(tmp_path, name="rank")
    ls = S[POS[F1] - 5]
    k1 = store.key("AAA", listed_start=ls, listed_end=FAR)
    k2 = store.key("BBB", listed_start=ls, listed_end=FAR)
    store.add_sessions("AAA", ls, R.DEVELOPMENT_END)
    store.add_sessions("BBB", ls, R.DEVELOPMENT_END)
    store.member(F1, k1, 1)
    store.member(F1, k2, 3)
    store.build()
    with pytest.raises(R.D7ConsistencyError):
        store.run()


# ---------------------------------------------------------------------------
# Reserve (items 43-49)
# ---------------------------------------------------------------------------


def _reserve_store(tmp_path: Path, name: str, f: date):
    store = _Store(tmp_path, name=name)
    key = store.key("AAA", listed_start=S[POS[f] - 5], listed_end=FAR)
    store.add_sessions("AAA", S[POS[f] - 5], R.DEVELOPMENT_END)
    store.member(f, key, 1)
    return store, key, f


def test_item43_reserve_crossing_purged(tmp_path: Path) -> None:
    f = date(2023, 3, 31)
    store, key, _ = _reserve_store(tmp_path, "s", f)
    store.build()
    content = store.run()
    purged = _label(store, content, f, key, 63)
    assert purged["label_state"] == R.STATE_PURGED_RESERVE_CROSSING
    assert pd.isna(purged["terminal_affected"])
    assert pd.isna(purged["observed_path_complete"])
    assert pd.isna(purged["n_observed_sessions"])
    assert pd.isna(purged["observed_segment_end"])
    assert purged["endpoint"] >= R.RESERVE_START
    not_purged = _label(store, content, f, key, 62)
    assert not_purged["label_state"] != R.STATE_PURGED_RESERVE_CROSSING
    daily = _read(store, content, R.DAILY_FILE)
    assert (pd.to_datetime(daily["date"]).dt.date < R.RESERVE_START).all()


def test_item44_purge_before_p7b_contract(tmp_path: Path) -> None:
    f = date(2023, 3, 31)
    store, key, _ = _reserve_store(tmp_path, "s", f)
    store.build()
    content = store.run()
    label = _label(store, content, f, key, 63)
    assert label["label_state"] == R.STATE_PURGED_RESERVE_CROSSING

    factor_panel = pd.DataFrame(
        {
            "date": pd.to_datetime([f]),
            "stock_id": pd.Series([key], dtype="string"),
            "value": pd.Series([1.0], dtype="float64"),
        }
    )
    dates = [d for d in S if f < d <= R.DEVELOPMENT_END]
    realized = pd.DataFrame(
        {
            "date": pd.to_datetime(dates),
            "stock_id": pd.Series([key] * len(dates), dtype="string"),
            fr.DEFAULT_RETURN_COL: pd.Series([0.0] * len(dates), dtype="float64"),
        }
    )
    alignment = fr.align_forward_returns(factor_panel, realized, 63)
    assert alignment.n_insufficient_forward_window == 1
    assert (
        alignment.panel.iloc[0][fr.STATUS_COL]
        == fr.STATUS_INSUFFICIENT_FORWARD_WINDOW
    )
    # D7a never reports it as INCOMPLETE.
    assert label["label_state"] != R.STATE_INCOMPLETE


def test_item45_refuses_forbidden_path_components(tmp_path: Path) -> None:
    store, key = _one_member(tmp_path)
    store.build()
    resolver = R.Resolver(store.root, store.bindings)
    for component in ("reserve_sealed", "raw", "dsc_evidence"):
        with pytest.raises(R.ReserveFirewallError):
            resolver.resolve(f"{component}/n-test/observations.parquet")
    # The component check must fire even when the malicious binding would make
    # the path allowlisted (defence in depth, not just an allowlist miss).
    reserved = R.StageBinding(
        stage="D3",
        artifact_id=D3_ID,
        manifest_relative_path="reserve_sealed/n-test/build.json",
        manifest_hash_field="normalized_hash",
        manifest_hash="0" * 64,
        manifest_sha256="0" * 64,
        files=(
            R.FileBinding("reserve_sealed/n-test/observations.parquet", "0" * 64),
        ),
    )
    bindings = replace(store.bindings, d3=reserved)
    resolver = R.Resolver(store.root, bindings)
    with pytest.raises(R.ReserveFirewallError):
        resolver.resolve("reserve_sealed/n-test/observations.parquet")


def test_item46_refuses_non_allowlisted_paths(tmp_path: Path) -> None:
    store, key = _one_member(tmp_path)
    store.build()
    resolver = R.Resolver(store.root, store.bindings)
    for path in (
        f"normalized/{D3_ID}/source_summary.parquet",
        f"normalized/{D3_ID}/obs_flags.parquet",
        f"derived/qc/{D5_ID}/security_qc.parquet",
        f"derived/universe/{D6_ID}/audit_development.parquet",
        "derived/identity/i-d7test/interval_mismatch.parquet",
        "derived/classification/c-test/class_timeline.parquet",
    ):
        with pytest.raises(R.ReserveFirewallError):
            resolver.resolve(path)
    with pytest.raises(R.ReturnsInputError):
        resolver.resolve("/tmp/absolute.parquet")
    with pytest.raises(R.ReturnsInputError):
        resolver.resolve("../escape.parquet")


def test_item47_reserve_dated_rows_fail(tmp_path: Path) -> None:
    # Observation.
    store, key = _one_member(tmp_path, "obs")
    store.obs("AAA", R.RESERVE_START, 10.0)
    store.build()
    with pytest.raises(R.ReserveFirewallError):
        store.run()
    assert not (store.root / "derived" / "returns").exists()

    # Day flag.
    store, _ = _one_member(tmp_path, "flag")
    store.flag("AAA", R.RESERVE_START, 0, "OHLC_INCONSISTENT")
    store.build()
    with pytest.raises(R.ReserveFirewallError):
        store.run()

    # Corporate action.
    store, _ = _one_member(tmp_path, "ca")
    store.ca("AAA", R.RESERVE_START, factor=1.1)
    store.build()
    with pytest.raises(R.ReserveFirewallError):
        store.run()


def test_item48_future_listed_end_hidden(tmp_path: Path) -> None:
    store, key = _one_member(tmp_path, listed_end=date(2099, 1, 1))
    content = store.run()
    members = _read(store, content, R.MEMBERS_FILE)
    row = members[members["security_key"] == key].iloc[0]
    assert pd.isna(row["listed_end_dev"])
    assert row["listing_status"] == R.LISTING_CONTINUES_BEYOND_DEVELOPMENT_PERIOD
    for path in _out_dir(store, content).iterdir():
        assert b"2099-01-01" not in path.read_bytes()


def test_item49_listed_end_on_development_end(tmp_path: Path) -> None:
    store, key = _one_member(tmp_path, listed_end=R.DEVELOPMENT_END)
    content = store.run()
    members = _read(store, content, R.MEMBERS_FILE)
    row = members[members["security_key"] == key].iloc[0]
    assert row["listed_end_dev"] == R.DEVELOPMENT_END
    assert row["listing_status"] == R.LISTING_ENDS_WITHIN_DEVELOPMENT


# ---------------------------------------------------------------------------
# Semantic separation (items 50-52)
# ---------------------------------------------------------------------------


def test_item50_empty_path_is_not_observed_zero(tmp_path: Path) -> None:
    store, key = _terminal_store(tmp_path, "s", F1)
    content = store.run()
    daily = _read(store, content, R.DAILY_FILE)
    dates = pd.to_datetime(daily["date"]).dt.date
    assert F1 not in set(dates)
    # No synthetic zero-valued daily return for the empty path.
    assert daily["daily_total_return"].isna().all()
    row = _label(store, content, F1, key, 1)
    assert row["label_state"] == R.STATE_EMPTY_OBSERVED_PATH_IDENTITY
    assert bool(row["observed_path_complete"])
    assert row["label_state"] != R.STATE_INCOMPLETE


def test_item51_column_whitelist(tmp_path: Path) -> None:
    store, key = _one_member(tmp_path)
    content = store.run()
    for name in (R.MEMBERS_FILE, R.HORIZONS_FILE, R.DAILY_FILE, R.LABELS_FILE):
        columns = pd.read_parquet(_out_dir(store, content) / name).columns
        for column in columns:
            lowered = column.lower()
            for forbidden in _FORBIDDEN_COLUMN_SUBSTRINGS:
                assert forbidden not in lowered, (name, column, forbidden)


def test_item52_all_horizons_present(tmp_path: Path) -> None:
    store, key = _one_member(tmp_path)
    content = store.run()
    labels = _read(store, content, R.LABELS_FILE)
    assert len(labels) == 63
    assert sorted(labels["h"].tolist()) == list(range(1, 64))


# ---------------------------------------------------------------------------
# Determinism and bindings (items 53-57)
# ---------------------------------------------------------------------------


def test_item53_input_permutation_byte_identical(tmp_path: Path) -> None:
    base, key = _one_member(tmp_path, "base")
    base_content = base.run()
    mut, _ = _one_member(tmp_path, "mut")
    mut.build(reverse=True)
    mut_content = mut.run()
    assert _data_file_bytes(base, base_content) == _data_file_bytes(mut, mut_content)


def test_item54_tz_locale_byte_identical(tmp_path: Path, monkeypatch) -> None:
    store, key = _one_member(tmp_path, "base")
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setenv("LC_ALL", "C")
    content = store.run()
    first = _data_file_bytes(store, content)

    store2, _ = _one_member(tmp_path, "mut")
    monkeypatch.setenv("TZ", "America/New_York")
    monkeypatch.setenv("LC_ALL", "en_US.UTF-8")
    content2 = store2.run()
    assert first == _data_file_bytes(store2, content2)
    assert content["returns_hash"] == content2["returns_hash"]


def test_item55_rebuild_identical_and_nondeterminism_guard(tmp_path: Path) -> None:
    store, key = _one_member(tmp_path, "s")
    content = store.run()
    before = _data_file_bytes(store, content)
    again = store.run()
    assert again["returns_hash"] == content["returns_hash"]
    assert _data_file_bytes(store, content) == before
    # Tamper with an existing output: a rebuild must refuse to overwrite it.
    target = _out_dir(store, content) / R.MEMBERS_FILE
    target.write_bytes(target.read_bytes() + b"tampered")
    with pytest.raises(R.NondeterministicReturnsError):
        store.run()


def test_item56_binding_and_document_mismatch(tmp_path: Path) -> None:
    store, key = _one_member(tmp_path)
    store.build()
    # File-level binding mismatch for each stage.
    for stage_name in ("d3", "d4", "d5", "d6"):
        stage = getattr(store.bindings, stage_name)
        bad_files = list(stage.files)
        bad_files[0] = replace(bad_files[0], sha256="0" * 64)
        bad_stage = replace(stage, files=tuple(bad_files))
        bad = replace(store.bindings, **{stage_name: bad_stage})
        with pytest.raises(R.ReturnsBindingError):
            R.run_returns(store.root, bindings=bad, calendar=CAL)
    # Manifest top-level hash mismatch.
    bad_stage = replace(store.bindings.d3, manifest_hash="0" * 64)
    bad = replace(store.bindings, d3=bad_stage)
    with pytest.raises(R.ReturnsBindingError):
        R.run_returns(store.root, bindings=bad, calendar=CAL)
    # Manifest-file hash mismatch for each stage.
    for stage_name in ("d3", "d4", "d5", "d6"):
        stage = getattr(store.bindings, stage_name)
        bad_stage = replace(stage, manifest_sha256="0" * 64)
        bad = replace(store.bindings, **{stage_name: bad_stage})
        with pytest.raises(R.ReturnsBindingError):
            R.run_returns(store.root, bindings=bad, calendar=CAL)
    # Document hash mismatch.
    bad_docs = tuple(
        replace(doc, sha256="0" * 64) if doc.name == "contract" else doc
        for doc in store.bindings.documents
    )
    bad = replace(store.bindings, documents=bad_docs)
    with pytest.raises(R.ReturnsBindingError):
        R.run_returns(store.root, bindings=bad, calendar=CAL)


def test_item57_decoy_artifacts_ignored(tmp_path: Path) -> None:
    store, key = _one_member(tmp_path)
    # Decoy newer directories, none of which are in the allowlist.
    for rel in (
        "normalized/n-decoy/observations.parquet",
        "derived/qc/q-decoy/day_flags.parquet",
        "derived/identity/i-decoy/identity_assignment.parquet",
        "derived/universe/u-decoy/universe_development.parquet",
    ):
        path = store.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"decoy")
    store.build()
    content = store.run()
    assert key in set(_read(store, content, R.MEMBERS_FILE)["security_key"])


# ---------------------------------------------------------------------------
# Temporal properties (items 58-60)
# ---------------------------------------------------------------------------


def _temporal_base(tmp_path: Path):
    store = _Store(tmp_path, name="base")
    key = store.key("AAA", listed_start=S[POS[F1] - 5], listed_end=FAR)
    store.add_sessions("AAA", S[POS[F1] - 5], R.DEVELOPMENT_END)
    store.member(F1, key, 1)
    store.build()
    content = store.run()
    return store, content, key


def test_item58_endpoint_invariance(tmp_path: Path) -> None:
    base, base_content, key = _temporal_base(tmp_path)
    endpoint1 = S[POS[F1] + 1]

    # A row altered after the endpoint does not change the h=1 label.
    mut = _Store(tmp_path, name="mut1")
    mut.key("AAA", listed_start=S[POS[F1] - 5], listed_end=FAR, security_key=key)
    mut.add_sessions("AAA", S[POS[F1] - 5], R.DEVELOPMENT_END)
    mut.obs_rows = [
        (r[0], r[1], 99.0, r[3], r[4]) if r[1] == S[POS[F1] + 4] else r
        for r in mut.obs_rows
    ]
    mut.member(F1, key, 1)
    mut.build()
    mut_content = mut.run()
    pd.testing.assert_series_equal(
        _label(base, base_content, F1, key, 1),
        _label(mut, mut_content, F1, key, 1),
    )

    # A corporate action after the endpoint does not change the h=1 label.
    mut2 = _Store(tmp_path, name="mut2")
    mut2.key("AAA", listed_start=S[POS[F1] - 5], listed_end=FAR, security_key=key)
    mut2.add_sessions("AAA", S[POS[F1] - 5], R.DEVELOPMENT_END)
    mut2.ca("AAA", S[POS[F1] + 4], factor=1.1)
    mut2.member(F1, key, 1)
    mut2.build()
    mut2_content = mut2.run()
    pd.testing.assert_series_equal(
        _label(base, base_content, F1, key, 1),
        _label(mut2, mut2_content, F1, key, 1),
    )

    # A different listed_end still beyond the endpoint leaves h=1 identical.
    mut3 = _terminal_store_sk(tmp_path, "mut3", date(2088, 1, 1), key)
    mut3_content = mut3.run()
    pd.testing.assert_series_equal(
        _label(base, base_content, F1, key, 1),
        _label(mut3, mut3_content, F1, key, 1),
    )
    assert endpoint1 == S[POS[F1] + 1]

    # Within-development variants (listed_end <= 2023-06-30 but > endpoint).
    wd_base, wd_key = _one_member(
        tmp_path, "wd_base", listed_end=LD, obs_end=S[POS[F1] + 1]
    )
    wd_base_content = wd_base.run()
    wd_mut, _ = _one_member(
        tmp_path, "wd_mut", listed_end=LD, obs_end=S[POS[F1] + 1]
    )
    wd_mut.obs("AAA", S[POS[F1] + 2], 11.0)  # append after the endpoint
    wd_mut.build()
    wd_mut_content = wd_mut.run()
    wd_b = _label(wd_base, wd_base_content, F1, wd_key, 1)
    wd_m = _label(wd_mut, wd_mut_content, F1, wd_key, 1)
    assert not bool(wd_b["terminal_affected"])
    assert not bool(wd_m["terminal_affected"])
    pd.testing.assert_series_equal(wd_b, wd_m)

    # A different within-development listed_end still beyond the endpoint.
    wd2 = _terminal_store_sk(tmp_path, "wd2", S[POS[F1] + 120], wd_key)
    wd2_content = wd2.run()
    pd.testing.assert_series_equal(
        wd_b, _label(wd2, wd2_content, F1, wd_key, 1)
    )


def _terminal_store_sk(tmp_path: Path, name: str, listed_end: date, sk: str):
    store = _Store(tmp_path, name=name)
    store.key("AAA", listed_start=S[POS[F1] - 5], listed_end=listed_end, security_key=sk)
    store.add_sessions("AAA", S[POS[F1] - 5], R.DEVELOPMENT_END)
    store.member(F1, sk, 1)
    store.build()
    return store


def test_item59_horizon_nesting(tmp_path: Path) -> None:
    base, base_content, key = _temporal_base(tmp_path)
    mut = _Store(tmp_path, name="mut")
    mut.key("AAA", listed_start=S[POS[F1] - 5], listed_end=FAR, security_key=key)
    mut.add_sessions("AAA", S[POS[F1] - 5], R.DEVELOPMENT_END)
    # Alter evidence at h=5's endpoint (strictly inside (endpoint(1), endpoint(5)]).
    mut.obs_rows = [r for r in mut.obs_rows if r[1] != S[POS[F1] + 5]]
    mut.member(F1, key, 1)
    mut.build()
    mut_content = mut.run()
    pd.testing.assert_series_equal(
        _label(base, base_content, F1, key, 1),
        _label(mut, mut_content, F1, key, 1),
    )

    # Within-development variant: altering evidence in
    # (endpoint(1), endpoint(2)] must leave the h=1 label identical, and the
    # newly-filled gap must not promote h=1 to terminal.
    wd_base, wd_key = _one_member(
        tmp_path, "wd_base", listed_end=LD, obs_end=S[POS[F1] + 1]
    )
    wd_base_content = wd_base.run()
    wd_mut, _ = _one_member(
        tmp_path, "wd_mut", listed_end=LD, obs_end=S[POS[F1] + 1]
    )
    wd_mut.obs("AAA", S[POS[F1] + 2], 11.0)
    wd_mut.build()
    wd_mut_content = wd_mut.run()
    wd_b = _label(wd_base, wd_base_content, F1, wd_key, 1)
    wd_m = _label(wd_mut, wd_mut_content, F1, wd_key, 1)
    assert not bool(wd_b["terminal_affected"])
    assert not bool(wd_m["terminal_affected"])
    pd.testing.assert_series_equal(wd_b, wd_m)


def test_item60_membership_immutable(tmp_path: Path) -> None:
    base, base_content, key = _temporal_base(tmp_path)
    mut = _Store(tmp_path, name="mut")
    mut.key("AAA", listed_start=S[POS[F1] - 5], listed_end=FAR, security_key=key)
    mut.add_sessions("AAA", S[POS[F1] - 5], R.DEVELOPMENT_END)
    mut.obs_rows = [
        (r[0], r[1], 999.0, r[3], r[4]) if r[1] == S[POS[F1] + 2] else r
        for r in mut.obs_rows
    ]
    mut.ca("AAA", S[POS[F1] + 3], factor=1.5)
    mut.member(F1, key, 1)
    mut.build()
    mut_content = mut.run()
    pd.testing.assert_frame_equal(
        _read(base, base_content, R.MEMBERS_FILE),
        _read(mut, mut_content, R.MEMBERS_FILE),
    )
