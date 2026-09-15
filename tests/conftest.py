import pytest

from smart_beta.data.sources.synthetic import SyntheticDataSource


@pytest.fixture
def synthetic_source() -> SyntheticDataSource:
    """A deterministic synthetic data source shared across tests."""
    return SyntheticDataSource(seed=42)
