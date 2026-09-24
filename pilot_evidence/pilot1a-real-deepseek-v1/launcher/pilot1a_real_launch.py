"""Pilot-1A single real run launcher (planner-provided; not part of the repo).

Runs the frozen run_pilot(...) path exactly once for the approved config.
The DeepSeek key is read with a no-echo prompt directly into THIS process
only; it is never printed, logged or written by this launcher, and run_pilot
captures it and deletes it from os.environ before any subprocess.
"""
import getpass
import json
import os
import sys
from pathlib import Path

APPROVED_HASH = "a9d45b97ed25a1e23239df82a15c66d3fd9fb7c82434a46fc431a57ea46162c1"
APPROVED_HEAD = "a901c4fec47b8d5be22b5f61a0caa534514195d1"
REPO = Path("/Users/dingningpei/Developer/personal/smart_beta/main")
CONFIG = Path("pilot_configs/pilot1a-real-deepseek-v1.json")
RESULT = Path(sys.argv[1])

for name in (
    "TIINGO_API_KEY", "TUSHARE_PROXY_TOKEN", "TUSHARE_BASIC_PROXY_TOKEN",
    "TUSHARE_API_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
    "OPENAI_API_KEY", "DEEPSEEK_API_KEY",
):
    os.environ.pop(name, None)

os.chdir(REPO)
sys.path.insert(0, str(REPO))

from smart_beta.pilot.config import load_config  # noqa: E402
from smart_beta.pilot.runner import run_pilot  # noqa: E402

config = load_config(CONFIG)
if config.config_hash() != APPROVED_HASH:
    raise SystemExit(f"config hash mismatch: {config.config_hash()}")

key = getpass.getpass("Enter DEEPSEEK_API_KEY for the Pilot-1A run (input hidden): ")
if not key.strip():
    raise SystemExit("no key entered; aborting before any run")
os.environ["DEEPSEEK_API_KEY"] = key.strip()
del key

print("key received (not shown); starting the single Pilot-1A real run ...", flush=True)
outcome = run_pilot(
    config,
    approved_config_hash=APPROVED_HASH,
    repo=REPO,
    config_path=CONFIG,
)
os.environ.pop("DEEPSEEK_API_KEY", None)
payload = outcome.to_dict()
RESULT.write_text(json.dumps(payload, indent=1, sort_keys=True, default=str))
print(json.dumps(payload, indent=1, sort_keys=True, default=str))
print("PILOT1A_RUN_FINISHED", flush=True)
