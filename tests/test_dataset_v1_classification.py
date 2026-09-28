"""Tests for D-SC — Discovery Dataset v1.1 security classification.

Every test is offline and synthetic.  Each integration test builds a tiny
D4 identity run, archived Wayback directory captures and a SEC full-index
under ``tmp_path``; the real ``data_store`` is never read and a socket guard
is installed for the whole module.  A credential sentinel is asserted
absent.  The normative Appendix A of
``worker_tasks/dataset_v1/task-dsc-classifier.md`` is exercised directly and
again through the full pipeline.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import socket
from datetime import date
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pytest

from smart_beta.dataset_v1 import classification
from smart_beta.dataset_v1.classification import (
    C,
    classify_string,
    class_state_at,
    issuer,
    name_evidence,
    norm,
    policy,
    policy_state_at,
    sec_overlay,
    state_at,
    suffix_reason,
    v3_points,
)

_STRING = pd.ArrowDtype(pa.string())
_DATE32 = pd.ArrowDtype(pa.date32())

A = date(2016, 1, 4)
CUT = date(2026, 8, 31)

NASDAQ_HEADER = [
    "Symbol",
    "Security Name",
    "Market Category",
    "Test Issue",
    "Financial Status",
    "Round Lot Size",
    "ETF",
    "NextShares",
]
OTHER_HEADER = [
    "ACT Symbol",
    "Security Name",
    "Exchange",
    "CQS Symbol",
    "ETF",
    "Round Lot Size",
    "Test Issue",
    "NASDAQ Symbol",
]
TRADED_HEADER = [
    "Symbol",
    "Security Name",
    "Listing Exchange",
    "Market Category",
    "ETF",
    "Round Lot Size",
    "Test Issue",
    "NASDAQ Symbol",
]


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch):
    """No D-SC test may open a socket."""

    def _refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("D-SC tests must never open a socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield


# ---------------------------------------------------------------------------
# Synthetic store construction
# ---------------------------------------------------------------------------


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical(value: object) -> str:
    return classification.canonical_json(value)


def _nasd_row(symbol: str, name: str, *, test: str = "N", etf: str = "N") -> str:
    return "|".join([symbol, name, "Q", test, "N", "100", etf, "N"])


def _other_row(
    symbol: str, name: str, *, exchange: str = "N", test: str = "N", etf: str = "N"
) -> str:
    return "|".join([symbol, name, exchange, "", etf, "100", test, ""])


def _traded_row(
    symbol: str, name: str, *, listing: str = "Q", test: str = "N", etf: str = "N"
) -> str:
    return "|".join([symbol, name, listing, "Q", etf, "100", test, ""])


class Store:
    """A synthetic D4-run + captures + SEC-index store under ``tmp_path``."""

    def __init__(self, root: Path, identity_id: str = "i-dsc-0000000000000000") -> None:
        self.root = root
        self.identity_id = identity_id
        self.keys: list[dict[str, object]] = []
        self.captures: list[tuple[str, str, list[str], list[str]]] = []
        self.masters: dict[str, list[tuple[str, str, str, str, str]]] = {}
        self.extra_identity: dict[str, object] = {}

    def key(
        self,
        ticker: str,
        exchange: str = "NASDAQ",
        *,
        security_key: str | None = None,
        start: date = A,
        end: date = C,
        state: str = "INTERVAL_ASSIGNED",
    ) -> str:
        key = security_key or f"TIINGO:{ticker}:{start.isoformat()}"
        self.keys.append(
            {
                "security_key": key,
                "ticker": ticker,
                "exchange": exchange,
                "listed_start": start,
                "listed_end": end,
                "state": state,
            }
        )
        return key

    def capture(
        self,
        kind: str,
        rows: list[str],
        *,
        timestamp: str = "20160630000000",
        header: list[str] | None = None,
    ) -> None:
        chosen = header
        if chosen is None:
            chosen = {
                "nasdaqlisted": NASDAQ_HEADER,
                "otherlisted": OTHER_HEADER,
                "nasdaqtraded": TRADED_HEADER,
            }[kind]
        self.captures.append((kind, timestamp, list(chosen), list(rows)))

    def nasdaq(self, rows: list[str], **kwargs: object) -> None:
        self.capture("nasdaqlisted", rows, **kwargs)  # type: ignore[arg-type]

    def other(self, rows: list[str], **kwargs: object) -> None:
        self.capture("otherlisted", rows, **kwargs)  # type: ignore[arg-type]

    def master(self, name: str, rows: list[tuple[str, str, str, str, str]]) -> None:
        self.masters[name] = list(rows)

    def _write_identity(self) -> None:
        identity_dir = self.root / "derived" / "identity" / self.identity_id
        identity_dir.mkdir(parents=True, exist_ok=True)
        frame = pd.DataFrame(
            {
                "security_key": pd.Series(
                    [str(k["security_key"]) for k in self.keys], dtype=_STRING
                ),
                "ticker": pd.Series([str(k["ticker"]) for k in self.keys], dtype=_STRING),
                "exchange": pd.Series(
                    [str(k["exchange"]) for k in self.keys], dtype=_STRING
                ),
                "listed_start": pd.Series(
                    [k["listed_start"] for k in self.keys], dtype=_DATE32
                ),
                "listed_end": pd.Series(
                    [k["listed_end"] for k in self.keys], dtype=_DATE32
                ),
                "state": pd.Series([str(k["state"]) for k in self.keys], dtype=_STRING),
            }
        )
        assignment_path = identity_dir / "identity_assignment.parquet"
        frame.to_parquet(
            assignment_path, engine="pyarrow", index=False, compression="zstd"
        )
        files_sha256 = {
            f"derived/identity/{self.identity_id}/identity_assignment.parquet": _sha(
                assignment_path
            )
        }
        content = {
            "schema": "discovery-dataset-v1-identity-v1",
            "identity_id": self.identity_id,
            "identity_hash": hashlib.sha256(
                _canonical(files_sha256).encode("utf-8")
            ).hexdigest(),
            "files_sha256": files_sha256,
        }
        content.update(self.extra_identity)
        (identity_dir / "identity.json").write_bytes(
            (_canonical(content) + "\n").encode("utf-8")
        )

    def _write_captures(self) -> Path:
        captures_dir = self.root / "dsc_evidence" / "wayback-20260927"
        captures_dir.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        for kind, timestamp, header, rows in self.captures:
            path = captures_dir / f"{kind}_{timestamp}.txt"
            text = "\n".join(["|".join(header), *rows, "File Creation Time: 2026063000:00" + "|" * (len(header) - 1)])
            path.write_text(text + "\n", encoding="latin-1")
            written.append(path)
        sums = captures_dir / "SHA256SUMS"
        sums.write_text(
            "".join(f"{_sha(path)}  {path.name}\n" for path in sorted(written)),
            encoding="utf-8",
        )
        return captures_dir

    def _write_masters(self) -> Path:
        sec_dir = self.root / "dsc_evidence" / "sec-regindex-20260927" / "raw"
        sec_dir.mkdir(parents=True, exist_ok=True)
        for name, rows in self.masters.items():
            body = ["CIK|Company Name|Form Type|Date Filed|Filename", "-" * 80]
            body.extend("|".join(row) for row in rows)
            with gzip.open(sec_dir / name, "wb") as handle:
                handle.write(("\n".join(body) + "\n").encode("latin-1"))
        return sec_dir

    def build(self) -> "Store":
        self._write_identity()
        self._write_captures()
        self._write_masters()
        return self

    def classify(self, **kwargs: object) -> dict:
        self.build()
        return classification.classify(self.root, self.identity_id, **kwargs)  # type: ignore[arg-type]

    def output_dir(self, content: dict) -> Path:
        return self.root / "derived" / "classification" / str(content["dsc_id"])

    def class_timeline(self, content: dict) -> dict:
        return classification.load_class_timeline(
            self.output_dir(content) / "class_timeline.parquet"
        )

    def policy_timeline(self, content: dict) -> dict:
        return classification.load_policy_timeline(
            self.output_dir(content) / "policy_timeline.parquet"
        )


def _classify_at(store: Store, key: str, cutoff: date, **kwargs: object) -> tuple[str, str]:
    content = store.classify(**kwargs)
    return class_state_at(store.class_timeline(content), key, cutoff)


def _policy_at(
    store: Store, key: str, cutoff: date, **kwargs: object
) -> tuple[str, frozenset[str]]:
    content = store.classify(**kwargs)
    return policy_state_at(store.policy_timeline(content), key, cutoff)


# ===========================================================================
# 1. Appendix A1 — string grammar
# ===========================================================================


V2_EXAMPLES = [
    ("Series A Common Stock Purchase Warrants", ("NON", "WARRANT")),
    ("Class C-1 ADS (ADW)", ("NON", "WARRANT")),
    (
        "Units, each consisting of one share of Class A common stock and one warrant",
        ("NON", "UNIT"),
    ),
    (
        "Rights, each entitling the holder to one-tenth of one ordinary share",
        ("NON", "RIGHT"),
    ),
    (
        "American Depositary Shares, each representing one Ordinary Share",
        ("COM", "ADR_ADS"),
    ),
    ("Petrobras American Depositary Shares", ("UNR", "BARE_ADS")),
    (
        "XYZ Limited American Depositary Shares, each representing one Class A Ordinary Share",
        ("COM", "ADR_ADS"),
    ),
    (
        "Depositary Shares, each representing 1/1,000th interest in a share of Preferred Stock",
        ("NON", "DEPOSITARY_PREFERRED"),
    ),
    (
        "KKR & Co. L.P. Common Units Representing Limited Partnership Interest",
        ("NON", "LP_OR_MLP_UNIT"),
    ),
    ("KKR & Co. Inc. Class A Common Stock", ("COM", "COMMON")),
    ("Preferred Apartment Communities, Inc. Common Stock", ("COM", "COMMON")),
    ("Common Stock When-Issued", ("NON", "WHEN_ISSUED")),
    ("AT&T Inc.", ("UNR", "ISSUER_ONLY")),
]


@pytest.mark.parametrize("name,expected", V2_EXAMPLES)
def test_v2_normative_examples(name: str, expected: tuple[str, str]) -> None:
    assert classify_string(name) == expected


def test_v1_and_v4_normative_examples() -> None:
    assert classify_string("Apple Inc. - Common Stock") == ("COM", "COMMON")
    assert classify_string("Rayonier Inc. REIT Common Stock") == ("COM", "REIT_COMMON")
    assert classify_string("Pennsylvania REIT Shares of Beneficial Interest") == (
        "UNR",
        "SBI_AMBIGUOUS",
    )
    assert classify_string("Pennsylvania REIT Common Shares of Beneficial Interest") == (
        "COM",
        "REIT_COMMON",
    )
    assert classify_string("XYZ Acquisition Corp - Class A Ordinary Shares") == (
        "COM",
        "COMMON",
    )
    assert classify_string("Test Stock") == ("NON", "TEST_ISSUE")
    # v4 removed the FUND / CLOSED_END_FUND / BDC security classes.
    assert classify_string("Some Fund") == ("UNR", "ISSUER_ONLY")
    assert classify_string("Foo - Closed End Fund") == ("UNR", "ISSUER_ONLY")
    assert classify_string("Foo - Business Development Company") == (
        "UNR",
        "ISSUER_ONLY",
    )


def test_whole_name_overrides_order() -> None:
    # O1 test wins over the common-stock grammar.
    assert classify_string("Test Stock") == ("NON", "TEST_ISSUE")
    assert classify_string("Foo ETF") == ("NON", "ETF")
    assert classify_string("Foo Tracking Stock") == ("NON", "TRACKING_STOCK")


# ===========================================================================
# 2. Appendix A2 — state_at
# ===========================================================================


def test_state_at_no_evidence() -> None:
    assert state_at([], date(2020, 1, 1)) == (
        "CLASS_UNRESOLVED",
        "NO_ADMISSIBLE_EVIDENCE",
    )


def test_state_at_static_test_symbol_and_suffix() -> None:
    assert state_at([], date(2020, 1, 1), static_test=True) == (
        "NON_COMMON",
        "TEST_ISSUE",
    )
    assert state_at([], date(2020, 1, 1), suffix="PREFERRED") == (
        "NON_COMMON",
        "PREFERRED",
    )


def test_state_at_mismatch_precedence() -> None:
    assert state_at([], date(2020, 1, 1), mism_dates=[date(2019, 1, 1)]) == (
        "CLASS_UNRESOLVED",
        "EXCHANGE_MISMATCH_REVIEW",
    )
    # An explicit point removes the mismatch state entirely.
    points = [(date(2018, 1, 1), "COM", "COMMON")]
    assert state_at(points, date(2020, 1, 1), mism_dates=[date(2019, 1, 1)]) == (
        "COMMON_STOCK",
        "COMMON",
    )


def test_state_at_unresolved_uses_latest() -> None:
    unres = [(date(2017, 1, 1), "GENERIC_SHARES"), (date(2018, 1, 1), "BARE_ADS")]
    assert state_at([], date(2020, 1, 1), unres=unres) == (
        "CLASS_UNRESOLVED",
        "BARE_ADS",
    )


def test_state_at_concurrent_and_alternating_conflict() -> None:
    concurrent = [
        (date(2018, 1, 1), "COM", "COMMON"),
        (date(2018, 1, 1), "NON", "WARRANT"),
    ]
    assert state_at(concurrent, date(2020, 1, 1)) == (
        "EVIDENCE_CONFLICT",
        "CONCURRENT",
    )
    alternating = [
        (date(2016, 1, 1), "COM", "COMMON"),
        (date(2017, 1, 1), "NON", "WARRANT"),
        (date(2018, 1, 1), "COM", "COMMON"),
    ]
    assert state_at(alternating, date(2020, 1, 1)) == (
        "EVIDENCE_CONFLICT",
        "ALTERNATING",
    )


def test_state_at_two_runs_transition_and_subtype() -> None:
    points = [
        (date(2016, 1, 1), "NON", "WARRANT"),
        (date(2018, 1, 1), "COM", "COMMON"),
        (date(2019, 1, 1), "COM", "ADR_ADS"),
    ]
    assert state_at(points, date(2017, 1, 1)) == ("NON_COMMON", "WARRANT")
    assert state_at(points, date(2020, 1, 1)) == ("COMMON_STOCK", "ADR_ADS")


def test_state_at_suffix_contradiction() -> None:
    points = [(date(2016, 1, 1), "COM", "COMMON")]
    assert state_at(points, date(2020, 1, 1), suffix="PREFERRED") == (
        "EVIDENCE_CONFLICT",
        "SUFFIX_CONTRADICTION",
    )
    non = [(date(2016, 1, 1), "NON", "WARRANT")]
    assert state_at(non, date(2020, 1, 1), suffix="PREFERRED") == (
        "NON_COMMON",
        "WARRANT",
    )


def test_state_at_only_uses_evidence_up_to_cutoff() -> None:
    points = [
        (date(2016, 1, 1), "COM", "COMMON"),
        (date(2020, 1, 1), "NON", "WARRANT"),
    ]
    assert state_at(points, date(2019, 12, 31)) == ("COMMON_STOCK", "COMMON")


# ===========================================================================
# 3. Appendix A3 — when-issued filter and SEC-form status machine
# ===========================================================================


def test_v3_points_flags_later_when_issued_parallel() -> None:
    points = [
        (date(2016, 1, 1), "COM", "COMMON"),
        (date(2018, 1, 1), "NON", "WHEN_ISSUED"),
        (date(2020, 1, 1), "COM", "COMMON"),
    ]
    kept, flag = v3_points(points)
    assert flag == [(date(2018, 1, 1), "NON", "WHEN_ISSUED")]
    assert (date(2018, 1, 1), "NON", "WHEN_ISSUED") not in kept


def test_v3_points_keeps_earlier_when_issued() -> None:
    points = [
        (date(2015, 1, 1), "NON", "WHEN_ISSUED"),
        (date(2016, 1, 1), "COM", "COMMON"),
    ]
    kept, flag = v3_points(points)
    assert flag == []
    assert kept == points


def test_sec_overlay_state_machine() -> None:
    assert sec_overlay([(date(2016, 1, 1), "N-54A")], date(2020, 1, 1)) == (
        "BDC",
        "N-54A",
    )
    assert sec_overlay(
        [(date(2016, 1, 1), "N-54A"), (date(2017, 1, 1), "N-54C")], date(2020, 1, 1)
    ) == ("ISSUER_STATUS_UNRESOLVED", "WITHDRAWN_NO_SUCCESSOR_STATUS")
    assert sec_overlay([(date(2016, 1, 1), "N-8A")], date(2020, 1, 1)) == (
        "REGISTERED_FUND",
        "N-8A",
    )
    # N-8F never terminates.
    assert sec_overlay(
        [(date(2016, 1, 1), "N-8A"), (date(2017, 1, 1), "N-8F")], date(2020, 1, 1)
    ) == ("REGISTERED_FUND", "N-8A")
    assert sec_overlay([], date(2020, 1, 1), ambiguous=True) == (
        "ISSUER_STATUS_UNRESOLVED",
        "AMBIGUOUS_MAPPING",
    )
    assert sec_overlay([], date(2020, 1, 1)) == ("NO_REGULATORY_EXCLUSION", None)


def test_sec_overlay_only_uses_past_events() -> None:
    assert sec_overlay([(date(2020, 1, 1), "N-54A")], date(2019, 1, 1)) == (
        "NO_REGULATORY_EXCLUSION",
        None,
    )


# ===========================================================================
# 4. Appendix A4 — name overlay and combination
# ===========================================================================


def test_name_evidence_rules() -> None:
    assert name_evidence("Acme Business Development Company", "N") == "NAME_BASED_BDC"
    assert name_evidence("Acme BDC", "N") == "NAME_BASED_BDC"
    assert name_evidence("Acme Fund", "N") == "NAME_BASED_REGISTERED_FUND"
    assert name_evidence("Acme Closed-End Fund", "N") == "NAME_BASED_REGISTERED_FUND"
    assert name_evidence("Acme Fund", "Y") is None
    # The BDC token is case-sensitive.
    assert name_evidence("Acme bdc", "N") is None


def test_policy_combination_or_semantics() -> None:
    status, sources = policy(
        date(2020, 1, 1),
        [(date(2016, 1, 1), "N-54A")],
        False,
        [(date(2017, 1, 1), "NAME_BASED_REGISTERED_FUND")],
    )
    assert status == "EXCLUDED"
    assert sources == frozenset({"SEC_FORM_BDC", "NAME_BASED_REGISTERED_FUND"})
    # A name exclusion overrides an ambiguous mapping.
    status, sources = policy(
        date(2020, 1, 1),
        [],
        True,
        [(date(2017, 1, 1), "NAME_BASED_BDC")],
    )
    assert status == "EXCLUDED"
    assert sources == frozenset({"NAME_BASED_BDC"})
    status, sources = policy(date(2020, 1, 1), [], False, [])
    assert status == "NO_REGULATORY_EXCLUSION"
    assert sources == frozenset()


def test_policy_unresolved_mapping() -> None:
    status, sources = policy(date(2020, 1, 1), [], True, [])
    assert status == "ISSUER_STATUS_UNRESOLVED"
    assert sources == frozenset({"AMBIGUOUS_MAPPING"})


# ===========================================================================
# 5. Appendix A5 — issuer extraction and normalization
# ===========================================================================


def test_norm_examples() -> None:
    assert norm("Apple Inc.") == "apple"
    assert norm("AT&T Inc.") == "at and t"
    assert norm("The Coca-Cola Company /md/") == "coca cola"
    assert norm("") == ""


def test_issuer_examples() -> None:
    assert issuer("Apple Inc. - Common Stock") == "Apple Inc."
    assert issuer("Apple Inc.") == "Apple Inc."
    assert issuer("AT&T Inc.") == "AT&T Inc."
    assert issuer("KKR & Co. L.P.") == "KKR & Co. L.P."


# ===========================================================================
# 6. Suffix evidence
# ===========================================================================


@pytest.mark.parametrize(
    "ticker,exchange,expected",
    [
        ("AACBR", "NASDAQ", "RIGHT"),
        ("AACBU", "NASDAQ", "UNIT"),
        ("AACIW", "NASDAQ", "WARRANT"),
        ("ABCDW", "NASDAQ", "WARRANT"),
        ("ABCDE", "NASDAQ", None),
        ("ABCDW", "NYSE", None),
        ("BWL-A", "NYSE MKT", None),
        ("BWL-A", "NYSE", None),
        ("XYZ-P", "NYSE", "PREFERRED"),
        ("XYZ-WS", "NYSE", "WARRANT"),
        ("XYZ-W", "NYSE", "WARRANT"),
        ("XYZ-U", "NYSE", "UNIT"),
        ("XYZ-R", "NYSE", "RIGHT"),
        ("XYZ-RT", "NYSE", "RIGHT"),
        ("XYZ-WI", "NYSE", "WHEN_ISSUED"),
    ],
)
def test_suffix_reason(ticker: str, exchange: str, expected: str | None) -> None:
    assert suffix_reason(ticker, exchange) == expected


# ===========================================================================
# 7. Adversarial string fixtures (integration)
# ===========================================================================


def test_adversarial_embedded_common_in_warrant(tmp_path: Path) -> None:
    store = Store(tmp_path)
    key = store.key("ABCD")
    store.nasdaq([_nasd_row("ABCD", "Foo Inc. - Common Stock Purchase Warrants")])
    state, reason = _classify_at(store, key, CUT)
    assert (state, reason) == ("NON_COMMON", "WARRANT")


def test_adversarial_unit_of_common_plus_warrant(tmp_path: Path) -> None:
    store = Store(tmp_path)
    key = store.key("ABCD")
    store.nasdaq(
        [
            _nasd_row(
                "ABCD",
                "Foo Inc. - Units, each consisting of one share of Common Stock and one Warrant",
            )
        ]
    )
    assert _classify_at(store, key, CUT) == ("NON_COMMON", "UNIT")


def test_adversarial_right_to_purchase_common(tmp_path: Path) -> None:
    store = Store(tmp_path)
    key = store.key("ABCD")
    store.nasdaq(
        [_nasd_row("ABCD", "Foo Inc. - Rights, each to purchase Common Stock")]
    )
    assert _classify_at(store, key, CUT) == ("NON_COMMON", "RIGHT")


def test_adversarial_preferred_ads(tmp_path: Path) -> None:
    store = Store(tmp_path)
    key = store.key("ABCD")
    store.nasdaq([_nasd_row("ABCD", "Foo Inc. - Preferred American Depositary Shares")])
    assert _classify_at(store, key, CUT) == ("NON_COMMON", "DEPOSITARY_PREFERRED")


def test_adversarial_bare_ads_stays_unresolved(tmp_path: Path) -> None:
    store = Store(tmp_path)
    key = store.key("ABCD")
    store.nasdaq([_nasd_row("ABCD", "Foo Inc. - American Depositary Shares")])
    assert _classify_at(store, key, CUT) == ("CLASS_UNRESOLVED", "BARE_ADS")


def test_adversarial_reit_common(tmp_path: Path) -> None:
    store = Store(tmp_path)
    key = store.key("ABCD")
    store.nasdaq([_nasd_row("ABCD", "Rayonier Inc. REIT Common Stock")])
    assert _classify_at(store, key, CUT) == ("COMMON_STOCK", "REIT_COMMON")


def test_adversarial_sbi_ambiguous(tmp_path: Path) -> None:
    store = Store(tmp_path)
    key = store.key("ABCD")
    store.nasdaq(
        [_nasd_row("ABCD", "Pennsylvania REIT Shares of Beneficial Interest")]
    )
    assert _classify_at(store, key, CUT) == ("CLASS_UNRESOLVED", "SBI_AMBIGUOUS")


def test_adversarial_suffix_contradiction(tmp_path: Path) -> None:
    store = Store(tmp_path)
    key = store.key("AACBR")
    store.nasdaq([_nasd_row("AACBR", "Foo Inc. - Common Stock")])
    assert _classify_at(store, key, CUT) == (
        "EVIDENCE_CONFLICT",
        "SUFFIX_CONTRADICTION",
    )


def test_adversarial_exchange_mismatch(tmp_path: Path) -> None:
    store = Store(tmp_path)
    key = store.key("ABCD", "NYSE")
    # Symbol- and date-valid but a NASDAQ-venue capture for an NYSE key.
    store.nasdaq([_nasd_row("ABCD", "Foo Inc. - Common Stock")])
    assert _classify_at(store, key, CUT) == (
        "CLASS_UNRESOLVED",
        "EXCHANGE_MISMATCH_REVIEW",
    )


def test_amex_key_admits_nyse_mkt_venue(tmp_path: Path) -> None:
    store = Store(tmp_path)
    key = store.key("ABCD", "AMEX")
    store.other([_other_row("ABCD", "Foo Inc. - Common Stock", exchange="A")])
    assert _classify_at(store, key, CUT) == ("COMMON_STOCK", "COMMON")


def test_adversarial_lp_to_corporation_transition(tmp_path: Path) -> None:
    store = Store(tmp_path)
    key = store.key("ABCD")
    store.nasdaq(
        [
            _nasd_row(
                "ABCD",
                "KKR & Co. L.P. Common Units Representing Limited Partnership Interest",
            )
        ],
        timestamp="20160630000000",
    )
    store.nasdaq(
        [_nasd_row("ABCD", "KKR & Co. Inc. Class A Common Stock")],
        timestamp="20180630000000",
    )
    assert _classify_at(store, key, date(2017, 1, 1)) == (
        "NON_COMMON",
        "LP_OR_MLP_UNIT",
    )
    assert _classify_at(store, key, date(2019, 1, 1)) == ("COMMON_STOCK", "COMMON")


def test_adversarial_common_to_depositary_preferred(tmp_path: Path) -> None:
    store = Store(tmp_path)
    key = store.key("ABCD")
    store.nasdaq(
        [_nasd_row("ABCD", "Foo Inc. - Common Stock")], timestamp="20160630000000"
    )
    store.nasdaq(
        [
            _nasd_row(
                "ABCD",
                "Foo Inc. - Depositary Shares, each representing a Preferred Share",
            )
        ],
        timestamp="20180630000000",
    )
    assert _classify_at(store, key, date(2017, 1, 1)) == ("COMMON_STOCK", "COMMON")
    assert _classify_at(store, key, date(2019, 1, 1)) == (
        "NON_COMMON",
        "DEPOSITARY_PREFERRED",
    )


def test_adversarial_later_when_issued_parallel(tmp_path: Path) -> None:
    """A CEG/SNDK-class parallel when-issued line never transitions."""
    store = Store(tmp_path)
    key = store.key("ABCD")
    store.nasdaq(
        [_nasd_row("ABCD", "Foo Inc. - Common Stock")], timestamp="20160630000000"
    )
    store.nasdaq(
        [_nasd_row("ABCD", "Foo Inc. - Common Stock When-Issued")],
        timestamp="20180630000000",
    )
    assert _classify_at(store, key, CUT) == ("COMMON_STOCK", "COMMON")
    content = store.classify()
    evidence = pd.read_parquet(store.output_dir(content) / "evidence.parquet")
    parallel = evidence[evidence["when_issued_parallel"]]
    assert len(parallel) == 1
    assert parallel.iloc[0]["string_reason"] == "WHEN_ISSUED"


def test_adversarial_bdc_with_common_stock_class(tmp_path: Path) -> None:
    """A GLAD-class BDC lists Common Stock; BDC is overlay only."""
    store = Store(tmp_path)
    key = store.key("GLAD")
    store.nasdaq([_nasd_row("GLAD", "Gladstone Capital Corporation - Common Stock")])
    store.master(
        "master_2016Q3.gz",
        [("1", "Gladstone Capital Corporation", "N-54A", "2016-08-01", "f1")],
    )
    content = store.classify()
    assert class_state_at(store.class_timeline(content), key, CUT) == (
        "COMMON_STOCK",
        "COMMON",
    )
    status, sources = policy_state_at(store.policy_timeline(content), key, CUT)
    assert status == "EXCLUDED"
    assert "SEC_FORM_BDC" in sources


def test_adversarial_registered_fund_with_common_stock(tmp_path: Path) -> None:
    store = Store(tmp_path)
    key = store.key("ABCD")
    store.nasdaq([_nasd_row("ABCD", "Some Fund Inc. - Common Stock")])
    store.master(
        "master_2016Q3.gz",
        [("2", "Some Fund Inc.", "N-8A", "2016-08-01", "f2")],
    )
    content = store.classify()
    assert class_state_at(store.class_timeline(content), key, CUT) == (
        "COMMON_STOCK",
        "COMMON",
    )
    status, sources = policy_state_at(store.policy_timeline(content), key, CUT)
    assert status == "EXCLUDED"
    assert "SEC_FORM_REGISTERED_FUND" in sources


def test_sec_form_n54a_to_n54c_with_and_without_n8a(tmp_path: Path) -> None:
    store = Store(tmp_path)
    first = store.key("AAAA")
    second = store.key("BBBB")
    store.nasdaq(
        [
            _nasd_row("AAAA", "Alpha Corporation - Common Stock"),
            _nasd_row("BBBB", "Beta Corporation - Common Stock"),
        ]
    )
    store.master(
        "master_2016Q3.gz",
        [
            ("1", "Alpha Corporation", "N-54A", "2016-08-01", "a1"),
            ("2", "Beta Corporation", "N-54A", "2016-08-01", "b1"),
            ("2", "Beta Corporation", "N-8A", "2016-08-02", "b2"),
        ],
    )
    store.master(
        "master_2018Q1.gz",
        [
            ("1", "Alpha Corporation", "N-54C", "2018-02-01", "a2"),
            ("2", "Beta Corporation", "N-54C/A", "2018-02-01", "b3"),
        ],
    )
    content = store.classify()
    timeline = store.policy_timeline(content)
    # N-54A -> N-54C with no successor and no N-8A: unresolved.
    assert policy_state_at(timeline, first, CUT)[0] == "ISSUER_STATUS_UNRESOLVED"
    # A parallel N-8A keeps the registered-fund exclusion active.
    assert policy_state_at(timeline, second, CUT)[0] == "EXCLUDED"


def test_sec_form_n8a_then_n8f_does_not_terminate(tmp_path: Path) -> None:
    store = Store(tmp_path)
    key = store.key("ABCD")
    store.nasdaq([_nasd_row("ABCD", "Foo Fund Inc. - Common Stock")])
    store.master(
        "master_2016Q3.gz",
        [("3", "Foo Fund Inc.", "N-8A", "2016-08-01", "f1")],
    )
    store.master(
        "master_2020Q1.gz",
        [("3", "Foo Fund Inc.", "N-8F", "2020-02-01", "f2")],
    )
    assert _policy_at(store, key, CUT)[0] == "EXCLUDED"


def test_ambiguous_issuer_mapping_is_unresolved(tmp_path: Path) -> None:
    store = Store(tmp_path)
    key = store.key("ABCD")
    store.nasdaq([_nasd_row("ABCD", "Acme Corporation - Common Stock")])
    store.master(
        "master_2016Q3.gz",
        [
            ("1", "Acme Corporation", "N-54A", "2016-08-01", "a"),
            ("2", "ACME CORPORATION", "N-8A", "2016-08-01", "b"),
        ],
    )
    content = store.classify()
    mapping = pd.read_parquet(store.output_dir(content) / "issuer_mapping.parquet")
    assert mapping.iloc[0]["mapping_state"] == "AMBIGUOUS"
    status, sources = policy_state_at(store.policy_timeline(content), key, CUT)
    assert status == "ISSUER_STATUS_UNRESOLVED"
    assert sources == frozenset({"AMBIGUOUS_MAPPING"})


def test_pre_edgar_absence_stays_unexcluded(tmp_path: Path) -> None:
    """A BDC-like name without wording or forms is a documented limitation."""
    store = Store(tmp_path)
    key = store.key("CSWC")
    store.nasdaq([_nasd_row("CSWC", "Capital Southwest Corporation - Common Stock")])
    content = store.classify()
    assert class_state_at(store.class_timeline(content), key, CUT)[0] == "COMMON_STOCK"
    assert (
        policy_state_at(store.policy_timeline(content), key, CUT)[0]
        == "NO_REGULATORY_EXCLUSION_OBSERVED"
    )


# ===========================================================================
# 8. Point-in-time invariants
# ===========================================================================


def test_pit_add_remove_future_evidence_invariant(tmp_path: Path) -> None:
    """Adding or removing evidence dated after f-1 leaves state at f fixed."""
    base = Store(tmp_path / "base")
    base_key = base.key("ABCD")
    base.nasdaq(
        [_nasd_row("ABCD", "Foo Inc. - Common Stock")], timestamp="20160630000000"
    )
    base.nasdaq(
        [_nasd_row("ABCD", "Foo Fund Inc. - Common Stock")],
        timestamp="20180630000000",
    )
    base_content = base.classify()

    extended = Store(tmp_path / "extended")
    extended_key = extended.key("ABCD")
    extended.nasdaq(
        [_nasd_row("ABCD", "Foo Inc. - Common Stock")], timestamp="20160630000000"
    )
    extended.nasdaq(
        [_nasd_row("ABCD", "Foo Fund Inc. - Common Stock")],
        timestamp="20180630000000",
    )
    extended.nasdaq(
        [_nasd_row("ABCD", "Foo Inc. - Warrants")], timestamp="20200630000000"
    )
    extended_content = extended.classify()

    base_class = base.class_timeline(base_content)
    extended_class = extended.class_timeline(extended_content)
    base_policy = base.policy_timeline(base_content)
    extended_policy = extended.policy_timeline(extended_content)
    # Cutoffs strictly before the extra warrant evidence (2020-06-30).
    for cutoff in (date(2016, 6, 30), date(2017, 1, 1), date(2019, 12, 31)):
        assert class_state_at(base_class, base_key, cutoff) == class_state_at(
            extended_class, extended_key, cutoff
        )
        assert policy_state_at(base_policy, base_key, cutoff) == policy_state_at(
            extended_policy, extended_key, cutoff
        )


def test_pit_transition_is_prospective_only(tmp_path: Path) -> None:
    store = Store(tmp_path)
    key = store.key("ABCD")
    store.nasdaq(
        [_nasd_row("ABCD", "Foo Inc. - Common Stock")], timestamp="20160630000000"
    )
    store.nasdaq(
        [_nasd_row("ABCD", "Foo Inc. - Warrants")], timestamp="20180630000000"
    )
    content = store.classify()
    timeline = store.class_timeline(content)
    # The public cutoff is f-1.  The warrant evidence dated 2018-06-30 is
    # knowable at f = 2018-07-01, i.e. at cutoff 2018-06-30.
    assert class_state_at(timeline, key, date(2018, 6, 29)) == (
        "COMMON_STOCK",
        "COMMON",
    )
    assert class_state_at(timeline, key, date(2018, 6, 30)) == (
        "NON_COMMON",
        "WARRANT",
    )


def test_pit_name_and_sec_never_change_class(tmp_path: Path) -> None:
    store = Store(tmp_path)
    key = store.key("ABCD")
    store.nasdaq([_nasd_row("ABCD", "Acme Fund Corporation - Common Stock")])
    store.master(
        "master_2016Q3.gz",
        [("1", "Acme Fund Corporation", "N-54A", "2016-08-01", "a")],
    )
    content = store.classify()
    timeline = store.class_timeline(content)
    assert class_state_at(timeline, key, CUT) == ("COMMON_STOCK", "COMMON")


def test_bare_ads_never_becomes_common_without_representation(tmp_path: Path) -> None:
    store = Store(tmp_path)
    key = store.key("ABCD")
    store.nasdaq([_nasd_row("ABCD", "Foo Inc. - American Depositary Shares")])
    content = store.classify()
    timeline = store.class_timeline(content)
    assert class_state_at(timeline, key, CUT) == ("CLASS_UNRESOLVED", "BARE_ADS")


def test_policy_absence_is_not_certification(tmp_path: Path) -> None:
    store = Store(tmp_path)
    key = store.key("ABCD")
    store.nasdaq([_nasd_row("ABCD", "Foo Inc. - Common Stock")])
    content = store.classify()
    status, sources = policy_state_at(store.policy_timeline(content), key, CUT)
    assert status == "NO_REGULATORY_EXCLUSION_OBSERVED"
    assert sources == frozenset()


# ===========================================================================
# 9. Outputs, determinism and binding
# ===========================================================================


def test_outputs_and_counts(tmp_path: Path) -> None:
    store = Store(tmp_path)
    first = store.key("AAAA")
    second = store.key("BBBB")
    third = store.key("CCCC")
    store.nasdaq(
        [
            _nasd_row("AAAA", "Alpha Inc. - Common Stock"),
            _nasd_row("BBBB", "Beta Inc. - Warrants"),
            _nasd_row("CCCC", "Gamma Inc. - American Depositary Shares"),
        ]
    )
    content = store.classify()
    out = store.output_dir(content)
    for name in (
        "dsc.json",
        "evidence.parquet",
        "regulatory_events.parquet",
        "issuer_mapping.parquet",
        "class_timeline.parquet",
        "policy_timeline.parquet",
        "unresolved_conflict.parquet",
    ):
        assert (out / name).is_file()
    assert content["dsc_id"].startswith("c-")
    assert len(content["dsc_id"]) == 18
    files_sha256 = content["files_sha256"]
    expected_hash = hashlib.sha256(
        _canonical(files_sha256).encode("utf-8")
    ).hexdigest()
    assert content["dsc_hash"] == expected_hash
    assert content["counts"]["n_interval_assigned_keys"] == 3
    assert content["counts"]["class_at_C"]["COMMON_STOCK"] == 1
    assert content["counts"]["class_at_C"]["NON_COMMON"] == 1
    assert content["counts"]["class_at_C"]["CLASS_UNRESOLVED"] == 1
    unresolved = pd.read_parquet(out / "unresolved_conflict.parquet")
    assert set(unresolved["security_key"]) == {third}


def test_determinism_two_runs_are_byte_identical(tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.key("AAAA")
    store.nasdaq([_nasd_row("AAAA", "Alpha Inc. - Common Stock")])
    first = store.classify()
    out = store.output_dir(first)
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    second = classification.classify(store.root, store.identity_id)
    after = {p.name: p.read_bytes() for p in out.iterdir()}
    assert first["dsc_id"] == second["dsc_id"]
    assert before == after


def test_binding_refusal_on_capture_hash(tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.key("AAAA")
    store.nasdaq([_nasd_row("AAAA", "Alpha Inc. - Common Stock")])
    store.build()
    captures_dir = store.root / "dsc_evidence" / "wayback-20260927"
    target = next(p for p in captures_dir.iterdir() if p.suffix == ".txt")
    target.write_text(target.read_text(encoding="latin-1") + "junk\n", encoding="latin-1")
    with pytest.raises(classification.ClassificationBindingError):
        classification.classify(store.root, store.identity_id)


def test_binding_refusal_on_identity_hash(tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.key("AAAA")
    store.nasdaq([_nasd_row("AAAA", "Alpha Inc. - Common Stock")])
    store.build()
    identity_json = (
        store.root / "derived" / "identity" / store.identity_id / "identity.json"
    )
    content = json.loads(identity_json.read_text())
    content["files_sha256"] = {
        "derived/identity/x/identity_assignment.parquet": "0" * 64
    }
    identity_json.write_bytes((_canonical(content) + "\n").encode("utf-8"))
    with pytest.raises(classification.ClassificationBindingError):
        classification.classify(store.root, store.identity_id)


def test_binding_refusal_on_wrong_amendment_hash(tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.key("AAAA")
    store.nasdaq([_nasd_row("AAAA", "Alpha Inc. - Common Stock")])
    store.build()
    wrong = tmp_path / "wrong_amendment.md"
    wrong.write_text("not the frozen amendment\n", encoding="utf-8")
    with pytest.raises(classification.ClassificationBindingError):
        classification.classify(
            store.root, store.identity_id, amendment_path=wrong
        )


def test_binding_refusal_on_wrong_vocabulary_hash(tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.key("AAAA")
    store.nasdaq([_nasd_row("AAAA", "Alpha Inc. - Common Stock")])
    store.build()
    wrong = tmp_path / "wrong_vocabulary.md"
    wrong.write_text("not the frozen vocabulary\n", encoding="utf-8")
    with pytest.raises(classification.ClassificationBindingError):
        classification.classify(
            store.root, store.identity_id, vocabulary_path=wrong
        )


def test_identity_id_mismatch_is_refused(tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.key("AAAA")
    store.nasdaq([_nasd_row("AAAA", "Alpha Inc. - Common Stock")])
    store.build()
    with pytest.raises(classification.ClassificationInputError):
        classification.classify(store.root, "i-missing")


def test_only_interval_assigned_keys_are_classified(tmp_path: Path) -> None:
    store = Store(tmp_path)
    assigned = store.key("AAAA")
    excluded = store.key("BBBB", security_key="TIINGO:BBBB:2016-01-04", state="HISTORY_UNREACHABLE")
    store.nasdaq([_nasd_row("AAAA", "Alpha Inc. - Common Stock")])
    content = store.classify()
    assert content["counts"]["n_interval_assigned_keys"] == 1
    mapping = pd.read_parquet(store.output_dir(content) / "issuer_mapping.parquet")
    assert list(mapping["security_key"]) == [assigned]
    assert excluded not in set(mapping["security_key"])


# ===========================================================================
# 10. Firewall, network and credential containment
# ===========================================================================


def test_reserve_firewall_no_forbidden_columns(tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.key("AAAA")
    store.nasdaq([_nasd_row("AAAA", "Alpha Inc. - Common Stock")])
    content = store.classify()
    out = store.output_dir(content)
    for path in out.glob("*.parquet"):
        import pyarrow.parquet as pq

        schema = pq.ParquetFile(path).schema_arrow
        for column in schema.names:
            assert column.lower() not in classification.FORBIDDEN_FIELDS


def test_no_network_socket(tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.key("AAAA")
    store.nasdaq([_nasd_row("AAAA", "Alpha Inc. - Common Stock")])
    store.classify()


def test_credential_sentinel_absent(tmp_path: Path, monkeypatch, capsys) -> None:
    sentinel = "dsc-sentinel-credential-value-xyz"
    for name in (
        "TIINGO_API_KEY",
        "TUSHARE_PROXY_TOKEN",
        "TUSHARE_BASIC_PROXY_TOKEN",
        "TUSHARE_API_TOKEN",
        "DEEPSEEK_API_KEY",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "OPENAI_API_KEY",
    ):
        monkeypatch.setenv(name, sentinel)
    store = Store(tmp_path)
    store.key("AAAA")
    store.nasdaq([_nasd_row("AAAA", "Alpha Inc. - Common Stock")])
    content = store.classify()
    captured = capsys.readouterr()
    assert sentinel not in captured.out
    assert sentinel not in captured.err
    for path in store.output_dir(content).iterdir():
        assert sentinel.encode("utf-8") not in path.read_bytes()


# ===========================================================================
# 11. SEC parsing and evidence details
# ===========================================================================


def test_regulatory_events_parse_amendments_and_source(tmp_path: Path) -> None:
    store = Store(tmp_path)
    key = store.key("AAAA")
    store.nasdaq([_nasd_row("AAAA", "Alpha Inc. - Common Stock")])
    store.master(
        "master_2016Q3.gz",
        [
            ("1", "Alpha Inc.", "N-54A/A", "2016-08-01", "a"),
            ("1", "Alpha Inc.", "10-K", "2016-08-02", "b"),
        ],
    )
    content = store.classify()
    events = pd.read_parquet(store.output_dir(content) / "regulatory_events.parquet")
    assert list(events["base_form"]) == ["N-54A"]
    assert events.iloc[0]["source_index"] == "master_2016Q3.gz"
    assert events.iloc[0]["form"] == "N-54A/A"
    assert policy_state_at(store.policy_timeline(content), key, CUT)[0] == "EXCLUDED"


def test_evidence_row_fields(tmp_path: Path) -> None:
    store = Store(tmp_path)
    key = store.key("AAAA")
    store.nasdaq([_nasd_row("AAAA", "Alpha Inc. - Common Stock", test="N", etf="N")])
    content = store.classify()
    evidence = pd.read_parquet(store.output_dir(content) / "evidence.parquet")
    assert len(evidence) == 1
    row = evidence.iloc[0]
    assert row["security_key"] == key
    assert row["source_file"].startswith("nasdaqlisted_")
    assert row["source_row"] == 0
    assert row["venue"] == "NASDAQ"
    assert bool(row["venue_admissible"]) is True
    assert row["string_state"] == "COM"
    assert row["string_reason"] == "COMMON"
    assert bool(row["when_issued_parallel"]) is False


def test_test_issue_and_etf_flags_override(tmp_path: Path) -> None:
    store = Store(tmp_path)
    test_key = store.key("AAAA", security_key="TIINGO:AAAA:2016-01-04")
    etf_key = store.key("BBBB", security_key="TIINGO:BBBB:2016-01-04")
    store.nasdaq(
        [
            _nasd_row("AAAA", "Alpha Inc. - Common Stock", test="Y"),
            _nasd_row("BBBB", "Beta Inc. - Common Stock", etf="Y"),
        ]
    )
    content = store.classify()
    timeline = store.class_timeline(content)
    assert class_state_at(timeline, test_key, CUT) == ("NON_COMMON", "TEST_ISSUE")
    assert class_state_at(timeline, etf_key, CUT) == ("NON_COMMON", "ETF")


def test_test_symbol_list_is_authoritative(tmp_path: Path) -> None:
    store = Store(tmp_path)
    key = store.key("NTEST", security_key="TIINGO:NTEST:2016-01-04")
    store.nasdaq([_nasd_row("NTEST", "Test Common Stock")])
    assert _classify_at(store, key, CUT) == ("NON_COMMON", "TEST_ISSUE")


def test_nasdaqtraded_symbol_dot_to_dash(tmp_path: Path) -> None:
    store = Store(tmp_path)
    key = store.key("ABC-D", "NYSE")
    store.capture(
        "nasdaqtraded", [_traded_row("ABC.D", "Foo Inc. - Common Stock", listing="N")]
    )
    assert _classify_at(store, key, CUT) == ("COMMON_STOCK", "COMMON")


# ===========================================================================
# 12. Mutation tests — these encode behaviours a mutation would break
# ===========================================================================


def test_mutation_instrument_precedence(tmp_path: Path) -> None:
    """Dropping instrument precedence would classify this as COMMON."""
    store = Store(tmp_path)
    key = store.key("ABCD")
    store.nasdaq([_nasd_row("ABCD", "Foo Inc. - Common Stock Purchase Warrants")])
    assert _classify_at(store, key, CUT)[0] == "NON_COMMON"


def test_mutation_accepting_bare_ads(tmp_path: Path) -> None:
    store = Store(tmp_path)
    key = store.key("ABCD")
    store.nasdaq([_nasd_row("ABCD", "Foo Inc. - American Depositary Shares")])
    assert _classify_at(store, key, CUT) == ("CLASS_UNRESOLVED", "BARE_ADS")


def test_mutation_wi_parallel_line_transition(tmp_path: Path) -> None:
    store = Store(tmp_path)
    key = store.key("ABCD")
    store.nasdaq(
        [_nasd_row("ABCD", "Foo Inc. - Common Stock")], timestamp="20160630000000"
    )
    store.nasdaq(
        [_nasd_row("ABCD", "Foo Inc. - Common Stock When-Issued")],
        timestamp="20180630000000",
    )
    assert _classify_at(store, key, CUT)[0] == "COMMON_STOCK"


def test_mutation_backfilling_a_transition(tmp_path: Path) -> None:
    store = Store(tmp_path)
    key = store.key("ABCD")
    store.nasdaq(
        [_nasd_row("ABCD", "Foo Inc. - Warrants")], timestamp="20160630000000"
    )
    store.nasdaq(
        [_nasd_row("ABCD", "Foo Inc. - Common Stock")], timestamp="20180630000000"
    )
    content = store.classify()
    timeline = store.class_timeline(content)
    # Before the common observation's cutoff the state is still the old NON
    # state; the transition is never backfilled.
    assert class_state_at(timeline, key, date(2018, 6, 29))[0] == "NON_COMMON"
    assert class_state_at(timeline, key, date(2018, 6, 30))[0] == "COMMON_STOCK"


def test_mutation_n8f_terminating_registered_fund(tmp_path: Path) -> None:
    store = Store(tmp_path)
    key = store.key("ABCD")
    store.nasdaq([_nasd_row("ABCD", "Foo Fund Inc. - Common Stock")])
    store.master(
        "master_2016Q3.gz",
        [("3", "Foo Fund Inc.", "N-8A", "2016-08-01", "f1")],
    )
    store.master(
        "master_2020Q1.gz",
        [("3", "Foo Fund Inc.", "N-8F", "2020-02-01", "f2")],
    )
    assert _policy_at(store, key, CUT)[0] == "EXCLUDED"


def test_mutation_n54c_certifying_unrestricted(tmp_path: Path) -> None:
    store = Store(tmp_path)
    key = store.key("ABCD")
    store.nasdaq([_nasd_row("ABCD", "Foo Corporation - Common Stock")])
    store.master(
        "master_2016Q3.gz",
        [("4", "Foo Corporation", "N-54A", "2016-08-01", "a")],
    )
    store.master(
        "master_2018Q1.gz",
        [("4", "Foo Corporation", "N-54C", "2018-02-01", "b")],
    )
    status, _ = _policy_at(store, key, CUT)
    assert status == "ISSUER_STATUS_UNRESOLVED"
    assert status != "NO_REGULATORY_EXCLUSION_OBSERVED"


def test_mutation_fuzzy_prefix_matching(tmp_path: Path) -> None:
    """Exact normalization only: 'Alpha' must not match 'Alpha Beta'."""
    store = Store(tmp_path)
    key = store.key("ABCD")
    store.nasdaq([_nasd_row("ABCD", "Alpha Corporation - Common Stock")])
    store.master(
        "master_2016Q3.gz",
        [("9", "Alpha Beta Corporation", "N-54A", "2016-08-01", "a")],
    )
    assert _policy_at(store, key, CUT)[0] == "NO_REGULATORY_EXCLUSION_OBSERVED"


def test_mutation_name_overlay_mutating_class(tmp_path: Path) -> None:
    store = Store(tmp_path)
    key = store.key("ABCD")
    store.nasdaq([_nasd_row("ABCD", "Some Fund Inc. - Common Stock")])
    content = store.classify()
    assert class_state_at(store.class_timeline(content), key, CUT) == (
        "COMMON_STOCK",
        "COMMON",
    )


def test_mutation_evidence_dated_on_f_counts(tmp_path: Path) -> None:
    store = Store(tmp_path)
    key = store.key("ABCD")
    store.nasdaq(
        [_nasd_row("ABCD", "Foo Inc. - Common Stock")], timestamp="20180630000000"
    )
    content = store.classify()
    timeline = store.class_timeline(content)
    # The public cutoff is f-1: evidence dated 2018-06-30 does not count at
    # f = 2018-06-30 (cutoff 2018-06-29) but does at f = 2018-07-01.
    assert class_state_at(timeline, key, date(2018, 6, 29))[0] == "CLASS_UNRESOLVED"
    assert class_state_at(timeline, key, date(2018, 6, 30))[0] == "COMMON_STOCK"


def test_mutation_suffix_never_promotes_to_common(tmp_path: Path) -> None:
    store = Store(tmp_path)
    key = store.key("AACBU")
    store.nasdaq([_nasd_row("AACBU", "Foo Inc. - Common Stock")])
    # The suffix is exclusion evidence, so a would-be COMMON is a conflict.
    assert _classify_at(store, key, CUT) == (
        "EVIDENCE_CONFLICT",
        "SUFFIX_CONTRADICTION",
    )


# ===========================================================================
# 13. CLI
# ===========================================================================


def test_cli_classify(tmp_path: Path, capsys) -> None:
    store = Store(tmp_path)
    store.key("AAAA")
    store.nasdaq([_nasd_row("AAAA", "Alpha Inc. - Common Stock")])
    store.build()
    rc = classification.main(
        ["--root", str(store.root), "--identity-id", store.identity_id]
    )
    assert rc == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out[0].startswith("c-")
    assert len(out[1]) == 64
