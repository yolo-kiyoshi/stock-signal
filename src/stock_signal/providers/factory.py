from stock_signal.config import Settings
from stock_signal.providers.alpha_vantage import AlphaVantageProvider
from stock_signal.providers.base import MarketDataProvider
from stock_signal.providers.jquants import JQuantsProvider


def create_market_data_provider(settings: Settings) -> MarketDataProvider:
    return create_market_data_provider_for(settings.market_data_provider, settings)


def create_market_data_provider_for(
    provider_name: str, settings: Settings
) -> MarketDataProvider:
    """指定名の取得元を設定から生成する。"""
    if provider_name == "alpha_vantage":
        return AlphaVantageProvider(api_key=settings.alpha_vantage_api_key)
    if provider_name == "jquants":
        return JQuantsProvider(
            api_key=settings.jquants_api_key,
            minimum_request_interval=settings.jquants_request_interval_seconds,
        )
    raise ValueError(f"未対応の市場データ取得元です: {provider_name}")
