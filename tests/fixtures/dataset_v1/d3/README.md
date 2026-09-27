# D3 normalizer test fixtures

D3 tests do not read a static batch here. Every test builds a **synthetic D2
batch** under a pytest `tmp_path` — a canonical expected-pool artifact, the
real D2 `create_batch`/`run_batch` tool driven by a fake in-process transport,
and synthetic raw bodies that follow the frozen 13-field contract. This keeps
the tests offline (no provider call, no socket, no access to the real
`data_store`) and lets each test tamper with exactly one manifest/raw fact.

The fixture directory is reserved for any future static specimen that cannot
be synthesized; none is required by the frozen D3 spec, so it holds only this
note.
