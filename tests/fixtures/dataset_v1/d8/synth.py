"""Synthetic-fixture builders for the D8 reserve firewall / ARTIFACT tests.

Every builder writes only under a caller-supplied ``tmp_path``; nothing here
touches the real ``data_store``, the reserve files, the network or a
credential.  The module is imported by the two D8 test files via an explicit
``sys.path`` insert into ``tests/fixtures/dataset_v1/d8/``.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

import pyarrow as pa
import pyarrow.parquet as pq

import smart_beta.dataset_v1.reserve_artifact as A
import smart_beta.dataset_v1.reserve_firewall as F

D3_ID = "n-f874d3ecb61374d4"
D4_ID = "i-a21f1e46621dee94"

# The frozen D3 observation schema, in order.
_OBS_FIELDS: list[tuple[str, pa.DataType]] = [
    ("ticker", pa.string()),
    ("date", pa.date32()),
    ("open", pa.float64()),
    ("high", pa.float64()),
    ("low", pa.float64()),
    ("close", pa.float64()),
    ("adjOpen", pa.float64()),
    ("adjHigh", pa.float64()),
    ("adjLow", pa.float64()),
    ("adjClose", pa.float64()),
    ("divCash", pa.float64()),
    ("splitFactor", pa.float64()),
    ("volume", pa.int64()),
    ("adjVolume", pa.int64()),
    ("raw_row_index", pa.int64()),
    ("raw_sha256", pa.string()),
]
_OBS_SCHEMA = pa.schema(_OBS_FIELDS)

_FLAG_SCHEMA = pa.schema(
    [
        ("ticker", pa.string()),
        ("date", pa.date32()),
        ("raw_row_index", pa.int64()),
        ("flag", pa.string()),
    ]
)

_CA_SCHEMA = pa.schema(
    [
        ("stock_id", pa.string()),
        ("effective_date", pa.timestamp("ns")),
        ("action_type", pa.string()),
        ("knowledge_date", pa.timestamp("ns")),
        ("adjustment_factor", pa.float64()),
        ("is_superseded", pa.bool_()),
        ("_source_vendor", pa.string()),
        ("_source_endpoint", pa.string()),
        ("batch_id", pa.string()),
    ]
)

_D4_SCHEMA = pa.schema(
    [
        ("security_key", pa.string()),
        ("ticker", pa.string()),
        ("exchange", pa.string()),
        ("listed_start", pa.date32()),
        ("listed_end", pa.date32()),
        ("state", pa.string()),
        ("reason", pa.string()),
        ("acquisition_status", pa.string()),
        ("candidate_row_count", pa.int64()),
        ("pool_row_count", pa.int64()),
        ("fit_row_count", pa.int64()),
        ("overlap", pa.bool_()),
        ("post_c_shadow", pa.bool_()),
        ("identifier_continuity", pa.string()),
        ("evidence", pa.string()),
    ]
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _as_ts(value: Any) -> Any:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    return value


def write_observations(path: Path, rows: Sequence[tuple[str, date]]) -> None:
    records = []
    for index, (ticker, row_date) in enumerate(rows):
        records.append(
            {
                "ticker": ticker,
                "date": row_date,
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "adjOpen": 1.0,
                "adjHigh": 1.0,
                "adjLow": 1.0,
                "adjClose": 1.0,
                "divCash": 0.0,
                "splitFactor": 1.0,
                "volume": 100,
                "adjVolume": 100,
                "raw_row_index": index,
                "raw_sha256": "0" * 64,
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(records, schema=_OBS_SCHEMA), path)


def write_observations_raw(
    path: Path, records: Sequence[Mapping[str, Any]]
) -> None:
    """Write observations with an arbitrary schema (for schema-mismatch tests)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(list(records))
    pq.write_table(table, path)


def write_flags(path: Path, rows: Sequence[tuple[str, date]]) -> None:
    records = [
        {
            "ticker": ticker,
            "date": row_date,
            "raw_row_index": index,
            "flag": "VENDOR_NULL",
        }
        for index, (ticker, row_date) in enumerate(rows)
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(records, schema=_FLAG_SCHEMA), path)


def write_ca(path: Path, rows: Sequence[tuple[str, date]]) -> None:
    records = [
        {
            "stock_id": stock_id,
            "effective_date": _as_ts(effective),
            "action_type": "DIVIDEND",
            "knowledge_date": _as_ts(effective),
            "adjustment_factor": 1.0,
            "is_superseded": False,
            "_source_vendor": "tiingo",
            "_source_endpoint": "daily",
            "batch_id": "b-test",
        }
        for stock_id, effective in rows
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(records, schema=_CA_SCHEMA), path)


def _d4_defaults(entry: Mapping[str, Any]) -> dict[str, Any]:
    base = {
        "security_key": entry["security_key"],
        "ticker": entry["ticker"],
        "exchange": "XNAS",
        "listed_start": entry.get("listed_start", date(2000, 1, 1)),
        "listed_end": entry.get("listed_end", date(2030, 1, 1)),
        "state": entry.get("state", "INTERVAL_ASSIGNED"),
        "reason": entry.get("reason", "FITS_ONE_LISTED_ROW"),
        "acquisition_status": entry.get("acquisition_status", "OK"),
        "candidate_row_count": 1,
        "pool_row_count": 1,
        "fit_row_count": 1,
        "overlap": False,
        "post_c_shadow": False,
        "identifier_continuity": "NOT_CERTIFIED",
        "evidence": "{}",
    }
    return base


def write_d4(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    records = [_d4_defaults(entry) for entry in rows]
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(records, schema=_D4_SCHEMA), path)


def _attempt(
    batch_id: str,
    ticker: str,
    *,
    retrieved_at_utc: str,
    attempt: int = 1,
    http_status: int = 200,
    error_class: Any = None,
    row_count: int = 10,
    last_date: str = "2026-08-31T00:00:00.000Z",
) -> dict[str, Any]:
    return {
        "batch_id": batch_id,
        "ticker": ticker,
        "security_keys": [f"XNAS:{ticker}"],
        "endpoint": f"/tiingo/daily/{ticker}/prices",
        "requested_range": {"startDate": "2015-01-02", "endDate": "2026-08-31"},
        "retrieved_at_utc": retrieved_at_utc,
        "http_status": http_status,
        "error_class": error_class,
        "attempt": attempt,
        "row_count": row_count,
        "first_date": "2015-01-02T00:00:00.000Z",
        "last_date": last_date,
    }


def write_manifest(
    root: Path, batch_id: str, attempts: Sequence[Mapping[str, Any]]
) -> None:
    manifest_dir = root / "manifest" / batch_id
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "batch.json").write_text(
        json.dumps({"batch_id": batch_id}, sort_keys=True), encoding="utf-8"
    )
    (manifest_dir / "status.json").write_text(
        json.dumps({"batch_id": batch_id, "batch_state": "COMPLETE"}, sort_keys=True),
        encoding="utf-8",
    )
    lines = [
        json.dumps(record, sort_keys=True, separators=(",", ":"))
        for record in attempts
    ]
    (manifest_dir / "attempts.jsonl").write_text(
        "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
    )


@dataclasses.dataclass
class SyntheticStore:
    """A synthetic data-store root plus the bindings that describe it."""

    root: Path
    bindings: A.ReserveBindings
    obs_rows: list[tuple[str, date]]
    flag_rows: list[tuple[str, date]]
    ca_rows: list[tuple[str, date]]
    d4_rows: list[dict[str, Any]]


def build_store(
    tmp_path: Path,
    *,
    obs_rows: Sequence[tuple[str, date]] | None = None,
    flag_rows: Sequence[tuple[str, date]] | None = None,
    ca_rows: Sequence[tuple[str, date]] | None = None,
    d4_rows: Sequence[Mapping[str, Any]] | None = None,
    attempts: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    obs_schema_override: Sequence[Mapping[str, Any]] | None = None,
) -> SyntheticStore:
    """Build a synthetic D8 store and return its bindings.

    Defaults are a fully resolvable single-ticker reserve and two qualifying
    manifests (smoke retrieved 2026-09-01, production 2026-09-27).
    """
    root = tmp_path / "discovery_dataset_v1"
    obs_rows = list(obs_rows) if obs_rows is not None else [("AAA", date(2023, 7, 5))]
    flag_rows = list(flag_rows) if flag_rows is not None else []
    ca_rows = list(ca_rows) if ca_rows is not None else []
    d4_rows = (
        list(d4_rows)
        if d4_rows is not None
        else [
            {
                "security_key": "XNAS:AAA",
                "ticker": "AAA",
                "listed_start": date(2000, 1, 1),
                "listed_end": date(2030, 1, 1),
                "state": "INTERVAL_ASSIGNED",
            }
        ]
    )

    obs_path = root / "reserve_sealed" / D3_ID / "observations.parquet"
    flags_path = root / "reserve_sealed" / D3_ID / "obs_flags.parquet"
    ca_path = root / "reserve_sealed" / D3_ID / "ca_events.parquet"
    if obs_schema_override is not None:
        write_observations_raw(obs_path, obs_schema_override)
    else:
        write_observations(obs_path, obs_rows)
    write_flags(flags_path, flag_rows)
    write_ca(ca_path, ca_rows)

    # Development files so the accessor bindings can also be built if wanted.
    dev_dir = root / "normalized" / D3_ID
    write_observations(dev_dir / "observations.parquet", obs_rows)
    write_flags(dev_dir / "obs_flags.parquet", flag_rows)
    write_ca(dev_dir / "ca_events.parquet", ca_rows)

    # D3 build.json.
    build_json = {
        "build_id": D3_ID,
        "normalized_hash": A.D3_NORMALIZED_HASH,
        "schema": "discovery-dataset-v1-normalized-build-v1",
    }
    build_json_path = dev_dir / "build.json"
    build_json_path.write_text(
        json.dumps(build_json, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    # D4 identity files.
    d4_dir = root / "derived" / "identity" / D4_ID
    write_d4(d4_dir / "identity_assignment.parquet", d4_rows)
    identity_json = {"identity_id": D4_ID, "identity_hash": "0" * 64}
    (d4_dir / "identity.json").write_text(
        json.dumps(identity_json, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    # Manifests.
    if attempts is None:
        attempts = {
            A.SMOKE_BATCH: [
                _attempt(
                    A.SMOKE_BATCH,
                    obs_rows[0][0] if obs_rows else "AAA",
                    retrieved_at_utc="2026-09-01T12:00:00Z",
                )
            ],
            A.PRODUCTION_BATCH: [
                _attempt(
                    A.PRODUCTION_BATCH,
                    obs_rows[0][0] if obs_rows else "AAA",
                    retrieved_at_utc="2026-09-27T00:05:53Z",
                )
            ],
        }
    for batch_id, records in attempts.items():
        write_manifest(root, batch_id, records)

    manifests = {
        batch_id: {
            filename: sha256_file(root / "manifest" / batch_id / filename)
            for filename in ("batch.json", "status.json", "attempts.jsonl")
        }
        for batch_id in sorted(attempts)
    }

    bindings = dataclasses.replace(
        A.default_bindings(),
        build_id=D3_ID,
        normalized_hash=A.D3_NORMALIZED_HASH,
        build_json_sha256=sha256_file(build_json_path),
        reserve_files={
            component: sha256_file(
                root / "reserve_sealed" / D3_ID / f"{component}.parquet"
            )
            for component in ("ca_events", "obs_flags", "observations")
        },
        identity_id=D4_ID,
        identity_hash="0" * 64,
        identity_json_sha256=sha256_file(d4_dir / "identity.json"),
        identity_assignment_sha256=sha256_file(
            d4_dir / "identity_assignment.parquet"
        ),
        manifests=manifests,
    )
    return SyntheticStore(
        root=root,
        bindings=bindings,
        obs_rows=obs_rows,
        flag_rows=flag_rows,
        ca_rows=ca_rows,
        d4_rows=d4_rows,
    )


def accessor_bindings(root: Path) -> dict[str, F.FileBinding]:
    """Build synthetic accessor bindings for a store's development files."""
    base = root / "normalized" / D3_ID
    return {
        component: F.FileBinding(
            f"normalized/{D3_ID}/{component}.parquet",
            sha256_file(base / f"{component}.parquet"),
        )
        for component in ("observations", "obs_flags", "ca_events")
    }
