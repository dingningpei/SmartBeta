"""Campaign-4 M1 -- A4 gate, family cap and A6 restatement (task C4M1-1).

Authority: ``worker_tasks/campaign4_m1/task-c4m1-1-contract-core.md``
section 3, ``docs/campaign4_m1a_contract_calibration_draft.md`` section 1 (A4 /
A6) and the frozen ``campaign4-m1-plan.md`` binding constraint 2
(training-window firewall).

The gate never re-implements the exposure arithmetic: it imports
:func:`smart_beta.diagnostics.direction_exposure.panel_values_by_date` and
:func:`~smart_beta.diagnostics.direction_exposure.mean_spearman` and restricts
them to formation dates strictly before ``window_cut``.

A4
--
``DiversityGate.check`` processes proposals in **proposal order** (the gate is
order-dependent, exactly like the C4M0-13 ``a4_replay``).  A proposal is a
``NEAR_DUPLICATE`` when its mean cross-sectional Spearman correlation with any
already-admitted proposal exceeds ``threshold`` in absolute value (a negated
copy is a duplicate too).  A proposal is ``FAMILY_CAP`` when the count of
proposals that declared its family -- admitted **or** rejected -- would exceed
``family_cap``.  Otherwise it is ``ADMIT``.

A6
--
``DiversityGate.restatement`` reports the maximum absolute exposure ``|E|`` of a
candidate against the five frozen C4M0-13 reference drivers
(:data:`direction_exposure.DRIVER_IDS`).  The driver panels are supplied by the
caller (they are built from the frozen driver specs, which are imported from
:mod:`smart_beta.diagnostics.direction_exposure`).  A6 is **information only**:
it never rejects.

Nothing here reads a real dataset, opens a network path or modifies a sealed
file.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import pandas as pd

from smart_beta.campaign4.contract import Campaign4Error, C4FirewallError
from smart_beta.diagnostics import direction_exposure as _de

__all__ = [
    "ADMIT",
    "NEAR_DUPLICATE",
    "FAMILY_CAP",
    "DEFAULT_THRESHOLD",
    "DEFAULT_FAMILY_CAP",
    "DiversityGateError",
    "Decision",
    "DiversityGate",
]


#: The three A4 decisions.
ADMIT = "ADMIT"
NEAR_DUPLICATE = "NEAR_DUPLICATE"
FAMILY_CAP = "FAMILY_CAP"

#: The frozen A4 thresholds (task spec section 3).
DEFAULT_THRESHOLD = 0.90
DEFAULT_FAMILY_CAP = 30


class DiversityGateError(Campaign4Error):
    """The A4/A6 gate was used outside its frozen contract."""


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    return pd.Timestamp(value).date()


@dataclass(frozen=True)
class Decision:
    """One A4 decision.

    ``kind`` is :data:`ADMIT`, :data:`NEAR_DUPLICATE` or :data:`FAMILY_CAP`.
    ``other_key`` / ``rho`` are set only for a near-duplicate; ``family`` is set
    only for a family-cap rejection.
    """

    kind: str
    other_key: str | None = None
    rho: float | None = None
    family: str | None = None

    @property
    def admitted(self) -> bool:
        return self.kind == ADMIT

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.kind,
            "other_key": self.other_key,
            "rho": self.rho,
            "family": self.family,
        }


class DiversityGate:
    """The order-dependent A4 gate plus the A6 restatement report."""

    def __init__(
        self,
        *,
        window_cut: date | str | datetime,
        threshold: float = DEFAULT_THRESHOLD,
        family_cap: int = DEFAULT_FAMILY_CAP,
        driver_panels: Mapping[str, Any] | None = None,
    ) -> None:
        self._window_cut = _as_date(window_cut)
        self._threshold = float(threshold)
        self._family_cap = int(family_cap)
        if self._family_cap < 1:
            raise DiversityGateError("family_cap must be a positive integer")
        self._admitted: dict[str, dict[date, dict[str, float | None]]] = {}
        self._order: list[str] = []
        self._family_counts: Counter[str] = Counter()
        self._driver_panels: Mapping[str, Any] = (
            {} if driver_panels is None else dict(driver_panels)
        )

    # -- read-only views -------------------------------------------------
    @property
    def window_cut(self) -> date:
        return self._window_cut

    @property
    def threshold(self) -> float:
        return self._threshold

    @property
    def family_cap(self) -> int:
        return self._family_cap

    @property
    def admitted_keys(self) -> tuple[str, ...]:
        return tuple(self._order)

    def family_count(self, family: str) -> int:
        return int(self._family_counts.get(family, 0))

    # -- firewall --------------------------------------------------------
    def assert_period(self, period: date | str | datetime) -> date:
        """Fail closed unless ``period`` is strictly before ``window_cut``."""
        value = _as_date(period)
        if value >= self._window_cut:
            raise C4FirewallError(
                f"period {value.isoformat()} is at/after the window cut "
                f"{self._window_cut.isoformat()}; the A4 gate is training-window "
                "only"
            )
        return value

    # -- orientation / filtering ----------------------------------------
    def _restricted(
        self, panel: Any, sign: int
    ) -> dict[date, dict[str, float | None]]:
        """Orient ``panel`` and keep only formations strictly before the cut."""
        values = _de.panel_values_by_date(panel, sign=int(sign))
        return {
            formation: buckets
            for formation, buckets in values.items()
            if formation < self._window_cut
        }

    # -- the A4 gate -----------------------------------------------------
    def check(
        self,
        candidate_key: str,
        panel: Any,
        family: str,
        *,
        sign: int = 1,
        period: date | str | datetime | None = None,
    ) -> Decision:
        """Process one proposal in proposal order.

        ``period``, when supplied, is the proposal's own evaluation period and
        is firewall-checked before any exposure is computed.
        """

        if period is not None:
            self.assert_period(period)
        if candidate_key in self._admitted:
            # Idempotent re-check of an already-admitted proposal.
            return Decision(kind=ADMIT)

        # The family count includes every proposal that declared the family,
        # admitted or rejected.
        self._family_counts[family] += 1
        if self._family_counts[family] > self._family_cap:
            return Decision(kind=FAMILY_CAP, family=family)

        oriented = self._restricted(panel, sign)
        for other in self._order:
            rho = _de.mean_spearman(oriented, self._admitted[other])
            if rho is not None and abs(rho) > self._threshold:
                return Decision(
                    kind=NEAR_DUPLICATE, other_key=other, rho=float(rho)
                )
        self._admitted[candidate_key] = oriented
        self._order.append(candidate_key)
        return Decision(kind=ADMIT)

    # -- the A6 restatement report --------------------------------------
    def restatement(
        self,
        panel: Any,
        driver_panels: Mapping[str, Any] | None = None,
        *,
        sign: int = 1,
        period: date | str | datetime | None = None,
    ) -> tuple[str | None, float | None]:
        """Return ``(driver_id, max_abs_E)`` of the five reference drivers.

        The five driver specs / ids are the frozen ones imported from
        :mod:`smart_beta.diagnostics.direction_exposure`; the driver panels are
        passed in (per-construction or per-call).  A driver that is not
        supplied is skipped.  When no driver yields a finite exposure the
        result is ``(None, None)``.
        """

        if period is not None:
            self.assert_period(period)
        panels = self._driver_panels if driver_panels is None else driver_panels
        if not panels:
            raise DiversityGateError(
                "restatement requires the five reference-driver panels"
            )
        candidate = self._restricted(panel, sign)
        best_driver: str | None = None
        best_abs: float | None = None
        for driver_id in _de.DRIVER_IDS:
            driver_panel = panels.get(driver_id)
            if driver_panel is None:
                continue
            driver_values = self._restricted(driver_panel, 1)
            rho = _de.mean_spearman(candidate, driver_values)
            if rho is None:
                continue
            magnitude = abs(float(rho))
            if best_abs is None or magnitude > best_abs:
                best_driver = driver_id
                best_abs = magnitude
        return best_driver, best_abs
