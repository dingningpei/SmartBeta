"""R7-U fold-generic D6 universe tests.

Every test builds a synthetic ``tmp_path`` store via
``tests/fixtures/reserve_chain/universe/synthetic.py`` with injected
``universe.Bindings``; none touches the real ``data_store``,
``reserve_sealed`` or any credential.  An autouse socket guard and credential
sentinel mirror the D6/D7c suites.

The central proof is byte-equivalence with the frozen
``universe.run_universe`` on a development fold.  The reserve-path tests prove
the fold window, the development-lookback stitching and the ACCESS gate.
"""

from __future__ import annotations

import importlib.util
import json
import socket
import sys
from datetime import date
from pathlib import Path
from typing import Iterable

import pandas as pd
import pytest

from smart_beta.dataset_v1 import universe as U
from smart_beta.dataset_v1.reserve_chain import universe_fold as UF
from smart_beta.dataset_v1.reserve_chain.fold import (
    DEVELOPMENT,
    AccessGrant,
    FoldWindow,
    ReserveAccessDenied,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "reserve_chain" / "universe" / "synthetic.py"
)
_MUTATIONS_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "reserve_chain" / "universe" / "mutations.json"
)
CREDENTIAL_SENTINEL = "r7u-credential-sentinel-4c2d"


def _load_synthetic():
    name = "r7u_universe_synthetic"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _FIXTURE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


SYN = _load_synthetic()


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch):
    def _refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("R7-U tests must never open a socket")

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


def _fold_f(f: date) -> FoldWindow:
    """A single-formation development fold (fast, same D6 rule)."""
    return FoldWindow(start=f, end=f, source=DEVELOPMENT)


def _out(store, content: dict, filename: str) -> Path:
    return store.root / "derived" / "universe" / content["universe_id"] / filename


def _read(store, content: dict, filename: str) -> pd.DataFrame:
    return pd.read_parquet(_out(store, content, filename))


def _audit_for(store, content: dict, f: date) -> pd.DataFrame:
    audit = _read(store, content, "audit_development.parquet")
    return audit[audit["formation_date"] == f].reset_index(drop=True)


def _row_for(frame: pd.DataFrame, security_key: str):
    rows = frame[frame["security_key"] == security_key]
    assert len(rows) == 1, f"expected one row for {security_key}, got {len(rows)}"
    return rows.iloc[0]


def _run_fold(store, bindings, fold: FoldWindow) -> dict:
    return UF.run_universe_fold(
        store.root, fold=fold, bindings=bindings, calendar=SYN.CALENDAR
    )


def _dev_store(tmp_path: Path, name: str, keys: Iterable[str]):
    store = SYN.make_store(tmp_path, name)
    for ticker in keys:
        store.healthy(ticker)
    built = store.build()
    return store, built["development"]


def _reserve_store(
    tmp_path: Path,
    name: str = "reserve",
    *,
    f: date = SYN.RESERVE_FORMATION,
    age: int = 300,
    ticker: str = "RES",
    inject_pre_start: bool = False,
    post_end_row: date | None = None,
):
    store = SYN.make_store(tmp_path, name)
    start = SYN.start_for_age(f, age)
    sk = store.key(ticker, listed_start=start)
    store.add_sessions(ticker, start, SYN.cutoff(f))
    if post_end_row is not None:
        idx = store.obs(ticker, post_end_row, 10.0, 100)
        assert idx >= 0
    if inject_pre_start:
        store.res_obs.append((ticker, start, 10.0, 100, 10**9))
    built = store.build(with_reserve=True)
    return store, built, sk, start


# ---------------------------------------------------------------------------
# 1. The equivalence proof: development fold == frozen run_universe
# ---------------------------------------------------------------------------


def test_dev_fold_reproduces_frozen_run_universe(tmp_path: Path) -> None:
    """The fold-generic development path is byte- and hash-equivalent."""
    a, bindings_a = _dev_store(tmp_path, "a", ("AAA", "BBB", "CCC"))
    b, bindings_b = _dev_store(tmp_path, "b", ("AAA", "BBB", "CCC"))

    frozen = U.run_universe(a.root, bindings=bindings_a, calendar=SYN.CALENDAR)
    folded = UF.run_universe_fold(
        b.root,
        fold=FoldWindow.development(),
        bindings=bindings_b,
        calendar=SYN.CALENDAR,
    )

    # Identity hashes are recomputed identically.
    assert folded["universe_id"] == frozen["universe_id"]
    assert folded["universe_code_sha256"] == frozen["universe_code_sha256"]
    assert folded["universe_hash"] == frozen["universe_hash"]
    assert folded["files_sha256"] == frozen["files_sha256"]
    assert folded == frozen

    key_columns = {
        "universe_development.parquet": ["formation_date", "security_key"],
        "formations.parquet": ["formation_date"],
        "audit_development.parquet": ["formation_date", "security_key"],
    }
    for filename, keys in key_columns.items():
        left = _read(a, frozen, filename).sort_values(keys, kind="mergesort")
        right = _read(b, folded, filename).sort_values(keys, kind="mergesort")
        pd.testing.assert_frame_equal(
            left.reset_index(drop=True),
            right.reset_index(drop=True),
            check_exact=True,
            check_dtype=True,
        )
        # Byte equality proves the content-based identity hash is reproduced.
        assert (
            _out(a, frozen, filename).read_bytes()
            == _out(b, folded, filename).read_bytes()
        )
    assert (
        _out(a, frozen, "universe.json").read_bytes()
        == _out(b, folded, "universe.json").read_bytes()
    )


def test_dev_fold_matches_frozen_partition_and_deferral(tmp_path: Path) -> None:
    store, bindings = _dev_store(tmp_path, "dev", ("AAA",))
    frozen = U.run_universe(store.root, bindings=bindings, calendar=SYN.CALENDAR)
    formations = pd.read_parquet(
        _out(store, frozen, "formations.parquet")
    )
    development = formations[formations["partition"] == "DEVELOPMENT"]
    reserve = formations[formations["partition"] == "RESERVE"]
    assert len(development) == U.N_DEVELOPMENT
    assert len(reserve) == U.N_RESERVE
    assert (reserve["status"] == U.STATUS_DEFERRED).all()
    assert frozen["gate_11"]["reserve_gate11"] == UF.DEFERRED_GATE11


def test_dev_fold_refuses_reserve_dated_row(tmp_path: Path) -> None:
    store = SYN.make_store(tmp_path, "leak")
    store.healthy("AAA")
    # A development package that (wrongly) carries a reserve-dated row.
    store.dev_obs.append(("AAA", U.RESERVE_START, 10.0, 100, 10**9))
    built = store.build()
    with pytest.raises(U.ReserveFirewallError):
        _run_fold(store, built["development"], FoldWindow.development())
    assert not (store.root / "derived" / "universe").exists()


# ---------------------------------------------------------------------------
# 2. ACCESS gate
# ---------------------------------------------------------------------------


def test_reserve_fold_requires_access_grant(tmp_path: Path) -> None:
    # Construction fails closed before any path is opened (root need not exist).
    missing = tmp_path / "does-not-exist"
    assert not missing.exists()
    with pytest.raises(ReserveAccessDenied):
        FoldWindow.reserve(SYN.RESERVE_FORMATION, grant=None)  # type: ignore[arg-type]
    with pytest.raises(ReserveAccessDenied):
        FoldWindow(
            start=U.RESERVE_START,
            end=SYN.RESERVE_FORMATION,
            source="reserve",
            grant=None,
        )
    # A grant makes it constructible.
    fold = FoldWindow.reserve(SYN.RESERVE_FORMATION, grant=AccessGrant())
    assert fold.source == "reserve"


def test_reserve_requires_lookback_bindings(tmp_path: Path) -> None:
    store, built, _sk, _start = _reserve_store(tmp_path)
    fold = FoldWindow.reserve(SYN.RESERVE_FORMATION, grant=AccessGrant())
    with pytest.raises(U.UniverseInputError):
        UF.run_universe_fold(
            store.root,
            fold=fold,
            bindings=built["reserve"],
            lookback_bindings=None,
            calendar=SYN.CALENDAR,
        )
    # A development fold must not carry lookback bindings.
    with pytest.raises(U.UniverseInputError):
        UF.run_universe_fold(
            store.root,
            fold=FoldWindow.development(),
            bindings=built["development"],
            lookback_bindings=built["lookback"],
            calendar=SYN.CALENDAR,
        )


# ---------------------------------------------------------------------------
# 3. Reserve fold: package source, lookback stitching, partitions
# ---------------------------------------------------------------------------


def test_reserve_fold_lookback_stitching(tmp_path: Path) -> None:
    store, built, sk, start = _reserve_store(tmp_path)
    fold = FoldWindow.reserve(SYN.RESERVE_FORMATION, grant=AccessGrant())
    content = UF.run_universe_fold(
        store.root,
        fold=fold,
        bindings=built["reserve"],
        lookback_bindings=built["lookback"],
        calendar=SYN.CALENDAR,
    )

    # The source packages are the reserve ones; lookback is development.
    assert content["bindings"]["D3"]["manifest_path"].startswith("reserve_sealed/")
    assert content["bindings"]["D5"]["manifest_path"].startswith("reserve_sealed/")
    assert content["lookback_bindings"]["D3"]["manifest_path"].startswith("normalized/")

    row = _row_for(_audit_for(store, content, SYN.RESERVE_FORMATION), sk)
    # The 300-session listing age and the 63-session liquidity window span the
    # boundary, so both can only be right if the development lookback is used.
    assert row["listing_age_sessions"] == 300
    assert row["missing_denominator"] == 300
    assert row["trailing_valid_sessions"] == 63
    assert row["reason"] == "ELIGIBLE"

    formations = _read(store, content, "formations.parquet")
    evaluated = formations[formations["formation_date"] == SYN.RESERVE_FORMATION]
    assert len(evaluated) == 1
    assert evaluated.iloc[0]["partition"] == "RESERVE"
    assert evaluated.iloc[0]["n_eligible"] == 1
    # Formations outside the reserve window are deferred on the other partition.
    outside = formations[formations["formation_date"] == date(2016, 1, 29)]
    assert outside.iloc[0]["partition"] == "DEVELOPMENT"
    assert outside.iloc[0]["status"] == U.STATUS_DEFERRED
    assert content["gate_11"]["development"] == UF.DEFERRED_GATE11


def test_reserve_fold_rejects_lookback_in_reserve_package(tmp_path: Path) -> None:
    store, built, _sk, _start = _reserve_store(tmp_path, inject_pre_start=True)
    fold = FoldWindow.reserve(SYN.RESERVE_FORMATION, grant=AccessGrant())
    with pytest.raises(UF.FoldConsistencyError):
        UF.run_universe_fold(
            store.root,
            fold=fold,
            bindings=built["reserve"],
            lookback_bindings=built["lookback"],
            calendar=SYN.CALENDAR,
        )


def test_reserve_fold_rejects_post_end_row(tmp_path: Path) -> None:
    post = date(2023, 8, 1)
    store, built, _sk, _start = _reserve_store(tmp_path, post_end_row=post)
    fold = FoldWindow.reserve(SYN.RESERVE_FORMATION, grant=AccessGrant())
    with pytest.raises(U.ReserveFirewallError):
        UF.run_universe_fold(
            store.root,
            fold=fold,
            bindings=built["reserve"],
            lookback_bindings=built["lookback"],
            calendar=SYN.CALENDAR,
        )


def test_run_reserve_universe_convenience(tmp_path: Path) -> None:
    store, built, sk, _start = _reserve_store(tmp_path)
    content = UF.run_reserve_universe(
        store.root,
        end=SYN.RESERVE_FORMATION,
        grant=AccessGrant(hypothesis_id="H-R7U"),
        bindings=built["reserve"],
        lookback_bindings=built["lookback"],
        calendar=SYN.CALENDAR,
    )
    row = _row_for(_audit_for(store, content, SYN.RESERVE_FORMATION), sk)
    assert row["reason"] == "ELIGIBLE"


# ---------------------------------------------------------------------------
# 4. FoldResolver window handling
# ---------------------------------------------------------------------------


def test_fold_resolver_window_and_allowlist(tmp_path: Path) -> None:
    store, built, _sk, _start = _reserve_store(tmp_path)
    fold = FoldWindow.reserve(SYN.RESERVE_FORMATION, grant=AccessGrant())
    resolver = UF.FoldResolver(store.root, built["reserve"], fold)
    reserve_rel = built["reserve"].d3.files[0].relative_path
    assert resolver.resolve(reserve_rel) == store.root / reserve_rel
    resolver.resolve(reserve_rel, formation_date=SYN.RESERVE_FORMATION)
    with pytest.raises(U.ReserveFirewallError):
        resolver.resolve(reserve_rel, formation_date=date(2023, 9, 1))
    with pytest.raises(U.ReserveFirewallError):
        resolver.resolve("normalized/not-allowlisted.parquet")
    with pytest.raises(U.UniverseInputError):
        resolver.resolve("/tmp/absolute.parquet")
    with pytest.raises(U.UniverseInputError):
        resolver.resolve("../escape.parquet")


# ---------------------------------------------------------------------------
# 5. Edge cases of the frozen D6 module, re-checked on the fold path
# ---------------------------------------------------------------------------


def test_listing_age_boundary(tmp_path: Path) -> None:
    for age, expected in ((251, "LISTING_AGE_INSUFFICIENT"), (252, "ELIGIBLE")):
        store = SYN.make_store(tmp_path, f"age-{age}")
        start = SYN.start_for_age(SYN.F1, age)
        sk = store.key("AAA", listed_start=start)
        store.add_sessions("AAA", start, SYN.cutoff(SYN.F1))
        built = store.build()
        content = _run_fold(store, built["development"], _fold_f(SYN.F1))
        row = _row_for(_audit_for(store, content, SYN.F1), sk)
        assert row["listing_age_sessions"] == age
        assert row["reason"] == expected


def _omit_sessions(
    store, ticker: str, f: date, count: int, *, age: int = 300
) -> str:
    start = SYN.start_for_age(f, age)
    sk = store.key(ticker, listed_start=start)
    omitted = set(SYN.window(f)[-count:]) if count else set()
    store.add_sessions(ticker, start, SYN.cutoff(f), skip=omitted)
    return sk


def test_liquidity_threshold(tmp_path: Path) -> None:
    for count, expected in ((14, "TRAILING_LIQUIDITY_INSUFFICIENT"), (13, "ELIGIBLE")):
        store = SYN.make_store(tmp_path, f"liq-{count}")
        sk = _omit_sessions(store, "AAA", SYN.FL, count)
        built = store.build()
        content = _run_fold(store, built["development"], _fold_f(SYN.FL))
        row = _row_for(_audit_for(store, content, SYN.FL), sk)
        assert row["trailing_valid_sessions"] == 63 - count
        assert row["reason"] == expected


def test_missingness_five_percent_boundary(tmp_path: Path) -> None:
    store = SYN.make_store(tmp_path, "missingness")
    start = SYN.start_for_age(SYN.F1, 260)
    sk = store.key("AAA", listed_start=start)
    omitted = set(SYN.sessions(start, SYN.cutoff(SYN.F1))[1:14])
    store.add_sessions("AAA", start, SYN.cutoff(SYN.F1), skip=omitted)
    built = store.build()
    content = _run_fold(store, built["development"], _fold_f(SYN.F1))
    row = _row_for(_audit_for(store, content, SYN.F1), sk)
    assert row["missing_numerator"] == 13
    assert row["missing_denominator"] == 260
    assert row["reason"] == "ELIGIBLE"  # exactly 5% passes


def test_dv_tie_resolved_by_security_key(tmp_path: Path) -> None:
    store = SYN.make_store(tmp_path, "ties")
    start = SYN.start_for_age(SYN.F1, 260)
    for i in reversed(range(501)):
        ticker = f"T{i:04d}"
        store.key(ticker, listed_start=start)
        store.add_sessions(ticker, start, SYN.cutoff(SYN.F1))
    built = store.build()
    content = _run_fold(store, built["development"], _fold_f(SYN.F1))
    development = _read(store, content, "universe_development.parquet")
    f1 = development[development["formation_date"] == SYN.F1].sort_values("rank")
    assert len(f1) == 501
    assert f1.iloc[499]["security_key"] == f"TIINGO:T0499:{start.isoformat()}"
    assert f1.iloc[500]["security_key"] == f"TIINGO:T0500:{start.isoformat()}"
    assert int(f1["top_200"].sum()) == 200
    assert int(f1["top_500"].sum()) == 500
    assert int(f1["top_1000"].sum()) == 501
    assert bool((f1.loc[f1["top_200"], "top_500"]).all())
    assert bool((f1.loc[f1["top_500"], "top_1000"]).all())


def test_top_1000_cap_excludes_rank_1001(tmp_path: Path) -> None:
    store = SYN.make_store(tmp_path, "cap")
    start = SYN.start_for_age(SYN.F1, 260)
    for i in range(1001):
        ticker = f"C{i:04d}"
        store.key(ticker, listed_start=start)
        store.add_sessions(ticker, start, SYN.cutoff(SYN.F1))
    built = store.build()
    content = _run_fold(store, built["development"], _fold_f(SYN.F1))
    development = _read(store, content, "universe_development.parquet")
    f1 = development[development["formation_date"] == SYN.F1].sort_values("rank")
    assert len(f1) == 1000
    assert f1["rank"].max() == 1000
    assert f"TIINGO:C1000:{start.isoformat()}" not in set(f1["security_key"])
    assert bool(f1["top_1000"].all())


def test_duplicate_with_interval_assigned_fails_closed(tmp_path: Path) -> None:
    start = SYN.start_for_age(SYN.F1, 260)
    store = SYN.make_store(tmp_path, "dup-fail")
    store.identity_row(
        "DUP",
        security_key="TIINGO:DUP:x",
        state="IDENTITY_AMBIGUOUS",
        listed_start=start,
        listed_end=date(2099, 1, 1),
        reason="OVERLAPPING_LISTED_ROWS",
    )
    store.identity_row(
        "DUP",
        security_key="TIINGO:DUP:x",
        state="INTERVAL_ASSIGNED",
        listed_start=start,
        listed_end=date(2099, 1, 1),
    )
    built = store.build()
    with pytest.raises(U.D6ConsistencyError):
        _run_fold(store, built["development"], FoldWindow.development())


def test_duplicate_all_inadmissible_collapses(tmp_path: Path) -> None:
    start = SYN.start_for_age(SYN.F1, 260)
    store = SYN.make_store(tmp_path, "dup-collapse")
    for _ in range(2):
        store.identity_row(
            "DUP",
            security_key="TIINGO:DUP:x",
            state="IDENTITY_AMBIGUOUS",
            listed_start=start,
            listed_end=date(2099, 1, 1),
            reason="OVERLAPPING_LISTED_ROWS",
        )
    store.add_sessions("DUP", start, SYN.cutoff(SYN.F1))
    built = store.build()
    content = _run_fold(store, built["development"], _fold_f(SYN.F1))
    audit = _audit_for(store, content, SYN.F1)
    assert len(audit[audit["security_key"] == "TIINGO:DUP:x"]) == 1
    row = _row_for(audit, "TIINGO:DUP:x")
    assert row["reason"] == "IDENTITY_NOT_ADMISSIBLE"
    assert row["duplicate_row_count"] == 2


def test_reason_precedence_identity_and_exchange(tmp_path: Path) -> None:
    start = SYN.start_for_age(SYN.F1, 270)
    for name, kwargs, expected in (
        ("state", {"state": "IDENTITY_AMBIGUOUS"}, "IDENTITY_NOT_ADMISSIBLE"),
        ("exchange", {"exchange": "ARCA"}, "EXCHANGE_NOT_ELIGIBLE"),
        ("class", {"class_state": None}, "NOT_COMMON_STOCK"),
        (
            "policy",
            {"policy_state": "EXCLUDED", "policy_knowledge": date(2010, 1, 4)},
            "ISSUER_POLICY_EXCLUDED",
        ),
    ):
        store = SYN.make_store(tmp_path, f"reason-{name}")
        sk = store.key("AAA", listed_start=start, **kwargs)
        store.add_sessions("AAA", start, SYN.cutoff(SYN.F1))
        built = store.build()
        content = _run_fold(store, built["development"], _fold_f(SYN.F1))
        row = _row_for(_audit_for(store, content, SYN.F1), sk)
        assert row["reason"] == expected, name


def test_future_data_invariance(tmp_path: Path) -> None:
    def build(name: str, future: bool):
        store = SYN.make_store(tmp_path, name)
        start = SYN.start_for_age(SYN.F1, 270)
        store.key("AAA", listed_start=start)
        store.add_sessions("AAA", start, SYN.cutoff(SYN.F1))
        if future:
            store.obs("AAA", SYN.CAL_DATES[SYN.CAL_DATES.index(SYN.F1) + 3], 10.0, 100)
        built = store.build()
        content = _run_fold(store, built["development"], _fold_f(SYN.F1))
        return _audit_for(store, content, SYN.F1)

    pd.testing.assert_frame_equal(build("base", False), build("future", True))


def test_determinism_and_credential_safety(tmp_path: Path) -> None:
    store, built = _dev_store(tmp_path, "det", ("AAA", "BBB"))
    first = _run_fold(store, built, _fold_f(SYN.F1))
    second = _run_fold(store, built, _fold_f(SYN.F1))
    assert first["universe_id"] == second["universe_id"]
    assert first["universe_hash"] == second["universe_hash"]
    for path in _out(store, first, "universe.json").parent.iterdir():
        if path.is_file():
            assert CREDENTIAL_SENTINEL.encode() not in path.read_bytes()


def test_adjusted_close_never_used(tmp_path: Path) -> None:
    store = SYN.make_store(tmp_path, "adjclose")
    start = SYN.start_for_age(SYN.F1, 260)
    sk = store.key("AAA", listed_start=start)
    store.add_sessions("AAA", start, SYN.cutoff(SYN.F1), close=10.0, volume=100)
    built = store.build()
    content = _run_fold(store, built["development"], _fold_f(SYN.F1))
    row = _row_for(_audit_for(store, content, SYN.F1), sk)
    # adjClose is 999999.0 in the fixture; DV must use raw close (10 * 100).
    assert row["median_dv"] == 1000.0


# ---------------------------------------------------------------------------
# 6. Mutation manifest contract
# ---------------------------------------------------------------------------


def test_mutation_fixture_contract() -> None:
    mutations = json.loads(_MUTATIONS_PATH.read_text(encoding="utf-8"))
    assert [mutation["id"] for mutation in mutations] == [
        f"R7U-M{i}" for i in range(1, len(mutations) + 1)
    ]
    for mutation in mutations:
        assert mutation["edits"], mutation["id"]
        assert mutation["file"].startswith("smart_beta/"), mutation["id"]
        source = (_REPO_ROOT / mutation["file"]).read_text(encoding="utf-8")
        for edit in mutation["edits"]:
            assert edit["old"] and edit["new"] is not None, mutation["id"]
            assert source.count(edit["old"]) == 1, mutation["id"]
            assert edit["old"] != edit["new"], mutation["id"]
        module_name, _, test_name = mutation["test"].partition("::")
        assert test_name, mutation["id"]
        module_source = (_REPO_ROOT / module_name).read_text(encoding="utf-8")
        assert f"def {test_name.split('[')[0]}" in module_source, mutation["id"]
