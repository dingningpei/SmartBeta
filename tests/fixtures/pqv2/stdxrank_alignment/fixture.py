"""Synthetic ``STD_X_RANK`` fixture with the D7c ``prev(d)`` row convention.

The PQ22-H evaluation fixture (:mod:`tests.fixtures.pqv2.evaluation.fixture`)
builds its D7c frames without the contract §11 bridge lag, so it could not
expose the observed-``dv`` double-lag erratum PQ-SX.  This fixture builds the
same synthetic dataset but feeds a ``dollar_volume`` frame that **does** follow
the bridge convention: the raw per-session observations are shifted so that the
factor row dated ``d`` carries the observation of ``prev(d)``.

The fixture reuses the PQ22-H ``SyntheticStore``/bindings (they are independent
of ``dv``) and only swaps the ``dollar_volume`` input and the factor spec.  The
independent oracle lives in ``oracle.py`` and has no ``smart_beta`` imports.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[4]
_EVAL_FIXTURE = (
    _REPO_ROOT / "tests" / "fixtures" / "pqv2" / "evaluation" / "fixture.py"
)
_ORACLE = Path(__file__).resolve().parent / "oracle.py"


def _load_eval_fixture():
    name = "pqv2h_fixture"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _EVAL_FIXTURE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_oracle():
    name = "pqv2_stdxrank_alignment_oracle"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _ORACLE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fx = _load_eval_fixture()
oracle = load_oracle()


def raw_observations(
    seed: int = 20241010,
    *,
    keys: Sequence[str] = fx.KEYS,
    sessions: Sequence[Any] = fx.SESSIONS,
    volatility: float = 0.6,
) -> pd.DataFrame:
    """Per-session ``dollar_volume`` observations (indexed by session).

    The series is key-specific (a lognormal random walk per key) so that a
    one-session shift of the window materially changes both the level and the
    cross-sectional ordering of ``V``.
    """

    rng = np.random.default_rng(int(seed))
    index = pd.to_datetime(list(sessions))
    raw = pd.DataFrame(index=index, columns=list(keys), dtype="float64")
    for position, key in enumerate(keys):
        base = 1000.0 * (1.0 + 0.1 * position)
        raw[key] = base * np.exp(
            np.cumsum(rng.normal(0.0, volatility, len(index)))
        )
    return raw


def bridge_frame(raw: pd.DataFrame) -> pd.DataFrame:
    """Apply the ``prev(d)`` convention: row ``r`` holds ``raw`` row ``r - 1``."""

    frame = raw.shift(1)
    frame.iloc[0] = raw.iloc[0]
    return frame


def zero_short_window_indices(
    w1: int,
    *,
    formations: Sequence[Any] = fx.FORMATIONS,
    sessions: Sequence[Any] = fx.SESSIONS,
) -> tuple[int, ...]:
    """Raw session indices zeroed so a key's short-window sum is exactly zero.

    The short window of formation ``f`` is the ``w1`` observations ending at
    ``f - 1`` -- session indices ``[f - w1, f - 1]``.  Zeroing exactly those
    sessions (for one key) keeps the long window's long-only part positive, so
    the key has ``V = 0`` rather than "no V" (which needs the long sum zero).
    """

    position = {session: index for index, session in enumerate(sessions)}
    cells: set[int] = set()
    for formation in formations:
        base = position[formation]
        cells.update(range(base - w1, base))
    return tuple(sorted(cells))


def std_x_rank_spec(*, w1: int = 2, w2: int = 6, momentum: tuple[int, int] = (21, 252)):
    """A §15A.2 ``STD_X_RANK_G`` member with small volume windows.

    The small windows make ``V`` sensitive to a one-session misalignment, which
    is exactly the defect this fixture exists to expose.
    """

    from smart_beta.spec.expression import Mul, lag, rank, ratio, rolling_mean, field
    from smart_beta.spec.expression import standardize

    base = fx.load_campaign_spec("C1-H4")
    expression = Mul(
        standardize(
            ratio(lag(field("tri"), momentum[0]), lag(field("tri"), momentum[1]))
        ),
        rank(
            ratio(
                rolling_mean(field("dv"), w1),
                rolling_mean(field("dv"), w2),
            )
        ),
    )
    return dataclasses.replace(
        base,
        id="pq_sx_alignment_member",
        description="PQ-SX prev(d) alignment fixture member",
        expression=expression,
        inputs=base.inputs,
        sign=1,
        hypothesis=None,
    )


def build_inputs(store: Any, raw: pd.DataFrame) -> dict[str, Any]:
    """The PQ22-H inputs with the ``dollar_volume`` frame in prev(d) form."""

    inputs = fx.build_inputs(store)
    inputs["dollar_volume"] = fx._trusted(
        bridge_frame(raw), "dollar_volume"
    )
    return inputs


@dataclasses.dataclass
class Case:
    fx: Any
    store: Any
    spec: Any
    inputs: Mapping[str, Any]
    panel: pd.DataFrame
    raw: pd.DataFrame
    input_frame: pd.DataFrame
    diagnostics: Any


def build_case(
    tmp_path: Path,
    *,
    seed: int = 20241010,
    w1: int = 2,
    w2: int = 6,
    raw: pd.DataFrame | None = None,
    spec: Any = None,
    fold: Any = None,
    zero_dv_key: str | None = None,
) -> Case:
    """Build and evaluate the prev(d) ``STD_X_RANK`` case end to end."""

    from smart_beta.dataset_v1 import pqv2_core as C
    from smart_beta.dataset_v1 import pqv2_evaluation as E

    store = fx.SyntheticStore(Path(tmp_path), name="root")
    store.standard()
    store.build_d7b()
    store.build_sources()
    pq_bindings, identity_hash = store.build_identity()
    raw = raw if raw is not None else raw_observations(seed)
    if zero_dv_key is not None:
        raw = raw.copy()
        indices = list(zero_short_window_indices(w1))
        column = raw.columns.get_loc(zero_dv_key)
        raw.iloc[indices, column] = 0.0
    spec = spec if spec is not None else std_x_rank_spec(w1=w1, w2=w2)
    inputs = build_inputs(store, raw)
    panel = fx.factor_panel(spec, inputs, store)
    base = fx.base_declaration(spec, panel)
    declaration = fx.declaration(
        base,
        identity_hash=identity_hash,
        methodology_sha256=C.AUTHORITY_V2_2.methodology_sha256,
        ed_id=C.AUTHORITY_V2_2.ed_id,
        ce_id=C.AUTHORITY_V2_2.ce_id,
    )
    overlay = store.overlay(pq_id=declaration.pq_id, pq_hash=declaration.pq_hash)
    part, _is, _holdout = fx.partition()
    diagnostics = E.build_pqv2_diagnostics(
        store.root,
        declaration,
        spec,
        panel,
        part,
        overlay=overlay,
        inputs=inputs,
        bindings=store.d7b_bindings,
        source_bindings=store.source_bindings,
        pq_bindings=pq_bindings,
        pq_read_records_fn=lambda path: fx.genesis_records(),
        fold=fold if fold is not None else C.DEVELOPMENT_FOLD,
    )
    return Case(
        fx=fx,
        store=store,
        spec=spec,
        inputs=inputs,
        panel=panel,
        raw=raw,
        input_frame=inputs["dollar_volume"].values,
        diagnostics=diagnostics,
    )


def formation_window(
    sessions: Sequence[Any],
    formation: Any,
    windows: Any,
    *,
    w1: int,
    w2: int,
) -> tuple[dict[Any, int], dict[int, Any], tuple[int, ...]]:
    """The formation-position map, its inverse and the dv offsets."""

    from smart_beta.dataset_v1 import pqv2_evaluation as E

    formation_pos = E._formation_positions(sessions, formation)
    session_at_offset = {offset: session for session, offset in formation_pos.items()}
    offsets = E._dv_offsets(windows, formation_pos)
    return formation_pos, session_at_offset, offsets
