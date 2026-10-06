"""Synthetic end-to-end Dataset-v1.2-shaped fixture for the PQ2-H tests.

This module builds a tiny but complete synthetic dataset root with injected
test bindings, exactly as the PQB / P-Q v2 source suites do:

* the D7A ``returns`` artifact (members, horizons, labels, daily returns) that
  the unmodified D7b pipeline consumes;
* the D3 ``normalized`` (observations + ``ca_events``) and D4 ``identity``
  artifacts that PQ2-B's source loader consumes;
* the PQA Dataset-v1.2 identity payload and the D8 genesis record set;
* D7c ``TrustedInput``s for ``daily_total_return``, ``total_return_index`` and
  ``dollar_volume``;
* a P-Q v2 overlay with synthetic flags.

Nothing here reads a production artifact, the reserve, holdout, ``raw/``,
``dsc_evidence`` or Knowledge-PIT.  No network call is made.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from smart_beta.dataset_v1 import delisting_bridge as B
from smart_beta.dataset_v1 import pqv2_core as C
from smart_beta.dataset_v1 import pqv2_sources as S
from smart_beta.dataset_v1 import quarantine as Q
from smart_beta.dataset_v1 import returns as R
from smart_beta.dataset_v1 import trusted_inputs as TI
from smart_beta.evaluation.partition import Fold, FoldRole, Partition
from smart_beta.spec import evaluator as EV
from smart_beta.spec.engine import TrustedInput
from smart_beta.spec.factor_spec import FactorSpec, MissingPolicy
from smart_beta.spec.requirements import (
    DataCapability,
    Frequency,
    ObservationPeriod,
    RevisionPolicy,
    Unit,
)

CAMPAIGN_SPECS = (
    Path(__file__).resolve().parents[4] / "docs" / "campaign1_factorspecs.json"
)

# ---------------------------------------------------------------------------
# Session grid
# ---------------------------------------------------------------------------


def business_days(start: date, n: int) -> list[date]:
    out: list[date] = []
    cursor = start
    while len(out) < n:
        if cursor.weekday() < 5:
            out.append(cursor)
        cursor += timedelta(days=1)
    return out


N_SESSIONS = 320
SESSIONS = business_days(date(2019, 12, 2), N_SESSIONS)
POS = {session: index for index, session in enumerate(SESSIONS)}
FORMATIONS = (
    SESSIONS[260],
    SESSIONS[264],
    SESSIONS[268],
    SESSIONS[272],
)
FOLD_START = SESSIONS[0]
FOLD_END = SESSIONS[315]
HOLDOUT_START = SESSIONS[315]
HOLDOUT_END = SESSIONS[319]
HORIZON = 3
ARTIFACT_ID = "r-pqv2h0000000000"
KEYS = ("A", "B", "C", "D", "E", "F")


def future(formation: date, n: int) -> date:
    return SESSIONS[POS[formation] + n]


def _canonical(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class _Kind:
    def __init__(self, value: str):
        self.value = value


class _GenesisRecord:
    def __init__(self) -> None:
        self.seq = Q.D8_GENESIS_SEQ
        self.kind = _Kind("ARTIFACT")
        self.record_hash = Q.D8_GENESIS_RECORD_HASH
        self.payload = {"source_label": Q.D8_SOURCE_LABEL}

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "kind": self.kind.value,
            "record_hash": self.record_hash,
            "payload": dict(self.payload),
        }


def genesis_records() -> tuple[_GenesisRecord, ...]:
    return (_GenesisRecord(),)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _sha256_bytes(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _write_parquet(root: Path, relative: str, frame: pd.DataFrame) -> str:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, engine="pyarrow", index=False, compression="zstd")
    return _sha256_file(path)


def _write_manifest(root: Path, relative: str, field: str, files: dict) -> str:
    content = {"files_sha256": files}
    content[field] = hashlib.sha256(_canonical(files).encode("utf-8")).hexdigest()
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((_canonical(content) + "\n").encode("utf-8"))
    return content[field]


def load_campaign_spec(hypothesis: str) -> FactorSpec:
    payload = json.loads(CAMPAIGN_SPECS.read_text(encoding="utf-8"))
    for entry in payload["specs"]:
        if entry["hypothesis_label"] == hypothesis:
            return FactorSpec.from_dict(entry["factor_spec_canonical_json"])
    raise KeyError(hypothesis)


# ---------------------------------------------------------------------------
# Member / store model
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class Member:
    formation: date
    key: str
    state: str = "COMPLETE"
    affected: bool = False
    m: int | None = None
    rank: int = 1
    rate: float = 0.01
    observed_segment_end: date | None = None
    endpoint: date | None = None
    listed_end_dev: date | None = None
    n_invalid_sessions: int = 0


class SyntheticStore:
    """A synthetic D7A + D3/D4 dataset root with injected bindings."""

    def __init__(self, tmp_path: Path, *, name: str = "root") -> None:
        self.root = tmp_path / name
        self.root.mkdir(parents=True, exist_ok=True)
        self.members: list[Member] = []
        self.extra_daily: list[tuple[str, date, str, float]] = []
        self.crosses_reserve: dict[date, bool] = {}
        self.flags: list[dict[str, Any]] = []
        self.ca_events: list[dict[str, Any]] = []
        self.d7b_bindings: B.Bindings | None = None
        self.source_bindings: S.SourceBindings | None = None
        self.pq_bindings: Q.Bindings | None = None
        self.identity_hash: str | None = None

    # -- construction ---------------------------------------------------
    def add(self, formation: date, key: str, **kwargs: Any) -> Member:
        member = Member(formation=formation, key=key, **kwargs)
        if member.state == "EMPTY":
            member.affected = True
        self.members.append(member)
        return member

    def standard(self, formations: Sequence[date] = FORMATIONS) -> None:
        for index, formation in enumerate(formations):
            for key in KEYS:
                rank = "ABCDEF".index(key) + 1
                shift = 0.01 if index % 2 == 1 else 0.0
                self.add(
                    formation,
                    key,
                    rank=rank,
                    rate=0.01 + 0.002 * rank + shift,
                )

    def add_flag(
        self,
        key: str,
        session: date,
        *,
        beyond: bool = False,
    ) -> dict[str, Any]:
        position = POS[session]
        prev_s = SESSIONS[position - 1] if position > 0 else None
        next_s = SESSIONS[position + 1] if position + 1 < len(SESSIONS) else None
        flag = {
            "security_key": key,
            "flag_session": session,
            "prev_session": prev_s,
            "next_session": next_s,
            "next_beyond_development": beyond,
        }
        self.flags.append(flag)
        return flag

    # -- daily returns --------------------------------------------------
    def _daily_rate(self, key: str, session: date) -> float:
        return 0.001 + 0.0005 * "ABCDEF".index(key) + 0.0001 * POS[session]

    def _member_daily(
        self, member: Member
    ) -> list[tuple[str, date, str, float]]:
        if member.state == "EMPTY":
            return []
        cover = member.m if (member.affected and member.m is not None) else HORIZON
        rows: list[tuple[str, date, str, float]] = []
        for j in range(1, cover + 1):
            rows.append(
                (member.key, future(member.formation, j), "VALID", member.rate)
            )
        return rows

    def build_d7b(self) -> B.Bindings:
        root = self.root
        members_rows: list[dict[str, Any]] = []
        horizon_rows: list[dict[str, Any]] = []
        label_rows: list[dict[str, Any]] = []
        daily_rows: list[tuple[str, date, str, float]] = []
        formations = sorted({member.formation for member in self.members})
        for formation in formations:
            endpoint = future(formation, HORIZON)
            horizon_rows.append(
                {
                    "formation_date": formation,
                    "h": HORIZON,
                    "endpoint": endpoint,
                    "crosses_reserve": self.crosses_reserve.get(formation, False),
                }
            )
        for member in self.members:
            endpoint = member.endpoint or future(member.formation, HORIZON)
            if member.observed_segment_end is not None:
                segment_end = member.observed_segment_end
            elif member.affected and member.m is not None:
                segment_end = future(member.formation, member.m)
            else:
                segment_end = endpoint
            sessions = member.m if member.m is not None else HORIZON
            members_rows.append(
                {
                    "formation_date": member.formation,
                    "security_key": member.key,
                    "ticker": member.key,
                    "rank": member.rank,
                    "top_200": False,
                    "top_500": True,
                    "top_1000": True,
                    "listed_end_dev": member.listed_end_dev or SESSIONS[314],
                    "listing_status": "ACTIVE",
                    "terminal_evidence_source": "D4_RETROSPECTIVE_LISTED_END",
                }
            )
            label_state = (
                "EMPTY_OBSERVED_PATH_IDENTITY"
                if member.state == "EMPTY"
                else member.state
            )
            label_rows.append(
                {
                    "formation_date": member.formation,
                    "security_key": member.key,
                    "h": HORIZON,
                    "endpoint": endpoint,
                    "label_state": label_state,
                    "terminal_affected": member.affected,
                    "observed_path_complete": label_state == "COMPLETE",
                    "n_observed_sessions": sessions,
                    "n_post_terminal_sessions": 0,
                    "n_invalid_sessions": member.n_invalid_sessions,
                    "observed_segment_end": segment_end,
                    "first_invalid_session": None,
                    "first_invalid_status": None,
                }
            )
            daily_rows.extend(self._member_daily(member))
        daily_rows.extend(self.extra_daily)

        daily = pd.DataFrame(
            daily_rows,
            columns=["security_key", "date", "session_status", "daily_total_return"],
        )
        daily["prev_session"] = pd.NaT
        daily["ca_event_count"] = 0
        daily["corp_action_suspect"] = False
        members = pd.DataFrame(members_rows)
        horizons = pd.DataFrame(horizon_rows)
        labels = pd.DataFrame(label_rows)
        for frame, cols in (
            (members, ["formation_date", "listed_end_dev"]),
            (horizons, ["formation_date", "endpoint"]),
            (
                labels,
                [
                    "formation_date",
                    "endpoint",
                    "observed_segment_end",
                    "first_invalid_session",
                ],
            ),
            (daily, ["date", "prev_session"]),
        ):
            for column in cols:
                if column in frame:
                    frame[column] = pd.to_datetime(frame[column])

        base = f"derived/returns/{ARTIFACT_ID}"
        (root / base).mkdir(parents=True, exist_ok=True)
        for name, frame in (
            ("members.parquet", members),
            ("horizons.parquet", horizons),
            ("labels.parquet", labels),
            ("daily_returns.parquet", daily),
        ):
            frame.to_parquet(root / base / name, index=False)
        files = {
            f"{base}/{name}": _sha256_file(root / base / name)
            for name in (
                "members.parquet",
                "horizons.parquet",
                "labels.parquet",
                "daily_returns.parquet",
            )
        }
        returns_hash = B.content_hash(files)
        manifest = {
            "returns_id": ARTIFACT_ID,
            "files_sha256": files,
            "returns_hash": returns_hash,
        }
        (root / base / "returns.json").write_text(json.dumps(manifest))
        manifest_sha = _sha256_file(root / base / "returns.json")
        d7a = B.D7aBinding(
            artifact_id=ARTIFACT_ID,
            returns_hash=returns_hash,
            manifest_relative_path=f"{base}/returns.json",
            manifest_sha256=manifest_sha,
            files=tuple(B.FileBinding(path, files[path]) for path in sorted(files)),
        )
        defaults = B.default_bindings()
        self.d7b_bindings = B.Bindings(
            d7a=d7a,
            code_sha256=defaults.code_sha256,
            documents=defaults.documents,
        )
        return self.d7b_bindings

    # -- D3/D4 source bindings ------------------------------------------
    def build_sources(self) -> S.SourceBindings:
        root = self.root
        d3_id = "n-test"
        d4_id = "i-test"
        observations = pd.DataFrame(
            {
                "ticker": pd.Series([], dtype="string"),
                "date": pd.Series([], dtype="datetime64[ns]"),
                "close": pd.Series([], dtype="float64"),
                "raw_row_index": pd.Series([], dtype="int64"),
            }
        )
        ca = pd.DataFrame(
            {
                "stock_id": pd.Series(
                    [event["stock_id"] for event in self.ca_events], dtype="string"
                ),
                "effective_date": pd.Series(
                    [event["effective_date"] for event in self.ca_events],
                    dtype="datetime64[ns]",
                ),
                "action_type": pd.Series(
                    [event["action_type"] for event in self.ca_events], dtype="string"
                ),
                "knowledge_date": pd.Series(
                    [event["knowledge_date"] for event in self.ca_events],
                    dtype="datetime64[ns]",
                ),
                "adjustment_factor": pd.Series(
                    [event["adjustment_factor"] for event in self.ca_events],
                    dtype="float64",
                ),
                "is_superseded": pd.Series(
                    [event.get("is_superseded", False) for event in self.ca_events],
                    dtype=bool,
                ),
            }
        )
        identity = pd.DataFrame(
            {
                "security_key": pd.Series(list(KEYS), dtype="string"),
                "ticker": pd.Series(list(KEYS), dtype="string"),
                "listed_start": pd.Series(
                    [pd.Timestamp(SESSIONS[0]) for _ in KEYS],
                    dtype="datetime64[ns]",
                ),
                "listed_end": pd.Series(
                    [pd.Timestamp(SESSIONS[-1]) for _ in KEYS],
                    dtype="datetime64[ns]",
                ),
            }
        )
        d3_obs = f"normalized/{d3_id}/observations.parquet"
        d3_ca = f"normalized/{d3_id}/ca_events.parquet"
        d4_file = f"derived/identity/{d4_id}/identity_assignment.parquet"
        sha_obs = _write_parquet(root, d3_obs, observations)
        sha_ca = _write_parquet(root, d3_ca, ca)
        sha_ident = _write_parquet(root, d4_file, identity)
        d3_hash = _write_manifest(
            root,
            f"normalized/{d3_id}/build.json",
            "normalized_hash",
            {d3_obs: sha_obs, d3_ca: sha_ca},
        )
        d3_msha = _sha256_file(root / f"normalized/{d3_id}/build.json")
        d4_hash = _write_manifest(
            root,
            f"derived/identity/{d4_id}/identity.json",
            "identity_hash",
            {d4_file: sha_ident},
        )
        d4_msha = _sha256_file(root / f"derived/identity/{d4_id}/identity.json")
        ca_bindings = R.Bindings(
            d3=R.StageBinding(
                "D3",
                d3_id,
                f"normalized/{d3_id}/build.json",
                "normalized_hash",
                d3_hash,
                d3_msha,
                (R.FileBinding(d3_obs, sha_obs), R.FileBinding(d3_ca, sha_ca)),
            ),
            d4=R.StageBinding(
                "D4",
                d4_id,
                f"derived/identity/{d4_id}/identity.json",
                "identity_hash",
                d4_hash,
                d4_msha,
                (R.FileBinding(d4_file, sha_ident),),
            ),
            d5=R.default_bindings().d5,
            d6=R.default_bindings().d6,
            documents=(),
        )
        assert self.d7b_bindings is not None
        self.source_bindings = S.SourceBindings(ca=ca_bindings, d7a=self.d7b_bindings)
        return self.source_bindings

    # -- PQA identity ----------------------------------------------------
    def build_identity(self) -> tuple[Q.Bindings, str]:
        root = self.root
        manifests = {
            "n-f874d3ecb61374d4": (
                "normalized/n-f874d3ecb61374d4/build.json",
                "normalized_hash",
            ),
            "q-5d1028655f8c82e1": (
                "derived/qc/q-5d1028655f8c82e1/qc_report.json",
                "qc_hash",
            ),
            "c-27b6dabd0db0cb15": (
                "derived/classification/c-27b6dabd0db0cb15/dsc.json",
                "dsc_hash",
            ),
            "u-6150bc6ac2866b7f": (
                "derived/universe/u-6150bc6ac2866b7f/universe.json",
                "universe_hash",
            ),
            "r-34565f074d64ccda": (
                "derived/returns/r-34565f074d64ccda/returns.json",
                "returns_hash",
            ),
            "ti-aa23fabd3db4e595": (
                "derived/trusted_inputs/ti-aa23fabd3db4e595/trusted_inputs.json",
                "ti_hash",
            ),
        }
        values = {
            "normalized_hash": Q.INHERITED_ARTIFACTS["n-f874d3ecb61374d4"],
            "qc_hash": Q.INHERITED_ARTIFACTS["q-5d1028655f8c82e1"],
            "dsc_hash": Q.INHERITED_ARTIFACTS["c-27b6dabd0db0cb15"],
            "universe_hash": Q.INHERITED_ARTIFACTS["u-6150bc6ac2866b7f"],
            "returns_hash": Q.INHERITED_ARTIFACTS["r-34565f074d64ccda"],
            "ti_hash": Q.INHERITED_ARTIFACTS["ti-aa23fabd3db4e595"],
        }
        for _artifact_id, (relative, field) in manifests.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((_canonical({field: values[field]}) + "\n").encode("utf-8"))
        bindings = dataclasses.replace(Q.DEFAULT_BINDINGS, artifact_manifests=manifests)
        payload = Q.build_identity_payload()
        pq_dir = Q._overlay_directory(root, bindings)
        pq_dir.mkdir(parents=True, exist_ok=True)
        (pq_dir / "dataset_identity.json").write_bytes(Q._canonical_bytes(payload))
        self.pq_bindings = bindings
        self.identity_hash = Q.compute_identity_hash(payload)
        return bindings, self.identity_hash

    # -- overlay / inputs -----------------------------------------------
    def overlay(self, *, pq_id: str, pq_hash: str) -> Q.Overlay:
        flag_frame = pd.DataFrame(
            {
                "security_key": pd.Series(
                    [flag["security_key"] for flag in self.flags], dtype="string"
                ),
                "flag_session": pd.Series(
                    [pd.Timestamp(flag["flag_session"]) for flag in self.flags],
                    dtype="datetime64[ns]",
                ),
                "prev_session": pd.Series(
                    [
                        pd.Timestamp(flag["prev_session"])
                        if flag["prev_session"] is not None
                        else pd.NaT
                        for flag in self.flags
                    ],
                    dtype="datetime64[ns]",
                ),
                "next_session": pd.Series(
                    [
                        pd.Timestamp(flag["next_session"])
                        if flag["next_session"] is not None
                        else pd.NaT
                        for flag in self.flags
                    ],
                    dtype="datetime64[ns]",
                ),
                "next_beyond_development": pd.Series(
                    [flag["next_beyond_development"] for flag in self.flags],
                    dtype=bool,
                ),
            }
        )
        empty = pd.DataFrame()
        return Q.Overlay(
            flags=flag_frame,
            primitive_states=empty,
            input_states=empty,
            label_states=empty,
            liquidity_exposure=empty,
            identity={},
            pq_id=pq_id,
            pq_hash=pq_hash,
        )

    def wide_inputs(self) -> dict[str, pd.DataFrame]:
        index = pd.DatetimeIndex(pd.to_datetime(SESSIONS))
        daily = pd.DataFrame(
            0.0, index=index, columns=list(KEYS), dtype="float64"
        )
        for key in KEYS:
            for session in SESSIONS:
                daily.at[pd.Timestamp(session), key] = self._daily_rate(key, session)
        tri = (1.0 + daily).cumprod()
        dv = pd.DataFrame(1.0, index=index, columns=list(KEYS), dtype="float64")
        for key in KEYS:
            dv[key] = 1000.0 * (1.0 + 0.01 * ("ABCDEF".index(key) + 1)) * (
                1.0 + 0.0002 * np.arange(len(SESSIONS))
            )
        return {"daily_total_return": daily, "total_return_index": tri, "dollar_volume": dv}


# ---------------------------------------------------------------------------
# TrustedInput construction
# ---------------------------------------------------------------------------


def _trusted(frame: pd.DataFrame, semantic_id: str) -> TrustedInput:
    capability = DataCapability(
        semantic_id=semantic_id,
        frequency=Frequency.DAILY,
        observation_period=ObservationPeriod.PERIOD,
        units=Unit.FRACTION,
        history=int(frame.shape[0]),
        has_knowledge_date=True,
        has_positive_vintage_identity=False,
        revision_policies=frozenset({RevisionPolicy.POINT_IN_TIME}),
    )
    return TrustedInput(values=frame, capability=capability)


def build_inputs(store: SyntheticStore) -> dict[str, TrustedInput]:
    wide = store.wide_inputs()
    return {
        "daily_total_return": _trusted(
            wide["daily_total_return"], "daily_total_return"
        ),
        "total_return_index": _trusted(
            wide["total_return_index"], "total_return_index"
        ),
        "dollar_volume": _trusted(wide["dollar_volume"], "dollar_volume"),
    }


def alias_frames(
    spec: FactorSpec, inputs: Mapping[str, TrustedInput]
) -> dict[str, pd.DataFrame]:
    return {
        item.alias: inputs[item.requirement.semantic_id].values
        for item in spec.inputs
    }


def factor_panel(
    spec: FactorSpec,
    inputs: Mapping[str, TrustedInput],
    store: SyntheticStore,
) -> pd.DataFrame:
    """Evaluate ``spec`` and keep the universe members' rows at the formations."""

    result = EV.evaluate(spec, alias_frames(spec, inputs))
    panel = result.panel
    wide = (
        panel.pivot(index="date", columns="stock_id", values="value")
        .sort_index()
        .sort_index(axis=1)
    )
    rows: list[dict[str, Any]] = []
    universe: dict[date, list[str]] = {}
    for member in store.members:
        universe.setdefault(member.formation, []).append(member.key)
    for formation in sorted(universe):
        stamp = pd.Timestamp(formation)
        for key in sorted(universe[formation]):
            value = (
                float(wide.at[stamp, key])
                if stamp in wide.index and key in wide.columns
                else float("nan")
            )
            rows.append({"date": formation, "stock_id": key, "value": value})
    frame = pd.DataFrame(rows, columns=["date", "stock_id", "value"])
    frame["date"] = pd.to_datetime(frame["date"])
    frame["stock_id"] = frame["stock_id"].astype("string")
    frame["value"] = frame["value"].astype("float64")
    return frame


# ---------------------------------------------------------------------------
# Partition / declaration helpers
# ---------------------------------------------------------------------------


def partition() -> tuple[Partition, Fold, Fold]:
    fold = Fold(FoldRole.IS, FOLD_START, FOLD_END)
    holdout = Fold(FoldRole.HOLDOUT, HOLDOUT_START, HOLDOUT_END)
    return Partition(folds=(fold, holdout), split_rule="pqv2h-test"), fold, holdout


def base_declaration(
    spec: FactorSpec,
    panel: pd.DataFrame,
    **overrides: Any,
) -> B.CampaignEvaluationDeclaration:
    part, fold, _holdout = partition()
    fields: dict[str, Any] = {
        "precommitment_ref": "pqv2h-precommitment",
        "factor_spec_version": spec.version,
        "factor_panel_sha256": B._canonical_factor_hash(panel),
        "partition_id": part.partition_id,
        "fold_key": B.fold_key(fold),
        "universe": "TOP_1000",
        "horizon": HORIZON,
        "n_groups": 2,
        "winsorization": 0.0,
        "transaction_cost_bps": 10.0,
        "periods_per_year": 252,
        "weighting": "EQUAL",
        "primary_metric": B.spec_mod.MetricKey.LONG_SHORT,
        "basis": "GROSS",
        "screen_id": "pqv2h-screen",
        "screen_policy_hash": "pqv2h-policy",
    }
    fields.update(overrides)
    return B.CampaignEvaluationDeclaration(**fields)


def declaration(
    base: B.CampaignEvaluationDeclaration,
    *,
    identity_hash: str,
    pq_id: str = "pqv2h",
    pq_hash: str = "pqv2h-hash",
    **overrides: Any,
) -> "object":
    from smart_beta.dataset_v1 import pqv2_evaluation as E

    fields = {
        "base": base,
        "dataset_id": Q.DATASET_ID,
        "dataset_identity_hash": identity_hash,
        "pq_id": pq_id,
        "pq_hash": pq_hash,
        "methodology_sha256": C.METHODOLOGY_SHA256,
        "cs_id": C.CS_ID,
        "ed_id": C.ED_ID,
        "ce_id": C.CE_ID,
    }
    fields.update(overrides)
    return E.PQv2EvaluationDeclaration(**fields)
