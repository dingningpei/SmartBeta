"""Fold-generic D6 development PIT-universe construction (P7 task R7-U).

Authority: ``docs/campaign3_p7_reserve_path_design.md`` (frozen design),
``docs/campaign3_p7_amendment_a1.md`` (frozen amendment A1) and
``docs/campaign3_p7_implementation_spec.md`` §1–§3.

The pinned ``smart_beta/dataset_v1/universe.py`` is the sole authority for the
**development** D6 artifact and is consulted **import-only**: this module
re-expresses its schedule, eligibility (including the 252-session listing-age
rule), liquidity window and TOP_200/500/1000 ranking logic so that a
:class:`~smart_beta.dataset_v1.reserve_chain.fold.FoldWindow` selects the
formation window and the package source.  A development fold must reproduce
``universe.run_universe`` byte-for-byte; a reserve fold applies the identical
rule to the reserve D3/D5 packages, with lookback rows before the fold start
supplied by the development packages.

The development path delegates data loading to ``universe._prepare`` so the
frozen development behaviour is exact; the reserve path re-expresses the same
loader with explicit lookback stitching and the fold's D-SC cutoff.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any

import numpy as np
import pandas as pd

from smart_beta.dataset_v1 import universe as U
from smart_beta.dataset_v1.reserve_chain.fold import (
    DEVELOPMENT,
    RESERVE,
    AccessGrant,
    FoldWindow,
    ReserveAccessDenied,
    _is_verified_access_grant,
)

__all__ = [
    "FoldUniverseError",
    "FoldConsistencyError",
    "run_universe_fold",
    "run_reserve_universe",
]

_OBS_COLUMNS = ("ticker", "date", "close", "volume", "raw_row_index")
_IDENTITY_COLUMNS = (
    "security_key",
    "ticker",
    "exchange",
    "listed_start",
    "listed_end",
    "state",
    "reason",
)
_FLAG_COLUMNS = ("ticker", "date", "raw_row_index", "class_or_flag", "disposition")

#: The gate string for a partition that a fold does not evaluate.
DEFERRED_GATE11 = "DEFERRED / NOT ASSESSED"

#: Reserve-fold certification boundary.  The frozen development boundary text
#: (``universe.CERTIFICATION_BOUNDARY``) is used for the development fold so
#: its artifact is byte-identical.
CERTIFICATION_BOUNDARY_RESERVE = (
    "Campaign-3 P7 reserve-fold D6 derivation by new fold-generic code "
    "(amendment A1). Not independently certified. The reserve universe is "
    "produced only inside an authorized confirmation execution under a K-PIT "
    "ACCESS record; reserve access and confirmation consumption are Phase-10 "
    "authorized. Reserve D6, reserve Gate 11 and reserve-path equivalence: "
    "NOT CERTIFIED by this artifact. Not claimed: alpha or factor validity; "
    "production investability; delisting returns; transaction-cost realism; "
    "capacity or borrow availability; complete survivorship elimination."
)


class FoldUniverseError(U.D6Error):
    """Base class for fold-generic D6 fail-closed errors."""


class FoldConsistencyError(FoldUniverseError, ValueError):
    """The fold's development-lookback / reserve-package split is violated."""


# ---------------------------------------------------------------------------
# Fold-aware resolver
# ---------------------------------------------------------------------------


class FoldResolver:
    """The guarded file gateway for a fold's own (source) packages.

    Unlike :class:`universe.Resolver` this resolver permits ``reserve_sealed``
    path components for a reserve fold and permits formation dates up to the
    fold end.  It still refuses absolute / escaping paths and any path not
    allowlisted by the fold's bindings.
    """

    def __init__(self, root: Path | str, bindings: U.Bindings, fold: FoldWindow):
        self._root = Path(root)
        self._bindings = bindings
        self._allow = bindings.allowlist()
        self._fold = fold

    @property
    def allowlist(self) -> frozenset[str]:
        return self._allow

    def resolve(
        self, relative_path: str, *, formation_date: date | None = None
    ) -> Path:
        raw = str(relative_path)
        pure = PurePosixPath(raw)
        if pure.is_absolute() or ".." in pure.parts:
            raise U.UniverseInputError(f"path {raw!r} escapes <root>")
        if formation_date is not None:
            if formation_date > self._fold.end:
                raise U.ReserveFirewallError(
                    f"refusing formation date {formation_date} after fold end "
                    f"{self._fold.end}"
                )
            if formation_date < self._fold.start and self._fold.source == RESERVE:
                # Lookback never evaluates a formation before the fold start.
                raise U.ReserveFirewallError(
                    f"refusing formation date {formation_date} before fold start "
                    f"{self._fold.start}"
                )
        if raw not in self._allow:
            raise U.ReserveFirewallError(f"path {raw!r} is not allowlisted")
        return self._root / pure

    def read_bytes(
        self, relative_path: str, *, formation_date: date | None = None
    ) -> bytes:
        path = self.resolve(relative_path, formation_date=formation_date)
        try:
            return path.read_bytes()
        except OSError as exc:
            raise U.UniverseInputError(f"cannot read {path}: {exc}") from exc


# ---------------------------------------------------------------------------
# Fold data loading
# ---------------------------------------------------------------------------


def _read_parquet(
    resolver: Any, relative_path: str, columns: tuple[str, ...]
) -> pd.DataFrame:
    return pd.read_parquet(resolver.resolve(relative_path), columns=list(columns))


def _calendar_index(calendar: Any) -> tuple[dict[date, int], np.ndarray]:
    cal_dates = tuple(U._as_date(ts) for ts in calendar.dates)
    pos = {d: i for i, d in enumerate(cal_dates)}
    d64 = np.array([np.datetime64(d) for d in cal_dates], dtype="datetime64[D]")
    return pos, d64


def _assert_fold_dates(series: pd.Series, fold: FoldWindow, what: str) -> None:
    """Refuse any input date after the fold end (fail closed)."""
    if len(series) == 0:
        return
    parsed = pd.to_datetime(series, errors="coerce")
    if bool((parsed > pd.Timestamp(fold.end)).any()):
        raise U.ReserveFirewallError(
            f"{what} contains a date after the fold end {fold.end}"
        )


def _stitch(
    lookback: pd.DataFrame,
    source: pd.DataFrame,
    fold: FoldWindow,
    what: str,
) -> pd.DataFrame:
    """Join development lookback rows with the fold's source-package rows.

    Every row before ``fold.start`` must come from the development lookback
    package; every row on/after ``fold.start`` must come from the fold's own
    (reserve) package.  Violations fail closed.
    """
    if len(lookback):
        lb_dates = pd.to_datetime(lookback["date"], errors="coerce")
        if bool((lb_dates >= pd.Timestamp(fold.start)).any()):
            raise FoldConsistencyError(
                f"{what}: lookback rows on/after fold start {fold.start} "
                "(lookback must come from the development packages)"
            )
    if len(source):
        src_dates = pd.to_datetime(source["date"], errors="coerce")
        if bool((src_dates < pd.Timestamp(fold.start)).any()):
            raise FoldConsistencyError(
                f"{what}: source-package rows before fold start {fold.start} "
                "(lookback must come from the development packages)"
            )
    combined = pd.concat([lookback, source], ignore_index=True)
    _assert_fold_dates(combined["date"], fold, what)
    return combined


def _prepare_reserve(
    root: Path | str,
    fold: FoldWindow,
    bindings: U.Bindings,
    lookback_bindings: U.Bindings,
    calendar: Any,
) -> tuple:
    """Reserve-fold input preparation: verify, read, stitch, index."""
    reserve_resolver = FoldResolver(root, bindings, fold)
    lookback_resolver = U.Resolver(root, lookback_bindings)

    for stage in (bindings.d3, bindings.d4, bindings.d5, bindings.dsc):
        U._verify_stage(reserve_resolver, stage)
    for stage in (lookback_bindings.d3, lookback_bindings.d5):
        U._verify_stage(lookback_resolver, stage)

    reserve_obs = _read_parquet(
        reserve_resolver, bindings.d3.files[0].relative_path, _OBS_COLUMNS
    )
    lookback_obs = _read_parquet(
        lookback_resolver, lookback_bindings.d3.files[0].relative_path, _OBS_COLUMNS
    )
    reserve_flags = _read_parquet(
        reserve_resolver, bindings.d5.files[0].relative_path, _FLAG_COLUMNS
    )
    lookback_flags = _read_parquet(
        lookback_resolver, lookback_bindings.d5.files[0].relative_path, _FLAG_COLUMNS
    )

    observations = _stitch(
        lookback_obs, reserve_obs, fold, "D3 observations"
    )
    day_flags = _stitch(lookback_flags, reserve_flags, fold, "D5 day flags")

    identity = _read_parquet(
        reserve_resolver, bindings.d4.files[0].relative_path, _IDENTITY_COLUMNS
    )
    class_timeline = U._load_timeline(
        reserve_resolver.resolve(bindings.dsc.files[0].relative_path)
    )
    policy_timeline = U._load_timeline(
        reserve_resolver.resolve(bindings.dsc.files[1].relative_path)
    )

    pos, d64 = _calendar_index(calendar)
    keys, duplicate_groups = U._group_identity(identity.to_dict("records"))
    unusable = U._unusable_keys(day_flags)
    tickers = U._prepare_tickers(observations, unusable, pos)
    return (
        keys,
        duplicate_groups,
        tickers,
        class_timeline,
        policy_timeline,
        d64,
        pos,
    )


# ---------------------------------------------------------------------------
# Per-formation evaluation (frozen D6 rule, fold cutoff)
# ---------------------------------------------------------------------------


def _evaluate_key_fold(
    key: U._Key,
    f: date,
    cutoff: date,
    p: int,
    tickers: Any,
    class_timeline: Any,
    policy_timeline: Any,
    d64: np.ndarray,
    dsc_max_cutoff: date,
) -> dict[str, Any]:
    if key.collapsed:
        return U._collapsed_evaluation(key)
    assert key.listed_start is not None
    lo_date = key.listed_start
    if lo_date < U.ACQUISITION_START:
        lo_date = U.ACQUISITION_START
    lo_idx = int(np.searchsorted(d64, np.datetime64(lo_date), side="left"))
    hi_idx = p - 1
    listing_age = hi_idx - lo_idx + 1 if lo_idx <= hi_idx else 0

    ticker = tickers.get(key.ticker)
    first: int | None = None
    usable_pos = 0
    if ticker is not None and len(ticker.usable_idx):
        usable_pos = int(np.searchsorted(ticker.usable_idx, lo_idx, side="left"))
        if (
            usable_pos < len(ticker.usable_idx)
            and int(ticker.usable_idx[usable_pos]) <= hi_idx
        ):
            first = int(ticker.usable_idx[usable_pos])
    if first is None:
        denominator = 0
        numerator = 0
    else:
        denominator = hi_idx - first + 1
        usable_in = (
            int(np.searchsorted(ticker.usable_idx, hi_idx, side="right"))
            - usable_pos
        )
        numerator = denominator - usable_in

    trailing = 0
    median_dv: float | None = None
    if ticker is not None and len(ticker.liq_idx):
        wlo = p - U.LIQUIDITY_WINDOW
        whi = p - 1
        a = int(np.searchsorted(ticker.liq_idx, wlo, side="left"))
        b = int(np.searchsorted(ticker.liq_idx, whi, side="right"))
        trailing = b - a
        if trailing > 0:
            median_dv = float(np.median(ticker.liq_dv[a:b]))

    if cutoff > dsc_max_cutoff:
        raise U.ReserveFirewallError(
            f"D-SC query cutoff {cutoff} exceeds fold cutoff {dsc_max_cutoff}"
        )
    class_state, class_reason = U.class_state_at(
        class_timeline, key.security_key, cutoff
    )
    policy_state, _ = U.policy_state_at(policy_timeline, key.security_key, cutoff)

    if key.state != U.STATE_INTERVAL_ASSIGNED:
        reason = U.REASON_IDENTITY_NOT_ADMISSIBLE
    elif key.exchange not in U.ELIGIBLE_EXCHANGES:
        reason = U.REASON_EXCHANGE_NOT_ELIGIBLE
    elif class_state != U.CLASS_COMMON_STOCK:
        reason = U.REASON_NOT_COMMON_STOCK
    elif policy_state == U.POLICY_EXCLUDED:
        reason = U.REASON_ISSUER_POLICY_EXCLUDED
    elif policy_state == U.POLICY_UNRESOLVED:
        reason = U.REASON_ISSUER_POLICY_UNRESOLVED
    elif listing_age < U.MIN_LISTING_AGE_SESSIONS:
        reason = U.REASON_LISTING_AGE_INSUFFICIENT
    elif denominator <= 0:
        reason = U.REASON_PIT_MISSINGNESS_EMPTY
    elif numerator * 100 > 5 * denominator:
        reason = U.REASON_PIT_MISSINGNESS_EXCEEDED
    elif trailing < U.MIN_LIQUIDITY_SESSIONS:
        reason = U.REASON_TRAILING_LIQUIDITY_INSUFFICIENT
    else:
        reason = U.REASON_ELIGIBLE

    return {
        "security_key": key.security_key,
        "ticker": key.ticker,
        "identity_state": key.state,
        "class_state": class_state,
        "class_reason": class_reason,
        "policy_state": policy_state,
        "listing_age_sessions": listing_age,
        "missing_numerator": numerator,
        "missing_denominator": denominator,
        "trailing_valid_sessions": trailing,
        "median_dv": median_dv,
        "reason": reason,
        "rank": None,
        "duplicate_row_count": None,
        "duplicate_identity_states": None,
        "duplicate_reasons": None,
    }


def _evaluate_formation_fold(
    f: date,
    cutoff: date,
    keys: Any,
    tickers: Any,
    class_timeline: Any,
    policy_timeline: Any,
    d64: np.ndarray,
    pos: dict[date, int],
    n_keys: int,
    dsc_max_cutoff: date,
) -> dict[str, Any]:
    p = pos[f]
    population = [key for key in keys if U._in_population(key, f, cutoff)]
    not_listed = n_keys - len(population)
    evaluations = [
        _evaluate_key_fold(
            key,
            f,
            cutoff,
            p,
            tickers,
            class_timeline,
            policy_timeline,
            d64,
            dsc_max_cutoff,
        )
        for key in population
    ]
    eligible = [e for e in evaluations if e["reason"] == U.REASON_ELIGIBLE]
    eligible.sort(key=lambda e: (-e["median_dv"], e["security_key"]))
    for rank, evaluation in enumerate(eligible, start=1):
        evaluation["rank"] = rank
    n_eligible = len(eligible)
    status = (
        U.STATUS_OK if n_eligible >= U.MIN_ELIGIBLE else U.STATUS_UNIVERSE_SHORTFALL
    )
    return {
        "formation_date": f,
        "cutoff_date": cutoff,
        "n_population": len(population),
        "n_not_listed_at_formation": not_listed,
        "n_eligible": n_eligible,
        "status": status,
        "top_1000_shortfall": n_eligible < 1000,
        "gate11": "PASS" if status == U.STATUS_OK else "FAIL",
        "evaluations": evaluations,
        "universe_rows": [
            evaluation for evaluation in eligible if evaluation["rank"] <= 1000
        ],
    }


# ---------------------------------------------------------------------------
# Output construction (fold partition labels)
# ---------------------------------------------------------------------------


def _build_formations_fold(
    schedule: U.Schedule, results: Any, fold: FoldWindow
) -> pd.DataFrame:
    by_date = {result["formation_date"]: result for result in results}
    records: list[list[Any]] = []
    for f in schedule.full:
        cutoff = U._as_date(schedule.calendar.previous_trading_day(f))
        if f in by_date:
            result = by_date[f]
            records.append(
                [
                    f,
                    cutoff,
                    fold.partition,
                    result["n_population"],
                    result["n_eligible"],
                    result["status"],
                    result["top_1000_shortfall"],
                ]
            )
        else:
            records.append(
                [f, cutoff, fold.other_partition, None, None, U.STATUS_DEFERRED, None]
            )
    return U._build_frame(records, U._FORMATIONS_COLUMNS, U._FORMATIONS_DTYPES)


def _formation_summary_fold(
    schedule: U.Schedule, results: Any, fold: FoldWindow
) -> list[dict[str, Any]]:
    by_date = {result["formation_date"]: result for result in results}
    summary: list[dict[str, Any]] = []
    for f in schedule.full:
        cutoff = U._as_date(schedule.calendar.previous_trading_day(f))
        if f in by_date:
            result = by_date[f]
            summary.append(
                {
                    "formation_date": f.isoformat(),
                    "cutoff_date": cutoff.isoformat(),
                    "partition": fold.partition,
                    "n_population": result["n_population"],
                    "n_not_listed_at_formation": result[
                        "n_not_listed_at_formation"
                    ],
                    "n_eligible": result["n_eligible"],
                    "status": result["status"],
                    "top_1000_shortfall": result["top_1000_shortfall"],
                    "gate11": result["gate11"],
                }
            )
        else:
            summary.append(
                {
                    "formation_date": f.isoformat(),
                    "cutoff_date": cutoff.isoformat(),
                    "partition": fold.other_partition,
                    "n_population": None,
                    "n_not_listed_at_formation": None,
                    "n_eligible": None,
                    "status": U.STATUS_DEFERRED,
                    "top_1000_shortfall": None,
                    "gate11": DEFERRED_GATE11,
                }
            )
    return summary


def _gate_11(results: Any, fold: FoldWindow) -> dict[str, str]:
    all_ok = all(result["status"] == U.STATUS_OK for result in results)
    verdict = "PASS" if all_ok else "FAIL"
    if fold.partition == U.PARTITION_DEVELOPMENT:
        return {"development": verdict, "reserve_gate11": DEFERRED_GATE11}
    return {"development": DEFERRED_GATE11, "reserve_gate11": verdict}


def _certification_boundary(fold: FoldWindow) -> str:
    if fold.source == DEVELOPMENT:
        return U.CERTIFICATION_BOUNDARY
    return CERTIFICATION_BOUNDARY_RESERVE


def _code_sha(fold: FoldWindow) -> str:
    """The code SHA folded into the artifact identity.

    The development fold reproduces the frozen development artifact, whose
    identity binds ``universe.py``; the reserve fold is new code and binds
    this module (``universe_fold.py``).
    """
    if fold.source == RESERVE:
        return U._sha256_hex(Path(__file__).read_bytes())
    return U._sha256_hex(Path(U.__file__).read_bytes())


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def run_universe_fold(
    root: Path | str,
    *,
    fold: FoldWindow,
    bindings: U.Bindings,
    lookback_bindings: U.Bindings | None = None,
    calendar: Any | None = None,
) -> dict[str, Any]:
    """Build the D6 PIT universe for ``fold`` and return its ``universe.json``.

    A development fold evaluates the development formations from ``bindings``
    and must reproduce :func:`universe.run_universe`.  A reserve fold evaluates
    the reserve formations from ``bindings``, stitching in the development
    ``lookback_bindings`` for every row before ``fold.start``.
    """
    if not isinstance(fold, FoldWindow):
        raise U.UniverseInputError("fold must be a FoldWindow")
    if not isinstance(bindings, U.Bindings):
        raise U.UniverseInputError("bindings must be a Bindings value")
    if fold.source == RESERVE:
        if not _is_verified_access_grant(fold.grant):
            raise ReserveAccessDenied(
                "a reserve universe derivation requires a K-PIT-verified "
                "AccessGrant (access.AccessGrant.from_kpit)"
            )
        if not isinstance(lookback_bindings, U.Bindings):
            raise U.UniverseInputError(
                "a reserve fold requires the development lookback bindings"
            )
    elif lookback_bindings is not None:
        raise U.UniverseInputError(
            "a development fold must not carry lookback bindings"
        )

    calendar = (
        calendar
        if calendar is not None
        else U.build_nyse_calendar(U.CALENDAR_START, U.CALENDAR_END)
    )
    root = Path(root)
    documents = U._verify_documents(bindings)
    code_sha = _code_sha(fold)
    universe_id = U._compute_universe_id(bindings, documents, code_sha)

    if fold.source == DEVELOPMENT:
        prepared = U._prepare(U.Resolver(root, bindings), bindings, calendar)
    else:
        assert lookback_bindings is not None
        prepared = _prepare_reserve(root, fold, bindings, lookback_bindings, calendar)

    (
        keys,
        duplicate_groups,
        tickers,
        class_timeline,
        policy_timeline,
        d64,
        pos,
    ) = prepared
    schedule = U.build_schedule(calendar)
    evaluated = [f for f in schedule.full if fold.contains(f)]

    results = [
        _evaluate_formation_fold(
            f,
            U._as_date(calendar.previous_trading_day(f)),
            keys,
            tickers,
            class_timeline,
            policy_timeline,
            d64,
            pos,
            len(keys),
            fold.dsc_max_cutoff,
        )
        for f in evaluated
    ]

    development = U._build_universe_development(results)
    formations = _build_formations_fold(schedule, results, fold)
    audit = U._build_audit(results)

    content: dict[str, Any] = {
        "schema": U.SCHEMA,
        "universe_id": universe_id,
        "universe_code_sha256": code_sha,
        "bindings": U._bindings_summary(bindings),
        "documents": dict(documents),
        "schedule": U._schedule_summary(schedule),
        "n_keys": len(keys),
        "duplicate_identity_groups": sorted(
            duplicate_groups, key=lambda group: group["security_key"]
        ),
        "formations": _formation_summary_fold(schedule, results, fold),
        "gate_11": _gate_11(results, fold),
        "certification_boundary": _certification_boundary(fold),
    }
    if fold.source == RESERVE:
        assert lookback_bindings is not None
        content["lookback_bindings"] = U._bindings_summary(lookback_bindings)
        content["fold"] = {
            "start": fold.start.isoformat(),
            "end": fold.end.isoformat(),
            "source": fold.source,
        }
    return U._write_outputs(root, universe_id, development, formations, audit, content)


def run_reserve_universe(
    root: Path | str,
    *,
    end: date,
    grant: AccessGrant,
    bindings: U.Bindings,
    lookback_bindings: U.Bindings,
    calendar: Any | None = None,
) -> dict[str, Any]:
    """Convenience entry point: build a reserve-fold D6 universe."""
    fold = FoldWindow.reserve(end, grant=grant)
    return run_universe_fold(
        root,
        fold=fold,
        bindings=bindings,
        lookback_bindings=lookback_bindings,
        calendar=calendar,
    )
