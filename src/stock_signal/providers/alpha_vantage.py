from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from stock_signal.domain.market_data import DailyBar, SymbolMatch
from stock_signal.domain.market_environment import MarketObservation
from stock_signal.providers.base import (
    MarketDataAuthenticationError,
    MarketDataProvider,
    MarketDataRateLimitError,
    MarketDataResponseError,
    MarketDataTransportError,
)
from stock_signal.providers.http import HttpClientError, JsonHttpClient, UrllibJsonHttpClient


class AlphaVantageProvider(MarketDataProvider):
    provider_name = "alpha_vantage"

    def __init__(
        self,
        api_key: str | None,
        *,
        base_url: str = "https://www.alphavantage.co/query",
        timeout_seconds: float = 15.0,
        max_retries: int = 2,
        http_client: JsonHttpClient | None = None,
        sleep: Callable[[float], None] = time.sleep,
        minimum_request_interval: float = 0.0,
    ) -> None:
        self._api_key = api_key.strip() if api_key else None
        self._base_url = base_url
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._http_client = http_client or UrllibJsonHttpClient()
        self._sleep = sleep
        self._minimum_request_interval = minimum_request_interval

    def fetch_market_environment(self) -> list[MarketObservation]:
        """米国市場終了後に、寄り付き前判定用の5指標を取得する。"""
        requests = (
            ("spy", "S&P 500 ETF", "USD", "TIME_SERIES_DAILY", "SPY"),
            ("qqq", "NASDAQ 100 ETF", "USD", "TIME_SERIES_DAILY", "QQQ"),
            ("wti", "WTI原油", "USD/barrel", "WTI", None),
            ("us10y", "米10年債利回り", "%", "TREASURY_YIELD", None),
            ("usdjpy", "ドル円", "JPY", "FX_DAILY", None),
        )
        observations = []
        for index, (key, label, unit, function, symbol) in enumerate(requests):
            try:
                values = self._fetch_environment_values(function, symbol)
            except (
                MarketDataRateLimitError,
                MarketDataResponseError,
                MarketDataTransportError,
            ):
                values = []
            if len(values) >= 2:
                observations.append(
                    MarketObservation(
                        indicator_key=key,
                        label=label,
                        observation_date=values[-1][0],
                        value=values[-1][1],
                        previous_value=values[-2][1],
                        unit=unit,
                        source="Alpha Vantage",
                    )
                )
            if index < len(requests) - 1 and self._minimum_request_interval > 0:
                self._sleep(self._minimum_request_interval)
        if not observations:
            raise MarketDataResponseError("寄り付き前の外部指標を取得できませんでした")
        return observations

    def _fetch_environment_values(
        self,
        function: str,
        symbol: str | None,
    ) -> list[tuple[date, float]]:
        """一つの外部指標を日付と値へ正規化する。"""
        if function == "TIME_SERIES_DAILY":
            payload = self._request(
                {
                    "function": function,
                    "symbol": str(symbol),
                    "outputsize": "compact",
                    "datatype": "json",
                }
            )
            return self._dated_values(payload, "Time Series (Daily)", "4. close")
        if function == "FX_DAILY":
            payload = self._request(
                {
                    "function": function,
                    "from_symbol": "USD",
                    "to_symbol": "JPY",
                    "outputsize": "compact",
                    "datatype": "json",
                }
            )
            return self._dated_values(payload, "Time Series FX (Daily)", "4. close")
        parameters = {"function": function, "interval": "daily"}
        if function == "TREASURY_YIELD":
            parameters["maturity"] = "10year"
        return self._macro_values(self._request(parameters))

    @staticmethod
    def _dated_values(
        payload: Mapping[str, Any], series_key: str, value_key: str
    ) -> list[tuple[date, float]]:
        raw_series = payload.get(series_key)
        if not isinstance(raw_series, Mapping):
            raise MarketDataResponseError(f"Alpha Vantage response did not contain {series_key}")
        try:
            return sorted(
                (date.fromisoformat(str(raw_date)), float(raw_value[value_key]))
                for raw_date, raw_value in raw_series.items()
                if isinstance(raw_value, Mapping)
            )
        except (KeyError, TypeError, ValueError) as error:
            raise MarketDataResponseError(f"{series_key}に不正な値があります") from error

    @staticmethod
    def _macro_values(payload: Mapping[str, Any]) -> list[tuple[date, float]]:
        raw_data = payload.get("data")
        if not isinstance(raw_data, list):
            raise MarketDataResponseError("Alpha Vantage response did not contain macro data")
        values = []
        try:
            for row in raw_data:
                if not isinstance(row, Mapping) or str(row.get("value", ".")) == ".":
                    continue
                values.append((date.fromisoformat(str(row["date"])), float(row["value"])))
        except (KeyError, TypeError, ValueError) as error:
            raise MarketDataResponseError("マクロ指標に不正な値があります") from error
        return sorted(values)

    def fetch_daily_prices(
        self,
        symbol: str,
        start: date | None = None,
        end: date | None = None,
    ) -> list[DailyBar]:
        normalized_symbol = symbol.strip().upper()
        if not normalized_symbol:
            raise ValueError("symbol must not be empty")
        if start and end and start > end:
            raise ValueError("start must not be after end")

        payload = self._request(
            {
                "function": "TIME_SERIES_DAILY",
                "symbol": normalized_symbol,
                "outputsize": "compact",
                "datatype": "json",
            }
        )
        raw_series = payload.get("Time Series (Daily)")
        if not isinstance(raw_series, Mapping):
            raise MarketDataResponseError("Alpha Vantage response did not contain daily prices")

        bars: list[DailyBar] = []
        for raw_date, raw_bar in raw_series.items():
            try:
                trade_date = date.fromisoformat(str(raw_date))
                if start and trade_date < start:
                    continue
                if end and trade_date > end:
                    continue
                if not isinstance(raw_bar, Mapping):
                    raise TypeError("daily bar is not an object")
                bars.append(
                    DailyBar(
                        symbol=normalized_symbol,
                        trade_date=trade_date,
                        open=Decimal(str(raw_bar["1. open"])),
                        high=Decimal(str(raw_bar["2. high"])),
                        low=Decimal(str(raw_bar["3. low"])),
                        close=Decimal(str(raw_bar["4. close"])),
                        volume=int(str(raw_bar["5. volume"])),
                        provider=self.provider_name,
                        is_adjusted=False,
                    )
                )
            except (KeyError, TypeError, ValueError, InvalidOperation) as error:
                raise MarketDataResponseError(
                    f"Alpha Vantage returned an invalid daily bar for {raw_date}"
                ) from error

        bars.sort(key=lambda bar: bar.trade_date)
        return bars

    def search_symbols(self, keywords: str) -> list[SymbolMatch]:
        normalized_keywords = keywords.strip()
        if not normalized_keywords:
            raise ValueError("keywords must not be empty")

        payload = self._request({"function": "SYMBOL_SEARCH", "keywords": normalized_keywords})
        raw_matches = payload.get("bestMatches")
        if not isinstance(raw_matches, list):
            raise MarketDataResponseError("Alpha Vantage response did not contain symbol matches")

        matches: list[SymbolMatch] = []
        for raw_match in raw_matches:
            try:
                if not isinstance(raw_match, Mapping):
                    raise TypeError("symbol match is not an object")
                matches.append(
                    SymbolMatch(
                        symbol=str(raw_match["1. symbol"]),
                        name=str(raw_match["2. name"]),
                        market=str(raw_match["4. region"]),
                        currency=str(raw_match["8. currency"]),
                        match_score=Decimal(str(raw_match["9. matchScore"])),
                    )
                )
            except (KeyError, TypeError, InvalidOperation) as error:
                raise MarketDataResponseError(
                    "Alpha Vantage returned an invalid symbol match"
                ) from error
        return matches

    def _request(self, params: Mapping[str, str]) -> Mapping[str, Any]:
        if not self._api_key:
            raise MarketDataAuthenticationError(
                "ALPHA_VANTAGE_API_KEY is required for Alpha Vantage requests"
            )

        request_params = {**params, "apikey": self._api_key}
        for attempt in range(self._max_retries + 1):
            try:
                payload = self._http_client.get_json(
                    self._base_url,
                    request_params,
                    self._timeout_seconds,
                )
            except HttpClientError as error:
                retryable = error.status_code == 429 or (
                    error.status_code is not None and error.status_code >= 500
                )
                if retryable and attempt < self._max_retries:
                    self._sleep(2**attempt)
                    continue
                if error.status_code == 429:
                    raise MarketDataRateLimitError(
                        "Alpha Vantage rate limit was reached"
                    ) from error
                raise MarketDataTransportError(str(error)) from error

            if not isinstance(payload, Mapping):
                raise MarketDataResponseError("Alpha Vantage returned a non-object response")
            self._raise_for_api_error(payload)
            return payload
        raise AssertionError("retry loop completed unexpectedly")

    @staticmethod
    def _raise_for_api_error(payload: Mapping[str, Any]) -> None:
        if "Note" in payload or "Information" in payload:
            message = str(payload.get("Note") or payload.get("Information"))
            lowered = message.lower()
            if "api key" in lowered and ("invalid" in lowered or "claim" in lowered):
                raise MarketDataAuthenticationError("Alpha Vantage rejected the API key")
            if "frequency" in lowered or "rate limit" in lowered or "requests per day" in lowered:
                raise MarketDataRateLimitError("Alpha Vantage rate limit was reached")
            raise MarketDataResponseError("Alpha Vantage could not complete the request")
        if "Error Message" in payload:
            raise MarketDataResponseError("Alpha Vantage rejected the requested function or symbol")
