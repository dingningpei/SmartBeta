"""E-2 tests — Dataset v1.3 identity map (IM) and IM-P precision audit.

The suite is offline.  An autouse guard replaces ``urllib.request.urlopen``,
``socket.socket.connect`` and ``socket.create_connection`` with a raiser and
scrubs the eight provider credentials plus ``SEC_CONTACT`` from the process
environment, so no test can reach the network.  Every input is a synthetic
fixture or an in-memory record; ``data_store/`` is never read.

Covers plan §4 (Routes row and IM-P row): the ticker route; the name route
with the ``formerNames`` interval overlap; verification (exactly one
candidate with periodic filings in the interval); all five statuses; ticker
reuse (the D4 B-R1 cases); unmapped keys retained; and the §8 IM-P
evidence-sufficiency rule with the seeded 200 (100/100) sample and the
one-sided 95% Clopper-Pearson upper bound.
"""

from __future__ import annotations

import json
import math
import socket
import sys
import urllib.request
from datetime import date
from pathlib import Path

import pytest

from scipy.stats import beta

from smart_beta.dataset_v1 import edgar_identity as I

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "edgar" / "identity"
RAW = FIXTURES / "raw"
MUTATIONS = FIXTURES / "mutations.json"


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forbid network egress and scrub every credential plus the contact."""

    def _refuse(*args: object, **kwargs: object) -> object:
        raise AssertionError("network access is forbidden in E-2 tests")

    monkeypatch.setattr(urllib.request, "urlopen", _refuse)
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    for name in (
        "TIINGO_API_KEY",
        "TUSHARE_PROXY_TOKEN",
        "TUSHARE_BASIC_PROXY_TOKEN",
        "TUSHARE_API_TOKEN",
        "DEEPSEEK_API_KEY",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "OPENAI_API_KEY",
        "SEC_CONTACT",
    ):
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _key(security_key, ticker=None, start=None, end=None, names=()):
    return I.IdentityKey(
        security_key=security_key,
        ticker=ticker,
        interval_start=start,
        interval_end=end,
        issuer_names=tuple(names),
    )


def _filing(form: str, filed: str, accession: str | None = None) -> dict:
    return {
        "form": form,
        "filingDate": filed,
        "accessionNumber": accession or f"{form}-{filed}",
    }


def _view(cik, entity=None, formers=(), filings=()):
    return I.SubmissionView(
        cik=cik,
        entity_name=entity,
        former_names=tuple(formers),
        filings=tuple(filings),
    )


def _resolve(keys, *, company=None, lookup=None, submissions=None, names=None):
    return I.resolve_identity(
        keys,
        company_tickers=company or {},
        cik_lookup=lookup or {},
        submissions=submissions or {},
        issuer_names=names or {},
    )


def _by_key(rows):
    return {row.security_key: row for row in rows}


# The ticker-reuse fixture shared by the B-R1 tests.
def _reuse_world():
    submissions = {
        111: _view(
            111,
            entity="First Zyx Inc.",
            filings=(
                _filing("10-Q", "2017-05-01"),
                _filing("10-K", "2018-02-01"),
            ),
        ),
        222: _view(
            222,
            entity="Second Zyx Inc.",
            filings=(_filing("10-Q", "2020-05-01"),),
        ),
    }
    company = {"ZYX": (111, 222)}
    return company, {}, submissions


# ---------------------------------------------------------------------------
# Constants and schema (plan §3)
# ---------------------------------------------------------------------------


def test_frozen_vocabulary_and_columns() -> None:
    assert I.STATUSES == (
        "MAPPED",
        "AMBIGUOUS",
        "NO_FILINGS_IN_INTERVAL",
        "NO_CANDIDATE",
        "FOREIGN_FILER",
    )
    assert I.AUDIT_VERDICTS == (
        "CONFIRMED",
        "CONTRADICTED",
        "NO_SYMBOL_EVIDENCE",
        "NOT_SAMPLED",
    )
    assert I.ROUTES == ("TICKER", "NAME")
    assert I.IDENTITY_COLUMNS == (
        "security_key",
        "cik",
        "status",
        "route",
        "interval_start",
        "interval_end",
        "n_verified_candidates",
        "audit_verdict",
    )
    assert I.IM_P_SAMPLE_SIZE == 200
    assert I.IM_P_PER_STRATUM == 100
    assert I.IM_P_MIN_EVIDENCE_TOTAL == 120
    assert I.IM_P_MIN_EVIDENCE_ENDED == 50
    assert I.IM_P_MAX_CONTRADICTED_RATE == 0.01
    assert I.IM_P_MAX_UPPER_BOUND == 0.03


# ---------------------------------------------------------------------------
# Raw-layout loader (E-1 format, read-only)
# ---------------------------------------------------------------------------


def test_load_edgar_reference_reads_e1_layout() -> None:
    ref = I.load_edgar_reference(RAW)
    assert ref.company_tickers["ZYX"] == (111,)
    assert ref.cik_lookup["ex why zee"] == (1234567,)
    assert ref.cik_lookup["nova systems"] == (777,)
    assert set(ref.submissions) == {111, 1234567, 777}
    # The current issuer name is the real top-level ``name`` key.
    assert ref.submissions[111].entity_name == "Zyx First Inc."
    assert ref.submissions[1234567].entity_name == "Ex Why Zee Holdings"
    assert ref.submissions[777].entity_name == "Nova Systems Inc."
    # The former-name bounds are the real ISO-8601 datetimes.
    assert ref.submissions[1234567].former_names == (
        ("Ex Why Zee Corp", date(2010, 1, 1), date(2018, 1, 1)),
    )
    # The paginated files/*.json rows (real top-level arrays) are merged.
    view = ref.submissions[111]
    assert {row["form"] for row in view.filings} == {"10-Q", "10-K"}
    assert ref.issuer_names["K-EX"] == ("Ex Why Zee Corp",)
    assert ref.issuer_names["K-NOVA"] == ("Nova Systems Inc.",)


def test_resolve_from_raw_fixture_ticker_and_name_routes() -> None:
    ref = I.load_edgar_reference(RAW)
    keys = [
        _key("K-ZYX", "ZYX", date(2016, 1, 1), date(2017, 12, 31)),
        _key("K-EX", "EXWQ", date(2015, 1, 1), date(2017, 12, 31)),
    ]
    rows = I.resolve_identity(
        keys,
        company_tickers=ref.company_tickers,
        cik_lookup=ref.cik_lookup,
        submissions=ref.submissions,
        issuer_names=ref.issuer_names,
    )
    by_key = _by_key(rows)
    assert by_key["K-ZYX"].status == I.STATUS_MAPPED
    assert by_key["K-ZYX"].cik == 111
    assert by_key["K-ZYX"].route == I.ROUTE_TICKER
    assert by_key["K-EX"].status == I.STATUS_MAPPED
    assert by_key["K-EX"].cik == 1234567
    assert by_key["K-EX"].route == I.ROUTE_NAME


def test_parse_tiingo_names_accepts_natural_shapes() -> None:
    assert I.parse_tiingo_names('{"K": ["A", "B"]}') == {"K": ("A", "B")}
    assert I.parse_tiingo_names({"K": {"name": "Solo"}}) == {"K": ("Solo",)}
    assert I.parse_tiingo_names({"K": "Solo"}) == {"K": ("Solo",)}
    assert I.parse_tiingo_names(
        [{"security_key": "K", "issuer_names": ["A", "B"]}]
    ) == {"K": ("A", "B")}
    assert I.parse_tiingo_names([{"security_key": "K", "name": "A"}]) == {
        "K": ("A",)
    }
    assert I.parse_tiingo_names({}) == {}


# ---------------------------------------------------------------------------
# Routes (plan §4)
# ---------------------------------------------------------------------------


def test_ticker_route_uses_company_tickers() -> None:
    company, lookup, submissions = _reuse_world()
    rows = _resolve(
        [_key("K1", "ZYX", date(2016, 1, 1), date(2018, 12, 31))],
        company=company,
        lookup=lookup,
        submissions=submissions,
    )
    row = rows[0]
    assert row.route == I.ROUTE_TICKER
    assert row.status == I.STATUS_MAPPED
    assert row.cik == 111


def test_name_route_uses_former_names_interval_overlap() -> None:
    submissions = {
        10: _view(
            10,
            entity="Ex Why Zee Holdings",
            formers=(("Ex Why Zee Corp", date(2010, 1, 1), date(2018, 1, 1)),),
            filings=(_filing("10-Q", "2016-06-01"),),
        )
    }
    lookup = {"ex why zee": (10,)}
    rows = _resolve(
        [_key("K-NAME", "EXWQ", date(2015, 1, 1), date(2017, 12, 31), ["Ex Why Zee Corp"])],
        lookup=lookup,
        submissions=submissions,
    )
    assert rows[0].route == I.ROUTE_NAME
    assert rows[0].status == I.STATUS_MAPPED
    assert rows[0].cik == 10


def test_name_route_requires_interval_overlap() -> None:
    # The former-name interval [2010, 2018] does not overlap the key's
    # [2019, 2020] listing; the CIK must be excluded even though it filed a
    # periodic report inside the key interval (mutation E2-M3).
    submissions = {
        1234567: _view(
            1234567,
            entity="Ex Why Zee Holdings",
            formers=(("Ex Why Zee Corp", date(2010, 1, 1), date(2018, 1, 1)),),
            filings=(_filing("10-Q", "2019-05-01"),),
        )
    }
    lookup = {"ex why zee": (1234567,)}
    rows = _resolve(
        [
            _key(
                "K-LATE",
                "EXWQ",
                date(2019, 1, 1),
                date(2020, 12, 31),
                ["Ex Why Zee Corp"],
            )
        ],
        lookup=lookup,
        submissions=submissions,
    )
    assert rows[0].status == I.STATUS_NO_CANDIDATE
    assert rows[0].cik is None


def test_name_route_keeps_current_name_open_ended() -> None:
    submissions = {
        10: _view(
            10,
            entity="Ex Why Zee Corp",
            formers=(),
            filings=(_filing("10-Q", "2016-06-01"),),
        )
    }
    rows = _resolve(
        [_key("K", "EXWQ", date(2015, 1, 1), date(2017, 1, 1), ["Ex Why Zee Corp"])],
        lookup={"ex why zee": (10,)},
        submissions=submissions,
    )
    assert rows[0].status == I.STATUS_MAPPED and rows[0].cik == 10


def test_name_route_uses_real_current_name_key() -> None:
    """The real submissions key is ``name``, never ``entityName``.

    CIK 777's current ``name`` matches the key's issuer name and its
    ``formerNames`` is empty, so reading ``entityName`` (mutation E2-M5)
    drops the only candidate and the key becomes NO_CANDIDATE.
    """
    ref = I.load_edgar_reference(RAW)
    rows = I.resolve_identity(
        [_key("K-NOVA", "NOVA", date(2015, 1, 1), date(2017, 12, 31))],
        company_tickers=ref.company_tickers,
        cik_lookup=ref.cik_lookup,
        submissions=ref.submissions,
        issuer_names=ref.issuer_names,
    )
    assert rows[0].route == I.ROUTE_NAME
    assert rows[0].status == I.STATUS_MAPPED
    assert rows[0].cik == 777
    assert ref.submissions[777].entity_name == "Nova Systems Inc."


# ---------------------------------------------------------------------------
# Verification and all five statuses (plan §4)
# ---------------------------------------------------------------------------


def test_verification_unique_candidate_is_mapped() -> None:
    company, lookup, submissions = _reuse_world()
    row = _by_key(
        _resolve(
            [_key("K1", "ZYX", date(2016, 1, 1), date(2018, 12, 31))],
            company=company,
            lookup=lookup,
            submissions=submissions,
        )
    )["K1"]
    assert row.status == I.STATUS_MAPPED
    assert row.n_verified_candidates == 1


def test_ambiguous_key_is_not_mapped_to_first_candidate() -> None:
    company, lookup, submissions = _reuse_world()
    row = _by_key(
        _resolve(
            [_key("K-OVER", "ZYX", date(2017, 1, 1), date(2020, 12, 31))],
            company=company,
            lookup=lookup,
            submissions=submissions,
        )
    )["K-OVER"]
    assert row.status == I.STATUS_AMBIGUOUS
    assert row.cik is None
    assert row.n_verified_candidates == 2


def test_no_filings_in_interval_status() -> None:
    company = {"ZZZ": (333,)}
    submissions = {333: _view(333, filings=(_filing("10-Q", "2005-05-01"),))}
    row = _resolve(
        [_key("K-NF", "ZZZ", date(2016, 1, 1), date(2017, 12, 31))],
        company=company,
        submissions=submissions,
    )[0]
    assert row.status == I.STATUS_NO_FILINGS_IN_INTERVAL
    assert row.cik is None
    assert row.n_verified_candidates == 0


def test_no_candidate_status() -> None:
    row = _resolve([_key("K-NONE", "NOPE", date(2016, 1, 1), date(2017, 1, 1))])[0]
    assert row.status == I.STATUS_NO_CANDIDATE
    assert row.route is None
    assert row.cik is None


def test_foreign_filer_status() -> None:
    company = {"FPI": (444,)}
    submissions = {
        444: _view(
            444,
            entity="Foreign Issuer PLC",
            filings=(
                _filing("20-F", "2017-03-01"),
                _filing("6-K", "2017-06-01"),
            ),
        )
    }
    row = _resolve(
        [_key("K-FPI", "FPI", date(2016, 1, 1), date(2018, 1, 1))],
        company=company,
        submissions=submissions,
    )[0]
    assert row.status == I.STATUS_FOREIGN_FILER
    assert row.cik == 444
    assert row.n_verified_candidates == 1


def test_foreign_filer_with_domestic_filing_is_mapped() -> None:
    company = {"MIX": (555,)}
    submissions = {
        555: _view(
            555,
            filings=(_filing("20-F", "2017-03-01"), _filing("10-K", "2018-02-01")),
        )
    }
    row = _resolve(
        [_key("K-MIX", "MIX", date(2016, 1, 1), date(2018, 6, 1))],
        company=company,
        submissions=submissions,
    )[0]
    assert row.status == I.STATUS_MAPPED
    assert row.cik == 555


def test_all_five_statuses_are_reachable() -> None:
    company, lookup, submissions = _reuse_world()
    company = dict(company)
    company["ZZZ"] = (333,)
    company["FPI"] = (444,)
    submissions = dict(submissions)
    submissions[333] = _view(333, filings=(_filing("10-Q", "2005-05-01"),))
    submissions[444] = _view(444, filings=(_filing("20-F", "2017-03-01"),))
    keys = [
        _key("MAPPED", "ZYX", date(2016, 1, 1), date(2018, 12, 31)),
        _key("AMBIGUOUS", "ZYX", date(2017, 1, 1), date(2020, 12, 31)),
        _key("NO_FILINGS", "ZZZ", date(2016, 1, 1), date(2017, 12, 31)),
        _key("NO_CANDIDATE", "NOPE", date(2016, 1, 1), date(2017, 1, 1)),
        _key("FOREIGN", "FPI", date(2016, 1, 1), date(2018, 1, 1)),
    ]
    statuses = {row.security_key: row.status for row in _resolve(
        keys, company=company, lookup=lookup, submissions=submissions
    )}
    assert statuses == {
        "MAPPED": I.STATUS_MAPPED,
        "AMBIGUOUS": I.STATUS_AMBIGUOUS,
        "NO_FILINGS": I.STATUS_NO_FILINGS_IN_INTERVAL,
        "NO_CANDIDATE": I.STATUS_NO_CANDIDATE,
        "FOREIGN": I.STATUS_FOREIGN_FILER,
    }


# ---------------------------------------------------------------------------
# Ticker reuse — the D4 B-R1 cases (plan §4)
# ---------------------------------------------------------------------------


def test_ticker_reuse_disjoint_intervals_map_each_owner() -> None:
    company, lookup, submissions = _reuse_world()
    keys = [
        _key("K-FIRST", "ZYX", date(2016, 1, 1), date(2018, 12, 31)),
        _key("K-SECOND", "ZYX", date(2020, 1, 1), date(2021, 12, 31)),
    ]
    rows = _by_key(
        _resolve(keys, company=company, lookup=lookup, submissions=submissions)
    )
    assert rows["K-FIRST"].cik == 111 and rows["K-FIRST"].status == I.STATUS_MAPPED
    assert rows["K-SECOND"].cik == 222 and rows["K-SECOND"].status == I.STATUS_MAPPED


def test_ticker_reuse_overlapping_intervals_are_ambiguous() -> None:
    company, lookup, submissions = _reuse_world()
    rows = _resolve(
        [_key("K-OVER", "ZYX", date(2017, 1, 1), date(2020, 12, 31))],
        company=company,
        lookup=lookup,
        submissions=submissions,
    )
    assert rows[0].status == I.STATUS_AMBIGUOUS


def test_interval_endpoints_are_inclusive() -> None:
    company, lookup, submissions = _reuse_world()
    lower = _resolve(
        [_key("K-LOWER", "ZYX", date(2017, 5, 1), date(2017, 5, 1))],
        company=company,
        lookup=lookup,
        submissions=submissions,
    )[0]
    upper = _resolve(
        [_key("K-UPPER", "ZYX", date(2018, 2, 1), date(2018, 2, 1))],
        company=company,
        lookup=lookup,
        submissions=submissions,
    )[0]
    assert lower.status == I.STATUS_MAPPED and lower.cik == 111
    assert upper.status == I.STATUS_MAPPED and upper.cik == 111


def test_row_order_invariance() -> None:
    company, lookup, submissions = _reuse_world()
    keys = [
        _key("K-FIRST", "ZYX", date(2016, 1, 1), date(2018, 12, 31)),
        _key("K-SECOND", "ZYX", date(2020, 1, 1), date(2021, 12, 31)),
    ]
    forward = _by_key(_resolve(keys, company=company, lookup=lookup, submissions=submissions))
    backward = _by_key(
        _resolve(list(reversed(keys)), company=company, lookup=lookup, submissions=submissions)
    )
    assert forward == backward


def test_filing_order_invariance() -> None:
    company, lookup, submissions = _reuse_world()
    rows_a = _resolve(
        [_key("K-FIRST", "ZYX", date(2016, 1, 1), date(2018, 12, 31))],
        company=company,
        lookup=lookup,
        submissions=submissions,
    )
    shuffled = {
        111: _view(
            111,
            entity="First Zyx Inc.",
            filings=(
                _filing("10-K", "2018-02-01"),
                _filing("10-Q", "2017-05-01"),
            ),
        ),
        222: submissions[222],
    }
    rows_b = _resolve(
        [_key("K-FIRST", "ZYX", date(2016, 1, 1), date(2018, 12, 31))],
        company=company,
        lookup=lookup,
        submissions=shuffled,
    )
    assert rows_a == rows_b


# ---------------------------------------------------------------------------
# Unmapped keys retained (plan §4)
# ---------------------------------------------------------------------------


def test_unmapped_keys_are_retained() -> None:
    company, lookup, submissions = _reuse_world()
    keys = [
        _key("K-MAPPED", "ZYX", date(2016, 1, 1), date(2018, 12, 31)),
        _key("K-ENDED", "EXWQ", date(2015, 1, 1), date(2017, 1, 1)),
        _key("K-UNMAPPED", "NOPE", date(2016, 1, 1), date(2017, 1, 1)),
    ]
    rows = _resolve(keys, company=company, lookup=lookup, submissions=submissions)
    assert len(rows) == len(keys)
    by_key = _by_key(rows)
    assert by_key["K-MAPPED"].status == I.STATUS_MAPPED
    assert by_key["K-UNMAPPED"].status == I.STATUS_NO_CANDIDATE
    assert by_key["K-UNMAPPED"].cik is None


# ---------------------------------------------------------------------------
# Output frame / parquet (plan §3)
# ---------------------------------------------------------------------------


def test_identity_frame_schema_and_determinism(tmp_path: Path) -> None:
    rows = [
        I.IdentityRow("B", 2, I.STATUS_MAPPED, I.ROUTE_TICKER, date(2016, 1, 1), None, 1),
        I.IdentityRow("A", None, I.STATUS_NO_CANDIDATE, None, None, None, 0),
    ]
    frame = I.identity_frame(rows)
    assert list(frame.columns) == list(I.IDENTITY_COLUMNS)
    assert list(frame["security_key"]) == ["A", "B"]
    assert str(frame["cik"].dtype) == "int64[pyarrow]"
    assert str(frame["interval_start"].dtype) == "date32[day][pyarrow]"

    path = I.write_identity_map(rows, tmp_path / "identity_map.parquet")
    assert path.exists()
    reordered = I.write_identity_map(list(reversed(rows)), tmp_path / "b.parquet")
    assert path.read_bytes() == reordered.read_bytes()
    read = I.read_identity_map(path)
    assert list(read["security_key"]) == ["A", "B"]
    assert read.loc[read["security_key"] == "A", "cik"].isna().all()


# ---------------------------------------------------------------------------
# IM-P seeded sample (plan §4, §8)
# ---------------------------------------------------------------------------


def _mapped_rows(active: int = 100, ended: int = 100):
    rows = []
    for i in range(active):
        rows.append(
            I.IdentityRow(
                f"A{i:04d}",
                100000 + i,
                I.STATUS_MAPPED,
                I.ROUTE_TICKER,
                date(2016, 1, 1),
                date(2020, 12, 31),
                1,
            )
        )
    for i in range(ended):
        rows.append(
            I.IdentityRow(
                f"E{i:04d}",
                200000 + i,
                I.STATUS_MAPPED,
                I.ROUTE_NAME,
                date(2010, 1, 1),
                date(2018, 12, 31),
                1,
            )
        )
    return rows


def _tickers(rows):
    return {row.security_key: (f"T{row.security_key}",) for row in rows}


def _evidence(rows, active=70, ended=50, contradicted=()):
    chosen = [row for row in rows if row.route == I.ROUTE_TICKER][:active]
    chosen += [row for row in rows if row.route == I.ROUTE_NAME][:ended]
    evidence = {}
    for row in chosen:
        if row.security_key in contradicted:
            evidence[row.security_key] = ("ZZZZ",)
        else:
            evidence[row.security_key] = (f"T{row.security_key}",)
    return evidence


def test_im_p_sample_is_seeded_100_active_100_ended() -> None:
    rows = _mapped_rows(active=130, ended=120)
    sample = I.select_im_p_sample(rows, seed=7)
    assert len(sample) == 200
    strata = {row.security_key: row.route for row in rows}
    assert sum(strata[key] == I.ROUTE_TICKER for key in sample) == 100
    assert sum(strata[key] == I.ROUTE_NAME for key in sample) == 100
    # Deterministic and invariant to the input row order.
    assert I.select_im_p_sample(rows, seed=7) == sample
    assert I.select_im_p_sample(list(reversed(rows)), seed=7) == sample


def test_im_p_sample_excludes_non_mapped_keys() -> None:
    rows = _mapped_rows(active=5, ended=5)
    rows.append(
        I.IdentityRow("X", None, I.STATUS_AMBIGUOUS, I.ROUTE_TICKER, None, None, 2)
    )
    rows.append(
        I.IdentityRow("F", 9, I.STATUS_FOREIGN_FILER, I.ROUTE_TICKER, None, None, 1)
    )
    sample = I.select_im_p_sample(rows, seed=1)
    assert "X" not in sample and "F" not in sample
    assert set(sample) == {row.security_key for row in rows if row.status == I.STATUS_MAPPED}


# ---------------------------------------------------------------------------
# IM-P symbol-evidence audit (plan §4)
# ---------------------------------------------------------------------------


def test_im_p_verdicts_confirm_contradict_and_no_evidence() -> None:
    rows = _mapped_rows(active=3, ended=0)
    tickers = _tickers(rows)
    sample = tuple(row.security_key for row in rows)
    result = I.audit_im_p(
        rows,
        sample,
        {
            "A0000": ("TA0000",),
            "A0001": ("WRONG",),
        },
        tickers,
        min_evidence_total=1,
        min_evidence_ended=0,
    )
    assert result.verdicts["A0000"] == I.AUDIT_CONFIRMED
    assert result.verdicts["A0001"] == I.AUDIT_CONTRADICTED
    assert result.verdicts["A0002"] == I.AUDIT_NO_SYMBOL_EVIDENCE
    assert result.n_confirmed == 1
    assert result.n_contradicted == 1
    assert result.n_no_symbol_evidence == 1
    assert result.n_evidence_total == 2


def test_im_p_supports_symbol_evidence_objects_and_history() -> None:
    rows = _mapped_rows(active=1, ended=0)
    tickers = {"A0000": ("OLD", "NEW")}
    result = I.audit_im_p(
        rows,
        ("A0000",),
        {"A0000": I.SymbolEvidence("A0000", ("old",))},
        tickers,
        min_evidence_total=1,
        min_evidence_ended=0,
    )
    assert result.verdicts["A0000"] == I.AUDIT_CONFIRMED


# ---------------------------------------------------------------------------
# IM-P gate and the §8 evidence-sufficiency rule
# ---------------------------------------------------------------------------


def test_im_p_gate_passes_with_clean_evidence() -> None:
    rows = _mapped_rows()
    sample = tuple(row.security_key for row in rows)
    result = I.audit_im_p(
        rows, sample, _evidence(rows, active=70, ended=50), _tickers(rows)
    )
    assert result.n_evidence_total == 120
    assert result.n_evidence_ended == 50
    assert result.evidence_sufficient is True
    assert result.contradicted_rate == 0.0
    assert result.gate_status == I.GATE_PASS


def test_im_p_gate_fails_when_rate_exceeds_one_percent() -> None:
    rows = _mapped_rows()
    sample = tuple(row.security_key for row in rows)
    result = I.audit_im_p(
        rows,
        sample,
        _evidence(rows, active=70, ended=50, contradicted=("A0000", "A0001", "A0002")),
        _tickers(rows),
    )
    assert result.contradicted_rate > I.IM_P_MAX_CONTRADICTED_RATE
    assert result.gate_status == I.GATE_FAIL


def test_im_p_one_sided_upper_bound_is_exact() -> None:
    rows = _mapped_rows()
    sample = tuple(row.security_key for row in rows)
    result = I.audit_im_p(
        rows,
        sample,
        _evidence(rows, active=70, ended=50, contradicted=("A0000",)),
        _tickers(rows),
    )
    assert result.n_evidence_total == 120
    assert result.contradicted_rate == pytest.approx(1 / 120)
    # The exact one-sided 95% Clopper-Pearson bound, not the normal
    # approximation (1/120 + 1.6449 * sqrt(p(1-p)/n) ~= 0.02198, which
    # would wrongly PASS).
    expected = float(beta.ppf(0.95, 2, 119))
    assert result.upper_bound == pytest.approx(expected)
    assert result.upper_bound > I.IM_P_MAX_UPPER_BOUND
    assert result.gate_status == I.GATE_FAIL


def test_im_p_gate_not_certified_on_insufficient_total_evidence() -> None:
    rows = _mapped_rows()
    sample = tuple(row.security_key for row in rows)
    result = I.audit_im_p(
        rows, sample, _evidence(rows, active=69, ended=50), _tickers(rows)
    )
    assert result.n_evidence_total == 119
    assert result.evidence_sufficient is False
    assert result.gate_status == I.GATE_NOT_CERTIFIED
    assert result.ended_stratum_certified is False


def test_im_p_gate_not_certified_on_insufficient_ended_evidence() -> None:
    rows = _mapped_rows()
    sample = tuple(row.security_key for row in rows)
    result = I.audit_im_p(
        rows, sample, _evidence(rows, active=71, ended=49), _tickers(rows)
    )
    assert result.n_evidence_total == 120
    assert result.n_evidence_ended == 49
    assert result.evidence_sufficient is False
    assert result.gate_status == I.GATE_NOT_CERTIFIED


def test_im_p_evidence_sufficiency_boundary_is_inclusive() -> None:
    rows = _mapped_rows()
    sample = tuple(row.security_key for row in rows)
    result = I.audit_im_p(
        rows, sample, _evidence(rows, active=70, ended=50), _tickers(rows)
    )
    assert result.n_evidence_total == 120
    assert result.n_evidence_ended == 50
    assert result.evidence_sufficient is True


def test_clopper_pearson_upper_known_values() -> None:
    assert I.clopper_pearson_upper(0, 120) == pytest.approx(
        1.0 - 0.05 ** (1.0 / 120.0), rel=1e-9
    )
    assert I.clopper_pearson_upper(1, 120) == pytest.approx(0.038916, abs=1e-5)
    assert I.clopper_pearson_upper(120, 120) == 1.0
    assert I.clopper_pearson_upper(0, 0) == 1.0
    # Exact bounds are strictly above the normal approximation here.
    normal = 1 / 120 + 1.6448536269514722 * math.sqrt((1 / 120) * (119 / 120) / 120)
    assert I.clopper_pearson_upper(1, 120) > normal


def test_run_im_p_stamps_audit_verdicts() -> None:
    rows = _mapped_rows(active=2, ended=0)
    tickers = _tickers(rows)
    evidence = {"A0000": tickers["A0000"]}
    updated, result = I.run_im_p(
        rows,
        evidence=evidence,
        tickers=tickers,
        seed=3,
        min_evidence_total=1,
        min_evidence_ended=0,
    )
    by_key = _by_key(updated)
    assert by_key["A0000"].audit_verdict == I.AUDIT_CONFIRMED
    assert by_key["A0001"].audit_verdict == I.AUDIT_NO_SYMBOL_EVIDENCE
    assert result.sample_size == 2


def test_identity_rows_default_to_not_sampled() -> None:
    row = I.IdentityRow("K", 1, I.STATUS_MAPPED, I.ROUTE_TICKER, None, None, 1)
    assert row.audit_verdict == I.AUDIT_NOT_SAMPLED


def test_coerce_key_accepts_mapping_and_timestamps() -> None:
    import pandas as pd

    key = I.coerce_key(
        {
            "security_key": "K",
            "ticker": "T",
            "listed_start": pd.Timestamp("2016-01-01"),
            "listed_end": "2017-12-31",
            "issuer_names": ["A"],
        }
    )
    assert key.security_key == "K"
    assert key.ticker == "T"
    assert key.interval_start == date(2016, 1, 1)
    assert key.interval_end == date(2017, 12, 31)
    assert key.issuer_names == ("A",)


# ---------------------------------------------------------------------------
# Mutation contract (plan §5)
# ---------------------------------------------------------------------------


def test_mutation_fixture_contract() -> None:
    mutations = json.loads(MUTATIONS.read_text(encoding="utf-8"))
    assert [mutation["id"] for mutation in mutations] == [
        "CONTROL",
        "E2-M1",
        "E2-M2",
        "E2-M3",
        "E2-M4",
        "E2-M5",
    ]
    source = (
        REPO_ROOT / "smart_beta" / "dataset_v1" / "edgar_identity.py"
    ).read_text(encoding="utf-8")
    this_module = sys.modules[__name__]
    for mutation in mutations:
        assert mutation["file"] == "smart_beta/dataset_v1/edgar_identity.py"
        assert mutation["description"]
        module_name, _, test_name = mutation["test"].partition("::")
        assert module_name == "tests/test_edgar_identity.py"
        assert hasattr(this_module, test_name), mutation["id"]
        if mutation["id"] == "CONTROL":
            assert mutation["edits"] == []
            continue
        assert mutation["edits"], mutation["id"]
        for edit in mutation["edits"]:
            assert edit["old"] and edit["new"] is not None
            assert source.count(edit["old"]) == 1, (mutation["id"], edit["old"])
            assert edit["old"] != edit["new"], mutation["id"]
