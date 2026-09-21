"""Constrained expression AST, parser, and validator (Phase 6, task P6-C).

This module is the *only* way a Phase 6 factor hypothesis may describe a
computation. It is deliberately tiny: it implements exactly the frozen
vocabulary of ``worker_tasks/phase6/phase6-plan.md`` section 6 and nothing
else. A factor expression is a small immutable tree, parsed from either a
nested mapping (the declarative/serialized form) or a compact textual form
(``"mean(turnover, 20) / mean(turnover, 120)"``), validated once, and then
executed by the trusted engine (P6-D/P6-E) over already-aligned PIT-safe
inputs.

Frozen vocabulary (section 6)
-----------------------------

===============================  ==========================  =====================
whitelisted operation            AST node                    mapping / text form
===============================  ==========================  =====================
raw field reference (by role)    :class:`Field`              ``{"op":"field","role":R}`` / ``R``
numeric constant                 :class:`Literal`            ``{"op":"const","value":V}`` / ``V``
arithmetic ``+ - * /``           :class:`Add` ... :class:`Div`  ``{"op":"add",...}`` / ``a + b``
ratio ``a / b``                  :class:`Div`                ``a / b``
difference ``a - b``             :class:`Sub`                ``a - b``
explicit lag-by-N (N >= 0)       :class:`Lag`                ``lag(x, N)``
rolling mean/sum/std/min/max     :class:`Rolling`            ``mean(x, N)``, ``std(x, N)``, ...
cross-sectional rank             :class:`CrossSectional`     ``rank(x)``
cross-sectional winsorize        :class:`CrossSectional`     ``winsorize(x, lo, hi)``
cross-sectional standardize      :class:`CrossSectional`     ``standardize(x)``
===============================  ==========================  =====================

There is no whitelist entry for anything else. Field roles are *semantic*
(the economic quantity, e.g. ``return``, ``market_cap``, ``turnover``),
never vendor names or vendor column names.

Not expressible (section 6 exclusions, enforced structurally)
-------------------------------------------------------------

* arbitrary Python / ``eval`` / ``exec`` / ``compile`` of user code --
  this module imports nothing but the standard library, never calls
  ``eval``/``exec``/``compile``/``__import__``, and never touches the
  filesystem, network, or environment (``tests/test_spec_expression.py``
  re-verifies this by inspecting this module's own AST);
* user-defined code or lambdas -- a node whose payload is a callable (or any
  object that is not one of the frozen node types) is rejected with
  :class:`UnsupportedOperationError`;
* provider access / network calls -- no import of any vendor package, and a
  field role that names a provider or a known vendor column is rejected with
  :class:`VendorReferenceError`;
* any direct knowledge-date or vintage selection -- reserved role tokens
  (``knowledge``, ``vintage``, ``latest``, ``asof``, ``restated``, ...) and
  reserved function/op names (``latest``, ``asof``, ``vintage``, ...) are
  rejected with :class:`TemporalSelectionError`. The trusted layer owns
  knowledge-date and vintage resolution;
* temporal alignment, formation lag, universe construction, return
  alignment -- ``align``/``merge``/``join``/``universe``/... are not
  whitelisted and are rejected with :class:`UnsupportedOperationError`;
* future / look-ahead references -- a negative lag, or ``shift``/``lead``/
  ``next``/``future``/... is rejected with :class:`LookaheadError`;
* unbounded recursion or arbitrary loops -- there is no loop or recursion
  construct in the grammar, the text parser has a fixed depth budget
  (:data:`MAX_DEPTH`), and the validator walks the tree iteratively, so a
  cyclic or over-deep structure is rejected
  (:class:`AmbiguousExpressionError`) instead of hanging.

Two additional fail-closed rules are enforced here because section 8 makes
them binding on the validator:

1. **Alignment order.** A temporal node (:class:`Lag` / :class:`Rolling`)
   may not consume the output of a cross-sectional node. The trusted layer
   performs temporal alignment; a spec that applies a cross-sectional
   transform and then rolls/lags it would be silently defining its own
   alignment pipeline. Cross-sectional transforms therefore apply last
   (outermost). Violations raise :class:`AlignmentOrderError`.
2. **Vintage-identity requirement.** When the caller supplies the declared
   requirement context, a role that is only certified together with a
   positive vintage signal is unusable unless the spec also declares the
   vintage-identity requirement for it
   (:class:`RequirementUnsatisfiableError`). Negative certification is
   reported as a typed failure, never as a silent empty result.

Totality and immutability (section 7)
-------------------------------------

Every node is a frozen dataclass whose ``__post_init__`` fully validates its
own payload, so a constructed node is always well-formed. Parsing therefore
yields either a valid :class:`Expr` or raises one of the typed errors below;
there is no "half-parsed" state and no partially built tree. ``children()``
always returns a tuple, and the validator additionally rejects any node type
that is not in the frozen set, so an expression is structurally unable to
smuggle executable behaviour.

Canonical form
--------------

:func:`to_dict` / :func:`from_dict` round-trip the declarative form, and
:func:`canonical_json` / :func:`expression_hash` provide the deterministic
canonical serialization/hash that later tasks hash into the ``FactorSpec``
content version.

Numeric/NaN semantics (frozen by section 7, implemented by P6-D/P6-E)
---------------------------------------------------------------------

Rolling windows are trailing windows ending at the observation date (never
centered, never future); division by zero yields NaN per the declared
``missing_policy`` rather than an exception; NaN propagation follows the
declared ``missing_policy``. Those are *execution* semantics; this module
only guarantees that the structures which express them are unambiguous.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, ClassVar

__all__ = [
    # typed error taxonomy
    "ExpressionError",
    "InvalidExpressionError",
    "UnsupportedOperationError",
    "VendorReferenceError",
    "TypeCheckError",
    "AlignmentOrderError",
    "AmbiguousExpressionError",
    "LookaheadError",
    "TemporalSelectionError",
    "RequirementUnsatisfiableError",
    "EvaluationError",
    # nodes
    "Expr",
    "Field",
    "Literal",
    "Add",
    "Sub",
    "Mul",
    "Div",
    "Lag",
    "Rolling",
    "RollingOp",
    "CrossSectional",
    "CrossSectionalOp",
    # constructors / helpers
    "field",
    "literal",
    "lag",
    "rolling_mean",
    "rolling_sum",
    "rolling_std",
    "rolling_min",
    "rolling_max",
    "rank",
    "winsorize",
    "standardize",
    "ratio",
    "difference",
    # parsing / validation
    "parse_expression",
    "validate_expression",
    "referenced_roles",
    "contains_cross_sectional",
    "contains_temporal",
    # canonical form
    "to_dict",
    "from_dict",
    "canonical_json",
    "expression_hash",
    # constants
    "MAX_DEPTH",
    "MAX_TEXT_LENGTH",
    "MAX_LAG_PERIODS",
    "MAX_ROLLING_WINDOW",
]


# --------------------------------------------------------------------------
# typed error taxonomy (section 7)
# --------------------------------------------------------------------------
class ExpressionError(Exception):
    """Base class for every typed error raised by the expression layer."""


class InvalidExpressionError(ExpressionError, ValueError):
    """The expression is malformed, ill-typed, or not totally defined."""


class UnsupportedOperationError(ExpressionError):
    """An operation outside the frozen section 6 whitelist was requested."""


class VendorReferenceError(InvalidExpressionError):
    """The expression names a provider or a vendor column instead of a role."""


class TypeCheckError(InvalidExpressionError):
    """An operand violates the numeric-only type discipline of the vocabulary."""


class AlignmentOrderError(InvalidExpressionError):
    """A cross-sectional transform is applied before temporal alignment."""


class AmbiguousExpressionError(InvalidExpressionError):
    """The structure is cyclic, over-deep, or otherwise not unambiguous."""


class LookaheadError(UnsupportedOperationError):
    """A future/next-period reference (negative lag, ``shift``, ...)."""


class TemporalSelectionError(UnsupportedOperationError):
    """The spec tries to choose a knowledge date or a publication vintage."""


class RequirementUnsatisfiableError(ExpressionError):
    """A declared requirement needed by the expression is not satisfied."""


class EvaluationError(ExpressionError):
    """Raised by the execution layer (P6-D/P6-E) when execution cannot proceed.

    The expression layer never raises this itself; it is defined here so the
    whole trusted-expression stack shares one error namespace.
    """


# --------------------------------------------------------------------------
# limits
# --------------------------------------------------------------------------
MAX_DEPTH = 32
"""Maximum nesting depth of a parsed/validated expression tree."""

MAX_TEXT_LENGTH = 4096
"""Maximum length of a textual expression, to bound parser work."""

_HARD_DEPTH_LIMIT = 4096
"""Absolute cap on a caller-supplied ``max_depth`` (bounds validator work)."""

MAX_LAG_PERIODS = 10_000
"""A sane upper bound on an explicit lag; keeps windows finite and auditable."""

MAX_ROLLING_WINDOW = 10_000
"""A sane upper bound on a rolling window (same rationale as ``MAX_LAG_PERIODS``)."""


# --------------------------------------------------------------------------
# frozen vocabulary enumerations
# --------------------------------------------------------------------------
class RollingOp(str, Enum):
    """The rolling (time-series) transforms whitelisted by section 6."""

    MEAN = "mean"
    SUM = "sum"
    STD = "std"
    MIN = "min"
    MAX = "max"


class CrossSectionalOp(str, Enum):
    """The per-date (cross-sectional) transforms whitelisted by section 6."""

    RANK = "rank"
    WINSORIZE = "winsorize"
    STANDARDIZE = "standardize"


_ROLLING_NAMES = frozenset(op.value for op in RollingOp)
_CROSS_SECTIONAL_NAMES = frozenset(op.value for op in CrossSectionalOp)


# --------------------------------------------------------------------------
# role / name safety nets
# --------------------------------------------------------------------------
_ROLE_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")

# Semantic roles are lowercase snake_case economic quantities. Provider names
# and vendor-specific column names are structurally distinguishable: they are
# either vendor-qualified (``tiingo.adjClose``, ``daily_basic:turnover_rate``),
# camelCase, or one of the reserved tokens below. The authoritative check is
# still "the role must be declared by the spec's ``inputs``"; this denylist is
# a secondary safety net so an undeclared vendor column fails even before the
# declared-role check runs.
_VENDOR_TOKENS = frozenset(
    {
        "tiingo",
        "tushare",
        "fred",
        "datahubco",
        "yahoo",
        "yfinance",
        "quandl",
        "bloomberg",
        "wind",
        "crsp",
        "compustat",
        "ibes",
        "eodhd",
        "alphavantage",
        "akshare",
        "baostock",
        "joinquant",
    }
)

_VENDOR_COLUMN_ROLES = frozenset(
    {
        "adj_close",
        "adjclose",
        "closeadj",
        "close_adj",
        "daily_basic",
        "fina_indicator",
        "balancesheet",
        "cashflow",
        "profit_dedt",
        "total_mv",
        "circ_mv",
        "turnover_rate",
        "turnover_rate_f",
        "pe_ttm",
    }
)

_LOOKAHEAD_TOKENS = frozenset(
    {
        "future",
        "next",
        "lead",
        "shift",
        "shifted",
        "lookahead",
        "fwd",
        "forward",
    }
)

_SELECTION_TOKENS = frozenset(
    {
        "latest",
        "vintage",
        "asof",
        "restated",
        "restatement",
        "revision",
        "knowledge",
        "provider",
        "vendor",
        "source",
        "pit",
        "now",
        "today",
        "current",
    }
)

# Function/op names that receive a precise typed rejection instead of the
# generic "not whitelisted" error. Every name here is *already* outside the
# whitelist; these sets only make the failure mode (and its named trap) clear.
_LOOKAHEAD_NAMES = frozenset(
    {
        "shift",
        "lead",
        "next",
        "future",
        "forward",
        "ffill",
        "bfill",
        "fill_forward",
        "next_value",
        "future_value",
    }
)
_TEMPORAL_SELECTION_NAMES = frozenset(
    {
        "latest",
        "asof",
        "as_of",
        "vintage",
        "restated",
        "revision",
        "knowledge_date",
        "point_in_time",
        "pit",
        "select_vintage",
        "select_knowledge_date",
    }
)
_ALIGNMENT_NAMES = frozenset(
    {
        "align",
        "reindex",
        "merge",
        "join",
        "concat",
        "universe",
        "screen",
        "formation",
        "formation_lag",
        "forward_return",
        "next_return",
        "return_align",
    }
)

_NUMERIC_TYPE_NAMES = frozenset(
    {"numeric", "number", "float", "float64", "int", "int64", "integer"}
)

# Exact role names (or any dunder-shaped role) that would look like an attempt
# to reach arbitrary Python instead of a semantic field reference. These are
# never economic quantities, so rejecting them is purely fail-closed.
_CODE_NAMES = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "lambda",
        "import",
        "globals",
        "locals",
        "vars",
        "getattr",
        "setattr",
        "delattr",
        "open",
        "input",
        "__import__",
        "__builtins__",
        "builtins",
        "subprocess",
        "os",
        "sys",
    }
)


# --------------------------------------------------------------------------
# AST nodes (immutable, total)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Expr:
    """Immutable base class of every whitelisted expression node."""

    KIND: ClassVar[str] = "expr"

    def children(self) -> tuple["Expr", ...]:
        """Ordered child nodes (empty for leaves). Always a tuple."""
        return ()

    def to_dict(self) -> dict[str, Any]:
        """Canonical declarative form of this node (round-trips)."""
        raise NotImplementedError(  # pragma: no cover - abstract
            "only frozen concrete node types have a canonical form"
        )

    @property
    def kind(self) -> str:
        """Canonical kind name used in errors, mappings, and hashing."""
        return type(self).KIND

    @property
    def is_cross_sectional(self) -> bool:
        """True iff this single node is a per-date (cross-sectional) op."""
        return isinstance(self, CrossSectional)

    @property
    def is_temporal(self) -> bool:
        """True iff this single node is a lag/rolling (time-series) op."""
        return isinstance(self, (Lag, Rolling))


@dataclass(frozen=True)
class Field(Expr):
    """A raw field reference by *semantic role* (never a vendor column)."""

    KIND: ClassVar[str] = "field"

    role: str

    def __post_init__(self) -> None:
        _validate_role(self.role)

    def to_dict(self) -> dict[str, Any]:
        return {"op": "field", "role": self.role}


@dataclass(frozen=True)
class Literal(Expr):
    """A numeric constant. Non-finite values are rejected as non-total."""

    KIND: ClassVar[str] = "const"

    value: float

    def __post_init__(self) -> None:
        raw = self.value
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise InvalidExpressionError(
                f"numeric constant must be a real number, got {type(raw).__name__}"
            )
        value = float(raw)
        if not math.isfinite(value):
            raise InvalidExpressionError(
                f"numeric constant must be finite, got {raw!r}"
            )
        # Normalize so that Literal(1) and Literal(1.0) are the same node and
        # -0.0 canonicalizes to 0.0 (deterministic hashing).
        object.__setattr__(self, "value", value + 0.0)

    def to_dict(self) -> dict[str, Any]:
        return {"op": "const", "value": self.value}


@dataclass(frozen=True)
class _Binary(Expr):
    """Internal base for the whitelisted arithmetic operators."""

    KIND: ClassVar[str] = "binary"

    left: Expr
    right: Expr

    def __post_init__(self) -> None:
        _check_child(self.left, "left operand")
        _check_child(self.right, "right operand")

    def children(self) -> tuple[Expr, ...]:
        return (self.left, self.right)

    def to_dict(self) -> dict[str, Any]:
        return {
            "op": self.KIND,
            "left": self.left.to_dict(),
            "right": self.right.to_dict(),
        }


@dataclass(frozen=True)
class Add(_Binary):
    """Arithmetic ``left + right``."""

    KIND: ClassVar[str] = "add"


@dataclass(frozen=True)
class Sub(_Binary):
    """Arithmetic ``left - right`` (the whitelisted "difference")."""

    KIND: ClassVar[str] = "sub"


@dataclass(frozen=True)
class Mul(_Binary):
    """Arithmetic ``left * right``."""

    KIND: ClassVar[str] = "mul"


@dataclass(frozen=True)
class Div(_Binary):
    """Arithmetic ``left / right`` (the whitelisted "ratio")."""

    KIND: ClassVar[str] = "div"


@dataclass(frozen=True)
class Lag(Expr):
    """Explicit lag-by-N over observation dates, with ``N >= 0``.

    ``N == 0`` is the identity. ``N < 0`` is a future reference and is
    rejected with :class:`LookaheadError` at construction time, so no
    look-ahead node can exist at all.
    """

    KIND: ClassVar[str] = "lag"

    operand: Expr
    periods: int

    def __post_init__(self) -> None:
        _check_child(self.operand, "lag operand")
        periods = self.periods
        if isinstance(periods, bool) or not isinstance(periods, int):
            raise InvalidExpressionError(
                f"lag periods must be an integer, got {type(periods).__name__}"
            )
        if periods < 0:
            raise LookaheadError(
                "lag periods must be non-negative (section 6); a negative lag "
                "is a future/look-ahead reference and is not expressible"
            )
        if periods > MAX_LAG_PERIODS:
            raise InvalidExpressionError(
                f"lag periods must be <= {MAX_LAG_PERIODS}, got {periods}"
            )
        object.__setattr__(self, "periods", int(periods))

    def children(self) -> tuple[Expr, ...]:
        return (self.operand,)

    def to_dict(self) -> dict[str, Any]:
        return {
            "op": "lag",
            "operand": self.operand.to_dict(),
            "periods": self.periods,
        }


@dataclass(frozen=True)
class Rolling(Expr):
    """A trailing rolling transform over ``window`` periods.

    The window ends at the observation date (never centered, never future);
    the numeric details are P6-E's contract. ``window`` must be an integer
    ``>= 1``.
    """

    KIND: ClassVar[str] = "rolling"

    op: RollingOp
    operand: Expr
    window: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "op", _coerce_enum(RollingOp, self.op, "rolling"))
        _check_child(self.operand, "rolling operand")
        window = self.window
        if isinstance(window, bool) or not isinstance(window, int):
            raise InvalidExpressionError(
                f"rolling window must be an integer, got {type(window).__name__}"
            )
        if window < 1:
            raise InvalidExpressionError(
                f"rolling window must be >= 1, got {window}"
            )
        if window > MAX_ROLLING_WINDOW:
            raise InvalidExpressionError(
                f"rolling window must be <= {MAX_ROLLING_WINDOW}, got {window}"
            )
        object.__setattr__(self, "window", int(window))

    def children(self) -> tuple[Expr, ...]:
        return (self.operand,)

    def to_dict(self) -> dict[str, Any]:
        return {
            "op": "rolling",
            "fn": self.op.value,
            "operand": self.operand.to_dict(),
            "window": self.window,
        }


@dataclass(frozen=True)
class CrossSectional(Expr):
    """A per-date (cross-sectional) transform over the aligned panel.

    ``bounds`` is required exactly for ``winsorize`` (explicit quantile
    bounds, ``0 <= lower < upper <= 1``) and forbidden for the others.
    """

    KIND: ClassVar[str] = "cross_section"

    op: CrossSectionalOp
    operand: Expr
    bounds: tuple[float, float] | None = None

    def __post_init__(self) -> None:
        op = _coerce_enum(CrossSectionalOp, self.op, "cross-sectional")
        object.__setattr__(self, "op", op)
        _check_child(self.operand, "cross-sectional operand")

        bounds = self.bounds
        if op is CrossSectionalOp.WINSORIZE:
            if bounds is None:
                raise InvalidExpressionError(
                    "winsorize requires explicit (lower, upper) quantile "
                    "bounds; a silent default would be an unfrozen semantic"
                )
            if not isinstance(bounds, (tuple, list)) or len(bounds) != 2:
                raise InvalidExpressionError(
                    "winsorize bounds must be a (lower, upper) pair, got "
                    f"{bounds!r}"
                )
            lower = _finite_float(bounds[0], "winsorize lower bound")
            upper = _finite_float(bounds[1], "winsorize upper bound")
            if not 0.0 <= lower < upper <= 1.0:
                raise InvalidExpressionError(
                    "winsorize bounds must satisfy 0 <= lower < upper <= 1, "
                    f"got ({lower}, {upper})"
                )
            object.__setattr__(self, "bounds", (lower, upper))
        elif bounds is not None:
            raise InvalidExpressionError(
                f"{op.value} does not accept bounds; got {bounds!r}"
            )

    def children(self) -> tuple[Expr, ...]:
        return (self.operand,)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "op": "cross_section",
            "fn": self.op.value,
            "operand": self.operand.to_dict(),
        }
        if self.op is CrossSectionalOp.WINSORIZE:
            bounds = self.bounds
            if bounds is None:  # pragma: no cover - guarded by __post_init__
                raise InvalidExpressionError(
                    "winsorize node is missing its explicit bounds"
                )
            payload["lower"], payload["upper"] = bounds
        return payload


_NODE_TYPES = frozenset(
    {
        Field,
        Literal,
        Add,
        Sub,
        Mul,
        Div,
        Lag,
        Rolling,
        CrossSectional,
    }
)


# --------------------------------------------------------------------------
# small construction guards
# --------------------------------------------------------------------------
def _coerce_enum(enum_cls: type[Enum], value: Any, family: str) -> Any:
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str):
        try:
            return enum_cls(value)
        except ValueError as exc:
            raise UnsupportedOperationError(
                f"{family} operation {value!r} is not in the frozen section 6 "
                f"whitelist (allowed: {sorted(m.value for m in enum_cls)})"
            ) from exc
    raise UnsupportedOperationError(
        f"{family} operation must be a string or {enum_cls.__name__}, got "
        f"{type(value).__name__}"
    )


def _check_child(value: Any, position: str) -> None:
    if callable(value) and not isinstance(value, Expr):
        raise UnsupportedOperationError(
            f"{position} is a callable; user-defined executable code is not "
            "expressible in a Phase 6 factor expression"
        )
    if not isinstance(value, Expr):
        raise UnsupportedOperationError(
            f"{position} must be an expression node, got {type(value).__name__}"
        )
    if type(value) not in _NODE_TYPES:
        raise UnsupportedOperationError(
            f"{position} has unsupported node type {type(value).__name__}; "
            "only the frozen section 6 node types are allowed"
        )


def _finite_float(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidExpressionError(
            f"{what} must be a real number, got {type(value).__name__}"
        )
    number = float(value)
    if not math.isfinite(number):
        raise InvalidExpressionError(f"{what} must be finite, got {value!r}")
    return number


def _validate_role(role: Any) -> None:
    if not isinstance(role, str):
        raise InvalidExpressionError(
            f"field role must be a string, got {type(role).__name__}"
        )
    if role in _CODE_NAMES or role.startswith("__"):
        raise UnsupportedOperationError(
            f"field role {role!r} looks like a Python/code reference; a factor "
            "expression may only reference a declared semantic input role"
        )
    if not _ROLE_RE.match(role):
        raise VendorReferenceError(
            f"field role {role!r} is not a semantic role token; roles are "
            "lowercase snake_case economic quantities (e.g. 'return', "
            "'market_cap'), never vendor-qualified or camelCase columns"
        )
    tokens = set(role.split("_"))
    vendor_hits = sorted(tokens & _VENDOR_TOKENS)
    if vendor_hits or role in _VENDOR_COLUMN_ROLES:
        raise VendorReferenceError(
            f"field role {role!r} names a provider/vendor column "
            f"({vendor_hits or [role]}); a FactorSpec is vendor-free and may "
            "only reference semantic roles"
        )
    lookahead_hits = sorted(tokens & _LOOKAHEAD_TOKENS)
    if lookahead_hits:
        raise LookaheadError(
            f"field role {role!r} names a future/next-period reference "
            f"({lookahead_hits}); look-ahead is not expressible (section 6)"
        )
    selection_hits = sorted(tokens & _SELECTION_TOKENS)
    if selection_hits:
        raise TemporalSelectionError(
            f"field role {role!r} attempts to select a knowledge date or "
            f"vintage ({selection_hits}); the trusted layer owns that choice"
        )


# --------------------------------------------------------------------------
# convenience constructors (thin, so the AST stays the single definition)
# --------------------------------------------------------------------------
def field(role: str) -> Field:
    """Raw field reference by semantic role."""
    return Field(role)


def literal(value: float) -> Literal:
    """Numeric constant."""
    return Literal(value)


def lag(operand: Expr, periods: int) -> Lag:
    """Explicit lag-by-N (``N >= 0``)."""
    return Lag(operand, periods)


def rolling_mean(operand: Expr, window: int) -> Rolling:
    """Trailing rolling mean over ``window`` periods."""
    return Rolling(RollingOp.MEAN, operand, window)


def rolling_sum(operand: Expr, window: int) -> Rolling:
    """Trailing rolling sum over ``window`` periods."""
    return Rolling(RollingOp.SUM, operand, window)


def rolling_std(operand: Expr, window: int) -> Rolling:
    """Trailing rolling standard deviation over ``window`` periods."""
    return Rolling(RollingOp.STD, operand, window)


def rolling_min(operand: Expr, window: int) -> Rolling:
    """Trailing rolling minimum over ``window`` periods."""
    return Rolling(RollingOp.MIN, operand, window)


def rolling_max(operand: Expr, window: int) -> Rolling:
    """Trailing rolling maximum over ``window`` periods."""
    return Rolling(RollingOp.MAX, operand, window)


def rank(operand: Expr) -> CrossSectional:
    """Per-date cross-sectional rank."""
    return CrossSectional(CrossSectionalOp.RANK, operand)


def winsorize(operand: Expr, lower: float, upper: float) -> CrossSectional:
    """Per-date cross-sectional winsorization at explicit quantile bounds."""
    return CrossSectional(CrossSectionalOp.WINSORIZE, operand, (lower, upper))


def standardize(operand: Expr) -> CrossSectional:
    """Per-date cross-sectional standardization."""
    return CrossSectional(CrossSectionalOp.STANDARDIZE, operand)


def ratio(numerator: Expr, denominator: Expr) -> Div:
    """Whitelisted ratio ``numerator / denominator``."""
    return Div(numerator, denominator)


def difference(left: Expr, right: Expr) -> Sub:
    """Whitelisted difference ``left - right``."""
    return Sub(left, right)


# --------------------------------------------------------------------------
# structural validation (context-free) and requirement checks
# --------------------------------------------------------------------------
def _checked_max_depth(max_depth: Any) -> int:
    if isinstance(max_depth, bool) or not isinstance(max_depth, int):
        raise InvalidExpressionError(
            f"max_depth must be an integer, got {type(max_depth).__name__}"
        )
    if max_depth < 1 or max_depth > _HARD_DEPTH_LIMIT:
        raise InvalidExpressionError(
            f"max_depth must be between 1 and {_HARD_DEPTH_LIMIT}, got {max_depth}"
        )
    return max_depth


def _validate_field_context(
    role: str,
    *,
    allowed_roles: frozenset[str] | None,
    role_types: Mapping[str, Any] | None,
    role_frequencies: Mapping[str, Any] | None,
    vintage_certified_roles: frozenset[str],
    vintage_identity_roles: frozenset[str],
) -> None:
    if allowed_roles is not None and role not in allowed_roles:
        raise RequirementUnsatisfiableError(
            f"expression references input role {role!r} which the spec does "
            "not declare in its inputs/data_requirements (fail closed)"
        )
    if role_types is not None:
        if role not in role_types:
            raise RequirementUnsatisfiableError(
                f"no declared type for input role {role!r}; a bound value "
                "field must carry a declared semantic requirement"
            )
        declared = str(role_types[role]).strip().lower()
        if declared not in _NUMERIC_TYPE_NAMES:
            raise TypeCheckError(
                f"input role {role!r} is declared {declared!r}; every frozen "
                "section 6 transform is numeric-only"
            )
    if role_frequencies is not None and role not in role_frequencies:
        raise RequirementUnsatisfiableError(
            f"no declared frequency for input role {role!r}; the spec must "
            "declare the observation frequency it consumes"
        )
    if role in vintage_certified_roles and role not in vintage_identity_roles:
        raise RequirementUnsatisfiableError(
            f"input role {role!r} is only certified together with a positive "
            "vintage-identity signal, but the spec declares no vintage-identity "
            "requirement for it (negative certification must be typed, not "
            "silently absorbed)"
        )


def validate_expression(
    expr: Expr,
    *,
    allowed_roles: Collection[str] | None = None,
    role_types: Mapping[str, Any] | None = None,
    role_frequencies: Mapping[str, Any] | None = None,
    vintage_certified_roles: Collection[str] = (),
    vintage_identity_roles: Collection[str] = (),
    max_depth: int = MAX_DEPTH,
) -> Expr:
    """Validate an expression tree; return it unchanged or raise a typed error.

    Structural checks always run: every node must be one of the frozen node
    types, the tree must be acyclic and within ``max_depth``, and temporal
    nodes must not consume cross-sectional subtrees (section 8 alignment
    trap).

    The remaining checks are *declared-context* checks and run only when the
    caller supplies the context (which the FactorSpec/data-requirement layer
    owns):

    ``allowed_roles``
        Roles the spec declares in ``inputs``. Referencing anything else is a
        :class:`RequirementUnsatisfiableError` (fail closed).
    ``role_types``
        Declared semantic type per role; anything non-numeric is a
        :class:`TypeCheckError`, and a missing declaration is unsatisfiable.
    ``role_frequencies``
        Declared observation frequency per role; a missing declaration is
        unsatisfiable.
    ``vintage_certified_roles`` / ``vintage_identity_roles``
        Roles that are only usable with a certified positive-vintage signal,
        and the roles for which the spec has declared that requirement.
        Using a certified-vintage role without the declared requirement is a
        :class:`RequirementUnsatisfiableError`.
    """
    if not isinstance(expr, Expr) or type(expr) not in _NODE_TYPES:
        raise UnsupportedOperationError(
            "expression must be one of the frozen section 6 node types, got "
            f"{type(expr).__name__}"
        )
    max_depth = _checked_max_depth(max_depth)
    allowed = None if allowed_roles is None else frozenset(allowed_roles)
    certified_vintage = frozenset(vintage_certified_roles)
    declared_vintage = frozenset(vintage_identity_roles)

    # Iterative post-order walk: no Python recursion, so an over-deep or
    # cyclic structure is a typed error instead of a RecursionError/hang.
    contains_xs: dict[int, bool] = {}
    path: set[int] = set()
    stack: list[tuple[str, Expr, int]] = [("enter", expr, 0)]
    while stack:
        action, node, depth = stack.pop()
        if action == "exit":
            path.discard(id(node))
            child_has_xs = any(contains_xs[id(child)] for child in node.children())
            if isinstance(node, (Lag, Rolling)) and child_has_xs:
                raise AlignmentOrderError(
                    f"{node.kind} consumes a cross-sectional subtree: a "
                    "cross-sectional transform may not be applied before "
                    "temporal alignment (section 8); apply cross-sectional "
                    "transforms last, outside time-series ops"
                )
            contains_xs[id(node)] = child_has_xs or isinstance(node, CrossSectional)
            continue

        if type(node) not in _NODE_TYPES:
            raise UnsupportedOperationError(
                f"unsupported node type {type(node).__name__}; only the frozen "
                "section 6 node types are allowed"
            )
        if id(node) in path:
            raise AmbiguousExpressionError(
                "expression tree is cyclic (a node is its own ancestor); "
                "unbounded recursion is not expressible"
            )
        if depth > max_depth:
            raise InvalidExpressionError(
                f"expression nesting depth {depth} exceeds max_depth "
                f"{max_depth}; unbounded recursion is not expressible"
            )
        path.add(id(node))

        if isinstance(node, Field):
            _validate_field_context(
                node.role,
                allowed_roles=allowed,
                role_types=role_types,
                role_frequencies=role_frequencies,
                vintage_certified_roles=certified_vintage,
                vintage_identity_roles=declared_vintage,
            )

        stack.append(("exit", node, depth))
        for child in node.children():
            stack.append(("enter", child, depth + 1))

    return expr


# --------------------------------------------------------------------------
# mapping (declarative) form
# --------------------------------------------------------------------------
_MAPPING_OPTIONAL_KEYS: dict[str, frozenset[str]] = {
    "field": frozenset(),
    "const": frozenset(),
    "add": frozenset(),
    "sub": frozenset(),
    "mul": frozenset(),
    "div": frozenset(),
    "lag": frozenset(),
    "rolling": frozenset(),
    "cross_section": frozenset({"lower", "upper"}),
}

_MAPPING_REQUIRED_KEYS: dict[str, frozenset[str]] = {
    "field": frozenset({"op", "role"}),
    "const": frozenset({"op", "value"}),
    "add": frozenset({"op", "left", "right"}),
    "sub": frozenset({"op", "left", "right"}),
    "mul": frozenset({"op", "left", "right"}),
    "div": frozenset({"op", "left", "right"}),
    "lag": frozenset({"op", "operand", "periods"}),
    "rolling": frozenset({"op", "fn", "operand", "window"}),
    "cross_section": frozenset({"op", "fn", "operand"}),
}


def _check_mapping_keys(
    payload: Mapping[str, Any],
    op: str,
    *,
    winsorize: bool = False,
) -> None:
    required = _MAPPING_REQUIRED_KEYS[op]
    optional = _MAPPING_OPTIONAL_KEYS[op]
    if winsorize:
        required = required | {"lower", "upper"}
        optional = frozenset()
    keys = set(payload)
    missing = sorted(map(repr, required - keys))
    extra = sorted(map(repr, keys - required - optional))
    if missing:
        raise InvalidExpressionError(
            f"{op!r} mapping is missing required keys {missing}"
        )
    if extra:
        raise InvalidExpressionError(
            f"{op!r} mapping has unsupported keys {extra}; the declarative "
            "expression form is closed to unwhitelisted parameters"
        )


def _reject_known_bad_name(name: str, context: str) -> None:
    """Raise the precise typed error for known non-whitelisted names."""
    if name in _CODE_NAMES or name.startswith("__"):
        raise UnsupportedOperationError(
            f"{context} {name!r} looks like a Python/code reference; a factor "
            "expression may only use frozen semantic operations"
        )
    if name in _LOOKAHEAD_NAMES:
        raise LookaheadError(
            f"{context} {name!r} is a future/next-period reference and is not "
            "expressible (section 6); only non-negative explicit lag-by-N is"
        )
    if name in _TEMPORAL_SELECTION_NAMES:
        raise TemporalSelectionError(
            f"{context} {name!r} would select a knowledge date or vintage "
            "inside the spec; the trusted layer owns that choice (section 8)"
        )
    if name in _ALIGNMENT_NAMES:
        raise UnsupportedOperationError(
            f"{context} {name!r} would perform temporal alignment / universe "
            "construction / return alignment inside the spec; the trusted "
            "infrastructure owns that (section 8)"
        )


def _from_mapping(payload: Any, depth: int) -> Expr:
    if depth > MAX_DEPTH:
        raise InvalidExpressionError(
            f"expression nesting depth exceeds MAX_DEPTH ({MAX_DEPTH})"
        )
    if not isinstance(payload, Mapping):
        raise InvalidExpressionError(
            "declarative expression nodes must be mappings, got "
            f"{type(payload).__name__}"
        )
    op = payload.get("op")
    if not isinstance(op, str):
        raise InvalidExpressionError(
            "every expression mapping requires a string 'op' key"
        )
    _reject_known_bad_name(op, "operation")

    if op == "field":
        _check_mapping_keys(payload, op)
        return Field(payload["role"])
    if op == "const":
        _check_mapping_keys(payload, op)
        return Literal(payload["value"])
    if op in ("add", "sub", "mul", "div"):
        _check_mapping_keys(payload, op)
        left = _from_mapping(payload["left"], depth + 1)
        right = _from_mapping(payload["right"], depth + 1)
        node_cls = {"add": Add, "sub": Sub, "mul": Mul, "div": Div}[op]
        return node_cls(left, right)
    if op == "lag":
        _check_mapping_keys(payload, op)
        operand = _from_mapping(payload["operand"], depth + 1)
        return Lag(operand, payload["periods"])
    if op == "rolling":
        _check_mapping_keys(payload, op)
        fn = payload["fn"]
        if not isinstance(fn, str) or fn not in _ROLLING_NAMES:
            _reject_known_bad_name(str(fn), "rolling operation")
            raise UnsupportedOperationError(
                f"{fn!r} is not a whitelisted rolling transform; allowed "
                f"rolling transforms are {sorted(_ROLLING_NAMES)}"
            )
        operand = _from_mapping(payload["operand"], depth + 1)
        return Rolling(RollingOp(fn), operand, payload["window"])
    if op == "cross_section":
        fn = payload.get("fn")
        if fn == "winsorize":
            _check_mapping_keys(payload, op, winsorize=True)
            operand = _from_mapping(payload["operand"], depth + 1)
            return CrossSectional(
                CrossSectionalOp.WINSORIZE,
                operand,
                (payload["lower"], payload["upper"]),
            )
        _check_mapping_keys(payload, op)
        if not isinstance(fn, str) or fn not in _CROSS_SECTIONAL_NAMES:
            _reject_known_bad_name(str(fn), "cross-sectional operation")
            raise UnsupportedOperationError(
                f"{fn!r} is not a whitelisted cross-sectional transform; "
                f"allowed transforms are {sorted(_CROSS_SECTIONAL_NAMES)}"
            )
        operand = _from_mapping(payload["operand"], depth + 1)
        return CrossSectional(CrossSectionalOp(fn), operand)

    _reject_known_bad_name(op, "operation")
    raise UnsupportedOperationError(
        f"operation {op!r} is not in the frozen section 6 whitelist "
        "(field, const, add, sub, mul, div, lag, rolling, cross_section)"
    )


# --------------------------------------------------------------------------
# textual form (recursive descent with a fixed depth budget)
# --------------------------------------------------------------------------
_TOKEN_RE = re.compile(
    r"""
    (?P<space>\s+)
  | (?P<number>(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)
  | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op>[+\-*/(),])
    """,
    re.VERBOSE,
)

_CALL_NAMES = frozenset({"lag", "rank", "standardize", "winsorize"}) | _ROLLING_NAMES


def _tokenize(text: str) -> list[tuple[str, str]]:
    if len(text) > MAX_TEXT_LENGTH:
        raise InvalidExpressionError(
            f"expression text exceeds MAX_TEXT_LENGTH ({MAX_TEXT_LENGTH})"
        )
    tokens: list[tuple[str, str]] = []
    pos = 0
    while pos < len(text):
        match = _TOKEN_RE.match(text, pos)
        if match is None:
            raise InvalidExpressionError(
                f"unexpected character {text[pos]!r} at position {pos}; only "
                "whitelisted arithmetic, identifiers, numbers, parentheses and "
                "commas are allowed"
            )
        pos = match.end()
        kind = match.lastgroup
        if kind == "space":
            continue
        tokens.append(("number" if kind == "number" else kind, match.group()))
    return tokens


def _arity(name: str, args: list[Expr], expected: int) -> None:
    if len(args) != expected:
        raise InvalidExpressionError(
            f"{name}() takes exactly {expected} argument(s), got {len(args)}"
        )


def _int_constant(node: Expr, name: str, position: str) -> int:
    if not isinstance(node, Literal) or node.value != int(node.value):
        raise InvalidExpressionError(
            f"{name}() {position} must be an integer literal"
        )
    return int(node.value)


def _float_constant(node: Expr, name: str, position: str) -> float:
    if not isinstance(node, Literal):
        raise InvalidExpressionError(
            f"{name}() {position} must be a numeric literal"
        )
    return node.value


def _build_call(name: str, args: list[Expr]) -> Expr:
    if name not in _CALL_NAMES:
        _reject_known_bad_name(name, "function")
        raise UnsupportedOperationError(
            f"function {name!r} is not in the frozen section 6 whitelist "
            f"(allowed: {sorted(_CALL_NAMES)})"
        )
    if name == "lag":
        _arity(name, args, 2)
        return Lag(args[0], _int_constant(args[1], name, "periods"))
    if name in _ROLLING_NAMES:
        _arity(name, args, 2)
        return Rolling(
            RollingOp(name), args[0], _int_constant(args[1], name, "window")
        )
    if name == "rank":
        _arity(name, args, 1)
        return CrossSectional(CrossSectionalOp.RANK, args[0])
    if name == "standardize":
        _arity(name, args, 1)
        return CrossSectional(CrossSectionalOp.STANDARDIZE, args[0])
    _arity(name, args, 3)
    return CrossSectional(
        CrossSectionalOp.WINSORIZE,
        args[0],
        (
            _float_constant(args[1], name, "lower bound"),
            _float_constant(args[2], name, "upper bound"),
        ),
    )


class _TextParser:
    """Recursive-descent parser for the frozen text grammar.

    ``expr := term (('+' | '-') term)*``
    ``term := factor (('*' | '/') factor)*``
    ``factor := ('+' | '-')? primary``  (unary only on numeric constants)
    ``primary := NUMBER | IDENT | IDENT '(' args ')' | '(' expr ')'``
    ``args := expr (',' expr)*``

    There is no loop keyword, no assignment, no attribute/index access, no
    string literal, and no user-defined call; identifiers followed by ``(``
    must name a whitelisted function.
    """

    def __init__(self, tokens: list[tuple[str, str]]) -> None:
        self._tokens = tokens
        self._pos = 0
        self._depth = 0

    # -- token helpers ----------------------------------------------------
    def _peek(self) -> tuple[str, str]:
        if self._pos >= len(self._tokens):
            return ("eof", "")
        return self._tokens[self._pos]

    def _peek_kind(self) -> str:
        return self._peek()[0]

    def _peek_op(self) -> str | None:
        kind, value = self._peek()
        return value if kind == "op" else None

    def _advance(self) -> tuple[str, str]:
        token = self._peek()
        if token[0] == "eof":
            raise InvalidExpressionError("unexpected end of expression")
        self._pos += 1
        return token

    def _expect_op(self, expected: str) -> None:
        kind, value = self._peek()
        if kind != "op" or value != expected:
            raise InvalidExpressionError(
                f"expected {expected!r} but found "
                f"{(value if kind != 'eof' else 'end of expression')!r}"
            )
        self._pos += 1

    def _enter(self) -> None:
        self._depth += 1
        if self._depth > MAX_DEPTH:
            raise InvalidExpressionError(
                f"expression nesting depth exceeds MAX_DEPTH ({MAX_DEPTH}); "
                "unbounded recursion is not expressible"
            )

    def _leave(self) -> None:
        self._depth -= 1

    # -- grammar ----------------------------------------------------------
    def parse(self) -> Expr:
        if self._peek_kind() == "eof":
            raise InvalidExpressionError("expression is empty")
        node = self._parse_expr()
        if self._peek_kind() != "eof":
            _kind, value = self._peek()
            raise InvalidExpressionError(
                f"unexpected trailing token {value!r}; the expression must be "
                "a single whitelisted expression"
            )
        return node

    def _parse_expr(self) -> Expr:
        node = self._parse_term()
        while self._peek_op() in ("+", "-"):
            op = self._advance()[1]
            right = self._parse_term()
            node = Add(node, right) if op == "+" else Sub(node, right)
        return node

    def _parse_term(self) -> Expr:
        node = self._parse_factor()
        while self._peek_op() in ("*", "/"):
            op = self._advance()[1]
            right = self._parse_factor()
            node = Mul(node, right) if op == "*" else Div(node, right)
        return node

    def _parse_factor(self) -> Expr:
        if self._peek_op() in ("+", "-"):
            sign = self._advance()[1]
            operand = self._parse_primary()
            if not isinstance(operand, Literal):
                raise InvalidExpressionError(
                    "unary '+'/'-' is not whitelisted except directly on a "
                    "numeric constant; write '0 - x' for negation"
                )
            return operand if sign == "+" else Literal(-operand.value)
        return self._parse_primary()

    def _parse_primary(self) -> Expr:
        kind, value = self._advance()
        if kind == "number":
            return Literal(float(value))
        if kind == "ident":
            if self._peek_op() == "(":
                return self._parse_call(value)
            return Field(value)
        if kind == "op" and value == "(":
            self._enter()
            node = self._parse_expr()
            self._expect_op(")")
            self._leave()
            return node
        raise InvalidExpressionError(
            f"unexpected token {value!r} where a field, constant, or "
            "parenthesized expression was expected"
        )

    def _parse_call(self, name: str) -> Expr:
        self._enter()
        self._expect_op("(")
        args: list[Expr] = []
        if self._peek_op() != ")":
            args.append(self._parse_expr())
            while self._peek_op() == ",":
                self._advance()
                args.append(self._parse_expr())
        self._expect_op(")")
        self._leave()
        return _build_call(name, args)


# --------------------------------------------------------------------------
# public parsing entry point
# --------------------------------------------------------------------------
def parse_expression(source: Any, *, max_depth: int = MAX_DEPTH) -> Expr:
    """Parse and structurally validate an expression from any supported form.

    ``source`` may be:

    * an :class:`Expr` (validated and returned unchanged);
    * a :class:`str` textual expression (e.g. ``"rank(return)"``);
    * a :class:`~collections.abc.Mapping` declarative form (e.g.
      ``{"op": "field", "role": "return"}``).

    Anything else -- including a callable, a partially built object, or a
    half-specified mapping -- raises a typed error, so a parse yields either a
    total, immutable node or a typed failure. Context-free structural
    validation always runs; declared-context requirement checks are the
    caller's job via :func:`validate_expression`.
    """
    if isinstance(source, Expr):
        node = source
    elif isinstance(source, str):
        node = _TextParser(_tokenize(source)).parse()
    elif isinstance(source, Mapping):
        node = _from_mapping(source, 0)
    elif callable(source):
        raise UnsupportedOperationError(
            "expression source is a callable; user-defined executable code is "
            "not expressible in a Phase 6 factor expression"
        )
    else:
        raise InvalidExpressionError(
            "expression source must be an Expr, a textual expression, or a "
            f"declarative mapping; got {type(source).__name__}"
        )
    return validate_expression(node, max_depth=max_depth)


def from_dict(payload: Mapping[str, Any]) -> Expr:
    """Parse an expression from its canonical declarative mapping form."""
    return parse_expression(payload)


# --------------------------------------------------------------------------
# canonical form and tree queries
# --------------------------------------------------------------------------
def to_dict(expr: Expr) -> dict[str, Any]:
    """Canonical declarative mapping for ``expr`` (validated first)."""
    return validate_expression(expr).to_dict()


def canonical_json(expr: Expr) -> str:
    """Deterministic canonical JSON string for ``expr``.

    Sorted keys, no insignificant whitespace, ASCII-only, finite numbers
    only (non-finite literals are rejected at construction).
    """
    return json.dumps(
        to_dict(expr),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def expression_hash(expr: Expr) -> str:
    """Deterministic SHA-256 content hash of the canonical expression form."""
    return hashlib.sha256(canonical_json(expr).encode("utf-8")).hexdigest()


def _iter_nodes(expr: Expr) -> list[Expr]:
    nodes: list[Expr] = []
    stack = [validate_expression(expr)]
    while stack:
        node = stack.pop()
        nodes.append(node)
        stack.extend(node.children())
    return nodes


def referenced_roles(expr: Expr) -> tuple[str, ...]:
    """Sorted, de-duplicated semantic roles referenced by ``expr``.

    Deterministic, so it can feed the derived-inputs step of the engine and
    the ``FactorSpec`` content hash.
    """
    roles = {node.role for node in _iter_nodes(expr) if isinstance(node, Field)}
    return tuple(sorted(roles))


def contains_cross_sectional(expr: Expr) -> bool:
    """True iff ``expr`` contains any per-date (cross-sectional) transform."""
    return any(isinstance(node, CrossSectional) for node in _iter_nodes(expr))


def contains_temporal(expr: Expr) -> bool:
    """True iff ``expr`` contains any lag/rolling (time-series) transform."""
    return any(isinstance(node, (Lag, Rolling)) for node in _iter_nodes(expr))
