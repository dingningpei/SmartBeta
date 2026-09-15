# SmartBeta

Portfolio approach and Regression approach to test smart beta in financial market

## Project layout

The original analysis (`Beta.ipynb`, `BetaEffect.ipynb`, `CAPM.ipynb`,
`Factor_Effect.ipynb`, still present at the repo root) is being migrated into
a tested `smart_beta/` package:

- `smart_beta/data` — canonical panel schema (`schema.py`) and the
  `DataSource` interface (`data/sources/base.py`) that vendor integrations
  and the synthetic fixture generator (`data/sources/synthetic.py`) implement.
- `smart_beta/config` — named, overridable constants (window lengths,
  thresholds, cost assumptions) replacing the magic numbers in the original
  notebooks.
- `smart_beta/factors`, `smart_beta/benchmarks`, `smart_beta/engines`,
  `smart_beta/pipelines` — factor construction, benchmark asset-pricing
  models, analysis engines (portfolio sorts, Fama-MacBeth, inference), and
  the orchestration layer, populated in later phases.

## Development

```
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

