import gzip
from datetime import date
from decimal import Decimal

import pytest

from stock_signal.providers.base import (
    MarketDataAuthenticationError,
    MarketDataRateLimitError,
    MarketDataResponseError,
)
from stock_signal.providers.http import HttpClientError
from stock_signal.providers.jquants import JQuantsProvider


class FakeHttpClient:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.calls = []

    def get_json(self, url, params, timeout, *, headers=None):
        self.calls.append((url, params, timeout, headers))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeBinaryHttpClient:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.calls = []

    def get_bytes(self, url, timeout):
        self.calls.append((url, timeout))
        return self.content


def daily_record(record_date: str, close: float) -> dict[str, object]:
    return {
        "Date": record_date,
        "Code": "72030",
        "O": close - 10,
        "H": close + 20,
        "L": close - 20,
        "C": close,
        "Vo": 1_000_000,
        "AdjFactor": 1.0,
        "AdjO": close - 10,
        "AdjH": close + 20,
        "AdjL": close - 20,
        "AdjC": close,
        "AdjVo": 1_000_000,
    }


def test_fetches_adjusted_daily_prices_with_api_key_header() -> None:
    client = FakeHttpClient(
        [{"data": [daily_record("2024-01-05", 2500), daily_record("2024-01-04", 2480)]}]
    )
    provider = JQuantsProvider(
        "secret", http_client=client, minimum_request_interval=0
    )

    bars = provider.fetch_daily_prices(
        "7203", start=date(2024, 1, 1), end=date(2024, 1, 31)
    )

    assert [bar.trade_date for bar in bars] == [date(2024, 1, 4), date(2024, 1, 5)]
    assert bars[-1].symbol == "7203"
    assert bars[-1].close == Decimal("2500")
    assert bars[-1].is_adjusted is True
    assert bars[-1].raw_close == Decimal("2500")
    assert bars[-1].adjustment_factor == Decimal("1.0")
    assert client.calls[0][0].endswith("/v2/equities/bars/daily")
    assert client.calls[0][1]["code"] == "7203"
    assert client.calls[0][3] == {"x-api-key": "secret"}


def test_fetches_adjusted_daily_prices_for_all_market_without_code() -> None:
    client = FakeHttpClient([{"data": [daily_record("2026-08-14", 2500)]}])
    provider = JQuantsProvider(
        "secret", http_client=client, minimum_request_interval=0
    )

    bars = provider.fetch_market_daily_prices(
        date(2026, 8, 14), date(2026, 8, 14)
    )

    assert bars[0].symbol == "7203"
    assert bars[0].is_adjusted is True
    assert bars[0].raw_close == Decimal("2500")
    assert client.calls[0][1] == {
        "date": "2026-08-14",
    }


def test_market_daily_prices_skips_weekends() -> None:
    client = FakeHttpClient([
        {"data": [daily_record("2026-08-14", 2500)]},
        {"data": [daily_record("2026-08-17", 2510)]},
    ])
    provider = JQuantsProvider(
        "secret", http_client=client, minimum_request_interval=0
    )

    bars = provider.fetch_market_daily_prices(
        date(2026, 8, 14), date(2026, 8, 17)
    )

    assert len(bars) == 2
    assert [call[1] for call in client.calls] == [
        {"date": "2026-08-14"},
        {"date": "2026-08-17"},
    ]


def test_accepts_alphanumeric_security_code() -> None:
    client = FakeHttpClient([{"data": [daily_record("2026-08-14", 2500)]}])
    provider = JQuantsProvider(
        "secret", http_client=client, minimum_request_interval=0
    )

    bars = provider.fetch_daily_prices("130a")

    assert bars[0].symbol == "130A"
    assert client.calls[0][1]["code"] == "130A"


def test_follows_pagination() -> None:
    client = FakeHttpClient(
        [
            {"data": [daily_record("2024-01-04", 2480)], "pagination_key": "next"},
            {"data": [daily_record("2024-01-05", 2500)]},
        ]
    )
    provider = JQuantsProvider("secret", http_client=client, minimum_request_interval=0)

    bars = provider.fetch_daily_prices("7203")

    assert len(bars) == 2
    assert client.calls[1][1]["pagination_key"] == "next"


def test_searches_company_master() -> None:
    client = FakeHttpClient(
        [{"data": [{"Code": "72030", "CoName": "トヨタ自動車", "MktNm": "プライム"}]}]
    )
    provider = JQuantsProvider("secret", http_client=client, minimum_request_interval=0)

    matches = provider.search_symbols("トヨタ")

    assert matches[0].symbol == "7203"
    assert matches[0].name == "トヨタ自動車"
    assert matches[0].currency == "JPY"


def test_fetches_light_plan_topix_prices() -> None:
    client = FakeHttpClient([
        {"data": [{"Date": "2026-08-14", "O": 3000, "H": 3020, "L": 2990, "C": 3010}]}
    ])
    provider = JQuantsProvider("secret", http_client=client, minimum_request_interval=0)

    bars = provider.fetch_topix_prices(
        start=date(2026, 8, 1), end=date(2026, 8, 14)
    )

    assert bars[0].symbol == "TOPIX"
    assert bars[0].close == Decimal("3010")
    assert client.calls[0][0].endswith("/v2/indices/bars/daily/topix")


def test_fetches_earnings_calendar() -> None:
    client = FakeHttpClient([{"data": [{
        "Date": "2026-08-20", "Code": "72030", "CoName": "トヨタ自動車",
        "FY": "2027-03", "FQ": "1Q",
    }]}])
    provider = JQuantsProvider("secret", http_client=client, minimum_request_interval=0)

    announcements = provider.fetch_earnings_calendar()

    assert announcements[0].symbol == "7203"
    assert announcements[0].scheduled_date == date(2026, 8, 20)
    assert client.calls[0][0].endswith("/v2/equities/earnings-calendar")


def test_fetches_instrument_master_for_bulk_registration() -> None:
    client = FakeHttpClient([{"data": [{
        "Code": "72030",
        "CoName": "トヨタ自動車",
        "CoNameEn": "TOYOTA MOTOR CORPORATION",
        "MktNm": "プライム",
        "S17": "6",
        "S17Nm": "自動車・輸送機",
        "S33": "3700",
        "S33Nm": "輸送用機器",
    }]}])
    provider = JQuantsProvider("secret", http_client=client, minimum_request_interval=0)

    instruments = provider.fetch_instrument_master(date(2026, 8, 14))

    assert instruments[0].symbol == "7203"
    assert instruments[0].sector_33_name == "輸送用機器"
    assert instruments[0].as_of_date == date(2026, 8, 14)
    assert client.calls[0][0].endswith("/v2/equities/master")


def test_lists_and_downloads_bulk_daily_bars() -> None:
    json_client = FakeHttpClient([
        {
            "data": [{
                "Key": "equities/bars/daily/2026-08-14.csv.gz",
                "Size": 100,
                "LastModified": "2026-08-14T08:00:00Z",
            }]
        },
        {"url": "https://download.example.invalid/2026-08-14.csv.gz"},
    ])
    csv_content = (
        b"Date,Code,AdjO,AdjH,AdjL,AdjC,AdjVo\n"
        b"2026-08-14,72030,2800,2850,2780,2830,1234000\n"
    )
    binary_client = FakeBinaryHttpClient(csv_content)
    provider = JQuantsProvider(
        "secret",
        http_client=json_client,
        binary_http_client=binary_client,
        minimum_request_interval=0,
    )

    files = provider.list_bulk_files(
        "/equities/bars/daily",
        date(2026, 8, 14),
        date(2026, 8, 14),
    )
    bars = provider.download_bulk_daily_bars(files[0].key)

    assert files[0].target_date == date(2026, 8, 14)
    assert bars[0].symbol == "7203"
    assert bars[0].close == Decimal("2830")
    assert bars[0].volume == 1_234_000
    assert json_client.calls[0][0].endswith("/v2/bulk/list")
    assert json_client.calls[0][1]["endpoint"] == "/equities/bars/daily"
    assert binary_client.calls[0][0].startswith("https://download.example.invalid/")


def test_bulk_daily_bars_accepts_full_historical_column_names() -> None:
    json_client = FakeHttpClient([
        {"url": "https://download.example.invalid/historical.csv.gz"},
    ])
    csv_content = gzip.compress(
        (
            "\ufeffCode,Date,Open,High,Low,Close,Volume,"
            "AdjustmentOpen,AdjustmentHigh,AdjustmentLow,AdjustmentClose,"
            "AdjustmentVolume\n"
            "72030,20260814,2800,2850,2780,2830,1234000,"
            "2800,2850,2780,2830,1234000\n"
        ).encode()
    )
    provider = JQuantsProvider(
        "secret",
        http_client=json_client,
        binary_http_client=FakeBinaryHttpClient(csv_content),
        minimum_request_interval=0,
    )

    bars = provider.download_bulk_daily_bars("historical.csv.gz")

    assert bars[0].symbol == "7203"
    assert bars[0].trade_date == date(2026, 8, 14)
    assert bars[0].close == Decimal("2830")
    assert bars[0].volume == 1_234_000
    assert bars[0].is_adjusted is True
    assert bars[0].raw_close == Decimal("2830")


def test_bulk_daily_bars_preserves_raw_prices_for_later_adjustment() -> None:
    provider = JQuantsProvider(
        "secret",
        http_client=FakeHttpClient([
            {"url": "https://download.example.invalid/raw.csv.gz"},
        ]),
        binary_http_client=FakeBinaryHttpClient(
            b"Date,Code,O,H,L,C,Vo,AdjFactor\n"
            b"2026-03-30,58030,4400,4500,4350,4445,5000000,0.1666666667\n"
        ),
        minimum_request_interval=0,
    )

    bars = provider.download_bulk_daily_bars("raw.csv.gz")

    assert bars[0].symbol == "5803"
    assert bars[0].is_adjusted is False
    assert bars[0].close == Decimal("4445")
    assert bars[0].raw_close == Decimal("4445")
    assert bars[0].adjustment_factor == Decimal("0.1666666667")


def test_bulk_daily_bars_rejects_unknown_schema_instead_of_returning_zero() -> None:
    provider = JQuantsProvider(
        "secret",
        http_client=FakeHttpClient([
            {"url": "https://download.example.invalid/unknown.csv.gz"},
        ]),
        binary_http_client=FakeBinaryHttpClient(b"unknown,value\n1,2\n"),
        minimum_request_interval=0,
    )

    with pytest.raises(MarketDataResponseError, match="必須列"):
        provider.download_bulk_daily_bars("unknown.csv.gz")


def test_bulk_daily_bars_rejects_csv_without_valid_prices() -> None:
    provider = JQuantsProvider(
        "secret",
        http_client=FakeHttpClient([
            {"url": "https://download.example.invalid/empty.csv.gz"},
        ]),
        binary_http_client=FakeBinaryHttpClient(
            b"Date,Code,AdjO,AdjH,AdjL,AdjC,AdjVo\n2026-08-14,72030,,,,,\n"
        ),
        minimum_request_interval=0,
    )

    with pytest.raises(MarketDataResponseError, match="有効な日足"):
        provider.download_bulk_daily_bars("empty.csv.gz")


def test_monthly_historical_bulk_key_uses_month_end_as_target_date() -> None:
    client = FakeHttpClient([{"data": [{
        "Key": (
            "equities/bars/daily/historical/2021/"
            "equities_bars_daily_202108.csv.gz"
        ),
        "Size": 100,
        "LastModified": "2026-08-14T08:00:00Z",
    }]}])
    provider = JQuantsProvider("secret", http_client=client, minimum_request_interval=0)

    files = provider.list_bulk_files(
        "/equities/bars/daily",
        date(2021, 8, 1),
        date(2021, 8, 31),
    )

    assert files[0].target_date == date(2021, 8, 31)


def test_missing_key_fails_before_request() -> None:
    provider = JQuantsProvider(None, http_client=FakeHttpClient([]))

    with pytest.raises(MarketDataAuthenticationError, match="JQUANTS_API_KEY"):
        provider.fetch_daily_prices("7203")


def test_rate_limit_is_classified_after_retry() -> None:
    client = FakeHttpClient(
        [HttpClientError("上限", status_code=429), HttpClientError("上限", status_code=429)]
    )
    waits = []
    provider = JQuantsProvider(
        "secret",
        http_client=client,
        max_retries=1,
        minimum_request_interval=0,
        sleep=waits.append,
    )

    with pytest.raises(MarketDataRateLimitError):
        provider.fetch_daily_prices("7203")
    assert 60 in waits
