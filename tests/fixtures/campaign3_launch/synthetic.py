"""Synthetic C3-L fixtures: fakes for every production loader.

Authority: the frozen ``docs/campaign3_p6_implementation_spec.md`` (task C3-L,
Erratum P6-E2).  Nothing here reads a production artifact, ``data_store``, the
reserve, holdout, Knowledge-PIT or network: every loader is a small in-memory
fake and the K-PIT is a caller-supplied disposable copy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping, Sequence

import pandas as pd

from smart_beta.campaign import campaign3_run as R
from smart_beta.evaluation.partition import Fold, FoldRole, Partition

__all__ = [
    "FAKE_PANEL",
    "fake_partition",
    "FakeCell",
    "FakeDiagnostics",
    "FakeSeriesRecord",
    "FakeSeriesExport",
    "fake_panel",
    "fake_bindings",
    "fake_event_layer_loader",
    "fake_inputs_loader",
    "fake_overlay_loader",
    "fake_adapter_factory",
    "fake_registry_loader",
    "FakeControlEvaluator",
    "fake_diagnostics_builder",
    "fake_export_series_fn",
]


def fake_partition() -> Partition:
    """A synthetic rebuild of the frozen Campaign-1/2 partition.

    The fold ranges and split rule are the frozen literals, so the computed
    ``partition_id`` is ``b4b56dbf…`` without importing ``campaign1``.
    """
    return Partition(
        folds=(
            Fold(
                FoldRole.IS,
                pd.Timestamp("2016-01-29"),
                pd.Timestamp("2020-07-31"),
            ),
            Fold(
                FoldRole.OOS,
                pd.Timestamp("2020-07-31"),
                pd.Timestamp("2022-01-31"),
            ),
            Fold(
                FoldRole.HOLDOUT,
                pd.Timestamp("2022-01-31"),
                pd.Timestamp("2023-07-03"),
            ),
        ),
        split_rule="campaign1-is-oos-sealed-v1",
    )


def fake_panel() -> pd.DataFrame:
    """A schema-valid, empty fold panel (the declaration hashes it)."""
    return pd.DataFrame(
        {
            "date": pd.Series([], dtype="datetime64[ns]"),
            "stock_id": pd.Series([], dtype="string"),
            "value": pd.Series([], dtype="float64"),
        }
    )


#: A panel value the tests can share (never mutated).
FAKE_PANEL = fake_panel


def fake_bindings(*_args: Any, **_kwargs: Any) -> R.Campaign3Bindings:
    """The frozen Campaign-3 bindings (the only bindings that verify)."""
    return R.Campaign3Bindings(
        dataset_id=R.EXPECTED_DATASET_ID,
        dataset_identity_hash=R.EXPECTED_DATASET_IDENTITY_HASH,
        pq_id=R.EXPECTED_PQ_ID,
        pq_hash=R.EXPECTED_PQ_HASH,
        ti_id=R.EXPECTED_TI_ID,
        ti_hash=R.EXPECTED_TI_HASH,
        acq_id=R.EXPECTED_ACQ_ID,
        ev_id=R.EXPECTED_EV_ID,
        partition_id=R.EXPECTED_PARTITION_ID,
    )


def fake_overlay_loader(_root: Any) -> Any:
    return object()


def fake_inputs_loader(_root: Any) -> Mapping[str, Any]:
    return {}


def fake_event_layer_loader(_root: Any, **_kwargs: Any) -> Any:
    return object()


def fake_adapter_factory(**_kwargs: Any) -> Any:
    """A stand-in for ``RealInputAdapter`` exposing only ``panel_of``."""
    return type(
        "FakeAdapter",
        (),
        {"panel_of": staticmethod(lambda spec, universe, fold: fake_panel())},
    )()


def fake_registry_loader(_path: Any) -> tuple[Any, tuple[Mapping[str, Any], ...]]:
    return object(), ()


# ---------------------------------------------------------------------------
# fake T-A diagnostics / pqv2_series export
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FakeCell:
    cell: str


@dataclass(frozen=True)
class FakeDiagnostics:
    diagnostics_hash: str = "d" * 64
    declaration_hash: str = "c" * 64
    metric_basis: str = "TURNOVER_COST_ADJUSTED"
    not_assessed: str | None = None
    certified_class: str | None = "EVENT_SIMPLE"
    certified_hypothesis: str | None = None
    event_scenarios: tuple[str, ...] = ("K0", "K1")
    cells: tuple[FakeCell, ...] = (
        FakeCell("B-T0-K0"),
        FakeCell("B-T0-K1"),
        FakeCell("B-TS-K0"),
        FakeCell("B-TS-K1"),
        FakeCell("L-T0-K0"),
        FakeCell("L-T0-K1"),
        FakeCell("L-TS-K0"),
        FakeCell("L-TS-K1"),
    )
    estimator_identity: str | None = "long_short_mean_tstat_v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "declaration_hash": self.declaration_hash,
            "diagnostics_hash": self.diagnostics_hash,
            "metric_basis": self.metric_basis,
            "not_assessed": self.not_assessed,
            "certified_class": self.certified_class,
            "certified_hypothesis": self.certified_hypothesis,
            "event_scenarios": list(self.event_scenarios),
            "cells": [cell.cell for cell in self.cells],
        }


@dataclass(frozen=True)
class FakeSeriesRecord:
    cell: str
    periods: tuple[date, ...]
    b_series: tuple[float, ...]
    l_series: tuple[float, ...]
    gross_series: tuple[float, ...]
    net_series: tuple[float, ...]
    assessed: bool = True
    status: str | None = None
    reason: str | None = None
    declaration_hash: str = "c" * 64
    estimand: str = "B"
    scenario: str = "T0"
    event_scenario: str | None = "K0"


@dataclass(frozen=True)
class FakeSeriesExport:
    declaration_hash: str
    diagnostics_hash: str
    estimator: str
    records: tuple[FakeSeriesRecord, ...]

    def get(self, cell: str) -> FakeSeriesRecord | None:
        for record in self.records:
            if record.cell == cell:
                return record
        return None


def fake_diagnostics_builder(*_args: Any, **_kwargs: Any) -> FakeDiagnostics:
    return FakeDiagnostics()


def fake_export_series_fn(*_args: Any, **_kwargs: Any) -> FakeSeriesExport:
    return FakeSeriesExport(
        declaration_hash="c" * 64,
        diagnostics_hash="d" * 64,
        estimator="long_short_mean_tstat_v1",
        records=tuple(
            FakeSeriesRecord(
                cell=cell.cell,
                periods=(date(2020, 1, 31), date(2020, 2, 29)),
                b_series=(0.01, 0.02),
                l_series=(0.015, 0.025),
                gross_series=(0.02, 0.03),
                net_series=(0.01, 0.02),
            )
            for cell in FakeDiagnostics().cells
        ),
    )


@dataclass
class FakeControlEvaluator:
    """A recording stand-in for the real fixed-spec evaluation."""

    diagnostics: Any = field(default_factory=FakeDiagnostics)
    export: Any = field(default_factory=fake_export_series_fn)
    calls: int = 0
    specs: list[Any] = field(default_factory=list)

    def __call__(self, *_args: Any, **_kwargs: Any) -> tuple[Any, Any]:
        self.calls += 1
        if len(_args) >= 2:
            self.specs.append(_args[1])
        return self.diagnostics, self.export
