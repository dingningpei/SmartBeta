import dataclasses

import pytest

from smart_beta.config.settings import DEFAULT_SETTINGS, Settings


def test_default_settings_values():
    assert DEFAULT_SETTINGS.beta_rolling_window_months == 24
    assert DEFAULT_SETTINGS.momentum_lookback_months == 11
    assert DEFAULT_SETTINGS.n_portfolio_groups == 5


def test_settings_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        DEFAULT_SETTINGS.beta_rolling_window_months = 12


def test_settings_can_be_overridden_via_new_instance():
    custom = Settings(beta_rolling_window_months=60)
    assert custom.beta_rolling_window_months == 60
    # overriding one field doesn't affect the default instance
    assert DEFAULT_SETTINGS.beta_rolling_window_months == 24
