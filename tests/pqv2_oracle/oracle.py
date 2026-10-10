"""Independent brute-force P-Q v2 oracle (test-only; no production imports).

The oracle enumerates finite correction states and recomputes *everything*
from primitive synthetic data -- closes, corporate-action (CA) events and
volumes -- with its own numpy/pandas kernels.  It never consumes intervals,
sandwiches or envelopes produced by the analytic code, so it is an
independent reference for the frozen methodology
(``docs/discovery_dataset_v1_2_pq_v2_methodology.md``, M§2-M§16).

Design
------
``World`` holds a synthetic universe:

* trusted sessions ``0..T-1`` with integer positions used as dates;
* per security: a raw close per session (absent key = no row), a raw volume,
  recorded CA events ``(session, kind, factor)``, a flagged-session set and
  an optional ``listed_end`` (terminal evidence);
* per formation session: universe membership;
* parameters: formation rows, label horizon ``h``, ``G`` groups, the
  winsorization quantile ``q``, the cost rate ``c``, the sign, and the
  expression form.

A *correction state* is a per-flag choice of either

* an **ordinary S2 alternative**: a branch ``NONE | CA | CLOSE_PREV |
  CLOSE_S`` with a multiplier ``lambda`` drawn from a finite grid over the
  relative S2 set ``Q_s`` (endpoints, ``1`` and interior points); or
* a **MISDATED alternative**: moving one recorded event at ``d`` with
  ``|d - s| <= 5`` from ``d`` to ``s``, which forces ``branch = NONE`` and
  ``lambda = 1``.

The two are **exclusive per flag** (PQ2-G-E1, amending G §3): no state
combines a MISDATED choice with a non-``NONE`` branch or ``lambda != 1`` for
the same flag.  Different flags are still combined by a Cartesian product.

The oracle enumerates the Cartesian product over flags and, for each state,
recomputes ``m_d = close_d / close_{prev d} * phi_d`` (valid only when both
closes exist), the segment-1 TRI, ``ret``, ``dv = close * volume``, the
expression, the universe-restricted linear-quantile winsorization, the
``rank(method="first")`` bucketing, the D7a label semantics and the T0/TS
scenario values.

Only ``numpy``, ``pandas`` and ``itertools`` are imported.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from itertools import product
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# frozen constants (mirrored locally so the oracle stays independent)
# ---------------------------------------------------------------------------
S2_LO = 0.5
S2_HI = 1.5
MISDATED_WINDOW = 5

#: P-Q v2.3 event timing scenarios (M1b §3).
SCENARIO_K0 = "K0"
SCENARIO_K1 = "K1"
EVENT_SCENARIOS = (SCENARIO_K0, SCENARIO_K1)

#: The frozen [15:45, 16:00) America/New_York pre-close boundary (M1b §3).
B_E_START = time(15, 45)
B_E_END = time(16, 0)

#: Event-lookback bound (plan §2 / E§2): 504 trading sessions.
EVENT_LOOKBACK = 504

#: Identity-map statuses whose event cells are NOT_ASSESSED (M1b §2).
MAPPED = "MAPPED"
FOREIGN_FILER = "FOREIGN_FILER"
AMBIGUOUS = "AMBIGUOUS"
NO_FILINGS_IN_INTERVAL = "NO_FILINGS_IN_INTERVAL"
NO_CANDIDATE = "NO_CANDIDATE"
CONTRADICTED = "CONTRADICTED"

UNMAPPED_STATUSES = (AMBIGUOUS, NO_FILINGS_IN_INTERVAL, NO_CANDIDATE)

#: The only admitted event type (E§1).
EVENT_TYPE_EA_8K202 = "EA_8K202"

#: The five M1b event causes, in the frozen precedence order (M1b §2).
EVENT_CAUSE_RESERVE = "PQV2_EVENT_RESERVE"
EVENT_CAUSE_IDENTITY_CONTRADICTED = "PQV2_EVENT_IDENTITY_CONTRADICTED"
EVENT_CAUSE_IDENTITY_UNMAPPED = "PQV2_EVENT_IDENTITY_UNMAPPED"
EVENT_CAUSE_SOURCE_EXCLUDED = "PQV2_EVENT_SOURCE_EXCLUDED"
EVENT_CAUSE_TIME_UNAVAILABLE = "PQV2_EVENT_TIME_UNAVAILABLE"
EVENT_CAUSE_PRECEDENCE = (
    EVENT_CAUSE_RESERVE,
    EVENT_CAUSE_IDENTITY_CONTRADICTED,
    EVENT_CAUSE_IDENTITY_UNMAPPED,
    EVENT_CAUSE_SOURCE_EXCLUDED,
    EVENT_CAUSE_TIME_UNAVAILABLE,
)
EVENT_CAUSES = frozenset(EVENT_CAUSE_PRECEDENCE)

#: The frozen event semantic ids (plan §2); an event primitive is a Field.
EVENT_ROLE_EAR = "event_ear_k_kp1"
EVENT_ROLE_DAYS_SINCE_EA = "event_days_since_ea"
EVENT_ROLE_RECENT_EA_PREFIX = "event_recent_ea_w"
EVENT_ROLES = (EVENT_ROLE_EAR, EVENT_ROLE_DAYS_SINCE_EA)


def event_recent_role(window: int) -> str:
    """The frozen semantic id of ``recent_ea(window)``."""
    return f"{EVENT_ROLE_RECENT_EA_PREFIX}{int(window)}"


def event_recent_window(role: str) -> "int | None":
    """The ``w`` of a ``recent_ea`` semantic id, else ``None``."""
    if not role.startswith(EVENT_ROLE_RECENT_EA_PREFIX):
        return None
    suffix = role[len(EVENT_ROLE_RECENT_EA_PREFIX):]
    if suffix.isdigit():
        return int(suffix)
    return None


def is_event_role(role: str) -> bool:
    """True iff ``role`` is one of the frozen event semantic ids."""
    return role in EVENT_ROLES or event_recent_window(role) is not None

BRANCH_NONE = "NONE"
BRANCH_CA = "CA"
BRANCH_CLOSE_PREV = "CLOSE_PREV"
BRANCH_CLOSE_S = "CLOSE_S"
_BRANCHES = (BRANCH_NONE, BRANCH_CA, BRANCH_CLOSE_PREV, BRANCH_CLOSE_S)

STATUS_COMPLETE = "COMPLETE"
STATUS_EMPTY = "EMPTY"
STATUS_MISSING = "MISSING"
STATUS_UNKNOWN = "UNKNOWN"

_EXPR_FORMS = ("ratio", "neg_short", "volscaled", "std_x_rank")

#: P-Q v2.4 fold-end firewall causes (firewall doc §2).  The development fold
#: keeps the v2.3 ``PQV2_RESERVE_UNKNOWN``; the reserve fold uses the new
#: ``PQV2_FOLD_END_UNKNOWN``.  The two never co-occur, so a cell carries at
#: most one of them.
FOLD_CAUSE_RESERVE = "PQV2_RESERVE_UNKNOWN"
FOLD_CAUSE_FOLD_END = "PQV2_FOLD_END_UNKNOWN"


@dataclass(frozen=True)
class Fold:
    """A declared evaluation fold (P-Q v2.4, firewall doc §2).

    ``start``/``end`` are synthetic trusted sessions (the oracle uses integer
    sessions as dates).  The development fold's end is the firewall at the
    development end; a reserve fold's end is the data cutoff ``C``.  The
    oracle's default development fold carries no explicit synthetic session
    firewall, so the default path is exactly the v2.3 path.
    """

    name: str
    start: int | None = None
    end: int | None = None

    @property
    def is_reserve(self) -> bool:
        return self.name == "reserve"

    @property
    def unknown_cause(self) -> str:
        """The period cause of a beyond-fold-end flag/label (firewall §2)."""
        return FOLD_CAUSE_FOLD_END if self.is_reserve else FOLD_CAUSE_RESERVE


#: The oracle's default development fold.  It carries no explicit synthetic
#: session firewall, so the default oracle path is byte-identical to v2.3; an
#: explicit development fold end is constructed with :class:`Fold` directly.
DEVELOPMENT_FOLD = Fold("development", None, None)


# ---------------------------------------------------------------------------
# primitive numeric kernels (own implementations; cross-checked in self-test)
# ---------------------------------------------------------------------------
def lag_frame(frame: np.ndarray, periods: int) -> np.ndarray:
    """Trailing lag down the row axis (``row r`` sees ``row r-periods``)."""
    frame = np.asarray(frame, dtype="float64")
    if periods < 0:
        raise ValueError("lag periods must be >= 0")
    out = np.full(frame.shape, np.nan, dtype="float64")
    if periods == 0:
        return frame.copy()
    if periods < frame.shape[0]:
        out[periods:] = frame[:-periods]
    return out


def _rolling(frame: np.ndarray, window: int, *, ddof: int) -> np.ndarray:
    frame = np.asarray(frame, dtype="float64")
    n_rows = frame.shape[0]
    out = np.full(frame.shape, np.nan, dtype="float64")
    for r in range(window - 1, n_rows):
        win = frame[r - window + 1 : r + 1]
        finite = np.all(np.isfinite(win), axis=0)
        if not finite.any():
            continue
        if ddof is None:
            out[r, finite] = np.mean(win[:, finite], axis=0)
            continue
        denom = window - ddof
        if denom <= 0:
            continue
        mean = np.mean(win[:, finite], axis=0)
        var = np.sum((win[:, finite] - mean) ** 2, axis=0) / denom
        out[r, finite] = np.sqrt(var)
    return out


def rolling_mean(frame: np.ndarray, window: int) -> np.ndarray:
    """Trailing rolling mean, ``min_periods == window`` (all-or-NaN)."""
    return _rolling(frame, window, ddof=None)


def rolling_std(frame: np.ndarray, window: int, *, ddof: int = 1) -> np.ndarray:
    """Trailing rolling sample std, ``min_periods == window``."""
    return _rolling(frame, window, ddof=ddof)


def standardize(frame: np.ndarray, *, ddof: int = 0) -> np.ndarray:
    """Per-row population z-score over non-NaN cells (M§11.2)."""
    frame = np.asarray(frame, dtype="float64")
    out = np.full(frame.shape, np.nan, dtype="float64")
    for r in range(frame.shape[0]):
        row = frame[r]
        finite = np.isfinite(row)
        count = int(finite.sum())
        if count == 0 or count - ddof <= 0:
            continue
        vals = row[finite]
        mean = vals.mean()
        std = np.sqrt(np.sum((vals - mean) ** 2) / (count - ddof))
        if std == 0.0 or not np.isfinite(std):
            continue
        out[r] = (row - mean) / std
    return out


def rank_average(frame: np.ndarray) -> np.ndarray:
    """Per-row average rank across non-NaN cells (NaN kept)."""
    df = pd.DataFrame(np.asarray(frame, dtype="float64"))
    return df.rank(axis=1, method="average", na_option="keep").to_numpy(
        dtype="float64"
    )


def winsorize(frame: np.ndarray, q: float) -> np.ndarray:
    """Per-row linear-quantile clip at ``q`` / ``1 - q`` (NaN kept)."""
    df = pd.DataFrame(np.asarray(frame, dtype="float64"))
    quantiles = df.quantile([q, 1.0 - q], axis=1, interpolation="linear")
    low = quantiles.iloc[0]
    high = quantiles.iloc[1]
    return df.clip(lower=low, upper=high, axis=0).to_numpy(dtype="float64")


def assign_groups(values: np.ndarray, n_groups: int) -> np.ndarray:
    """Rank-``first`` bucket assignment exactly as ``portfolio_sort``."""
    values = np.asarray(values, dtype="float64")
    out = np.full(values.shape, np.nan, dtype="float64")
    valid = np.isfinite(values)
    n = int(valid.sum())
    if n == 0:
        return out
    idx = np.flatnonzero(valid)
    ordered = np.argsort(values[idx], kind="stable")
    ranks = np.empty(n, dtype="float64")
    ranks[ordered] = np.arange(1, n + 1, dtype="float64")
    bucket = ((ranks - 1.0) * n_groups // n) + 1.0
    out[idx] = np.minimum(bucket, float(n_groups))
    return out


def turnover_series(memberships: Sequence[Mapping[str, int]]) -> list[float]:
    """Membership-based turnover, first/empty-previous period exactly 0."""
    values: list[float] = []
    previous: Mapping[str, int] | None = None
    for member in memberships:
        if previous is None or not previous:
            values.append(0.0)
        else:
            union = set(previous) | set(member)
            if not union:
                values.append(0.0)
            else:
                changed = 0
                for stock in union:
                    if stock not in previous or stock not in member:
                        changed += 1
                    elif previous[stock] != member[stock]:
                        changed += 1
                values.append(changed / len(union))
        previous = member
    return values


def s2_relative(m_obs: float) -> tuple[float, float]:
    """Relative correction set ``Q_s = S2(m_obs) / m_obs``."""
    if not np.isfinite(m_obs) or m_obs <= 0.0:
        raise ValueError("m_obs must be finite and positive")
    return (min(S2_LO, m_obs) / m_obs, max(S2_HI, m_obs) / m_obs)


# ---------------------------------------------------------------------------
# synthetic world
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CaEvent:
    session: int
    kind: str
    factor: float


@dataclass(frozen=True)
class Stock:
    key: str
    close: Mapping[int, float]
    volume: Mapping[int, float]
    events: tuple[CaEvent, ...] = ()
    flags: frozenset[int] = frozenset()
    listed_end: int | None = None


@dataclass(frozen=True)
class EventDatum:
    """One synthetic v1.3 event, oracle-local.

    ``k_session`` is the v1.3 ``k_session`` (K0); ``None`` models a null-time
    event (``EVENT_TIME_UNAVAILABLE``).  ``acceptance_et`` is the index-header
    acceptance **wall clock** in America/New_York (the M1b time source); only
    its time-of-day is used for the B_e boundary.  ``filing_date`` is the
    scope proxy (an integer session in the synthetic worlds).
    """

    k_session: int | None
    acceptance_et: time | None = None
    filing_date: int | None = None
    accession: str = ""
    form: str = "8-K"
    event_type: str = EVENT_TYPE_EA_8K202


def is_preclose(acceptance_et: time | None) -> bool:
    """B_e: the acceptance time lies in ``[15:45, 16:00)`` ET (M1b §3).

    The boundary is asymmetric and pre-close: at or after 16:00 the event is
    already mapped to the next session by v1.3, so no second scenario applies.
    """
    return acceptance_et is not None and B_E_START <= acceptance_et < B_E_END


def _event_order(event: EventDatum) -> tuple:
    """Deterministic tie-break for events sharing a knowledge session."""
    return (event.acceptance_et or time.min, event.accession)


@dataclass(frozen=True)
class Expr:
    """Expression form (M§2 / plan §6.3)."""

    kind: str
    k1: int = 0
    k2: int = 0
    w: int = 0
    w_s: int = 0
    w_l: int = 0

    def __post_init__(self) -> None:
        if self.kind not in _EXPR_FORMS:
            raise ValueError(f"unknown expression form {self.kind!r}")


@dataclass(frozen=True)
class FlagState:
    security: str
    s: int
    branch: str
    lam: float = 1.0
    misdated: int | None = None


class World:
    """A synthetic panel plus the correction-grid configuration."""

    def __init__(
        self,
        *,
        sessions: Sequence[int],
        stocks: Sequence[Stock],
        universes: Mapping[int, Iterable[str]],
        expression: Expr | None = None,
        expression_ast: object | None = None,
        h: int = 1,
        n_groups: int = 5,
        q: float = 0.20,
        cost: float = 0.0,
        sign: int = 1,
        development_end: int | None = None,
        reserve_start: int | None = None,
        fold: "Fold | None" = None,
        interior_points: int = 1,
        misdated: bool = True,
        single_misdating: bool = False,
        event_calendars: Mapping[str, Sequence[EventDatum]] | None = None,
        key_event_status: Mapping[str, str] | None = None,
    ) -> None:
        self.sessions = tuple(int(s) for s in sessions)
        self.stocks = tuple(stocks)
        self.universes = {
            int(f): frozenset(members) for f, members in universes.items()
        }
        if expression is None and expression_ast is None:
            raise ValueError("a World needs an expression or an expression_ast")
        self.expression = expression
        self.expression_ast = expression_ast
        self.h = int(h)
        self.n_groups = int(n_groups)
        self.q = float(q)
        self.cost = float(cost)
        self.sign = int(sign)
        self.development_end = development_end
        self.reserve_start = reserve_start
        self.fold = fold
        self.interior_points = int(interior_points)
        self.misdated = bool(misdated)
        self.single_misdating = bool(single_misdating)

        self._pos = {s: i for i, s in enumerate(self.sessions)}
        self.keys = tuple(stock.key for stock in self.stocks)
        self.by_key = {stock.key: stock for stock in self.stocks}
        self.column = {key: i for i, key in enumerate(self.keys)}
        self.formations = tuple(sorted(self.universes))
        self.event_calendars = {
            key: tuple(events)
            for key, events in (event_calendars or {}).items()
        }
        self.key_event_status = dict(key_event_status or {})
        self._view_cache: dict = {}
        self._states_cache: tuple[FlagState, ...] | None = None

    # -- positions / sessions -------------------------------------------------
    def pos_of(self, session: int) -> int:
        return self._pos[int(session)]

    def next_session(self, session: int) -> int | None:
        p = self._pos[int(session)]
        if p + 1 < len(self.sessions):
            return self.sessions[p + 1]
        return None

    def formation_end(self, formation: int) -> int:
        return self.sessions[self._pos[formation] + self.h]

    # -- fold firewall (P-Q v2.4, firewall §2) --------------------------------
    def fold_end_position(self) -> int | None:
        """The row position of the declared fold's end, else ``None``."""
        if self.fold is None or self.fold.end is None:
            return None
        return self._pos[int(self.fold.end)]

    def fold_unknown_cause(self) -> str | None:
        """The declared fold's period cause for a beyond-end flag/label.

        ``None`` when no fold is declared (the v2.3 path).
        """
        if self.fold is None:
            return None
        return self.fold.unknown_cause

    def _fold_end_label_unknown(self, key: str, f: int, e2: int) -> bool:
        """The P-Q v2.4 fold-end firewall on a COMPLETE label (firewall §2).

        True when the observed endpoint is the fold end E, or a reaching flag
        has ``next(s) > E``, or its ``+5`` MISDATED window crosses E.
        """
        end = self.fold_end_position()
        if end is None:
            return False
        if e2 == self.sessions[end]:
            return True
        last = self.next_session(e2)
        for s in self._flag_sessions(key):
            if not (f <= s and (last is None or s <= last)):
                continue
            nxt = self.next_session(s)
            if nxt is None or nxt > self.sessions[end]:
                return True
            if self._pos[s] + MISDATED_WINDOW > end:
                return True
        return False

    def fold_label_cause(self, key: str, f: int, e2: int) -> str | None:
        """The fold-end cause of one label cell, or ``None`` when not bounded."""
        if not self._fold_end_label_unknown(key, f, e2):
            return None
        assert self.fold is not None
        return self.fold.unknown_cause

    # -- event calendar (P-Q v2.3 / M1b) --------------------------------------
    def effective_k(self, event: EventDatum, scenario: str) -> int | None:
        """The M1b scenario knowledge session of one event."""
        if scenario not in EVENT_SCENARIOS:
            raise ValueError(f"unknown event scenario {scenario!r}")
        if event.k_session is None:
            return None
        if scenario == SCENARIO_K0:
            return event.k_session
        # K1 moves only a pre-close B_e event to the next trusted session.
        if is_preclose(event.acceptance_et):
            return self.next_session(event.k_session)
        return event.k_session

    def _calendar(self, key: str) -> tuple[EventDatum, ...]:
        return self.event_calendars.get(key, ())

    def event_observed(
        self, key: str, row: int, scenario: str
    ) -> EventDatum | None:
        """e*(row): the latest observed event in the 504-session lookback.

        Observability (plan §2): ``K(e) + 1 <= date(row - 1)``; the chosen e*
        must satisfy ``K(e*) >= date(row - 1) - 504 sessions``.
        """
        status = self.key_event_status.get(key, MAPPED)
        if status in UNMAPPED_STATUSES or status in (
            CONTRADICTED,
            FOREIGN_FILER,
        ):
            return None
        if row <= 0:
            return None
        prev_pos = row - 1
        best: EventDatum | None = None
        best_key: tuple | None = None
        for event in self._calendar(key):
            if event.event_type != EVENT_TYPE_EA_8K202:
                continue
            k = self.effective_k(event, scenario)
            if k is None:
                continue
            pk = self.pos_of(k)
            if pk + 1 > prev_pos:
                continue
            marker = (pk, _event_order(event))
            if best_key is None or marker > best_key:
                best_key = marker
                best = event
        if best is None:
            return None
        pk = self.pos_of(self.effective_k(best, scenario))
        if pk < prev_pos - EVENT_LOOKBACK:
            return None
        return best

    def event_causes(self, key: str, row: int, scenario: str) -> frozenset[str]:
        """The M1b causes for one event-dependent cell (oracle reference)."""
        status = self.key_event_status.get(key, MAPPED)
        if status in UNMAPPED_STATUSES:
            return frozenset({EVENT_CAUSE_IDENTITY_UNMAPPED})
        if status == CONTRADICTED:
            return frozenset({EVENT_CAUSE_IDENTITY_CONTRADICTED})
        if status == FOREIGN_FILER:
            return frozenset({EVENT_CAUSE_SOURCE_EXCLUDED})
        if row <= 0:
            return frozenset()
        prev_pos = row - 1
        causes: set[str] = set()
        event = self.event_observed(key, row, scenario)
        if event is not None:
            pk = self.pos_of(self.effective_k(event, scenario))
            if (
                self.fold is None
                and self.reserve_start is not None
                and self.sessions[pk] >= self.reserve_start
            ):
                causes.add(EVENT_CAUSE_RESERVE)
            if (
                self.fold is not None
                and self.fold.end is not None
                and self.sessions[pk] > int(self.fold.end)
            ):
                causes.add(EVENT_CAUSE_RESERVE)
        for candidate in self._calendar(key):
            if candidate.event_type != EVENT_TYPE_EA_8K202:
                continue
            if candidate.k_session is not None or candidate.filing_date is None:
                continue
            fp = self.pos_of(candidate.filing_date)
            if event is not None:
                pk = self.pos_of(self.effective_k(event, scenario))
                if pk < fp <= prev_pos:
                    causes.add(EVENT_CAUSE_TIME_UNAVAILABLE)
            elif prev_pos - EVENT_LOOKBACK <= fp <= prev_pos:
                causes.add(EVENT_CAUSE_TIME_UNAVAILABLE)
        return frozenset(causes)

    def event_primary_cause(
        self, key: str, row: int, scenario: str
    ) -> str | None:
        """The highest-precedence M1b event cause for one cell, else None."""
        causes = self.event_causes(key, row, scenario)
        for cause in EVENT_CAUSE_PRECEDENCE:
            if cause in causes:
                return cause
        return None

    # -- state enumeration ----------------------------------------------------
    def _lam_grid(self, m_obs: float) -> tuple[float, ...]:
        lo, hi = s2_relative(m_obs)
        points = {lo, hi, 1.0}
        for i in range(1, self.interior_points + 1):
            points.add(lo + (hi - lo) * i / (self.interior_points + 1))
        return tuple(sorted(points))

    def _misdated_options(self, stock: Stock, s: int) -> tuple[int, ...]:
        if not self.misdated:
            return ()
        out = []
        for event in stock.events:
            # M§7: only already-recorded split or cash-distribution events.
            if event.kind not in ("split", "dividend"):
                continue
            if event.session == s:
                continue
            if abs(self._pos[event.session] - self._pos[s]) <= MISDATED_WINDOW:
                out.append(event.session)
        return tuple(sorted(set(out)))

    def _flag_states(self, stock: Stock, s: int) -> tuple[FlagState, ...]:
        m_obs = self._observed_m(stock, s)
        grid = self._lam_grid(m_obs)
        misdated = self._misdated_options(stock, s)
        # E1.1 (a): the ordinary S2 alternatives (unchanged).
        options: list[FlagState] = [
            FlagState(stock.key, s, BRANCH_NONE, 1.0, None)
        ]
        for branch in (BRANCH_CA, BRANCH_CLOSE_PREV, BRANCH_CLOSE_S):
            for lam in grid:
                options.append(FlagState(stock.key, s, branch, lam, None))
        # E1.1 (b): a MISDATED alternative is exclusive with an S2 correction
        # of the same flag: branch NONE and lambda = 1.
        for mis in misdated:
            options.append(FlagState(stock.key, s, BRANCH_NONE, 1.0, mis))
        return tuple(options)

    def _observed_m(self, stock: Stock, s: int) -> float:
        p = self._pos[s]
        if p == 0:
            raise ValueError("flagged session has no previous session")
        prev = self.sessions[p - 1]
        if s not in stock.close or prev not in stock.close:
            raise ValueError("flagged session lacks a valid close pair")
        phi = 1.0
        for event in stock.events:
            if event.session == s:
                phi *= event.factor
        return stock.close[s] / stock.close[prev] * phi

    def states(self) -> tuple[FlagState, ...]:
        if self._states_cache is not None:
            return self._states_cache
        per_flag: list[tuple[FlagState, ...]] = []
        for stock in self.stocks:
            for s in sorted(stock.flags):
                per_flag.append(self._flag_states(stock, s))
        if not per_flag:
            self._states_cache = ((),)
            return self._states_cache
        combos = []
        for combo in product(*per_flag):
            ordered = tuple(
                sorted(combo, key=lambda fs: (fs.security, fs.s))
            )
            if self.single_misdating and (
                sum(1 for fs in ordered if fs.misdated is not None) > 1
            ):
                continue
            combos.append(ordered)
        self._states_cache = tuple(combos)
        return self._states_cache

    def view(self, state: Sequence[FlagState]) -> "_StateView":
        key = tuple(state)
        if key not in self._view_cache:
            self._view_cache[key] = _StateView(self, key)
        return self._view_cache[key]

    # -- label status ---------------------------------------------------------
    def label_status(
        self, key: str, f: int, e: int
    ) -> tuple[str, int | None]:
        stock = self.by_key[key]
        pos_f = self._pos[f]
        pos_e = self._pos[e]
        h = pos_e - pos_f
        if h <= 0:
            raise ValueError("label endpoint must follow the formation")
        n_obs = h
        terminal = False
        if stock.listed_end is not None:
            listed_end = stock.listed_end
            within_dev = (
                self.development_end is None
                or listed_end <= self.development_end
            )
            if within_dev and listed_end <= e:
                terminal = True
                lid = max(
                    i
                    for i, s in enumerate(self.sessions)
                    if s <= listed_end
                )
                l_offset = max(0, lid - pos_f)
                n_obs = min(h, l_offset)
        if n_obs == 0:
            return STATUS_EMPTY, None
        e2 = self.sessions[pos_f + n_obs]
        # validity over the observed segment (state invariant)
        for p in range(pos_f + 1, pos_f + n_obs + 1):
            cur = self.sessions[p]
            prev = self.sessions[p - 1]
            if cur not in stock.close or prev not in stock.close:
                return STATUS_MISSING, e2
        # reserve firewall on the observed endpoint
        if self.development_end is not None and e2 == self.development_end:
            return STATUS_UNKNOWN, e2
        if self.reserve_start is not None and e2 >= self.reserve_start:
            return STATUS_UNKNOWN, e2
        # flag-level firewall: a dependent flag whose +-5 window runs past the
        # development end has an indeterminate candidate set (M§8).
        if self.development_end is not None:
            last = self.next_session(e2)
            dependent: set[int] = set()
            for stock_flag in self._flag_sessions(key):
                if f <= stock_flag and (last is None or stock_flag <= last):
                    dependent.add(stock_flag)
            for s in dependent:
                if self._pos[s] + MISDATED_WINDOW > self._pos[
                    self.development_end
                ]:
                    return STATUS_UNKNOWN, e2
        # P-Q v2.4 fold-end firewall (firewall §2): the declared fold's end E.
        if self._fold_end_label_unknown(key, f, e2):
            return STATUS_UNKNOWN, e2
        return STATUS_COMPLETE, e2

    def _flag_sessions(self, key: str) -> tuple[int, ...]:
        return tuple(sorted(self.by_key[key].flags))


class _StateView:
    """Corrected primitives and derived frames for one correction state."""

    def __init__(self, world: World, state: Sequence[FlagState]) -> None:
        self.world = world
        self.state = tuple(state)
        self._expr_frame: dict[str, np.ndarray] = {}
        self._event_frame_cache: dict[tuple[str, str], np.ndarray] = {}
        self._build()

    def _build(self) -> None:
        w = self.world
        close: dict[str, np.ndarray] = {}
        phi: dict[str, np.ndarray] = {}
        volume: dict[str, np.ndarray] = {}
        for stock in w.stocks:
            close[stock.key] = np.array(
                [stock.close.get(t, np.nan) for t in w.sessions], dtype="float64"
            )
            volume[stock.key] = np.array(
                [stock.volume.get(t, np.nan) for t in w.sessions], dtype="float64"
            )
            ph = np.ones(len(w.sessions), dtype="float64")
            for event in stock.events:
                ph[w.pos_of(event.session)] *= event.factor
            phi[stock.key] = ph

        for fs in self.state:
            stock = w.by_key[fs.security]
            c = close[fs.security]
            ph = phi[fs.security]
            p = w.pos_of(fs.s)
            if fs.branch == BRANCH_CA:
                ph[p] *= fs.lam
            elif fs.branch == BRANCH_CLOSE_PREV:
                if p - 1 >= 0:
                    c[p - 1] = c[p - 1] / fs.lam
            elif fs.branch == BRANCH_CLOSE_S:
                c[p] = c[p] * fs.lam
            if fs.misdated is not None:
                d = fs.misdated
                dp = w.pos_of(d)
                combined = 1.0
                for event in stock.events:
                    if event.session == d:
                        combined *= event.factor
                ph[dp] = ph[dp] / combined
                ph[p] = ph[p] * combined

        n = len(w.sessions)
        self.close = close
        self.volume = volume
        self.phi = phi
        m: dict[str, np.ndarray] = {}
        tri: dict[str, np.ndarray] = {}
        dv: dict[str, np.ndarray] = {}
        for key in w.keys:
            c = close[key]
            ph = phi[key]
            mk = np.full(n, np.nan, dtype="float64")
            for i in range(1, n):
                if np.isfinite(c[i]) and np.isfinite(c[i - 1]):
                    mk[i] = c[i] / c[i - 1] * ph[i]
            m[key] = mk
            tri[key] = _tri_from_m(mk)
            dv[key] = c * volume[key]
        self.m = m
        self.tri = tri
        self.dv = dv

    # -- row frames (bridge lag: row r holds the field dated r-1) -------------
    def tri_row(self) -> np.ndarray:
        return self._row_matrix(self.tri)

    def ret_row(self) -> np.ndarray:
        return self._row_matrix({k: self.m[k] - 1.0 for k in self.world.keys})

    def dv_row(self) -> np.ndarray:
        return self._row_matrix(self.dv)

    def _row_matrix(self, arrays: Mapping[str, np.ndarray]) -> np.ndarray:
        w = self.world
        cols = []
        for key in w.keys:
            arr = np.asarray(arrays[key], dtype="float64")
            shifted = np.full(arr.shape, np.nan, dtype="float64")
            shifted[1:] = arr[:-1]
            cols.append(shifted)
        return np.column_stack(cols)

    # -- event primitives (P-Q v2.3 / M1b) ------------------------------------
    def tri_at_date(self, key: str, date: int) -> float:
        """``TRI_dated(date)`` from the D7c frame row whose prev is ``date``.

        The D7c frame is bridge-lagged (row ``r`` holds the field dated
        ``r - 1``), so the value dated ``date`` sits one row below its
        position (the E-4-R2 rule).
        """
        frame = self.tri_row()
        row = self.world.pos_of(date) + 1
        if row >= len(self.world.sessions):
            return float("nan")
        return float(frame[row, self.world.column[key]])

    def _event_ear(self, key: str, pk: int) -> float:
        sessions = self.world.sessions
        if pk == 0:
            return float("nan")
        kp1 = self.world.next_session(sessions[pk])
        if kp1 is None:
            return float("nan")
        km1 = sessions[pk - 1]
        numer = self.tri_at_date(key, kp1)
        denom = self.tri_at_date(key, km1)
        if not (np.isfinite(numer) and np.isfinite(denom)) or denom == 0.0:
            return float("nan")
        return numer / denom - 1.0

    def event_primitive_frame(
        self, role: str, scenario: str = SCENARIO_K0
    ) -> np.ndarray:
        """The per-(row, key) matrix of one event primitive for a scenario.

        A caused cell (M1b) and a cell with no observed e* are NaN.
        """
        cache_key = (role, scenario)
        cached = self._event_frame_cache.get(cache_key)
        if cached is not None:
            return cached
        n = len(self.world.sessions)
        out = np.full((n, len(self.world.keys)), np.nan, dtype="float64")
        window = event_recent_window(role)
        for j, key in enumerate(self.world.keys):
            for row in range(n):
                if self.world.event_causes(key, row, scenario):
                    continue
                event = self.world.event_observed(key, row, scenario)
                if event is None:
                    continue
                pk = self.world.pos_of(
                    self.world.effective_k(event, scenario)
                )
                days = (row - 1) - pk
                if role == EVENT_ROLE_EAR:
                    value = self._event_ear(key, pk)
                elif role == EVENT_ROLE_DAYS_SINCE_EA:
                    value = float(days)
                elif window is not None:
                    value = 1.0 if days <= window else 0.0
                else:  # pragma: no cover - Field validates the role
                    raise ValueError(f"unknown event role {role!r}")
                if np.isfinite(value):
                    out[row, j] = value
        self._event_frame_cache[cache_key] = out
        return out

    # -- expression -----------------------------------------------------------
    def expr_frame(self, scenario: str = SCENARIO_K0) -> np.ndarray:
        cached = self._expr_frame.get(scenario)
        if cached is not None:
            return cached
        if self.world.expression_ast is not None:
            from . import ast as _ast

            frame = _ast.evaluate(
                self.world.expression_ast, self, scenario=scenario
            )
            self._expr_frame[scenario] = frame
            return frame
        expr = self.world.expression
        tri_row = self.tri_row()
        if expr.kind == "ratio":
            frame = lag_frame(tri_row, expr.k1) / lag_frame(tri_row, expr.k2)
        elif expr.kind == "neg_short":
            frame = -lag_frame(tri_row, 0) / lag_frame(tri_row, expr.k1)
        elif expr.kind == "volscaled":
            ratio = (
                lag_frame(tri_row, expr.k1) / lag_frame(tri_row, expr.k2)
                - 1.0
            )
            std = rolling_std(self.ret_row(), expr.w, ddof=1)
            denom = lag_frame(std, expr.k1)
            frame = ratio / denom
        else:  # std_x_rank
            momentum = lag_frame(tri_row, expr.k1) / lag_frame(tri_row, expr.k2)
            dv_row = self.dv_row()
            value = rolling_mean(dv_row, expr.w_s) / rolling_mean(
                dv_row, expr.w_l
            )
            frame = standardize(momentum, ddof=0) * rank_average(value)
        self._expr_frame[scenario] = frame
        return frame

    def factor_row(
        self, formation: int, scenario: str = SCENARIO_K0
    ) -> np.ndarray:
        return self.expr_frame(scenario)[formation]

    def factor_values(
        self, formation: int, scenario: str = SCENARIO_K0
    ) -> dict[str, float]:
        row = self.factor_row(formation, scenario)
        return {
            key: float(row[self.world.column[key]])
            for key in self.world.keys
            if np.isfinite(row[self.world.column[key]])
        }

    # -- grouping -------------------------------------------------------------
    def group_map(
        self, formation: int, scenario: str = SCENARIO_K0
    ) -> dict[str, int]:
        w = self.world
        row = self.factor_row(formation, scenario)
        members = sorted(w.universes[formation])
        values = np.array(
            [row[w.column[key]] for key in members], dtype="float64"
        )
        clipped = winsorize(values.reshape(1, -1), w.q)[0]
        groups = assign_groups(clipped, w.n_groups)
        out: dict[str, int] = {}
        for key, group in zip(members, groups):
            if np.isfinite(group):
                out[key] = int(group)
        return out

    def legs(
        self, formation: int, scenario: str = SCENARIO_K0
    ) -> tuple[frozenset[str], frozenset[str]]:
        groups = self.group_map(formation, scenario)
        top = frozenset(k for k, g in groups.items() if g == self.world.n_groups)
        bottom = frozenset(k for k, g in groups.items() if g == 1)
        return top, bottom

    # -- labels ---------------------------------------------------------------
    def label_R(self, key: str, f: int, e: int) -> float:
        status, e2 = self.world.label_status(key, f, e)
        if status == STATUS_EMPTY:
            return 0.0
        if status != STATUS_COMPLETE or e2 is None:
            return float("nan")
        pos_f = self.world.pos_of(f)
        pos_e2 = self.world.pos_of(e2)
        m = self.m[key]
        prod = 1.0
        for p in range(pos_f + 1, pos_e2 + 1):
            value = m[p]
            if not np.isfinite(value):
                return float("nan")
            prod *= value
        return prod - 1.0


def _tri_from_m(m: np.ndarray) -> np.ndarray:
    """D7c segment-1-only TRI (``trusted_inputs._compute_segments``).

    The anchor is the first session ``j`` with a usable close at ``j`` and a
    valid return at ``j + 1`` (a valid ``m[j + 1]`` already implies a usable
    close at ``j``).  ``TRI(anchor) = 1.0`` and ``TRI(u)`` is the product of
    ``m`` over ``(anchor, u]`` while returns stay valid.  After the first
    break TRI is undefined for the rest of the series -- later segments are
    discovered by D7c but **never** given TRI values, so they stay NaN here.
    """
    out = np.full(m.shape, np.nan, dtype="float64")
    n = m.shape[0]
    anchor = None
    for j in range(n - 1):
        if np.isfinite(m[j + 1]):
            anchor = j
            break
    if anchor is None:
        return out
    out[anchor] = 1.0
    running = 1.0
    for u in range(anchor + 1, n):
        if np.isfinite(m[u]):
            running *= m[u]
            out[u] = running
        else:
            break
    return out


# ---------------------------------------------------------------------------
# public oracle API
# ---------------------------------------------------------------------------
def enumerate_states(world: World) -> tuple[tuple[FlagState, ...], ...]:
    """Every Cartesian correction state (deterministic order)."""
    return world.states()


def factor_values(
    world: World,
    state: Sequence[FlagState],
    formation: int,
    *,
    event_scenario: str = SCENARIO_K0,
) -> dict[str, float]:
    """Raw factor values at ``formation`` for one state (debug aid)."""
    return world.view(state).factor_values(formation, event_scenario)


def reachable_assignments(
    world: World, formation: int, *, event_scenario: str = SCENARIO_K0
) -> set[tuple[frozenset[str], frozenset[str]]]:
    """All (top, bottom) economic legs reachable over the correction set."""
    out: set[tuple[frozenset[str], frozenset[str]]] = set()
    for state in world.states():
        top, bottom = world.view(state).legs(formation, event_scenario)
        out.add((top, bottom))
    return out


def label_extremes(
    world: World, security: str, f: int, e: int
) -> tuple[float, float] | str:
    """Min/max label over the correction set, or a status string."""
    status, _ = world.label_status(security, f, e)
    if status != STATUS_COMPLETE:
        return status
    values = [
        world.view(state).label_R(security, f, e) for state in world.states()
    ]
    finite = [v for v in values if np.isfinite(v)]
    if not finite:
        return STATUS_MISSING
    return (min(finite), max(finite))


def _scenario_value(R: float, side: str, scenario: str) -> float:
    if scenario == "T0":
        return R
    if scenario == "TS":
        if side == "long":
            return (1.0 + R) * 0.0 - 1.0
        return (1.0 + R) * 2.0 - 1.0
    raise ValueError(f"unknown scenario {scenario!r}")


def _formation_index(world: World, formation: int) -> int:
    return world.formations.index(formation)


def _state_series(
    world: World,
    state: Sequence[FlagState],
    event_scenario: str = SCENARIO_K0,
):
    view = world.view(state)
    groups = [view.group_map(f, event_scenario) for f in world.formations]
    turnovers = turnover_series(groups)
    return view, groups, turnovers


def ls_min(
    world: World,
    formation: int,
    scenario: str = "T0",
    *,
    event_scenario: str = SCENARIO_K0,
) -> float | None:
    """Minimum realized long-short over the correction set (or ``None``)."""
    index = _formation_index(world, formation)
    f = world.formations[index]
    e = world.formation_end(f)
    best: float | None = None
    for state in world.states():
        view, groups, turnovers = _state_series(world, state, event_scenario)
        top = {k for k, g in groups[index].items() if g == world.n_groups}
        bottom = {k for k, g in groups[index].items() if g == 1}
        long_vals = [
            _scenario_value(view.label_R(k, f, e), "long", scenario)
            for k in top
        ]
        short_vals = [
            _scenario_value(view.label_R(k, f, e), "short", scenario)
            for k in bottom
        ]
        long_vals = [v for v in long_vals if np.isfinite(v)]
        short_vals = [v for v in short_vals if np.isfinite(v)]
        if not long_vals or not short_vals:
            continue
        cost = world.cost * turnovers[index]
        value = float(np.mean(long_vals)) - float(np.mean(short_vals)) - cost
        best = value if best is None else min(best, value)
    return best


def turnover_max(
    world: World, formation: int, *, event_scenario: str = SCENARIO_K0
) -> float:
    """Maximum turnover at ``formation`` over the correction set."""
    index = _formation_index(world, formation)
    return max(
        _state_series(world, state, event_scenario)[2][index]
        for state in world.states()
    )


# ---------------------------------------------------------------------------
# M§16 case library
# ---------------------------------------------------------------------------
def _stock(
    key: str,
    closes: Sequence[float | None],
    *,
    volumes: Sequence[float] | None = None,
    events: Sequence[tuple[int, str, float]] = (),
    flags: Sequence[int] = (),
    listed_end: int | None = None,
) -> Stock:
    close = {i: float(v) for i, v in enumerate(closes) if v is not None}
    if volumes is None:
        vol = {i: 1.0e6 for i in range(len(closes))}
    else:
        vol = {i: float(v) for i, v in enumerate(volumes)}
    return Stock(
        key=key,
        close=close,
        volume=vol,
        events=tuple(CaEvent(s, k, f) for s, k, f in events),
        flags=frozenset(flags),
        listed_end=listed_end,
    )


def _growth(n: int, rate: float, start: float = 100.0) -> list[float]:
    return [start * (rate**i) for i in range(n)]


def _union_world(
    *,
    n: int,
    stocks: Sequence[Stock],
    expression: Expr | None = None,
    expression_ast: object | None = None,
    formation: int,
    universe: Sequence[str],
    h: int = 1,
    n_groups: int = 5,
    q: float = 0.20,
    cost: float = 0.0,
    interior_points: int = 1,
    misdated: bool = True,
    single_misdating: bool = False,
    development_end: int | None = None,
    reserve_start: int | None = None,
    fold: "Fold | None" = None,
    sign: int = 1,
    universes: Mapping[int, Sequence[str]] | None = None,
    event_calendars: Mapping[str, Sequence[EventDatum]] | None = None,
    key_event_status: Mapping[str, str] | None = None,
) -> World:
    if universes is None:
        universes = {formation: tuple(universe)}
    return World(
        sessions=tuple(range(n)),
        stocks=stocks,
        universes={f: tuple(members) for f, members in universes.items()},
        expression=expression,
        expression_ast=expression_ast,
        h=h,
        n_groups=n_groups,
        q=q,
        cost=cost,
        sign=sign,
        development_end=development_end,
        reserve_start=reserve_start,
        fold=fold,
        interior_points=interior_points,
        misdated=misdated,
        single_misdating=single_misdating,
        event_calendars=event_calendars,
        key_event_status=key_event_status,
    )


def _campaign_window_world(*, n: int, expression: Expr, rates, suspect_rate=1.6,
                           suspect_index=2, flag_offset=3) -> World:
    """A full-Campaign-window world (>= 300 sessions)."""
    n_stocks = len(rates)
    stocks = []
    flags = []
    for i, rate in enumerate(rates):
        if i == suspect_index:
            flag_session = n - 5 - flag_offset
            events = ((flag_session, "split", 1.30),)
            flags_list = (flag_session,)
        else:
            events = ()
            flags_list = ()
        stocks.append(
            _stock(
                f"S{i:02d}",
                _growth(n, rate, start=100.0 + i),
                events=events,
                flags=flags_list,
            )
        )
    return _union_world(
        n=n,
        stocks=stocks,
        expression=expression,
        formation=n - 5,
        universe=[stock.key for stock in stocks],
        h=1,
        interior_points=1,
    )


def _build_cases() -> dict[str, World]:
    cases: dict[str, World] = {}
    n = 40
    formation = n - 5
    expr = Expr("ratio", k1=4, k2=1)

    # 1. Candidate-E single suspect -----------------------------------------
    stocks = [
        _stock(f"C{i}", _growth(n, 1.01 * (1 + 0.002 * i)))
        for i in range(8)
    ]
    flag_session = formation - 2
    stocks.append(
        _stock(
            "SUS",
            _growth(n, 1.30),
            events=((flag_session - 3, "split", 1.30),),
            flags=(flag_session,),
        )
    )
    cases["candidate_e_single_suspect"] = _union_world(
        n=n,
        stocks=stocks,
        expression=expr,
        formation=formation,
        universe=[s.key for s in stocks],
    )

    # 2. Multiple suspects ---------------------------------------------------
    stocks = [
        _stock(f"C{i}", _growth(n, 1.01 * (1 + 0.002 * i)))
        for i in range(6)
    ]
    stocks.append(
        _stock(
            "S1",
            _growth(n, 1.25),
            events=((flag_session - 3, "split", 1.25),),
            flags=(flag_session,),
        )
    )
    stocks.append(
        _stock(
            "S2",
            _growth(n, 0.80),
            events=((flag_session - 3, "dividend", 0.80),),
            flags=(flag_session,),
        )
    )
    cases["multiple_suspects"] = _union_world(
        n=n,
        stocks=stocks,
        expression=expr,
        formation=formation,
        universe=[s.key for s in stocks],
    )

    # 3. Formation endpoint (s = f) -----------------------------------------
    stocks = [
        _stock(f"C{i}", _growth(n, 1.01 * (1 + 0.002 * i)))
        for i in range(8)
    ]
    stocks.append(
        _stock(
            "SUS",
            _growth(n, 1.30),
            events=((formation, "split", 1.30),),
            flags=(formation,),
        )
    )
    cases["formation_endpoint"] = _union_world(
        n=n,
        stocks=stocks,
        expression=expr,
        formation=formation,
        universe=[s.key for s in stocks],
    )

    # 4. Ending endpoint (s = next(e)) --------------------------------------
    stocks = [
        _stock(f"C{i}", _growth(n, 1.01 * (1 + 0.002 * i)))
        for i in range(8)
    ]
    stocks.append(
        _stock(
            "SUS",
            _growth(n, 1.30),
            events=((formation + 1, "split", 1.30),),
            flags=(formation + 1,),
        )
    )
    cases["ending_endpoint"] = _union_world(
        n=n,
        stocks=stocks,
        expression=expr,
        formation=formation,
        universe=[s.key for s in stocks],
    )

    # 5. Interior close cancellation ----------------------------------------
    stocks = [
        _stock(f"C{i}", _growth(n, 1.01 * (1 + 0.002 * i)))
        for i in range(8)
    ]
    stocks.append(
        _stock(
            "SUS",
            _growth(n, 1.30),
            events=((flag_session - 3, "split", 1.30),),
            flags=(flag_session,),
        )
    )
    cases["interior_close_cancellation"] = _union_world(
        n=n,
        stocks=stocks,
        expression=expr,
        formation=formation,
        universe=[s.key for s in stocks],
    )

    # 6. MISDATED paired corporate action -----------------------------------
    stocks = [
        _stock(f"C{i}", _growth(n, 1.01 * (1 + 0.002 * i)))
        for i in range(8)
    ]
    stocks.append(
        _stock(
            "SUS",
            _growth(n, 1.30),
            events=((flag_session - 3, "split", 1.30),),
            flags=(flag_session,),
        )
    )
    cases["misdated_paired_ca"] = _union_world(
        n=n,
        stocks=stocks,
        expression=expr,
        formation=formation,
        universe=[s.key for s in stocks],
    )

    # 7. EMPTY label ---------------------------------------------------------
    stocks = [
        _stock(f"C{i}", _growth(n, 1.01 * (1 + 0.002 * i)))
        for i in range(8)
    ]
    stocks.append(
        _stock(
            "TERM",
            _growth(n, 1.30),
            listed_end=formation,
            flags=(),
        )
    )
    cases["empty_label"] = _union_world(
        n=n,
        stocks=stocks,
        expression=expr,
        formation=formation,
        universe=[s.key for s in stocks],
    )

    # 8. TRI prefix cancellation --------------------------------------------
    stocks = [
        _stock(f"C{i}", _growth(n, 1.01 * (1 + 0.002 * i)))
        for i in range(8)
    ]
    cases["tri_prefix_cancellation"] = _union_world(
        n=n,
        stocks=stocks,
        expression=expr,
        formation=formation,
        universe=[s.key for s in stocks],
    )

    # 9. Simple assignment movement -----------------------------------------
    stocks = [
        _stock(f"C{i}", _growth(n, 1.01 * (1 + 0.004 * i)))
        for i in range(8)
    ]
    stocks.append(
        _stock(
            "SUS",
            _growth(n, 1.05),
            events=((flag_session - 3, "split", 1.05),),
            flags=(flag_session,),
        )
    )
    cases["simple_assignment_movement"] = _union_world(
        n=n,
        stocks=stocks,
        expression=expr,
        formation=formation,
        universe=[s.key for s in stocks],
        cost=0.001,
        n_groups=3,
        interior_points=2,
        universes={
            formation - 1: [s.key for s in stocks],
            formation: [s.key for s in stocks],
        },
    )

    # 10. H4/H5 clean-clean crossing ----------------------------------------
    std_expr = Expr("std_x_rank", k1=4, k2=1, w_s=3, w_l=6)
    stocks = [
        _stock(f"C{i}", _growth(n, 1.01 * (1 + 0.03 * i)))
        for i in range(8)
    ]
    stocks.append(
        _stock(
            "SUS",
            _growth(n, 1.05),
            events=((flag_session - 3, "split", 1.05),),
            flags=(flag_session,),
        )
    )
    cases["h4h5_clean_clean_crossing"] = _union_world(
        n=n,
        stocks=stocks,
        expression=std_expr,
        formation=formation,
        universe=[s.key for s in stocks],
        interior_points=2,
    )

    # 11. Suspect group-boundary crossing -----------------------------------
    cases["suspect_group_boundary_crossing"] = _union_world(
        n=n,
        stocks=[
            _stock(f"C{i}", _growth(n, 1.01 * (1 + 0.006 * i)))
            for i in range(8)
        ]
        + [
            _stock(
                "SUS",
                _growth(n, 1.02),
                events=((flag_session - 3, "split", 1.02),),
                flags=(flag_session,),
            )
        ],
        expression=std_expr,
        formation=formation,
        universe=[f"C{i}" for i in range(8)] + ["SUS"],
        n_groups=3,
        interior_points=2,
    )

    # 12. Volume-rank movement ----------------------------------------------
    stocks = [
        _stock(
            f"C{i}",
            _growth(n, 1.01 * (1 + 0.01 * i)),
            volumes=[1.0e6 * (1 + 0.3 * i)] * n,
        )
        for i in range(8)
    ]
    stocks.append(
        _stock(
            "SUS",
            _growth(n, 1.05),
            volumes=[2.0e6 * (1 + 0.1 * j % 3) for j in range(n)],
            events=((flag_session - 3, "split", 1.05),),
            flags=(flag_session,),
        )
    )
    cases["volume_rank_movement"] = _union_world(
        n=n,
        stocks=stocks,
        expression=std_expr,
        formation=formation,
        universe=[s.key for s in stocks],
        interior_points=2,
    )

    # 13. Coupled M/V uncertainty -------------------------------------------
    cases["coupled_mv_uncertainty"] = _union_world(
        n=n,
        stocks=[
            _stock(
                f"C{i}",
                _growth(n, 1.01 * (1 + 0.01 * i)),
                volumes=[1.0e6 * (1 + 0.2 * i)] * n,
            )
            for i in range(7)
        ]
        + [
            _stock(
                "SUS",
                _growth(n, 1.05),
                volumes=[2.5e6] * n,
                events=((flag_session - 3, "split", 1.05),),
                flags=(flag_session,),
            )
        ],
        expression=std_expr,
        formation=formation,
        universe=[f"C{i}" for i in range(7)] + ["SUS"],
        interior_points=2,
    )

    # 14. Ties ---------------------------------------------------------------
    stocks = [
        _stock(f"C{i}", [100.0] * n) for i in range(6)
    ]
    stocks.append(
        _stock(
            "SUS",
            [100.0] * n,
            events=((flag_session - 3, "split", 1.20),),
            flags=(flag_session,),
        )
    )
    cases["ties"] = _union_world(
        n=n,
        stocks=stocks,
        expression=Expr("ratio", k1=2, k2=1),
        formation=formation,
        universe=[s.key for s in stocks],
        interior_points=1,
    )

    # 15. sigma = 0 ----------------------------------------------------------
    stocks = [_stock(f"C{i}", [100.0] * n) for i in range(6)]
    stocks.append(
        _stock(
            "SUS",
            [100.0] * n,
            events=((flag_session - 3, "split", 1.20),),
            flags=(flag_session,),
        )
    )
    cases["sigma_zero"] = _union_world(
        n=n,
        stocks=stocks,
        expression=std_expr,
        formation=formation,
        universe=[s.key for s in stocks],
    )

    # 16. NaN ----------------------------------------------------------------
    nan_closes: list[float | None] = _growth(n, 1.01)
    nan_closes[formation - 2] = None
    stocks = [
        _stock(f"C{i}", _growth(n, 1.01 * (1 + 0.01 * i)))
        for i in range(7)
    ]
    stocks.append(_stock("NAN", nan_closes))
    stocks.append(
        _stock(
            "SUS",
            _growth(n, 1.05),
            events=((flag_session - 3, "split", 1.05),),
            flags=(flag_session,),
        )
    )
    cases["nan"] = _union_world(
        n=n,
        stocks=stocks,
        expression=Expr("ratio", k1=2, k2=1),
        formation=formation,
        universe=[s.key for s in stocks],
        interior_points=0,
    )

    # 17. Winsorization block-condition failure ------------------------------
    stocks = [
        _stock(f"C{i}", _growth(n, 1.01 * (1 + 0.01 * i)))
        for i in range(8)
    ]
    stocks.append(
        _stock(
            "SUS",
            _growth(n, 1.05),
            events=((flag_session - 3, "split", 1.05),),
            flags=(flag_session,),
        )
    )
    cases["winsor_block_failure"] = _union_world(
        n=n,
        stocks=stocks,
        expression=expr,
        formation=formation,
        universe=[s.key for s in stocks],
        q=0.30,
        n_groups=5,
        interior_points=2,
    )

    # 18. Turnover upper bound ----------------------------------------------
    stocks = [
        _stock(f"C{i}", _growth(n, 1.01 * (1 + 0.004 * i)))
        for i in range(8)
    ]
    stocks.append(
        _stock(
            "SUS",
            _growth(n, 1.05),
            events=((flag_session - 3, "split", 1.05),),
            flags=(flag_session,),
        )
    )
    cases["turnover_upper_bound"] = _union_world(
        n=n,
        stocks=stocks,
        expression=expr,
        formation=formation,
        universe=[s.key for s in stocks],
        cost=0.001,
        n_groups=3,
        interior_points=2,
        universes={
            formation - 1: [s.key for s in stocks],
            formation: [s.key for s in stocks],
        },
    )

    # Full-Campaign-window worlds (>= 300 sessions, 252/126/21/231/105) ------
    rates = [1.0005 * (1 + 0.0004 * i) for i in range(10)]
    cases["full_window_simple"] = _campaign_window_world(
        n=310,
        expression=Expr("ratio", k1=252, k2=21),
        rates=rates,
    )
    cases["full_window_std_x_rank"] = _campaign_window_world(
        n=310,
        expression=Expr("std_x_rank", k1=252, k2=21, w_s=21, w_l=252),
        rates=rates,
    )
    cases["full_window_ratio126"] = _campaign_window_world(
        n=310,
        expression=Expr("ratio", k1=126, k2=21),
        rates=rates,
    )
    cases["full_window_volscaled231"] = _campaign_window_world(
        n=310,
        expression=Expr("volscaled", k1=252, k2=21, w=231),
        rates=rates,
    )
    cases["full_window_volscaled105"] = _campaign_window_world(
        n=310,
        expression=Expr("volscaled", k1=126, k2=21, w=105),
        rates=rates,
    )
    cases["full_window_neg_short"] = _campaign_window_world(
        n=310,
        expression=Expr("neg_short", k1=21),
        rates=rates,
    )
    return cases


CASES: dict[str, World] = _build_cases()
