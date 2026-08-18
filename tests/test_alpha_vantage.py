from datetime import date
from decimal import Decimal

import pytest

from stock_signal.providers.alpha_vantage import AlphaVantageProvider
from stock_signal.providers.base import (
    MarketDataAuthenticationError,
    MarketDataRateLimitError,
    MarketDataResponseError,
)
from stock_signal.providers.http import HttpClientError


class FakeHttpClient:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.calls = []

    def get_json(self, url, params, timeout):
        self.calls.append((url, params, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_fetch_daily_prices_filters_and_sorts() -> None:
    client = FakeHttpClient(
        [
            {
                "Time Series (Daily)": {
                    "2026-08-14": {
                        "1. open": "2500.0",
                        "2. high": "2550.0",
                        "3. low": "2480.0",
                        "4. close": "2535.0",
                        "5. volume": "1234567",
                    },
                    "2026-08-12": {
                        "1. open": "2450.0",
                        "2. high": "2510.0",
                        "3. low": "2440.0",
                        "4. close": "2500.0",
                        "5. volume": "1000000",
                    },
                    "2026-08-11": {
                        "1. open": "2400.0",
                        "2. high": "2460.0",
                        "3. low": "2390.0",
                        "4. close": "2450.0",
                        "5. volume": "900000",
                    },
                }
            }
        ]
    )
    provider = AlphaVantageProvider("test-key", http_client=client)

    bars = provider.fetch_daily_prices("7203.t", start=date(2026, 8, 12))

    assert [bar.trade_date for bar in bars] == [date(2026, 8, 12), date(2026, 8, 14)]
    assert bars[-1].symbol == "7203.T"
    assert bars[-1].close == Decimal("2535.0")
    assert bars[-1].volume == 1_234_567
    assert bars[-1].is_adjusted is False
    assert client.calls[0][1]["function"] == "TIME_SERIES_DAILY"
    assert client.calls[0][1]["outputsize"] == "compact"


def test_search_symbols() -> None:
    client = FakeHttpClient(
        [
            {
                "bestMatches": [
                    {
                        "1. symbol": "TEST.T",
                        "2. name": "Test Corporation",
                        "4. region": "Japan",
                        "8. currency": "JPY",
                        "9. matchScore": "0.9000",
                    }
                ]
            }
        ]
    )
    provider = AlphaVantageProvider("test-key", http_client=client)

    matches = provider.search_symbols("Test")

    assert matches[0].symbol == "TEST.T"
    assert matches[0].currency == "JPY"
    assert matches[0].match_score == Decimal("0.9000")


def test_missing_api_key_fails_before_http_request() -> None:
    provider = AlphaVantageProvider(None, http_client=FakeHttpClient([]))

    with pytest.raises(MarketDataAuthenticationError, match="required"):
        provider.fetch_daily_prices("TEST")


@pytest.mark.parametrize(
    ("payload", "expected_error"),
    [
        ({"Note": "API call frequency rate limit reached"}, MarketDataRateLimitError),
        ({"Error Message": "Invalid API call"}, MarketDataResponseError),
        ({"unexpected": {}}, MarketDataResponseError),
    ],
)
def test_api_errors_are_classified(payload, expected_error) -> None:
    provider = AlphaVantageProvider("test-key", http_client=FakeHttpClient([payload]))

    with pytest.raises(expected_error):
        provider.fetch_daily_prices("TEST")


def test_retries_transient_http_error() -> None:
    client = FakeHttpClient(
        [
            HttpClientError("temporary", status_code=503),
            {"Time Series (Daily)": {}},
        ]
    )
    delays = []
    provider = AlphaVantageProvider(
        "test-key",
        http_client=client,
        max_retries=1,
        sleep=delays.append,
    )

    assert provider.fetch_daily_prices("TEST") == []
    assert delays == [1]
    assert len(client.calls) == 2


def test_invalid_daily_bar_is_rejected() -> None:
    client = FakeHttpClient([{"Time Series (Daily)": {"2026-08-14": {"1. open": "1"}}}])
    provider = AlphaVantageProvider("test-key", http_client=client)

    with pytest.raises(MarketDataResponseError, match="invalid daily bar"):
        provider.fetch_daily_prices("TEST")
