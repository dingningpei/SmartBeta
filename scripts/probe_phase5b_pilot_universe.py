#!/usr/bin/env python3
"""P5B-4 pilot-universe bounded entitlement probe.

This is a **recording/evidence tool, not shipped package code and not a
test**: it is never imported by ``smart_beta`` and it lives outside
``tests/`` so pytest never collects it. It performs the one-time, bounded,
credentialed live probe of the third-party Tushare proxy documented in the
Phase 5B plan's "Pilot universe" section, and writes the probe result -- plus
every raw response body, including error bodies -- under
``docs/phase5b/pilot_universe/``.

Frozen procedure (Phase 5B plan, "Pilot universe")
--------------------------------------------------
* 3-5 real, currently-listed, large-cap A-shares;
* at least one ``.SH`` and one ``.SZ`` name (both exchanges' calendar);
* full-window listing history (plus 250-trading-day turnover lookback and at
  least one fiscal-report cycle for E/P);
* no name whose *only* in-window fiscal year is known (from Phase 4D-B's own
  findings) to raise an unresolved ``TushareConflictingVintageError``.

The universe itself is frozen in ``docs/phase5b/pilot_universe/README.md``;
this script only probes it. It never substitutes a name inline, never
downgrades the universe, and never routes around a non-200.

Exactly-one-call discipline
---------------------------
The probe makes **exactly ONE** bounded live call per ``(ts_code,
endpoint)`` identity. It uses ``ProxyTushareClient.record(samples=1)``,
which issues a single transport call and performs no retry, no backoff and
no sleep loop. A non-200 (including a transient ``429``/``503`` or the
proxy's own ``date_range_too_large`` rejection) is captured verbatim as
evidence and drives a fail-closed ``PROBE_BLOCKED`` disposition -- it is
never retried away, and no name/endpoint is silently skipped.

Fail-closed overwrite rule
--------------------------
A probe ledger is written only when one does not already exist. A blocked
run still writes its ledger and raw bodies (the blocker is evidence), but a
pre-existing ledger is never overwritten: re-probing requires deliberately
deleting the prior ledger first, so an accidental second run cannot burn a
second call per identity. The token is read from the
``TUSHARE_PROXY_TOKEN`` environment variable (or a file named by
``--token-file``, for the local ``~/.secrets`` convenience) and is never
logged, hashed or written to disk.

Usage
-----
Live probe (one call per identity; run once)::

    .venv/bin/python scripts/probe_phase5b_pilot_universe.py \
        --token-file ~/.secrets

Network-free self-test of the classification/fail-closed logic::

    .venv/bin/python scripts/probe_phase5b_pilot_universe.py --self-test
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from smart_beta.vendors.tushare.client import Transport  # noqa: E402
from smart_beta.vendors.tushare.proxy_client import (  # noqa: E402
    TOKEN_ENV_VAR,
    ProxyTushareClient,
)

DEFAULT_OUT_DIR = REPO_ROOT / "docs" / "phase5b" / "pilot_universe"

# ---------------------------------------------------------------------------
# Frozen probe parameters (chosen before any live call; see the P5B-4 report)
# ---------------------------------------------------------------------------
#: The frozen P5B-4 pilot universe. 000001.SZ is the Phase 4D-B clean
#: specimen; the other three are long-tenured, still-listed, large-cap names
#: spanning both exchanges and both boards (SZ main / SH main).
PILOT_UNIVERSE: tuple[str, ...] = (
    "000001.SZ",  # 平安银行 Ping An Bank   (SZ main, Phase 4D-B specimen)
    "600519.SH",  # 贵州茅台 Kweichow Moutai (SH main)
    "601318.SH",  # 中国平安 Ping An Insurance (SH main)
    "000858.SZ",  # 五粮液 Wuliangye        (SZ main)
)

#: Bounded recent probe window for every date-ranged endpoint. Deliberately
#: a single calendar month, far inside the proxy's documented <=366-day cap.
PROBE_START = "2026-08-03"
PROBE_END = "2026-08-31"

#: Bounded fundamentals period: the most recent annual report a late-2026
#: pilot resolves E/P against. One period is enough for an entitlement probe.
PROBE_PERIOD = "20251231"

#: The exact fields ``TushareAShareSource.get_listing_info`` requests.
STOCK_BASIC_FIELDS = "ts_code,name,list_date,delist_date,list_status"

#: The Tushare endpoints the P5B-2/P5B-3 PIT-native CH3/CH4 pilot needs,
#: with the same parameter shapes the assembled source actually issues.
#: (``bak_basic`` is deliberately absent: the assembled source never fetches
#: it, so it is not an entitlement the pilot depends on.)
DAILY_RANGE_ENDPOINTS = ("daily", "daily_basic", "stk_limit")
NO_RANGE_ENDPOINTS = ("suspend_d", "dividend")
STOCK_BASIC_ENDPOINT = "stock_basic"
FUNDAMENTAL_ENDPOINTS = ("income", "balancesheet", "fina_indicator")


@dataclass(frozen=True)
class ProbeSpec:
    """One ``(ts_code, endpoint)`` identity: exactly one live call."""

    ts_code: str
    endpoint: str
    params: dict[str, str] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"{self.ts_code}::{self.endpoint}"


def _compact(iso: str) -> str:
    return iso.replace("-", "")


def probe_specs(universe: Sequence[str] = PILOT_UNIVERSE) -> list[ProbeSpec]:
    """Every identity this probe makes exactly one live call for."""
    specs: list[ProbeSpec] = []
    for ts_code in universe:
        for endpoint in DAILY_RANGE_ENDPOINTS:
            specs.append(
                ProbeSpec(
                    ts_code,
                    endpoint,
                    {
                        "ts_code": ts_code,
                        "start_date": _compact(PROBE_START),
                        "end_date": _compact(PROBE_END),
                    },
                )
            )
        for endpoint in NO_RANGE_ENDPOINTS:
            specs.append(ProbeSpec(ts_code, endpoint, {"ts_code": ts_code}))
        specs.append(
            ProbeSpec(
                ts_code,
                STOCK_BASIC_ENDPOINT,
                {"ts_code": ts_code, "fields": STOCK_BASIC_FIELDS},
            )
        )
        for endpoint in FUNDAMENTAL_ENDPOINTS:
            specs.append(
                ProbeSpec(
                    ts_code, endpoint, {"ts_code": ts_code, "period": PROBE_PERIOD}
                )
            )
    return specs


# ---------------------------------------------------------------------------
# Response classification (pure, self-testable)
# ---------------------------------------------------------------------------
def _items(body: object) -> list:
    if isinstance(body, Mapping):
        data = body.get("data")
        if isinstance(data, Mapping):
            return list(data.get("items") or [])
        return list(body.get("items") or [])
    return []


def _fields(body: object) -> list:
    if isinstance(body, Mapping):
        data = body.get("data")
        if isinstance(data, Mapping):
            return list(data.get("fields") or [])
        return list(body.get("fields") or [])
    return []


def _proxy_code(body: object) -> object:
    if isinstance(body, Mapping):
        return body.get("code")
    return None


def classify(status: int | None, body: object) -> dict:
    """Classify one raw ``(status, body)`` response as ``ok`` or ``error``.

    Success requires BOTH an HTTP 200 and a well-formed envelope (proxy
    ``code`` 0/absent, with a resolvable ``data``/``items`` payload). A 200
    that carries a non-zero proxy ``code`` is an error, never a success.
    """
    if status != 200:
        return {
            "outcome": "error",
            "error_code": (
                body.get("error") if isinstance(body, Mapping) else None
            )
            or f"http_{status}",
            "message": (
                body.get("msg") or body.get("message")
                if isinstance(body, Mapping)
                else None
            ),
        }
    code = _proxy_code(body)
    if code not in (0, None):
        return {
            "outcome": "error",
            "error_code": str(code),
            "message": body.get("msg") if isinstance(body, Mapping) else None,
        }
    if not isinstance(body, Mapping) or not (
        isinstance(body.get("data"), Mapping)
        or "items" in body
        or "fields" in body
    ):
        return {
            "outcome": "error",
            "error_code": "malformed_response",
            "message": "HTTP 200 without a usable data payload",
        }
    return {"outcome": "ok", "error_code": None, "message": None}


def _sha256(body: object) -> str:
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Probe execution
# ---------------------------------------------------------------------------
def _safe_filename(spec: ProbeSpec) -> str:
    return f"{spec.ts_code}__{spec.endpoint}.json"


def _result_entry(spec: ProbeSpec, status: int | None, body: object) -> dict:
    classification = classify(status, body)
    entry = {
        "ts_code": spec.ts_code,
        "endpoint": spec.endpoint,
        "params": dict(spec.params),
        "http_status": status,
        "proxy_code": _proxy_code(body),
        "outcome": classification["outcome"],
        "error_code": classification["error_code"],
        "message": classification["message"],
        "n_items": len(_items(body)),
        "fields": _fields(body),
        "body_sha256": _sha256(body),
        "raw_file": f"raw/{_safe_filename(spec)}",
    }
    return entry


def run_probe(
    *,
    client: ProxyTushareClient,
    out_dir: Path,
    allow_existing: bool = False,
) -> int:
    """Run the probe once and write its evidence. Returns an exit code.

    ``0`` = every identity returned a well-formed 200 (``PROBE_PASS``);
    ``3`` = at least one identity did not (``PROBE_BLOCKED``). A blocked run
    still writes every outcome and raw body; it just never claims the
    universe is entitlement-cleared.
    """
    ledger_path = out_dir / "entitlement_probe_ledger.json"
    if ledger_path.exists() and not allow_existing:
        print(
            f"refusing to re-probe: {ledger_path} already exists. Exactly one "
            "live call per (ts_code, endpoint) identity is the frozen rule; "
            "delete the ledger deliberately to re-run.",
            file=sys.stderr,
        )
        return 4

    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    specs = probe_specs()
    results: list[dict] = []

    for spec in specs:
        try:
            status, body = client.record(
                spec.endpoint, samples=1, **spec.params
            )
            entry = _result_entry(spec, status, body)
        except Exception as exc:  # noqa: BLE001 - every transport failure is evidence
            entry = _result_entry(spec, None, None)
            entry["outcome"] = "error"
            entry["error_code"] = type(exc).__name__
            entry["message"] = str(exc)
            body = {"_transport_exception": type(exc).__name__, "message": str(exc)}
        else:
            body = body

        (raw_dir / _safe_filename(spec)).write_text(
            json.dumps(body, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        results.append(entry)
        print(
            f"{spec.label:<28} HTTP {entry['http_status']!s:<5} "
            f"outcome={entry['outcome']!s:<6} n_items={entry['n_items']} "
            f"error={entry['error_code']}",
            flush=True,
        )

    blockers = [r for r in results if r["outcome"] != "ok"]
    disposition = "PROBE_BLOCKED" if blockers else "PROBE_PASS"

    ledger = {
        "_provenance": {
            "task": "P5B-4",
            "live_recorded": True,
            "access_path": (
                "proxy:pcd.mobcvb.cn/tushare/pro (X-API-Key from "
                f"{TOKEN_ENV_VAR})"
            ),
            "retrieved_at_utc": datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "universe": list(PILOT_UNIVERSE),
            "probe_window": {"start": PROBE_START, "end": PROBE_END},
            "fundamentals_period": PROBE_PERIOD,
            "one_call_per_identity": True,
            "retry_policy": (
                "none -- ProxyTushareClient.record(samples=1); exactly one "
                "transport call per identity, no retry/backoff/sleep"
            ),
            "token_stored": False,
            "note": (
                "Proxy-observed live evidence, NOT direct official-Tushare "
                "behavior. Raw bodies are written verbatim, including any "
                "error body; a blocked run is never presented as a cleared "
                "universe."
            ),
        },
        "disposition": disposition,
        "n_identities": len(specs),
        "n_ok": len(results) - len(blockers),
        "n_blocked": len(blockers),
        "blockers": [
            {
                "ts_code": r["ts_code"],
                "endpoint": r["endpoint"],
                "http_status": r["http_status"],
                "error_code": r["error_code"],
                "message": r["message"],
            }
            for r in blockers
        ],
        "results": results,
    }
    ledger_path.write_text(
        json.dumps(ledger, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"disposition: {disposition} -> {ledger_path}", flush=True)
    return 0 if disposition == "PROBE_PASS" else 3


# ---------------------------------------------------------------------------
# Network-free self-test
# ---------------------------------------------------------------------------
def _self_test() -> int:
    """Exercise PASS and BLOCKED classification with a synthetic transport."""

    def ok_transport(api_name: str, params: Mapping[str, str]):
        return 200, {
            "code": 0,
            "msg": None,
            "data": {"fields": ["ts_code"], "items": [[params.get("ts_code")]]},
        }

    def blocked_transport(api_name: str, params: Mapping[str, str]):
        if api_name == "fina_indicator" and params.get("ts_code") == "600519.SH":
            return 429, {"error": "rate_limited", "msg": "too many requests"}
        return ok_transport(api_name, params)

    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        ok_dir = Path(tmp) / "pass"
        rc = run_probe(
            client=ProxyTushareClient(transport=ok_transport), out_dir=ok_dir
        )
        if rc != 0:
            failures.append(f"all-200 probe returned {rc}, expected 0")
        ledger = json.loads(
            (ok_dir / "entitlement_probe_ledger.json").read_text()
        )
        if ledger["disposition"] != "PROBE_PASS":
            failures.append(f"all-200 disposition {ledger['disposition']!r}")
        if ledger["n_identities"] != len(probe_specs()):
            failures.append("all-200 identity count mismatch")
        # A pre-existing ledger must never be overwritten by a re-run.
        rc_again = run_probe(
            client=ProxyTushareClient(transport=ok_transport), out_dir=ok_dir
        )
        if rc_again != 4:
            failures.append(f"re-run of an existing ledger returned {rc_again}")

        blocked_dir = Path(tmp) / "blocked"
        rc = run_probe(
            client=ProxyTushareClient(transport=blocked_transport),
            out_dir=blocked_dir,
        )
        if rc != 3:
            failures.append(f"429 probe returned {rc}, expected 3")
        ledger = json.loads(
            (blocked_dir / "entitlement_probe_ledger.json").read_text()
        )
        if ledger["disposition"] != "PROBE_BLOCKED":
            failures.append(f"429 disposition {ledger['disposition']!r}")
        if ledger["n_blocked"] != 1:
            failures.append(f"429 n_blocked {ledger['n_blocked']}, expected 1")
        if ledger["blockers"][0]["endpoint"] != "fina_indicator":
            failures.append("429 blocker names the wrong endpoint")
        if ledger["blockers"][0]["http_status"] != 429:
            failures.append("429 blocker status not preserved")

    if failures:
        for failure in failures:
            print(f"SELF-TEST FAIL: {failure}", file=sys.stderr)
        return 1
    print("SELF-TEST PASS: PASS/BLOCKED classification + overwrite refusal")
    return 0


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT_DIR, help="evidence directory"
    )
    parser.add_argument(
        "--token-file",
        type=Path,
        default=None,
        help=(
            f"file containing an 'export {TOKEN_ENV_VAR}=...' line (e.g. "
            "~/.secrets); used only to populate the process environment for "
            "the call and never logged or written"
        ),
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run the network-free classification/fail-closed self-test",
    )
    parser.add_argument(
        "--allow-existing",
        action="store_true",
        help=(
            "deliberately allow overwriting an existing probe ledger "
            "(default: refuse, so one run is one call per identity)"
        ),
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    token = _load_token(args.token_file)
    if not token:
        print(
            f"error: no token. Set {TOKEN_ENV_VAR} in the environment or pass "
            "--token-file pointing at a file with an "
            f"'export {TOKEN_ENV_VAR}=...' line. The token is never logged.",
            file=sys.stderr,
        )
        return 2
    # ProxyTushareClient reads the token from the environment at call time.
    os.environ[TOKEN_ENV_VAR] = token

    client = ProxyTushareClient()
    return run_probe(
        client=client, out_dir=args.out, allow_existing=args.allow_existing
    )


if __name__ == "__main__":
    raise SystemExit(main())
