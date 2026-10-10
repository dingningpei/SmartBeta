"""R7-T — fold-generic D7c trusted inputs: development equivalence and stitching.

Authority: ``docs/campaign3_p7_implementation_spec.md`` (§2 fold parameter,
§3 R7-T), ``docs/campaign3_p7_amendment_a1.md`` (new fold-generic code proven
by development equivalence), ``docs/campaign3_p7_reserve_path_design.md``
(§2.3 lookback across the boundary).

Every test builds synthetic packages under ``tmp_path``; no test reads the real
``data_store``, ``reserve_sealed``, a credential or the network.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import socket
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from smart_beta.dataset_v1 import trusted_inputs as T
from smart_beta.dataset_v1.reserve_chain import trusted_fold as F

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE = (
    _REPO_ROOT
    / "tests"
    / "fixtures"
    / "reserve_chain"
    / "trusted"
    / "synthetic.py"
)
_MUTATIONS = (
    _REPO_ROOT
    / "tests"
    / "fixtures"
    / "reserve_chain"
    / "trusted"
    / "mutations.json"
)


def _load_fixture():
    name = "r7t_synthetic"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _FIXTURE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


FX = _load_fixture()

_GRANTS_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "reserve_chain" / "confirm" / "synthetic.py"
)


def _load_grants():
    name = "r7x_confirm_synthetic"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _GRANTS_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_GRANTS = _load_grants()


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch):
    def _refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("R7-T tests must never open a socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield


@pytest.fixture(autouse=True)
def _credential_sentinel(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TIINGO_API_KEY", "r7t-credential-sentinel")
    yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LONG_KEYS = ["row_date", "security_key"]
_SEGMENT_KEYS = ["security_key", "segment_id"]


def _standard_store(tmp_path: Path, name: str = "dev"):
    store = FX.SyntheticStore(tmp_path, name=name)
    FX.populate_standard(store)
    FX.populate_reserve(store)
    return store


def _artifact_dir(root: Path, content, prefix: str = "derived/trusted_inputs"):
    return root / prefix / content["ti_id"]


def _read_dev_frames(root: Path, content) -> dict[str, pd.DataFrame]:
    directory = _artifact_dir(root, content)
    frames = {
        name: pd.read_parquet(directory / T._INPUT_FILES[name])
        for name in T.INPUT_NAMES
    }
    frames["segments"] = pd.read_parquet(directory / T.SEGMENTS_FILE)
    return frames


def _canonical(frame: pd.DataFrame, keys) -> pd.DataFrame:
    return frame.sort_values(keys, kind="stable").reset_index(drop=True)


def _assert_equal_frames(new, frozen, keys) -> None:
    pd.testing.assert_frame_equal(
        _canonical(new, keys),
        _canonical(frozen, keys),
        check_exact=True,
        check_dtype=True,
    )


def _select(frame: pd.DataFrame, d: date, key: str) -> pd.DataFrame:
    dates = pd.to_datetime(frame["row_date"]).dt.date
    return frame[(dates == d) & (frame["security_key"] == key)]


def _dev_fold():
    return F.development_fold()


def _grant(end: date = date(2023, 12, 29)):
    # A hand-built grant is refused since R7-X-R1; mint a K-PIT-verified one.
    return _GRANTS.verified_grant(hypothesis_id="H-R7T-TEST", fold_end=end)


def _reserve_fold(end: date = date(2023, 7, 14)):
    return F.FoldWindow.reserve(end, grant=_grant(end))


# ===========================================================================
# 1. Development equivalence (amendment A1)
# ===========================================================================


def test_dev_fold_reproduces_frozen_tables_and_fields(tmp_path: Path) -> None:
    store = _standard_store(tmp_path)
    bindings = store.build_development()

    frozen = T.build_trusted_inputs(
        store.root, bindings=bindings, calendar=FX.CAL
    )
    new = F.build_trusted_fold(
        store.root, fold=_dev_fold(),
        bindings=bindings, calendar=FX.CAL,
    )

    frozen_frames = _read_dev_frames(store.root, frozen)
    new_frames = F.read_fold_frames(store.root, new)

    _assert_equal_frames(
        new_frames[T.INPUT_DAILY_TOTAL_RETURN],
        frozen_frames[T.INPUT_DAILY_TOTAL_RETURN],
        _LONG_KEYS,
    )
    _assert_equal_frames(
        new_frames[T.INPUT_TOTAL_RETURN_INDEX],
        frozen_frames[T.INPUT_TOTAL_RETURN_INDEX],
        _LONG_KEYS,
    )
    _assert_equal_frames(
        new_frames[T.INPUT_DOLLAR_VOLUME],
        frozen_frames[T.INPUT_DOLLAR_VOLUME],
        _LONG_KEYS,
    )
    _assert_equal_frames(
        new_frames["segments"], frozen_frames["segments"], _SEGMENT_KEYS
    )

    assert new["mask_counts"] == frozen["mask_counts"]
    assert new["dsc_max_query_cutoff"] == frozen["dsc_max_query_cutoff"]
    assert new["grid"]["columns"] == frozen["grid"]["columns"]
    assert new["grid"]["columns_sha256"] == frozen["grid"]["columns_sha256"]
    assert new["grid"]["row_count"] == frozen["grid"]["row_count"]
    assert new["grid"]["first_row"] == frozen["grid"]["first_row"]
    assert new["grid"]["last_row"] == frozen["grid"]["last_row"]
    assert new["capabilities"] == frozen["capabilities"]
    assert new["fold"] == F._fold_payload(_dev_fold())


def test_dev_fold_per_file_bytes_identical(tmp_path: Path) -> None:
    store = _standard_store(tmp_path)
    bindings = store.build_development()
    frozen = T.build_trusted_inputs(
        store.root, bindings=bindings, calendar=FX.CAL
    )
    new = F.build_trusted_fold(
        store.root, fold=_dev_fold(),
        bindings=bindings, calendar=FX.CAL,
    )
    frozen_dir = _artifact_dir(store.root, frozen)
    new_dir = _artifact_dir(store.root, new)
    names = [T._INPUT_FILES[n] for n in T.INPUT_NAMES] + [T.SEGMENTS_FILE]
    for name in names:
        assert (
            frozen_dir / name
        ).read_bytes() == (new_dir / name).read_bytes(), name
    # The per-file content hashes equal the frozen artifact's (the dict keys
    # differ only by the fold-specific ti_id path prefix).
    assert sorted(new["files_sha256"].values()) == sorted(
        frozen["files_sha256"].values()
    )


def test_ti_hash_is_internally_consistent(tmp_path: Path) -> None:
    store = _standard_store(tmp_path)
    bindings = store.build_development()
    new = F.build_trusted_fold(
        store.root, fold=_dev_fold(),
        bindings=bindings, calendar=FX.CAL,
    )
    directory = _artifact_dir(store.root, new)
    recomputed = {
        relative: T._sha256_file(directory / Path(relative).name)
        for relative in new["files_sha256"]
    }
    assert recomputed == new["files_sha256"]
    assert new["ti_hash"] == T._sha256_hex(
        T.canonical_json(recomputed).encode("utf-8")
    )


# ===========================================================================
# 2. Fold window validation and geometry
# ===========================================================================


def test_fold_window_validation() -> None:
    with pytest.raises(ValueError):
        F.FoldWindow(date(2023, 1, 2), date(2023, 1, 1))
    with pytest.raises(ValueError):
        F.FoldWindow(date(2020, 1, 2), date(2023, 1, 1), "other")
    # A reserve FoldWindow is refused at construction without a grant.
    with pytest.raises(F.ReserveAccessDenied):
        F.FoldWindow(date(2023, 7, 2), date(2023, 12, 29), F.RESERVE)
    with pytest.raises(F.ReserveAccessDenied):
        F.FoldWindow.reserve(date(2023, 12, 29), grant=None)
    dev = _dev_fold()
    assert dev.source == F.DEVELOPMENT
    res = _reserve_fold(date(2023, 9, 29))
    assert res.source == F.RESERVE
    assert res.grant is not None
    assert res.start == F.RESERVE_START
    # The fold owns the D-SC cutoff: dev inherits the frozen firewall date,
    # reserve may query up to its end.
    assert dev.dsc_max_cutoff == T.LAST_SOURCE_DATE
    assert res.dsc_max_cutoff == date(2023, 9, 29)


def test_fold_grid_geometry() -> None:
    dev = F._fold_grid(FX.CAL, _dev_fold())
    assert dev.grid_sessions[0] == F.CALENDAR_START
    assert dev.grid_sessions[-1] == F.DEVELOPMENT_END
    assert dev.source_end == T.LAST_SOURCE_DATE
    assert dev.interval_floor == FX.SESSIONS[0]

    res = F._fold_grid(
        FX.CAL, _reserve_fold(date(2023, 9, 29))
    )
    assert res.grid_sessions[0] == F.RESERVE_START
    assert res.grid_sessions[-1] == date(2023, 9, 29)
    assert res.source_end == FX.prev_session(date(2023, 9, 29))
    start_idx = FX.POS[F.RESERVE_START]
    assert res.interval_floor == FX.SESSIONS[start_idx - F.LOOKBACK_SESSIONS]
    assert res.interval_floor < F.RESERVE_START


def test_empty_fold_is_refused(tmp_path: Path) -> None:
    store = _standard_store(tmp_path)
    bindings = store.build_development()
    # A weekend with no NYSE session.
    fold = F.FoldWindow(date(2023, 7, 8), date(2023, 7, 9))
    with pytest.raises(F.TrustedFoldInputError):
        F.build_trusted_fold(
            store.root, fold=fold, bindings=bindings, calendar=FX.CAL
        )


# ===========================================================================
# 3. Bridge lag and stitching
# ===========================================================================


def test_dev_row_holds_prev_session_primitive(tmp_path: Path) -> None:
    store = FX.SyntheticStore(tmp_path)
    key = store.key("AAA", listed_start=date(2019, 1, 2))
    store.obs("AAA", date(2020, 1, 2), 10.0)
    store.obs("AAA", date(2020, 1, 3), 11.0)
    store.obs("AAA", date(2020, 1, 6), 12.1)
    store.obs("AAA", date(2020, 1, 7), 13.31)
    bindings = store.build_development()
    new = F.build_trusted_fold(
        store.root, fold=_dev_fold(),
        bindings=bindings, calendar=FX.CAL,
    )
    frames = F.read_fold_frames(store.root, new)
    row = frames[T.INPUT_DAILY_TOTAL_RETURN]
    got = _select(row, date(2020, 1, 6), key).iloc[0]
    assert got["source_session"] == date(2020, 1, 3)
    assert got["value"] == pytest.approx(0.1)


def test_reserve_fold_bridges_dev_and_reserve(tmp_path: Path) -> None:
    store = _standard_store(tmp_path)
    dev_bindings = store.build_development()
    res_bindings = store.build_reserve()
    fold = _reserve_fold(date(2023, 7, 14))
    new = F.build_trusted_fold(
        store.root,
        fold=fold,
        bindings=dev_bindings,
        calendar=FX.CAL,
        reserve_bindings=res_bindings,
        reserve_accessor=store.reserve_accessor(),
    )
    frames = F.read_fold_frames(store.root, new)
    row = frames[T.INPUT_DAILY_TOTAL_RETURN]
    aaa = "TIINGO:AAA:2019-01-02"

    # The first reserve row carries the development session 2023-06-30.
    first = _select(row, date(2023, 7, 3), aaa).iloc[0]
    assert first["source_session"] == date(2023, 6, 30)

    # The next row (2023-07-04 is a holiday) carries the reserve session
    # 2023-07-03, whose return spans the dev/reserve boundary.
    second = _select(row, date(2023, 7, 5), aaa).iloc[0]
    assert second["source_session"] == date(2023, 7, 3)
    # dev close on 2023-06-30 is 13.5; reserve close on 2023-07-03 is 14.0.
    assert second["value"] == pytest.approx(14.0 / 13.5 - 1.0)

    # Every output row_date is inside the reserve fold.
    for name in T.INPUT_NAMES:
        frame = frames[name]
        out_dates = pd.to_datetime(frame["row_date"]).dt.date
        assert bool((out_dates >= F.RESERVE_START).all())
        assert bool((out_dates <= date(2023, 7, 14)).all())


def test_reserve_lookback_carries_dev_history(tmp_path: Path) -> None:
    """The reserve fold must reach back into development for TRI/returns."""
    store = FX.SyntheticStore(tmp_path)
    key = store.key("AAA", listed_start=date(2019, 1, 2))
    store.add_sessions("AAA", date(2019, 1, 2), date(2023, 6, 30), close=10.0)
    store.add_sessions(
        "AAA", date(2023, 7, 3), date(2023, 7, 14), close=11.0
    )
    dev_bindings = store.build_development()
    res_bindings = store.build_reserve()
    new = F.build_trusted_fold(
        store.root,
        fold=_reserve_fold(date(2023, 7, 14)),
        bindings=dev_bindings,
        calendar=FX.CAL,
        reserve_bindings=res_bindings,
        reserve_accessor=store.reserve_accessor(),
    )
    frames = F.read_fold_frames(store.root, new)
    # The TRI level carried into the reserve is the dev segment's level (1.0
    # anchor compounded from 2019); it is not reset at the boundary and it is
    # present (not segment-masked).
    tri = frames[T.INPUT_TOTAL_RETURN_INDEX]
    got = _select(tri, date(2023, 7, 3), key).iloc[0]
    assert got["source_session"] == date(2023, 6, 30)
    assert got["value"] == pytest.approx(1.0)
    # One session later the TRI uses the reserve close, still in segment 1.
    later = _select(tri, date(2023, 7, 5), key).iloc[0]
    assert later["source_session"] == date(2023, 7, 3)
    assert later["value"] == pytest.approx(1.1)


def test_reserve_corporate_action_applied_within_fold(tmp_path: Path) -> None:
    store = FX.SyntheticStore(tmp_path)
    key = store.key("AAA", listed_start=date(2019, 1, 2))
    store.add_sessions("AAA", date(2019, 1, 2), date(2023, 6, 30), close=10.0)
    store.add_sessions(
        "AAA", date(2023, 7, 3), date(2023, 7, 14), close=10.0
    )
    store.ca("AAA", date(2023, 7, 7), factor=2.0)
    dev_bindings = store.build_development()
    res_bindings = store.build_reserve()
    new = F.build_trusted_fold(
        store.root,
        fold=_reserve_fold(date(2023, 7, 14)),
        bindings=dev_bindings,
        calendar=FX.CAL,
        reserve_bindings=res_bindings,
        reserve_accessor=store.reserve_accessor(),
    )
    frames = F.read_fold_frames(store.root, new)
    row = frames[T.INPUT_DAILY_TOTAL_RETURN]
    # Row 2023-07-10 carries source session 2023-07-07 (the CA session):
    # raw return 0, adjusted by factor 2 -> 1.0.
    got = _select(row, date(2023, 7, 10), key).iloc[0]
    assert got["source_session"] == date(2023, 7, 7)
    assert got["value"] == pytest.approx(1.0)


def test_stitched_accessor_splits_and_concatenates(tmp_path: Path) -> None:
    store = _standard_store(tmp_path)
    dev_bindings = store.build_development()
    res_bindings = store.build_reserve()
    from smart_beta.dataset_v1.reserve_firewall import DevelopmentAccessor

    dev = DevelopmentAccessor(
        store.root, bindings=T._accessor_bindings(dev_bindings)
    )
    reserve = store.reserve_accessor()
    stitched = F.StitchedAccessor(dev, reserve)
    frame = stitched.read(
        "observations",
        start=date(2023, 6, 26),
        end=date(2023, 7, 7),
        columns=list(F._OBSERVATION_COLUMNS),
    )
    dates = sorted(pd.to_datetime(frame["date"]).dt.date.unique())
    assert date(2023, 6, 30) in dates  # development side
    assert date(2023, 7, 3) in dates  # reserve side
    assert date(2023, 7, 7) in dates

    # A purely reserve range goes to the reserve package only.
    only_res = stitched.read(
        "observations",
        start=date(2023, 7, 3),
        end=date(2023, 7, 7),
        columns=list(F._OBSERVATION_COLUMNS),
    )
    res_dates = sorted(pd.to_datetime(only_res["date"]).dt.date.unique())
    assert res_dates[0] == date(2023, 7, 3)
    assert res_dates[-1] == date(2023, 7, 7)

    # A purely development range never touches the reserve package.
    only_dev = stitched.read(
        "observations",
        start=date(2023, 6, 1),
        end=date(2023, 6, 30),
        columns=list(F._OBSERVATION_COLUMNS),
    )
    assert max(pd.to_datetime(only_dev["date"]).dt.date) == date(
        2023, 6, 30
    )


# ===========================================================================
# 4. Fail-closed gates
# ===========================================================================


def test_reserve_fold_requires_bindings_and_accessor(tmp_path: Path) -> None:
    store = _standard_store(tmp_path)
    dev_bindings = store.build_development()
    fold = _reserve_fold(date(2023, 7, 14))
    with pytest.raises(F.TrustedFoldInputError):
        F.build_trusted_fold(
            store.root, fold=fold, bindings=dev_bindings, calendar=FX.CAL
        )
    res_bindings = store.build_reserve()
    from smart_beta.dataset_v1.reserve_firewall import DevelopmentAccessor

    dev_accessor = DevelopmentAccessor(
        store.root, bindings=T._accessor_bindings(dev_bindings)
    )
    # An explicit (development) accessor skips the first gate; the reserve
    # accessor gate must still refuse.
    with pytest.raises(F.TrustedFoldInputError):
        F.build_trusted_fold(
            store.root,
            fold=fold,
            bindings=dev_bindings,
            calendar=FX.CAL,
            accessor=dev_accessor,
            reserve_bindings=res_bindings,
        )


def _forged_grantless_reserve_fold(end: date):
    """A reserve ``FoldWindow`` crafted past R7-U's construction gate."""
    fold = object.__new__(F.FoldWindow)
    object.__setattr__(fold, "start", F.RESERVE_START)
    object.__setattr__(fold, "end", end)
    object.__setattr__(fold, "source", F.RESERVE)
    object.__setattr__(fold, "grant", None)
    return fold


def test_reserve_without_grant_denied_before_any_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _standard_store(tmp_path)
    dev_bindings = store.build_development()
    res_bindings = store.build_reserve()
    reserve_accessor = store.reserve_accessor()

    # A file-open audit records every read path.
    opened: list[str] = []
    real_read_bytes = Path.read_bytes
    real_read_parquet = pd.read_parquet

    def spy_read_bytes(self):
        opened.append(str(self))
        return real_read_bytes(self)

    def spy_read_parquet(path, *args, **kwargs):
        opened.append(str(path))
        return real_read_parquet(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", spy_read_bytes)
    monkeypatch.setattr(pd, "read_parquet", spy_read_parquet)

    fold = _forged_grantless_reserve_fold(date(2023, 7, 14))
    with pytest.raises(F.ReserveAccessDenied):
        F.build_trusted_fold(
            store.root,
            fold=fold,
            bindings=dev_bindings,
            calendar=FX.CAL,
            reserve_bindings=res_bindings,
            reserve_accessor=reserve_accessor,
        )
    # Refused before any path is opened: no document, no stage manifest and
    # no reserve path.
    assert opened == []
    # The normal constructor gate also refuses (R7-U's FoldWindow).
    with pytest.raises(F.ReserveAccessDenied):
        F.FoldWindow.reserve(date(2023, 7, 14), grant=None)


def test_dev_fold_post_load_assertion_refuses_reserve_row() -> None:
    # The development accessor filters reserve rows before returning them; the
    # fold's post-load assertion is the defense-in-depth check for rows that
    # slip through (a leaky accessor).  Exercise it directly.
    import pyarrow as pa

    def _flag_frame(dates):
        return pd.DataFrame(
            {
                "ticker": pd.Series(
                    ["AAA"] * len(dates),
                    dtype=pd.ArrowDtype(pa.string()),
                ),
                "date": pd.Series(
                    dates, dtype=pd.ArrowDtype(pa.date32())
                ),
                "raw_row_index": pd.Series(
                    list(range(len(dates))), dtype="int64"
                ),
                "class_or_flag": pd.Series(
                    ["NONPOSITIVE_PRICE"] * len(dates),
                    dtype=pd.ArrowDtype(pa.string()),
                ),
                "disposition": pd.Series(
                    ["CORRUPT"] * len(dates),
                    dtype=pd.ArrowDtype(pa.string()),
                ),
            }
        )

    empty = pd.DataFrame(
        {
            "stock_id": pd.Series([], dtype=pd.ArrowDtype(pa.string())),
            "effective_date": pd.Series(
                [], dtype=pd.ArrowDtype(pa.date32())
            ),
            "action_type": pd.Series([], dtype=pd.ArrowDtype(pa.string())),
            "knowledge_date": pd.Series(
                [], dtype=pd.ArrowDtype(pa.date32())
            ),
            "adjustment_factor": pd.Series([], dtype="float64"),
            "is_superseded": pd.Series([], dtype=bool),
        }
    )
    inputs = T._Inputs(
        observations=pd.DataFrame(
            {
                "ticker": pd.Series([], dtype=pd.ArrowDtype(pa.string())),
                "date": pd.Series([], dtype=pd.ArrowDtype(pa.date32())),
                "close": pd.Series([], dtype="float64"),
                "volume": pd.Series([], dtype="Int64"),
                "raw_row_index": pd.Series([], dtype="int64"),
            }
        ),
        ca_events=empty,
        identity=pd.DataFrame(),
        day_flags=_flag_frame(
            [T.RESERVE_START]
        ),
        class_timeline={},
        policy_timeline={},
    )
    with pytest.raises(F.ReserveFirewallError):
        F._post_load_assertions(
            inputs, _dev_fold(), T.LAST_SOURCE_DATE
        )


def test_duplicate_usable_rows_fail_closed(tmp_path: Path) -> None:
    store = FX.SyntheticStore(tmp_path)
    store.key("AAA", listed_start=date(2019, 1, 2))
    store.obs("AAA", date(2020, 1, 2), 10.0)
    store.obs("AAA", date(2020, 1, 2), 10.5)
    bindings = store.build_development()
    with pytest.raises(F.TrustedFoldConsistencyError):
        F.build_trusted_fold(
            store.root,
            fold=_dev_fold(),
            bindings=bindings,
            calendar=FX.CAL,
        )


# ===========================================================================
# 5. No network / no credential read
# ===========================================================================


def test_module_makes_no_network_or_credential_import() -> None:
    source = (
        _REPO_ROOT
        / "smart_beta/dataset_v1/reserve_chain/trusted_fold.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = (
        "requests",
        "urllib",
        "httpx",
        "anthropic",
        "tushare",
        "tiingo",
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not any(
                    alias.name == name or alias.name.startswith(name + ".")
                    for name in forbidden
                )
        if isinstance(node, ast.ImportFrom):
            assert not any(
                node.module == name
                or (node.module or "").startswith(name + ".")
                for name in forbidden
            )
    for marker in ("API_KEY", "TOKEN", "SECRET", "os.getenv"):
        assert marker not in source


# ===========================================================================
# 6. Mutation fixture contract
# ===========================================================================


def test_mutation_fixture_contract() -> None:
    mutations = json.loads(_MUTATIONS.read_text(encoding="utf-8"))
    assert [m["id"] for m in mutations] == [
        *(f"T-M{i}" for i in range(1, 8)),
        "T7-M_gate",
    ]
    for mutation in mutations:
        assert mutation["edits"], mutation["id"]
        assert mutation["file"] == (
            "smart_beta/dataset_v1/reserve_chain/trusted_fold.py"
        )
        source = (_REPO_ROOT / mutation["file"]).read_text(encoding="utf-8")
        for edit in mutation["edits"]:
            assert edit["old"] and edit["new"] is not None
            assert source.count(edit["old"]) == 1, mutation["id"]
            assert edit["old"] != edit["new"], mutation["id"]
        module_name, _, test_name = mutation["test"].partition("::")
        assert test_name, mutation["id"]
        module_source = (_REPO_ROOT / module_name).read_text(
            encoding="utf-8"
        )
        assert f"def {test_name.split('[')[0]}" in module_source, mutation["id"]
