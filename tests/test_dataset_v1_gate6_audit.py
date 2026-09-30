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


def _artifacts():
    specimen_set = _one_set()
    specimen = specimen_set.specimens[0]
    decision = A.decide_gate6(
        [
            _rec(
                specimen.stratum,
                specimen.band,
                specimen.year,
                A.OUTCOME_UNRESOLVED,
            )
        ]
    )
    return A.build_g6_artifacts(
        population_sha256="p" * 64,
        specimen_sha256=A.specimen_sha256(specimen_set),
        fetch_plan_sha256="f" * 64,
        specimen_set=specimen_set,
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
        decision=A.decide_gate6([]),
        mapping_state_counts={"MAPPED": 1},
        procedure={"rho_example": 0.123456},
    )
    second = A.build_g6_artifacts(
        population_sha256="p" * 64,
        specimen_sha256=A.specimen_sha256(_one_set()),
        fetch_plan_sha256="f" * 64,
        specimen_set=_one_set(),
        decision=A.decide_gate6([]),
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


def _filings():
    return [
        F.PlanFiling(
            "g6-0001", "1", "ACC1", "/Archives/edgar/data/1/acc1.txt"
        ),
        F.PlanFiling(
            "g6-0001", "1", "ACC2", "/Archives/edgar/data/1/acc2.txt"
        ),
    ]


def test_55_host_path_allowlist_refuses_off_plan() -> None:
    filings = _filings()
    transport = _FakeTransport([])
    fetcher = F.SecFetcher(
        filings,
        plan_sha256=F.plan_sha256(filings),
        transport=transport,
        contact="audit@example.com",
    )
    off_plan = F.PlanFiling(
        "g6-0002", "2", "ACC9", "/Archives/edgar/data/2/acc9.txt"
    )
    with pytest.raises(F.UrlRefused):
        fetcher.fetch_filing(off_plan)
    assert transport.calls == []

    weird = F.PlanFiling("g6-0001", "1", "ACC1", "evil.example/path")
    weird_transport = _FakeTransport([])
    weird_fetcher = F.SecFetcher(
        [weird],
        plan_sha256=F.plan_sha256([weird]),
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
            plan_sha256=F.plan_sha256(filings),
            transport=_FakeTransport([]),
            contact=None,
        )
    monkeypatch.setenv("SEC_CONTACT", "audit@example.com")
    transport = _FakeTransport(
        [F.TransportResponse(200, b"body-1"), F.TransportResponse(200, b"body-2")]
    )
    fetcher = F.SecFetcher(
        filings,
        plan_sha256=F.plan_sha256(filings),
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
    plan_hash = F.plan_sha256(filings)

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
        plan_sha256=F.plan_sha256([filings[0]]),
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
        plan_sha256=F.plan_sha256([filings[0]]),
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
    filing = F.PlanFiling(
        "g6-0001", "1", "ACC1", "/Archives/edgar/data/1/acc1.txt"
    )
    plan_hash = F.plan_sha256([filing])
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
        plan_sha256=F.plan_sha256(filings),
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
