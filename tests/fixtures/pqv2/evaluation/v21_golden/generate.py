#!/usr/bin/env python
"""Regenerate ``golden.json`` for the PQ22-H C1-equivalence test.

The golden is the seven Campaign-1 C1 diagnostics produced by the **v2.1**
evaluation path at the PQ22-H branch base (master ``73e6687`` A, G, C merged),
before any PQ22-H edit.  It is *not* a byte-for-byte serialization of the
pre-edit ``PQv2Diagnostics``: the revision identities and the hashes derived
from them are replaced by stable sentinels, so the v2.2 replay can be compared
field by field after substituting exactly those identity fields (plan §1).

Sentinel substitution set (the identity fields and their derived hashes):

* ``methodology_sha256`` / ``cs_id`` / ``ed_id`` / ``ce_id``
* ``pqv2_code_sha256`` (the code-revision identity; it necessarily changes
  when this module is edited)
* ``declaration_hash`` and every cell's ``declaration_hash`` / ``cell_hash``
  (all derived from the identity fields above)
* the PQ22-H-only ``methodology_version`` / ``base_methodology_sha256``
  (absent at the branch base)

Regenerate exactly as:

    env -u TIINGO_API_KEY -u TUSHARE_PROXY_TOKEN -u TUSHARE_BASIC_PROXY_TOKEN \
        -u TUSHARE_API_TOKEN -u DEEPSEEK_API_KEY -u ANTHROPIC_API_KEY \
        -u ANTHROPIC_AUTH_TOKEN -u OPENAI_API_KEY -u SEC_CONTACT \
        /Users/dingningpei/Developer/personal/smart_beta/main/.venv/bin/python \
        tests/fixtures/pqv2/evaluation/v21_golden/generate.py

No network call is made; the fixture is synthetic.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[4]

# Import the branch's own ``smart_beta`` package even when an editable install of
# another checkout is present in site-packages.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

IDENTITY_SENTINELS = {
    "methodology_sha256": "<METHODOLOGY_SHA256>",
    "cs_id": "<CS_ID>",
    "ed_id": "<ED_ID>",
    "ce_id": "<CE_ID>",
    "pqv2_code_sha256": "<PQV2_CODE_SHA256>",
    "declaration_hash": "<DECLARATION_HASH>",
    "methodology_version": "<METHODOLOGY_VERSION>",
    "base_methodology_sha256": "<BASE_METHODOLOGY_SHA256>",
}

HYPOTHESES = ("C1-H1", "C1-H2", "C1-H3", "C1-H4", "C1-H5", "C1-H6", "C1-H7")


def normalize(body: dict) -> dict:
    """Return ``body`` with every identity/derived-hash field sentinel-ed."""

    out = copy.deepcopy(body)
    for key, sentinel in IDENTITY_SENTINELS.items():
        if key in out:
            out[key] = sentinel
    for cell in out.get("cells", []):
        cell["declaration_hash"] = IDENTITY_SENTINELS["declaration_hash"]
        cell["cell_hash"] = "<CELL_HASH>"
    return out


def _load_test_module():
    path = REPO_ROOT / "tests" / "test_pqv2_evaluation.py"
    spec = importlib.util.spec_from_file_location("pqv2h_golden_tests", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["pqv2h_golden_tests"] = module
    spec.loader.exec_module(module)
    return module


def build_golden() -> dict:
    module = _load_test_module()
    golden: dict = {}
    with tempfile.TemporaryDirectory() as tmp:
        for hypothesis in HYPOTHESES:
            case = module._build_case(Path(tmp), hypothesis=hypothesis)
            golden[hypothesis] = normalize(case.diagnostics.to_dict())
    return golden


def main() -> None:
    golden = build_golden()
    path = HERE / "golden.json"
    path.write_text(
        json.dumps(golden, sort_keys=True, indent=1, default=str) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {path} ({len(golden)} hypotheses)")


if __name__ == "__main__":
    main()
