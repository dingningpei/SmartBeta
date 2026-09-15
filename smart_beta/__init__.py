"""smart_beta: modular research package for China A-share factor investing.

Replaces the original standalone notebooks (Beta.ipynb, BetaEffect.ipynb,
CAPM.ipynb, Factor_Effect.ipynb) with a tested, schema-driven package. See
package subdirectories for the layered architecture:

- data:       schema contract, DataSource interface, universe construction
- factors:    beta and characteristic construction
- benchmarks: CAPM / FF3 / FF5 / CH-3 / CH-4 factor construction
- engines:    portfolio sorting, Fama-MacBeth, spanning tests, inference
- pipelines:  orchestration layer consumed by the notebooks/ wrappers
"""
