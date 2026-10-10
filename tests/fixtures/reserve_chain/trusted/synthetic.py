"""Synthetic D3/D4/D5/D-SC packages for the R7-T trusted-fold tests.

Nothing here reads a production artifact, the real ``data_store``, a credential
or the network.  The same :class:`SyntheticStore` materialises *development*
packages (frozen paths ``normalized/…``, ``derived/…``) and *reserve-shaped*
packages (a distinct injected prefix) so the fold-generic builder can be
exercised end to end without touching ``reserve_sealed``.

Authority: ``docs/campaign3_p7_implementation_spec.md`` (§2, §3) and
``docs/campaign3_p7_amendment_a1.md``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd
import pyarrow as pa

from smart_beta.dataset_v1 import trusted_inputs as T
from smart_beta.vendors.tiingo.calendar_source import build_nyse_calendar

__all__ = [
    "CAL",
    "SESSIONS",
    "POS",
    "FAR",
    "D3_ID",
    "RESERVE_START",
    "SyntheticStore",
    "PackageAccessor",
    "sessions_between",
    "prev_session",
    "next_session",
]

CAL = build_nyse_calendar(T.CALENDAR_START, T.CALENDAR_END)
SESSIONS = [ts.date() for ts in CAL.dates]
POS = {d: i for i, d in enumerate(SESSIONS)}
FAR = date(2099, 1, 1)
D3_ID = "n-f874d3ecb61374d4"
RESERVE_START = T.RESERVE_START

_STRING = pd.ArrowDtype(pa.string())
_DATE32 = pd.ArrowDtype(pa.date32())

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


def sessions_between(a: date, b: date) -> list[date]:
    return [d for d in SESSIONS if a <= d <= b]


def prev_session(d: date) -> date | None:
    i = POS.get(d)
    if i is None or i == 0:
        return None
    return SESSIONS[i - 1]


def next_session(d: date) -> date | None:
    i = POS.get(d)
    if i is None or i + 1 >= len(SESSIONS):
        return None
    return SESSIONS[i + 1]


def _canon(obj: object) -> str:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write_parquet(root: Path, rel: str, frame: pd.DataFrame) -> str:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, engine="pyarrow", index=False, compression="zstd")
    return _sha256_file(path)


def _write_manifest(
    root: Path, rel: str, field_name: str, files: dict
) -> tuple[str, str]:
    content = {"files_sha256": files}
    content[field_name] = hashlib.sha256(
        _canon(files).encode("utf-8")
    ).hexdigest()
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
        files=tuple(
            T.FileBinding(rel, sha) for rel, sha in hashes.items()
        ),
    )


class PackageAccessor:
    """A minimal D3 accessor over an injected package (reserve-shaped tests).

    It mirrors the frozen ``DevelopmentAccessor.read`` contract and has no
    firewall of its own: the test is responsible for pointing it at synthetic
    reserve-shaped files.
    """

    def __init__(
        self, root: Path | str, files: Mapping[str, str]
    ) -> None:
        self._root = Path(root)
        self._files = dict(files)

    def read(
        self,
        component: str,
        *,
        start: date,
        end: date,
        columns: Sequence[str],
        tickers: Sequence[str] | None = None,
    ) -> pd.DataFrame:
        if component not in self._files:
            raise KeyError(component)
        frame = pd.read_parquet(self._root / self._files[component])
        date_col = (
            "effective_date" if component == "ca_events" else "date"
        )
        id_col = "stock_id" if component == "ca_events" else "ticker"
        parsed = pd.to_datetime(frame[date_col]).dt.date
        frame = frame[(parsed >= start) & (parsed <= end)]
        if tickers is not None:
            frame = frame[frame[id_col].isin(list(tickers))]
        return frame[list(columns)].reset_index(drop=True)


class SyntheticStore:
    """A synthetic D3/D4/D5/D-SC store that can emit dev and reserve packages."""

    def __init__(self, tmp_path: Path, name: str = "store") -> None:
        self.root = Path(tmp_path)
        self.name = name
        self.keys: list[dict[str, Any]] = []
        self.obs_rows: list[tuple] = []
        self.flag_rows: list[tuple] = []
        self.ca_rows: list[tuple] = []
        self.class_rows: list[tuple] = []
        self.policy_rows: list[tuple] = []
        self._next_idx: dict[str, int] = {}
        self._explicit_class: set[str] = set()
        self._explicit_policy: set[str] = set()

    # -- declarations ---------------------------------------------------

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
        for d in sessions_between(start, end):
            if d in skipped:
                continue
            self.obs(ticker, d, close, volume, flag=flag)

    def ca(
        self,
        ticker: str,
        effective_date: date,
        *,
        factor: float = 1.1,
        action_type: str = "dividend",
        knowledge_date: date | None = None,
    ) -> None:
        self.ca_rows.append(
            (
                ticker,
                effective_date,
                action_type,
                knowledge_date or effective_date,
                factor,
                False,
            )
        )

    def class_seg(
        self,
        security_key: str,
        knowledge_date: date,
        state: str,
        reason: str = "TEST",
    ) -> None:
        self.class_rows.append(
            (security_key, knowledge_date, state, reason)
        )
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

    # -- materialise ----------------------------------------------------

    def _identity_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "security_key": pd.Series(
                    [k["security_key"] for k in self.keys], dtype=_STRING
                ),
                "ticker": pd.Series(
                    [k["ticker"] for k in self.keys], dtype=_STRING
                ),
                "listed_start": pd.Series(
                    [k["listed_start"] for k in self.keys], dtype=_DATE32
                ),
                "listed_end": pd.Series(
                    [k["listed_end"] for k in self.keys], dtype=_DATE32
                ),
                "state": pd.Series(
                    [k["state"] for k in self.keys], dtype=_STRING
                ),
            }
        )

    def _class_frame(self) -> pd.DataFrame:
        rows = list(self.class_rows)
        for key in self.keys:
            sk = key["security_key"]
            if sk not in self._explicit_class:
                rows.append(
                    (sk, key["listed_start"], "COMMON_STOCK", "DEFAULT")
                )
        return pd.DataFrame(
            {
                "security_key": pd.Series(
                    [r[0] for r in rows], dtype=_STRING
                ),
                "knowledge_date": pd.Series(
                    [r[1] for r in rows], dtype=_DATE32
                ),
                "state": pd.Series([r[2] for r in rows], dtype=_STRING),
                "reason": pd.Series([r[3] for r in rows], dtype=_STRING),
                "evidence_ids": pd.Series(["[]"] * len(rows), dtype=_STRING),
            }
        )

    def _policy_frame(self) -> pd.DataFrame:
        rows = list(self.policy_rows)
        for key in self.keys:
            sk = key["security_key"]
            if sk not in self._explicit_policy:
                rows.append(
                    (sk, key["listed_start"], T.POLICY_NO_EXCLUSION, "")
                )
        return pd.DataFrame(
            {
                "security_key": pd.Series(
                    [r[0] for r in rows], dtype=_STRING
                ),
                "knowledge_date": pd.Series(
                    [r[1] for r in rows], dtype=_DATE32
                ),
                "policy_state": pd.Series(
                    [r[2] for r in rows], dtype=_STRING
                ),
                "active_sources": pd.Series(
                    [r[3] for r in rows], dtype=_STRING
                ),
                "evidence_refs": pd.Series(
                    ["[]"] * len(rows), dtype=_STRING
                ),
            }
        )

    def _obs_frame(self, *, reserve: bool) -> pd.DataFrame:
        rows = [
            r for r in self.obs_rows
            if (r[1] >= RESERVE_START) == reserve
        ]
        return pd.DataFrame(
            {
                "ticker": pd.Series([r[0] for r in rows], dtype=_STRING),
                "date": pd.Series([r[1] for r in rows], dtype=_DATE32),
                "close": pd.Series([r[2] for r in rows], dtype="float64"),
                "volume": pd.Series([r[3] for r in rows], dtype="Int64"),
                "raw_row_index": pd.Series(
                    [r[4] for r in rows], dtype="int64"
                ),
            }
        )

    def _flag_frame(self, *, reserve: bool) -> pd.DataFrame:
        rows = [
            r for r in self.flag_rows
            if (r[1] >= RESERVE_START) == reserve
        ]
        return pd.DataFrame(
            {
                "ticker": pd.Series([r[0] for r in rows], dtype=_STRING),
                "date": pd.Series([r[1] for r in rows], dtype=_DATE32),
                "raw_row_index": pd.Series(
                    [r[2] for r in rows], dtype="int64"
                ),
                "class_or_flag": pd.Series(
                    [r[3] for r in rows], dtype=_STRING
                ),
                "disposition": pd.Series(
                    [r[4] for r in rows], dtype=_STRING
                ),
            }
        )

    def _ca_frame(self, *, reserve: bool) -> pd.DataFrame:
        rows = [
            r for r in self.ca_rows
            if (r[1] >= RESERVE_START) == reserve
        ]
        return pd.DataFrame(
            {
                "stock_id": pd.Series([r[0] for r in rows], dtype=_STRING),
                "effective_date": pd.Series(
                    [r[1] for r in rows], dtype=_DATE32
                ),
                "action_type": pd.Series(
                    [r[2] for r in rows], dtype=_STRING
                ),
                "knowledge_date": pd.Series(
                    [r[3] for r in rows], dtype=_DATE32
                ),
                "adjustment_factor": pd.Series(
                    [r[4] for r in rows], dtype="float64"
                ),
                "is_superseded": pd.Series(
                    [r[5] for r in rows], dtype=bool
                ),
            }
        )

    def build_development(self) -> T.Bindings:
        root = self.root
        d3 = _stage(
            root,
            "D3",
            D3_ID,
            f"normalized/{D3_ID}/build.json",
            "normalized_hash",
            [
                (
                    f"normalized/{D3_ID}/observations.parquet",
                    self._obs_frame(reserve=False),
                ),
                (
                    f"normalized/{D3_ID}/ca_events.parquet",
                    self._ca_frame(reserve=False),
                ),
            ],
        )
        d4 = _stage(
            root,
            "D4",
            "i-foldsynth",
            "derived/identity/i-foldsynth/identity.json",
            "identity_hash",
            [
                (
                    "derived/identity/i-foldsynth/identity_assignment.parquet",
                    self._identity_frame(),
                )
            ],
        )
        d5 = _stage(
            root,
            "D5",
            "q-foldsynth",
            "derived/qc/q-foldsynth/qc_report.json",
            "qc_hash",
            [
                (
                    "derived/qc/q-foldsynth/day_flags.parquet",
                    self._flag_frame(reserve=False),
                )
            ],
        )
        dsc = _stage(
            root,
            "DSC",
            "c-foldsynth",
            "derived/classification/c-foldsynth/dsc.json",
            "dsc_hash",
            [
                (
                    "derived/classification/c-foldsynth/class_timeline.parquet",
                    self._class_frame(),
                ),
                (
                    "derived/classification/c-foldsynth/policy_timeline.parquet",
                    self._policy_frame(),
                ),
            ],
        )
        return T.Bindings(
            d3=d3,
            d4=d4,
            d5=d5,
            dsc=dsc,
            documents=T._documents(),
            pinned_code=T._pinned_code(),
        )

    def build_reserve(self, prefix: str = "reserve_pkg") -> T.Bindings:
        root = self.root
        d3 = _stage(
            root,
            "D3",
            D3_ID,
            f"{prefix}/{D3_ID}/build.json",
            "normalized_hash",
            [
                (
                    f"{prefix}/{D3_ID}/observations.parquet",
                    self._obs_frame(reserve=True),
                ),
                (
                    f"{prefix}/{D3_ID}/ca_events.parquet",
                    self._ca_frame(reserve=True),
                ),
            ],
        )
        d4 = _stage(
            root,
            "D4",
            "i-foldsynth",
            f"{prefix}/identity/identity.json",
            "identity_hash",
            [
                (
                    f"{prefix}/identity/identity_assignment.parquet",
                    self._identity_frame(),
                )
            ],
        )
        d5 = _stage(
            root,
            "D5",
            "q-foldsynth",
            f"{prefix}/qc/qc_report.json",
            "qc_hash",
            [
                (
                    f"{prefix}/qc/day_flags.parquet",
                    self._flag_frame(reserve=True),
                )
            ],
        )
        dsc = _stage(
            root,
            "DSC",
            "c-foldsynth",
            f"{prefix}/classification/dsc.json",
            "dsc_hash",
            [
                (
                    f"{prefix}/classification/class_timeline.parquet",
                    self._class_frame(),
                ),
                (
                    f"{prefix}/classification/policy_timeline.parquet",
                    self._policy_frame(),
                ),
            ],
        )
        return T.Bindings(
            d3=d3,
            d4=d4,
            d5=d5,
            dsc=dsc,
            documents=T._documents(),
            pinned_code=T._pinned_code(),
        )

    def reserve_accessor(self, prefix: str = "reserve_pkg") -> PackageAccessor:
        return PackageAccessor(
            self.root,
            {
                "observations": (
                    f"{prefix}/{D3_ID}/observations.parquet"
                ),
                "ca_events": f"{prefix}/{D3_ID}/ca_events.parquet",
            },
        )


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


def populate_standard(store: SyntheticStore) -> None:
    """A rich development scenario: gaps, corporate actions, flags, masks."""
    aaa = store.key("AAA", listed_start=date(2019, 1, 2))
    # A long series with a gap in Jan 2020 (a TRI break) and a second gap in
    # Jun 2023 so that both development and reserve lookback matter.
    store.add_sessions(
        "AAA", date(2019, 1, 2), date(2023, 6, 29),
        close=10.0, skip=(date(2020, 1, 8),),
    )
    # Re-price every session deterministically after the gap so returns vary.
    store.obs_rows = [
        (
            r[0], r[1],
            10.0 + (POS.get(r[1], 0) % 7) * 0.25,
            r[3], r[4],
        )
        if r[0] == "AAA" and r[1] >= date(2020, 1, 9)
        else r
        for r in store.obs_rows
    ]
    store.ca("AAA", date(2021, 3, 15), factor=1.25)

    bbb = store.key("BBB", listed_start=date(2021, 6, 1))
    store.add_sessions("BBB", date(2021, 6, 1), date(2023, 6, 29), close=5.0)
    # A corrupt row on BBB makes one session unusable (but not the whole key).
    corrupt_idx = store.obs("BBB", date(2022, 5, 4), 6.0)
    store.flag("BBB", date(2022, 5, 4), corrupt_idx, "OHLC_INCONSISTENT")

    ccc = store.key("CCC", listed_start=date(2020, 1, 2))
    store.add_sessions("CCC", date(2020, 1, 2), date(2023, 6, 29), close=20.0)
    # CCC is COMMON until 2022-01-03, then not a common stock.
    store.class_seg(ccc, date(2020, 1, 2), "COMMON_STOCK")
    store.class_seg(ccc, date(2022, 1, 3), "REIT")

    ddd = store.key("DDD", listed_start=date(2019, 6, 3))
    store.add_sessions("DDD", date(2019, 6, 3), date(2023, 6, 29), close=8.0)
    # DDD has no regulatory exclusion from 2023-01-03.
    store.policy_seg(ddd, date(2023, 1, 3), "EXCLUDED")

    # A duplicate/ambiguous identity key is excluded from the grid.
    store.key(
        "EEE",
        listed_start=date(2020, 1, 2),
        security_key="TIINGO:EEE:2020-01-02",
        state=T.STATE_INTERVAL_ASSIGNED,
    )
    store.key(
        "EEE",
        listed_start=date(2020, 1, 2),
        security_key="TIINGO:EEE:2020-01-02",
        state=T.STATE_INTERVAL_ASSIGNED,
    )


def populate_reserve(store: SyntheticStore) -> None:
    """Extend a standard scenario with reserve-shaped rows on one key."""
    store.add_sessions(
        "AAA", date(2023, 6, 30), date(2023, 6, 30), close=13.5
    )
    store.add_sessions(
        "AAA", date(2023, 7, 3), date(2023, 7, 14), close=14.0
    )
    store.ca("AAA", date(2023, 7, 7), factor=1.05)
