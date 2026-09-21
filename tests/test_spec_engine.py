"""Tests for the P6-F PIT trust-boundary admission facade.

Coverage follows the frozen P6-F acceptance criteria (Phase 6 plan section 11
task table, sections 7/8/9, and this task's contract):

* **positive path** -- a valid trusted-input fixture built from the
  repository's *existing* PIT representation
  (:class:`~smart_beta.pit.view.PointInTimeView` over
  :class:`~smart_beta.pit.synthetic.SyntheticPITSource`) is admitted, then
  evaluated through the facade, producing a deterministic factor result whose
  upstream observation selection is *unchanged* by P6-F (the P6-D evaluation
  over the same frames is bit-identical);
* **fail-closed negatives** -- every adversarial trap the task names fails
  closed with a typed, recordable error: missing knowledge-date evidence,
  missing/ambiguous/incomplete positive vintage identity, insufficient
  history, wrong frequency, wrong semantic identity, incompatible units,
  incompatible grid, provenance below the required certification, extra
  undeclared input, missing declared input, and raw multi-vintage candidate
  shapes that a selector would have chosen from;
* **Phase 5B-shape regression** -- value + stock identity + observation
  period exist, but positive vintage identity cannot be established, and the
  declaration of that requirement makes admission fail closed (a bounded,
  synthetic trust-boundary regression only -- it does not certify CH3);
* **no upgrade / provenance** -- the declared evidence class is preserved
  verbatim, the structured P6-B ``SatisfactionResult`` survives, and the
  canonical expression-stack ``RequirementUnsatisfiableError`` is never
  conflated with the facade's translation.

A final trust-boundary meta-check inspects this production module's source
with :mod:`ast` (never by executing it) and asserts it contains no dynamic
execution and imports no vendor/PIT/network/environment module.
"""

from __future__ import annotations

import ast
import pathlib

import pandas as pd
import pytest

from smart_beta.data.schema import STOCK_COL, VALUE_COL
from smart_beta.spec import engine as eng
from smart_beta.spec import evaluator as ev
from smart_beta.spec import requirements as R
from smart_beta.spec.expression import RequirementUnsatisfiableError
from smart_beta.spec.factor_spec import FactorInput, FactorSpec, MissingPolicy
from smart_beta.pit.schema import (
    KNOWLEDGE_DATE_COL,
    REPORT_PERIOD_END_COL,
)
from smart_beta.pit.synthetic import (
    RESTATEMENT_T1,
    RESTATEMENT_T2,
    RESTATEMENT_X,
    RESTATEMENT_Y,
    S_RESTATEMENT,
    SyntheticPITSource,
)
from smart_beta.pit.view import PointInTimeView

# ---------------------------------------------------------------------------
# small deterministic fixtures / helpers
# ---------------------------------------------------------------------------
DATES = pd.to_datetime(["2021-01-01", "2021-02-01", "2021-03-01", "2021-04-01"])
STOCKS = ["A", "B", "C"]

_MODULE_SOURCE_PATH = pathlib.Path(eng.__file__)


def _frame(data=None, dates=DATES, columns=STOCKS) -> pd.DataFrame:
    """Build a value frame (date index, stock columns) deterministically."""
    if data is None:
        data = {
            "A": [1.0, 2.0, 3.0, 4.0],
            "B": [2.0, 4.0, 6.0, 8.0],
            "C": [3.0, 6.0, 9.0, 12.0],
        }
    return pd.DataFrame(data, index=dates).loc[:, list(columns)]


def _requirement(semantic_id: str = "revenue", **overrides: object) -> R.DataRequirement:
    fields: dict[str, object] = {
        "semantic_id": semantic_id,
        "frequency": R.Frequency.MONTHLY,
        "observation_period": R.ObservationPeriod.PERIOD,
        "units": R.Unit.CURRENCY,
        "lookback": 0,
        "revision_policy": R.RevisionPolicy.POINT_IN_TIME,
        "require_knowledge_date": True,
        "require_positive_vintage_identity": False,
    }
    fields.update(overrides)
    return R.DataRequirement(**fields)  # type: ignore[arg-type]


def _capability(semantic_id: str = "revenue", **overrides: object) -> R.DataCapability:
    fields: dict[str, object] = {
        "semantic_id": semantic_id,
        "frequency": R.Frequency.MONTHLY,
        "observation_period": R.ObservationPeriod.PERIOD,
        "units": R.Unit.CURRENCY,
        "history": None,
        "has_knowledge_date": True,
        "has_positive_vintage_identity": False,
        "revision_policies": frozenset({R.RevisionPolicy.POINT_IN_TIME}),
    }
    fields.update(overrides)
    return R.DataCapability(**fields)  # type: ignore[arg-type]


def _spec(
    expression: object = "revenue",
    alias: str = "revenue",
    requirement: R.DataRequirement | None = None,
    inputs: tuple[FactorInput, ...] | None = None,
    frequency: R.Frequency = R.Frequency.MONTHLY,
    missing_policy: MissingPolicy = MissingPolicy.PROPAGATE,
) -> FactorSpec:
    if inputs is None:
        bound = requirement if requirement is not None else _requirement()
        inputs = (FactorInput(alias, bound),)
    return FactorSpec(
        id="factor_under_test",
        description="P6-F admission under test",
        expression=expression,
        inputs=inputs,
        frequency=frequency,
        missing_policy=missing_policy,
    )


def _trusted(
    values: pd.DataFrame | None = None,
    capability: R.DataCapability | None = None,
    evidence_class: eng.EvidenceClass = eng.EvidenceClass.CONSTRUCTED,
    vintage_evidence: eng.VintageEvidence | None = None,
    semantic_id: str = "revenue",
) -> eng.TrustedInput:
    return eng.TrustedInput(
        values=values if values is not None else _frame(),
        capability=capability if capability is not None else _capability(semantic_id=semantic_id),
        evidence_class=evidence_class,
        vintage_evidence=vintage_evidence,
    )


def _complete_vintage(
    values: pd.DataFrame, knowledge: str = "2021-01-15"
) -> eng.VintageEvidence:
    """Aligned, complete per-observation knowledge-date evidence."""
    kd = pd.DataFrame(
        pd.Timestamp(knowledge), index=values.index, columns=values.columns
    )
    return eng.VintageEvidence(knowledge_dates=kd)


def _value_for(panel: pd.DataFrame, stock: str, date: str) -> float:
    match = panel[
        (panel[STOCK_COL] == stock) & (panel["date"] == pd.Timestamp(date))
    ]
    assert len(match) == 1
    return float(match[VALUE_COL].iloc[0])


def _pit_trusted_input(
    as_of: str,
    *,
    require_vintage: bool = True,
    field: str = "revenue",
    start: str = "2019-06-30",
    end: str = "2020-03-31",
) -> eng.TrustedInput:
    """Build an already-selected trusted input through the real PIT layer.

    Uses :class:`~smart_beta.pit.synthetic.SyntheticPITSource` and
    :class:`~smart_beta.pit.view.PointInTimeView` -- the repository's trusted
    PIT representation -- to resolve "latest known as of ``as_of``" and pivot
    the resolved facts (which preserve ``knowledge_date``) into a value frame
    plus per-observation vintage evidence. P6-F consumes this; it does not
    perform any of the resolution itself.
    """
    source = SyntheticPITSource()
    view = PointInTimeView(source)
    resolved = view.as_of(as_of).fundamentals(start, end, [field])

    values = (
        resolved.pivot(
            index=REPORT_PERIOD_END_COL, columns=STOCK_COL, values=VALUE_COL
        )
        .rename_axis(index=None, columns=None)
    )
    knowledge_dates = (
        resolved.pivot(
            index=REPORT_PERIOD_END_COL,
            columns=STOCK_COL,
            values=KNOWLEDGE_DATE_COL,
        )
        .rename_axis(index=None, columns=None)
    )

    capability = R.DataCapability(
        semantic_id=field,
        frequency=R.Frequency.MONTHLY,
        observation_period=R.ObservationPeriod.PERIOD,
        units=R.Unit.CURRENCY,
        history=int(values.shape[0]),
        has_knowledge_date=True,
        has_positive_vintage_identity=True,
        revision_policies=frozenset({R.RevisionPolicy.POINT_IN_TIME}),
    )
    vintage_evidence = (
        eng.VintageEvidence(knowledge_dates=knowledge_dates)
        if require_vintage
        else None
    )
    return eng.TrustedInput(
        values=values,
        capability=capability,
        evidence_class=eng.EvidenceClass.CONSTRUCTED,
        vintage_evidence=vintage_evidence,
    )


# ---------------------------------------------------------------------------
# positive path
# ---------------------------------------------------------------------------
def test_positive_path_admits_and_evaluates_pit_safe_inputs() -> None:
    spec = _spec(requirement=_requirement(require_positive_vintage_identity=True))
    trusted = _pit_trusted_input("2020-05-15")

    admission = eng.admit(spec, {"revenue": trusted})
    assert admission.admitted is True
    assert admission.admitted_aliases == ("revenue",)
    assert admission.failures == ()
    assert admission.aliases[0].evidence_class is eng.EvidenceClass.CONSTRUCTED

    result = eng.evaluate_factor(spec, {"revenue": trusted})
    assert result.admission.admitted is True
    assert list(result.evaluation.panel.columns) == ["date", "stock_id", "value"]

    # The PIT layer selected the original vintage at as_of=2020-05-15.
    assert _value_for(
        result.evaluation.panel, S_RESTATEMENT, "2020-03-31"
    ) == pytest.approx(RESTATEMENT_X)


def test_p6f_does_not_change_upstream_observation_selection() -> None:
    spec = _spec(requirement=_requirement(require_positive_vintage_identity=True))

    early = eng.evaluate_factor(spec, {"revenue": _pit_trusted_input("2020-05-15")})
    late = eng.evaluate_factor(spec, {"revenue": _pit_trusted_input("2020-07-15")})

    # The trusted PIT layer selected X before t2 and Y at/after t2. P6-F
    # admitted each already-selected frame unchanged, so each result carries
    # exactly the vintage the upstream layer chose.
    assert _value_for(early.evaluation.panel, S_RESTATEMENT, "2020-03-31") == pytest.approx(
        RESTATEMENT_X
    )
    assert _value_for(late.evaluation.panel, S_RESTATEMENT, "2020-03-31") == pytest.approx(
        RESTATEMENT_Y
    )
    assert early.evaluation.content_hash != late.evaluation.content_hash


def test_p6f_evaluation_matches_p6d_over_the_same_admitted_frames() -> None:
    spec = _spec(requirement=_requirement(require_positive_vintage_identity=True))
    trusted = _pit_trusted_input("2020-05-15")

    result = eng.evaluate_factor(spec, {"revenue": trusted})

    # Feeding the exact same trusted value frame straight to P6-D yields an
    # identical evaluation: P6-F neither re-selects nor alters observations.
    direct = ev.evaluate(spec, {"revenue": trusted.values})
    assert result.evaluation.content_hash == direct.content_hash
    pd.testing.assert_frame_equal(result.evaluation.panel, direct.panel)


def test_facade_does_not_mutate_trusted_inputs() -> None:
    values = _frame()
    knowledge = _complete_vintage(values)
    before_values = values.copy(deep=True)
    before_knowledge = knowledge.knowledge_dates.copy(deep=True)
    trusted = _trusted(
        values=values,
        capability=_capability(has_positive_vintage_identity=True),
        vintage_evidence=knowledge,
    )
    spec = _spec(requirement=_requirement(require_positive_vintage_identity=True))

    eng.evaluate_factor(spec, {"revenue": trusted})

    pd.testing.assert_frame_equal(values, before_values)
    pd.testing.assert_frame_equal(knowledge.knowledge_dates, before_knowledge)


# ---------------------------------------------------------------------------
# fail-closed negatives
# ---------------------------------------------------------------------------
def test_missing_required_knowledge_date_evidence_fails_closed() -> None:
    spec = _spec(requirement=_requirement(require_knowledge_date=True))
    trusted = _trusted(capability=_capability(has_knowledge_date=False))

    with pytest.raises(eng.AdmissionError) as excinfo:
        eng.admit(spec, {"revenue": trusted})

    reasons = excinfo.value.result.failures[0].reasons
    assert eng.AdmissionReason.KNOWLEDGE_DATE_UNAVAILABLE.value in reasons


def test_missing_positive_vintage_identity_fails_closed() -> None:
    spec = _spec(requirement=_requirement(require_positive_vintage_identity=True))
    trusted = _trusted(
        capability=_capability(has_positive_vintage_identity=False)
    )

    with pytest.raises(eng.AdmissionError) as excinfo:
        eng.admit(spec, {"revenue": trusted})

    reasons = excinfo.value.result.failures[0].reasons
    assert (
        eng.AdmissionReason.POSITIVE_VINTAGE_IDENTITY_UNAVAILABLE.value in reasons
    )
    assert eng.AdmissionReason.VINTAGE_IDENTITY_EVIDENCE_MISSING.value in reasons


def test_phase5b_shape_value_without_vintage_identity_fails_closed() -> None:
    """Bounded, synthetic Phase 5B-shape trust-boundary regression.

    A value exists, stock identity exists, and an observation period exists,
    but positive vintage identity cannot be established. When the
    DataRequirement declares ``require_positive_vintage_identity``, admission
    must be rejected. This does not certify CH3 and makes no live call.
    """
    spec = _spec(requirement=_requirement(require_positive_vintage_identity=True))
    value_exists = _trusted(
        values=_frame(),
        capability=_capability(has_positive_vintage_identity=False),
        vintage_evidence=None,
    )

    with pytest.raises(eng.AdmissionError) as excinfo:
        eng.admit(spec, {"revenue": value_exists})

    reasons = excinfo.value.result.failures[0].reasons
    assert (
        eng.AdmissionReason.POSITIVE_VINTAGE_IDENTITY_UNAVAILABLE.value in reasons
    )
    assert not excinfo.value.result.admitted


def test_ambiguous_vintage_identity_fails_closed() -> None:
    values = _frame()
    ambiguous_counts = pd.DataFrame(
        2.0, index=values.index, columns=values.columns
    )
    vintage = eng.VintageEvidence(
        knowledge_dates=_complete_vintage(values).knowledge_dates,
        candidate_counts=ambiguous_counts,
    )
    spec = _spec(requirement=_requirement(require_positive_vintage_identity=True))
    trusted = _trusted(
        values=values,
        capability=_capability(has_positive_vintage_identity=True),
        vintage_evidence=vintage,
    )

    with pytest.raises(eng.AdmissionError) as excinfo:
        eng.admit(spec, {"revenue": trusted})

    reasons = excinfo.value.result.failures[0].reasons
    assert eng.AdmissionReason.VINTAGE_IDENTITY_EVIDENCE_AMBIGUOUS.value in reasons


def test_incomplete_vintage_evidence_fails_closed() -> None:
    values = _frame()
    knowledge_dates = _complete_vintage(values).knowledge_dates.copy()
    # A present value with no knowable vintage date is not positive evidence.
    knowledge_dates.iloc[0, 0] = pd.NaT
    spec = _spec(requirement=_requirement(require_positive_vintage_identity=True))
    trusted = _trusted(
        values=values,
        capability=_capability(has_positive_vintage_identity=True),
        vintage_evidence=eng.VintageEvidence(knowledge_dates=knowledge_dates),
    )

    with pytest.raises(eng.AdmissionError) as excinfo:
        eng.admit(spec, {"revenue": trusted})

    reasons = excinfo.value.result.failures[0].reasons
    assert (
        eng.AdmissionReason.VINTAGE_IDENTITY_EVIDENCE_INCOMPLETE.value in reasons
    )


def test_vintage_evidence_on_wrong_grid_fails_closed() -> None:
    values = _frame()
    wrong_grid = _complete_vintage(values).knowledge_dates.iloc[:, :1]
    trusted = _trusted(
        values=values,
        capability=_capability(has_positive_vintage_identity=True),
        vintage_evidence=eng.VintageEvidence(knowledge_dates=wrong_grid),
    )
    spec = _spec(requirement=_requirement(require_positive_vintage_identity=True))

    with pytest.raises(eng.VintageIdentityError):
        eng.admit(spec, {"revenue": trusted})


def test_insufficient_required_history_fails_closed() -> None:
    spec = _spec(requirement=_requirement(lookback=10))
    trusted = _trusted(capability=_capability(history=5))

    with pytest.raises(eng.AdmissionError) as excinfo:
        eng.admit(spec, {"revenue": trusted})
    reasons = excinfo.value.result.failures[0].reasons
    assert eng.AdmissionReason.INSUFFICIENT_HISTORY.value in reasons


def test_unknown_history_is_not_sufficient_history() -> None:
    spec = _spec(requirement=_requirement(lookback=10))
    trusted = _trusted(capability=_capability(history=None))

    with pytest.raises(eng.AdmissionError) as excinfo:
        eng.admit(spec, {"revenue": trusted})
    reasons = excinfo.value.result.failures[0].reasons
    assert eng.AdmissionReason.HISTORY_UNKNOWN.value in reasons


def test_wrong_frequency_fails_closed() -> None:
    spec = _spec(requirement=_requirement(frequency=R.Frequency.MONTHLY))
    trusted = _trusted(capability=_capability(frequency=R.Frequency.DAILY))

    with pytest.raises(eng.AdmissionError) as excinfo:
        eng.admit(spec, {"revenue": trusted})
    reasons = excinfo.value.result.failures[0].reasons
    assert eng.AdmissionReason.FREQUENCY_MISMATCH.value in reasons


def test_observation_period_mismatch_fails_closed() -> None:
    spec = _spec(
        requirement=_requirement(observation_period=R.ObservationPeriod.PERIOD)
    )
    trusted = _trusted(
        capability=_capability(observation_period=R.ObservationPeriod.INSTANT)
    )

    with pytest.raises(eng.AdmissionError) as excinfo:
        eng.admit(spec, {"revenue": trusted})
    reasons = excinfo.value.result.failures[0].reasons
    assert eng.AdmissionReason.OBSERVATION_PERIOD_MISMATCH.value in reasons


def test_wrong_semantic_identity_fails_closed() -> None:
    spec = _spec(requirement=_requirement(semantic_id="revenue"))
    trusted = _trusted(capability=_capability(semantic_id="market_cap"))

    with pytest.raises(eng.AdmissionError) as excinfo:
        eng.admit(spec, {"revenue": trusted})
    reasons = excinfo.value.result.failures[0].reasons
    assert eng.AdmissionReason.SEMANTIC_IDENTITY_MISMATCH.value in reasons


def test_incompatible_units_fails_closed() -> None:
    spec = _spec(requirement=_requirement(units=R.Unit.CURRENCY))
    trusted = _trusted(capability=_capability(units=R.Unit.RATIO))

    with pytest.raises(eng.AdmissionError) as excinfo:
        eng.admit(spec, {"revenue": trusted})
    reasons = excinfo.value.result.failures[0].reasons
    assert eng.AdmissionReason.UNITS_MISMATCH.value in reasons


def test_incompatible_grid_fails_closed() -> None:
    inputs = (
        FactorInput("revenue", _requirement(semantic_id="revenue")),
        FactorInput("market_cap", _requirement(semantic_id="market_cap")),
    )
    spec = _spec(expression="revenue + market_cap", inputs=inputs)
    revenue = _trusted(values=_frame(), semantic_id="revenue")
    market_cap = _trusted(
        values=_frame(
            {"A": [1.0, 2.0, 3.0], "B": [2.0, 4.0, 6.0], "C": [3.0, 6.0, 9.0]},
            dates=DATES[:3],
        ),
        capability=_capability(semantic_id="market_cap"),
        semantic_id="market_cap",
    )

    with pytest.raises(eng.GridMismatchError):
        eng.admit(spec, {"revenue": revenue, "market_cap": market_cap})


def test_evidence_provenance_below_required_certification_fails_closed() -> None:
    spec = _spec()
    trusted = _trusted(evidence_class=eng.EvidenceClass.CONSTRUCTED)

    with pytest.raises(eng.AdmissionError) as excinfo:
        eng.admit(
            spec,
            {"revenue": trusted},
            required_evidence_class=eng.EvidenceClass.LIVE_RECORDED,
        )
    reasons = excinfo.value.result.failures[0].reasons
    assert eng.AdmissionReason.EVIDENCE_CLASS_BELOW_REQUIRED.value in reasons


def test_extra_undeclared_input_fails_closed() -> None:
    spec = _spec()
    trusted = _trusted()

    with pytest.raises(eng.UndeclaredInputError):
        eng.admit(spec, {"revenue": trusted, "not_declared": trusted})


def test_missing_declared_input_fails_closed() -> None:
    spec = _spec()

    with pytest.raises(eng.MissingInputError):
        eng.admit(spec, {})


def test_duplicate_input_fails_closed() -> None:
    spec = _spec()
    trusted = _trusted()

    with pytest.raises(eng.DuplicateInputError):
        eng.admit(spec, [("revenue", trusted), ("revenue", trusted)])


def test_bare_value_frame_without_capability_fails_closed() -> None:
    spec = _spec()

    with pytest.raises(eng.TrustBoundaryError):
        eng.admit(spec, {"revenue": _frame()})


def test_raw_multi_vintage_candidate_shape_is_rejected_not_selected() -> None:
    """A raw long fundamentals frame (many vintages per fact) has no single
    already-selected value: the facade rejects the shape rather than choosing.
    """
    raw = pd.DataFrame(
        {
            STOCK_COL: ["A", "A"],
            REPORT_PERIOD_END_COL: pd.to_datetime(["2020-03-31", "2020-03-31"]),
            KNOWLEDGE_DATE_COL: pd.to_datetime(["2020-04-30", "2020-06-30"]),
            VALUE_COL: [1.0e9, 8.0e8],
        }
    )
    spec = _spec(requirement=_requirement(require_positive_vintage_identity=True))
    trusted = _trusted(
        values=raw,
        capability=_capability(has_positive_vintage_identity=True),
    )

    with pytest.raises(eng.InputShapeError):
        eng.admit(spec, {"revenue": trusted})


def test_unresolved_candidate_multiplicity_is_never_selected() -> None:
    values = _frame()
    counts = _complete_vintage(values).knowledge_dates.notna().astype("float64")
    counts.iloc[1, 1] = 3.0  # one observation still has three raw candidates
    vintage = eng.VintageEvidence(
        knowledge_dates=_complete_vintage(values).knowledge_dates,
        candidate_counts=counts,
    )
    spec = _spec(requirement=_requirement(require_positive_vintage_identity=True))
    trusted = _trusted(
        values=values,
        capability=_capability(has_positive_vintage_identity=True),
        vintage_evidence=vintage,
    )

    with pytest.raises(eng.AdmissionError) as excinfo:
        eng.admit(spec, {"revenue": trusted})
    reasons = excinfo.value.result.failures[0].reasons
    assert eng.AdmissionReason.VINTAGE_IDENTITY_EVIDENCE_AMBIGUOUS.value in reasons


def test_unsupported_revision_policy_fails_closed() -> None:
    spec = _spec(
        requirement=_requirement(
            revision_policy=R.RevisionPolicy.AS_FIRST_REPORTED,
            require_positive_vintage_identity=True,
        )
    )
    values = _frame()
    trusted = _trusted(
        values=values,
        capability=_capability(
            has_positive_vintage_identity=True,
            revision_policies=frozenset({R.RevisionPolicy.POINT_IN_TIME}),
        ),
        vintage_evidence=eng.VintageEvidence(
            knowledge_dates=_complete_vintage(values).knowledge_dates
        ),
    )

    with pytest.raises(eng.AdmissionError) as excinfo:
        eng.admit(spec, {"revenue": trusted})
    reasons = excinfo.value.result.failures[0].reasons
    assert eng.AdmissionReason.REVISION_POLICY_UNSUPPORTED.value in reasons


def test_multiple_reasons_are_all_recorded() -> None:
    spec = _spec(
        requirement=_requirement(
            semantic_id="revenue",
            frequency=R.Frequency.MONTHLY,
            units=R.Unit.CURRENCY,
            lookback=10,
            require_knowledge_date=True,
        )
    )
    trusted = _trusted(
        capability=_capability(
            semantic_id="market_cap",
            frequency=R.Frequency.DAILY,
            units=R.Unit.RATIO,
            history=1,
            has_knowledge_date=False,
        )
    )

    with pytest.raises(eng.AdmissionError) as excinfo:
        eng.admit(spec, {"revenue": trusted})

    reasons = set(excinfo.value.result.failures[0].reasons)
    assert {
        eng.AdmissionReason.SEMANTIC_IDENTITY_MISMATCH.value,
        eng.AdmissionReason.FREQUENCY_MISMATCH.value,
        eng.AdmissionReason.UNITS_MISMATCH.value,
        eng.AdmissionReason.INSUFFICIENT_HISTORY.value,
        eng.AdmissionReason.KNOWLEDGE_DATE_UNAVAILABLE.value,
    } <= reasons


# ---------------------------------------------------------------------------
# provenance, no-upgrade, determinism
# ---------------------------------------------------------------------------
def test_engine_result_records_structured_provenance() -> None:
    spec = _spec(requirement=_requirement(require_positive_vintage_identity=True))
    trusted = _pit_trusted_input("2020-05-15")

    result = eng.evaluate_factor(spec, {"revenue": trusted})
    record = result.to_dict()

    assert record["admission"]["factor_id"] == spec.id
    assert record["admission"]["factor_version"] == spec.version
    assert record["admission"]["admitted"] is True
    alias_record = record["admission"]["aliases"][0]
    assert alias_record["alias"] == "revenue"
    assert alias_record["admitted"] is True
    assert alias_record["reasons"] == []
    # The reused P6-B SatisfactionResult survives as structured provenance.
    assert alias_record["satisfaction"]["satisfied"] is True
    assert alias_record["satisfaction"]["requirement"]["semantic_id"] == "revenue"
    assert record["evaluation"]["factor_id"] == spec.id
    assert record["content_hash"] == result.content_hash
    assert eng.engine_hash(result) == result.content_hash
    assert eng.canonical_json(result)


def test_evidence_class_is_preserved_never_upgraded() -> None:
    spec = _spec()
    trusted = _trusted(evidence_class=eng.EvidenceClass.PROXY_OBSERVED)

    admission = eng.admit(spec, {"revenue": trusted})

    assert admission.aliases[0].evidence_class is eng.EvidenceClass.PROXY_OBSERVED
    assert (
        admission.to_dict()["aliases"][0]["evidence_class"]
        == "proxy_observed"
    )


def test_admission_error_is_typed_and_not_the_canonical_requirement_error() -> None:
    spec = _spec(requirement=_requirement(require_knowledge_date=True))
    trusted = _trusted(capability=_capability(has_knowledge_date=False))

    with pytest.raises(eng.AdmissionError) as excinfo:
        eng.admit(spec, {"revenue": trusted})

    error = excinfo.value
    assert isinstance(error, eng.TrustBoundaryError)
    assert isinstance(error.result, eng.AdmissionResult)
    # The upstream DataRequirementUnsatisfiableError is translated explicitly
    # and never conflated with the canonical expression-stack error.
    assert not isinstance(error, RequirementUnsatisfiableError)
    assert engine_source_does_not_import("RequirementUnsatisfiableError")


def test_evaluation_is_deterministic_across_calls() -> None:
    spec = _spec(requirement=_requirement(require_positive_vintage_identity=True))
    trusted = _pit_trusted_input("2020-05-15")

    first = eng.evaluate_factor(spec, {"revenue": trusted})
    second = eng.evaluate_factor(spec, {"revenue": trusted})

    assert first.content_hash == second.content_hash
    assert eng.canonical_json(first) == eng.canonical_json(second)
    pd.testing.assert_frame_equal(first.evaluation.panel, second.evaluation.panel)


def test_result_is_independent_of_mapping_insertion_order() -> None:
    inputs = (
        FactorInput("revenue", _requirement(semantic_id="revenue")),
        FactorInput("market_cap", _requirement(semantic_id="market_cap")),
    )
    spec = _spec(expression="revenue / market_cap", inputs=inputs)
    revenue = _trusted(values=_frame(), semantic_id="revenue")
    market_cap = _trusted(
        values=_frame(
            {"A": [4.0, 8.0, 12.0, 16.0], "B": [8.0, 16.0, 24.0, 32.0],
             "C": [12.0, 24.0, 36.0, 48.0]}
        ),
        capability=_capability(semantic_id="market_cap"),
        semantic_id="market_cap",
    )

    forward = eng.evaluate_factor(spec, {"revenue": revenue, "market_cap": market_cap})
    reverse = eng.evaluate_factor(
        spec, [("market_cap", market_cap), ("revenue", revenue)]
    )

    assert forward.content_hash == reverse.content_hash
    assert forward.admission.to_dict() == reverse.admission.to_dict()


# ---------------------------------------------------------------------------
# trust-boundary meta-checks (source inspection, never execution)
# ---------------------------------------------------------------------------
def test_admission_reason_covers_every_requirement_contract_reason() -> None:
    requirement_reasons = {reason.value for reason in R.UnsatisfactionReason}
    admission_reasons = {reason.value for reason in eng.AdmissionReason}
    assert requirement_reasons <= admission_reasons


def engine_source_does_not_import(name: str) -> bool:
    source = _MODULE_SOURCE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == name:
                    return False
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[-1] == name:
                    return False
    return True


def _imported_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    return modules


def test_engine_module_has_no_dynamic_execution_or_vendor_import() -> None:
    source = _MODULE_SOURCE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    forbidden_calls = {"eval", "exec", "compile", "__import__"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in forbidden_calls, (
                f"engine.py must not call {node.func.id}()"
            )

    modules = _imported_modules(tree)
    for module in modules:
        assert not module.startswith("smart_beta.vendors")
    for banned in ("requests", "urllib", "socket", "subprocess", "random", "os"):
        assert not any(
            module == banned or module.startswith(f"{banned}.")
            for module in modules
        )


def test_engine_module_has_no_trusted_layer_import() -> None:
    """The admission facade consumes already-selected inputs only.

    It therefore imports neither the PIT resolver nor any vendor adapter:
    importing the resolver would give admission the structural opportunity to
    select, which is exactly what P6-F must not do. The PIT layer is consumed
    through its *already-resolved output* (see the positive-path fixture).
    """
    modules = _imported_modules(ast.parse(_MODULE_SOURCE_PATH.read_text("utf-8")))
    assert not any(module.startswith("smart_beta.pit") for module in modules)


def test_engine_module_never_imports_the_canonical_requirement_error() -> None:
    assert engine_source_does_not_import("RequirementUnsatisfiableError")
