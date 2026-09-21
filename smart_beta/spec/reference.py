"""Reference / migration fixtures for the Phase 6 declarative vocabulary (P6-G).

Phase 6, task **P6-G**. This module is *not* a production transform, a data
source, or an empirical study. It is a small, frozen, deterministic set of
**reference** :class:`~smart_beta.spec.factor_spec.FactorSpec` definitions
plus the tiny synthetic reference dataset they consume, whose only purpose is
to demonstrate -- end to end through the trusted Phase 6 stack -- that the
frozen declarative vocabulary reproduces representative factor behaviour.

The reference stress the vocabulary, not the market
---------------------------------------------------

Each fixture isolates one category of the frozen section 10 fixture list:

1. a raw-field factor (``market_cap``);
2. a lagged-return factor (lag-by-N);
3. a rolling-mean return factor (momentum proxy);
4. a ratio of two fields;
5. a difference of two fields;
6. a cross-sectional standardize of a raw field;
7. a cross-sectional rank of a raw field;
8. a cross-sectional winsorize of a raw field;
9. a CH4-like turnover-ratio factor,
   ``mean(turnover, short) / mean(turnover, long)``.

The fixture data is deliberately trivial -- a handful of monthly dates and
four synthetic securities -- so a human can audit every expected value by
hand. Expected outputs are **not** stored here: the companion test
``tests/test_spec_reference.py`` derives every expectation independently
(plain pandas arithmetic and/or closed-form values) and compares it to the
engine output, so a fixture can never be "confirmed" by re-running the very
code under test.

Fixture properties (all binding)
--------------------------------

* **Deterministic** -- fixed dates, fixed securities, fixed values, no
  randomness, no clock, no environment, no network.
* **Small and human-auditable** -- six monthly observations, four securities.
* **Vendor-independent** -- no vendor name appears in any
  :class:`~smart_beta.spec.factor_spec.FactorSpec`,
  :class:`~smart_beta.spec.requirements.DataRequirement`,
  :class:`~smart_beta.spec.requirements.DataCapability`, alias, or column.
* **PIT-safe by construction** -- the reference frames are already-selected,
  already-aligned, single-vintage panels over observation dates at which each
  value is knowable, and the accompanying
  :class:`~smart_beta.spec.requirements.DataCapability` declares
  ``has_knowledge_date=True``. No temporal selection happens here; the
  trusted boundary owns it (Phase 6 plan, section 8).
* **Provenance preserved** -- the reference inputs declare
  :attr:`~smart_beta.spec.engine.EvidenceClass.CONSTRUCTED`, the weakest
  evidence class, and the facade preserves that label verbatim. This
  demonstrates behaviour only; it certifies no provider, no data source, and
  no empirical result.

Scope exclusions (binding)
---------------------------

* No CH3 / ``profit_dedt`` dependency and no Phase 5B CH3 certification is
  referenced, asserted, or reopened.
* CAPM / CH3 / CH4 portfolio-level benchmarks are **not** re-expressed as
  FactorSpecs; portfolio construction stays an engine responsibility outside
  the frozen vocabulary (Phase 6 plan, section 10). The turnover-ratio
  fixture is a reference *behaviour* fixture -- not CH4 empirical
  replication, not benchmark certification, not production-data
  certification, and not economic validation.
* No live provider call of any kind is made; only the in-module synthetic
  reference dataset is used.

The trusted end-to-end path a fixture exercises is::

    FactorSpec -> DataRequirement admission (P6-F admit)
              -> P6-F evaluate_factor -> P6-D evaluator -> P6-E transforms
              -> frozen (date, stock_id, value) factor panel
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import pandas as pd

from smart_beta.spec.engine import (
    EngineResult,
    EvidenceClass,
    TrustedInput,
    evaluate_factor,
)
from smart_beta.spec.factor_spec import FactorInput, FactorSpec, MissingPolicy
from smart_beta.spec.requirements import (
    DataCapability,
    DataRequirement,
    Frequency,
    ObservationPeriod,
    RevisionPolicy,
    Unit,
)

__all__ = [
    "REFERENCE_DATES",
    "REFERENCE_STOCKS",
    "ReferenceFixture",
    "difference_of_fields_factor",
    "lagged_return_factor",
    "rank_of_market_cap_factor",
    "ratio_of_fields_factor",
    "raw_market_cap_factor",
    "reference_capability",
    "reference_dataset",
    "reference_fixture",
    "reference_fixtures",
    "reference_inputs",
    "reference_requirement",
    "reference_specs",
    "rolling_mean_return_factor",
    "standardize_of_market_cap_factor",
    "turnover_ratio_factor",
    "winsorize_of_market_cap_factor",
]


# ---------------------------------------------------------------------------
# the tiny, fully synthetic reference panel
# ---------------------------------------------------------------------------
_REFERENCE_DATE_STRINGS = (
    "2021-01-31",
    "2021-02-28",
    "2021-03-31",
    "2021-04-30",
    "2021-05-31",
    "2021-06-30",
)

#: Observation dates of the reference panel (monthly, six observations).
REFERENCE_DATES: tuple[pd.Timestamp, ...] = tuple(
    pd.Timestamp(value) for value in _REFERENCE_DATE_STRINGS
)

#: Synthetic securities of the reference panel.
REFERENCE_STOCKS: tuple[str, ...] = ("A", "B", "C", "D")

#: The semantic roles / FactorSpec aliases the reference panel supplies.
_REFERENCE_DATA: Mapping[str, Mapping[str, Sequence[float]]] = {
    "market_cap": {
        "A": (100.0, 110.0, 120.0, 130.0, 140.0, 150.0),
        "B": (200.0, 190.0, 180.0, 170.0, 160.0, 150.0),
        "C": (50.0, 55.0, 60.0, 65.0, 70.0, 75.0),
        "D": (400.0, 380.0, 360.0, 340.0, 320.0, 300.0),
    },
    "return": {
        "A": (0.01, 0.02, 0.03, 0.04, 0.05, 0.06),
        "B": (0.02, 0.02, 0.02, 0.02, 0.02, 0.02),
        "C": (0.03, 0.01, -0.01, 0.02, 0.00, 0.04),
        "D": (0.04, -0.02, 0.03, -0.01, 0.02, -0.03),
    },
    "turnover": {
        "A": (0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
        "B": (1.0, 0.9, 0.8, 0.7, 0.6, 0.5),
        "C": (0.4, 0.4, 0.4, 0.4, 0.4, 0.4),
        "D": (2.0, 2.0, 2.0, 2.0, 2.0, 2.0),
    },
    "revenue": {
        "A": (100.0, 120.0, 110.0, 130.0, 140.0, 150.0),
        "B": (200.0, 180.0, 220.0, 210.0, 190.0, 230.0),
        "C": (80.0, 90.0, 85.0, 95.0, 100.0, 105.0),
        "D": (300.0, 280.0, 320.0, 300.0, 340.0, 360.0),
    },
    "earnings": {
        "A": (10.0, 12.0, 11.0, 13.0, 14.0, 15.0),
        "B": (20.0, 18.0, 22.0, 21.0, 19.0, 23.0),
        "C": (8.0, 9.0, 8.5, 9.5, 10.0, 10.5),
        "D": (30.0, 28.0, 32.0, 30.0, 34.0, 36.0),
    },
}

#: Semantic identity, effective-time shape and unit of each reference role.
_REFERENCE_SEMANTICS: Mapping[str, tuple[str, ObservationPeriod, Unit]] = {
    "market_cap": ("market_cap", ObservationPeriod.INSTANT, Unit.CURRENCY),
    "return": ("return", ObservationPeriod.PERIOD, Unit.FRACTION),
    "turnover": ("turnover", ObservationPeriod.PERIOD, Unit.RATIO),
    "revenue": ("revenue", ObservationPeriod.PERIOD, Unit.CURRENCY),
    "earnings": ("earnings", ObservationPeriod.PERIOD, Unit.CURRENCY),
}


def reference_dataset() -> dict[str, pd.DataFrame]:
    """Return fresh, deterministic value frames for every reference role.

    Each frame has the reference observation dates on its (unnamed) index and
    the reference securities as its (unnamed) columns, with ``float64``
    values. A fresh copy is returned on every call so a caller can never
    mutate the frozen reference panel.
    """
    return {
        role: pd.DataFrame(
            {stock: list(values) for stock, values in by_stock.items()},
            index=pd.DatetimeIndex(REFERENCE_DATES),
            columns=list(REFERENCE_STOCKS),
            dtype="float64",
        )
        for role, by_stock in _REFERENCE_DATA.items()
    }


# ---------------------------------------------------------------------------
# vendor-free requirement / capability declarations
# ---------------------------------------------------------------------------
def reference_requirement(role: str, *, lookback: int = 0) -> DataRequirement:
    """The declared requirement for a reference role.

    ``lookback`` is expressed in observation periods and never refers to a
    future period. The requirement is vendor-free, PIT-admissible
    (``require_knowledge_date=True``) and declares no positive vintage
    identity: the reference panel is single-vintage by construction, and
    claiming an unavailable vintage signal would be exactly the silent
    upgrade the trust boundary forbids.
    """
    if role not in _REFERENCE_SEMANTICS:
        raise KeyError(f"unknown reference role {role!r}")
    if not isinstance(lookback, int) or isinstance(lookback, bool):
        raise TypeError("lookback must be an integer number of periods")
    if lookback < 0:
        raise ValueError("lookback must be non-negative")
    if lookback > len(REFERENCE_DATES):
        raise ValueError(
            f"lookback {lookback} exceeds the {len(REFERENCE_DATES)} reference "
            "observations; a reference fixture must be satisfiable by its own "
            "dataset"
        )
    semantic_id, observation_period, units = _REFERENCE_SEMANTICS[role]
    return DataRequirement(
        semantic_id=semantic_id,
        frequency=Frequency.MONTHLY,
        observation_period=observation_period,
        units=units,
        lookback=lookback,
        revision_policy=RevisionPolicy.POINT_IN_TIME,
        require_knowledge_date=True,
        require_positive_vintage_identity=False,
    )


def reference_capability(role: str) -> DataCapability:
    """What the reference panel can demonstrate for a reference role.

    The capability is a declaration by the fixture, not a provider claim; the
    facade compares it against the requirement with P6-B's fail-closed
    machinery. History equals the number of reference observations.
    """
    if role not in _REFERENCE_SEMANTICS:
        raise KeyError(f"unknown reference role {role!r}")
    semantic_id, observation_period, units = _REFERENCE_SEMANTICS[role]
    return DataCapability(
        semantic_id=semantic_id,
        frequency=Frequency.MONTHLY,
        observation_period=observation_period,
        units=units,
        history=len(REFERENCE_DATES),
        has_knowledge_date=True,
        has_positive_vintage_identity=False,
        revision_policies=frozenset({RevisionPolicy.POINT_IN_TIME}),
    )


def reference_inputs(aliases: Sequence[str]) -> dict[str, TrustedInput]:
    """Build already-selected, already-aligned trusted inputs for ``aliases``.

    Every input is labelled :attr:`~smart_beta.spec.engine.EvidenceClass.CONSTRUCTED`
    -- the weakest, honest class for a synthetic fixture -- and the facade
    never upgrades it.
    """
    dataset = reference_dataset()
    inputs: dict[str, TrustedInput] = {}
    for alias in aliases:
        if alias not in dataset:
            raise KeyError(f"no reference panel for alias {alias!r}")
        inputs[alias] = TrustedInput(
            values=dataset[alias],
            capability=reference_capability(alias),
            evidence_class=EvidenceClass.CONSTRUCTED,
        )
    return inputs


# ---------------------------------------------------------------------------
# the reference FactorSpec factories (one per frozen section 10 category)
# ---------------------------------------------------------------------------
def raw_market_cap_factor() -> FactorSpec:
    """Category 1: a raw-field factor over a market-cap role."""
    return FactorSpec(
        id="ref_raw_market_cap",
        description="Raw market-cap field with no transform.",
        hypothesis="A raw level field is the simplest expressible factor.",
        expression="market_cap",
        inputs=(FactorInput("market_cap", reference_requirement("market_cap")),),
        frequency=Frequency.MONTHLY,
        missing_policy=MissingPolicy.PROPAGATE,
    )


def lagged_return_factor(periods: int = 1) -> FactorSpec:
    """Category 2: an explicit lag-by-N of a return field."""
    return FactorSpec(
        id=f"ref_lagged_return_{periods}",
        description=f"Return lagged by {periods} observation period(s).",
        hypothesis="A lagged return is strictly trailing, never look-ahead.",
        expression=f"lag(return, {periods})",
        inputs=(
            FactorInput(
                "return", reference_requirement("return", lookback=periods)
            ),
        ),
        frequency=Frequency.MONTHLY,
        missing_policy=MissingPolicy.PROPAGATE,
    )


def rolling_mean_return_factor(window: int = 3) -> FactorSpec:
    """Category 3: a trailing rolling-mean return factor (momentum proxy)."""
    return FactorSpec(
        id=f"ref_rolling_mean_return_{window}",
        description=f"Trailing {window}-period mean of the return field.",
        hypothesis="A trailing mean return is the simplest momentum proxy.",
        expression=f"mean(return, {window})",
        inputs=(
            FactorInput(
                "return", reference_requirement("return", lookback=window)
            ),
        ),
        frequency=Frequency.MONTHLY,
        missing_policy=MissingPolicy.PROPAGATE,
    )


def ratio_of_fields_factor() -> FactorSpec:
    """Category 4: a ratio of two raw fields (revenue / earnings)."""
    return FactorSpec(
        id="ref_ratio_revenue_to_earnings",
        description="Ratio of the revenue field to the earnings field.",
        hypothesis="A ratio of two levels is expressible without any helper.",
        expression="revenue / earnings",
        inputs=(
            FactorInput("revenue", reference_requirement("revenue")),
            FactorInput("earnings", reference_requirement("earnings")),
        ),
        frequency=Frequency.MONTHLY,
        missing_policy=MissingPolicy.PROPAGATE,
    )


def difference_of_fields_factor() -> FactorSpec:
    """Category 5: a difference of two raw fields (revenue - earnings)."""
    return FactorSpec(
        id="ref_difference_revenue_minus_earnings",
        description="Difference of the revenue field and the earnings field.",
        hypothesis="A difference of two levels is expressible arithmetically.",
        expression="revenue - earnings",
        inputs=(
            FactorInput("revenue", reference_requirement("revenue")),
            FactorInput("earnings", reference_requirement("earnings")),
        ),
        frequency=Frequency.MONTHLY,
        missing_policy=MissingPolicy.PROPAGATE,
    )


def standardize_of_market_cap_factor() -> FactorSpec:
    """Category 6: a per-date cross-sectional standardize of a raw field."""
    return FactorSpec(
        id="ref_standardize_market_cap",
        description="Per-date cross-sectional standardized market cap.",
        hypothesis="Cross-sectional standardization is a per-date transform.",
        expression="standardize(market_cap)",
        inputs=(FactorInput("market_cap", reference_requirement("market_cap")),),
        frequency=Frequency.MONTHLY,
        missing_policy=MissingPolicy.PROPAGATE,
    )


def rank_of_market_cap_factor() -> FactorSpec:
    """Category 7: a per-date cross-sectional rank of a raw field."""
    return FactorSpec(
        id="ref_rank_market_cap",
        description="Per-date cross-sectional rank of market cap.",
        hypothesis="Cross-sectional ranking is a per-date transform.",
        expression="rank(market_cap)",
        inputs=(FactorInput("market_cap", reference_requirement("market_cap")),),
        frequency=Frequency.MONTHLY,
        missing_policy=MissingPolicy.PROPAGATE,
    )


def winsorize_of_market_cap_factor(
    lower: float = 0.25, upper: float = 0.75
) -> FactorSpec:
    """Category 8: a per-date cross-sectional winsorize of a raw field."""
    return FactorSpec(
        id="ref_winsorize_market_cap",
        description=(
            f"Per-date cross-sectional winsorization of market cap at "
            f"({lower}, {upper}) quantile bounds."
        ),
        hypothesis="Winsorization is explicit about its quantile bounds.",
        expression=f"winsorize(market_cap, {lower}, {upper})",
        inputs=(FactorInput("market_cap", reference_requirement("market_cap")),),
        frequency=Frequency.MONTHLY,
        missing_policy=MissingPolicy.PROPAGATE,
    )


def turnover_ratio_factor(short: int = 3, long: int = 5) -> FactorSpec:
    """Category 9: a CH4-like ``mean(turnover, short) / mean(turnover, long)``.

    This is a reference **behaviour** fixture only. It is not CH4 empirical
    replication, not benchmark certification, not production-data
    certification, and not economic validation; portfolio-level benchmark
    construction remains outside the frozen vocabulary.
    """
    if not isinstance(short, int) or isinstance(short, bool):
        raise TypeError("short window must be an integer number of periods")
    if not isinstance(long, int) or isinstance(long, bool):
        raise TypeError("long window must be an integer number of periods")
    if short < 1 or long < 1:
        raise ValueError("rolling windows must be >= 1")
    if short >= long:
        raise ValueError(
            "the short turnover window must be strictly shorter than the long "
            "window so the fixture has the documented shape"
        )
    return FactorSpec(
        id=f"ref_turnover_ratio_{short}_{long}",
        description=(
            f"Ratio of the {short}-period mean turnover to the {long}-period "
            "mean turnover."
        ),
        hypothesis="A turnover-ratio is two trailing means and a ratio.",
        expression=f"mean(turnover, {short}) / mean(turnover, {long})",
        inputs=(
            FactorInput(
                "turnover", reference_requirement("turnover", lookback=long)
            ),
        ),
        frequency=Frequency.MONTHLY,
        missing_policy=MissingPolicy.PROPAGATE,
    )


# ---------------------------------------------------------------------------
# fixture registry
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ReferenceFixture:
    """A named reference :class:`FactorSpec` plus its trusted reference inputs.

    The bundle is intentionally minimal: it carries the spec and the
    already-selected trusted inputs (one per declared alias). It performs no
    evaluation itself -- a caller runs it through
    :func:`smart_beta.spec.engine.evaluate_factor` so the trusted end-to-end
    path is exercised explicitly.
    """

    name: str
    spec: FactorSpec
    inputs: Mapping[str, TrustedInput]

    def evaluate(
        self, *, required_evidence_class: EvidenceClass = EvidenceClass.CONSTRUCTED
    ) -> EngineResult:
        """Convenience wrapper: admit then evaluate through P6-F."""
        return evaluate_factor(
            self.spec,
            self.inputs,
            required_evidence_class=required_evidence_class,
        )


_FIXTURE_FACTORIES: tuple[tuple[str, Callable[[], FactorSpec]], ...] = (
    ("raw_market_cap", raw_market_cap_factor),
    ("lagged_return", lagged_return_factor),
    ("rolling_mean_return", rolling_mean_return_factor),
    ("ratio_of_fields", ratio_of_fields_factor),
    ("difference_of_fields", difference_of_fields_factor),
    ("standardize_market_cap", standardize_of_market_cap_factor),
    ("rank_market_cap", rank_of_market_cap_factor),
    ("winsorize_market_cap", winsorize_of_market_cap_factor),
    ("turnover_ratio", turnover_ratio_factor),
)


def reference_specs() -> tuple[FactorSpec, ...]:
    """Every reference :class:`FactorSpec`, in category order."""
    return tuple(factory() for _, factory in _FIXTURE_FACTORIES)


def reference_fixtures() -> tuple[ReferenceFixture, ...]:
    """Every reference fixture (spec + trusted inputs), in category order."""
    fixtures: list[ReferenceFixture] = []
    for name, factory in _FIXTURE_FACTORIES:
        spec = factory()
        fixtures.append(
            ReferenceFixture(
                name=name,
                spec=spec,
                inputs=reference_inputs(spec.aliases),
            )
        )
    return tuple(fixtures)


def reference_fixture(name: str) -> ReferenceFixture:
    """Return the named reference fixture, or fail closed on an unknown name."""
    for fixture in reference_fixtures():
        if fixture.name == name:
            return fixture
    known = ", ".join(name for name, _ in _FIXTURE_FACTORIES)
    raise KeyError(f"unknown reference fixture {name!r}; known fixtures: {known}")
