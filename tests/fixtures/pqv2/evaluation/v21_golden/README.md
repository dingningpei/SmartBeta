# v2.1 C1 golden (PQ22-H)

`golden.json` is the PQ22-H C1-equivalence golden: the seven Campaign-1 C1
diagnostics produced by the **v2.1** evaluation path at the PQ22-H branch base
(master `73e6687`, PQ22-A/G/C merged), captured **before any PQ22-H edit**.

## Regeneration command

```bash
env -u TIINGO_API_KEY -u TUSHARE_PROXY_TOKEN -u TUSHARE_BASIC_PROXY_TOKEN \
    -u TUSHARE_API_TOKEN -u DEEPSEEK_API_KEY -u ANTHROPIC_API_KEY \
    -u ANTHROPIC_AUTH_TOKEN -u OPENAI_API_KEY -u SEC_CONTACT \
    /Users/dingningpei/Developer/personal/smart_beta/main/.venv/bin/python \
    tests/fixtures/pqv2/evaluation/v21_golden/generate.py
```

`generate.py` pins the branch's own `smart_beta` package on `sys.path` (an
editable install of another checkout may be present in site-packages) and
writes the normalized payload to `golden.json`.  It imports `_build_case` from
`tests/test_pqv2_evaluation.py`, so it is regenerated against the branch's
frozen evaluation semantics.

## Normalization (plan §1)

The golden is the diagnostics payload with the revision identities and the
hashes derived from them replaced by stable sentinels:

| Field | Why it is substituted |
|---|---|
| `methodology_sha256`, `ed_id`, `ce_id` | the v2.1 → v2.2 authority identity |
| `cs_id` | the authority CS identity (unchanged, but an identity field) |
| `methodology_version`, `base_methodology_sha256` | PQ22-H-only authority fields (absent in the v2.1 golden) |
| `pqv2_code_sha256` | the code-revision identity; it necessarily changes when `pqv2_evaluation.py` is edited |
| `declaration_hash`, per-cell `declaration_hash` / `cell_hash` | derived from the identity fields above |

Every other field — every period value, cell, cause set, status, count and
data hash — is compared exactly.  `tests/test_pqv2_evaluation.py` applies the
same projection on both sides, so a change to any non-identity diagnostic
field fails the C1-equivalence test (mutation V22-M12).
