"""Gate-6 corporate-action specimen audit tests (G6-SW, synthetic).

Every test builds synthetic ``tmp_path`` fixtures and calls the pure helpers
of :mod:`smart_beta.dataset_v1.gate6_audit` or the fake-transport SEC fetcher.
No test opens anything under ``data_store``, reads a credential, or opens a
socket; autouse guards enforce all three.  The tests are named
``test_01_*`` … ``test_60_*`` so the frozen spec section-13 item map is
mechanical.
"""

from __future__ import annotations

import builtins
import gzip
import hashlib
import inspect
import json
import os
import socket
import urllib.error
from dataclasses import replace
from datetime import date

import pandas as pd
import pytest

from smart_beta.dataset_v1 import gate6_audit as A
from smart_beta.dataset_v1 import gate6_sec_fetch as F
from smart_beta.dataset_v1.reserve_firewall import (
    DevelopmentAccessor,
    FileBinding,
)

# ---------------------------------------------------------------------------
# Constants, guards
# ---------------------------------------------------------------------------

SENTINEL = "g6-credential-sentinel-4f0a"
_OPENED: list[str] = []


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch):
    def _refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("G6 tests must never open a socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield


@pytest.fixture(autouse=True)
def _credential_sentinel(monkeypatch: pytest.MonkeyPatch):
    for name in F.CREDENTIAL_VARIABLES:
        monkeypatch.setenv(name, SENTINEL)
    monkeypatch.delenv("SEC_CONTACT", raising=False)
    yield


@pytest.fixture(autouse=True)
def _no_data_store(monkeypatch: pytest.MonkeyPatch):
    real_open = builtins.open
    _OPENED.clear()

    def _open(file: object, *args: object, **kwargs: object):
        path = str(file)
        if "data_store" in path or "reserve_sealed" in path:
            _OPENED.append(path)
            raise AssertionError(f"tests must never open {path}")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _open)
    yield


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _day_flags(rows):
    return pd.DataFrame(
        rows,
        columns=[
            "security_key",
            "ticker",
            "date",
            "class_or_flag",
            "disposition",
        ],
    )


def _suspect(key: str, ticker: str, d: date):
    return (key, ticker, d, A.SUSPECT, A.UNCERTIFIED)


def _series(
    key: str,
    ticker: str,
    closes,
    *,
    unusable=(),
    listed_start: date = A.DEV_START,
    listed_end: date = A.DEV_END,
):
    return A.KeySeries(
        security_key=key,
        ticker=ticker,
        listed_start=listed_start,
        listed_end=listed_end,
        closes=dict(closes),
        unusable=frozenset(unusable),
    )


def _candidates(entries):
    return [
        A.make_candidate(key, key, t, stratum, band)
        for key, t, stratum, band in entries
    ]


def _select(entries):
    return A.select_specimens(
        _candidates(entries), population_sha256="p" * 64
    )


def _fact(ftype: str, **fields):
    base = {
        "type": ftype,
        "source": "E1",
        "accession": "ACC1",
        "form": "8-K",
        "date_filed": date(2020, 1, 10),
        "doc_sha256": "sha1",
        "locator": "doc.txt",
        "quote": "quote",
        "specimen_id": "g6-0001",
    }
    base.update(fields)
    return base


def _engine(**overrides):
    base = {
        "specimen_id": "g6-0001",
        "security_key": "K1",
        "ticker": "AAA",
        "t": date(2020, 1, 3),
        "p": date(2020, 1, 2),
        "rho": 9.6,
        "close_t": 96.0,
        "close_p": 10.0,
        "facts": (),
        "split_events": (),
        "dividend_events": (),
        "sessions": (date(2020, 1, 2), date(2020, 1, 3), date(2020, 1, 6)),
    }
    base.update(overrides)
    return A.EngineInput(**base)


def _rec(stratum, band, year, outcome, reason=None, offset=None, sid="g6-0001"):
    return A.SpecimenOutcome(sid, stratum, band, year, outcome, reason, offset)


def _s1(n, resolved, band, year=2020):
    out = []
    for i in range(n):
        if i < resolved:
            out.append(_rec("S1", band, year, A.OUTCOME_GENUINE, "EARNINGS_OR_GUIDANCE"))
        else:
            out.append(_rec("S1", band, year, A.OUTCOME_UNRESOLVED))
    return out


def _one_set():
    return _select([("K1", date(2020, 1, 3), A.STRATUM_S2, A.BAND_B1)])


def _one_specimen():
    return _one_set().specimens[0]


class _D3Store:
    """A tiny synthetic D3 store written to ``tmp_path``."""

    def __init__(self, tmp_path):
        self.root = tmp_path / "store"
        self.obs_rows = []
        self.ca_rows = []

    def obs(self, ticker, d, close, idx=0):
        self.obs_rows.append((ticker, d, close, idx))

    def ca(self, key, d, action, factor):
        self.ca_rows.append((key, d, action, factor))

    def _observations(self):
        return pd.DataFrame(
            {
                "ticker": pd.Series([r[0] for r in self.obs_rows], dtype="string"),
                "date": pd.Series(
                    [pd.Timestamp(r[1]) for r in self.obs_rows],
                    dtype="datetime64[ns]",
                ),
                "close": pd.Series([r[2] for r in self.obs_rows], dtype="float64"),
                "raw_row_index": pd.Series(
                    [r[3] for r in self.obs_rows], dtype="int64"
                ),
            }
        )

    def _ca_events(self):
        return pd.DataFrame(
            {
                "stock_id": pd.Series([r[0] for r in self.ca_rows], dtype="string"),
                "effective_date": pd.Series(
                    [pd.Timestamp(r[1]) for r in self.ca_rows],
                    dtype="datetime64[ns]",
                ),
                "action_type": pd.Series(
                    [r[2] for r in self.ca_rows], dtype="string"
                ),
                "adjustment_factor": pd.Series(
                    [r[3] for r in self.ca_rows], dtype="float64"
                ),
            }
        )

    def accessor(self):
        base = self.root / "normalized" / "n-f874d3ecb61374d4"
        base.mkdir(parents=True, exist_ok=True)
        obs_path = base / "observations.parquet"
        ca_path = base / "ca_events.parquet"
        self._observations().to_parquet(obs_path, index=False)
        self._ca_events().to_parquet(ca_path, index=False)
        bindings = {
            "observations": FileBinding(
                "normalized/n-f874d3ecb61374d4/observations.parquet",
                hashlib.sha256(obs_path.read_bytes()).hexdigest(),
            ),
            "ca_events": FileBinding(
                "normalized/n-f874d3ecb61374d4/ca_events.parquet",
                hashlib.sha256(ca_path.read_bytes()).hexdigest(),
            ),
        }
        return DevelopmentAccessor(self.root, bindings=bindings)


# ===========================================================================
# Population and bindings (1-7)
# ===========================================================================


def test_01_population_equals_fixture_and_hash_rule() -> None:
    frame = _day_flags(
        [
            _suspect("K1", "AAA", date(2020, 1, 3)),
            _suspect("K2", "BBB", date(2021, 6, 1)),
            ("K3", "CCC", date(2020, 5, 1), "OHLC_INCONSISTENT", "CORRUPT"),
        ]
    )
    records = A.population_records(frame)
    assert records == [("K1", date(2020, 1, 3)), ("K2", date(2021, 6, 1))]
    expected = hashlib.sha256(
        "".join(f"{k}\t{d:%Y-%m-%d}\n" for k, d in records).encode("utf-8")
    ).hexdigest()
    assert A.population_sha256(records) == expected
    assert A.population_sha256(list(reversed(records))) == expected
    pop = A.build_population(
        frame,
        A.PopulationBinding(count=2, sha256=expected, unique_keys=2),
    )
    assert pop.count == 2
    assert pop.unique_keys == 2
    assert pop.sha256 == expected
    assert pop.per_year == {2020: 1, 2021: 1}


def test_02_reserve_dated_row_fails_closed() -> None:
    frame = _day_flags([_suspect("K1", "AAA", A.RESERVE_START)])
    with pytest.raises(A.PopulationInconsistencyError):
        A.population_records(frame)


def test_03_day_flags_sha_mismatch_fails_closed(tmp_path) -> None:
    frame = _day_flags(
        [
            _suspect("K1", "AAA", date(2020, 1, 3)),
            _suspect("K2", "BBB", date(2021, 6, 1)),
        ]
    )
    path = tmp_path / "day_flags.parquet"
    frame.to_parquet(path, index=False)
    good = hashlib.sha256(path.read_bytes()).hexdigest()
    records = A.population_records(frame)
    binding = A.PopulationBinding(
        count=2, sha256=A.population_sha256(records), unique_keys=2
    )
    pop = A.load_day_flags(path, expected_sha256=good, binding=binding)
    assert pop.count == 2
    with pytest.raises(A.BindingMismatch):
        A.load_day_flags(path, expected_sha256="0" * 64, binding=binding)


def test_04_one_row_changes_hash_and_binding_mismatch() -> None:
    frame = _day_flags(
        [
            _suspect("K1", "AAA", date(2020, 1, 3)),
            _suspect("K2", "BBB", date(2021, 6, 1)),
        ]
    )
    baseline = A.population_sha256(A.population_records(frame))
    binding = A.PopulationBinding(count=2, sha256=baseline, unique_keys=2)
    A.build_population(frame, binding)

    changed = frame.copy()
    changed.loc[0, "date"] = pd.Timestamp(date(2020, 1, 6))
    assert A.population_sha256(A.population_records(changed)) != baseline
    with pytest.raises(A.BindingMismatch):
        A.build_population(changed, binding)

    dropped = frame.iloc[:1].copy()
    assert A.population_sha256(A.population_records(dropped)) != baseline
    with pytest.raises(A.BindingMismatch):
        A.build_population(dropped, binding)


def test_05_rho_inside_excluded_band_raises() -> None:
    sessions = [date(2020, 1, 2), date(2020, 1, 3)]
    series = _series("K1", "AAA", {sessions[0]: 10.0, sessions[1]: 12.0})
    with pytest.raises(A.PopulationInconsistencyError):
        A.compute_magnitude(series, sessions[1], sessions)


def test_06_rho_uses_previous_usable_session() -> None:
    d1, d2, d3, d4, d5 = (
        date(2020, 1, 2),
        date(2020, 1, 3),
        date(2020, 1, 6),
        date(2020, 1, 7),
        date(2020, 1, 8),
    )
    sessions = [d1, d2, d3, d4, d5]
    # d2 is VENDOR_MISSING (no close); d3 CORRUPT; d4 INTERVAL_MISMATCH.
    series = _series(
        "K1",
        "AAA",
        {d1: 10.0, d3: 8.0, d4: 9.0, d5: 3.0},
        unusable={d3, d4},
    )
    p, rho = A.compute_magnitude(series, d5, sessions)
    assert p == d1
    assert rho == pytest.approx(0.3)


def test_07_d3_reads_are_development_only(tmp_path) -> None:
    store = _D3Store(tmp_path)
    store.obs("AAA", date(2020, 1, 2), 10.0)
    store.obs("AAA", date(2020, 1, 3), 3.0)
    store.ca("K1", date(2020, 1, 3), "split", 0.5)
    accessor = store.accessor()
    market = A.MarketData(accessor)
    market.observations()
    market.ca_events()
    assert market.reads
    for component, _start, end in market.reads:
        assert end <= A.DEV_END
        assert end < A.RESERVE_START
    with pytest.raises(Exception):
        accessor.read(
            "observations",
            start=A.DEV_START,
            end=A.RESERVE_START,
            columns=["close"],
        )
    assert not any("reserve" in p or "data_store" in p for p in _OPENED)


# ===========================================================================
# Strata (8-11)
# ===========================================================================


def test_08_band_boundaries_exact() -> None:
    assert A.magnitude_band(1 / 3 - 1e-9) == A.BAND_B1
    assert A.magnitude_band(1 / 3) == A.BAND_B2
    with pytest.raises(A.PopulationInconsistencyError):
        A.magnitude_band(0.5)
    with pytest.raises(A.PopulationInconsistencyError):
        A.magnitude_band(1.5)
    assert A.magnitude_band(3.0) == A.BAND_B3
    assert A.magnitude_band(3.0 + 1e-9) == A.BAND_B4


def test_09_s3_uses_trusted_sessions_and_split_at_t_fails() -> None:
    sessions = A.default_calendar()
    t = date(2020, 1, 6)  # Monday
    previous_thursday = date(2020, 1, 2)  # two sessions back, four calendar days
    ca = pd.DataFrame(
        {
            "stock_id": ["K1"],
            "effective_date": [pd.Timestamp(previous_thursday)],
            "action_type": ["split"],
            "adjustment_factor": [0.5],
        }
    )
    stratum, offset = A.assign_stratum(
        "K1",
        t,
        ticker="K1",
        ca_events=ca,
        sessions=sessions,
        listed_start=A.DEV_START,
        listed_end=A.DEV_END,
        admitted=set(),
    )
    assert stratum == A.STRATUM_S3
    assert offset == -2
    at_t = ca.copy()
    at_t.loc[0, "effective_date"] = pd.Timestamp(t)
    with pytest.raises(A.StratumError):
        A.assign_stratum(
            "K1",
            t,
            ticker="K1",
            ca_events=at_t,
            sessions=sessions,
            listed_start=A.DEV_START,
            listed_end=A.DEV_END,
            admitted=set(),
        )


def test_10_s1_membership_equals_d7c_admission() -> None:
    sessions = A.default_calendar()
    t = date(2020, 1, 3)
    ca = pd.DataFrame(
        columns=["stock_id", "effective_date", "action_type", "adjustment_factor"]
    )
    df = pd.DataFrame(
        {
            "security_key": ["K1", "K1", "K2"],
            "source_session": [
                pd.Timestamp(t),
                pd.Timestamp(date(2020, 1, 6)),
                pd.Timestamp(t),
            ],
        }
    )
    admitted = A.admitted_pairs(df)
    assert admitted == {("K1", t), ("K1", date(2020, 1, 6)), ("K2", t)}
    stratum, _ = A.assign_stratum(
        "K1",
        t,
        ticker="K1",
        ca_events=ca,
        sessions=sessions,
        listed_start=A.DEV_START,
        listed_end=A.DEV_END,
        admitted=admitted,
    )
    assert stratum == A.STRATUM_S1
    stratum2, _ = A.assign_stratum(
        "K2",
        date(2020, 1, 2),
        ticker="K2",
        ca_events=ca,
        sessions=sessions,
        listed_start=A.DEV_START,
        listed_end=A.DEV_END,
        admitted=admitted,
    )
    assert stratum2 == A.STRATUM_S2


def test_11_strata_disjoint_exhaustive_and_s3_precedence() -> None:
    sessions = A.default_calendar()
    t = date(2020, 1, 6)
    ca = pd.DataFrame(
        {
            "stock_id": ["K1"],
            "effective_date": [pd.Timestamp(date(2020, 1, 2))],
            "action_type": ["split"],
            "adjustment_factor": [0.5],
        }
    )
    admitted = {("K1", t)}
    stratum, _ = A.assign_stratum(
        "K1",
        t,
        ticker="K1",
        ca_events=ca,
        sessions=sessions,
        listed_start=A.DEV_START,
        listed_end=A.DEV_END,
        admitted=admitted,
    )
    assert stratum == A.STRATUM_S3
    other, _ = A.assign_stratum(
        "K9",
        t,
        ticker="K9",
        ca_events=ca,
        sessions=sessions,
        listed_start=A.DEV_START,
        listed_end=A.DEV_END,
        admitted=set(),
    )
    assert other in (A.STRATUM_S1, A.STRATUM_S2)


# ===========================================================================
# Sampling (12-22)
# ===========================================================================


def _sample_entries():
    return [
        (f"K{y}-{i}", date(y, 1, 2 + i), A.STRATUM_S1, A.BAND_B1)
        for y in (2018, 2019, 2020)
        for i in range(3)
    ]


def test_12_sampling_deterministic_and_tz(monkeypatch) -> None:
    first = A.specimen_payload(_select(_sample_entries()))
    second = A.specimen_payload(_select(_sample_entries()))
    assert first == second
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    monkeypatch.setenv("LC_ALL", "C")
    third = A.specimen_payload(_select(_sample_entries()))
    assert third == first


def test_13_rank_is_sha256_of_seed_key_date() -> None:
    key, t = "TIINGO:AAA:2020-01-02", date(2020, 1, 3)
    expected = hashlib.sha256(
        f"g6-specimen-v1|{key}|{t.isoformat()}".encode("utf-8")
    ).hexdigest()
    assert A.rank_hex(key, t) == expected


def test_14_seed_pinned_and_changed_rejected() -> None:
    candidates = _candidates(_sample_entries())
    with pytest.raises(A.BindingMismatch):
        A.select_specimens(
            candidates, population_sha256="p" * 64, seed="something-else|"
        )
    with pytest.raises(A.BindingMismatch):
        A.select_specimens(
            candidates, population_sha256="p" * 64, algorithm="g6-specimen-v2"
        )


def test_15_year_round_robin_order() -> None:
    specimen_set = _select(_sample_entries())
    first_three = [s.year for s in specimen_set.specimens[:3]]
    assert sorted(first_three) == [2018, 2019, 2020]


def test_16_at_most_one_specimen_per_key() -> None:
    entries = [
        ("K1", date(2020, 1, 3), A.STRATUM_S1, A.BAND_B1),
        ("K2", date(2020, 1, 3), A.STRATUM_S1, A.BAND_B1),
        ("K1", date(2020, 1, 3), A.STRATUM_S2, A.BAND_B1),
    ]
    specimen_set = _select(entries)
    keys = [s.security_key for s in specimen_set.specimens]
    assert keys.count("K1") == 1
    chosen = next(s for s in specimen_set.specimens if s.security_key == "K1")
    assert chosen.stratum == A.STRATUM_S1


def test_17_cell_processing_order() -> None:
    entries = [
        ("K1", date(2020, 1, 3), A.STRATUM_S3, A.BAND_B1),
        ("K2", date(2020, 1, 3), A.STRATUM_S3, A.BAND_B4),
        ("K3", date(2020, 1, 3), A.STRATUM_S1, A.BAND_B2),
        ("K4", date(2020, 1, 3), A.STRATUM_S2, A.BAND_B4),
    ]
    specimen_set = _select(entries)
    assert [s.stratum for s in specimen_set.specimens] == [
        A.STRATUM_S3,
        A.STRATUM_S3,
        A.STRATUM_S1,
        A.STRATUM_S2,
    ]
    assert specimen_set.specimens[0].selection_order == 0


def test_18_small_cell_census_shortfall_not_reallocated() -> None:
    entries = [
        ("K1", date(2020, 1, 3), A.STRATUM_S1, A.BAND_B1),
        ("K2", date(2020, 1, 3), A.STRATUM_S1, A.BAND_B1),
    ]
    specimen_set = _select(entries)
    assert len(specimen_set.specimens) == 2
    assert specimen_set.shortfalls[(A.STRATUM_S1, A.BAND_B1)] == 28


def test_19_cap_induced_exhaustion_recorded_as_shortfall() -> None:
    entries = [
        ("K1", date(2020, 1, 3), A.STRATUM_S3, A.BAND_B1),
        ("K2", date(2020, 1, 3), A.STRATUM_S3, A.BAND_B1),
        ("K1", date(2021, 1, 4), A.STRATUM_S1, A.BAND_B1),
        ("K2", date(2021, 1, 4), A.STRATUM_S1, A.BAND_B1),
    ]
    specimen_set = _select(entries)
    assert specimen_set.shortfalls[(A.STRATUM_S1, A.BAND_B1)] == 30
    assert len(specimen_set.specimens) == 2


def test_20_selection_independent_of_outcomes(tmp_path) -> None:
    parameters = inspect.signature(A.select_specimens).parameters
    for forbidden in ("facts", "outcomes", "evidence", "context"):
        assert forbidden not in parameters
    baseline = A.specimen_payload(_select(_sample_entries()))
    (tmp_path / "outcomes.parquet").write_bytes(b"not-really-parquet")
    (tmp_path / "facts_worker.jsonl").write_text("{}\n")
    aftermath = A.specimen_payload(_select(_sample_entries()))
    assert aftermath == baseline


def test_21_duplicate_specimen_fails_closed() -> None:
    specimen = _one_specimen()
    same_key = replace(
        specimen,
        t=date(2021, 1, 4),
        selection_order=1,
        specimen_id="g6-0002",
    )
    combined = A.SpecimenSet(
        specimens=(specimen, same_key),
        shortfalls={},
        population_sha256="p" * 64,
        seed=A.G6_RANK_PREFIX,
        allocation=dict(A.DEFAULT_ALLOCATION),
        algorithm=A.G6_ALGORITHM_ID,
    )
    with pytest.raises(A.SpecimenValidationError):
        combined.validate()
    identical = A.SpecimenSet(
        specimens=(specimen, specimen),
        shortfalls={},
        population_sha256="p" * 64,
        seed=A.G6_RANK_PREFIX,
        allocation=dict(A.DEFAULT_ALLOCATION),
        algorithm=A.G6_ALGORITHM_ID,
    )
    with pytest.raises(A.SpecimenValidationError):
        identical.validate()


def test_22_specimen_and_fetch_plan_hash_verified() -> None:
    specimen_set = _select(_sample_entries())
    expected = A.specimen_sha256(specimen_set)
    assert A.verify_specimen_hash(specimen_set, expected) == expected
    with pytest.raises(A.BindingMismatch):
        A.verify_specimen_hash(specimen_set, "0" * 64)

    rows = (
        A.FetchPlanRow(
            "g6-0001", "MAPPED", "1", "acc", "8-K", date(2020, 1, 3), "f", 1
        ),
    )
    plan_hash = A.fetch_plan_sha256(rows)
    assert A.verify_fetch_plan_hash(rows, plan_hash) == plan_hash
    with pytest.raises(A.BindingMismatch):
        A.verify_fetch_plan_hash(rows, "0" * 64)


# ===========================================================================
# Mapping and fetch plan (23-25)
# ===========================================================================


def _index_row(cik, name, d, form="8-K", filename="edgar/data/x/a.txt"):
    return A.IndexRow(cik, name, form, d, filename)


def test_23_issuer_mapping() -> None:
    t = date(2020, 6, 15)
    rows = [
        _index_row("1", "Acme Corp", date(2020, 6, 1)),
        _index_row("2", "Bob Inc", date(2020, 6, 1)),
    ]
    result = A.map_issuer("K1", ["Acme Corp"], rows, t)
    assert result.state == "MAPPED"
    assert result.cik == "1"
    assert A.map_issuer("K1", ["Nobody Inc"], rows, t).reason == A.REASON_NO_ISSUER_MAPPING
    assert A.map_issuer("K1", [], rows, t).reason == A.REASON_NO_ISSUER_NAME

    # Two candidate CIKs; only one is active in the 400-day window.
    tie = [
        _index_row("1", "Acme Corp", date(2020, 6, 1)),
        _index_row("2", "Acme Corp", date(2015, 1, 5)),
    ]
    result = A.map_issuer("K1", ["Acme Corp"], tie, t)
    assert result.cik == "1"

    ambiguous = [
        _index_row("1", "Acme Corp", date(2020, 6, 1)),
        _index_row("2", "Acme Corp", date(2019, 6, 1)),
    ]
    result = A.map_issuer("K1", ["Acme Corp"], ambiguous, t)
    assert result.reason == A.REASON_AMBIGUOUS_ISSUER_MAPPING


def _master(rows: list[str]) -> bytes:
    body = "-----\n" + "\n".join(rows) + "\n"
    return gzip.compress(body.encode("latin-1"))


def test_24_index_after_dev_end_never_used() -> None:
    files = {
        "master_2015Q1.gz": _master(
            [
                "1|Acme Corp|8-K|2023-08-01|edgar/data/1/late.txt",
                "1|Acme Corp|8-K|2020-06-01|edgar/data/1/ok.txt",
            ]
        ),
        "master_2023Q3.gz": _master(
            ["2|Acme Corp|8-K|2020-06-01|edgar/data/2/ignored.txt"]
        ),
    }
    rows = A.parse_sec_index_files(files)
    assert all(row.date_filed <= A.DEV_END for row in rows)
    assert all(row.source_file == "master_2015Q1.gz" for row in rows)
    assert [row.filename for row in rows] == ["edgar/data/1/ok.txt"]
    assert A.allowed_index_filename("master_2023Q2.gz")
    assert not A.allowed_index_filename("master_2023Q3.gz")


def test_25_event_window_priority_order_cap_and_no_filings() -> None:
    t = date(2020, 6, 15)
    forms = ["10-Q", "8-K", "DEF 14A", "25-NSE", "10-K"]
    rows = [
        _index_row("1", "Acme Corp", t, form=form, filename=f"edgar/data/1/{idx}.txt")
        for idx, form in enumerate(forms)
    ]
    rows.append(
        _index_row("1", "Acme Corp", date(2019, 1, 1), filename="edgar/data/1/old.txt")
    )
    result = A.map_issuer("K1", ["Acme Corp"], rows, t)
    assert result.state == "MAPPED"
    priorities = [A.FORM_PRIORITY[row.form] for row in result.filings]
    assert priorities == sorted(priorities)
    assert priorities[0] == 1
    assert len(result.filings) == len(forms)

    many = [
        _index_row(
            "1", "Acme Corp", t, form="8-K", filename=f"edgar/data/1/{i:02d}.txt"
        )
        for i in range(20)
    ]
    capped = A.map_issuer("K1", ["Acme Corp"], many, t)
    assert len(capped.filings) == A.MAX_FILINGS_PER_SPECIMEN

    empty = [_index_row("1", "Acme Corp", date(2019, 1, 1))]
    assert A.map_issuer("K1", ["Acme Corp"], empty, t).reason == A.REASON_NO_FILINGS_IN_WINDOW


# ===========================================================================
# Facts (26-30)
# ===========================================================================


def test_26_fact_schema_closed_types_and_required_fields() -> None:
    evidence = {"ACC1": "sha1"}
    valid = _fact("SPLIT", factor=0.5, market_effective_date=date(2020, 1, 3))
    assert A.validate_fact(valid, specimen_ids={"g6-0001"}, evidence=evidence)
    with pytest.raises(A.FactValidationError):
        A.validate_fact(
            _fact("NOT_A_TYPE"), specimen_ids={"g6-0001"}, evidence=evidence
        )
    broken = _fact("SPLIT")
    with pytest.raises(A.FactValidationError):
        A.validate_fact(broken, specimen_ids={"g6-0001"}, evidence=evidence)


def test_27_non_e1_fact_rejected() -> None:
    evidence = {"ACC1": "sha1"}
    with pytest.raises(A.FactValidationError):
        A.validate_fact(
            _fact("SPLIT", source="TIINGO", factor=0.5,
                  market_effective_date=date(2020, 1, 3)),
            specimen_ids={"g6-0001"},
            evidence=evidence,
        )


def test_28_fact_after_dev_end_rejected() -> None:
    evidence = {"ACC1": "sha1"}
    with pytest.raises(A.FactValidationError):
        A.validate_fact(
            _fact(
                "SPLIT",
                date_filed=date(2023, 7, 1),
                factor=0.5,
                market_effective_date=date(2020, 1, 3),
            ),
            specimen_ids={"g6-0001"},
            evidence=evidence,
        )


def test_29_doc_sha256_mismatch_rejected() -> None:
    with pytest.raises(A.FactValidationError):
        A.validate_fact(
            _fact(
                "SPLIT",
                doc_sha256="wrong",
                factor=0.5,
                market_effective_date=date(2020, 1, 3),
            ),
            specimen_ids={"g6-0001"},
            evidence={"ACC1": "sha1"},
        )


def test_30_fact_unknown_specimen_rejected() -> None:
    with pytest.raises(A.FactValidationError):
        A.validate_fact(
            _fact(
                "SPLIT",
                specimen_id="g6-9999",
                factor=0.5,
                market_effective_date=date(2020, 1, 3),
            ),
            specimen_ids={"g6-0001"},
            evidence={"ACC1": "sha1"},
        )


# ===========================================================================
# Rule engine (31-46)
# ===========================================================================


def test_31_reverse_split_f_0_1_rho_9_6_omitted() -> None:
    out = A.run_rule_engine(
        _engine(
            facts=[
                _fact(
                    "SPLIT", factor=0.1, market_effective_date=date(2020, 1, 3)
                )
            ]
        )
    )
    assert out == A.Outcome(A.OUTCOME_OMISSION, A.REASON_OMITTED_SPLIT)


def test_32_misdated_split_offset() -> None:
    out = A.run_rule_engine(
        _engine(
            facts=[
                _fact(
                    "SPLIT", factor=0.1, market_effective_date=date(2020, 1, 3)
                )
            ],
            split_events=[(date(2020, 1, 6), 0.1)],
        )
    )
    assert out == A.Outcome(A.OUTCOME_OMISSION, A.REASON_MISDATED_SPLIT, 1)


def test_33_split_effective_outside_window_no_explanation() -> None:
    before = A.run_rule_engine(
        _engine(
            facts=[
                _fact(
                    "SPLIT", factor=0.1, market_effective_date=date(2020, 1, 1)
                )
            ]
        )
    )
    assert before == A.Outcome(
        A.OUTCOME_UNRESOLVED, A.REASON_NO_EXPLANATORY_FILING
    )
    # A split effective on p is outside the half-open window (p, t].
    at_p = A.run_rule_engine(
        _engine(
            facts=[
                _fact(
                    "SPLIT", factor=0.1, market_effective_date=date(2020, 1, 2)
                )
            ]
        )
    )
    assert at_p == A.Outcome(
        A.OUTCOME_UNRESOLVED, A.REASON_NO_EXPLANATORY_FILING
    )


def test_34_split_with_a_outside_tolerance_no_explanation() -> None:
    out = A.run_rule_engine(
        _engine(
            facts=[
                _fact(
                    "SPLIT", factor=0.045, market_effective_date=date(2020, 1, 3)
                )
            ]
        )
    )
    assert out == A.Outcome(A.OUTCOME_UNRESOLVED, A.REASON_NO_EXPLANATORY_FILING)


def test_35_omitted_and_misdated_cash_distributions() -> None:
    base = dict(rho=0.2, close_t=2.0, close_p=10.0)
    omitted = A.run_rule_engine(
        _engine(
            **base,
            facts=[
                _fact("CASH_DISTRIBUTION", amount=7.0, ex_date=date(2020, 1, 3))
            ],
        )
    )
    assert omitted == A.Outcome(A.OUTCOME_OMISSION, A.REASON_OMITTED_CASH)
    misdated = A.run_rule_engine(
        _engine(
            **base,
            facts=[
                _fact("CASH_DISTRIBUTION", amount=7.0, ex_date=date(2020, 1, 3))
            ],
            dividend_events=[(date(2020, 1, 6), 7.0)],
        )
    )
    assert misdated == A.Outcome(A.OUTCOME_OMISSION, A.REASON_MISDATED_CASH, 1)


def test_36_spinoff_effective_in_window() -> None:
    out = A.run_rule_engine(
        _engine(
            facts=[
                _fact(
                    "COMPLEX_ACTION",
                    complex_type="SPINOFF",
                    effective_date=date(2020, 1, 3),
                )
            ]
        )
    )
    assert out == A.Outcome(A.OUTCOME_COMPLEX, "SPINOFF")


def test_37_timing_rules() -> None:
    p, t = date(2020, 1, 3), date(2020, 1, 6)
    sessions = (p, t)
    assert A.event_in_window(p, "AFTER_CLOSE", p=p, t=t, sessions=sessions)
    assert not A.event_in_window(t, "AFTER_CLOSE", p=p, t=t, sessions=sessions)
    assert A.event_in_window(
        date(2020, 1, 4), "DURING_SESSION", p=p, t=t, sessions=sessions
    )
    assert A.event_in_window(p, "UNSTATED", p=p, t=t, sessions=sessions)
    decided = A.run_rule_engine(
        _engine(
            t=t,
            p=p,
            sessions=sessions,
            facts=[
                _fact(
                    "MATERIAL_EVENT",
                    event_type="EARNINGS_OR_GUIDANCE",
                    event_date=p,
                    timing="AFTER_CLOSE",
                )
            ],
        )
    )
    assert decided == A.Outcome(A.OUTCOME_GENUINE, "EARNINGS_OR_GUIDANCE")
    not_decided = A.run_rule_engine(
        _engine(
            t=t,
            p=p,
            sessions=sessions,
            facts=[
                _fact(
                    "MATERIAL_EVENT",
                    event_type="EARNINGS_OR_GUIDANCE",
                    event_date=t,
                    timing="AFTER_CLOSE",
                )
            ],
        )
    )
    assert not_decided == A.Outcome(
        A.OUTCOME_UNRESOLVED, A.REASON_NO_EXPLANATORY_FILING
    )


def test_38_direction_table_blocks_wrong_direction() -> None:
    out = A.run_rule_engine(
        _engine(
            rho=9.6,
            facts=[
                _fact(
                    "MATERIAL_EVENT",
                    event_type="BANKRUPTCY_OR_RESTRUCTURING",
                    event_date=date(2020, 1, 3),
                    timing="UNSTATED",
                )
            ],
        )
    )
    assert out == A.Outcome(A.OUTCOME_UNRESOLVED, A.REASON_NO_EXPLANATORY_FILING)


def test_39_unlisted_event_type_does_not_decide() -> None:
    out = A.run_rule_engine(
        _engine(
            facts=[
                _fact(
                    "MATERIAL_EVENT",
                    event_type="MYSTERY_EVENT",
                    event_date=date(2020, 1, 3),
                    timing="UNSTATED",
                )
            ]
        )
    )
    assert out == A.Outcome(A.OUTCOME_UNRESOLVED, A.REASON_NO_EXPLANATORY_FILING)


def test_40_ticker_reassignment_overrides_matching_split() -> None:
    out = A.run_rule_engine(
        _engine(
            facts=[
                _fact(
                    "TICKER_REASSIGNMENT",
                    date=date(2020, 1, 3),
                    from_issuer="Old",
                    to_issuer="New",
                ),
                _fact(
                    "SPLIT", factor=0.1, market_effective_date=date(2020, 1, 3)
                ),
            ]
        )
    )
    assert out == A.Outcome(A.OUTCOME_IDENTITY, A.REASON_IDENTITY_MISMATCH)


def test_41_stated_close_outside_band() -> None:
    out = A.run_rule_engine(
        _engine(
            facts=[_fact("STATED_CLOSE", date=date(2020, 1, 3), price=1000.0)]
        )
    )
    assert out == A.Outcome(A.OUTCOME_IDENTITY, A.REASON_PRICE_CONTRADICTED)


def test_42_two_explanations_conflict() -> None:
    out = A.run_rule_engine(
        _engine(
            facts=[
                _fact(
                    "SPLIT", factor=0.1, market_effective_date=date(2020, 1, 3)
                ),
                _fact(
                    "COMPLEX_ACTION",
                    complex_type="SPINOFF",
                    effective_date=date(2020, 1, 3),
                ),
            ]
        )
    )
    assert out == A.Outcome(A.OUTCOME_UNRESOLVED, A.REASON_EVIDENCE_CONFLICT)


def test_43_two_splits_with_different_f_conflict() -> None:
    out = A.run_rule_engine(
        _engine(
            facts=[
                _fact(
                    "SPLIT", factor=0.1, market_effective_date=date(2020, 1, 3)
                ),
                _fact(
                    "SPLIT", factor=0.12, market_effective_date=date(2020, 1, 3)
                ),
            ]
        )
    )
    assert out == A.Outcome(A.OUTCOME_UNRESOLVED, A.REASON_EVIDENCE_CONFLICT)


def test_44_blind_extraction_agreement_and_disagreement() -> None:
    agree = A.combine_outcomes(
        A.Outcome(A.OUTCOME_GENUINE, "EARNINGS_OR_GUIDANCE"),
        A.Outcome(A.OUTCOME_GENUINE, "EARNINGS_OR_GUIDANCE"),
    )
    assert agree == A.Outcome(A.OUTCOME_GENUINE, "EARNINGS_OR_GUIDANCE")
    disagree = A.combine_outcomes(
        A.Outcome(A.OUTCOME_GENUINE, "EARNINGS_OR_GUIDANCE"),
        A.Outcome(A.OUTCOME_OMISSION, A.REASON_OMITTED_SPLIT),
    )
    assert disagree == A.Outcome(
        A.OUTCOME_UNRESOLVED, A.REASON_REVIEWER_DISAGREEMENT
    )


def test_45_tiingo_context_alone_never_decides() -> None:
    context = {
        "material_events": [
            {
                "event_type": "EARNINGS_OR_GUIDANCE",
                "event_date": date(2020, 1, 3),
            }
        ]
    }
    out = A.run_rule_engine(_engine(facts=[]), context=context)
    assert out == A.Outcome(A.OUTCOME_UNRESOLVED, A.REASON_NO_EXPLANATORY_FILING)
    assert A.tiingo_context_facts(context)


def test_46_mapping_and_retrieval_failure_reasons() -> None:
    reasons = (
        A.REASON_NO_ISSUER_NAME,
        A.REASON_NO_ISSUER_MAPPING,
        A.REASON_AMBIGUOUS_ISSUER_MAPPING,
        A.REASON_NO_FILINGS_IN_WINDOW,
    )
    for reason in reasons:
        assert A.run_rule_engine(_engine(mapping_reason=reason)) == A.Outcome(
            A.OUTCOME_UNRESOLVED, reason
        )
    assert A.run_rule_engine(_engine(evidence_unretrievable=True)) == A.Outcome(
        A.OUTCOME_UNRESOLVED, A.REASON_EVIDENCE_UNRETRIEVABLE
    )
    assert A.run_rule_engine(_engine(request_cap_reached=True)) == A.Outcome(
        A.OUTCOME_UNRESOLVED, A.REASON_REQUEST_CAP_REACHED
    )


# ===========================================================================
# Decision (47-54)
# ===========================================================================


def test_47_one_s2_data_error_gives_hold() -> None:
    records = [_rec("S2", "B1", 2020, A.OUTCOME_OMISSION, A.REASON_OMITTED_SPLIT)]
    decision = A.decide_gate6(records)
    assert decision.decision == A.DECISION_HOLD
    assert A.TAG_ISOLATED in decision.tags


def test_48_escalation_tags_boundaries() -> None:
    assert A.TAG_T1 in A.escalation_tags(
        [
            _rec("S1", "B1", 2020, A.OUTCOME_OMISSION, A.REASON_OMITTED_SPLIT),
            _rec("S1", "B2", 2021, A.OUTCOME_OMISSION, A.REASON_OMITTED_SPLIT),
        ]
    )
    assert A.TAG_T2 in A.escalation_tags(
        [
            _rec("S1", "B1", 2020, A.OUTCOME_IDENTITY, A.REASON_IDENTITY_MISMATCH),
            _rec("S1", "B2", 2021, A.OUTCOME_IDENTITY, A.REASON_IDENTITY_MISMATCH),
        ]
    )
    assert A.TAG_T3 in A.escalation_tags(
        [
            _rec(
                "S1", "B1", 2020, A.OUTCOME_OMISSION, A.REASON_MISDATED_SPLIT, 1
            ),
            _rec(
                "S1", "B2", 2021, A.OUTCOME_OMISSION, A.REASON_MISDATED_SPLIT, 1
            ),
        ]
    )
    assert A.TAG_T4 in A.escalation_tags(
        [
            _rec("S1", "B1", 2020, A.OUTCOME_OMISSION, A.REASON_OMITTED_SPLIT),
            _rec("S1", "B1", 2020, A.OUTCOME_IDENTITY, A.REASON_IDENTITY_MISMATCH),
        ]
    )
    assert A.TAG_T5 in A.escalation_tags(
        [
            _rec("S3", "B1", 2020, A.OUTCOME_OMISSION, A.REASON_OMITTED_SPLIT),
            _rec("S1", "B1", 2020, A.OUTCOME_OMISSION, A.REASON_OMITTED_SPLIT),
        ]
    )
    assert A.escalation_tags(
        [_rec("S1", "B1", 2020, A.OUTCOME_OMISSION, A.REASON_OMITTED_SPLIT)]
    ) == frozenset({A.TAG_ISOLATED})


def test_49_inconclusive_boundaries() -> None:
    # r1 boundary 58 vs 59 (n1 = 120, both populated bands well above 16).
    below = A.decide_gate6(_s1(60, 29, "B1") + _s1(60, 29, "B2"))
    assert below.r1 == 58
    assert below.decision == A.DECISION_INCONCLUSIVE
    above = A.decide_gate6(_s1(60, 29, "B1") + _s1(60, 30, "B2"))
    assert above.r1 == 59
    assert above.decision == A.DECISION_PASS

    # band boundary 15 vs 16 (B1 has 32 members, B2 has 60).
    band_below = A.decide_gate6(_s1(32, 15, "B1") + _s1(60, 60, "B2"))
    assert band_below.decision == A.DECISION_INCONCLUSIVE
    band_above = A.decide_gate6(_s1(32, 16, "B1") + _s1(60, 60, "B2"))
    assert band_above.decision == A.DECISION_PASS

    # ceil(n/2) form when the cell is short (n1 = 100 -> threshold 50).
    short_below = A.decide_gate6(_s1(100, 49, "B1"))
    assert short_below.decision == A.DECISION_INCONCLUSIVE
    short_above = A.decide_gate6(_s1(100, 50, "B1"))
    assert short_above.decision == A.DECISION_PASS


def test_50_pass_and_unresolved_stay_in_counts() -> None:
    records = (
        _s1(30, 15, "B1") + _s1(30, 15, "B2") + _s1(30, 16, "B3") + _s1(30, 15, "B4")
    )
    decision = A.decide_gate6(records)
    assert decision.decision == A.DECISION_PASS
    assert decision.n1 == 120
    assert decision.r1 == 61
    assert decision.unresolved == 59
    counts = A.outcome_counts(records)
    assert any(key[2] == A.OUTCOME_UNRESOLVED for key in counts)


def test_51_no_api_drops_an_unresolved_result() -> None:
    records = _s1(10, 3, "B1")
    assert sum(1 for r in records if r.outcome == A.OUTCOME_UNRESOLVED) == 7
    assert A._resolved(records) == 3
    counts = A.outcome_counts(records)
    unresolved = sum(v for k, v in counts.items() if k[2] == A.OUTCOME_UNRESOLVED)
    assert unresolved == 7
    assert A.combine_outcomes(
        A.Outcome(A.OUTCOME_UNRESOLVED), A.Outcome(A.OUTCOME_UNRESOLVED)
    ) == A.Outcome(A.OUTCOME_UNRESOLVED)


def test_52_reconciliation_mismatch_fails_closed() -> None:
    records = [_rec("S1", "B1", 2020, A.OUTCOME_UNRESOLVED)]
    counts = A.outcome_counts(records)
    assert sum(counts.values()) == 1
    A.reconcile_counts(1, counts)
    with pytest.raises(A.ReconciliationError):
        A.reconcile_counts(2, counts)


def _records_for(specimen_set, outcome=A.OUTCOME_UNRESOLVED):
    return [
        _rec(s.stratum, s.band, s.year, outcome, sid=s.specimen_id)
        for s in specimen_set.specimens
    ]


def _artifacts():
    specimen_set = _one_set()
    records = _records_for(specimen_set)
    decision = A.decide_gate6(records)
    return A.build_g6_artifacts(
        population_sha256="p" * 64,
        specimen_sha256=A.specimen_sha256(specimen_set),
        fetch_plan_sha256="f" * 64,
        specimen_set=specimen_set,
        records=records,
        decision=decision,
        mapping_state_counts={"MAPPED": 1},
        procedure={"rho_example": 0.123456, "runtime": "test"},
    )


def test_53_artifacts_deterministic_and_git_summary_data_free() -> None:
    first = A.build_g6_artifacts(
        population_sha256="p" * 64,
        specimen_sha256=A.specimen_sha256(_one_set()),
        fetch_plan_sha256="f" * 64,
        specimen_set=_one_set(),
        records=_records_for(_one_set()),
        decision=A.decide_gate6(_records_for(_one_set())),
        mapping_state_counts={"MAPPED": 1},
        procedure={"rho_example": 0.123456},
    )
    second = A.build_g6_artifacts(
        population_sha256="p" * 64,
        specimen_sha256=A.specimen_sha256(_one_set()),
        fetch_plan_sha256="f" * 64,
        specimen_set=_one_set(),
        records=_records_for(_one_set()),
        decision=A.decide_gate6(_records_for(_one_set())),
        mapping_state_counts={"MAPPED": 1},
        procedure={"rho_example": 0.123456},
    )
    assert A.canonical_json(first) == A.canonical_json(second)
    assert "timestamp" not in A.canonical_json(first)
    summary = A.git_summary(_artifacts())
    assert "0.123456" not in summary
    assert "rho" not in summary.lower()
    assert "K1" not in summary


def test_54_certification_text_and_cli_are_exact() -> None:
    text = A.certification_text("SPEC", "POP")
    assert text == (
        "The predeclared development-only gate-6 specimen audit "
        "(g6-specimen-v1, population POP, specimen SPEC) found no audit "
        "outcome requiring remediation under the frozen decision rule."
    )
    assert A.NONCLAIMS == (
        "that every suspect observation is correct",
        "that Tiingo's price level is verified for GENUINE_MOVE_DOCUMENTED specimens",
        "universal corporate-action certification",
        "certification of complex corporate actions",
        "point-in-time corporate-action knowledge",
        "any result for the reserve suspect days, which stay DEFERRED",
        "reserve or confirmation eligibility",
        "alpha, predictive validity, investability or production readiness",
    )
    artifacts = _artifacts()
    printed = A.cli_summary(artifacts)
    assert "g6-specimen-v1" in printed
    assert "K1" not in printed
    assert "2020-01-03" not in printed


# ===========================================================================
# Fetcher (55-60)
# ===========================================================================


class _FakeTransport:
    def __init__(self, script):
        self.script = script
        self.calls = []

    def __call__(self, url, headers):
        self.calls.append((url, dict(headers)))
        if callable(self.script):
            return self.script(url, headers)
        return self.script.pop(0)


class _Clock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += seconds


def _plan_row(
    specimen_id="g6-0001",
    cik="1",
    accession="ACC1",
    filename="edgar/data/1/acc1.txt",
    mapping_state="MAPPED",
    form="8-K",
    date_filed=date(2020, 1, 3),
    priority=1,
):
    return A.FetchPlanRow(
        specimen_id=specimen_id,
        mapping_state=mapping_state,
        cik=cik,
        accession=accession,
        form=form,
        date_filed=date_filed,
        filename=filename,
        priority=priority,
    )


def _filings():
    return [
        _plan_row(accession="ACC1", filename="edgar/data/1/acc1.txt"),
        _plan_row(accession="ACC2", filename="edgar/data/1/acc2.txt"),
    ]


def test_55_host_path_allowlist_refuses_off_plan() -> None:
    filings = _filings()
    transport = _FakeTransport([])
    fetcher = F.SecFetcher(
        filings,
        plan_sha256=A.fetch_plan_sha256(filings),
        transport=transport,
        contact="audit@example.com",
    )
    off_plan = _plan_row(
        specimen_id="g6-0002", cik="2", accession="ACC9",
        filename="edgar/data/2/acc9.txt",
    )
    with pytest.raises(F.UrlRefused):
        fetcher.fetch_filing(off_plan)
    assert transport.calls == []

    weird = _plan_row(filename="evil.example/path")
    weird_transport = _FakeTransport([])
    weird_fetcher = F.SecFetcher(
        [weird],
        plan_sha256=A.fetch_plan_sha256([weird]),
        transport=weird_transport,
        contact="audit@example.com",
    )
    with pytest.raises(F.UrlRefused):
        weird_fetcher.fetch_filing(weird)
    assert weird_transport.calls == []


def test_56_fetch_plan_hash_verified_before_request() -> None:
    filings = _filings()
    transport = _FakeTransport([])
    with pytest.raises(F.FetchPlanMismatch):
        F.SecFetcher(
            filings,
            plan_sha256="0" * 64,
            transport=transport,
            contact="audit@example.com",
        )
    assert transport.calls == []


def test_57_contact_required_and_never_logged(monkeypatch) -> None:
    filings = _filings()
    monkeypatch.delenv("SEC_CONTACT", raising=False)
    with pytest.raises(F.ContactMissing):
        F.SecFetcher(
            filings,
            plan_sha256=A.fetch_plan_sha256(filings),
            transport=_FakeTransport([]),
            contact=None,
        )
    monkeypatch.setenv("SEC_CONTACT", "audit@example.com")
    transport = _FakeTransport(
        [F.TransportResponse(200, b"body-1"), F.TransportResponse(200, b"body-2")]
    )
    fetcher = F.SecFetcher(
        filings,
        plan_sha256=A.fetch_plan_sha256(filings),
        transport=transport,
        contact=None,
    )
    outcomes = fetcher.fetch_all()
    assert "audit@example.com" not in fetcher.log_jsonl()
    assert "audit@example.com" not in repr(outcomes)
    assert all(
        headers["User-Agent"].startswith("SmartBeta-Gate6-Audit")
        for _, headers in transport.calls
    )


def test_58_rate_retries_and_request_cap() -> None:
    filings = _filings()
    plan_hash = A.fetch_plan_sha256(filings)

    clock = _Clock()
    rate_transport = _FakeTransport(
        [F.TransportResponse(200, b"a"), F.TransportResponse(200, b"b")]
    )
    rate_fetcher = F.SecFetcher(
        filings,
        plan_sha256=plan_hash,
        transport=rate_transport,
        contact="audit@example.com",
        clock=clock.now,
        sleep=clock.sleep,
    )
    rate_fetcher.fetch_all()
    assert clock.sleeps == [pytest.approx(0.25)]

    retry_transport = _FakeTransport(
        [F.TransportResponse(500), F.TransportResponse(200, b"ok")]
    )
    retry_fetcher = F.SecFetcher(
        [filings[0]],
        plan_sha256=A.fetch_plan_sha256([filings[0]]),
        transport=retry_transport,
        contact="audit@example.com",
        clock=clock.now,
        sleep=clock.sleep,
    )
    result = retry_fetcher.fetch_all()[0]
    assert result.outcome == "FETCHED"
    assert len(retry_transport.calls) == 2

    not_found = _FakeTransport([F.TransportResponse(404)])
    not_found_fetcher = F.SecFetcher(
        [filings[0]],
        plan_sha256=A.fetch_plan_sha256([filings[0]]),
        transport=not_found,
        contact="audit@example.com",
        clock=clock.now,
        sleep=clock.sleep,
    )
    assert not_found_fetcher.fetch_all()[0].outcome == "UNRETRIEVABLE"
    assert len(not_found.calls) == 1

    capped_transport = _FakeTransport([F.TransportResponse(200, b"a")])
    capped = F.SecFetcher(
        filings,
        plan_sha256=plan_hash,
        transport=capped_transport,
        contact="audit@example.com",
        clock=clock.now,
        sleep=clock.sleep,
        max_requests=1,
    )
    outcomes = capped.fetch_all()
    assert capped.requests == 1
    assert outcomes[1].outcome == "REQUEST_CAP_REACHED"


def test_59_atomic_cache_resume_and_corruption(tmp_path) -> None:
    filing = _plan_row(accession="ACC1", filename="edgar/data/1/acc1.txt")
    plan_hash = A.fetch_plan_sha256([filing])
    first_transport = _FakeTransport([F.TransportResponse(200, b"hello")])
    first = F.SecFetcher(
        [filing],
        plan_sha256=plan_hash,
        transport=first_transport,
        contact="audit@example.com",
        cache_dir=tmp_path,
    )
    digest = first.fetch_all()[0].doc_sha256
    assert (tmp_path / "evidence" / "ACC1.txt").read_bytes() == b"hello"

    def _must_not_fetch(url, headers):
        raise AssertionError("a verified cache file must not be re-fetched")

    resumed = F.SecFetcher(
        [filing],
        plan_sha256=plan_hash,
        transport=_must_not_fetch,
        contact="audit@example.com",
        cache_dir=tmp_path,
        cache_index={"ACC1": digest},
    )
    assert resumed.fetch_all()[0].outcome == "CACHED"

    (tmp_path / "evidence" / "ACC1.txt").write_bytes(b"corrupted")
    repair_transport = _FakeTransport([F.TransportResponse(200, b"repaired")])
    repaired = F.SecFetcher(
        [filing],
        plan_sha256=plan_hash,
        transport=repair_transport,
        contact="audit@example.com",
        cache_dir=tmp_path,
        cache_index={"ACC1": digest},
    )
    assert repaired.fetch_all()[0].outcome == "FETCHED"
    assert (tmp_path / "evidence" / "ACC1.txt").read_bytes() == b"repaired"
    assert len(repair_transport.calls) == 1


def test_60_no_credentials_read_or_logged_no_vendor_client() -> None:
    filings = _filings()
    transport = _FakeTransport(
        [F.TransportResponse(200, b"a"), F.TransportResponse(200, b"b")]
    )
    fetcher = F.SecFetcher(
        filings,
        plan_sha256=A.fetch_plan_sha256(filings),
        transport=transport,
        contact="audit@example.com",
    )
    fetcher.fetch_all()
    text = fetcher.log_jsonl()
    for name in F.CREDENTIAL_VARIABLES:
        assert os.environ[name] == SENTINEL
        assert SENTINEL not in text

    copied = dict(os.environ)
    assert set(F.scrub_credentials(copied)) == set(F.CREDENTIAL_VARIABLES)
    for name in F.CREDENTIAL_VARIABLES:
        assert name not in copied

    source = inspect.getsource(A)
    assert "import requests" not in source
    assert "vendors.tiingo.client" not in source
    assert "import socket" not in source


# ===========================================================================
# Addendum A1 — conflict domain
# ===========================================================================


def test_a1_out_of_window_split_does_not_conflict() -> None:
    out = A.run_rule_engine(
        _engine(
            facts=[
                _fact(
                    "SPLIT", factor=0.1, market_effective_date=date(2020, 1, 1)
                ),
                _fact(
                    "SPLIT", factor=0.5, market_effective_date=date(2020, 1, 6)
                ),
            ]
        )
    )
    assert out == A.Outcome(A.OUTCOME_UNRESOLVED, A.REASON_NO_EXPLANATORY_FILING)


def test_a1_out_of_window_cash_does_not_conflict() -> None:
    out = A.run_rule_engine(
        _engine(
            rho=0.2,
            close_t=2.0,
            close_p=10.0,
            facts=[
                _fact(
                    "CASH_DISTRIBUTION", amount=7.0, ex_date=date(2020, 1, 1)
                ),
                _fact(
                    "CASH_DISTRIBUTION", amount=99.0, ex_date=date(2020, 1, 6)
                ),
            ],
        )
    )
    assert out == A.Outcome(A.OUTCOME_UNRESOLVED, A.REASON_NO_EXPLANATORY_FILING)


def test_a1_in_window_split_conflicts_even_when_one_fails_a_test() -> None:
    out = A.run_rule_engine(
        _engine(
            facts=[
                _fact(
                    "SPLIT", factor=0.1, market_effective_date=date(2020, 1, 3)
                ),
                _fact(
                    "SPLIT", factor=0.5, market_effective_date=date(2020, 1, 3)
                ),
            ]
        )
    )
    assert out == A.Outcome(A.OUTCOME_UNRESOLVED, A.REASON_EVIDENCE_CONFLICT)


def test_a1_in_window_cash_conflicts_even_when_one_fails_qualification() -> None:
    out = A.run_rule_engine(
        _engine(
            rho=0.2,
            close_t=2.0,
            close_p=10.0,
            facts=[
                _fact(
                    "CASH_DISTRIBUTION", amount=7.0, ex_date=date(2020, 1, 3)
                ),
                _fact(
                    "CASH_DISTRIBUTION", amount=100.0, ex_date=date(2020, 1, 3)
                ),
            ],
        )
    )
    assert out == A.Outcome(A.OUTCOME_UNRESOLVED, A.REASON_EVIDENCE_CONFLICT)


# ===========================================================================
# Addendum A2 — the form table is a closed allowlist
# ===========================================================================


def test_a2_unlisted_forms_never_enter_the_plan() -> None:
    t = date(2020, 6, 15)
    rows = [
        _index_row("1", "Acme Corp", t, form="8-K", filename="edgar/data/1/a.txt"),
        _index_row("1", "Acme Corp", t, form="4", filename="edgar/data/1/b.txt"),
        _index_row("1", "Acme Corp", t, form="SC 13G", filename="edgar/data/1/c.txt"),
    ]
    result = A.map_issuer("K1", ["Acme Corp"], rows, t)
    assert result.state == "MAPPED"
    assert [row.form for row in result.filings] == ["8-K"]
    specimen = A.Specimen(
        selection_order=0,
        specimen_id="g6-0000",
        stratum=A.STRATUM_S2,
        band=A.BAND_B1,
        year=2020,
        security_key="K1",
        ticker="AAA",
        t=t,
        p=date(2020, 6, 12),
        rho=9.6,
        rank_hex="0" * 64,
        near_split_offset=None,
        dividend_within_3=False,
    )
    plan, _counts = A.build_fetch_plan(
        [specimen], issuer_names={"K1": ["Acme Corp"]}, index_rows=rows
    )
    assert {row.form for row in plan} == {"8-K"}
    assert all(row.form in A.FORM_PRIORITY for row in plan)


def test_a2_unlisted_forms_never_change_the_plan_hash() -> None:
    t = date(2020, 6, 15)
    listed = [
        _index_row("1", "Acme Corp", t, form="8-K", filename="edgar/data/1/a.txt")
    ]
    with_unlisted = listed + [
        _index_row("1", "Acme Corp", t, form="4", filename="edgar/data/1/b.txt"),
        _index_row("1", "Acme Corp", t, form="SC 13G", filename="edgar/data/1/c.txt"),
    ]
    specimen = A.Specimen(
        selection_order=0,
        specimen_id="g6-0000",
        stratum=A.STRATUM_S2,
        band=A.BAND_B1,
        year=2020,
        security_key="K1",
        ticker="AAA",
        t=t,
        p=date(2020, 6, 12),
        rho=9.6,
        rank_hex="0" * 64,
        near_split_offset=None,
        dividend_within_3=False,
    )
    plan_a, _ = A.build_fetch_plan(
        [specimen], issuer_names={"K1": ["Acme Corp"]}, index_rows=listed
    )
    plan_b, _ = A.build_fetch_plan(
        [specimen], issuer_names={"K1": ["Acme Corp"]}, index_rows=with_unlisted
    )
    assert A.fetch_plan_sha256(plan_a) == A.fetch_plan_sha256(plan_b)


def test_a2_only_unlisted_forms_in_window_gives_no_filings() -> None:
    t = date(2020, 6, 15)
    rows = [
        _index_row("1", "Acme Corp", t, form="4", filename="edgar/data/1/b.txt"),
        _index_row("1", "Acme Corp", t, form="SC 13G", filename="edgar/data/1/c.txt"),
        _index_row(
            "1", "Acme Corp", date(2019, 1, 2), form="8-K",
            filename="edgar/data/1/old.txt",
        ),
    ]
    result = A.map_issuer("K1", ["Acme Corp"], rows, t)
    assert result.reason == A.REASON_NO_FILINGS_IN_WINDOW


# ===========================================================================
# Repair defects — production pipeline and semantics
# ===========================================================================


def _synthetic_root(tmp_path):
    root = tmp_path / "store"
    build_id = A.D3_BUILD_ID
    (root / "normalized" / build_id).mkdir(parents=True, exist_ok=True)

    day = pd.DataFrame(
        [
            {
                "security_key": "TIINGO:ABC:2010-01-04",
                "ticker": "ABC",
                "date": pd.Timestamp(date(2020, 1, 3)),
                "raw_row_index": 1,
                "class_or_flag": A.SUSPECT,
                "disposition": A.UNCERTIFIED,
            }
        ]
    )
    day_dir = root / "derived" / "qc" / "q-test"
    day_dir.mkdir(parents=True, exist_ok=True)
    day_path = day_dir / "day_flags.parquet"
    day.to_parquet(day_path, index=False)

    obs = pd.DataFrame(
        {
            "ticker": ["ABC", "ABC"],
            "date": [pd.Timestamp(date(2020, 1, 2)), pd.Timestamp(date(2020, 1, 3))],
            "close": [10.0, 96.0],
            "raw_row_index": [0, 1],
        }
    )
    obs_path = root / "normalized" / build_id / "observations.parquet"
    obs.to_parquet(obs_path, index=False)

    ca = pd.DataFrame(
        {
            "stock_id": pd.Series([], dtype="string"),
            "effective_date": pd.Series([], dtype="datetime64[ns]"),
            "action_type": pd.Series([], dtype="string"),
            "adjustment_factor": pd.Series([], dtype="float64"),
        }
    )
    ca_path = root / "normalized" / build_id / "ca_events.parquet"
    ca.to_parquet(ca_path, index=False)

    ident = pd.DataFrame(
        {
            "security_key": ["TIINGO:ABC:2010-01-04"],
            "ticker": ["ABC"],
            "listed_start": [pd.Timestamp(A.DEV_START)],
            "listed_end": [pd.Timestamp(A.DEV_END)],
            "state": [A.INTERVAL_ASSIGNED],
        }
    )
    ident_dir = root / "derived" / "identity" / "i-test"
    ident_dir.mkdir(parents=True, exist_ok=True)
    ident_path = ident_dir / "identity_assignment.parquet"
    ident.to_parquet(ident_path, index=False)

    d7c = pd.DataFrame(
        {
            "security_key": pd.Series([], dtype="string"),
            "source_session": pd.Series([], dtype="datetime64[ns]"),
        }
    )
    d7c_dir = root / "derived" / "trusted_inputs" / "ti-test"
    d7c_dir.mkdir(parents=True, exist_ok=True)
    d7c_path = d7c_dir / "daily_total_return.parquet"
    d7c.to_parquet(d7c_path, index=False)

    issuer = pd.DataFrame(
        {
            "security_key": ["TIINGO:ABC:2010-01-04"],
            "issuer_names_norm": [json.dumps(["Acme Corp"])],
        }
    )
    clas_dir = root / "derived" / "classification" / "c-test"
    clas_dir.mkdir(parents=True, exist_ok=True)
    issuer_path = clas_dir / "issuer_mapping.parquet"
    issuer.to_parquet(issuer_path, index=False)

    index_dir = root / A.SEC_INDEX_DIR
    index_dir.mkdir(parents=True, exist_ok=True)
    index_bytes: dict[str, bytes] = {}
    for name in A.expected_index_filenames():
        if name == "master_2020Q1.gz":
            body = "-----\n1|Acme Corp|8-K|2020-01-03|edgar/data/1/acc1.txt\n"
        else:
            body = "-----\n"
        data = gzip.compress(body.encode("latin-1"))
        (index_dir / name).write_bytes(data)
        index_bytes[name] = data
    dsc = {
        "dsc_hash": "d-test",
        "sec_index_files_sha256": {
            name: hashlib.sha256(data).hexdigest()
            for name, data in index_bytes.items()
        },
    }
    # A 2023Q3+ decoy entry must never be opened or verified.
    dsc["sec_index_files_sha256"]["master_2023Q3.gz"] = "0" * 64
    dsc_path = clas_dir / "dsc.json"
    dsc_path.write_text(A.canonical_json(dsc) + "\n")

    day_sha = hashlib.sha256(day_path.read_bytes()).hexdigest()
    qc_report = {
        "qc_hash": "q-test",
        "files_sha256": {"day_flags.parquet": day_sha},
    }
    qc_report_path = day_dir / "qc_report.json"
    qc_report_path.write_text(A.canonical_json(qc_report) + "\n")

    records = A.population_records(day)
    bindings = A.ProductionBindings(
        day_flags=A.ArtifactBinding(
            "derived/qc/q-test/day_flags.parquet", day_sha
        ),
        qc_hash="q-test",
        d3_build_id=build_id,
        observations=A.ArtifactBinding(
            f"normalized/{build_id}/observations.parquet",
            hashlib.sha256(obs_path.read_bytes()).hexdigest(),
        ),
        ca_events=A.ArtifactBinding(
            f"normalized/{build_id}/ca_events.parquet",
            hashlib.sha256(ca_path.read_bytes()).hexdigest(),
        ),
        identity_assignment=A.ArtifactBinding(
            "derived/identity/i-test/identity_assignment.parquet",
            hashlib.sha256(ident_path.read_bytes()).hexdigest(),
        ),
        daily_total_return=A.ArtifactBinding(
            "derived/trusted_inputs/ti-test/daily_total_return.parquet",
            hashlib.sha256(d7c_path.read_bytes()).hexdigest(),
        ),
        issuer_mapping=A.ArtifactBinding(
            "derived/classification/c-test/issuer_mapping.parquet",
            hashlib.sha256(issuer_path.read_bytes()).hexdigest(),
        ),
        dsc_json=A.ArtifactBinding(
            "derived/classification/c-test/dsc.json",
            hashlib.sha256(dsc_path.read_bytes()).hexdigest(),
        ),
        dsc_hash="d-test",
        qc_report_path="derived/qc/q-test/qc_report.json",
        population=A.PopulationBinding(
            count=len(records),
            sha256=A.population_sha256(records),
            unique_keys=1,
        ),
    )
    return root, bindings


def test_d1_production_bindings_present_and_fail_closed(tmp_path) -> None:
    b = A.PRODUCTION_BINDINGS
    assert b.day_flags.sha256 == (
        "f4010ebeabf5cfae2684f3658a5f19f922f15e2c8898410049f2148440cf7891"
    )
    assert b.qc_hash == (
        "3d63e51b983096f0ae579cd1e382c613d9329d239c3f848cc785dee2d61cdfc3"
    )
    assert b.observations.sha256 == (
        "557001d0e9c617befd3c036683f171094e86e19badb760098f40c08f3f56596e"
    )
    assert b.ca_events.sha256 == (
        "afd4a2986c934d84c2b23eaf68f23ee18746e7c929c6b8625b6c9911e4a2b109"
    )
    assert b.identity_assignment.sha256 == (
        "fee4614009095773f75fb832343a5fa9b87b9de1cf363125567222b08fad3f8b"
    )
    assert b.daily_total_return.sha256 == (
        "511bcf4ac4ea2bd02158438732e0fdf19c04d783b88ca7a01ff5a364ec8e688c"
    )
    assert b.issuer_mapping.sha256 == (
        "dbfb424e50ccdc8127ba9064228738cfc105d5d4e7d4071694a27e28d3e127b5"
    )
    assert b.dsc_json.sha256 == (
        "23877643018f28b4776e092be1c53d048f0c51a810544f073f9a68f57abcc030"
    )
    assert b.dsc_hash == (
        "2ee4940c14a85048fddd9e3e675c3e9a6f40a11c8887192a5e61939adfc62b60"
    )
    assert b.population.count == 17469
    assert b.population.sha256 == (
        "d2153329b8c13a5d20d2bbe351972e048c9dc421216051e08633ffda68fb2783"
    )

    root, bindings = _synthetic_root(tmp_path)
    A.load_g6_inputs(root, bindings)
    (root / bindings.day_flags.relative_path).write_bytes(b"tampered")
    with pytest.raises(A.BindingMismatch):
        A.load_g6_inputs(root, bindings)


def test_d1_full_e1_e2_e3_pipeline(tmp_path) -> None:
    root, bindings = _synthetic_root(tmp_path)
    audit = tmp_path / "audit"
    e1 = A.run_e1(root, audit, bindings=bindings)
    assert e1["specimen_size"] == 1
    assert (audit / "specimen.parquet").exists()
    assert (audit / "fetch_plan.parquet").exists()
    assert (audit / "population.json").exists()
    assert (audit / "specimen_manifest.md").exists()

    body = b"8-K split filing"
    transport = _FakeTransport([F.TransportResponse(200, body)])
    e2 = A.run_e2(root, audit, transport=transport, contact="audit@example.com")
    assert e2["fetch_plan_sha256"] == e1["fetch_plan_sha256"]
    evidence = A.read_evidence(audit)
    digest = hashlib.sha256(body).hexdigest()
    assert list(evidence.values()) == [digest]

    specimen_id = pd.read_parquet(audit / "specimen.parquet")["specimen_id"][0]
    fact = {
        "specimen_id": str(specimen_id),
        "type": "SPLIT",
        "source": "E1",
        "accession": "acc1",
        "form": "8-K",
        "date_filed": "2020-01-03",
        "doc_sha256": digest,
        "locator": "acc1.txt",
        "quote": "reverse split 1-for-10",
        "factor": 0.1,
        "market_effective_date": "2020-01-03",
    }
    A.write_facts(audit / "facts_worker.jsonl", [fact])
    A.write_facts(audit / "facts_planner.jsonl", [fact])

    e3 = A.run_e3(root, audit, bindings=bindings)
    assert e3["decision"] == A.DECISION_HOLD
    artifacts = json.loads((audit / "g6_audit.json").read_text())
    assert any(
        "CERTIFIED_FIELD_OMISSION" in key
        for key in artifacts["stratum_band_outcome_counts"]
    )
    outcomes = pd.read_parquet(audit / "outcomes.parquet")
    assert set(outcomes["outcome"]) == {A.OUTCOME_OMISSION}
    assert set(outcomes["worker_outcome"]) == {A.OUTCOME_OMISSION}
    assert set(outcomes["planner_outcome"]) == {A.OUTCOME_OMISSION}


def test_d1_e3_two_blind_fact_sets_are_separate_inputs(tmp_path) -> None:
    root, bindings = _synthetic_root(tmp_path)
    audit = tmp_path / "audit"
    A.run_e1(root, audit, bindings=bindings)
    body = b"8-K split filing"
    A.run_e2(
        root,
        audit,
        transport=_FakeTransport([F.TransportResponse(200, body)]),
        contact="audit@example.com",
    )
    digest = hashlib.sha256(body).hexdigest()
    specimen_id = pd.read_parquet(audit / "specimen.parquet")["specimen_id"][0]
    fact = {
        "specimen_id": str(specimen_id),
        "type": "SPLIT",
        "source": "E1",
        "accession": "acc1",
        "form": "8-K",
        "date_filed": "2020-01-03",
        "doc_sha256": digest,
        "locator": "acc1.txt",
        "quote": "reverse split 1-for-10",
        "factor": 0.1,
        "market_effective_date": "2020-01-03",
    }
    A.write_facts(audit / "facts_worker.jsonl", [fact])
    A.write_facts(audit / "facts_planner.jsonl", [])
    A.run_e3(root, audit, bindings=bindings)
    outcomes = pd.read_parquet(audit / "outcomes.parquet")
    assert set(outcomes["worker_outcome"]) == {A.OUTCOME_OMISSION}
    assert set(outcomes["planner_outcome"]) == {A.OUTCOME_UNRESOLVED}
    assert set(outcomes["outcome"]) == {A.OUTCOME_UNRESOLVED}
    assert set(outcomes["reason"]) == {A.REASON_REVIEWER_DISAGREEMENT}


def test_d1_specimens_carry_real_p_rho_and_covariate(tmp_path) -> None:
    root, bindings = _synthetic_root(tmp_path)
    inputs = A.load_g6_inputs(root, bindings)
    candidates = A.build_candidates(inputs)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.p == date(2020, 1, 2)
    assert candidate.rho == pytest.approx(9.6)
    assert candidate.band == A.BAND_B4
    assert candidate.stratum == A.STRATUM_S2
    assert candidate.dividend_within_3 is False
    specimen_set = A.select_specimens(
        candidates, population_sha256=inputs.population.sha256
    )
    specimen = specimen_set.specimens[0]
    assert specimen.p == date(2020, 1, 2)
    assert specimen.rho == pytest.approx(9.6)
    assert specimen.t != specimen.p
    assert not (specimen.rho != specimen.rho)  # not NaN


def test_d1_writers_deterministic(tmp_path) -> None:
    root, bindings = _synthetic_root(tmp_path)
    first = tmp_path / "audit-a"
    second = tmp_path / "audit-b"
    A.run_e1(root, first, bindings=bindings)
    A.run_e1(root, second, bindings=bindings)
    for name in ("population.json", "specimen.json", "specimen_manifest.md"):
        assert (first / name).read_text() == (second / name).read_text()
    assert A.specimen_frame(A.read_specimen_set(first)[0]).equals(
        A.specimen_frame(A.read_specimen_set(second)[0])
    )
    assert A.fetch_plan_frame(A.read_fetch_plan(first)).equals(
        A.fetch_plan_frame(A.read_fetch_plan(second))
    )


def test_d1_manifest_is_data_free(tmp_path) -> None:
    root, bindings = _synthetic_root(tmp_path)
    audit = tmp_path / "audit"
    A.run_e1(root, audit, bindings=bindings)
    manifest = (audit / "specimen_manifest.md").read_text()
    assert "TIINGO" not in manifest
    assert "ABC" not in manifest
    assert "2020-" not in manifest
    assert "rho" not in manifest
    assert "ratio" not in manifest
    assert "quote" not in manifest


def test_d1_cli_prints_only_ids_hashes_counts() -> None:
    line = A._format_cli(
        {
            "population_sha256": "p" * 64,
            "specimen_sha256": "s" * 64,
            "decision": A.DECISION_PASS,
            "rho": 0.123,
            "security_key": "TIINGO:ABC:2010-01-04",
            "t": "2020-01-03",
        }
    )
    assert "population_sha256=" + "p" * 64 in line
    assert "0.123" not in line
    assert "TIINGO" not in line
    assert "2020-01-03" not in line
    assert callable(A.main)


def test_d2_stock_id_is_ticker_and_s3_uses_identity(tmp_path) -> None:
    key = "TIINGO:ABC:2010-01-04"
    ticker = "ABC"
    t = date(2020, 1, 6)
    ca = pd.DataFrame(
        {
            "stock_id": [ticker],
            "effective_date": [pd.Timestamp(date(2020, 1, 2))],
            "action_type": ["split"],
            "adjustment_factor": [0.5],
        }
    )
    assert A.split_events_for(ticker, A.DEV_START, A.DEV_END, ca) == [
        (date(2020, 1, 2), 0.5)
    ]
    assert A.split_events_for("ZZZ", A.DEV_START, A.DEV_END, ca) == []
    stratum, offset = A.assign_stratum(
        key,
        t,
        ticker=ticker,
        ca_events=ca,
        sessions=A.default_calendar(),
        listed_start=A.DEV_START,
        listed_end=A.DEV_END,
        admitted=set(),
    )
    assert stratum == A.STRATUM_S3
    assert offset == -2

    # MISDATED_SPLIT flows from a D3 event associated through the ticker.
    d3_split = pd.DataFrame(
        {
            "stock_id": [ticker],
            "effective_date": [pd.Timestamp(date(2020, 1, 6))],
            "action_type": ["split"],
            "adjustment_factor": [0.1],
        }
    )
    out = A.run_rule_engine(
        _engine(
            security_key=key,
            ticker=ticker,
            facts=[
                _fact(
                    "SPLIT", factor=0.1,
                    market_effective_date=date(2020, 1, 3),
                )
            ],
            split_events=A.split_events_for(
                ticker, A.DEV_START, A.DEV_END, d3_split
            ),
        )
    )
    assert out == A.Outcome(A.OUTCOME_OMISSION, A.REASON_MISDATED_SPLIT, 1)


def test_d2_missing_or_ambiguous_identity_fails_closed() -> None:
    missing = pd.DataFrame(
        {
            "security_key": ["K1"],
            "ticker": ["AAA"],
            "listed_start": [pd.Timestamp(A.DEV_START)],
            "listed_end": [pd.Timestamp(A.DEV_END)],
            "state": ["NOT_ASSIGNED"],
        }
    )
    index = A.build_identity_index(missing)
    with pytest.raises(A.Gate6Error):
        A.require_identity("K1", index)

    ambiguous = pd.DataFrame(
        {
            "security_key": ["K1", "K1"],
            "ticker": ["AAA", "BBB"],
            "listed_start": [pd.Timestamp(A.DEV_START)] * 2,
            "listed_end": [pd.Timestamp(A.DEV_END)] * 2,
            "state": [A.INTERVAL_ASSIGNED, A.INTERVAL_ASSIGNED],
        }
    )
    index = A.build_identity_index(ambiguous)
    with pytest.raises(A.Gate6Error):
        A.require_identity("K1", index)


def test_d3_dividend_amount_derived_from_factor() -> None:
    ticker = "ABC"
    effective = date(2020, 1, 6)
    observations = pd.DataFrame(
        {
            "ticker": [ticker],
            "date": [pd.Timestamp(effective)],
            "close": [20.0],
        }
    )
    ca = pd.DataFrame(
        {
            "stock_id": [ticker],
            "effective_date": [pd.Timestamp(effective)],
            "action_type": ["dividend"],
            # adjustment_factor = 1 + divCash / close = 1 + 7 / 20.
            "adjustment_factor": [1.35],
        }
    )
    events = A.dividend_events_for(
        ticker, A.DEV_START, A.DEV_END, ca, observations
    )
    assert events == [(effective, pytest.approx(7.0))]

    out = A.run_rule_engine(
        _engine(
            rho=0.4,
            close_t=4.0,
            close_p=10.0,
            facts=[
                _fact(
                    "CASH_DISTRIBUTION", amount=7.0, ex_date=date(2020, 1, 3)
                )
            ],
            dividend_events=events,
        )
    )
    assert out == A.Outcome(A.OUTCOME_OMISSION, A.REASON_MISDATED_CASH, 1)

    missing_close = observations.iloc[:0]
    with pytest.raises(A.Gate6Error):
        A.dividend_events_for(
            ticker, A.DEV_START, A.DEV_END, ca, missing_close
        )


def test_d4_artifact_records_real_outcomes(tmp_path) -> None:
    specimen_set = _select(
        [
            ("K1", date(2020, 1, 3), A.STRATUM_S1, A.BAND_B1),
            ("K2", date(2020, 2, 3), A.STRATUM_S1, A.BAND_B1),
            ("K3", date(2020, 3, 3), A.STRATUM_S1, A.BAND_B1),
        ]
    )
    specimens = list(specimen_set.specimens)
    records = [
        _rec(
            specimens[0].stratum, specimens[0].band, specimens[0].year,
            A.OUTCOME_OMISSION, A.REASON_OMITTED_SPLIT, sid=specimens[0].specimen_id,
        ),
        _rec(
            specimens[1].stratum, specimens[1].band, specimens[1].year,
            A.OUTCOME_GENUINE, "EARNINGS_OR_GUIDANCE", sid=specimens[1].specimen_id,
        ),
        _rec(
            specimens[2].stratum, specimens[2].band, specimens[2].year,
            A.OUTCOME_UNRESOLVED, A.REASON_NO_EXPLANATORY_FILING,
            sid=specimens[2].specimen_id,
        ),
    ]
    artifacts = A.build_g6_artifacts(
        population_sha256="p" * 64,
        specimen_sha256=A.specimen_sha256(specimen_set),
        fetch_plan_sha256="f" * 64,
        specimen_set=specimen_set,
        records=records,
        decision=A.decide_gate6(records),
        mapping_state_counts={"MAPPED": 3},
    )
    counts = artifacts["stratum_band_outcome_counts"]
    assert any("CERTIFIED_FIELD_OMISSION" in key for key in counts)
    assert any("GENUINE_MOVE_DOCUMENTED" in key for key in counts)
    assert any("UNRESOLVED" in key for key in counts)
    assert sum(counts.values()) == len(specimens)
    with pytest.raises(A.ReconciliationError):
        A.build_g6_artifacts(
            population_sha256="p" * 64,
            specimen_sha256=A.specimen_sha256(specimen_set),
            fetch_plan_sha256="f" * 64,
            specimen_set=specimen_set,
            records=records[:-1],
            decision=A.decide_gate6(records),
            mapping_state_counts={"MAPPED": 3},
        )


def test_d5_single_canonical_fetch_plan_hash(tmp_path) -> None:
    assert not hasattr(F, "plan_sha256")
    root, bindings = _synthetic_root(tmp_path)
    audit = tmp_path / "audit"
    e1 = A.run_e1(root, audit, bindings=bindings)
    rows = A.read_fetch_plan(audit)
    assert A.fetch_plan_sha256(rows) == e1["fetch_plan_sha256"]

    transport = _FakeTransport([])
    F.SecFetcher(
        rows,
        plan_sha256=e1["fetch_plan_sha256"],
        transport=transport,
        contact="audit@example.com",
    )
    assert transport.calls == []
    with pytest.raises(F.FetchPlanMismatch):
        F.SecFetcher(
            rows,
            plan_sha256="0" * 64,
            transport=transport,
            contact="audit@example.com",
        )
    assert transport.calls == []


def test_d6_t3_groups_cross_type_by_signed_offset() -> None:
    cross_type = A.escalation_tags(
        [
            _rec("S1", "B1", 2020, A.OUTCOME_OMISSION, A.REASON_MISDATED_SPLIT, 1),
            _rec("S1", "B2", 2021, A.OUTCOME_OMISSION, A.REASON_MISDATED_CASH, 1),
        ]
    )
    assert A.TAG_T3 in cross_type
    different = A.escalation_tags(
        [
            _rec("S1", "B1", 2020, A.OUTCOME_OMISSION, A.REASON_MISDATED_SPLIT, 1),
            _rec("S1", "B2", 2021, A.OUTCOME_OMISSION, A.REASON_MISDATED_CASH, -1),
        ]
    )
    assert A.TAG_T3 not in different


def test_d7_generic_transport_error_is_not_retried() -> None:
    filing = _plan_row(accession="ACC1", filename="edgar/data/1/acc1.txt")
    plan_hash = A.fetch_plan_sha256([filing])

    def _raise_oserror(url, headers):
        raise OSError("connection reset")

    transport = _FakeTransport(_raise_oserror)
    fetcher = F.SecFetcher(
        [filing],
        plan_sha256=plan_hash,
        transport=transport,
        contact="audit@example.com",
    )
    assert fetcher.fetch_all()[0].outcome == "UNRETRIEVABLE"
    assert len(transport.calls) == 1

    attempts = {"n": 0}

    def _timeout(url, headers):
        attempts["n"] += 1
        raise TimeoutError("slow")

    timeout_fetcher = F.SecFetcher(
        [filing],
        plan_sha256=plan_hash,
        transport=_FakeTransport(_timeout),
        contact="audit@example.com",
        clock=_Clock().now,
        sleep=_Clock().sleep,
    )
    assert timeout_fetcher.fetch_all()[0].outcome == "UNRETRIEVABLE"
    assert attempts["n"] == 3


def test_d8a_population_requires_uncertified_disposition() -> None:
    frame = _day_flags(
        [("K1", "AAA", date(2020, 1, 3), A.SUSPECT, "SOME_OTHER_DISPOSITION")]
    )
    with pytest.raises(A.PopulationInconsistencyError):
        A.population_records(frame)


def test_d8b_unusable_sessions_follow_disposition_semantics() -> None:
    d1, d2, d3 = date(2020, 1, 2), date(2020, 1, 3), date(2020, 1, 6)
    observations = pd.DataFrame(
        {
            "ticker": ["ABC", "ABC", "ABC"],
            "date": [pd.Timestamp(d1), pd.Timestamp(d2), pd.Timestamp(d3)],
            "close": [10.0, 8.0, 30.0],
        }
    )
    day_flags = pd.DataFrame(
        [
            # A brand-new class name with disposition CORRUPT is unusable.
            ("K1", "ABC", pd.Timestamp(d2), "BRAND_NEW_CLASS", "CORRUPT"),
            # A known CORRUPT class name without the CORRUPT disposition is usable.
            ("K1", "ABC", pd.Timestamp(d1), "OHLC_INCONSISTENT", "LEGIT"),
        ],
        columns=[
            "security_key", "ticker", "date", "class_or_flag", "disposition",
        ],
    )
    identity = pd.DataFrame(
        {
            "security_key": ["K1"],
            "ticker": ["ABC"],
            "listed_start": [pd.Timestamp(A.DEV_START)],
            "listed_end": [pd.Timestamp(A.DEV_END)],
        }
    )
    series = A.build_key_series(identity, observations, day_flags)["K1"]
    assert d2 in series.unusable
    assert d1 not in series.unusable

    p, rho = A.compute_magnitude(series, d3, [d1, d2, d3])
    assert p == d1
    assert rho == pytest.approx(3.0)


# ===========================================================================
# Repair round 2 (R1-R11)
# ===========================================================================


def _e1_e2(tmp_path, body=b"8-K split filing"):
    root, bindings = _synthetic_root(tmp_path)
    audit = tmp_path / "audit"
    A.run_e1(root, audit, bindings=bindings)
    transport = _FakeTransport([F.TransportResponse(200, body)])
    A.run_e2(root, audit, transport=transport, contact="audit@example.com")
    return root, bindings, audit, hashlib.sha256(body).hexdigest()


def _split_fact(audit, digest):
    specimen_id = pd.read_parquet(audit / "specimen.parquet")["specimen_id"][0]
    return {
        "specimen_id": str(specimen_id),
        "type": "SPLIT",
        "source": "E1",
        "accession": "acc1",
        "form": "8-K",
        "date_filed": "2020-01-03",
        "doc_sha256": digest,
        "locator": "acc1.txt",
        "quote": "reverse split 1-for-10",
        "factor": 0.1,
        "market_effective_date": "2020-01-03",
    }


def test_r1_dsc_json_file_and_field_binding(tmp_path) -> None:
    root, bindings = _synthetic_root(tmp_path)
    A.load_g6_inputs(root, bindings)
    dsc_path = root / bindings.dsc_json.relative_path

    original = dsc_path.read_bytes()
    dsc_path.write_bytes(b"tampered")
    with pytest.raises(A.BindingMismatch):
        A.load_g6_inputs(root, bindings)

    dsc = json.loads(original.decode("utf-8"))
    dsc["dsc_hash"] = "wrong"
    dsc_path.write_text(A.canonical_json(dsc) + "\n")
    with pytest.raises(A.BindingMismatch):
        A.load_g6_inputs(root, bindings)


def test_r2_qc_report_binding(tmp_path) -> None:
    root, bindings = _synthetic_root(tmp_path)
    A.load_g6_inputs(root, bindings)
    qc_path = root / bindings.qc_report_path
    report = json.loads(qc_path.read_text())

    bad_hash = dict(report)
    bad_hash["qc_hash"] = "wrong"
    qc_path.write_text(A.canonical_json(bad_hash) + "\n")
    with pytest.raises(A.BindingMismatch):
        A.load_g6_inputs(root, bindings)

    bad_files = dict(report)
    bad_files["files_sha256"] = {"day_flags.parquet": "0" * 64}
    qc_path.write_text(A.canonical_json(bad_files) + "\n")
    with pytest.raises(A.BindingMismatch):
        A.load_g6_inputs(root, bindings)


def test_r3_all_122_sec_index_files_required(tmp_path) -> None:
    names = A.expected_index_filenames()
    assert len(names) == 122
    assert names[0] == "master_1993Q1.gz"
    assert names[-1] == "master_2023Q2.gz"
    assert "master_2023Q3.gz" not in names

    root, bindings = _synthetic_root(tmp_path)
    # The 2023Q3 decoy in dsc.json has a bogus hash and no file on disk; the
    # successful load proves it is never opened.
    A.load_g6_inputs(root, bindings)

    index_dir = root / A.SEC_INDEX_DIR
    (index_dir / "master_2001Q3.gz").unlink()
    with pytest.raises(A.BindingMismatch):
        A.load_g6_inputs(root, bindings)
    (index_dir / "master_2001Q3.gz").write_bytes(
        gzip.compress(b"-----\n")
    )

    dsc_path = root / bindings.dsc_json.relative_path
    dsc = json.loads(dsc_path.read_text())
    del dsc["sec_index_files_sha256"]["master_2001Q3.gz"]
    dsc_path.write_text(A.canonical_json(dsc) + "\n")
    # Rebind dsc.json so the file hash is valid; only the missing entry fails.
    rebound = bindings.__class__(
        **{
            **bindings.__dict__,
            "dsc_json": A.ArtifactBinding(
                bindings.dsc_json.relative_path,
                hashlib.sha256(dsc_path.read_bytes()).hexdigest(),
            ),
        }
    )
    with pytest.raises(A.BindingMismatch):
        A.load_g6_inputs(root, rebound)


def test_r4_request_cap_and_evidence_unretrievable(tmp_path) -> None:
    for outcome, expected in (
        ("REQUEST_CAP_REACHED", A.REASON_REQUEST_CAP_REACHED),
        ("UNRETRIEVABLE", A.REASON_EVIDENCE_UNRETRIEVABLE),
    ):
        root, bindings = _synthetic_root(tmp_path / outcome)
        audit = tmp_path / outcome / "audit"
        A.run_e1(root, audit, bindings=bindings)
        specimen_id = pd.read_parquet(audit / "specimen.parquet")["specimen_id"][0]
        A.write_facts(
            audit / "fetch_outcomes.jsonl",
            [
                {
                    "specimen_id": str(specimen_id),
                    "accession": "acc1",
                    "outcome": outcome,
                    "doc_sha256": None,
                }
            ],
        )
        (audit / "facts_worker.jsonl").write_text("")
        (audit / "facts_planner.jsonl").write_text("")
        A.run_e3(root, audit, bindings=bindings)
        outcomes = pd.read_parquet(audit / "outcomes.parquet")
        assert set(outcomes["reason"]) == {expected}


def test_r5_evidence_binding_and_fact_ownership(tmp_path) -> None:
    root, bindings, audit, digest = _e1_e2(tmp_path)
    fact = _split_fact(audit, digest)
    A.write_facts(audit / "facts_worker.jsonl", [fact])
    A.write_facts(audit / "facts_planner.jsonl", [fact])

    # An extra cached file fails closed.
    (audit / "evidence" / "extra.txt").write_bytes(b"extra")
    with pytest.raises(A.Gate6Error):
        A.run_e3(root, audit, bindings=bindings)
    (audit / "evidence" / "extra.txt").unlink()

    # A hash mismatch fails closed.
    cache = audit / "evidence" / "acc1.txt"
    original = cache.read_bytes()
    cache.write_bytes(b"corrupted")
    with pytest.raises(A.Gate6Error):
        A.run_e3(root, audit, bindings=bindings)
    cache.write_bytes(original)

    # A fact must cite an accession from its own specimen's planned filings.
    with pytest.raises(A.FactValidationError):
        A.validate_fact(
            fact,
            specimen_ids={fact["specimen_id"]},
            evidence={"acc1": digest},
            planned_accessions={fact["specimen_id"]: {"different-accession"}},
        )


def test_r6_artifact_completeness_and_decision_independence(monkeypatch) -> None:
    monkeypatch.setenv("SEC_CONTACT", "secret@example.com")
    specimen_set = _select(
        [
            ("K1", date(2020, 1, 3), A.STRATUM_S1, A.BAND_B1),
            ("K2", date(2020, 2, 3), A.STRATUM_S1, A.BAND_B1),
            ("K3", date(2020, 3, 3), A.STRATUM_S2, A.BAND_B1),
        ]
    )
    specimens = list(specimen_set.specimens)
    records = [
        _rec(
            specimens[0].stratum, specimens[0].band, specimens[0].year,
            A.OUTCOME_OMISSION, A.REASON_OMITTED_SPLIT, sid=specimens[0].specimen_id,
        ),
        _rec(
            specimens[1].stratum, specimens[1].band, specimens[1].year,
            A.OUTCOME_UNRESOLVED, A.REASON_NO_EXPLANATORY_FILING,
            sid=specimens[1].specimen_id,
        ),
        _rec(
            specimens[2].stratum, specimens[2].band, specimens[2].year,
            A.OUTCOME_COMPLEX, "SPINOFF", sid=specimens[2].specimen_id,
        ),
    ]
    worker_facts = [
        {"type": "TICKER_LINK", "specimen_id": specimens[0].specimen_id}
    ]
    planner_facts: list = []
    procedure = {
        "worker_runtime": "pi",
        "worker_model": "deepseek-v4-flash",
        "planner_model": "claude",
        "herdr_workspace": "w1",
        "timestamp": "2026-09-30T00:00:00Z",
        "contact": "secret@example.com",
        "note": "reach me at secret@example.com",
    }
    artifacts = A.build_g6_artifacts(
        population_sha256="p" * 64,
        specimen_sha256=A.specimen_sha256(specimen_set),
        fetch_plan_sha256="f" * 64,
        specimen_set=specimen_set,
        records=records,
        decision=A.decide_gate6(records),
        mapping_state_counts={"MAPPED": 3},
        evidence={"acc1": "a" * 64},
        worker_facts=worker_facts,
        planner_facts=planner_facts,
        bindings_manifest={"day_flags_sha256": "d" * 64},
        procedure=procedure,
    )
    for key in (
        "population_sha256",
        "specimen_sha256",
        "fetch_plan_sha256",
        "evidence_sha256",
        "worker_facts_sha256",
        "planner_facts_sha256",
        "outcomes_sha256",
        "bindings",
    ):
        assert key in artifacts["hashes"]
    assert artifacts["unresolved_by_reason"] == {A.REASON_NO_EXPLANATORY_FILING: 1}
    assert artifacts["unresolved_by_year"] == {"2020": 1}
    assert artifacts["unresolved_by_band"] == {A.BAND_B1: 1}
    assert set(artifacts["resolution_rates"]) == {"S1", "S2", "S3"}
    assert artifacts["resolution_rates"]["S2"]["n"] == 1
    assert artifacts["covariates"]["ticker_link_present_worker"] == 1
    assert artifacts["covariates"]["ticker_link_present_planner"] == 0
    assert set(artifacts["covariates"]["admission_context"]) == {"S1", "S2", "S3"}
    assert "timestamp" not in artifacts["procedure"]
    assert "contact" not in artifacts["procedure"]
    assert "note" not in artifacts["procedure"]
    assert "secret@example.com" not in A.canonical_json(artifacts)
    assert artifacts["decision"] == A.decide_gate6(records).decision

    # The reported extras never affect the decision.
    bare = A.build_g6_artifacts(
        population_sha256="p" * 64,
        specimen_sha256=A.specimen_sha256(specimen_set),
        fetch_plan_sha256="f" * 64,
        specimen_set=specimen_set,
        records=records,
        decision=A.decide_gate6(records),
        mapping_state_counts={"MAPPED": 3},
    )
    assert bare["decision"] == artifacts["decision"]
    assert A.decide_gate6(records).decision == artifacts["decision"]


def test_r6_clopper_pearson_known_values() -> None:
    lower, upper = A.clopper_pearson(0, 120)
    assert lower == 0.0
    assert upper == pytest.approx(0.03027297257742007, abs=1e-9)
    assert A._cp_upper_bisect(0, 120, 0.05) == pytest.approx(
        0.03027297257742007, abs=1e-6
    )
    lower6, upper6 = A.clopper_pearson(6, 120)
    assert 0.0 < lower6 < upper6 < 1.0


def test_r7_fact_refs() -> None:
    out = A.run_rule_engine(
        _engine(
            facts=[
                _fact(
                    "SPLIT", factor=0.1,
                    market_effective_date=date(2020, 1, 3),
                )
            ]
        )
    )
    assert out.fact_refs == ("ACC1|doc.txt",)
    unresolved = A.run_rule_engine(_engine(facts=[]))
    assert unresolved.fact_refs == ()


def test_r7_e3_outcomes_record_worker_and_planner_refs(tmp_path) -> None:
    root, bindings, audit, digest = _e1_e2(tmp_path)
    fact = _split_fact(audit, digest)
    A.write_facts(audit / "facts_worker.jsonl", [fact])
    A.write_facts(audit / "facts_planner.jsonl", [])
    A.run_e3(root, audit, bindings=bindings)
    outcomes = pd.read_parquet(audit / "outcomes.parquet")
    row = outcomes.iloc[0]
    assert list(row["worker_fact_refs"]) == ["acc1|acc1.txt"]
    assert list(row["planner_fact_refs"]) == []
    # Disagreement -> the final UNRESOLVED has no deciding fact.
    assert list(row["fact_refs"]) == []


def test_r8_audit_dir_derivation(tmp_path) -> None:
    root, bindings = _synthetic_root(tmp_path)
    derived = A.derive_audit_dir(root, bindings, code_sha256_value="c" * 64)
    assert derived.parent.name == "qc_audit"
    assert derived.parent.parent == root / "derived"
    assert derived.name.startswith("g6-")
    assert len(derived.name) == len("g6-") + 16
    assert A.resolve_audit_dir(
        root, derived, bindings=bindings, code_sha256_value="c" * 64
    ) == derived
    with pytest.raises(A.Gate6Error):
        A.resolve_audit_dir(
            root,
            tmp_path / "somewhere-else",
            bindings=bindings,
            code_sha256_value="c" * 64,
        )


def test_r9_resume_skips_verified_cache(tmp_path) -> None:
    root, bindings, audit, digest = _e1_e2(tmp_path / "run1")

    calls = {"n": 0}

    def _must_not_fetch(url, headers):
        calls["n"] += 1
        return F.TransportResponse(200, b"should-not-be-used")

    A.run_e2(
        root,
        audit,
        transport=_FakeTransport(_must_not_fetch),
        contact="audit@example.com",
    )
    assert calls["n"] == 0
    outcomes = A.read_fetch_outcomes(audit)
    assert outcomes[0]["outcome"] == "CACHED"

    # A corrupted cached file is re-fetched exactly once.
    (audit / "evidence" / "acc1.txt").write_bytes(b"corrupted")
    repair_calls = {"n": 0}

    def _repair(url, headers):
        repair_calls["n"] += 1
        return F.TransportResponse(200, b"repaired")

    A.run_e2(
        root,
        audit,
        transport=_FakeTransport(_repair),
        contact="audit@example.com",
    )
    assert repair_calls["n"] == 1
    assert (audit / "evidence" / "acc1.txt").read_bytes() == b"repaired"


def test_r10_retry_classes() -> None:
    filing = _plan_row(accession="ACC1", filename="edgar/data/1/acc1.txt")
    plan_hash = A.fetch_plan_sha256([filing])

    def _fetcher(script):
        return F.SecFetcher(
            [filing],
            plan_sha256=plan_hash,
            transport=_FakeTransport(script),
            contact="audit@example.com",
            clock=_Clock().now,
            sleep=_Clock().sleep,
        )

    for status in (500, 503, 599, 429):
        transport = _FakeTransport(
            [F.TransportResponse(status), F.TransportResponse(200, b"ok")]
        )
        fetcher = F.SecFetcher(
            [filing],
            plan_sha256=plan_hash,
            transport=transport,
            contact="audit@example.com",
            clock=_Clock().now,
            sleep=_Clock().sleep,
        )
        assert fetcher.fetch_all()[0].outcome == "FETCHED"
        assert len(transport.calls) == 2

    for status in (400, 403, 404, 418):
        transport = _FakeTransport([F.TransportResponse(status)])
        fetcher = F.SecFetcher(
            [filing],
            plan_sha256=plan_hash,
            transport=transport,
            contact="audit@example.com",
            clock=_Clock().now,
            sleep=_Clock().sleep,
        )
        assert fetcher.fetch_all()[0].outcome == "UNRETRIEVABLE"
        assert len(transport.calls) == 1

    timeout_calls = {"n": 0}

    def _timeout(url, headers):
        timeout_calls["n"] += 1
        raise TimeoutError("slow")

    fetcher = F.SecFetcher(
        [filing],
        plan_sha256=plan_hash,
        transport=_FakeTransport(_timeout),
        contact="audit@example.com",
        clock=_Clock().now,
        sleep=_Clock().sleep,
    )
    assert fetcher.fetch_all()[0].outcome == "UNRETRIEVABLE"
    assert timeout_calls["n"] == 3

    urlerror_timeout_calls = {"n": 0}

    def _url_timeout(url, headers):
        urlerror_timeout_calls["n"] += 1
        raise urllib.error.URLError(TimeoutError("timed out"))

    fetcher = F.SecFetcher(
        [filing],
        plan_sha256=plan_hash,
        transport=_FakeTransport(_url_timeout),
        contact="audit@example.com",
        clock=_Clock().now,
        sleep=_Clock().sleep,
    )
    assert fetcher.fetch_all()[0].outcome == "UNRETRIEVABLE"
    assert urlerror_timeout_calls["n"] == 3

    urlerror_calls = {"n": 0}

    def _url_error(url, headers):
        urlerror_calls["n"] += 1
        raise urllib.error.URLError(OSError("connection reset"))

    fetcher = F.SecFetcher(
        [filing],
        plan_sha256=plan_hash,
        transport=_FakeTransport(_url_error),
        contact="audit@example.com",
        clock=_Clock().now,
        sleep=_Clock().sleep,
    )
    assert fetcher.fetch_all()[0].outcome == "UNRETRIEVABLE"
    assert urlerror_calls["n"] == 1


def test_r11_missing_extraction_file_fails_closed(tmp_path) -> None:
    root, bindings_ = _synthetic_root(tmp_path)
    audit = tmp_path / "audit"
    A.run_e1(root, audit, bindings=bindings_)
    with pytest.raises(A.Gate6Error):
        A.run_e3(root, audit, bindings=bindings_)

    # An empty but present file is allowed.
    (audit / "facts_worker.jsonl").write_text("")
    (audit / "facts_planner.jsonl").write_text("")
    A.run_e3(root, audit, bindings=bindings_)


def test_r12_cumulative_cap_and_log_append(tmp_path) -> None:
    root, bindings = _synthetic_root(tmp_path)
    audit = tmp_path / "audit"
    A.run_e1(root, audit, bindings=bindings)
    log_path = audit / "retrieval_log.jsonl"

    # First run consumes the whole cap (one request).
    transport = _FakeTransport([F.TransportResponse(200, b"body")])
    first = A.run_e2(
        root,
        audit,
        transport=transport,
        contact="audit@example.com",
        max_requests=1,
    )
    assert first["requests"] == 1
    first_log = log_path.read_text()
    assert len([line for line in first_log.splitlines() if line.strip()]) == 1

    # Resume: the cap is already consumed.  Corrupt the cache so a re-fetch
    # would otherwise be attempted; prove no transport call happens, the total
    # never exceeds the cap, and the earlier log lines are preserved.
    (audit / "evidence" / "acc1.txt").write_bytes(b"corrupted")
    calls = {"n": 0}

    def _must_not_fetch(url, headers):
        calls["n"] += 1
        return F.TransportResponse(200, b"should-not-be-used")

    resumed = A.run_e2(
        root,
        audit,
        transport=_FakeTransport(_must_not_fetch),
        contact="audit@example.com",
        max_requests=1,
    )
    assert calls["n"] == 0
    assert resumed["requests"] == 1 <= 1
    outcomes = A.read_fetch_outcomes(audit)
    assert outcomes[0]["outcome"] == "REQUEST_CAP_REACHED"
    assert log_path.read_text() == first_log

    # A fetcher initialised at the cap makes no request at all.
    filing = _plan_row(accession="ACC1", filename="edgar/data/1/acc1.txt")
    empty_transport = _FakeTransport([])
    capped = F.SecFetcher(
        [filing],
        plan_sha256=A.fetch_plan_sha256([filing]),
        transport=empty_transport,
        contact="audit@example.com",
        max_requests=2,
        initial_requests=2,
    )
    assert capped.fetch_all()[0].outcome == "REQUEST_CAP_REACHED"
    assert empty_transport.calls == []


def test_r13_one_get_per_shared_filing(tmp_path) -> None:
    shared = _plan_row(
        specimen_id="g6-0000", accession="ACC1",
        filename="edgar/data/1/acc1.txt",
    )
    duplicate = _plan_row(
        specimen_id="g6-0001", accession="ACC1",
        filename="edgar/data/1/acc1.txt",
    )
    plan = [shared, duplicate]
    body = b"shared-body"
    digest = hashlib.sha256(body).hexdigest()
    transport = _FakeTransport([F.TransportResponse(200, body)])
    fetcher = F.SecFetcher(
        plan,
        plan_sha256=A.fetch_plan_sha256(plan),
        transport=transport,
        contact="audit@example.com",
        cache_dir=tmp_path,
    )
    outcomes = fetcher.fetch_all()
    assert len(transport.calls) == 1
    assert outcomes[0].outcome == "FETCHED"
    assert outcomes[1].outcome == "CACHED"
    assert outcomes[0].doc_sha256 == digest
    assert outcomes[1].doc_sha256 == digest
    assert outcomes[1].specimen_id == "g6-0001"

    # A failed first retrieval is shared too, but is not mislabelled CACHED.
    fail_transport = _FakeTransport([F.TransportResponse(404)])
    fail_fetcher = F.SecFetcher(
        plan,
        plan_sha256=A.fetch_plan_sha256(plan),
        transport=fail_transport,
        contact="audit@example.com",
    )
    failed = fail_fetcher.fetch_all()
    assert len(fail_transport.calls) == 1
    assert failed[0].outcome == "UNRETRIEVABLE"
    assert failed[1].outcome == "UNRETRIEVABLE"


# ===========================================================================
# Repair round 4 (G1-G7)
# ===========================================================================


def test_g1_s3_boundary_three_sessions_yes_four_no() -> None:
    sessions = A.default_calendar()
    t = date(2020, 1, 8)
    assert t in sessions
    pos = {d: i for i, d in enumerate(sessions)}
    three_back = sessions[pos[t] - 3]
    four_back = sessions[pos[t] - 4]

    def _ca(day):
        return pd.DataFrame(
            {
                "stock_id": ["ABC"],
                "effective_date": [pd.Timestamp(day)],
                "action_type": ["split"],
                "adjustment_factor": [0.5],
            }
        )

    near, near_offset = A.assign_stratum(
        "K1",
        t,
        ticker="ABC",
        ca_events=_ca(three_back),
        sessions=sessions,
        listed_start=A.DEV_START,
        listed_end=A.DEV_END,
        admitted=set(),
    )
    assert near == A.STRATUM_S3
    assert near_offset == -3

    far, _ = A.assign_stratum(
        "K1",
        t,
        ticker="ABC",
        ca_events=_ca(four_back),
        sessions=sessions,
        listed_start=A.DEV_START,
        listed_end=A.DEV_END,
        admitted=set(),
    )
    assert far == A.STRATUM_S2


def test_g2_engine_ignores_non_e1_facts_passed_directly() -> None:
    context_facts = A.tiingo_context_facts(
        {
            "material_events": [
                {
                    "event_type": "EARNINGS_OR_GUIDANCE",
                    "event_date": date(2020, 1, 3),
                }
            ]
        }
    )
    assert context_facts
    assert all(fact["source"] == "TIINGO" for fact in context_facts)
    out = A.run_rule_engine(_engine(facts=context_facts))
    assert out == A.Outcome(A.OUTCOME_UNRESOLVED, A.REASON_NO_EXPLANATORY_FILING)


def test_g3_fact_rejected_when_accession_absent_from_evidence() -> None:
    fact = _fact(
        "SPLIT",
        accession="ACC1",
        doc_sha256="self-consistent",
        factor=0.1,
        market_effective_date=date(2020, 1, 3),
    )
    with pytest.raises(A.FactValidationError):
        A.validate_fact(
            fact,
            specimen_ids={"g6-0001"},
            evidence={},
            planned_accessions={"g6-0001": {"ACC1"}},
        )


def test_g4_cap_reached_mid_retry_stops_transport() -> None:
    filing = _plan_row(accession="ACC1", filename="edgar/data/1/acc1.txt")
    transport = _FakeTransport([F.TransportResponse(500)])
    fetcher = F.SecFetcher(
        [filing],
        plan_sha256=A.fetch_plan_sha256([filing]),
        transport=transport,
        contact="audit@example.com",
        max_requests=1,
        clock=_Clock().now,
        sleep=_Clock().sleep,
    )
    outcome = fetcher.fetch_all()[0]
    assert outcome.outcome == "REQUEST_CAP_REACHED"
    assert len(transport.calls) == 1


def _bound_input_paths(root, bindings):
    paths = [
        root / bindings.day_flags.relative_path,
        root / bindings.observations.relative_path,
        root / bindings.ca_events.relative_path,
        root / bindings.identity_assignment.relative_path,
        root / bindings.daily_total_return.relative_path,
        root / bindings.issuer_mapping.relative_path,
        root / bindings.dsc_json.relative_path,
        root / bindings.qc_report_path,
    ]
    index_dir = root / A.SEC_INDEX_DIR
    paths.extend(index_dir / name for name in A.expected_index_filenames())
    return paths


def test_g5_no_bound_input_remediation(tmp_path) -> None:
    root, bindings = _synthetic_root(tmp_path)
    paths = _bound_input_paths(root, bindings)
    before = {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    }

    audit = tmp_path / "audit"
    A.run_e1(root, audit, bindings=bindings)
    body = b"8-K split filing"
    A.run_e2(
        root,
        audit,
        transport=_FakeTransport([F.TransportResponse(200, body)]),
        contact="audit@example.com",
    )
    digest = hashlib.sha256(body).hexdigest()
    fact = _split_fact(audit, digest)
    A.write_facts(audit / "facts_worker.jsonl", [fact])
    A.write_facts(audit / "facts_planner.jsonl", [fact])
    A.run_e3(root, audit, bindings=bindings)

    after = {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    }
    assert before == after


def test_g6_out_of_interval_split_ignored(tmp_path) -> None:
    sessions = A.default_calendar()
    t = date(2020, 2, 3)
    start, end = date(2020, 2, 1), date(2020, 3, 1)
    pos = {d: i for i, d in enumerate(sessions)}
    outside = sessions[pos[t] - 2]
    assert not (start <= outside <= end)
    ca = pd.DataFrame(
        {
            "stock_id": ["ABC"],
            "effective_date": [pd.Timestamp(outside)],
            "action_type": ["split"],
            "adjustment_factor": [0.1],
        }
    )
    events = A.split_events_for("ABC", start, end, ca)
    assert events == []

    stratum, _ = A.assign_stratum(
        "K1",
        t,
        ticker="ABC",
        ca_events=ca,
        sessions=sessions,
        listed_start=start,
        listed_end=end,
        admitted=set(),
    )
    assert stratum == A.STRATUM_S2

    out = A.run_rule_engine(
        _engine(
            security_key="K1",
            ticker="ABC",
            t=t,
            sessions=sessions,
            facts=[
                _fact(
                    "SPLIT", factor=0.1, market_effective_date=t
                )
            ],
            split_events=events,
        )
    )
    assert out == A.Outcome(A.OUTCOME_OMISSION, A.REASON_OMITTED_SPLIT)


def test_g7_dsc_hash_field_mismatch_with_rebound_file_sha(tmp_path) -> None:
    root, bindings = _synthetic_root(tmp_path)
    dsc_path = root / bindings.dsc_json.relative_path
    dsc = json.loads(dsc_path.read_text())
    dsc["dsc_hash"] = "wrong"
    dsc_path.write_text(A.canonical_json(dsc) + "\n")
    rebound = replace(
        bindings,
        dsc_json=A.ArtifactBinding(
            bindings.dsc_json.relative_path,
            hashlib.sha256(dsc_path.read_bytes()).hexdigest(),
        ),
    )
    # The file SHA-256 now matches, so only the dsc_hash field is wrong.
    with pytest.raises(A.BindingMismatch):
        A.load_g6_inputs(root, rebound)


