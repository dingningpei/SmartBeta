"""R7-R — fold-generic D7a returns/labels tests (``reserve_chain/returns_fold``).

Two kinds of proof live here:

* **Development equivalence** — the fold module is run on the *development* fold
  with the same synthetic inputs as the pinned :func:`returns.run_returns`, and
  the four output tables are compared exactly (``check_exact=True,
  check_dtype=True``) after canonical sorting.  Both sides are written to
  parquet with the same writer and read back with the same reader call, so no
  in-memory ArrowDtype frame is ever compared against a parquet-read frame.
* **Reserve-shaped folds** — synthetic reserve-window inputs exercise the
  fold-relative boundary: terminal evidence against ``fold.end`` (not
  ``DEVELOPMENT_END``), the ``h = 21`` confirmation horizon, the fold-end purge
  and the :class:`FoldWindow` ``AccessGrant`` gate.

The synthetic store is the canonical D7a one
(:class:`test_dataset_v1_returns._Store`), imported read-only, so both sides of
the equivalence see identical inputs.  No test reads ``data_store``,
``reserve_sealed`` or a credential; the autouse socket guard mirrors D7a.
"""

from __future__ import annotations

import json
import importlib.util
import os
import socket
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from smart_beta.dataset_v1 import returns as R
from smart_beta.dataset_v1.reserve_chain import returns_fold as RF
from smart_beta.dataset_v1.reserve_chain.fold import (
    RESERVE,
    FoldWindow,
    ReserveAccessDenied,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MUTATIONS_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "reserve_chain" / "returns" / "mutations.json"
)
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

# The canonical D7a synthetic store.  Imported read-only so the equivalence run
# is between two derivations of the *same* synthetic inputs.
sys.path.insert(0, str(_REPO_ROOT / "tests"))
from test_dataset_v1_returns import (  # noqa: E402
    CAL,
    F1,
    POS,
    S,
    _Store,
)

CREDENTIAL_SENTINEL = "r7r-credential-sentinel-3a91"

# -- reserve-window geometry -------------------------------------------------
_RESERVE_START = date(2023, 7, 3)
RES_F1 = S[POS[_RESERVE_START] + 5]
#: Fold end ``C``: exactly 21 trusted sessions after the reserve formation, so
#: the h = 21 endpoint equals ``C`` and h = 22 crosses it.
RES_C = S[POS[RES_F1] + 21]
RES_GRANT = _GRANTS.verified_grant(hypothesis_id="H-R7R-TEST", fold_end=RES_C)
RESERVE_FOLD = FoldWindow.reserve(RES_C, grant=RES_GRANT)

#: canonical key columns per frozen output table (spec §1 sort).
_TABLE_KEYS = {
    R.MEMBERS_FILE: ["formation_date", "rank"],
    R.HORIZONS_FILE: ["formation_date", "h"],
    R.DAILY_FILE: ["security_key", "date"],
    R.LABELS_FILE: ["formation_date", "security_key", "h"],
}


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch):
    def _refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("R7-R tests must never open a socket")

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


def _inputs(store: _Store):
    assert store.bindings is not None
    resolver = R.Resolver(store.root, store.bindings)
    return R._load_inputs(resolver, store.bindings)


_USE_INPUT_FORMATIONS = object()


def _fold_result(
    store: _Store,
    *,
    fold: FoldWindow,
    hmax: int = R.HMAX,
    formations: object = _USE_INPUT_FORMATIONS,
):
    inputs = _inputs(store)
    if formations is _USE_INPUT_FORMATIONS:
        formations = inputs.formations
    return RF.build_returns_fold(
        observations=inputs.observations,
        ca_events=inputs.ca_events,
        identity=inputs.identity,
        day_flags=inputs.day_flags,
        universe=inputs.universe,
        formations=formations,
        fold=fold,
        calendar=CAL,
        hmax=hmax,
    )


def _assert_equivalent(store: _Store, tmp_path: Path) -> None:
    """Development equivalence against ``returns.run_returns`` (parquet round-trip)."""
    content = store.run()
    ref_dir = store.root / "derived" / "returns" / content["returns_id"]
    result = _fold_result(store, fold=RF.development_fold())
    produced = {
        R.MEMBERS_FILE: result.members,
        R.HORIZONS_FILE: result.horizons,
        R.DAILY_FILE: result.daily_returns,
        R.LABELS_FILE: result.labels,
    }
    scratch = tmp_path / "fold_out"
    scratch.mkdir(parents=True, exist_ok=True)
    for filename, keycols in _TABLE_KEYS.items():
        mine_path = scratch / filename
        produced[filename].to_parquet(
            mine_path, engine="pyarrow", index=False, compression="zstd"
        )
        mine = pd.read_parquet(mine_path)
        frozen = pd.read_parquet(ref_dir / filename)
        mine = mine.sort_values(keycols, kind="mergesort", ignore_index=True)
        frozen = frozen.sort_values(keycols, kind="mergesort", ignore_index=True)
        pd.testing.assert_frame_equal(
            frozen, mine, check_exact=True, check_dtype=True
        )


# ---------------------------------------------------------------------------
# Development equivalence (amendment-A1 obligation)
# ---------------------------------------------------------------------------


def test_development_equivalence_full_window(tmp_path: Path) -> None:
    store = _Store(tmp_path, "eq_full")
    key = store.key("AAA", listed_start=S[POS[F1] - 5], listed_end=R.DEVELOPMENT_END)
    store.add_sessions("AAA", S[POS[F1] - 5], R.DEVELOPMENT_END)
    store.member(F1, key, 1, top_200=True, top_500=True)
    store.build()
    _assert_equivalent(store, tmp_path)


def test_development_equivalence_terminal_and_ca(tmp_path: Path) -> None:
    store = _Store(tmp_path, "eq_term")
    a = store.key("AAA", listed_start=S[POS[F1] - 5], listed_end=S[POS[F1] + 3])
    b = store.key("BBB", listed_start=S[POS[F1] - 5], listed_end=R.DEVELOPMENT_END)
    store.add_sessions("AAA", S[POS[F1] - 5], R.DEVELOPMENT_END)
    store.add_sessions(
        "BBB", S[POS[F1] - 5], R.DEVELOPMENT_END, skip=[S[POS[F1] + 4]]
    )
    store.ca("BBB", S[POS[F1] + 5], factor=1.25)
    store.member(F1, a, 1, top_200=True, top_500=True)
    store.member(F1, b, 2, top_500=True)
    store.build()
    _assert_equivalent(store, tmp_path)


def test_development_equivalence_flags(tmp_path: Path) -> None:
    store = _Store(tmp_path, "eq_flags")
    key = store.key("CCC", listed_start=S[POS[F1] - 5], listed_end=R.DEVELOPMENT_END)
    store.add_sessions("CCC", S[POS[F1] - 5], R.DEVELOPMENT_END)
    store.flag(
        "CCC", S[POS[F1] + 2], store.index_of("CCC", S[POS[F1] + 2]), "OHLC_INCONSISTENT"
    )
    store.flag(
        "CCC",
        S[POS[F1] + 5],
        store.index_of("CCC", S[POS[F1] + 5]),
        "CORP_ACTION_SUSPECT",
    )
    store.member(F1, key, 1)
    store.build()
    _assert_equivalent(store, tmp_path)


def test_development_equivalence_hmax_is_frozen_63(tmp_path: Path) -> None:
    store = _Store(tmp_path, "eq_hmax")
    key = store.key("DDD", listed_start=S[POS[F1] - 5], listed_end=R.DEVELOPMENT_END)
    store.add_sessions("DDD", S[POS[F1] - 5], R.DEVELOPMENT_END)
    store.member(F1, key, 1)
    store.build()
    result = _fold_result(store, fold=RF.development_fold(), hmax=R.HMAX)
    assert sorted(set(result.horizons["h"].tolist())) == list(range(1, 64))
    assert R.HMAX == 63


# ---------------------------------------------------------------------------
# Reserve-shaped folds
# ---------------------------------------------------------------------------


def _reserve_store(
    tmp_path: Path,
    *,
    name: str,
    listed_end: date,
    ticker: str = "ZR",
) -> tuple[_Store, str]:
    store = _Store(tmp_path, name=name)
    key = store.key(ticker, listed_start=S[POS[RES_F1] - 30], listed_end=listed_end)
    store.add_sessions(ticker, S[POS[RES_F1] - 30], RES_C)
    store.member(RES_F1, key, 1, top_200=True, top_500=True)
    store.build()
    return store, key


def test_reserve_member_terminal_evidence_uses_fold_end(tmp_path: Path) -> None:
    listed_end = S[POS[RES_F1] + 3]
    store, key = _reserve_store(tmp_path, name="res_term", listed_end=listed_end)
    result = _fold_result(store, fold=RESERVE_FOLD, hmax=21, formations=None)
    members = result.members
    assert len(members) == 1
    row = members.iloc[0]
    # A reserve-period delisting is within *this* fold (listed_end > DEVELOPMENT_END).
    assert row["listing_status"] == R.LISTING_ENDS_WITHIN_DEVELOPMENT
    assert row["listed_end_dev"] == listed_end


def test_reserve_terminal_affected_labels(tmp_path: Path) -> None:
    listed_end = S[POS[RES_F1] + 3]
    store, key = _reserve_store(tmp_path, name="res_term2", listed_end=listed_end)
    result = _fold_result(store, fold=RESERVE_FOLD, hmax=21, formations=None)
    labels = result.labels[result.labels["security_key"] == key].set_index("h")
    for h in range(1, 3):
        assert not bool(labels.loc[h, "terminal_affected"])
        assert int(labels.loc[h, "n_observed_sessions"]) == h
    for h in range(3, 22):
        assert bool(labels.loc[h, "terminal_affected"])
        assert int(labels.loc[h, "n_observed_sessions"]) == 3


def test_reserve_post_terminal_daily_status(tmp_path: Path) -> None:
    listed_end = S[POS[RES_F1] + 3]
    store, key = _reserve_store(tmp_path, name="res_term3", listed_end=listed_end)
    result = _fold_result(store, fold=RESERVE_FOLD, hmax=21, formations=None)
    daily = result.daily_returns[result.daily_returns["security_key"] == key]
    after = daily[pd.to_datetime(daily["date"]).dt.date > listed_end]
    assert not after.empty
    assert (after["session_status"] == R.STATUS_POST_TERMINAL).all()


def test_reserve_hmax_21_limits_horizons_and_labels(tmp_path: Path) -> None:
    store, key = _reserve_store(
        tmp_path, name="res_hmax", listed_end=S[POS[RES_F1] + 30]
    )
    result = _fold_result(store, fold=RESERVE_FOLD, hmax=21, formations=None)
    assert sorted(set(result.horizons["h"].tolist())) == list(range(1, 22))
    assert sorted(set(result.labels["h"].tolist())) == list(range(1, 22))
    assert result.content["constants"]["hmax"] == 21


def test_reserve_purge_and_crossing_at_fold_end(tmp_path: Path) -> None:
    store, key = _reserve_store(
        tmp_path, name="res_purge", listed_end=S[POS[RES_F1] + 30]
    )
    result = _fold_result(store, fold=RESERVE_FOLD, hmax=63, formations=None)
    labels = result.labels[result.labels["security_key"] == key].set_index("h")
    # endpoint(h=21) == C: inside the fold, never purged.
    assert labels.loc[21, "endpoint"] == RES_C
    assert labels.loc[21, "label_state"] != R.STATE_PURGED_RESERVE_CROSSING
    # endpoint(h=22) > C: purged by the fold end.
    assert labels.loc[22, "label_state"] == R.STATE_PURGED_RESERVE_CROSSING
    horizons = result.horizons.set_index("h")
    assert not bool(horizons.loc[21, "crosses_reserve"])
    assert bool(horizons.loc[22, "crosses_reserve"])


# ---------------------------------------------------------------------------
# AccessGrant gate (reserve fold refused before any path opens)
# ---------------------------------------------------------------------------


def test_reserve_fold_without_grant_denied(tmp_path: Path) -> None:
    with pytest.raises(ReserveAccessDenied):
        FoldWindow(_RESERVE_START, RES_C, RESERVE)


def test_grantless_forged_reserve_fold_denied_before_open(tmp_path: Path) -> None:
    store, _ = _reserve_store(
        tmp_path, name="res_gate", listed_end=S[POS[RES_F1] + 30]
    )
    inputs = _inputs(store)
    forged = FoldWindow.development()
    object.__setattr__(forged, "source", RESERVE)
    object.__setattr__(forged, "grant", None)

    open_calls: list[object] = []

    def _record_open(*args: object, **kwargs: object):
        open_calls.append(args)
        raise AssertionError("no path may be opened before the gate")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("builtins.open", _record_open)
        with pytest.raises(ReserveAccessDenied):
            RF.build_returns_fold(
                observations=inputs.observations,
                ca_events=inputs.ca_events,
                identity=inputs.identity,
                day_flags=inputs.day_flags,
                universe=inputs.universe,
                formations=None,
                fold=forged,
                calendar=CAL,
                hmax=21,
            )
    assert open_calls == []


# ---------------------------------------------------------------------------
# Mutation fixture contract (planner applies R7R-M1..R7R-M6)
# ---------------------------------------------------------------------------


def test_mutation_fixture_contract() -> None:
    mutations = json.loads(_MUTATIONS_PATH.read_text(encoding="utf-8"))
    assert [m["id"] for m in mutations] == [
        f"R7R-M{i}" for i in range(1, 7)
    ]
    module_rel = "smart_beta/dataset_v1/reserve_chain/returns_fold.py"
    source = (_REPO_ROOT / module_rel).read_text(encoding="utf-8")
    test_source = Path(__file__).read_text(encoding="utf-8")
    for mutation in mutations:
        assert mutation["edits"], mutation["id"]
        assert mutation["file"] == module_rel, mutation["id"]
        for edit in mutation["edits"]:
            assert edit["old"] and edit["new"] is not None, mutation["id"]
            assert source.count(edit["old"]) == 1, mutation["id"]
            assert edit["old"] != edit["new"], mutation["id"]
        module_name, _, test_name = mutation["test"].partition("::")
        assert module_name == "tests/test_reserve_chain_returns.py", mutation["id"]
        assert f"def {test_name.split('[')[0]}" in test_source, mutation["id"]
