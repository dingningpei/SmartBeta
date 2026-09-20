#!/usr/bin/env python3
"""P5B-4 Run-2 resume: staged migration + bounded re-probe + classification.

This is a **task-owned evidence tool, not shipped package code and not a
test**. It is never imported by ``smart_beta`` and lives outside ``tests/`` so
pytest never collects it.

It does exactly what the planner-authorized P5B-4 Run-2 asks, in order:

1. Construct a ``StagedCaptureSet`` (from the P5B-5 module
   ``scripts/staged_capture.py``, imported read-only) over the same 36
   ``(ts_code, endpoint)`` identities Run 1 used.
2. **Migration (zero live calls).** Stage the 13 Run-1 successes with
   ``ingest_body()``, using their EXACT original raw bodies (parsed from
   ``docs/phase5b/pilot_universe/raw/``) and the EXACT Run-1 ``retrieved_at``
   timestamp from the Run-1 ledger (``_provenance.retrieved_at_utc`` -- Run 1
   did not capture a distinct per-call timestamp; using the single recorded
   value verbatim is the only non-fabricating choice). Then verify
   deterministically that exactly 13 identities are staged-valid and that the
   missing set is exactly the 23 identities Run 1 left unresolved. Any
   mismatch STOPS with a non-zero exit, before any live call.
3. **Run 2 (live).** Make exactly ONE transport attempt for each of the 23
   missing identities only -- same transport/endpoint semantics as Run 1
   (``ProxyTushareClient.record(samples=1)``), no retry/backoff/sleep/
   substitution. Every attempt is appended to an append-only JSONL ledger and
   every raw body is preserved. The 13 already-staged identities are never
   touched or re-requested. Successful 200s are staged via ``ingest_body()``.
4. Classify all 36 identities into exactly four buckets from the captured
   status/body evidence (never inferred).
5. Exit ``0`` only when all 36 are SUCCESS/ACCESSIBLE; otherwise ``3``
   (BLOCKED), with no Run 3.

Usage::

    # migration + verification only (ZERO live calls) -- run this first
    .venv/bin/python scripts/resume_phase5b_pilot_universe.py --token-file ~/.secrets

    # the same, plus the live Run-2 attempts
    .venv/bin/python scripts/resume_phase5b_pilot_universe.py --run2 --token-file ~/.secrets
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from smart_beta.vendors.tushare.proxy_client import (  # noqa: E402
    TOKEN_ENV_VAR,
    ProxyTushareClient,
)

DOCS = REPO_ROOT / "docs" / "phase5b" / "pilot_universe"
RUN1_LEDGER = DOCS / "entitlement_probe_ledger.json"
RUN1_RAW = DOCS / "raw"

RESUME_DIR = DOCS / "resume"
STAGING_DIR = RESUME_DIR / "staging"
STAGING_LEDGER = RESUME_DIR / "staging_ledger.json"
RUN2_RAW = RESUME_DIR / "raw_run2"
RUN2_LEDGER = RESUME_DIR / "run2_attempts.jsonl"
CLASSIFICATION = RESUME_DIR / "classification.json"

BUCKET_SUCCESS = "SUCCESS/ACCESSIBLE"
BUCKET_DENIAL = "EXPLICIT ENTITLEMENT DENIAL"
BUCKET_TRANSPORT = "UPSTREAM/TRANSPORT FAILURE"
BUCKET_OTHER = "OTHER UNRESOLVED"

TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})

#: Text markers for an *explicit* entitlement/permission denial. Matched
#: case-insensitively against the raw response body only; a transient
#: ``upstream_pool_exhausted`` or a parameter error never matches.
_ENTITLEMENT_MARKERS = (
    "not entitled",
    "entitlement",
    "permission",
    "not authorized",
    "unauthorized",
    "forbidden",
    "plan tier",
    "limited to the",
    "subscribe",
    "subscription",
    "无权",
    "权限",
    "订阅",
)


# ---------------------------------------------------------------------------
# Dynamic module loads (scripts/ is not an importable package)
# ---------------------------------------------------------------------------
def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    # dataclass() resolves the defining module through sys.modules.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sc = _load_module("staged_capture_p5b4", REPO_ROOT / "scripts" / "staged_capture.py")
probe = _load_module(
    "probe_p5b4", REPO_ROOT / "scripts" / "probe_phase5b_pilot_universe.py"
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Spec construction
# ---------------------------------------------------------------------------
def build_specs() -> list[Any]:
    """The same 36 ``(ts_code, endpoint)`` identities Run 1 used.

    Endpoint and params are byte-identical to Run 1's request identities, so
    the framework's live transport reproduces Run 1's exact request shape.
    """
    specs = []
    for ps in probe.probe_specs():
        specs.append(
            sc.RequestSpec.build(
                endpoint=ps.endpoint,
                params=dict(ps.params),
                label=ps.label,
                metadata={
                    "ts_code": ps.ts_code,
                    "pilot_identity": ps.label,
                    "task": "P5B-4",
                },
            )
        )
    return specs


def build_capture() -> Any:
    return sc.StagedCaptureSet(
        build_specs(),
        staging_dir=STAGING_DIR,
        ledger_path=STAGING_LEDGER,
        canonical_dir=None,
    )


def _label_to_spec(capture: Any) -> dict[str, Any]:
    return {spec.label: spec for spec in capture.specs}


# ---------------------------------------------------------------------------
# Step 2 -- migration (zero live calls) + deterministic verification
# ---------------------------------------------------------------------------
def migrate(
    capture: Any, run1: Mapping[str, Any]
) -> tuple[dict[str, dict], list[Any], list[str]]:
    """Stage the 13 Run-1 successes; verify the 13/23 split exactly.

    Returns ``(valid, missing_specs, run1_ok_labels)``. Raises
    ``RuntimeError`` on any verification failure -- the caller must stop
    before any live call.
    """
    retrieved_at = run1["_provenance"]["retrieved_at_utc"]
    label_to_spec = _label_to_spec(capture)
    run1_ok_labels: list[str] = []
    run1_unresolved_labels: set[str] = set()

    for result in run1["results"]:
        label = f"{result['ts_code']}::{result['endpoint']}"
        if label not in label_to_spec:
            raise RuntimeError(f"Run-1 identity {label!r} not in capture set")
        if result["outcome"] == "ok":
            raw_path = DOCS / result["raw_file"]
            body = json.loads(raw_path.read_text(encoding="utf-8"))
            spec = label_to_spec[label]
            # Canonical-consistency guard: re-ingesting an identical body is
            # a no-op; a differing body fails closed inside the framework.
            capture.ingest_body(spec, body, retrieved_at=retrieved_at)
            run1_ok_labels.append(label)
        else:
            run1_unresolved_labels.add(label)

    valid = capture.load_valid()
    valid_labels = {
        spec.label for spec in capture.specs if spec.identity in valid
    }
    missing_specs = [
        spec for spec in capture.specs if spec.identity not in valid
    ]
    missing_labels = {spec.label for spec in missing_specs}

    problems: list[str] = []
    if len(valid) != 13:
        problems.append(f"expected 13 staged-valid, got {len(valid)}")
    if set(valid_labels) != set(run1_ok_labels):
        problems.append("staged-valid set is not exactly the Run-1 success set")
    if len(missing_specs) != 23:
        problems.append(f"expected 23 missing, got {len(missing_specs)}")
    if missing_labels != run1_unresolved_labels:
        problems.append(
            "missing set is not exactly the Run-1 unresolved set: "
            f"only-in-capture={sorted(missing_labels - run1_unresolved_labels)}, "
            f"only-in-Run1={sorted(run1_unresolved_labels - missing_labels)}"
        )

    # Byte/provenance verification: each staged record's body must equal the
    # exact parsed Run-1 raw body, and its timestamp the exact Run-1 value.
    for label in sorted(set(valid_labels) & set(run1_ok_labels)):
        spec = label_to_spec[label]
        record = valid[spec.identity]
        result = next(
            r
            for r in run1["results"]
            if f"{r['ts_code']}::{r['endpoint']}" == label
        )
        raw_body = json.loads(
            (DOCS / result["raw_file"]).read_text(encoding="utf-8")
        )
        if record["body"] != raw_body:
            problems.append(f"{label}: staged body != Run-1 raw body")
        if record["retrieved_at_utc"] != retrieved_at:
            problems.append(
                f"{label}: retrieved_at {record['retrieved_at_utc']!r} != "
                f"Run-1 {retrieved_at!r}"
            )
        if record["content_sha256"] != sc.content_sha256(raw_body):
            problems.append(f"{label}: staged content hash mismatch")

    if problems:
        raise RuntimeError(
            "migration verification FAILED (no live call made): "
            + "; ".join(problems)
        )
    return valid, missing_specs, sorted(set(valid_labels))


# ---------------------------------------------------------------------------
# Step 3 -- Run 2 (bounded, one attempt per missing identity)
# ---------------------------------------------------------------------------
def _live_transport(client: ProxyTushareClient):
    """One attempt per call: ``record(samples=1)`` -> one transport call."""

    def transport(endpoint: str, params: Mapping[str, Any]):
        return client.record(endpoint, samples=1, **params)

    return transport


def _attempt_bucket(status: int | None, body: object) -> tuple[str, str | None]:
    """Classify ONE captured attempt from its actual status/body evidence."""
    if status == 200:
        classification = probe.classify(status, body)
        if classification["outcome"] == "ok":
            return BUCKET_SUCCESS, None
        text = json.dumps(body, ensure_ascii=False).lower()
        if any(m in text for m in _ENTITLEMENT_MARKERS):
            return BUCKET_DENIAL, classification["error_code"]
        return BUCKET_OTHER, classification["error_code"]
    if status in (401, 403):
        return BUCKET_DENIAL, f"http_{status}"
    if status == 400:
        text = json.dumps(body, ensure_ascii=False).lower()
        if any(m in text for m in _ENTITLEMENT_MARKERS):
            return BUCKET_DENIAL, "http_400_entitlement_text"
        return BUCKET_OTHER, "http_400_request_rejected"
    if status is None or status == 0:
        return BUCKET_TRANSPORT, "transport_exception"
    if status in TRANSIENT_STATUSES:
        error = body.get("error") if isinstance(body, Mapping) else None
        return BUCKET_TRANSPORT, error or f"http_{status}"
    return BUCKET_OTHER, f"http_{status}"


def _write_attempt(record: dict) -> None:
    """Append one attempt to the append-only JSONL ledger, flushed at once."""
    RUN2_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with RUN2_LEDGER.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def run2(capture: Any, missing_specs: list[Any], client: ProxyTushareClient) -> int:
    """Exactly one live attempt for each missing identity; append-only."""
    RUN2_RAW.mkdir(parents=True, exist_ok=True)
    transport = _live_transport(client)
    for spec in missing_specs:
        ts_code = spec.metadata["ts_code"]
        attempted_at = _now()
        try:
            status, body = transport(spec.endpoint, dict(spec.params))
            error = None
        except Exception as exc:  # noqa: BLE001 - every failure is evidence
            status, body, error = None, None, repr(exc)

        raw_path = RUN2_RAW / f"{ts_code}__{spec.endpoint}.json"
        raw_payload = (
            body if body is not None else {"_transport_exception": error}
        )
        raw_path.write_text(
            json.dumps(raw_payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        bucket, error_code = _attempt_bucket(status, body)
        staged = False
        if bucket == BUCKET_SUCCESS:
            capture.ingest_body(spec, body, retrieved_at=attempted_at)
            staged = True

        _write_attempt(
            {
                "run": "run2",
                "ts_code": ts_code,
                "endpoint": spec.endpoint,
                "params": dict(spec.params),
                "attempted_at_utc": attempted_at,
                "http_status": status,
                "error": error,
                "error_code": error_code,
                "bucket": bucket,
                "staged": staged,
                "body_sha256": sc.content_sha256(body) if body is not None else None,
                "raw_file": str(raw_path.relative_to(DOCS)),
            }
        )
        print(
            f"RUN2 {spec.label:<28} HTTP {str(status):<5} "
            f"bucket={bucket:<26} staged={staged}",
            flush=True,
        )
    return 0


# ---------------------------------------------------------------------------
# Step 4 -- combined 4-bucket classification
# ---------------------------------------------------------------------------
def build_classification(
    run1: Mapping[str, Any], attempts: list[dict]
) -> dict:
    attempt_by_label = {
        f"{a['ts_code']}::{a['endpoint']}": a for a in attempts
    }
    rows: list[dict] = []
    for result in run1["results"]:
        label = f"{result['ts_code']}::{result['endpoint']}"
        run1_body = json.loads(
            (DOCS / result["raw_file"]).read_text(encoding="utf-8")
        )
        run1_bucket, run1_code = _attempt_bucket(
            result["http_status"], run1_body
        )
        evidence = [
            {
                "run": "run1",
                "http_status": result["http_status"],
                "error_code": run1_code,
                "bucket": run1_bucket,
                "raw_file": result["raw_file"],
            }
        ]
        buckets = [run1_bucket]
        attempt = attempt_by_label.get(label)
        if attempt is not None:
            buckets.append(attempt["bucket"])
            evidence.append(
                {
                    "run": "run2",
                    "http_status": attempt["http_status"],
                    "error_code": attempt["error_code"],
                    "bucket": attempt["bucket"],
                    "raw_file": attempt["raw_file"],
                }
            )
        if BUCKET_SUCCESS in buckets:
            combined = BUCKET_SUCCESS
        elif BUCKET_DENIAL in buckets:
            combined = BUCKET_DENIAL
        elif BUCKET_TRANSPORT in buckets:
            combined = BUCKET_TRANSPORT
        else:
            combined = BUCKET_OTHER
        rows.append(
            {
                "ts_code": result["ts_code"],
                "endpoint": result["endpoint"],
                "identity": label,
                "combined_bucket": combined,
                "attempt_buckets": buckets,
                "attempted_in_run2": attempt is not None,
                "evidence": evidence,
            }
        )

    counts = {b: 0 for b in (BUCKET_SUCCESS, BUCKET_DENIAL, BUCKET_TRANSPORT, BUCKET_OTHER)}
    for row in rows:
        counts[row["combined_bucket"]] += 1
    unresolved = [
        row["identity"]
        for row in rows
        if row["combined_bucket"] != BUCKET_SUCCESS
    ]
    return {
        "_provenance": {
            "task": "P5B-4 Run-2",
            "note": (
                "Classification from ACTUAL captured status/body evidence "
                "(Run 1 + Run 2). A transport failure is never converted "
                "into an entitlement denial, or vice versa."
            ),
            "precedence": (
                "SUCCESS/ACCESSIBLE > EXPLICIT ENTITLEMENT DENIAL > "
                "UPSTREAM/TRANSPORT FAILURE > OTHER UNRESOLVED"
            ),
            "generated_at_utc": _now(),
            "proxy_determinism": "NOT CERTIFIED",
            "proxy_sufficient_for_production": "NO",
            "proxy_observed_not_official": True,
        },
        "counts": counts,
        "n_identities": len(rows),
        "n_success": counts[BUCKET_SUCCESS],
        "n_denial": counts[BUCKET_DENIAL],
        "n_transport_failure": counts[BUCKET_TRANSPORT],
        "n_other_unresolved": counts[BUCKET_OTHER],
        "remaining_unresolved": unresolved,
        "identities": rows,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _load_token(token_file: Path | None) -> str | None:
    if token_file is None:
        return os.environ.get(TOKEN_ENV_VAR)
    if not token_file.is_file():
        return None
    for line in token_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if line.startswith(f"{TOKEN_ENV_VAR}="):
            return line.split("=", 1)[1].strip().strip("'\"")
    return None


def _read_attempts() -> list[dict]:
    if not RUN2_LEDGER.is_file():
        return []
    return [
        json.loads(line)
        for line in RUN2_LEDGER.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run2",
        action="store_true",
        help="after migration verification, perform the live one-attempt pass",
    )
    parser.add_argument(
        "--token-file", type=Path, default=None, help="file with the token export line"
    )
    args = parser.parse_args(argv)

    run1 = json.loads(RUN1_LEDGER.read_text(encoding="utf-8"))
    capture = build_capture()
    valid, missing_specs, run1_ok_labels = migrate(capture, run1)
    print(
        f"migration OK: staged-valid={len(valid)} (Run-1 successes), "
        f"missing={len(missing_specs)} (Run-1 unresolved). Zero live calls."
    )
    print("staged-valid:", ", ".join(run1_ok_labels))

    if not args.run2:
        # Migration-only: still write the combined classification from the
        # current (Run-1-only) evidence, but perform no live call.
        attempts = _read_attempts()
        classification = build_classification(run1, attempts)
        CLASSIFICATION.parent.mkdir(parents=True, exist_ok=True)
        CLASSIFICATION.write_text(
            json.dumps(classification, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {CLASSIFICATION} (no live calls)")
        return 0

    if RUN2_LEDGER.exists():
        print(
            f"refusing Run 2: {RUN2_LEDGER} already exists. Exactly one "
            "attempt per missing identity is the rule; delete it "
            "deliberately to re-run.",
            file=sys.stderr,
        )
        return 4

    token = _load_token(args.token_file)
    if not token:
        print(
            f"error: no token. Set {TOKEN_ENV_VAR} or pass --token-file.",
            file=sys.stderr,
        )
        return 2
    os.environ[TOKEN_ENV_VAR] = token

    client = ProxyTushareClient(max_attempts=1)
    run2(capture, missing_specs, client)

    attempts = _read_attempts()
    classification = build_classification(run1, attempts)
    CLASSIFICATION.write_text(
        json.dumps(classification, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print("\n=== combined classification ===")
    print(json.dumps(classification["counts"], indent=2))
    print("remaining unresolved:")
    for identity in classification["remaining_unresolved"]:
        print("  -", identity)
    return 0 if classification["n_success"] == classification["n_identities"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
