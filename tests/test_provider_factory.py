import pytest

from stock_signal.config import Settings
from stock_signal.providers.alpha_vantage import AlphaVantageProvider
from stock_signal.providers.factory import create_market_data_provider
from stock_signal.providers.jquants import JQuantsProvider


def test_creates_alpha_vantage_provider() -> None:
    provider = create_market_data_provider(Settings(alpha_vantage_api_key="test-key"))
    assert isinstance(provider, AlphaVantageProvider)


def test_creates_jquants_provider() -> None:
    provider = create_market_data_provider(
        Settings(market_data_provider="jquants", jquants_api_key="test-key")
    )
    assert isinstance(provider, JQuantsProvider)


def test_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError, match="未対応"):
        create_market_data_provider(Settings(market_data_provider="unknown"))
