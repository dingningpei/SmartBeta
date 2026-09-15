"""Minimal factor/characteristic interface.

A *factor* is any per-``(date, stock_id)`` signal derived from the
long-format panels defined in :mod:`smart_beta.data.schema`. The single
contract every factor implements is :meth:`Factor.compute`, which maps an
input panel to a three-column long-format frame::

    date | stock_id | value

A not-estimable observation is represented by ``NaN`` -- never by ``0`` and
never by silently dropping the row. The original notebooks pre-allocated
``np.zeros((nr, nc))`` for the beta matrix, which made "no estimate"
indistinguishable from a genuine near-zero beta (Phase 1 Task B, bug #2).

The concrete Phase 1 estimators live in :mod:`smart_beta.factors.beta`.
"""

from __future__ import annotations

import abc

import pandas as pd

from smart_beta.data.schema import DATE_COL, STOCK_COL, PanelSchema

#: Column holding the factor/characteristic value in a long-format factor panel.
VALUE_COL = "value"

# TODO(schema): promote this to smart_beta/data/schema.py once the shared
# schema module is unfrozen. Task B may not edit shared files, so the factor
# panel schema is defined locally here instead.
FACTOR_PANEL_SCHEMA = PanelSchema(
    key_columns=(DATE_COL, STOCK_COL),
    dtypes={DATE_COL: "datetime", STOCK_COL: "string", VALUE_COL: "float"},
)


class Factor(abc.ABC):
    """Abstract base class for a factor/characteristic estimator."""

    #: Human-readable identifier; subclasses override.
    name: str = "factor"

    @abc.abstractmethod
    def compute(self, panel: pd.DataFrame) -> pd.DataFrame:
        """Compute the factor and return a long-format ``(date, stock_id, value)`` frame.

        Parameters
        ----------
        panel:
            Input observation panel with at least ``date`` and ``stock_id``
            columns. Implementations must not mutate it.

        Returns
        -------
        pandas.DataFrame
            Columns ``date, stock_id, value``. ``value`` is ``NaN`` wherever
            the factor cannot be estimated for a given observation.
        """

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"{type(self).__name__}(name={self.name!r})"
