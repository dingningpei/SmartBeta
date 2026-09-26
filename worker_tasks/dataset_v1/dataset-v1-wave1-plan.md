# Discovery Dataset v1 — Wave 1 (D1 + D2) plan

**Authority:** `docs/discovery_dataset_v1_contract.md`. Its canonical SHA-256
is `d03f35549777e5a94046f06dd0621acfc3a1dedb7a4b40adc55fdae911fa80e3`. The
contract is frozen and prevails over this plan wherever the two disagree.

**Base:** master `7348a315d12eabf68118697361de421b68c674e5`, plus the planning
commit that adds this directory.

**Scope:** D1 (candidate-pool builder) and D2 (resumable acquisition tool and
manifest) only. D3–D8, live acquisition, the Starter smoke test and the Power
purchase are out of scope.

## Tasks, ownership and parallelism

| Task | Spec | Owns (new files only) |
|---|---|---|
| D1 | `task-d1-candidate-pool.md` | `smart_beta/dataset_v1/candidate_pool.py`, `tests/test_dataset_v1_candidate_pool.py`, `tests/fixtures/dataset_v1/d1/**` |
| D2 | `task-d2-acquisition.md` | `smart_beta/dataset_v1/acquisition.py`, `tests/test_dataset_v1_acquisition.py`, `tests/fixtures/dataset_v1/d2/**` |

- **Shared package file:** the planner already created
  `smart_beta/dataset_v1/__init__.py` (docstring only) on master. Neither
  worker may edit it.
- **Parallelism: D1 and D2 run in parallel.** D2 depends only on the frozen
  expected-pool interface below. It never imports D1 code, and it tests
  against its own fixture that follows that interface. No other file is
  shared.
- **Files neither task may touch:** every existing file, including
  `smart_beta/vendors/**`, sealed Phase-6–10 code, the contract,
  `pilot_evidence/**` and Phase-5B resources.

## Frozen interface — expected-pool artifact

D1 produces this artifact; D2 consumes it. The file is UTF-8 canonical JSON:
`json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)`
followed by `"\n"`.

`expected_pool_sha256` is the SHA-256 of the file's exact bytes.

```json
{
  "schema": "discovery-dataset-v1-expected-pool-v1",
  "contract_sha256": "<64 hex>",
  "snapshot_sha256": "<64 hex of the supported-tickers input file bytes>",
  "acquisition_start": "2015-01-02",
  "research_start": "2016-01-04",
  "C": "YYYY-MM-DD",
  "tickers": [
    {
      "ticker": "<vendor ticker, verbatim>",
      "duplicate_ticker": false,
      "listed_intervals": [
        {"security_key": "TIINGO:<ticker>:<startDate>", "exchange": "<exchange>",
         "startDate": "YYYY-MM-DD", "endDate": "YYYY-MM-DD"}
      ]
    }
  ],
  "counts": {"<name>": 0},
  "exclusions": {"<reason>": 0}
}
```

**Rules:**
- `tickers` is sorted by `ticker`.
- `listed_intervals` is sorted by (`startDate`, `security_key`).
- Each ticker appears once. D2 fetches once per ticker, as contract §4
  requires.
- `duplicate_ticker` is true when a ticker has more than one passing listed
  interval.

## Security (both tasks)

- **No live provider call** in implementation or tests; every test uses a
  fake transport or fixtures. **No credentials are needed or read by tests.**
- **Test runs** remove the provider keys from the process environment for
  that run only:
  `env -u TIINGO_API_KEY -u TUSHARE_PROXY_TOKEN -u TUSHARE_BASIC_PROXY_TOKEN -u TUSHARE_API_TOKEN -u DEEPSEEK_API_KEY -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN -u OPENAI_API_KEY PYTHONPATH=$PWD /Users/dingningpei/Developer/personal/smart_beta/main/.venv/bin/python -m pytest -q <args>`
- **Never print, log or persist a credential value.**
- **No bulk data** is created, and nothing is written under
  `~/Developer/personal/smart_beta/data_store/` by tests. Tests use `tmp_path`.

## Stop conditions

A worker stops and reports, without guessing, if any of the following occurs:
- the contract is materially silent on a behaviour it needs;
- it needs a file outside its ownership;
- it would need a live call;
- it finds a contradiction between this plan and the contract.
