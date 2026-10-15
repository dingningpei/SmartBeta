"""Synthetic C4M0-13 fixtures (offline only).

The direction audit (M0-1) and common exposure (M0-3) diagnostics consume the
persisted Campaign-3 ``generation.json`` / ``executor_state.json`` /
``screen_cache`` artifacts and the production factor panels.  This fixture
builds a tiny, fully synthetic instance of all three plus a
:class:`FakeAssembly` whose ``fast_panel_for`` returns hand-built panels, so the
whole orchestration (coding hash, firewall, classification, A4 replay, PCA,
determinism) runs end to end on ``tmp_path``.

Nothing here reads ``data_store``, a production artifact, the reserve, a
holdout, the Knowledge-PIT or the network.  The candidate specs are real P-Q
v2.3 :class:`~smart_beta.spec.factor_spec.FactorSpec` objects so their hashes
are genuine; only the panels are hand-injected (never built through the
grammar), exactly the C4M0-2 synthetic-store discipline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from smart_beta.diagnostics import direction_exposure as de
from smart_beta.spec.factor_spec import FactorSpec
from smart_beta.spec.factor_spec import from_dict as factor_spec_from_dict
from smart_beta.spec.factor_spec import to_dict as factor_spec_to_dict

#: Six synthetic monthly formations (>= 2 so Spearman is defined).
FORMATIONS: tuple[date, ...] = (
    date(2016, 1, 29),
    date(2016, 2, 29),
    date(2016, 3, 31),
    date(2016, 4, 29),
    date(2016, 5, 31),
    date(2016, 6, 30),
)

#: Forty synthetic universe members (the section 1.3 minimum is 30).
STOCKS: tuple[str, ...] = tuple(f"S{index:02d}" for index in range(40))


def panel_from_series(
    series: Mapping[str, Sequence[float]],
    *,
    formations: Sequence[date] = FORMATIONS,
) -> pd.DataFrame:
    """Build a ``(date, stock_id, value)`` panel from per-stock time series."""

    rows = []
    for stock_id, values in series.items():
        for formation, value in zip(formations, values):
            rows.append(
                {
                    "date": formation,
                    "stock_id": stock_id,
                    "value": float(value),
                }
            )
    return pd.DataFrame(rows, columns=["date", "stock_id", "value"])


def random_series(
    *,
    seed: int,
    stocks: Sequence[str] = STOCKS,
    formations: Sequence[date] = FORMATIONS,
) -> dict[str, list[float]]:
    """Deterministic independent per-stock series (distinct across seed)."""

    rng = np.random.default_rng(seed)
    return {
        stock: [float(value) for value in rng.normal(size=len(formations))]
        for stock in stocks
    }


def momentum_series(
    *,
    seed: int = 7,
    stocks: Sequence[str] = STOCKS,
    formations: Sequence[date] = FORMATIONS,
) -> dict[str, list[float]]:
    """A cross-sectionally varying momentum-like series (one value/formation)."""

    return random_series(seed=seed, stocks=stocks, formations=formations)


def negate(series: Mapping[str, Sequence[float]]) -> dict[str, list[float]]:
    return {stock: [-float(value) for value in values] for stock, values in series.items()}


def _expression_spec(spec_id: str, expression: Mapping[str, Any]) -> FactorSpec:
    base = de.build_driver_specs()[0].spec
    payload = factor_spec_to_dict(base)
    payload["id"] = spec_id
    payload["description"] = f"synthetic {spec_id}"
    payload["hypothesis"] = spec_id
    payload["expression"] = dict(expression)
    # The copied ``version`` is the base spec's content hash; drop it so the
    # parser recomputes it for this synthetic spec.
    payload.pop("version", None)
    return factor_spec_from_dict(payload)


def negated_momentum_spec() -> FactorSpec:
    """A valid FactorSpec whose expression is ``-1 * D-MOM`` (no sign field)."""

    momentum = de.build_driver_specs()[0].spec
    expression = {
        "op": "mul",
        "left": {"op": "const", "value": -1.0},
        "right": factor_spec_to_dict(momentum)["expression"],
    }
    return _expression_spec("c4m0_13_negated_momentum", expression)


def independent_spec() -> FactorSpec:
    """A valid FactorSpec independent of every driver (synthetic only)."""

    momentum = de.build_driver_specs()[0].spec
    expression = factor_spec_to_dict(momentum)["expression"]
    return _expression_spec("c4m0_13_independent", expression)


class FakeAssembly:
    """A minimal assembly surface: ``is_fold`` + ``fast_panel_for`` only.

    The real :class:`~smart_beta.campaign.campaign3_launch.ProductionAssembly`
    is replaced so the synthetic tests never evaluate the grammar; the panel is
    looked up by ``spec.id`` and returned as a copy.
    """

    def __init__(
        self,
        is_fold: Any,
        panels: Mapping[str, pd.DataFrame],
        calls: list[str] | None = None,
    ) -> None:
        self.is_fold = is_fold
        self._panels = {key: value.copy() for key, value in panels.items()}
        self.calls = calls

    def fast_panel_for(self, spec: Any, fold: Any) -> pd.DataFrame:
        if self.calls is not None:
            self.calls.append(spec.id)
        if spec.id not in self._panels:
            raise KeyError(f"no synthetic panel for spec {spec.id!r}")
        return self._panels[spec.id].copy()


# ---------------------------------------------------------------------------
# persisted-input writers
# ---------------------------------------------------------------------------


def write_export(
    path: Path,
    *,
    diagnostics_hash: str,
    net_series: Sequence[float],
    l_series: Sequence[float] | None = None,
    periods: Sequence[date] = FORMATIONS,
    cell: str = "L-T0-K0",
    event_scenario: str | None = "K0",
) -> None:
    """Write one ``<digest>.export.json`` with a single chosen record."""

    record: dict[str, Any] = {
        "cell": cell,
        "estimand": "L",
        "scenario": "T0",
        "periods": [period.isoformat() for period in periods],
        "net_series": [float(value) for value in net_series],
        "l_series": [float(value) for value in (l_series or net_series)],
        "status": None,
        "reason": None,
        "declaration_hash": "0" * 64,
    }
    if event_scenario is not None:
        record["event_scenario"] = event_scenario
    payload = {
        "schema": "campaign3-ta-export-v1",
        "diagnostics_hash": diagnostics_hash,
        "export": {
            "declaration_hash": "0" * 64,
            "diagnostics_hash": diagnostics_hash,
            "estimator": "metrics.long_short_mean_tstat",
            "records": [record],
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_coding(
    path: Path,
    coding: Mapping[str, tuple[str, str]],
) -> str:
    """Write the frozen coding document and return its SHA-256 (lowercase)."""

    import hashlib

    payload = {
        "schema": de.CODING_SCHEMA,
        "coding": {
            proposal_id: {"driver": driver, "relation": relation}
            for proposal_id, (driver, relation) in coding.items()
        },
    }
    raw = json.dumps(payload, sort_keys=True).encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


# ---------------------------------------------------------------------------
# the full run fixture
# ---------------------------------------------------------------------------


@dataclass
class RunFixture:
    assembly: FakeAssembly
    generation_path: Path
    executor_state_path: Path
    screen_cache_dir: Path
    coding_path: Path
    coding_sha256: str
    candidates: tuple[Any, ...]
    panels: dict[str, pd.DataFrame] = field(default_factory=dict)


def build_run_fixture(tmp_path: Path, is_fold: Any) -> RunFixture:
    """Assemble a complete synthetic C4M0-13 run.

    Three candidates are persisted: a momentum restatement (CONSISTENT vs
    D-MOM), its exact negation (INVERTED), and an independent factor (WEAK).
    """

    root = Path(tmp_path)
    screen_cache_dir = root / "screen_cache"
    screen_cache_dir.mkdir(parents=True, exist_ok=True)

    momentum = momentum_series()
    independent = random_series(seed=99)
    momentum_spec = de.build_driver_specs()[0].spec
    negated_spec = negated_momentum_spec()
    indep_spec = independent_spec()

    panels: dict[str, pd.DataFrame] = {}
    for driver in de.build_driver_specs():
        if driver.driver_id == "D-MOM":
            panels[driver.spec.id] = panel_from_series(momentum)
        else:
            panels[driver.spec.id] = panel_from_series(
                random_series(seed=100 + de.DRIVER_IDS.index(driver.driver_id))
            )
    panels[momentum_spec.id] = panel_from_series(momentum)
    panels[negated_spec.id] = panel_from_series(negate(momentum))
    panels[indep_spec.id] = panel_from_series(independent)

    candidates = (
        ("a" * 64, momentum_spec, "d" * 64, momentum),
        ("b" * 64, negated_spec, "e" * 64, negate(momentum)),
        ("c" * 64, indep_spec, "f" * 64, independent),
    )

    generation = {"candidates": []}
    proposals = {"proposals": []}
    for proposal_id, spec, diagnostics_hash, series in candidates:
        generation["candidates"].append(
            {
                "proposal_id": proposal_id,
                "factor_spec_hash": spec.version,
                "diagnostics_hash": diagnostics_hash,
                "sign": int(spec.sign),
                "assessed": True,
            }
        )
        proposals["proposals"].append(
            {
                "proposal_id": proposal_id,
                "research_question": f"synthetic question {proposal_id[0]}",
                "economic_rationale": f"synthetic rationale {proposal_id[0]}",
                "generation_reason": "synthetic",
                "proposed_factor_spec": {
                    "kind": "factor_spec",
                    "spec": factor_spec_to_dict(spec),
                },
            }
        )
        net = [float(value) for value in next(iter(series.values()))]
        write_export(
            screen_cache_dir / f"{diagnostics_hash}.export.json",
            diagnostics_hash=diagnostics_hash,
            net_series=net,
            l_series=net,
        )

    generation_path = root / "generation.json"
    generation_path.write_text(json.dumps(generation), encoding="utf-8")
    executor_state_path = root / "executor_state.json"
    executor_state_path.write_text(json.dumps(proposals), encoding="utf-8")

    coding = {
        "a" * 64: ("MOM", "+"),
        "b" * 64: ("MOM", "+"),
        "c" * 64: ("MOM", "+"),
    }
    coding_path = root / "coding.json"
    coding_sha256 = write_coding(coding_path, coding)

    assembly = FakeAssembly(is_fold, panels)
    return RunFixture(
        assembly=assembly,
        generation_path=generation_path,
        executor_state_path=executor_state_path,
        screen_cache_dir=screen_cache_dir,
        coding_path=coding_path,
        coding_sha256=coding_sha256,
        candidates=candidates,
        panels=panels,
    )
