"""Oracle-local P-Q v2.3 event worlds and the E§7 adversarial cases.

This module is **oracle-local**: it imports only ``numpy`` and the sibling
oracle modules.  It never imports ``smart_beta`` or any production ``pqv2_``
module.  It constructs synthetic per-key event calendars and evaluates the
E§3 ``EVENT_G`` primitives with the independent kernels in
:mod:`pqv2_oracle.oracle` / :mod:`pqv2_oracle.ast`.

Everything here is offline and synthetic: no production artifact, reserve
file, holdout or provider is read.  The worlds use integer session indices as
dates; ``acceptance_et`` is the index-header wall clock in
America/New_York (the M1b time source), so only its time-of-day drives the
asymmetric ``[15:45, 16:00)`` pre-close boundary.
"""

from __future__ import annotations

from datetime import time
from typing import Any, Mapping, Sequence

from . import ast as A
from . import oracle as O

MORNING = time(10, 0)
PRECLOSE = time(15, 50)
AT_1605 = time(16, 5)


def _growth(n: int, rate: float, start: float = 100.0) -> list[float]:
    return [start * (rate**i) for i in range(n)]


def _event_world(
    *,
    n: int,
    formation: int,
    k_session: int | None,
    acceptance_et: time | None = MORNING,
    filing_date: int | None = None,
    flag_session: int | None = None,
    expression_ast: Any | None = None,
    extra_events: Sequence[O.EventDatum] = (),
    key_status: str | None = None,
    reserve_start: int | None = None,
    event_key: str = "EV",
    n_controls: int = 6,
    n_groups: int = 5,
    cost: float = 0.0,
) -> O.World:
    """A world with one event key, clean controls and an optional flag."""
    stocks = [
        O._stock(f"C{i}", _growth(n, 1.01 * (1 + 0.003 * i)))
        for i in range(n_controls)
    ]
    flags = (flag_session,) if flag_session is not None else ()
    stocks.append(O._stock(event_key, _growth(n, 1.02), flags=flags))
    events = {
        event_key: (
            O.EventDatum(
                k_session=k_session,
                acceptance_et=acceptance_et,
                filing_date=filing_date,
            ),
            *extra_events,
        )
    }
    statuses = {event_key: key_status} if key_status is not None else None
    if expression_ast is None:
        expression_ast = A.Field(A.EVENT_ROLE_EAR)
    return O._union_world(
        n=n,
        stocks=stocks,
        expression_ast=expression_ast,
        formation=formation,
        universe=[s.key for s in stocks],
        event_calendars=events,
        key_event_status=statuses,
        reserve_start=reserve_start,
        n_groups=n_groups,
        cost=cost,
    )


def _multi_event_world(
    *,
    n: int,
    formations: Sequence[int],
    specs: Sequence[Mapping[str, Any]],
    n_controls: int = 4,
    n_groups: int = 2,
    q: float = 0.2,
    cost: float = 0.0,
    interior_points: int = 1,
) -> O.World:
    """A world with several event keys (each optional flags/events)."""
    stocks = [
        O._stock(f"C{i}", _growth(n, 1.01 * (1 + 0.003 * i)))
        for i in range(n_controls)
    ]
    calendars: dict[str, tuple[O.EventDatum, ...]] = {}
    for index, spec in enumerate(specs):
        key = str(spec["key"])
        rate = float(spec.get("rate", 1.02 + 0.005 * index))
        flags = tuple(spec.get("flags", ()))
        closes = spec.get("closes")
        if closes is None:
            closes = _growth(n, rate)
        stocks.append(O._stock(key, closes, flags=flags))
        calendars[key] = tuple(spec["events"])
    universe = [s.key for s in stocks]
    return O._union_world(
        n=n,
        stocks=stocks,
        expression_ast=A.Field(A.EVENT_ROLE_EAR),
        formation=formations[-1],
        universe=universe,
        universes={f: tuple(universe) for f in formations},
        event_calendars=calendars,
        n_groups=n_groups,
        q=q,
        cost=cost,
        interior_points=interior_points,
    )


def event_inside_suspect_window() -> O.World:
    """A flag at ``K+1`` (inside the ``(K-1, K+1]`` event window)."""
    return _event_world(
        n=30, formation=25, k_session=18, flag_session=19
    )


def event_outside_suspect_window() -> O.World:
    """A flag at ``K+3`` (outside the event window)."""
    return _event_world(
        n=30, formation=25, k_session=18, flag_session=21
    )


def lookback_edge_504() -> O.World:
    """An event exactly 504 sessions before ``date(f-1)``."""
    return _event_world(n=520, formation=519, k_session=14)


def lookback_beyond_505() -> O.World:
    """An event 505 sessions before ``date(f-1)``: no primitive."""
    return _event_world(n=520, formation=519, k_session=13)


def preclose_be_event() -> O.World:
    """A pre-close ``B_e`` event (K0 and K1 both evaluated)."""
    return _event_world(
        n=30, formation=25, k_session=18, acceptance_et=PRECLOSE
    )


def acceptance_1605() -> O.World:
    """A 16:05 acceptance: not ``B_e``, so K1 == K0."""
    return _event_world(
        n=30, formation=25, k_session=18, acceptance_et=AT_1605
    )


def identity_unmapped(status: str) -> O.World:
    """An UNMAPPED key (AMBIGUOUS / NO_FILINGS_IN_INTERVAL / NO_CANDIDATE)."""
    return _event_world(
        n=30, formation=25, k_session=18, key_status=status
    )


def identity_contradicted() -> O.World:
    """A CONTRADICTED key."""
    return _event_world(
        n=30, formation=25, k_session=18, key_status=O.CONTRADICTED
    )


def source_excluded_fpi() -> O.World:
    """An FPI key: FOREIGN_FILER, SOURCE_EXCLUDED, retained in the universe."""
    return _event_world(
        n=30, formation=25, k_session=18, key_status=O.FOREIGN_FILER
    )


def time_unavailable_null_event() -> O.World:
    """A null-time in-scope event between ``e*`` and ``date(f-1)``."""
    return _event_world(
        n=30,
        formation=25,
        k_session=18,
        extra_events=(
            O.EventDatum(k_session=None, acceptance_et=None, filing_date=22),
        ),
    )


def reserve_incident() -> O.World:
    """An event with ``K >= reserve_start`` (firewall incident)."""
    return _event_world(
        n=30, formation=25, k_session=18, reserve_start=18
    )


def brute_force_event_window(
    *, n: int = 40, formations: Sequence[int] = (30, 33)
) -> O.World:
    """A correction grid touching and missing the event windows.

    ``EV1`` carries a pre-close ``B_e`` event (so K0 and K1 differ) and a flag
    inside its event window; ``EV2`` carries a flag inside a non-``B_e``
    window; ``EV3``/``EV4`` carry events without flags.  Two formations make
    the turnover bound non-trivial.
    """
    ev1_closes = _growth(n, 1.02)
    ev1_closes[19] *= 1.5
    specs = [
        {
            "key": "EV1",
            "closes": ev1_closes,
            "events": (O.EventDatum(k_session=18, acceptance_et=PRECLOSE),),
            "flags": (19,),
        },
        {
            "key": "EV2",
            "rate": 1.03,
            "events": (O.EventDatum(k_session=20, acceptance_et=MORNING),),
            "flags": (21,),
        },
        {
            "key": "EV3",
            "rate": 1.04,
            "events": (O.EventDatum(k_session=17, acceptance_et=MORNING),),
            "flags": (),
        },
        {
            "key": "EV4",
            "rate": 1.05,
            "events": (O.EventDatum(k_session=15, acceptance_et=MORNING),),
            "flags": (),
        },
        {
            "key": "EV5",
            "rate": 1.015,
            "events": (O.EventDatum(k_session=30, acceptance_et=MORNING),),
            "flags": (),
        },
    ]
    return _multi_event_world(
        n=n,
        formations=formations,
        specs=specs,
        n_groups=2,
        cost=0.001,
    )


def event_cases() -> dict[str, O.World]:
    """The E§7 adversarial event worlds, deterministically rebuilt."""
    return {
        "event_inside_suspect_window": event_inside_suspect_window(),
        "event_outside_suspect_window": event_outside_suspect_window(),
        "lookback_edge_504": lookback_edge_504(),
        "lookback_beyond_505": lookback_beyond_505(),
        "preclose_be_event": preclose_be_event(),
        "acceptance_1605": acceptance_1605(),
        "identity_unmapped_ambiguous": identity_unmapped(O.AMBIGUOUS),
        "identity_unmapped_no_filings": identity_unmapped(
            O.NO_FILINGS_IN_INTERVAL
        ),
        "identity_unmapped_no_candidate": identity_unmapped(O.NO_CANDIDATE),
        "identity_contradicted": identity_contradicted(),
        "source_excluded_fpi": source_excluded_fpi(),
        "time_unavailable_null_event": time_unavailable_null_event(),
        "reserve_incident": reserve_incident(),
        "brute_force_event_window": brute_force_event_window(),
    }


EVENT_CASES: dict[str, O.World] = event_cases()
