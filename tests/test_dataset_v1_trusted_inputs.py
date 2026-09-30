"""D7c development -> Phase-6 TrustedInput bridge tests.

Every test builds a synthetic ``tmp_path`` store with injected
:class:`~smart_beta.dataset_v1.trusted_inputs.Bindings`; none touches the real
``data_store``, ``reserve_sealed`` or any credential.  An autouse socket guard
and credential sentinel mirror the D5/D-SC/D6/D7a suites.

The test functions are named ``test_itemNN_*`` so the completion report's
test-to-item mapping (items 1-66 of the frozen spec §8) is mechanical.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import socket
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest

from smart_beta.dataset_v1 import trusted_inputs as T
from smart_beta.spec.engine import (
    EvidenceClass,
    GridMismatchError,
    TrustedInput,
    admit,
    evaluate_factor,
)
from smart_beta.spec.factor_spec import FactorInput, FactorSpec, MissingPolicy
from smart_beta.spec.requirements import (
    DataRequirement,
    Frequency,
    ObservationPeriod,
    RevisionPolicy,
    Unit,
    require_satisfiable,
)
from smart_beta.vendors.tiingo.calendar_source import build_nyse_calendar

# ---------------------------------------------------------------------------
# Constants and shared calendar
# ---------------------------------------------------------------------------

CAL = build_nyse_calendar(T.CALENDAR_START, T.CALENDAR_END)
SESSIONS = [ts.date() for ts in CAL.dates]
POS = {d: i for i, d in enumerate(SESSIONS)}
FAR = date(2099, 1, 1)
D3_ID = "n-f874d3ecb61374d4"
CREDENTIAL_SENTINEL = "d7c-credential-sentinel-9b1f"

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


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch):
    def _refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("D7c tests must never open a socket")

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


def _prev(d: date) -> date | None:
    i = POS.get(d)
    if i is None or i == 0:
        return None
    return SESSIONS[i - 1]


def _next(d: date) -> date | None:
    i = POS.get(d)
    if i is None or i + 1 >= len(SESSIONS):
        return None
    return SESSIONS[i + 1]


def _sessions_between(a: date, b: date) -> list[date]:
    return [d for d in SESSIONS if a <= d <= b]


def _canon(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write_parquet(root: Path, rel: str, frame: pd.DataFrame) -> str:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, engine="pyarrow", index=False, compression="zstd")
    return _sha256_file(path)


def _write_manifest(root: Path, rel: str, field_name: str, files: dict) -> tuple[str, str]:
    content = {"files_sha256": files}
    content[field_name] = hashlib.sha256(_canon(files).encode("utf-8")).hexdigest()
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((_canon(content) + "\n").encode("utf-8"))
    return content[field_name], _sha256_file(path)


def _stage(
    root: Path,
    stage: str,
    artifact_id: str,
    manifest_rel: str,
    field: str,
    files: Sequence[tuple[str, pd.DataFrame]],
) -> T.StageBinding:
    hashes: dict[str, str] = {}
    for rel, frame in files:
        hashes[rel] = _write_parquet(root, rel, frame)
    manifest_hash, manifest_sha = _write_manifest(
        root, manifest_rel, field, hashes
    )
    return T.StageBinding(
        stage=stage,
        artifact_id=artifact_id,
        manifest_relative_path=manifest_rel,
        manifest_hash_field=field,
        manifest_hash=manifest_hash,
        manifest_sha256=manifest_sha,
        files=tuple(T.FileBinding(rel, sha) for rel, sha in hashes.items()),
    )


class _Store:
    """A synthetic D3/D4/D5/D-SC store with injected bindings."""

    def __init__(self, tmp_path: Path, name: str = "store"):
        self.root = tmp_path / name
        self.root.mkdir(parents=True, exist_ok=True)
        self.keys: list[dict[str, Any]] = []
        self.obs_rows: list[tuple] = []
        self.flag_rows: list[tuple] = []
        self.ca_rows: list[tuple] = []
        self.class_rows: list[tuple] = []
        self.policy_rows: list[tuple] = []
        self.adj_close: float | None = None
        self._next_idx: dict[str, int] = {}
        self._explicit_class: set[str] = set()
        self._explicit_policy: set[str] = set()
        self._bindings: T.Bindings | None = None

    # -- declarations ----------------------------------------------------

    def key(
        self,
        ticker: str,
        *,
        listed_start: date = date(2015, 1, 2),
        listed_end: date = FAR,
        security_key: str | None = None,
        state: str = T.STATE_INTERVAL_ASSIGNED,
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
        listed_start: date = date(2015, 1, 2),
        listed_end: date = FAR,
        state: str = T.STATE_INTERVAL_ASSIGNED,
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
        volume: int | None = 100,
        *,
        flag: str | None = None,
        disposition: str | None = None,
    ) -> int:
        idx = self._next_idx.get(ticker, 0)
        self._next_idx[ticker] = idx + 1
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
        volume: int | None = 100,
        skip: Sequence[date] = (),
        flag: str | None = None,
    ) -> None:
        skipped = set(skip)
        for d in _sessions_between(start, end):
            if d in skipped:
                continue
            self.obs(ticker, d, close, volume, flag=flag)

    def add_closes(self, ticker: str, closes: Mapping[date, float]) -> None:
        for d, close in closes.items():
            self.obs(ticker, d, close)

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

    def class_seg(
        self, security_key: str, knowledge_date: date, state: str, reason: str = "TEST"
    ) -> None:
        self.class_rows.append((security_key, knowledge_date, state, reason))
        self._explicit_class.add(security_key)

    def policy_seg(
        self,
        security_key: str,
        knowledge_date: date,
        policy_state: str,
        active_sources: str = "",
    ) -> None:
        self.policy_rows.append(
            (security_key, knowledge_date, policy_state, active_sources)
        )
        self._explicit_policy.add(security_key)

    def index_of(self, ticker: str, d: date) -> int:
        for row in self.obs_rows:
            if row[0] == ticker and row[1] == d:
                return int(row[4])
        raise AssertionError(f"no observation for ({ticker}, {d})")

    # -- materialise -----------------------------------------------------

    def build(self, *, reverse: bool = False) -> T.Bindings:
        if self._bindings is not None and not reverse:
            return self._bindings
        root = self.root
        obs_rows = list(self.obs_rows)
        key_rows = list(self.keys)
        flag_rows = list(self.flag_rows)
        ca_rows = list(self.ca_rows)
        class_rows = list(self.class_rows)
        policy_rows = list(self.policy_rows)
        for key in key_rows:
            sk = key["security_key"]
            if sk not in self._explicit_class:
                class_rows.append((sk, key["listed_start"], "COMMON_STOCK", "DEFAULT"))
            if sk not in self._explicit_policy:
                policy_rows.append(
                    (sk, key["listed_start"], T.POLICY_NO_EXCLUSION, "")
                )
        if reverse:
            obs_rows.reverse()
            key_rows.reverse()
            flag_rows.reverse()
            ca_rows.reverse()
            class_rows.reverse()
            policy_rows.reverse()

        obs = pd.DataFrame(
            {
                "ticker": pd.Series([r[0] for r in obs_rows], dtype=_STRING),
                "date": pd.Series([r[1] for r in obs_rows], dtype=_DATE32),
                "close": pd.Series([r[2] for r in obs_rows], dtype="float64"),
                "volume": pd.Series([r[3] for r in obs_rows], dtype="Int64"),
                "raw_row_index": pd.Series([r[4] for r in obs_rows], dtype="int64"),
            }
        )
        if self.adj_close is not None:
            obs["adjClose"] = float(self.adj_close)
        ca = pd.DataFrame(
            {
                "stock_id": pd.Series([r[0] for r in ca_rows], dtype=_STRING),
                "effective_date": pd.Series(
                    [r[1] for r in ca_rows], dtype=_DATE32
                ),
                "action_type": pd.Series([r[2] for r in ca_rows], dtype=_STRING),
                "knowledge_date": pd.Series([r[3] for r in ca_rows], dtype=_DATE32),
                "adjustment_factor": pd.Series(
                    [r[4] for r in ca_rows], dtype="float64"
                ),
                "is_superseded": pd.Series([r[5] for r in ca_rows], dtype=bool),
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
                "disposition": pd.Series([r[4] for r in flag_rows], dtype=_STRING),
            }
        )
        cls = pd.DataFrame(
            {
                "security_key": pd.Series(
                    [r[0] for r in class_rows], dtype=_STRING
                ),
                "knowledge_date": pd.Series(
                    [r[1] for r in class_rows], dtype=_DATE32
                ),
                "state": pd.Series([r[2] for r in class_rows], dtype=_STRING),
                "reason": pd.Series([r[3] for r in class_rows], dtype=_STRING),
                "evidence_ids": pd.Series(["[]"] * len(class_rows), dtype=_STRING),
            }
        )
        pol = pd.DataFrame(
            {
                "security_key": pd.Series(
                    [r[0] for r in policy_rows], dtype=_STRING
                ),
                "knowledge_date": pd.Series(
                    [r[1] for r in policy_rows], dtype=_DATE32
                ),
                "policy_state": pd.Series(
                    [r[2] for r in policy_rows], dtype=_STRING
                ),
                "active_sources": pd.Series(
                    [r[3] for r in policy_rows], dtype=_STRING
                ),
                "evidence_refs": pd.Series(["[]"] * len(policy_rows), dtype=_STRING),
            }
        )

        d3 = _stage(
            root,
            "D3",
            D3_ID,
            f"normalized/{D3_ID}/build.json",
            "normalized_hash",
            [
                (f"normalized/{D3_ID}/observations.parquet", obs),
                (f"normalized/{D3_ID}/ca_events.parquet", ca),
            ],
        )
        d4 = _stage(
            root,
            "D4",
            "i-d7ctest",
            "derived/identity/i-d7ctest/identity.json",
            "identity_hash",
            [("derived/identity/i-d7ctest/identity_assignment.parquet", ident)],
        )
        d5 = _stage(
            root,
            "D5",
            "q-d7ctest",
            "derived/qc/q-d7ctest/qc_report.json",
            "qc_hash",
            [("derived/qc/q-d7ctest/day_flags.parquet", flags)],
        )
        dsc = _stage(
            root,
            "DSC",
            "c-d7ctest",
            "derived/classification/c-d7ctest/dsc.json",
            "dsc_hash",
            [
                ("derived/classification/c-d7ctest/class_timeline.parquet", cls),
                ("derived/classification/c-d7ctest/policy_timeline.parquet", pol),
            ],
        )
        bindings = replace(
            T.default_bindings(), d3=d3, d4=d4, d5=d5, dsc=dsc
        )
        if not reverse:
            self._bindings = bindings
        return bindings


# ---------------------------------------------------------------------------
# Build / load helpers
# ---------------------------------------------------------------------------


def _build(store: _Store, **kwargs: Any) -> dict[str, Any]:
    return T.build_trusted_inputs(
        store.root, bindings=store.build(), calendar=CAL, **kwargs
    )


def _load(store: _Store, **kwargs: Any) -> dict[str, TrustedInput]:
    T.build_trusted_inputs(
        store.root, bindings=store.build(), calendar=CAL, **kwargs
    )
    return T.load_trusted_inputs(
        store.root, bindings=store.build(), calendar=CAL, **kwargs
    )


def _artifact_dir(store: _Store, content: Mapping[str, Any]) -> Path:
    return store.root / "derived" / "trusted_inputs" / content["ti_id"]


def _read_long(store: _Store, content: Mapping[str, Any], name: str) -> pd.DataFrame:
    return pd.read_parquet(_artifact_dir(store, content) / T._INPUT_FILES[name])


def _value(inputs: Mapping[str, TrustedInput], name: str, key: str, d: date) -> float:
    frame = inputs[name].values
    return float(frame.loc[pd.Timestamp(d), key])


def _row_is_nan(inputs: Mapping[str, TrustedInput], name: str, key: str, d: date) -> bool:
    frame = inputs[name].values
    return bool(pd.isna(frame.loc[pd.Timestamp(d), key]))


def _dag_spec(
    expression: object,
    *,
    alias: str = "ret",
    semantic_id: str = T.INPUT_DAILY_TOTAL_RETURN,
    observation_period: ObservationPeriod = ObservationPeriod.PERIOD,
    units: Unit | None = Unit.FRACTION,
    lookback: int = 0,
) -> FactorSpec:
    requirement = DataRequirement(
        semantic_id=semantic_id,
        frequency=Frequency.DAILY,
        observation_period=observation_period,
        units=units,
        lookback=lookback,
    )
    return FactorSpec(
        id="d7c_factor",
        description="D7c bridge test factor",
        expression=expression,
        inputs=(FactorInput(alias, requirement),),
        frequency=Frequency.DAILY,
        missing_policy=MissingPolicy.PROPAGATE,
    )


def _eval(
    inputs: Mapping[str, TrustedInput],
    spec: FactorSpec,
    alias: str = "ret",
) -> Any:
    return evaluate_factor(spec, {alias: inputs[T.INPUT_DAILY_TOTAL_RETURN]})


def _wide_panel(result: Any) -> pd.DataFrame:
    panel = result.evaluation.panel
    if len(panel) == 0:
        return pd.DataFrame()
    return (
        panel.pivot(index="date", columns="stock_id", values="value")
        .sort_index()
        .sort_index(axis=1)
    )


def _tri_store(tmp_path: Path, name: str) -> tuple[_Store, str]:
    """A store whose single key has one break followed by a later segment."""
    store = _Store(tmp_path, name)
    key = store.key("AAA", listed_start=date(2019, 1, 2))
    store.obs("AAA", date(2020, 1, 2), 10.0)
    store.obs("AAA", date(2020, 1, 3), 11.0)
    store.obs("AAA", date(2020, 1, 6), 12.1)
    store.obs("AAA", date(2020, 1, 7), 13.31)
    # 2020-01-08 missing -> break
    store.obs("AAA", date(2020, 1, 9), 14.0)
    store.obs("AAA", date(2020, 1, 10), 15.4)
    store.obs("AAA", date(2020, 1, 13), 16.94)
    return store, key


TRI_A = date(2020, 1, 2)   # anchor
TRI_B = date(2020, 1, 8)   # break
TRI_C = date(2020, 1, 9)   # later-segment anchor
TRI_LAST = date(2020, 1, 13)


def _tri_store_boundary(tmp_path: Path, name: str) -> tuple[_Store, str]:
    """A TRI fixture whose break is within 400 sessions of the grid end.

    With the break this close to the grid end, no row can use a 400-session
    nested lag to reach back into segment 1, so the nested-lag lookback is
    NaN at every row after the break.
    """
    store = _Store(tmp_path, name)
    key = store.key("AAA", listed_start=date(2019, 1, 2))
    store.obs("AAA", date(2023, 6, 5), 10.0)
    store.obs("AAA", date(2023, 6, 6), 11.0)
    store.obs("AAA", date(2023, 6, 7), 12.1)
    store.obs("AAA", date(2023, 6, 8), 13.31)
    # 2023-06-09 missing -> break
    store.obs("AAA", date(2023, 6, 12), 14.0)
    store.obs("AAA", date(2023, 6, 13), 15.4)
    return store, key


# ===========================================================================
# 1. Timing and gate 13
# ===========================================================================


def test_item01_row_holds_prev_session_primitive(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    key = store.key("AAA", listed_start=date(2019, 1, 2))
    store.obs("AAA", date(2020, 1, 2), 10.0)
    store.obs("AAA", date(2020, 1, 3), 11.0)
    store.obs("AAA", date(2020, 1, 6), 12.1, volume=250)
    inputs = _load(store)
    # daily return dated 2020-01-03 appears at row 2020-01-06
    assert _value(inputs, T.INPUT_DAILY_TOTAL_RETURN, key, date(2020, 1, 6)) == pytest.approx(0.1)
    # dollar volume dated 2020-01-06 appears at row 2020-01-07
    assert _value(inputs, T.INPUT_DOLLAR_VOLUME, key, date(2020, 1, 7)) == pytest.approx(12.1 * 250)
    # TRI anchor 2020-01-02 = 1.0 appears at row 2020-01-03
    assert _value(inputs, T.INPUT_TOTAL_RETURN_INDEX, key, date(2020, 1, 3)) == pytest.approx(1.0)


def test_item02_lag_applied_exactly_once(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    key = store.key("AAA", listed_start=date(2019, 1, 2))
    for d, c in ((date(2020, 1, 2), 10.0), (date(2020, 1, 3), 11.0), (date(2020, 1, 6), 12.1)):
        store.obs("AAA", d, c)
    content = _build(store)
    long = _read_long(store, content, T.INPUT_DAILY_TOTAL_RETURN)
    rows = long[long["security_key"] == key]
    s = date(2020, 1, 3)
    # return dated s appears exactly at row next(s)
    hit = rows[pd.to_datetime(rows["source_session"]).dt.date == s]
    assert sorted(pd.to_datetime(hit["row_date"]).dt.date.tolist()) == [_next(s)]
    # and never at row s or next(next(s))
    assert not ((pd.to_datetime(rows["row_date"]).dt.date == s) & (pd.to_datetime(rows["source_session"]).dt.date == s)).any()
    nn = _next(_next(s))
    assert not ((pd.to_datetime(rows["row_date"]).dt.date == nn) & (pd.to_datetime(rows["source_session"]).dt.date == s)).any()


def test_item03_friday_to_monday(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    key = store.key("AAA", listed_start=date(2019, 1, 2))
    store.obs("AAA", date(2020, 1, 2), 10.0)
    store.obs("AAA", date(2020, 1, 3), 11.0)
    store.obs("AAA", date(2020, 1, 6), 12.1)
    content = _build(store)
    long = _read_long(store, content, T.INPUT_DAILY_TOTAL_RETURN)
    row = long[(long["security_key"] == key) & (pd.to_datetime(long["row_date"]).dt.date == date(2020, 1, 6))]
    assert len(row) == 1
    assert pd.Timestamp(row.iloc[0]["source_session"]).date() == date(2020, 1, 3)


def test_item04_nyse_holiday_row_holds_pre_holiday_session(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    key = store.key("AAA", listed_start=date(2015, 1, 2))
    store.obs("AAA", date(2016, 6, 30), 10.0)
    store.obs("AAA", date(2016, 7, 1), 11.0)
    store.obs("AAA", date(2016, 7, 5), 12.1)
    content = _build(store)
    long = _read_long(store, content, T.INPUT_DAILY_TOTAL_RETURN)
    row = long[(long["security_key"] == key) & (pd.to_datetime(long["row_date"]).dt.date == date(2016, 7, 5))]
    assert len(row) == 1
    assert pd.Timestamp(row.iloc[0]["source_session"]).date() == date(2016, 7, 1)


def test_item05_month_boundary(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    key = store.key("AAA", listed_start=date(2015, 1, 2))
    store.obs("AAA", date(2019, 1, 30), 10.0)
    store.obs("AAA", date(2019, 1, 31), 11.0)
    store.obs("AAA", date(2019, 2, 1), 12.1)
    content = _build(store)
    long = _read_long(store, content, T.INPUT_DAILY_TOTAL_RETURN)
    row = long[(long["security_key"] == key) & (pd.to_datetime(long["row_date"]).dt.date == date(2019, 2, 1))]
    assert len(row) == 1
    assert pd.Timestamp(row.iloc[0]["source_session"]).date() == date(2019, 1, 31)


def test_item06_year_boundary(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    key = store.key("AAA", listed_start=date(2015, 1, 2))
    store.obs("AAA", date(2019, 12, 30), 10.0)
    store.obs("AAA", date(2019, 12, 31), 11.0)
    store.obs("AAA", date(2020, 1, 2), 12.1)
    content = _build(store)
    long = _read_long(store, content, T.INPUT_DAILY_TOTAL_RETURN)
    row = long[(long["security_key"] == key) & (pd.to_datetime(long["row_date"]).dt.date == date(2020, 1, 2))]
    assert len(row) == 1
    assert pd.Timestamp(row.iloc[0]["source_session"]).date() == date(2019, 12, 31)


def test_item07_first_grid_row_is_nan(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    key = store.key("AAA", listed_start=date(2015, 1, 2))
    store.add_sessions("AAA", date(2015, 1, 2), date(2015, 2, 1))
    inputs = _load(store)
    first = SESSIONS[0]
    for name in T.INPUT_NAMES:
        assert _row_is_nan(inputs, name, key, first)


def test_item08_last_row_holds_prev_day_and_no_reserve_read(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    key = store.key("AAA", listed_start=date(2015, 1, 2))
    closes = {d: 10.0 + i for i, d in enumerate(_sessions_between(date(2023, 6, 1), date(2023, 6, 29)))}
    store.add_closes("AAA", closes)
    inputs = _load(store)
    expected = closes[date(2023, 6, 29)] / closes[date(2023, 6, 28)] - 1.0
    assert _value(inputs, T.INPUT_DAILY_TOTAL_RETURN, key, date(2023, 6, 30)) == pytest.approx(expected)
    for name in T.INPUT_NAMES:
        frame = inputs[name].values
        assert frame.index.max() == pd.Timestamp(date(2023, 6, 30))


def test_item09_future_data_invariance_property(tmp_path: Path) -> None:
    def make(name: str, mutate: bool, cutoff: date) -> _Store:
        s = _Store(tmp_path, name)
        s.key("AAA", listed_start=date(2019, 6, 3))
        closes = {}
        for i, d in enumerate(_sessions_between(date(2019, 6, 3), date(2019, 12, 31))):
            closes[d] = 10.0 + i
        if mutate:
            for d in list(closes):
                if d >= cutoff:
                    closes[d] = closes[d] * 3.0 + 7.0
        s.add_closes("AAA", closes)
        return s

    for cutoff in (date(2019, 9, 30), date(2019, 12, 31), date(2023, 6, 30)):
        full = _load(make("full", False, cutoff))
        changed = _load(make("changed", True, cutoff))
        for name in T.INPUT_NAMES:
            a = full[name].values
            b = changed[name].values
            mask = a.index <= pd.Timestamp(cutoff)
            pd.testing.assert_frame_equal(a.loc[mask], b.loc[mask])


def test_item10_changing_prev_primitive_changes_row_cell(tmp_path: Path) -> None:
    f = date(2020, 6, 30)
    prev = _prev(f)
    assert prev is not None

    def make(name: str, close_at_prev: float) -> _Store:
        s = _Store(tmp_path, name)
        s.key("AAA", listed_start=date(2019, 1, 2))
        s.obs("AAA", _prev(prev), 10.0)
        s.obs("AAA", prev, close_at_prev)
        s.obs("AAA", f, 20.0)
        return s

    a = _load(make("a", 11.0))
    b = _load(make("b", 12.0))
    key = "TIINGO:AAA:2019-01-02"
    va = _value(a, T.INPUT_DAILY_TOTAL_RETURN, key, f)
    vb = _value(b, T.INPUT_DAILY_TOTAL_RETURN, key, f)
    assert va == pytest.approx(11.0 / 10.0 - 1.0)
    assert vb == pytest.approx(12.0 / 10.0 - 1.0)
    assert va != vb


def test_item11_real_phase6_path_invariance(tmp_path: Path) -> None:
    def make(name: str, cutoff: date) -> _Store:
        s = _Store(tmp_path, name)
        s.key("AAA", listed_start=date(2019, 6, 3))
        closes = {}
        for i, d in enumerate(_sessions_between(date(2019, 6, 3), date(2019, 12, 31))):
            closes[d] = 10.0 + (i % 5) + (0.5 if d >= cutoff else 0.0)
        s.add_closes("AAA", closes)
        return s

    f = date(2019, 9, 30)
    full = _load(make("full", date(2099, 1, 1)))
    changed = _load(make("changed", f))
    specs = [
        _dag_spec("mean(ret, 3) + lag(ret, 1)"),
        _dag_spec("std(ret, 3) + max(ret, 2)"),
        _dag_spec("rank(ret) + standardize(ret)"),
    ]
    for spec in specs:
        ea = _eval(full, spec)
        eb = _eval(changed, spec)
        va = _wide_panel(ea)
        vb = _wide_panel(eb)
        mask = va.index <= pd.Timestamp(f)
        pd.testing.assert_frame_equal(va.loc[mask], vb.loc[mask])


def test_item12_source_session_equals_prev_row_date(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.key("AAA", listed_start=date(2019, 1, 2))
    store.add_sessions("AAA", date(2019, 6, 3), date(2019, 7, 1))
    content = _build(store)
    for name in T.INPUT_NAMES:
        long = _read_long(store, content, name)
        for row_date, source in zip(long["row_date"], long["source_session"]):
            d = pd.Timestamp(row_date).date()
            s = pd.Timestamp(source).date()
            assert s == _prev(d)


def test_item13_previous_calendar_day_semantics_rejected(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    key = store.key("AAA", listed_start=date(2019, 1, 2))
    store.obs("AAA", date(2020, 1, 2), 10.0)
    store.obs("AAA", date(2020, 1, 3), 11.0)
    store.obs("AAA", date(2020, 1, 6), 12.1)
    inputs = _load(store)
    monday = date(2020, 1, 6)
    sunday = date(2020, 1, 5)
    # The trusted-session lag uses Friday's observation.
    assert _value(inputs, T.INPUT_DAILY_TOTAL_RETURN, key, monday) == pytest.approx(0.1)
    # A naive previous-calendar-day fixture would have yielded Sunday's
    # (absent) primitive, so the result differs from the frozen value.
    naive = 10.0 / 11.0 - 1.0  # deliberately not the frozen value
    assert _value(inputs, T.INPUT_DAILY_TOTAL_RETURN, key, monday) != pytest.approx(naive)
    assert sunday not in POS


# ===========================================================================
# 14-26. Primitives and QC
# ===========================================================================


def _one_return(tmp_path: Path, name: str, *, factor: float = 1.0, action_type: str = "dividend") -> tuple[_Store, str]:
    store = _Store(tmp_path, name)
    key = store.key("AAA", listed_start=date(2019, 1, 2))
    store.obs("AAA", date(2020, 1, 2), 100.0)
    store.obs("AAA", date(2020, 1, 3), 110.0)
    store.obs("AAA", date(2020, 1, 6), 121.0)
    if factor != 1.0:
        store.ca("AAA", date(2020, 1, 3), factor=factor, action_type=action_type)
    return store, key


def test_item14_daily_total_return_reference(tmp_path: Path) -> None:
    div, key = _one_return(tmp_path, "div", factor=1.1)
    inputs = _load(div)
    # raw 110/100-1 = 0.10; adjusted (1.10)*1.1-1 = 0.21
    assert _value(inputs, T.INPUT_DAILY_TOTAL_RETURN, key, date(2020, 1, 6)) == pytest.approx(0.21)

    split, key2 = _one_return(tmp_path, "split", factor=2.0, action_type="split")
    inputs2 = _load(split)
    assert _value(inputs2, T.INPUT_DAILY_TOTAL_RETURN, key2, date(2020, 1, 6)) == pytest.approx(1.10 * 2.0 - 1.0)


def test_item15_corporate_action_preconditions_fail_closed(tmp_path: Path) -> None:
    # non-session event
    s = _Store(tmp_path, "nonsession")
    s.key("AAA", listed_start=date(2019, 1, 2))
    s.obs("AAA", date(2020, 1, 2), 100.0)
    s.obs("AAA", date(2020, 1, 3), 110.0)
    s.ca("AAA", date(2020, 1, 4), factor=1.1)  # Saturday
    with pytest.raises(T.D7cConsistencyError):
        _build(s)

    # superseded
    s = _Store(tmp_path, "superseded")
    s.key("AAA", listed_start=date(2019, 1, 2))
    s.obs("AAA", date(2020, 1, 2), 100.0)
    s.obs("AAA", date(2020, 1, 3), 110.0)
    s.ca("AAA", date(2020, 1, 3), is_superseded=True)
    with pytest.raises(T.D7cConsistencyError):
        _build(s)

    # knowledge date mismatch
    s = _Store(tmp_path, "kdmismatch")
    s.key("AAA", listed_start=date(2019, 1, 2))
    s.obs("AAA", date(2020, 1, 2), 100.0)
    s.obs("AAA", date(2020, 1, 3), 110.0)
    s.ca("AAA", date(2020, 1, 3), knowledge_date=date(2020, 1, 2))
    with pytest.raises(T.D7cConsistencyError):
        _build(s)

    # factor <= 0
    s = _Store(tmp_path, "badfactor")
    s.key("AAA", listed_start=date(2019, 1, 2))
    s.obs("AAA", date(2020, 1, 2), 100.0)
    s.obs("AAA", date(2020, 1, 3), 110.0)
    s.ca("AAA", date(2020, 1, 3), factor=0.0)
    with pytest.raises(T.D7cConsistencyError):
        _build(s)


def test_item16_nonfinite_return_raises(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.key("AAA", listed_start=date(2019, 1, 2))
    store.obs("AAA", date(2020, 1, 2), 100.0)
    store.obs("AAA", date(2020, 1, 3), float("inf"))
    with pytest.raises(T.D7cConsistencyError):
        _build(store)


def test_item17_corrupt_row_nan(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    key = store.key("AAA", listed_start=date(2019, 1, 2))
    store.obs("AAA", date(2020, 1, 2), 100.0)
    store.obs("AAA", date(2020, 1, 3), 110.0, flag="NONPOSITIVE_PRICE")
    store.obs("AAA", date(2020, 1, 6), 121.0)
    inputs = _load(store)
    assert _row_is_nan(inputs, T.INPUT_DAILY_TOTAL_RETURN, key, date(2020, 1, 6))
    assert _row_is_nan(inputs, T.INPUT_DAILY_TOTAL_RETURN, key, date(2020, 1, 7))


def test_item18_interval_mismatch_nan(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    key = store.key("AAA", listed_start=date(2019, 1, 2))
    store.obs("AAA", date(2020, 1, 2), 100.0)
    store.obs("AAA", date(2020, 1, 3), 110.0, flag="INTERVAL_MISMATCH")
    store.obs("AAA", date(2020, 1, 6), 121.0)
    inputs = _load(store)
    assert _row_is_nan(inputs, T.INPUT_DAILY_TOTAL_RETURN, key, date(2020, 1, 6))
    assert _row_is_nan(inputs, T.INPUT_DAILY_TOTAL_RETURN, key, date(2020, 1, 7))


def test_item19_vendor_null_nan(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    key = store.key("AAA", listed_start=date(2019, 1, 2))
    store.obs("AAA", date(2020, 1, 2), 100.0)
    store.obs("AAA", date(2020, 1, 3), 110.0, flag="VENDOR_NULL")
    store.obs("AAA", date(2020, 1, 6), 121.0)
    inputs = _load(store)
    assert _row_is_nan(inputs, T.INPUT_DAILY_TOTAL_RETURN, key, date(2020, 1, 6))
    assert _row_is_nan(inputs, T.INPUT_DOLLAR_VOLUME, key, date(2020, 1, 6))
    assert _row_is_nan(inputs, T.INPUT_DAILY_TOTAL_RETURN, key, date(2020, 1, 7))


def test_item20_missing_row_nan_nothing_filled(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    key = store.key("AAA", listed_start=date(2019, 1, 2))
    store.obs("AAA", date(2020, 1, 2), 100.0)
    # 2020-01-03 missing
    store.obs("AAA", date(2020, 1, 6), 121.0)
    store.obs("AAA", date(2020, 1, 7), 130.0)
    inputs = _load(store)
    assert _row_is_nan(inputs, T.INPUT_DAILY_TOTAL_RETURN, key, date(2020, 1, 6))
    assert _value(inputs, T.INPUT_DAILY_TOTAL_RETURN, key, date(2020, 1, 8)) == pytest.approx(130.0 / 121.0 - 1.0)


def test_item21_ca_suspect_row_is_usable(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    key = store.key("AAA", listed_start=date(2019, 1, 2))
    store.obs("AAA", date(2020, 1, 2), 100.0)
    store.obs("AAA", date(2020, 1, 3), 110.0, flag="CORP_ACTION_SUSPECT")
    store.obs("AAA", date(2020, 1, 6), 121.0)
    inputs = _load(store)
    assert _value(inputs, T.INPUT_DAILY_TOTAL_RETURN, key, date(2020, 1, 6)) == pytest.approx(0.1)


def test_item22_zero_volume_is_zero(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    key = store.key("AAA", listed_start=date(2019, 1, 2))
    store.obs("AAA", date(2020, 1, 2), 100.0, volume=0)
    store.obs("AAA", date(2020, 1, 3), 110.0, volume=0)
    inputs = _load(store)
    assert _value(inputs, T.INPUT_DOLLAR_VOLUME, key, date(2020, 1, 3)) == 0.0


def test_item23_null_volume_vendor_null_unusable(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    key = store.key("AAA", listed_start=date(2019, 1, 2))
    store.obs("AAA", date(2020, 1, 2), 100.0)
    store.obs("AAA", date(2020, 1, 3), 110.0, volume=None, flag="VENDOR_NULL")
    store.obs("AAA", date(2020, 1, 6), 121.0)
    inputs = _load(store)
    assert _row_is_nan(inputs, T.INPUT_DOLLAR_VOLUME, key, date(2020, 1, 6))
    assert _row_is_nan(inputs, T.INPUT_DAILY_TOTAL_RETURN, key, date(2020, 1, 6))
    assert _row_is_nan(inputs, T.INPUT_DAILY_TOTAL_RETURN, key, date(2020, 1, 7))


def test_item24_duplicate_usable_rows_raise(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.key("AAA", listed_start=date(2019, 1, 2))
    store.obs("AAA", date(2020, 1, 2), 100.0)
    store.obs("AAA", date(2020, 1, 3), 110.0)
    store.obs("AAA", date(2020, 1, 3), 111.0)  # duplicate usable row
    with pytest.raises(T.D7cConsistencyError):
        _build(store)


def test_item25_off_calendar_row_never_a_session(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    key = store.key("AAA", listed_start=date(2019, 1, 2))
    store.obs("AAA", date(2020, 1, 2), 100.0)
    store.obs("AAA", date(2020, 1, 4), 105.0, flag="OFF_CALENDAR_ROW")  # Saturday
    store.obs("AAA", date(2020, 1, 6), 121.0)
    content = _build(store)
    long = _read_long(store, content, T.INPUT_DAILY_TOTAL_RETURN)
    assert date(2020, 1, 4) not in set(pd.to_datetime(long["row_date"]).dt.date)
    assert date(2020, 1, 4) not in set(pd.to_datetime(long["source_session"]).dt.date)


def test_item26_adjusted_fields_never_read(tmp_path: Path) -> None:
    def make(name: str, adj: float) -> _Store:
        s = _Store(tmp_path, name)
        s.key("AAA", listed_start=date(2019, 1, 2))
        s.obs("AAA", date(2020, 1, 2), 100.0)
        s.obs("AAA", date(2020, 1, 3), 110.0)
        s.obs("AAA", date(2020, 1, 6), 121.0)
        s.adj_close = adj
        return s

    a = make("a", 1.0)
    b = make("b", 999.0)
    ca = _build(a)
    cb = _build(b)
    for name in T.INPUT_NAMES:
        la = _read_long(a, ca, name)
        lb = _read_long(b, cb, name)
        pd.testing.assert_frame_equal(la, lb)


# ===========================================================================
# 27-30. Identity and population
# ===========================================================================


def test_item27_only_unique_interval_assigned_keys_are_columns(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.identity_row("AAA", security_key="K1")
    store.identity_row("BBB", security_key="K2", state="IDENTITY_AMBIGUOUS")
    store.identity_row("CCC", security_key="K3", state="HISTORY_UNREACHABLE")
    store.identity_row("DDD", security_key="K4")
    store.identity_row("DDD2", security_key="K4")
    content = _build(store)
    assert content["grid"]["columns"] == ["K1"]


def test_item28_row_outside_interval_not_used(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    key = store.key("AAA", listed_start=date(2020, 1, 6), listed_end=date(2020, 1, 10))
    store.class_seg(key, date(2015, 1, 2), "COMMON_STOCK")
    store.obs("AAA", date(2020, 1, 2), 100.0)
    store.obs("AAA", date(2020, 1, 3), 110.0)
    store.obs("AAA", date(2020, 1, 6), 121.0)
    store.obs("AAA", date(2020, 1, 7), 133.1)
    inputs = _load(store)
    # 2020-01-06's return needs 2020-01-03, which is outside the interval.
    assert _row_is_nan(inputs, T.INPUT_DAILY_TOTAL_RETURN, key, date(2020, 1, 6))
    assert _value(inputs, T.INPUT_DAILY_TOTAL_RETURN, key, date(2020, 1, 8)) == pytest.approx(0.1)


def test_item29_reused_ticker_keys_do_not_stitch(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    k1 = store.key("AAA", security_key="K1", listed_start=date(2019, 1, 2), listed_end=date(2020, 1, 3))
    k2 = store.key("AAA", security_key="K2", listed_start=date(2020, 1, 6), listed_end=FAR)
    store.obs("AAA", date(2020, 1, 2), 100.0)
    store.obs("AAA", date(2020, 1, 3), 110.0)
    store.obs("AAA", date(2020, 1, 6), 121.0)
    store.obs("AAA", date(2020, 1, 7), 133.1)
    inputs = _load(store)
    # K1 ends at 2020-01-03; it never sees K2's later rows.
    assert _row_is_nan(inputs, T.INPUT_DAILY_TOTAL_RETURN, k1, date(2020, 1, 7))
    # K2 cannot use K1's pre-start row as prev for 2020-01-06.
    assert _row_is_nan(inputs, T.INPUT_DAILY_TOTAL_RETURN, k2, date(2020, 1, 6))
    assert _value(inputs, T.INPUT_DAILY_TOTAL_RETURN, k2, date(2020, 1, 8)) == pytest.approx(0.1)


def test_item30_future_d6_membership_never_affects_columns(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.identity_row("AAA", security_key="K1")
    store.identity_row("BBB", security_key="K2")
    content = _build(store)
    assert content["grid"]["columns"] == ["K1", "K2"]
    stages = [s.stage for s in store.build().stages()]
    assert stages == ["D3", "D4", "D5", "DSC"]


# ===========================================================================
# 31-38. P1c mask
# ===========================================================================


def _mask_store(tmp_path: Path, name: str) -> tuple[_Store, str]:
    store = _Store(tmp_path, name)
    key = store.key("AAA", listed_start=date(2019, 1, 2))
    store.obs("AAA", date(2020, 1, 2), 100.0)
    store.obs("AAA", date(2020, 1, 3), 110.0)
    store.obs("AAA", date(2020, 1, 6), 121.0)
    return store, key


def test_item31_non_common_at_prev_is_nan(tmp_path: Path) -> None:
    store, key = _mask_store(tmp_path, "s")
    store.class_seg(key, date(2019, 1, 2), "COMMON_STOCK")
    store.class_seg(key, date(2020, 1, 3), "NON_COMMON")
    inputs = _load(store)
    # row 2020-01-06 uses cutoff prev = 2020-01-03 -> NON_COMMON -> NaN
    assert _row_is_nan(inputs, T.INPUT_DAILY_TOTAL_RETURN, key, date(2020, 1, 6))


def test_item32_policy_excluded_and_unresolved_nan(tmp_path: Path) -> None:
    store, key = _mask_store(tmp_path, "s")
    store.policy_seg(key, date(2020, 1, 3), T.POLICY_EXCLUDED)
    inputs = _load(store)
    assert _row_is_nan(inputs, T.INPUT_DAILY_TOTAL_RETURN, key, date(2020, 1, 6))

    store2, key2 = _mask_store(tmp_path, "s2")
    store2.policy_seg(key2, date(2020, 1, 3), T.POLICY_ISSUER_STATUS_UNRESOLVED)
    inputs2 = _load(store2)
    assert _row_is_nan(inputs2, T.INPUT_DAILY_TOTAL_RETURN, key2, date(2020, 1, 6))


def test_item33_common_and_no_exclusion_admitted(tmp_path: Path) -> None:
    store, key = _mask_store(tmp_path, "s")
    inputs = _load(store)
    assert _value(inputs, T.INPUT_DAILY_TOTAL_RETURN, key, date(2020, 1, 6)) == pytest.approx(0.1)


def test_item34_cutoff_is_prev_row_not_row(tmp_path: Path) -> None:
    store, key = _mask_store(tmp_path, "s")
    store.class_seg(key, date(2019, 1, 2), "COMMON_STOCK")
    # A class transition dated exactly d = 2020-01-06 must not affect row d.
    store.class_seg(key, date(2020, 1, 6), "NON_COMMON")
    inputs = _load(store)
    assert _value(inputs, T.INPUT_DAILY_TOTAL_RETURN, key, date(2020, 1, 6)) == pytest.approx(0.1)
    assert _row_is_nan(inputs, T.INPUT_DAILY_TOTAL_RETURN, key, date(2020, 1, 7))

    # G1: the same must hold for the policy cutoff.  A policy transition dated
    # exactly d = 2020-01-06 must leave row d admitted and only mask next(d).
    store2, key2 = _mask_store(tmp_path, "s2")
    store2.policy_seg(key2, date(2020, 1, 6), T.POLICY_EXCLUDED)
    inputs2 = _load(store2)
    assert _value(inputs2, T.INPUT_DAILY_TOTAL_RETURN, key2, date(2020, 1, 6)) == pytest.approx(0.1)
    assert _row_is_nan(inputs2, T.INPUT_DAILY_TOTAL_RETURN, key2, date(2020, 1, 7))


def test_item35_future_transition_leaves_earlier_rows_unchanged(tmp_path: Path) -> None:
    store, key = _mask_store(tmp_path, "s")
    store.class_seg(key, date(2019, 1, 2), "COMMON_STOCK")
    base = _load(store)
    store2, key2 = _mask_store(tmp_path, "s2")
    store2.class_seg(key2, date(2019, 1, 2), "COMMON_STOCK")
    store2.class_seg(key2, date(2020, 1, 7), "NON_COMMON")
    later = _load(store2)
    assert _row_is_nan(base, T.INPUT_DAILY_TOTAL_RETURN, key, date(2020, 1, 6)) is False
    assert _value(base, T.INPUT_DAILY_TOTAL_RETURN, key, date(2020, 1, 6)) == _value(
        later, T.INPUT_DAILY_TOTAL_RETURN, key2, date(2020, 1, 6)
    )


def test_item36_later_common_security_leaves_earlier_factor_unchanged(tmp_path: Path) -> None:
    def make(name: str, extra: bool) -> Mapping[str, TrustedInput]:
        s = _Store(tmp_path, name)
        s.key("AAA", security_key="K1", listed_start=date(2019, 1, 2))
        s.obs("AAA", date(2020, 1, 2), 100.0)
        s.obs("AAA", date(2020, 1, 3), 110.0)
        s.obs("AAA", date(2020, 1, 6), 121.0)
        if extra:
            s.key("BBB", security_key="K2", listed_start=date(2019, 1, 2))
            s.class_seg("K2", date(2020, 1, 7), "COMMON_STOCK")
            s.obs("BBB", date(2020, 1, 2), 50.0)
            s.obs("BBB", date(2020, 1, 3), 55.0)
            s.obs("BBB", date(2020, 1, 6), 60.5)
        return _load(s)

    a = make("a", False)
    b = make("b", True)
    spec = _dag_spec("rank(ret)")
    ea = _eval(a, spec)
    eb = _eval(b, spec)
    va = _wide_panel(ea)
    vb = _wide_panel(eb)
    early = va.index <= pd.Timestamp(date(2020, 1, 6))
    # The later-COMMON column is all-NaN over the early rows, so it cannot
    # change the cross-sectional rank of the eligible column.
    pd.testing.assert_series_equal(va["K1"].loc[early], vb["K1"].loc[early])


def test_item37_ineligible_nan_column_does_not_affect_rank_winsorize_standardize(tmp_path: Path) -> None:
    def make(name: str, extra: bool) -> Mapping[str, TrustedInput]:
        s = _Store(tmp_path, name)
        for i, (ticker, k) in enumerate(((("AAA", "K1")), ("BBB", "K2"))):
            s.key(ticker, security_key=k, listed_start=date(2019, 1, 2))
            s.obs(ticker, date(2020, 1, 2), 100.0 + i)
            s.obs(ticker, date(2020, 1, 3), 110.0 + i)
            s.obs(ticker, date(2020, 1, 6), 121.0 + i)
        if extra:
            s.key("CCC", security_key="K3", listed_start=date(2019, 1, 2))
            s.class_seg("K3", date(2020, 1, 7), "COMMON_STOCK")
            s.obs("CCC", date(2020, 1, 2), 7.0)
            s.obs("CCC", date(2020, 1, 3), 8.0)
            s.obs("CCC", date(2020, 1, 6), 9.0)
        return _load(s)

    a = make("a", False)
    b = make("b", True)
    for expression in ("rank(ret)", "winsorize(ret, 0.25, 0.75)", "standardize(ret)"):
        ea = _eval(a, _dag_spec(expression))
        eb = _eval(b, _dag_spec(expression))
        va = _wide_panel(ea)
        vb = _wide_panel(eb)
        for key in ("K1", "K2"):
            pd.testing.assert_series_equal(va[key], vb[key])


def test_item38_no_final_class_or_ever_common_mask(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    key = store.key("AAA", listed_start=date(2019, 1, 2))
    store.class_seg(key, date(2020, 1, 7), "COMMON_STOCK")
    store.obs("AAA", date(2020, 1, 2), 100.0)
    store.obs("AAA", date(2020, 1, 3), 110.0)
    store.obs("AAA", date(2020, 1, 6), 121.0)
    store.obs("AAA", date(2020, 1, 7), 133.1)
    inputs = _load(store)
    # before it becomes COMMON -> NaN even though it is COMMON later
    assert _row_is_nan(inputs, T.INPUT_DAILY_TOTAL_RETURN, key, date(2020, 1, 6))
    assert _value(inputs, T.INPUT_DAILY_TOTAL_RETURN, key, date(2020, 1, 8)) == pytest.approx(0.1)


# ===========================================================================
# 39-49. total_return_index
# ===========================================================================


def test_item39_anchor_is_one_and_recursion_matches_products(tmp_path: Path) -> None:
    store, key = _tri_store(tmp_path, "s")
    inputs = _load(store)
    assert _value(inputs, T.INPUT_TOTAL_RETURN_INDEX, key, date(2020, 1, 3)) == pytest.approx(1.0)
    assert _value(inputs, T.INPUT_TOTAL_RETURN_INDEX, key, date(2020, 1, 6)) == pytest.approx(1.1)
    assert _value(inputs, T.INPUT_TOTAL_RETURN_INDEX, key, date(2020, 1, 7)) == pytest.approx(1.1 ** 2)
    assert _value(inputs, T.INPUT_TOTAL_RETURN_INDEX, key, date(2020, 1, 8)) == pytest.approx(1.1 ** 3)


def test_item40_break_session_and_later_unlagged_nan(tmp_path: Path) -> None:
    store, key = _tri_store(tmp_path, "s")
    inputs = _load(store)
    # unlagged TRI(b) appears at row next(b)
    assert _row_is_nan(inputs, T.INPUT_TOTAL_RETURN_INDEX, key, _next(TRI_B))
    for d in (date(2020, 1, 10), TRI_LAST, date(2020, 1, 14)):
        assert _row_is_nan(inputs, T.INPUT_TOTAL_RETURN_INDEX, key, d)
    # G2 INDEPENDENCE: the TRI suppression applies only to total_return_index.
    # daily_total_return and dollar_volume follow their own validity rules and
    # must be non-NaN again after the gap when independently valid and admitted.
    assert _value(inputs, T.INPUT_DAILY_TOTAL_RETURN, key, date(2020, 1, 13)) == pytest.approx(15.4 / 14.0 - 1.0)
    assert _value(inputs, T.INPUT_DOLLAR_VOLUME, key, date(2020, 1, 13)) == pytest.approx(15.4 * 100.0)
    assert _value(inputs, T.INPUT_DAILY_TOTAL_RETURN, key, date(2020, 1, 14)) == pytest.approx(16.94 / 15.4 - 1.0)
    assert _value(inputs, T.INPUT_DOLLAR_VOLUME, key, date(2020, 1, 14)) == pytest.approx(16.94 * 100.0)
    assert _row_is_nan(inputs, T.INPUT_TOTAL_RETURN_INDEX, key, date(2020, 1, 13))
    assert _row_is_nan(inputs, T.INPUT_TOTAL_RETURN_INDEX, key, date(2020, 1, 14))


def test_item41_row_b_carries_tri_b_minus_1(tmp_path: Path) -> None:
    store, key = _tri_store(tmp_path, "s")
    inputs = _load(store)
    assert _value(inputs, T.INPUT_TOTAL_RETURN_INDEX, key, TRI_B) == pytest.approx(1.1 ** 3)
    assert _row_is_nan(inputs, T.INPUT_TOTAL_RETURN_INDEX, key, _next(TRI_B))


def test_item42_first_valid_return_after_gap_stays_nan(tmp_path: Path) -> None:
    store, key = _tri_store(tmp_path, "s")
    inputs = _load(store)
    # r(2020-01-10) is VALID but TRI is suppressed.
    assert _value(inputs, T.INPUT_DAILY_TOTAL_RETURN, key, date(2020, 1, 13)) == pytest.approx(0.1)
    assert _row_is_nan(inputs, T.INPUT_TOTAL_RETURN_INDEX, key, date(2020, 1, 13))


def test_item43_ratio_across_break_nan_for_all_n(tmp_path: Path) -> None:
    store, key = _tri_store(tmp_path, "s")
    inputs = _load(store)
    for n in (1, 2, 251, 252, 253, 1000):
        spec = _dag_spec(f"tri / lag(tri, {n})", alias="tri", semantic_id=T.INPUT_TOTAL_RETURN_INDEX,
                         observation_period=ObservationPeriod.INSTANT, units=Unit.RATIO)
        result = evaluate_factor(spec, {"tri": inputs[T.INPUT_TOTAL_RETURN_INDEX]})
        frame = _wide_panel(result)
        after = frame.loc[frame.index > pd.Timestamp(TRI_B), key]
        assert after.isna().all()


def test_item44_nested_lag_across_break_nan(tmp_path: Path) -> None:
    store, key = _tri_store(tmp_path, "s")
    inputs = _load(store)
    spec = _dag_spec("lag(lag(tri, 200), 200)", alias="tri", semantic_id=T.INPUT_TOTAL_RETURN_INDEX,
                     observation_period=ObservationPeriod.INSTANT, units=Unit.RATIO)
    frame = _wide_panel(
        evaluate_factor(spec, {"tri": inputs[T.INPUT_TOTAL_RETURN_INDEX]})
    )
    nested = frame[key]
    # `lag(lag(x,200),200)` is exactly `lag(x,400)`; an endpoint whose source
    # session is at or after b lies in the suppressed region, so the value is
    # NaN.  It also fails closed at the boundary rows b and next(b) and over
    # the 400-row warmup.
    for d in nested.index:
        i = POS[d.date()]
        if i - 400 >= 0 and SESSIONS[i - 400] > TRI_B:
            assert pd.isna(nested.loc[d])
    assert pd.isna(nested.loc[pd.Timestamp(TRI_B)])
    assert pd.isna(nested.loc[pd.Timestamp(_next(TRI_B))])
    assert nested.iloc[:400].isna().all()
    # A finite value after b reuses one segment-1 TRI cell exactly; it never
    # combines two different segments (the source cell is non-NaN).
    tri = inputs[T.INPUT_TOTAL_RETURN_INDEX].values[key]
    for d in nested.index:
        if d > pd.Timestamp(TRI_B) and not pd.isna(nested.loc[d]):
            i = POS[d.date()]
            assert not pd.isna(tri.loc[pd.Timestamp(SESSIONS[i - 400])])

    # The literal frozen wording ("across b is NaN") holds on a boundary
    # fixture whose break is within the nested lookback of the grid end, so no
    # post-break row can reach segment 1 at all.
    boundary_store, boundary_key = _tri_store_boundary(tmp_path, "boundary")
    boundary_inputs = _load(boundary_store)
    boundary = _wide_panel(
        evaluate_factor(spec, {"tri": boundary_inputs[T.INPUT_TOTAL_RETURN_INDEX]})
    )[boundary_key]
    cut = pd.Timestamp(date(2023, 6, 9))
    assert boundary.loc[boundary.index > cut].isna().all()
    assert pd.isna(boundary.loc[cut])
    assert pd.isna(boundary.loc[pd.Timestamp(date(2023, 6, 12))])


def test_item45_rolling_max_mean_across_break_nan(tmp_path: Path) -> None:
    store, key = _tri_store(tmp_path, "s")
    inputs = _load(store)
    for expression in ("max(tri, 252)", "mean(tri, 3)", "std(tri, 3)"):
        spec = _dag_spec(expression, alias="tri", semantic_id=T.INPUT_TOTAL_RETURN_INDEX,
                         observation_period=ObservationPeriod.INSTANT, units=Unit.RATIO)
        frame = _wide_panel(
            evaluate_factor(spec, {"tri": inputs[T.INPUT_TOTAL_RETURN_INDEX]})
        )
        after = frame.loc[frame.index > pd.Timestamp(TRI_B), key]
        assert after.isna().all()


def test_item46_within_segment_ratio_equals_compounded_return(tmp_path: Path) -> None:
    store, key = _tri_store(tmp_path, "s")
    inputs = _load(store)
    tri = inputs[T.INPUT_TOTAL_RETURN_INDEX].values[key]
    r = inputs[T.INPUT_DAILY_TOTAL_RETURN].values[key]
    # TRI(2020-01-03)=1.0, TRI(2020-01-06)=1.1, TRI(2020-01-07)=1.21
    assert tri.loc[pd.Timestamp(date(2020, 1, 6))] / tri.loc[pd.Timestamp(date(2020, 1, 3))] == pytest.approx(
        1.0 + r.loc[pd.Timestamp(date(2020, 1, 6))]
    )
    assert tri.loc[pd.Timestamp(date(2020, 1, 7))] / tri.loc[pd.Timestamp(date(2020, 1, 3))] == pytest.approx(
        (1.0 + r.loc[pd.Timestamp(date(2020, 1, 6))]) * (1.0 + r.loc[pd.Timestamp(date(2020, 1, 7))])
    )


def test_item47_identity_boundary_breaks_segment(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    key = store.key("AAA", listed_start=date(2019, 1, 2), listed_end=date(2020, 1, 6))
    store.obs("AAA", date(2020, 1, 2), 10.0)
    store.obs("AAA", date(2020, 1, 3), 11.0)
    store.obs("AAA", date(2020, 1, 6), 12.1)
    # 2020-01-07 is outside the interval -> return invalid -> break
    store.obs("AAA", date(2020, 1, 7), 13.31)
    inputs = _load(store)
    assert _value(inputs, T.INPUT_TOTAL_RETURN_INDEX, key, date(2020, 1, 7)) == pytest.approx(1.21)
    assert _row_is_nan(inputs, T.INPUT_TOTAL_RETURN_INDEX, key, date(2020, 1, 8))


def test_item48_later_segments_recorded_distinct_and_not_admitted(tmp_path: Path) -> None:
    store, key = _tri_store(tmp_path, "s")
    content = _build(store)
    segments = pd.read_parquet(_artifact_dir(store, content) / T.SEGMENTS_FILE)
    rows = segments[segments["security_key"] == key].sort_values("segment_id")
    assert rows["segment_id"].tolist() == [1, 2]
    assert rows["admitted"].tolist() == [True, False]
    assert len(set(rows["segment_id"])) == 2


def test_item49_no_forward_fill_no_zero_substitution_no_carried_level(tmp_path: Path) -> None:
    store, key = _tri_store(tmp_path, "s")
    inputs = _load(store)
    frame = inputs[T.INPUT_TOTAL_RETURN_INDEX].values
    # The gap session's own row and the suppressed restart are NaN, and no
    # non-zero value is carried forward.
    assert _row_is_nan(inputs, T.INPUT_TOTAL_RETURN_INDEX, key, _next(TRI_B))
    assert _row_is_nan(inputs, T.INPUT_TOTAL_RETURN_INDEX, key, date(2020, 1, 10))
    assert not (frame[key].dropna() == 0.0).any()


# ===========================================================================
# 50-55. Delivery and Phase-6 contract
# ===========================================================================


def test_item50_exactly_three_trusted_inputs_on_one_grid(tmp_path: Path) -> None:
    store, key = _tri_store(tmp_path, "s")
    inputs = _load(store)
    assert set(inputs) == set(T.INPUT_NAMES)
    frames = [inputs[name].values for name in T.INPUT_NAMES]
    for frame in frames[1:]:
        assert frame.index.equals(frames[0].index)
        assert frame.columns.equals(frames[0].columns)


def test_item51_capabilities_exact_and_requirement_admitted(tmp_path: Path) -> None:
    store, key = _tri_store(tmp_path, "s")
    inputs = _load(store)
    history = inputs[T.INPUT_DAILY_TOTAL_RETURN].capability.history
    caps = {
        T.INPUT_DAILY_TOTAL_RETURN: (Frequency.DAILY, ObservationPeriod.PERIOD, Unit.FRACTION),
        T.INPUT_TOTAL_RETURN_INDEX: (Frequency.DAILY, ObservationPeriod.INSTANT, Unit.RATIO),
        T.INPUT_DOLLAR_VOLUME: (Frequency.DAILY, ObservationPeriod.PERIOD, Unit.CURRENCY),
    }
    for name, (freq, period, units) in caps.items():
        cap = inputs[name].capability
        assert cap.frequency == freq
        assert cap.observation_period == period
        assert cap.units == units
        assert cap.history == history
        assert cap.has_knowledge_date is True
        assert cap.has_positive_vintage_identity is False
        assert cap.revision_policies == frozenset({RevisionPolicy.POINT_IN_TIME})
    requirement = DataRequirement(
        semantic_id=T.INPUT_DAILY_TOTAL_RETURN,
        frequency=Frequency.DAILY,
        observation_period=ObservationPeriod.PERIOD,
        units=Unit.FRACTION,
    )
    assert require_satisfiable(requirement, inputs[T.INPUT_DAILY_TOTAL_RETURN].capability).satisfied


def test_item52_evidence_class_live_recorded_never_upgraded(tmp_path: Path) -> None:
    store, key = _tri_store(tmp_path, "s")
    inputs = _load(store)
    for name in T.INPUT_NAMES:
        assert inputs[name].evidence_class == EvidenceClass.LIVE_RECORDED
        assert inputs[name].vintage_evidence is None


def test_item53_phase6_grid_check(tmp_path: Path) -> None:
    store, key = _tri_store(tmp_path, "s")
    inputs = _load(store)
    spec = FactorSpec(
        id="two",
        description="two aliases",
        expression="ret + dv",
        inputs=(
            FactorInput("ret", DataRequirement(semantic_id=T.INPUT_DAILY_TOTAL_RETURN, frequency=Frequency.DAILY, observation_period=ObservationPeriod.PERIOD, units=Unit.FRACTION)),
            FactorInput("dv", DataRequirement(semantic_id=T.INPUT_DOLLAR_VOLUME, frequency=Frequency.DAILY, observation_period=ObservationPeriod.PERIOD, units=Unit.CURRENCY)),
        ),
        frequency=Frequency.DAILY,
        missing_policy=MissingPolicy.PROPAGATE,
    )
    good = {"ret": inputs[T.INPUT_DAILY_TOTAL_RETURN], "dv": inputs[T.INPUT_DOLLAR_VOLUME]}
    assert admit(spec, good).admitted
    bad_values = inputs[T.INPUT_DOLLAR_VOLUME].values.iloc[:-1]
    bad = TrustedInput(values=bad_values, capability=inputs[T.INPUT_DOLLAR_VOLUME].capability, evidence_class=EvidenceClass.LIVE_RECORDED)
    with pytest.raises(GridMismatchError):
        admit(spec, {"ret": inputs[T.INPUT_DAILY_TOTAL_RETURN], "dv": bad})


def test_item54_no_delisting_or_scenario_values(tmp_path: Path) -> None:
    store, key = _tri_store(tmp_path, "s")
    content = _build(store)
    segments = pd.read_parquet(_artifact_dir(store, content) / T.SEGMENTS_FILE)
    for name in T.INPUT_NAMES:
        long = _read_long(store, content, name)
        assert list(long.columns) == ["row_date", "security_key", "source_session", "value"]
    assert "tau" not in _canon(content["grid"]).lower()
    assert "t0" not in list(content["grid"]["columns"])
    assert not any(str(c).lower() in {"t0", "ts", "tau"} for c in segments.columns)


def test_item55_loader_reads_only_artifact(tmp_path: Path) -> None:
    store, key = _tri_store(tmp_path, "s")
    bindings = store.build()
    content = T.build_trusted_inputs(store.root, bindings=bindings, calendar=CAL)
    # Remove every raw source; the loader must still succeed.
    for rel in ("normalized", "derived/identity", "derived/qc", "derived/classification"):
        target = store.root / rel
        if target.exists():
            import shutil as _shutil

            _shutil.rmtree(target)
    inputs = T.load_trusted_inputs(store.root, bindings=bindings, calendar=CAL)
    assert set(inputs) == set(T.INPUT_NAMES)
    assert not _row_is_nan(inputs, T.INPUT_DAILY_TOTAL_RETURN, key, date(2020, 1, 6))


# ===========================================================================
# 56-66. Firewall, bindings and determinism
# ===========================================================================


def test_item56_d3_reads_go_through_development_accessor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, key = _tri_store(tmp_path, "s")
    real = T.DevelopmentAccessor
    calls: list[tuple[str, Any]] = []

    class _Spy:
        def __init__(self, root: Path, *, bindings: Any = None) -> None:
            self._real = real(root, bindings=bindings)

        def read(self, component: str, **kwargs: Any) -> pd.DataFrame:
            calls.append((component, kwargs))
            return self._real.read(component, **kwargs)

    monkeypatch.setattr(T, "DevelopmentAccessor", _Spy)
    _build(store)
    assert [c[0] for c in calls] == ["observations", "ca_events"]
    for _, kwargs in calls:
        assert kwargs["end"] <= T.LAST_SOURCE_DATE
        assert kwargs["end"] < T.RESERVE_START


def test_item57_resolver_refuses_forbidden_and_nonallowlisted_paths(tmp_path: Path) -> None:
    store, key = _tri_store(tmp_path, "s")
    resolver = T.Resolver(store.root, store.build())
    for bad in ("reserve_sealed/x.parquet", "raw/x.parquet", "dsc_evidence/x.parquet"):
        with pytest.raises(T.ReserveFirewallError):
            resolver.resolve(bad)
    with pytest.raises(T.ReserveFirewallError):
        resolver.resolve("derived/identity/i-not-allowlisted/identity_assignment.parquet")
    with pytest.raises(T.D7cInputError):
        resolver.resolve("../escape.parquet")
    # Addendum A2/A4(b): the exact boundary for BOTH guarded helpers.
    # Cutoff 2023-06-29 is the last permitted D-SC query and passes through to
    # the frozen query function; 2023-06-30 and later fail closed.
    assert T._guarded_class_state_at({}, "K1", T.LAST_SOURCE_DATE) == (
        "CLASS_UNRESOLVED",
        "NO_ADMISSIBLE_EVIDENCE",
    )
    assert T._guarded_policy_state_at({}, "K1", T.LAST_SOURCE_DATE) == (
        T.POLICY_NO_EXCLUSION,
        frozenset(),
    )
    for helper in (T._guarded_class_state_at, T._guarded_policy_state_at):
        with pytest.raises(T.ReserveFirewallError):
            helper({}, "K1", date(2023, 6, 30))
        with pytest.raises(T.ReserveFirewallError):
            helper({}, "K1", date(2023, 7, 3))


def test_item58_injected_reserve_dated_row_raises(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.key("AAA", listed_start=date(2019, 1, 2))
    store.obs("AAA", date(2020, 1, 2), 100.0)
    store.obs("AAA", date(2020, 1, 3), 110.0)
    # A D5 flag dated in the reserve is loaded and must fail closed.
    store.flag("AAA", T.RESERVE_START, 99, "NONPOSITIVE_PRICE")
    with pytest.raises(T.ReserveFirewallError):
        _build(store)

    # Addendum A1/A2: a D-SC timeline containing segments dated on/after the
    # reserve boundary is loaded whole and builds without error; the boundary
    # is enforced only when the timeline is queried.
    future = _Store(tmp_path, "future_dsc")
    future_key = future.key("AAA", listed_start=date(2019, 1, 2))
    future.class_seg(future_key, date(2019, 1, 2), "COMMON_STOCK")
    future.class_seg(future_key, date(2023, 7, 3), "NON_COMMON")
    future.policy_seg(future_key, date(2019, 1, 2), T.POLICY_NO_EXCLUSION)
    future.policy_seg(future_key, date(2023, 7, 3), T.POLICY_EXCLUDED)
    future.obs("AAA", date(2020, 1, 2), 100.0)
    future.obs("AAA", date(2020, 1, 3), 110.0)
    content_future = _build(future)
    assert content_future["dsc_max_query_cutoff"] == T.LAST_SOURCE_DATE.isoformat()
    future_inputs = T.load_trusted_inputs(
        future.root, bindings=future.build(), calendar=CAL
    )
    assert _value(
        future_inputs, T.INPUT_DAILY_TOTAL_RETURN, future_key, date(2020, 1, 6)
    ) == pytest.approx(0.1)


def test_item59_binding_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    # each data file
    for stage_index, file_index in ((0, 0), (0, 1), (1, 0), (2, 0), (3, 0), (3, 1)):
        store = _Store(tmp_path, f"s{stage_index}{file_index}")
        store.key("AAA", listed_start=date(2019, 1, 2))
        store.obs("AAA", date(2020, 1, 2), 100.0)
        bindings = store.build()
        rel = bindings.stages()[stage_index].files[file_index].relative_path
        path = store.root / rel
        path.write_bytes(path.read_bytes() + b"x")
        with pytest.raises(T.D7cBindingError):
            T.build_trusted_inputs(store.root, bindings=bindings, calendar=CAL)

    # document mismatch
    store = _Store(tmp_path, "doc")
    store.key("AAA", listed_start=date(2019, 1, 2))
    bindings = store.build()
    bad_doc = replace(bindings.documents[0], sha256="0" * 64)
    with pytest.raises(T.D7cBindingError):
        T.build_trusted_inputs(
            store.root, bindings=replace(bindings, documents=(bad_doc,) + bindings.documents[1:]), calendar=CAL
        )

    # pinned-code mismatch
    bad_code = replace(bindings.pinned_code[0], sha256="0" * 64)
    with pytest.raises(T.D7cBindingError):
        T.build_trusted_inputs(
            store.root, bindings=replace(bindings, pinned_code=(bad_code,) + bindings.pinned_code[1:]), calendar=CAL
        )


def test_item60_decoy_newer_artifact_ignored(tmp_path: Path) -> None:
    store, key = _tri_store(tmp_path, "s")
    bindings = store.build()
    T.build_trusted_inputs(store.root, bindings=bindings, calendar=CAL)
    decoy = store.root / "derived" / "trusted_inputs" / "ti-ffffffffffffffff"
    decoy.mkdir(parents=True)
    (decoy / T.MANIFEST_FILE).write_text("{}", encoding="utf-8")
    inputs = T.load_trusted_inputs(store.root, bindings=bindings, calendar=CAL)
    assert set(inputs) == set(T.INPUT_NAMES)


def test_item61_permuted_inputs_byte_identical(tmp_path: Path) -> None:
    store = _Store(tmp_path, "s")
    store.key("AAA", listed_start=date(2019, 1, 2))
    store.add_sessions("AAA", date(2019, 6, 3), date(2019, 7, 1))
    store.ca("AAA", date(2019, 6, 5), factor=1.05)
    store.key("BBB", listed_start=date(2019, 1, 2))
    store.add_sessions("BBB", date(2019, 6, 3), date(2019, 7, 1), close=25.0, volume=50)
    ca = T.build_trusted_inputs(store.root, bindings=store.build(), calendar=CAL)
    cb = T.build_trusted_inputs(store.root, bindings=store.build(reverse=True), calendar=CAL)
    for name in T.INPUT_NAMES:
        pa_ = _sha256_file(_artifact_dir(store, ca) / T._INPUT_FILES[name])
        pb_ = _sha256_file(_artifact_dir(store, cb) / T._INPUT_FILES[name])
        assert pa_ == pb_
    assert _sha256_file(_artifact_dir(store, ca) / T.SEGMENTS_FILE) == _sha256_file(
        _artifact_dir(store, cb) / T.SEGMENTS_FILE
    )


def test_item62_timezone_and_locale_independent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    store = _Store(tmp_path, "s")
    store.key("AAA", listed_start=date(2019, 1, 2))
    store.add_sessions("AAA", date(2019, 6, 3), date(2019, 7, 1))
    ca = T.build_trusted_inputs(store.root, bindings=store.build(), calendar=CAL)
    with monkeypatch.context() as context:
        context.setenv("TZ", "Asia/Tokyo")
        context.setenv("LC_ALL", "C")
        time.tzset()
        cb = T.build_trusted_inputs(store.root, bindings=store.build(), calendar=CAL)
    time.tzset()
    assert ca["ti_hash"] == cb["ti_hash"]
    for name in T.INPUT_NAMES:
        assert _sha256_file(_artifact_dir(store, ca) / T._INPUT_FILES[name]) == _sha256_file(
            _artifact_dir(store, cb) / T._INPUT_FILES[name]
        )


def test_item63_two_builds_identical_existing_different_bytes_raises(tmp_path: Path) -> None:
    store = _Store(tmp_path, "s")
    store.key("AAA", listed_start=date(2019, 1, 2))
    store.add_sessions("AAA", date(2019, 6, 3), date(2019, 7, 1))
    ca = T.build_trusted_inputs(store.root, bindings=store.build(), calendar=CAL)
    cb = T.build_trusted_inputs(store.root, bindings=store.build(), calendar=CAL)
    assert ca["ti_hash"] == cb["ti_hash"]
    # corrupt an existing artifact file; the next build must refuse to overwrite
    target = _artifact_dir(store, ca) / T._INPUT_FILES[T.INPUT_DAILY_TOTAL_RETURN]
    target.write_bytes(target.read_bytes() + b"corrupt")
    with pytest.raises(T.NondeterministicTrustedInputsError):
        T.build_trusted_inputs(store.root, bindings=store.build(), calendar=CAL)


def test_item64_no_output_date_reaches_reserve(tmp_path: Path) -> None:
    store, key = _tri_store(tmp_path, "s")
    content = _build(store)
    for name in T.INPUT_NAMES:
        long = _read_long(store, content, name)
        if len(long):
            assert (pd.to_datetime(long["row_date"]) < pd.Timestamp(T.RESERVE_START)).all()
            assert (pd.to_datetime(long["source_session"]) < pd.Timestamp(T.RESERVE_START)).all()


def test_item65_no_network_and_no_credential_read(tmp_path: Path) -> None:
    store, key = _tri_store(tmp_path, "s")
    content = _build(store)
    for path in _artifact_dir(store, content).iterdir():
        assert CREDENTIAL_SENTINEL.encode() not in path.read_bytes()


def test_item66_mask_counts_computed_not_hard_coded(tmp_path: Path) -> None:
    store, key = _tri_store(tmp_path, "s")
    content = _build(store)
    counts = content["mask_counts"][T.INPUT_DAILY_TOTAL_RETURN]
    assert counts["admitted"] > 0
    # A store with the key non-COMMON from the start masks more cells.
    store2 = _Store(tmp_path, "s2")
    key2 = store2.key("AAA", listed_start=date(2019, 1, 2))
    store2.class_seg(key2, date(2020, 1, 20), "COMMON_STOCK")
    store2.obs("AAA", date(2020, 1, 2), 10.0)
    store2.obs("AAA", date(2020, 1, 3), 11.0)
    store2.obs("AAA", date(2020, 1, 6), 12.1)
    content2 = _build(store2)
    counts2 = content2["mask_counts"][T.INPUT_DAILY_TOTAL_RETURN]
    assert counts2["admitted"] == 0
    assert counts2["class_masked"] > counts["class_masked"]
