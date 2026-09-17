"""Tests for :mod:`smart_beta.research_inputs.identifier_continuity`.

These tests exercise the frozen policy table with plain, hand-built
identifier objects.  They deliberately never import
:mod:`smart_beta.vendors.tiingo.identifiers`, proving the guard works on
any source's duck-typed identifier-resolution result.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, replace
from pathlib import Path
from typing import get_type_hints

import pytest

from smart_beta.research_inputs import identifier_continuity
from smart_beta.research_inputs.identifier_continuity import (
    IdentifierContinuityError,
    IdentifierContinuityEvidence,
    ResolvedIdentifierLike,
    check_identifier_continuity,
)

_ALL_POLICIES = ["fail", "warn", "allow"]


@dataclass(frozen=True)
class _PlainIdentifier:
    """A hand-built identifier result -- *not* the Tiingo dataclass.

    Satisfies :class:`ResolvedIdentifierLike` structurally; the guard must
    work with it without any vendor import.
    """

    stock_id: str
    is_permanent: bool
    source_field: str


def _permanent(stock_id: str) -> _PlainIdentifier:
    return _PlainIdentifier(
        stock_id=stock_id,
        is_permanent=True,
        source_field="permaTicker",
    )


def _mutable(stock_id: str) -> _PlainIdentifier:
    return _PlainIdentifier(
        stock_id=stock_id,
        is_permanent=False,
        source_field="ticker",
    )


def _mixed() -> list[_PlainIdentifier]:
    """Some permanent, some not, with a stable expected ordering."""
    return [_permanent("US000000000038"), _mutable("TWTR"), _mutable("FB")]


# ---------------------------------------------------------------------------
# 1. All-permanent input, every policy value
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("policy", _ALL_POLICIES)
def test_all_permanent_certified_for_every_policy(policy: str) -> None:
    evidence = check_identifier_continuity(
        [_permanent("US000000000038"), _permanent("US000000000041")],
        policy=policy,  # type: ignore[arg-type]
    )

    assert isinstance(evidence, IdentifierContinuityEvidence)
    assert evidence.certified is True
    assert evidence.non_permanent_stock_ids == ()
    assert evidence.proceeded_under_override is False
    assert evidence.policy_applied == policy


# ---------------------------------------------------------------------------
# 2. Mixed input under policy="fail" (default and explicit)
# ---------------------------------------------------------------------------


def test_mixed_fail_default_raises() -> None:
    with pytest.raises(IdentifierContinuityError) as excinfo:
        check_identifier_continuity(_mixed())

    evidence = excinfo.value.evidence
    assert evidence.non_permanent_stock_ids == ("TWTR", "FB")
    assert evidence.certified is False
    assert evidence.policy_applied == "fail"


def test_mixed_fail_explicit_raises() -> None:
    with pytest.raises(IdentifierContinuityError) as excinfo:
        check_identifier_continuity(_mixed(), policy="fail")

    evidence = excinfo.value.evidence
    assert evidence.non_permanent_stock_ids == ("TWTR", "FB")
    assert evidence.certified is False
    assert evidence.policy_applied == "fail"


# ---------------------------------------------------------------------------
# 3. Mixed input under policy="warn"
# ---------------------------------------------------------------------------


def test_mixed_warn_does_not_raise_and_reports_non_permanent_ids() -> None:
    evidence = check_identifier_continuity(_mixed(), policy="warn")

    assert evidence.certified is False
    assert evidence.non_permanent_stock_ids == ("TWTR", "FB")
    assert evidence.proceeded_under_override is True
    assert evidence.policy_applied == "warn"


# ---------------------------------------------------------------------------
# 4. Mixed input under policy="allow" -- content-identical to "warn"
# ---------------------------------------------------------------------------


def test_mixed_allow_evidence_is_content_identical_to_warn() -> None:
    warn_evidence = check_identifier_continuity(_mixed(), policy="warn")
    allow_evidence = check_identifier_continuity(_mixed(), policy="allow")

    # Same continuity content under both override modes: nothing is stripped
    # or weakened under "allow".
    assert allow_evidence.non_permanent_stock_ids == warn_evidence.non_permanent_stock_ids
    assert allow_evidence.non_permanent_stock_ids == ("TWTR", "FB")
    assert allow_evidence.certified is False
    assert warn_evidence.certified is False
    assert allow_evidence.proceeded_under_override is True
    assert warn_evidence.proceeded_under_override is True

    # The single field that may differ is which policy produced the evidence.
    # A future edit that strips information under "allow" breaks the asserts
    # above (and this equality) immediately.
    assert replace(warn_evidence, policy_applied="allow") == allow_evidence


# ---------------------------------------------------------------------------
# 5. The guard computes nothing about listing history
# ---------------------------------------------------------------------------


def test_protocol_has_exactly_three_fields() -> None:
    assert set(get_type_hints(ResolvedIdentifierLike)) == {
        "stock_id",
        "is_permanent",
        "source_field",
    }


def test_module_never_mentions_listing_history_fields() -> None:
    source = Path(identifier_continuity.__file__).read_text(encoding="utf-8")

    assert "list_date" not in source
    assert "startDate" not in source


def test_guard_reads_only_stock_id_and_is_permanent() -> None:
    """``source_field`` may explode; the guard must never touch it."""

    class _ExplodingSourceField:
        stock_id = "AAPL"
        is_permanent = False

        @property
        def source_field(self) -> str:
            raise AssertionError("guard must not read source_field")

    evidence = check_identifier_continuity([_ExplodingSourceField()], policy="warn")

    assert evidence.non_permanent_stock_ids == ("AAPL",)


# ---------------------------------------------------------------------------
# 6. Duck-typed input needs no vendor import
# ---------------------------------------------------------------------------


def test_module_does_not_import_any_vendor_module() -> None:
    source = Path(identifier_continuity.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")

    assert not any(name.startswith("smart_beta.vendors") for name in imported)


def test_plain_hand_built_object_works_identically() -> None:
    plain: list[_PlainIdentifier] = [
        _permanent("US000000000038"),
        _mutable("TWTR"),
    ]

    evidence = check_identifier_continuity(plain, policy="warn")

    assert evidence == IdentifierContinuityEvidence(
        certified=False,
        non_permanent_stock_ids=("TWTR",),
        policy_applied="warn",
        proceeded_under_override=True,
    )
    # The input is demonstrably not the vendor dataclass.
    assert type(plain[0]).__module__ == __name__
