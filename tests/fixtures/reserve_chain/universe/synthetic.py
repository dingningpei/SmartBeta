"""Synthetic D3/D4/D5/D-SC store for the R7-U fold-universe tests.

Authority: ``docs/campaign3_p7_implementation_spec.md`` §3.  Nothing here
reads ``data_store``, ``reserve_sealed``, any real reserve artifact or any
credential.  The store writes both a development package set (``normalized/``,
``derived/qc/``, ``derived/identity/``, ``derived/classification/``) and, when
reserve-dated rows exist, a synthetic ``reserve_sealed/`` D3/D5 package set.
D4 identity and D-SC classification are shared across folds, matching the
frozen design's package table.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Sequence

import pandas as pd
import pyarrow as pa

from smart_beta.dataset_v1 import universe
from smart_beta.vendors.tiingo.calendar_source import build_nyse_calendar

__all__ = [
    "CALENDAR",
    "CAL_DATES",
    "F1",
    "RESERVE_FORMATION",
    "D3_ID",
    "D4_ID",
    "D5_ID",
    "DSC_ID",
    "make_store",
    "Store",
]

CALENDAR = build_nyse_calendar(universe.CALENDAR_START, universe.CALENDAR_END)
CAL_DATES = [ts.date() for ts in CALENDAR.dates]

F1 = date(2016, 1, 29)
#: A later development formation with > 300 preceding sessions.
FL = date(2017, 1, 31)
#: The first reserve formation (design: reserve start 2023-07-03).
RESERVE_FORMATION = date(2023, 7, 31)

D3_ID = "n-r7utest"
D4_ID = "i-r7utest"
D5_ID = "q-r7utest"
DSC_ID = "c-r7utest"

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


def sessions(start: date, end: date) -> list[date]:
    return [d for d in CAL_DATES if start <= d <= end]


def cutoff(f: date) -> date:
    return CALENDAR.previous_trading_day(f).date()


def start_for_age(f: date, age: int) -> date:
    return CAL_DATES[CAL_DATES.index(f) - age]


def window(f: date) -> list[date]:
    i = CAL_DATES.index(f)
    return CAL_DATES[i - universe.LIQUIDITY_WINDOW : i]


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
class Store:
    """A synthetic fold store; rows route to packages by date."""

    tmp_path: Path
    name: str = "store"
    keys: list[dict] = field(default_factory=list)
    dev_obs: list[tuple] = field(default_factory=list)
    res_obs: list[tuple] = field(default_factory=list)
    dev_flags: list[tuple] = field(default_factory=list)
    res_flags: list[tuple] = field(default_factory=list)
    class_rows: list[tuple] = field(default_factory=list)
    policy_rows: list[tuple] = field(default_factory=list)
    _next: dict = field(default_factory=dict)

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
        reason: str = "FITS_ONE_LISTED_ROW",
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
                "reason": reason,
            }
        )
        if class_state is not None:
            self.class_rows.append(
                (sk, class_knowledge, class_state, class_reason, "[]")
            )
        for segment in class_segments:
            self.class_rows.append((sk, segment[0], segment[1], segment[2], "[]"))
        if policy_state is not None:
            self.policy_rows.append((sk, policy_knowledge, policy_state, "[]", "[]"))
        for segment in policy_segments:
            self.policy_rows.append((sk, segment[0], segment[1], "[]", "[]"))
        return sk

    def identity_row(
        self,
        ticker: str,
        *,
        security_key: str,
        state: str,
        listed_start: date | None = None,
        listed_end: date | None = None,
        exchange: str = "NASDAQ",
        reason: str = "FITS_ONE_LISTED_ROW",
    ) -> None:
        self.keys.append(
            {
                "security_key": security_key,
                "ticker": ticker,
                "exchange": exchange,
                "listed_start": listed_start,
                "listed_end": listed_end,
                "state": state,
                "reason": reason,
            }
        )

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
        row = (ticker, d, close, volume, idx)
        if d >= universe.RESERVE_START:
            self.res_obs.append(row)
        else:
            self.dev_obs.append(row)
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
        row = (ticker, d, idx, klass, disposition or _DISPOSITION[klass])
        if d >= universe.RESERVE_START:
            self.res_flags.append(row)
        else:
            self.dev_flags.append(row)

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
        for d in sessions(start, end):
            if d in skipped:
                continue
            self.obs(ticker, d, close, volume, flag=flag, disposition=disposition)

    def healthy(self, ticker: str, f: date = F1, *, age: int = 270) -> str:
        start = start_for_age(f, age)
        self.key(ticker, listed_start=start)
        self.add_sessions(ticker, start, cutoff(f))
        return ticker

    # -- materialise -----------------------------------------------------

    def _frames(self, obs_rows, flag_rows):
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
        return obs, flags

    def _identity_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
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
                "reason": pd.Series(
                    [k["reason"] for k in self.keys], dtype=_STRING
                ),
            }
        )

    def _dsc_frames(self):
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
        return class_tl, policy_tl

    def _documents(self) -> tuple:
        documents = []
        for name in sorted(universe._DOCUMENT_NAMES):
            path = self.root / "docs" / f"{name}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"normative document {name}\n", encoding="utf-8")
            documents.append(
                universe.DocumentBinding(name, str(path), _sha256_file(path))
            )
        return tuple(documents)

    def build(self, *, with_reserve: bool | None = None) -> dict[str, Any]:
        """Write the packages and return the fold bindings.

        Returns ``{"development": dev_bindings, "reserve": res_bindings|None,
        "lookback": dev_bindings, "documents": docs}``.
        """
        root = self.root
        if with_reserve is None:
            with_reserve = bool(self.res_obs or self.res_flags)

        d3_dev = f"normalized/{D3_ID}/observations.parquet"
        d5_dev = f"derived/qc/{D5_ID}/day_flags.parquet"
        d4_file = f"derived/identity/{D4_ID}/identity_assignment.parquet"
        dsc_class = f"derived/classification/{DSC_ID}/class_timeline.parquet"
        dsc_policy = f"derived/classification/{DSC_ID}/policy_timeline.parquet"

        obs_dev, flags_dev = self._frames(self.dev_obs, self.dev_flags)
        sha_obs_dev = _write_parquet(root, d3_dev, obs_dev)
        sha_flags_dev = _write_parquet(root, d5_dev, flags_dev)
        sha_ident = _write_parquet(root, d4_file, self._identity_frame())
        class_tl, policy_tl = self._dsc_frames()
        sha_class = _write_parquet(root, dsc_class, class_tl)
        sha_policy = _write_parquet(root, dsc_policy, policy_tl)

        d3_hash = _write_manifest(
            root,
            f"normalized/{D3_ID}/build.json",
            "normalized_hash",
            {d3_dev: sha_obs_dev},
        )
        d4_hash = _write_manifest(
            root,
            f"derived/identity/{D4_ID}/identity.json",
            "identity_hash",
            {d4_file: sha_ident},
        )
        d5_hash = _write_manifest(
            root,
            f"derived/qc/{D5_ID}/qc_report.json",
            "qc_hash",
            {d5_dev: sha_flags_dev},
        )
        dsc_hash = _write_manifest(
            root,
            f"derived/classification/{DSC_ID}/dsc.json",
            "dsc_hash",
            {dsc_class: sha_class, dsc_policy: sha_policy},
        )

        documents = self._documents()
        dev_bindings = universe.Bindings(
            d3=universe.StageBinding(
                "D3",
                D3_ID,
                f"normalized/{D3_ID}/build.json",
                "normalized_hash",
                d3_hash,
                (universe.FileBinding(d3_dev, sha_obs_dev),),
            ),
            d4=universe.StageBinding(
                "D4",
                D4_ID,
                f"derived/identity/{D4_ID}/identity.json",
                "identity_hash",
                d4_hash,
                (universe.FileBinding(d4_file, sha_ident),),
            ),
            d5=universe.StageBinding(
                "D5",
                D5_ID,
                f"derived/qc/{D5_ID}/qc_report.json",
                "qc_hash",
                d5_hash,
                (universe.FileBinding(d5_dev, sha_flags_dev),),
            ),
            dsc=universe.StageBinding(
                "DSC",
                DSC_ID,
                f"derived/classification/{DSC_ID}/dsc.json",
                "dsc_hash",
                dsc_hash,
                (
                    universe.FileBinding(dsc_class, sha_class),
                    universe.FileBinding(dsc_policy, sha_policy),
                ),
            ),
            documents=documents,
        )

        res_bindings = None
        if with_reserve:
            d3_res = f"reserve_sealed/{D3_ID}/observations.parquet"
            d5_res = f"reserve_sealed/{D5_ID}/day_flags.parquet"
            obs_res, flags_res = self._frames(self.res_obs, self.res_flags)
            sha_obs_res = _write_parquet(root, d3_res, obs_res)
            sha_flags_res = _write_parquet(root, d5_res, flags_res)
            d3_res_hash = _write_manifest(
                root,
                f"reserve_sealed/{D3_ID}/build.json",
                "normalized_hash",
                {d3_res: sha_obs_res},
            )
            d5_res_hash = _write_manifest(
                root,
                f"reserve_sealed/{D5_ID}/qc_report.json",
                "qc_hash",
                {d5_res: sha_flags_res},
            )
            res_bindings = universe.Bindings(
                d3=universe.StageBinding(
                    "D3",
                    D3_ID,
                    f"reserve_sealed/{D3_ID}/build.json",
                    "normalized_hash",
                    d3_res_hash,
                    (universe.FileBinding(d3_res, sha_obs_res),),
                ),
                d4=dev_bindings.d4,
                d5=universe.StageBinding(
                    "D5",
                    D5_ID,
                    f"reserve_sealed/{D5_ID}/qc_report.json",
                    "qc_hash",
                    d5_res_hash,
                    (universe.FileBinding(d5_res, sha_flags_res),),
                ),
                dsc=dev_bindings.dsc,
                documents=documents,
            )

        return {
            "development": dev_bindings,
            "reserve": res_bindings,
            "lookback": dev_bindings,
            "documents": documents,
        }


def make_store(tmp_path: Path, name: str = "store") -> Store:
    return Store(tmp_path, name=name)
