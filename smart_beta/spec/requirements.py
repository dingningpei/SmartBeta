"""Vendor-free, fail-closed data-requirement contract (Phase 6, P6-B).

A :class:`DataRequirement` states *what semantic properties* a factor input
requires of the trusted PIT infrastructure. It deliberately says nothing
about *which* provider supplies a value and nothing about *how* the trusted
engine resolves time: knowledge-date selection, vintage selection, provider
choice, universe construction, formation-date alignment and future-return
alignment are all owned by the trusted boundary (Phase 6 plan, section 8),
never by this contract.

Design invariants (Phase 6 plan, sections 5 and 9):

* **Vendor-free** -- a semantic identifier is an economic quantity
  (``"return"``, ``"market_cap"``, ``"turnover"``, ...), never a vendor
  column. :func:`assert_vendor_free` rejects known vendor tokens, and no
  vendor name may appear in a :class:`DataRequirement`.
* **data availability is not semantic usability** -- the presence of *a*
  value does not satisfy a requirement. A capability must demonstrate the
  declared semantic identity, frequency, observation period, units, PIT
  admissibility and (where required) positive vintage identity, or it fails.
* **value semantics are not vintage semantics** -- a field whose economic
  meaning matches does not automatically establish when its value became
  knowable; PIT admissibility and positive vintage identity are separately
  declarable requirements.
* **provider capability is not spec admissibility** -- a capability that
  cannot satisfy a declared requirement fails closed with a named reason,
  never by silent substitution.
* **negative certification is representable** -- an unsatisfiable
  requirement produces a typed, recordable :class:`SatisfactionResult` and
  a :class:`RequirementUnsatisfiableError`, never a silent empty result.
* **provider quirks stay outside the spec** -- this module never imports,
  names, or special-cases any concrete vendor or field; the mechanism is
  generic and is not specialised to ``profit_dedt`` or any single field.
* **PIT-structural** -- the dataclasses here expose no field that could
  choose a knowledge date, a publication vintage, a provider, a security
  universe, a formation date, or a future return.

The module is self-contained: it has no dependencies on the PIT engine or
on vendor adapters. A provider adapter is expected to expose what it can
offer as a :class:`DataCapability`, and the trusted boundary calls
:func:`check_satisfiable` / :func:`require_satisfiable` to decide
admissibility.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

__all__ = [
    "DataCapability",
    "DataRequirement",
    "Frequency",
    "ObservationPeriod",
    "RequirementUnsatisfiableError",
    "RequirementValidationError",
    "RevisionPolicy",
    "SatisfactionResult",
    "Unit",
    "UnsatisfactionReason",
    "VendorNameError",
    "KNOWN_VENDOR_NAMES",
    "assert_vendor_free",
    "check_satisfiable",
    "find_vendor_names",
    "require_satisfiable",
]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class RequirementValidationError(ValueError):
    """A :class:`DataRequirement`/:class:`DataCapability` is malformed.

    Raised at construction time, so an invalid requirement can never reach
    the trusted boundary.
    """


class VendorNameError(RequirementValidationError):
    """A semantic identifier names a data vendor.

    Naming a vendor would move a provider-specific choice into the spec
    layer, violating the PIT trust boundary (Phase 6 plan, section 8).
    """


class RequirementUnsatisfiableError(Exception):
    """A capability cannot satisfy a declared data requirement (fail-closed).

    Carries the full :class:`SatisfactionResult` so the typed, named reason
    and the requirement/capability provenance survive a failed evaluation
    (Phase 6 plan, section 9, items 5 and 7).
    """

    def __init__(self, result: "SatisfactionResult") -> None:
        self.result = result
        reasons = ", ".join(r.value for r in result.reasons)
        super().__init__(
            f"data requirement {result.requirement.semantic_id!r} is "
            f"unsatisfiable: {reasons}"
        )


# ---------------------------------------------------------------------------
# Frozen vocabularies
# ---------------------------------------------------------------------------


class Frequency(str, Enum):
    """Observation frequency a requirement is declared at.

    This is the frequency the factor *consumes*, not a provider's native
    publishing cadence; the trusted boundary owns any resampling.
    """

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"


class ObservationPeriod(str, Enum):
    """Effective-time shape of an economic quantity (Phase 6 plan, section 5).

    ``INSTANT`` quantities apply at a point in effective time (e.g. a
    market capitalisation). ``PERIOD`` quantities accrue over an interval
    (e.g. a return or an earnings figure). This is a semantic declaration
    only; the trusted boundary decides how it is joined to observations.
    """

    INSTANT = "instant"
    PERIOD = "period"


class RevisionPolicy(str, Enum):
    """How revisions/restatements of a value are to be handled.

    A policy statement, never a vintage selection: choosing a concrete
    vintage remains the trusted boundary's job (Phase 6 plan, section 8).
    ``AS_FIRST_REPORTED`` requires a positive vintage-identity signal, so
    it can only be declared together with
    ``require_positive_vintage_identity=True``.
    """

    POINT_IN_TIME = "point_in_time"
    AS_FIRST_REPORTED = "as_first_reported"


class Unit(str, Enum):
    """Vendor-free unit of account for a semantic quantity.

    Deliberately coarse and economic, not vendor-specific. ``None`` on a
    requirement means "unit not declared"; a capability that cannot
    demonstrate a declared unit fails closed.
    """

    FRACTION = "fraction"
    RATIO = "ratio"
    CURRENCY = "currency"
    COUNT = "count"
    SHARES = "shares"


class UnsatisfactionReason(str, Enum):
    """Named, recordable reasons a capability fails to satisfy a requirement."""

    SEMANTIC_IDENTITY_MISMATCH = "semantic_identity_mismatch"
    FREQUENCY_MISMATCH = "frequency_mismatch"
    OBSERVATION_PERIOD_MISMATCH = "observation_period_mismatch"
    UNITS_MISMATCH = "units_mismatch"
    INSUFFICIENT_HISTORY = "insufficient_history"
    HISTORY_UNKNOWN = "history_unknown"
    KNOWLEDGE_DATE_UNAVAILABLE = "knowledge_date_unavailable"
    POSITIVE_VINTAGE_IDENTITY_UNAVAILABLE = "positive_vintage_identity_unavailable"
    REVISION_POLICY_UNSUPPORTED = "revision_policy_unsupported"


# ---------------------------------------------------------------------------
# Vendor-name rejection
# ---------------------------------------------------------------------------

#: Known data-vendor tokens. Matching is token/boundary aware, so common
#: words that merely contain a vendor name as a substring (e.g. ``"window"``
#: vs ``"wind"``) are not rejected.
KNOWN_VENDOR_NAMES: frozenset[str] = frozenset(
    {
        "tushare",
        "tiingo",
        "fred",
        "datahubco",
        "wind",
        "bloomberg",
        "refinitiv",
        "crsp",
        "compustat",
        "yahoo",
        "quandl",
        "akshare",
        "alphavantage",
        "alpha_vantage",
        "sharadar",
        "norgate",
        "eodhd",
        "baostock",
        "csmar",
        "joinquant",
        "ricequant",
        "eastmoney",
    }
)

# Insert a separator at lower/digit -> upper camelCase boundaries so that,
# e.g., ``"TushareDaily"`` is scanned as ``"tushare_daily"`` rather than as
# one unbroken token.
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _vendor_name_pattern(name: str) -> re.Pattern[str]:
    # A vendor token must stand on its own: not preceded/followed by another
    # alphanumeric character. This keeps "wind" out of "window".
    return re.compile(r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])")


_VENDOR_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, _vendor_name_pattern(name)) for name in sorted(KNOWN_VENDOR_NAMES)
)


def find_vendor_names(text: str) -> tuple[str, ...]:
    """Return the known vendor tokens appearing in ``text`` (sorted, unique).

    Scans both the raw lowercased text and a camelCase-split form, so a
    vendor name embedded in an identifier (``"tushare_close"``,
    ``"TushareClose"``) is found either way.
    """

    if not isinstance(text, str):
        raise RequirementValidationError(
            f"vendor scan requires a string, got {type(text).__name__}"
        )
    candidates = (text.lower(), _CAMEL_BOUNDARY.sub("_", text).lower())
    found = [
        name
        for name, pattern in _VENDOR_PATTERNS
        if any(pattern.search(candidate) for candidate in candidates)
    ]
    return tuple(found)


def assert_vendor_free(text: str, *, context: str = "value") -> str:
    """Return ``text`` unchanged if it names no known vendor, else raise.

    Raises :class:`VendorNameError` (a :class:`RequirementValidationError`)
    when a vendor token is present, so a vendor-named field can never enter
    a requirement.
    """

    found = find_vendor_names(text)
    if found:
        raise VendorNameError(
            f"{context} must not name a data vendor; found {list(found)} "
            f"in {text!r}"
        )
    return text


# ---------------------------------------------------------------------------
# Field validation helpers
# ---------------------------------------------------------------------------

_SEMANTIC_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.]*$")


def _validate_semantic_id(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise RequirementValidationError(
            f"{field_name} must be a string, got {type(value).__name__}"
        )
    if not value or value != value.strip():
        raise RequirementValidationError(
            f"{field_name} must be a non-empty identifier with no "
            f"surrounding whitespace, got {value!r}"
        )
    assert_vendor_free(value, context=field_name)
    if not _SEMANTIC_ID_RE.match(value):
        raise RequirementValidationError(
            f"{field_name} must match {_SEMANTIC_ID_RE.pattern!r} "
            f"(economic quantity, not a vendor column), got {value!r}"
        )
    return value


def _coerce_enum(value: Any, enum_cls: type[Enum], *, field_name: str) -> Any:
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str):
        try:
            return enum_cls(value)
        except ValueError:
            pass
    allowed = ", ".join(sorted(member.value for member in enum_cls))
    raise RequirementValidationError(
        f"{field_name} must be one of [{allowed}], got {value!r}"
    )


def _coerce_optional_enum(
    value: Any, enum_cls: type[Enum], *, field_name: str
) -> Any:
    if value is None:
        return None
    return _coerce_enum(value, enum_cls, field_name=field_name)


def _validate_int(
    value: Any, *, field_name: str, minimum: int = 0, allow_none: bool = False
) -> int | None:
    if value is None and allow_none:
        return None
    # ``bool`` is an ``int`` subclass; reject it so True/False can never be
    # silently read as 1/0.
    if isinstance(value, bool) or not isinstance(value, int):
        raise RequirementValidationError(
            f"{field_name} must be an integer, got {type(value).__name__}"
        )
    if value < minimum:
        raise RequirementValidationError(
            f"{field_name} must be >= {minimum}, got {value}"
        )
    return value


def _validate_bool(value: Any, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise RequirementValidationError(
            f"{field_name} must be a bool, got {type(value).__name__}"
        )
    return value


def _coerce_revision_policy_set(value: Any, *, field_name: str) -> frozenset:
    if value is None:
        return frozenset()
    if isinstance(value, (RevisionPolicy, str)):
        return frozenset({_coerce_enum(value, RevisionPolicy, field_name=field_name)})
    if isinstance(value, Iterable):
        return frozenset(
            _coerce_enum(item, RevisionPolicy, field_name=field_name)
            for item in value
        )
    raise RequirementValidationError(
        f"{field_name} must be a revision policy or an iterable of them, "
        f"got {type(value).__name__}"
    )


# ---------------------------------------------------------------------------
# The requirement contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DataRequirement:
    """A vendor-free, fail-closed declaration of what an input needs.

    Parameters
    ----------
    semantic_id:
        The economic quantity required (``"return"``, ``"market_cap"``,
        ``"turnover"``, ``"earnings"``, ...). Never a vendor column name and
        never a provider name.
    frequency:
        The observation frequency the factor consumes.
    observation_period:
        Whether the quantity is an ``INSTANT`` (a point in effective time)
        or a ``PERIOD`` (accrued over an interval).
    units:
        Optional declared unit of account. When declared, a capability must
        demonstrate the same unit or fail closed.
    lookback:
        Required history, in ``frequency`` periods, at or before the
        observation date (never future periods). ``0`` means no history
        requirement.
    revision_policy:
        How revisions/restatements must be handled. A policy statement, not
        a vintage choice.
    require_knowledge_date:
        Whether the value must be PIT-admissible (carry a usable
        knowledge/publication date). Defaults to ``True``; the trusted
        boundary owns the actual date resolution.
    require_positive_vintage_identity:
        Whether the value must carry a certified revision/vintage signal
        (Phase 6 plan, section 5). Defaults to ``False``.
    """

    semantic_id: str
    frequency: Frequency
    observation_period: ObservationPeriod
    units: Unit | None = None
    lookback: int = 0
    revision_policy: RevisionPolicy = RevisionPolicy.POINT_IN_TIME
    require_knowledge_date: bool = True
    require_positive_vintage_identity: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "semantic_id", _validate_semantic_id(self.semantic_id, field_name="semantic_id")
        )
        object.__setattr__(
            self,
            "frequency",
            _coerce_enum(self.frequency, Frequency, field_name="frequency"),
        )
        object.__setattr__(
            self,
            "observation_period",
            _coerce_enum(
                self.observation_period,
                ObservationPeriod,
                field_name="observation_period",
            ),
        )
        object.__setattr__(
            self,
            "units",
            _coerce_optional_enum(self.units, Unit, field_name="units"),
        )
        object.__setattr__(
            self, "lookback", _validate_int(self.lookback, field_name="lookback")
        )
        object.__setattr__(
            self,
            "revision_policy",
            _coerce_enum(
                self.revision_policy, RevisionPolicy, field_name="revision_policy"
            ),
        )
        object.__setattr__(
            self,
            "require_knowledge_date",
            _validate_bool(
                self.require_knowledge_date, field_name="require_knowledge_date"
            ),
        )
        object.__setattr__(
            self,
            "require_positive_vintage_identity",
            _validate_bool(
                self.require_positive_vintage_identity,
                field_name="require_positive_vintage_identity",
            ),
        )

        # Coherence invariants (fail closed at construction time).
        if (
            self.require_positive_vintage_identity
            and not self.require_knowledge_date
        ):
            raise RequirementValidationError(
                "a positive vintage-identity requirement implies PIT "
                "admissibility: require_knowledge_date must be True"
            )
        if (
            self.revision_policy is RevisionPolicy.AS_FIRST_REPORTED
            and not self.require_positive_vintage_identity
        ):
            raise RequirementValidationError(
                "revision_policy='as_first_reported' requires "
                "require_positive_vintage_identity=True (value semantics do "
                "not establish vintage semantics)"
            )

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe provenance record (deterministic key order)."""

        return {
            "semantic_id": self.semantic_id,
            "frequency": self.frequency.value,
            "observation_period": self.observation_period.value,
            "units": self.units.value if self.units is not None else None,
            "lookback": self.lookback,
            "revision_policy": self.revision_policy.value,
            "require_knowledge_date": self.require_knowledge_date,
            "require_positive_vintage_identity": (
                self.require_positive_vintage_identity
            ),
        }

    def check(self, capability: "DataCapability") -> "SatisfactionResult":
        """Return the full satisfaction result for ``capability``."""

        return check_satisfiable(self, capability)

    def is_satisfied_by(self, capability: "DataCapability") -> bool:
        """Whether ``capability`` satisfies this requirement."""

        return check_satisfiable(self, capability).satisfied


# ---------------------------------------------------------------------------
# What a provider can offer
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DataCapability:
    """A vendor-free statement of what a data source can offer.

    This is the adapter-side dual of :class:`DataRequirement`: it describes
    semantics, not provenance, and carries no provider name. A trusted
    adapter builds one and the boundary compares it to the declared
    requirement. Every field defaults to the fail-closed ("cannot
    demonstrate") value.
    """

    semantic_id: str
    frequency: Frequency
    observation_period: ObservationPeriod
    units: Unit | None = None
    history: int | None = None
    has_knowledge_date: bool = False
    has_positive_vintage_identity: bool = False
    revision_policies: frozenset[RevisionPolicy] = frozenset()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "semantic_id", _validate_semantic_id(self.semantic_id, field_name="semantic_id")
        )
        object.__setattr__(
            self,
            "frequency",
            _coerce_enum(self.frequency, Frequency, field_name="frequency"),
        )
        object.__setattr__(
            self,
            "observation_period",
            _coerce_enum(
                self.observation_period,
                ObservationPeriod,
                field_name="observation_period",
            ),
        )
        object.__setattr__(
            self,
            "units",
            _coerce_optional_enum(self.units, Unit, field_name="units"),
        )
        object.__setattr__(
            self,
            "history",
            _validate_int(
                self.history, field_name="history", allow_none=True
            ),
        )
        object.__setattr__(
            self,
            "has_knowledge_date",
            _validate_bool(self.has_knowledge_date, field_name="has_knowledge_date"),
        )
        object.__setattr__(
            self,
            "has_positive_vintage_identity",
            _validate_bool(
                self.has_positive_vintage_identity,
                field_name="has_positive_vintage_identity",
            ),
        )
        object.__setattr__(
            self,
            "revision_policies",
            _coerce_revision_policy_set(
                self.revision_policies, field_name="revision_policies"
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe provenance record (deterministic key order)."""

        return {
            "semantic_id": self.semantic_id,
            "frequency": self.frequency.value,
            "observation_period": self.observation_period.value,
            "units": self.units.value if self.units is not None else None,
            "history": self.history,
            "has_knowledge_date": self.has_knowledge_date,
            "has_positive_vintage_identity": self.has_positive_vintage_identity,
            "revision_policies": sorted(p.value for p in self.revision_policies),
        }


# ---------------------------------------------------------------------------
# Fail-closed satisfaction check
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SatisfactionResult:
    """A typed, recordable outcome of comparing a requirement to a capability.

    ``reasons`` is empty exactly when the requirement is satisfied. It is
    never silently emptied to represent satisfaction-by-substitution: any
    missing semantic property yields a named
    :class:`UnsatisfactionReason`.
    """

    requirement: DataRequirement
    capability: DataCapability
    reasons: tuple[UnsatisfactionReason, ...]

    @property
    def satisfied(self) -> bool:
        return not self.reasons

    @property
    def reason(self) -> UnsatisfactionReason | None:
        """The first named reason, or ``None`` when satisfied."""

        return self.reasons[0] if self.reasons else None

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe provenance record, emitted even (especially) on failure."""

        return {
            "satisfied": self.satisfied,
            "reasons": [r.value for r in self.reasons],
            "requirement": self.requirement.to_dict(),
            "capability": self.capability.to_dict(),
        }


def check_satisfiable(
    requirement: DataRequirement, capability: DataCapability
) -> SatisfactionResult:
    """Compare a capability against a requirement, fail-closed.

    Checks, in a fixed order: semantic identity, frequency, observation
    period, units, required history, knowledge/publication-date
    admissibility, positive vintage identity, and revision policy. Every
    failed check contributes its named reason; no property is inferred or
    substituted from another.
    """

    if not isinstance(requirement, DataRequirement):
        raise RequirementValidationError(
            f"requirement must be a DataRequirement, got {type(requirement).__name__}"
        )
    if not isinstance(capability, DataCapability):
        raise RequirementValidationError(
            f"capability must be a DataCapability, got {type(capability).__name__}"
        )

    reasons: list[UnsatisfactionReason] = []

    if capability.semantic_id != requirement.semantic_id:
        reasons.append(UnsatisfactionReason.SEMANTIC_IDENTITY_MISMATCH)

    if capability.frequency != requirement.frequency:
        reasons.append(UnsatisfactionReason.FREQUENCY_MISMATCH)

    if capability.observation_period != requirement.observation_period:
        reasons.append(UnsatisfactionReason.OBSERVATION_PERIOD_MISMATCH)

    if requirement.units is not None and capability.units != requirement.units:
        reasons.append(UnsatisfactionReason.UNITS_MISMATCH)

    if requirement.lookback > 0:
        if capability.history is None:
            # Unknown history is not evidence of sufficient history.
            reasons.append(UnsatisfactionReason.HISTORY_UNKNOWN)
        elif capability.history < requirement.lookback:
            reasons.append(UnsatisfactionReason.INSUFFICIENT_HISTORY)

    if requirement.require_knowledge_date and not capability.has_knowledge_date:
        reasons.append(UnsatisfactionReason.KNOWLEDGE_DATE_UNAVAILABLE)

    if (
        requirement.require_positive_vintage_identity
        and not capability.has_positive_vintage_identity
    ):
        reasons.append(UnsatisfactionReason.POSITIVE_VINTAGE_IDENTITY_UNAVAILABLE)

    if requirement.revision_policy not in capability.revision_policies:
        reasons.append(UnsatisfactionReason.REVISION_POLICY_UNSUPPORTED)

    return SatisfactionResult(
        requirement=requirement,
        capability=capability,
        reasons=tuple(reasons),
    )


def require_satisfiable(
    requirement: DataRequirement, capability: DataCapability
) -> SatisfactionResult:
    """Fail closed: return the result, or raise on any unsatisfied property.

    The raised :class:`RequirementUnsatisfiableError` carries the full
    :class:`SatisfactionResult`, so the named reason and the
    requirement/capability provenance remain recordable.
    """

    result = check_satisfiable(requirement, capability)
    if not result.satisfied:
        raise RequirementUnsatisfiableError(result)
    return result
