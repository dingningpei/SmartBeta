"""Tests for the vendor-free, fail-closed data-requirement contract (P6-B).

Coverage follows the frozen P6-B completion criteria:

* **requirement validation** -- the model is frozen/hashable and rejects
  malformed or internally incoherent declarations at construction time;
* **fail-closed on unsatisfiable requirements** -- a capability either
  demonstrates every declared semantic property or the requirement is
  reported as a typed, named, recordable failure (never a silent empty
  result or a substituted value);
* **vendor-name rejection** -- no vendor name can appear in a requirement
  or capability, while ordinary words that merely embed a vendor token
  (``window``/``wind``) are not falsely rejected.

A structural test additionally pins the Phase 6 section-8 trust boundary:
:class:`DataRequirement` exposes no field that could choose a knowledge
date, vintage, provider, universe, formation date, or future return, and
the module imports no vendor code.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

from smart_beta.spec.requirements import (
    DataCapability,
    DataRequirement,
    Frequency,
    ObservationPeriod,
    DataRequirementUnsatisfiableError,
    RequirementValidationError,
    RevisionPolicy,
    SatisfactionResult,
    Unit,
    UnsatisfactionReason,
    VendorNameError,
    assert_vendor_free,
    check_satisfiable,
    find_vendor_names,
    require_satisfiable,
)


MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "smart_beta" / "spec" / "requirements.py"
)


def _requirement(**overrides) -> DataRequirement:
    base = dict(
        semantic_id="return",
        frequency=Frequency.DAILY,
        observation_period=ObservationPeriod.PERIOD,
        units=Unit.FRACTION,
        lookback=5,
        revision_policy=RevisionPolicy.POINT_IN_TIME,
        require_knowledge_date=True,
        require_positive_vintage_identity=False,
    )
    base.update(overrides)
    return DataRequirement(**base)


def _capability(**overrides) -> DataCapability:
    base = dict(
        semantic_id="return",
        frequency=Frequency.DAILY,
        observation_period=ObservationPeriod.PERIOD,
        units=Unit.FRACTION,
        history=10,
        has_knowledge_date=True,
        has_positive_vintage_identity=True,
        revision_policies=frozenset(
            {RevisionPolicy.POINT_IN_TIME, RevisionPolicy.AS_FIRST_REPORTED}
        ),
    )
    base.update(overrides)
    return DataCapability(**base)


# ---------------------------------------------------------------------------
# Requirement validation
# ---------------------------------------------------------------------------


def test_valid_requirement_round_trips_fields() -> None:
    req = _requirement()
    assert req.semantic_id == "return"
    assert req.frequency is Frequency.DAILY
    assert req.observation_period is ObservationPeriod.PERIOD
    assert req.units is Unit.FRACTION
    assert req.lookback == 5
    assert req.revision_policy is RevisionPolicy.POINT_IN_TIME
    assert req.require_knowledge_date is True
    assert req.require_positive_vintage_identity is False


def test_requirement_is_hashable_and_frozen() -> None:
    req = _requirement()
    assert isinstance(hash(req), int)
    # Equal requirements hash equally and can be used as dict/set members.
    assert {req, _requirement()} == {req}
    with pytest.raises(dataclasses.FrozenInstanceError):
        req.lookback = 99  # type: ignore[misc]


def test_requirement_defaults_are_fail_closed() -> None:
    req = DataRequirement(
        semantic_id="market_cap",
        frequency="daily",
        observation_period="instant",
    )
    assert req.units is None
    assert req.lookback == 0
    assert req.revision_policy is RevisionPolicy.POINT_IN_TIME
    assert req.require_knowledge_date is True
    assert req.require_positive_vintage_identity is False


@pytest.mark.parametrize(
    "semantic_id",
    ["", "   ", "return value", " leading", "trailing ", "1return", "return;--"],
)
def test_invalid_semantic_id_rejected(semantic_id: str) -> None:
    with pytest.raises(RequirementValidationError):
        _requirement(semantic_id=semantic_id)


@pytest.mark.parametrize("semantic_id", [None, 123, ["return"]])
def test_non_string_semantic_id_rejected(semantic_id) -> None:
    with pytest.raises(RequirementValidationError):
        _requirement(semantic_id=semantic_id)


def test_unknown_enum_values_rejected() -> None:
    with pytest.raises(RequirementValidationError):
        _requirement(frequency="hourly")
    with pytest.raises(RequirementValidationError):
        _requirement(observation_period="flow")
    with pytest.raises(RequirementValidationError):
        _requirement(units="dollars")
    with pytest.raises(RequirementValidationError):
        _requirement(revision_policy="use_latest")


@pytest.mark.parametrize("lookback", [-1, -100, 1.5, True, False, "5"])
def test_invalid_lookback_rejected(lookback) -> None:
    with pytest.raises(RequirementValidationError):
        _requirement(lookback=lookback)


@pytest.mark.parametrize(
    "field_name",
    ["require_knowledge_date", "require_positive_vintage_identity"],
)
@pytest.mark.parametrize("value", [1, 0, "true", None])
def test_non_bool_flags_rejected(field_name: str, value) -> None:
    with pytest.raises(RequirementValidationError):
        _requirement(**{field_name: value})


def test_positive_vintage_identity_requires_knowledge_date() -> None:
    # Vintage semantics imply a knowledge time; declaring otherwise is
    # incoherent and must fail closed at construction.
    with pytest.raises(RequirementValidationError):
        _requirement(
            require_knowledge_date=False,
            require_positive_vintage_identity=True,
        )


def test_as_first_reported_requires_positive_vintage_identity() -> None:
    with pytest.raises(RequirementValidationError):
        _requirement(
            revision_policy=RevisionPolicy.AS_FIRST_REPORTED,
            require_positive_vintage_identity=False,
        )
    # The coherent combination is accepted.
    req = _requirement(
        revision_policy=RevisionPolicy.AS_FIRST_REPORTED,
        require_positive_vintage_identity=True,
    )
    assert req.revision_policy is RevisionPolicy.AS_FIRST_REPORTED


def test_to_dict_is_json_safe_and_deterministic() -> None:
    req = _requirement()
    first = req.to_dict()
    second = _requirement().to_dict()
    assert first == second
    assert first == {
        "semantic_id": "return",
        "frequency": "daily",
        "observation_period": "period",
        "units": "fraction",
        "lookback": 5,
        "revision_policy": "point_in_time",
        "require_knowledge_date": True,
        "require_positive_vintage_identity": False,
    }


def test_capability_defaults_are_fail_closed() -> None:
    cap = DataCapability(
        semantic_id="return",
        frequency=Frequency.DAILY,
        observation_period=ObservationPeriod.PERIOD,
    )
    assert cap.units is None
    assert cap.history is None
    assert cap.has_knowledge_date is False
    assert cap.has_positive_vintage_identity is False
    assert cap.revision_policies == frozenset()


def test_capability_invalid_history_rejected() -> None:
    with pytest.raises(RequirementValidationError):
        _capability(history=-1)
    with pytest.raises(RequirementValidationError):
        _capability(history=True)


# ---------------------------------------------------------------------------
# Vendor-name rejection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "semantic_id",
    [
        "tushare",
        "tiingo",
        "fred",
        "datahubco",
        "wind",
        "Tushare_return",
        "close_tiingo",
        "FRED_rate",
        "datahubco_snapshot",
    ],
)
def test_vendor_named_requirement_rejected(semantic_id: str) -> None:
    with pytest.raises(VendorNameError):
        _requirement(semantic_id=semantic_id)


def test_vendor_named_capability_rejected() -> None:
    with pytest.raises(VendorNameError):
        _capability(semantic_id="tiingo_close")


def test_vendor_error_is_a_validation_error() -> None:
    assert issubclass(VendorNameError, RequirementValidationError)


def test_camel_case_vendor_identifier_rejected() -> None:
    # "TushareDaily" lowercases to an unbroken token; the scan must still
    # detect it via camelCase splitting.
    with pytest.raises(VendorNameError):
        _requirement(semantic_id="TushareDaily")


@pytest.mark.parametrize(
    "semantic_id",
    ["window_return", "winsorized_return", "frequency", "alfred", "return"],
)
def test_ordinary_words_embedding_vendor_tokens_not_rejected(
    semantic_id: str,
) -> None:
    # Boundary-aware matching must not flag "wind" inside "window" or "fred"
    # inside "alfred".
    req = _requirement(semantic_id=semantic_id)
    assert req.semantic_id == semantic_id


def test_find_vendor_names_is_sorted_and_deduplicated() -> None:
    assert find_vendor_names("tiingo_and_tushare") == ("tiingo", "tushare")
    assert find_vendor_names("DataHubCo") == ("datahubco",)
    assert find_vendor_names("plain_return") == ()


def test_assert_vendor_free_rejects_non_string() -> None:
    with pytest.raises(RequirementValidationError):
        assert_vendor_free(None)  # type: ignore[arg-type]


def test_assert_vendor_free_returns_input_unchanged() -> None:
    assert assert_vendor_free("market_cap") == "market_cap"


# ---------------------------------------------------------------------------
# Fail-closed satisfaction
# ---------------------------------------------------------------------------


def test_matching_capability_satisfies_requirement() -> None:
    result = check_satisfiable(_requirement(), _capability())
    assert isinstance(result, SatisfactionResult)
    assert result.satisfied is True
    assert result.reasons == ()
    assert result.reason is None
    assert require_satisfiable(_requirement(), _capability()) is not None


@pytest.mark.parametrize(
    "capability_overrides, expected_reason",
    [
        (
            {"semantic_id": "market_cap"},
            UnsatisfactionReason.SEMANTIC_IDENTITY_MISMATCH,
        ),
        ({"frequency": Frequency.MONTHLY}, UnsatisfactionReason.FREQUENCY_MISMATCH),
        (
            {"observation_period": ObservationPeriod.INSTANT},
            UnsatisfactionReason.OBSERVATION_PERIOD_MISMATCH,
        ),
        ({"units": Unit.CURRENCY}, UnsatisfactionReason.UNITS_MISMATCH),
        ({"units": None}, UnsatisfactionReason.UNITS_MISMATCH),
        ({"history": 3}, UnsatisfactionReason.INSUFFICIENT_HISTORY),
        ({"history": None}, UnsatisfactionReason.HISTORY_UNKNOWN),
        (
            {"has_knowledge_date": False},
            UnsatisfactionReason.KNOWLEDGE_DATE_UNAVAILABLE,
        ),
        (
            {"revision_policies": frozenset()},
            UnsatisfactionReason.REVISION_POLICY_UNSUPPORTED,
        ),
    ],
)
def test_each_unsatisfied_property_yields_named_reason(
    capability_overrides: dict, expected_reason: UnsatisfactionReason
) -> None:
    result = check_satisfiable(_requirement(), _capability(**capability_overrides))
    assert result.satisfied is False
    assert expected_reason in result.reasons


def test_positive_vintage_identity_requirement_fails_closed() -> None:
    req = _requirement(require_positive_vintage_identity=True)
    result = check_satisfiable(req, _capability(has_positive_vintage_identity=False))
    assert result.satisfied is False
    assert (
        UnsatisfactionReason.POSITIVE_VINTAGE_IDENTITY_UNAVAILABLE in result.reasons
    )
    # The same capability is admissible once the property is demonstrated.
    assert check_satisfiable(req, _capability()).satisfied is True


def test_data_availability_is_not_semantic_usability() -> None:
    # Plenty of history is present, but without a knowledge date the value
    # is not PIT-admissible and the requirement must fail.
    cap = _capability(history=10_000, has_knowledge_date=False)
    result = check_satisfiable(_requirement(), cap)
    assert result.satisfied is False
    assert UnsatisfactionReason.KNOWLEDGE_DATE_UNAVAILABLE in result.reasons


def test_unknown_history_never_assumed_sufficient() -> None:
    # lookback=0 imposes no history requirement, so unknown history is fine.
    assert check_satisfiable(_requirement(lookback=0), _capability(history=None)).satisfied
    # Any positive lookback with unknown history fails closed.
    assert not check_satisfiable(
        _requirement(lookback=1), _capability(history=None)
    ).satisfied


def test_require_satisfiable_raises_typed_error_with_provenance() -> None:
    req = _requirement()
    cap = _capability(semantic_id="turnover")
    with pytest.raises(DataRequirementUnsatisfiableError) as excinfo:
        require_satisfiable(req, cap)
    err = excinfo.value
    assert isinstance(err.result, SatisfactionResult)
    assert err.result.satisfied is False
    assert (
        UnsatisfactionReason.SEMANTIC_IDENTITY_MISMATCH in err.result.reasons
    )
    assert "semantic_identity_mismatch" in str(err)
    # Provenance survives the failure (both objects are recorded).
    record = err.result.to_dict()
    assert record["requirement"]["semantic_id"] == "return"
    assert record["capability"]["semantic_id"] == "turnover"
    assert record["reasons"]


def test_unsatisfied_result_is_not_an_empty_result() -> None:
    result = check_satisfiable(_requirement(), _capability(semantic_id="turnover"))
    assert result.satisfied is False
    assert result.reason is UnsatisfactionReason.SEMANTIC_IDENTITY_MISMATCH
    assert result.to_dict()["reasons"] == ["semantic_identity_mismatch"]


def test_multiple_unsatisfied_properties_all_recorded() -> None:
    cap = _capability(
        semantic_id="turnover",
        frequency=Frequency.ANNUAL,
        units=None,
        history=1,
        has_knowledge_date=False,
        revision_policies=frozenset(),
    )
    result = check_satisfiable(_requirement(), cap)
    assert set(result.reasons) == {
        UnsatisfactionReason.SEMANTIC_IDENTITY_MISMATCH,
        UnsatisfactionReason.FREQUENCY_MISMATCH,
        UnsatisfactionReason.UNITS_MISMATCH,
        UnsatisfactionReason.INSUFFICIENT_HISTORY,
        UnsatisfactionReason.KNOWLEDGE_DATE_UNAVAILABLE,
        UnsatisfactionReason.REVISION_POLICY_UNSUPPORTED,
    }


def test_check_satisfiable_rejects_wrong_types() -> None:
    with pytest.raises(RequirementValidationError):
        check_satisfiable(_requirement(), object())  # type: ignore[arg-type]
    with pytest.raises(RequirementValidationError):
        check_satisfiable(object(), _capability())  # type: ignore[arg-type]


def test_requirement_methods_delegate_to_checks() -> None:
    req = _requirement()
    assert req.is_satisfied_by(_capability()) is True
    assert req.is_satisfied_by(_capability(semantic_id="turnover")) is False
    assert req.check(_capability()).satisfied is True


# ---------------------------------------------------------------------------
# Structural PIT trust boundary (Phase 6, section 8)
# ---------------------------------------------------------------------------

_FORBIDDEN_FIELD_FRAGMENTS = (
    "knowledge_date",
    "publication",
    "vintage",
    "provider",
    "vendor",
    "universe",
    "formation",
    "future",
    "return_date",
)


def test_requirement_has_no_time_or_provider_choice_fields() -> None:
    field_names = {f.name for f in dataclasses.fields(DataRequirement)}
    assert field_names == {
        "semantic_id",
        "frequency",
        "observation_period",
        "units",
        "lookback",
        "revision_policy",
        "require_knowledge_date",
        "require_positive_vintage_identity",
    }
    # The only permitted mention of "knowledge"/"vintage" is the boolean
    # *requirement* flags -- never a chosen value.
    for name in field_names:
        lowered = name.lower()
        for fragment in _FORBIDDEN_FIELD_FRAGMENTS:
            if lowered == "require_knowledge_date" and fragment == "knowledge_date":
                continue
            if (
                lowered == "require_positive_vintage_identity"
                and fragment == "vintage"
            ):
                continue
            assert fragment not in lowered, (name, fragment)


def test_requirement_exposes_no_forbidden_attributes() -> None:
    req = _requirement()
    for attr in (
        "knowledge_date",
        "publication_date",
        "vintage",
        "provider",
        "universe",
        "formation_date",
        "future_return",
    ):
        assert not hasattr(req, attr)


def test_module_imports_no_vendor_code() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    assert imported, "expected the module to have imports"
    offenders = [
        name
        for name in imported
        if name.startswith("smart_beta.vendors")
        or name.startswith("smart_beta.pit")
    ]
    assert offenders == [], f"module must not import vendor/PIT-engine code: {offenders}"
