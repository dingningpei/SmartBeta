#!/usr/bin/env python3
"""One-time live recorder + Gate A artifact producer for P5A-2.

This is a **recording tool, not shipped package code and not a test**: it is
never imported by ``smart_beta`` and it lives outside ``tests/`` so pytest
never collects it. It performs the one-time live Tiingo fetch documented in
the P5A-2 completion report, writes the raw response bodies plus a provenance
manifest under ``tests/fixtures/tiingo/phase5a_gate_a/``, and then runs the
*same* offline fixture-replay path the test suite uses to produce the initial
Gate A artifacts under ``docs/phase5a/gate_a/`` -- so the live-data step is
reproducible rather than an unrecorded manual action.

Run it once, by hand, with a real key in the environment::

    TIINGO_API_KEY=... .venv/bin/python scripts/fetch_phase5a_gate_a_fixtures.py

The API key is read only from the environment and is never logged or written
to disk.

The frozen Gate A window is 2026-06-15..2026-09-15. EOD prices are recorded
with the same 10-calendar-day lookback ``TiingoPITSource`` itself uses, so the
return *on* the window start is computable offline from the recording alone.

Gate A disposition enforced by this script
------------------------------------------
The frozen Gate A universe is ``("AAPL", "MSFT", "JPM")``, amended (see
``worker_tasks/phase5a/phase5a-plan.md``'s "Universe amendment history")
from the originally frozen ``("AAPL", "MSFT", "GOOGL")`` after a live
entitlement probe found a real, persistent HTTP 400 plan-tier body for
GOOGL -- *"Free and Power plans are limited to the DOW 30."* -- on the
required ``get_fundamentals_daily`` call. GOOGL was replaced by JPM (a
long-tenured DOW-30 constituent) only after a single bounded live
entitlement probe of JPM's own ``get_fundamentals_daily`` call returned a
real HTTP 200. This universe is never downgraded or substituted by this
script itself -- any further ticker change requires its own reviewed spec
amendment, exactly like this one. When any required call is non-200 this
script **writes nothing**: it reports a ``BLOCKED`` disposition and exits,
so a rate-limited or entitlement-limited run can never overwrite a previously
valid fixture set. No market cap is fabricated, no ticker is substituted
inline, no market cap is derived from another endpoint, and the universe is
never weakened.
"""

from __future__ import annotations

import argparse
import dataclasses
import io
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import pandas as pd

# Make the repository importable when run as a plain script from anywhere.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from smart_beta.config.settings import DEFAULT_SETTINGS  # noqa: E402
from smart_beta.pipelines.capm_pilot import (  # noqa: E402
    derive_trading_dates,
    run_capm_pilot,
)
from smart_beta.pit.view import PointInTimeView  # noqa: E402
from smart_beta.research_inputs.risk_free_treasury import (  # noqa: E402
    TreasuryBillRiskFreeProvider,
)
from smart_beta.vendors.tiingo.client import (  # noqa: E402
    API_KEY_ENV_VAR,
    TiingoAPIError,
    TiingoClient,
    replay_transport,
)
from smart_beta.vendors.tiingo.source import TiingoPITSource  # noqa: E402

# ---------------------------------------------------------------------------
# Frozen Gate A parameters (chosen before any price/return fixture recording)
# ---------------------------------------------------------------------------
GATE_A_START = "2026-06-15"
GATE_A_END = "2026-09-15"
CANDIDATE_TICKERS = ("AAPL", "MSFT", "JPM")
EOD_LOOKBACK_DAYS = 10

FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "tiingo" / "phase5a_gate_a"
ARTIFACT_DIR = REPO_ROOT / "docs" / "phase5a" / "gate_a"
FRED_FIXTURE = (
    REPO_ROOT
    / "tests"
    / "fixtures"
    / "risk_free"
    / "treasury"
    / "dgs3mo_2025-09-01_2026-09-16.json"
)

_DAILY_PATH = "/tiingo/fundamentals/{ticker}/daily"
_EOD_PATH = "/tiingo/daily/{ticker}/prices"
_META_PATH = "/tiingo/daily/{ticker}"


def _eod_start() -> str:
    """The EOD fetch start: the window start minus the trusted lookback."""
    return (date.fromisoformat(GATE_A_START) - timedelta(days=EOD_LOOKBACK_DAYS)).isoformat()


def _record(call: Callable[[], object]) -> tuple[int, object]:
    """Run one live call and return ``(status_code, body)``; a non-200
    response is captured as data (via :class:`TiingoAPIError`), never treated
    as a script error, so any real error body is preserved as evidence
    rather than crashing the script uninformatively."""
    try:
        return 200, call()
    except TiingoAPIError as exc:
        return exc.status_code, exc.body


def _write_body(path: Path, body: object) -> None:
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=FIXTURE_DIR,
        help=f"fixture output directory (default: {FIXTURE_DIR})",
    )
    parser.add_argument(
        "--artifacts",
        type=Path,
        default=ARTIFACT_DIR,
        help=f"artifact output directory (default: {ARTIFACT_DIR})",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help=(
            "skip the live fetch and regenerate the artifacts from the "
            "already-recorded fixture set and manifest under --out"
        ),
    )
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    args.artifacts.mkdir(parents=True, exist_ok=True)
    eod_start = _eod_start()

    recordings: dict[str, dict[str, object]] = {}
    bodies: dict[str, object] = {}

    if args.offline:
        manifest = json.loads(
            (args.out / "manifest.json").read_text(encoding="utf-8")
        )
        if manifest.get("_provenance", {}).get("gate_a_disposition") == "BLOCKED":
            print(
                "BLOCKED: the committed manifest records a blocked Gate A "
                "recording (see _provenance.gate_a_disposition); refusing to "
                "regenerate artifacts from an invalid fixture set.",
                file=sys.stderr,
            )
            return 3
        recordings = dict(manifest["recordings"])
        for filename in recordings:
            bodies[filename] = json.loads(
                (args.out / filename).read_text(encoding="utf-8")
            )
        print(f"offline: reusing {len(recordings)} recordings in {args.out}")
    else:
        api_key = os.environ.get(API_KEY_ENV_VAR)
        if not api_key:
            print(
                f"error: {API_KEY_ENV_VAR} is not set. This recorder needs a "
                "real Tiingo key; it never logs or writes the key itself.",
                file=sys.stderr,
            )
            return 2

        live_client = TiingoClient(api_key=api_key)

        # Collect every live response in memory, validate it, and only then
        # write -- so a rate-limited or partial run can never overwrite a good
        # committed fixture set with error bodies. Every ticker in the
        # (amended, entitlement-probed) universe is expected to return 200;
        # no ticker's non-200 is specially pre-accepted as expected evidence
        # any more (unlike the original GOOGL run, whose known 400 was
        # explicitly special-cased here before the amendment).
        call_specs: list[
            tuple[str, str, dict[str, str], Callable[[], object]]
        ] = []
        for ticker in CANDIDATE_TICKERS:
            low = ticker.lower()
            call_specs.extend(
                [
                    (
                        f"{low}_meta.json",
                        _META_PATH.format(ticker=ticker),
                        {},
                        lambda t=ticker: live_client.get_meta(t),
                    ),
                    (
                        f"{low}_eod_prices_{eod_start}_{GATE_A_END}.json",
                        _EOD_PATH.format(ticker=ticker),
                        {"startDate": eod_start, "endDate": GATE_A_END},
                        lambda t=ticker: live_client.get_eod_prices(
                            t, eod_start, GATE_A_END
                        ),
                    ),
                    (
                        f"{low}_fundamentals_daily_"
                        f"{GATE_A_START}_{GATE_A_END}.json",
                        _DAILY_PATH.format(ticker=ticker),
                        {"startDate": GATE_A_START, "endDate": GATE_A_END},
                        lambda t=ticker: live_client.get_fundamentals_daily(
                            t, GATE_A_START, GATE_A_END
                        ),
                    ),
                ]
            )

        collected: list[tuple[str, str, dict[str, str], int, object]] = []
        for filename, path, params, call in call_specs:
            status, body = _record(call)
            collected.append((filename, path, params, status, body))

        # Fail closed: validate the *entire* set in memory before writing a
        # single byte. Any non-200 for the amended, already entitlement-probed
        # universe is an unexpected finding -- nothing is written, no ticker
        # is substituted inline, and no universe is silently downgraded.
        blockers: list[str] = []
        for filename, path, params, status, body in collected:
            if status == 200:
                continue
            detail = body.get("detail") if isinstance(body, dict) else ""
            if status == 429:
                blockers.append(
                    f"GATE-A-429-class temporary rate limit: {path} -> HTTP "
                    f"{status} {body!r}"
                )
            elif status == 400 and "DOW 30" in str(detail):
                blockers.append(
                    f"unexpected DOW-30 plan-tier restriction (this ticker "
                    f"was already entitlement-probed successfully -- "
                    f"investigate before retrying): {path} -> HTTP {status} "
                    f"{body!r}"
                )
            else:
                blockers.append(
                    f"unexpected non-200: {path} -> HTTP {status} {body!r}"
                )
        if blockers:
            print(
                "BLOCKED: live recording did not return a complete, valid "
                "fixture set. Refusing to write or overwrite anything.\n",
                file=sys.stderr,
            )
            for blocker in blockers:
                print(f"  - {blocker}", file=sys.stderr)
            return 3

        for filename, path, params, status, body in collected:
            _write_body(args.out / filename, body)
            bodies[filename] = body
            entry: dict[str, object] = {
                "url_path": path,
                "params": params,
                "status_code": status,
            }
            if path.startswith("/tiingo/fundamentals/") and path.endswith(
                "/daily"
            ):
                entry["plan_tier_limited"] = status == 400
            recordings[filename] = entry

        retrieved_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        manifest = {
            "_provenance": {
                "live_recorded": True,
                "recorded_at": date.today().isoformat(),
                "retrieved_at_utc": retrieved_at,
                "source": "Tiingo",
                "retrieval_method": (
                    "Live HTTPS GETs through TiingoClient with the key read "
                    f"from the {API_KEY_ENV_VAR} environment variable; the key "
                    "is never logged or written"
                ),
                "api_key_stored": False,
                "gate_a_window": {"start": GATE_A_START, "end": GATE_A_END},
                "candidate_universe": list(CANDIDATE_TICKERS),
                "eod_lookback_start": eod_start,
                "gate_a_disposition": "RUN",
                "note": (
                    "Raw Tiingo response bodies (verbatim, no wrapper). "
                    "Written only when every required call returned 200; a "
                    "non-200 half-run is never written."
                ),
            },
            "recordings": recordings,
        }
        (args.out / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )

    # ------------------------------------------------------------------
    # Offline replay + Gate A artifact production
    # ------------------------------------------------------------------
    path_recordings: dict[str, tuple[int, object]] = {}
    for filename, entry in recordings.items():
        if filename.endswith("manifest.json"):
            continue
        path_recordings[str(entry["url_path"])] = (
            int(entry["status_code"]),
            bodies[filename],
        )
    offline_client = TiingoClient(transport=replay_transport(path_recordings))

    fred = json.loads(FRED_FIXTURE.read_text(encoding="utf-8"))
    fred_frame = pd.read_csv(io.StringIO(fred["raw_csv"]))

    source = TiingoPITSource(list(CANDIDATE_TICKERS), client=offline_client)
    view = PointInTimeView(source)
    trading_dates = derive_trading_dates(view, GATE_A_START, GATE_A_END)
    risk_free = TreasuryBillRiskFreeProvider(
        fred_frame, trading_dates=trading_dates
    )
    # GATE-A-2 (see phase5a-plan.md's "Gate-A-only settings amendment"):
    # DEFAULT_SETTINGS.bottom_mcap_exclude_pct=0.30 ("CH-3 style small-cap
    # exclusion") mathematically cannot be satisfied by the smallest-cap
    # member of a 2-3 name cross-section, regardless of its real size.
    # This is a Gate-A-only call-site configuration choice -- it does not
    # modify DEFAULT_SETTINGS, USZeroVolumeTradabilityPolicy, or
    # _above_cap_cutoff. Zero-volume and listing-age checks stay active.
    gate_a_settings = dataclasses.replace(
        DEFAULT_SETTINGS, bottom_mcap_exclude_pct=0.0
    )
    result = run_capm_pilot(
        CANDIDATE_TICKERS,
        GATE_A_START,
        GATE_A_END,
        tiingo_client=offline_client,
        risk_free=risk_free,
        settings=gate_a_settings,
    )

    # Artifact A: CSV (literal spec) and JSON (committed review copy).
    result.factor.to_csv(args.artifacts / "artifact_a_market_factor.csv", index=False)
    (args.artifacts / "artifact_a_market_factor.json").write_text(
        result.factor.to_json(
            orient="records", indent=2, date_format="iso", double_precision=15
        )
        + "\n",
        encoding="utf-8",
    )

    # Artifact B: constituent diagnostics + risk-free diagnostics.
    result.diagnostics.to_csv(
        args.artifacts / "artifact_b_constituent_diagnostics.csv", index=False
    )
    (args.artifacts / "artifact_b_constituent_diagnostics.json").write_text(
        result.diagnostics.to_json(
            orient="records", indent=2, date_format="iso", double_precision=15
        )
        + "\n",
        encoding="utf-8",
    )
    result.risk_free_diagnostics.to_csv(
        args.artifacts / "artifact_b_risk_free_diagnostics.csv", index=False
    )
    (args.artifacts / "artifact_b_risk_free_diagnostics.json").write_text(
        result.risk_free_diagnostics.to_json(
            orient="records", indent=2, date_format="iso", double_precision=15
        )
        + "\n",
        encoding="utf-8",
    )

    # Artifact C: statistical summary.
    (args.artifacts / "artifact_c_statistical_summary.json").write_text(
        json.dumps(result.statistics, indent=2) + "\n", encoding="utf-8"
    )

    print(f"wrote {len(recordings)} recordings + manifest.json to {args.out}")
    print(f"wrote Gate A artifacts to {args.artifacts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
