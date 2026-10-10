"""Oracle-local P-Q v2.4 fold-end worlds (PQ24-G).

This module is **oracle-local**: it imports only the sibling oracle modules.
It never imports ``smart_beta`` or any production ``pqv2_`` module, and it
reads no dataset artifact, reserve file, holdout or provider.

The P-Q v2.4 amendment (``docs/discovery_dataset_v1_2_pq_v2_4_fold_firewall.md``
§2) parameterizes the temporal firewall by a declared :class:`~pqv2_oracle.oracle.Fold`
with end session ``E``:

* a flag with ``next(s) > E`` or a ``+5`` MISDATED window crossing ``E`` is
  UNKNOWN -- with cause ``PQV2_RESERVE_UNKNOWN`` on the development fold and
  the new ``PQV2_FOLD_END_UNKNOWN`` on the reserve fold;
* a label whose observed endpoint ``e* == E`` is UNKNOWN with the same cause;
* an event with ``K(e) <= E`` is admissible; ``K(e) > E`` is a firewall
  incident (``PQV2_EVENT_RESERVE``).

The default oracle path (``fold is None``, i.e. the development fold with no
explicit synthetic session firewall) is exactly the v2.3 path.  These worlds
carry an explicit fold end so the boundary itself is exercised; the reserve
fold additionally supports the brute-force envelope checks (membership subset,
``L >= min``, turnover ``<=`` max).
"""

from __future__ import annotations

from datetime import time
from typing import Any, Mapping, Sequence

from . import ast as A
from . import oracle as O

MORNING = time(10, 0)
PRECLOSE = time(15, 50)

#: The reserve fold used by the boundary worlds (synthetic sessions).
RESERVE_FOLD = O.Fold("reserve", 18, 25)


def _growth(n: int, rate: float, start: float = 100.0) -> list[float]:
    return [start * (rate**i) for i in range(n)]


def _fold_event_world(
    *,
    fold: O.Fold,
    n: int,
    formation: int,
    k_session: int | None,
    flag_session: int | None = None,
    acceptance_et: time | None = MORNING,
    expression_ast: Any | None = None,
    event_key: str = "EV",
    n_controls: int = 6,
    n_groups: int = 5,
    cost: float = 0.0,
) -> O.World:
    """One event key, clean controls and an optional flag, under *fold*."""
    stocks = [
        O._stock(f"C{i}", _growth(n, 1.01 * (1 + 0.003 * i)))
        for i in range(n_controls)
    ]
    flags = (flag_session,) if flag_session is not None else ()
    stocks.append(O._stock(event_key, _growth(n, 1.02), flags=flags))
    events: Mapping[str, Sequence[O.EventDatum]] = {
        event_key: (
            O.EventDatum(k_session=k_session, acceptance_et=acceptance_et),
        )
    }
    if expression_ast is None:
        expression_ast = A.Field(A.EVENT_ROLE_EAR)
    return O._union_world(
        n=n,
        stocks=stocks,
        expression_ast=expression_ast,
        formation=formation,
        universe=[s.key for s in stocks],
        event_calendars=events,
        fold=fold,
        n_groups=n_groups,
        cost=cost,
    )


def _multi_fold_world(
    *,
    fold: O.Fold,
    n: int,
    formations: Sequence[int],
    specs: Sequence[Mapping[str, Any]],
    n_controls: int = 4,
    n_groups: int = 2,
    q: float = 0.2,
    cost: float = 0.0,
    interior_points: int = 1,
) -> O.World:
    """A multi-key fold world (each key optional flags/events)."""
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
        fold=fold,
        n_groups=n_groups,
        q=q,
        cost=cost,
        interior_points=interior_points,
    )


# ---------------------------------------------------------------------------
# boundary worlds (firewall §2)
# ---------------------------------------------------------------------------
def development_fold_label_endpoint(*, end: int = 25) -> O.World:
    """A label whose observed endpoint is the development fold end."""
    return _fold_event_world(
        fold=O.Fold("development", None, end),
        n=30,
        formation=end - 1,
        k_session=20,
    )


def development_fold_event_beyond(*, end: int = 25) -> O.World:
    """An event with ``K > E`` under the development fold (incident)."""
    return _fold_event_world(
        fold=O.Fold("development", None, end),
        n=30,
        formation=end + 3,
        k_session=end + 1,
    )


def reserve_fold_label_endpoint(*, start: int = 18, end: int = 25) -> O.World:
    """A label whose observed endpoint is the reserve fold end."""
    return _fold_event_world(
        fold=O.Fold("reserve", start, end),
        n=30,
        formation=end - 1,
        k_session=20,
    )


def reserve_fold_misdated_crossing(*, start: int = 18, end: int = 25) -> O.World:
    """A reaching flag whose ``+5`` MISDATED window crosses the reserve end."""
    return _fold_event_world(
        fold=O.Fold("reserve", start, end),
        n=30,
        formation=end - 5,
        k_session=10,
        flag_session=end - 3,
    )


def reserve_fold_event_within(*, start: int = 18, end: int = 25) -> O.World:
    """An event with ``K == E`` under the reserve fold (admissible)."""
    return _fold_event_world(
        fold=O.Fold("reserve", start, end),
        n=30,
        formation=end + 2,
        k_session=end,
    )


def reserve_fold_event_beyond(*, start: int = 18, end: int = 25) -> O.World:
    """An event with ``K > E`` under the reserve fold (incident)."""
    return _fold_event_world(
        fold=O.Fold("reserve", start, end),
        n=30,
        formation=end + 3,
        k_session=end + 1,
    )


def brute_force_reserve_fold(
    *, n: int = 45, formations: Sequence[int] = (30, 33, 38), end: int = 35
) -> O.World:
    """A reserve-fold correction grid touching and missing the event window.

    ``EV1`` carries a pre-close ``B_e`` event (so K0 and K1 differ) and a
    resolved flag; ``EV3`` an event with ``K > E`` (a fold incident);
    ``EV6`` a flag whose ``+5`` window crosses ``E`` (UNKNOWN under the
    reserve fold).  Three formations make the turnover bound non-trivial.
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
            "flags": (),
        },
        {
            "key": "EV3",
            "rate": 1.04,
            "events": (O.EventDatum(k_session=end + 1, acceptance_et=MORNING),),
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
        {
            "key": "EV6",
            "rate": 1.025,
            "events": (O.EventDatum(k_session=27, acceptance_et=MORNING),),
            "flags": (end - 1,),
        },
    ]
    return _multi_fold_world(
        fold=O.Fold("reserve", 31, end),
        n=n,
        formations=formations,
        specs=specs,
        n_groups=2,
        cost=0.001,
        interior_points=0,
    )


def fold_cases() -> dict[str, O.World]:
    """The P-Q v2.4 fold-end worlds, deterministically rebuilt."""
    return {
        "development_fold_label_endpoint": development_fold_label_endpoint(),
        "development_fold_event_beyond": development_fold_event_beyond(),
        "reserve_fold_label_endpoint": reserve_fold_label_endpoint(),
        "reserve_fold_misdated_crossing": reserve_fold_misdated_crossing(),
        "reserve_fold_event_within": reserve_fold_event_within(),
        "reserve_fold_event_beyond": reserve_fold_event_beyond(),
        "brute_force_reserve_fold": brute_force_reserve_fold(),
    }


FOLD_CASES: dict[str, O.World] = fold_cases()
