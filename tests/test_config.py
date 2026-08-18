from datetime import date

import pytest

from stock_signal.config import (
    ConfigurationError,
    Settings,
    plan_history_start,
    subtract_years,
)


def test_defaults(monkeypatch) -> None:
    for name in (
        "APP_ENV",
        "LOG_LEVEL",
        "DATABASE_URL",
        "MARKET_DATA_PROVIDER",
        "NOTIFICATION_DRY_RUN",
        "ALPHA_VANTAGE_API_KEY",
        "JQUANTS_API_KEY",
        "JQUANTS_PLAN",
        "SLACK_WEBHOOK_URL",
        "MARKET_SCREENING_LIMIT",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env()

    assert settings.app_env == "development"
    assert settings.notification_dry_run is True
    assert settings.database_url == (
        "postgresql+psycopg://tomoshibiyori:tomoshibiyori@db:5432/tomoshibiyori"
    )
    assert settings.jquants_plan == "light"
    assert settings.jquants_rate_limit_per_minute == 60
    assert settings.jquants_request_interval_seconds == 1.05
    assert settings.jquants_history_years == 5
    assert settings.jquants_data_delay_days == 0
    assert settings.market_screening_limit == 500


def test_invalid_boolean(monkeypatch) -> None:
    monkeypatch.setenv("NOTIFICATION_DRY_RUN", "sometimes")

    with pytest.raises(ConfigurationError, match="boolean"):
        Settings.from_env()


def test_invalid_jquants_plan(monkeypatch) -> None:
    monkeypatch.setenv("JQUANTS_PLAN", "enterprise")

    with pytest.raises(ConfigurationError, match="JQUANTS_PLAN"):
        Settings.from_env()


def test_subtract_years_handles_leap_day() -> None:
    assert subtract_years(date(2024, 2, 29), 5) == date(2019, 2, 28)
    assert plan_history_start(date(2026, 8, 15), 5) == date(2021, 8, 16)


def test_safe_dict_does_not_reveal_secrets(monkeypatch) -> None:
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "secret-key")
    monkeypatch.setenv("JQUANTS_API_KEY", "jquants-secret")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://example.invalid/secret")

    safe_settings = Settings.from_env().safe_dict()

    assert safe_settings["alpha_vantage_api_key"] == "configured"
    assert safe_settings["jquants_api_key"] == "configured"
    assert safe_settings["slack_webhook_url"] == "configured"
    assert "secret" not in repr(safe_settings)
