# D5 QC test fixtures

D5's tests do not read a static batch here.  Every test synthesizes a tiny D2
batch under a pytest `tmp_path` with the real D2 acquisition tool driven by an
in-process fake transport, then runs the real D3 normalizer and D4 identity
resolver on it, and finally runs `smart_beta.dataset_v1.qc`.  This keeps the
suite offline (no provider call, no socket, no access to the real
`data_store`) and lets each test seed exactly one L0–L3 fact.

The fixture directory is reserved for any static specimen that cannot be
synthesized; the frozen D5 spec requires none, so it holds only this note.
