import json
from datetime import date, timedelta
from decimal import Decimal

import pytest

from stock_signal.batch import (
    BatchAlreadyRunningError,
    BatchDatabaseLock,
    DailyBatchRunner,
)
from stock_signal.config import Settings
from stock_signal.database import (
    add_watchlist_item,
    get_pipeline_run,
    list_market_candidates,
    list_watchlist_items,
    load_daily_bars,
    record_bulk_adjustment,
    record_bulk_file,
    record_data_sync,
    request_watchlist_registration,
    upsert_daily_bars,
)
from stock_signal.domain.market_data import (
    BulkFile,
    DailyBar,
    EarningsAnnouncement,
    ListedInstrument,
    SymbolMatch,
)
from stock_signal.market_sync import sync_bulk_daily_bars
from stock_signal.providers.base import MarketDataTransportError


def make_bar(symbol: str, trade_date: date, provider: str = "test") -> DailyBar:
    price = Decimal("100") + Decimal(str(trade_date.toordinal() % 20))
    return DailyBar(
        symbol=symbol,
        trade_date=trade_date,
        open=price,
        high=price + 1,
        low=price - 1,
        close=price,
        volume=1000,
        provider=provider,
        is_adjusted=True,
    )


class RecordingProvider:
    def __init__(self, provider_name: str, *, fail: bool = False) -> None:
        self.provider_name = provider_name
        self.fail = fail
        self.calls = []

    def fetch_daily_prices(self, symbol, start=None, end=None):
        self.calls.append((symbol, start, end))
        if self.fail:
            raise MarketDataTransportError("試験用の通信失敗")
        return [make_bar(symbol, start, self.provider_name)] if start <= end else []

    def search_symbols(self, keywords):
        return []


class LightRecordingProvider(RecordingProvider):
    def __init__(self) -> None:
        super().__init__("jquants")
        self.reference_calls = []

    def fetch_topix_prices(self, start=None, end=None):
        self.reference_calls.append(("topix", start, end))
        return [make_bar("TOPIX", end, "jquants")]

    def fetch_earnings_calendar(self):
        self.reference_calls.append(("earnings", None, None))
        return [
            EarningsAnnouncement(
                symbol="7203",
                scheduled_date=date(2026, 2, 12),
                company_name="トヨタ自動車",
            )
        ]

    def search_symbols(self, keywords):
        self.reference_calls.append(("master", keywords, None))
        return [
            SymbolMatch(
                symbol="7203",
                name="トヨタ自動車",
                market="プライム",
                currency="JPY",
                match_score=Decimal("1"),
            )
        ]


class UniverseRecordingProvider(LightRecordingProvider):
    def __init__(self) -> None:
        super().__init__()
        self.bulk_list_calls = []

    def fetch_instrument_master(self, as_of):
        return [
            ListedInstrument(
                symbol="7203",
                name="トヨタ自動車",
                english_name="TOYOTA MOTOR CORPORATION",
                market="プライム",
                sector_17_code="6",
                sector_17_name="自動車・輸送機",
                sector_33_code="3700",
                sector_33_name="輸送用機器",
                instrument_type="stock",
                as_of_date=as_of,
            )
        ]

    def list_bulk_files(self, endpoint, start, end):
        self.bulk_list_calls.append((endpoint, start, end))
        return [BulkFile("daily-20260210", endpoint, date(2026, 2, 10))]

    def download_bulk_daily_bars(self, file_key):
        return [
            make_bar(
                "7203",
                date(2026, 1, 1) + timedelta(days=index),
                "jquants",
            )
            for index in range(30)
        ]

    def fetch_market_daily_prices(self, start, end):
        return [make_bar("7203", end, "jquants")]


class EmptyUniverseRecordingProvider(UniverseRecordingProvider):
    def download_bulk_daily_bars(self, file_key):
        return []


class AdjustedUniverseRecordingProvider(UniverseRecordingProvider):
    def download_bulk_daily_bars(self, file_key):
        return [make_bar("7203", date(2026, 2, 10), "jquants")]


class SplitUniverseRecordingProvider(UniverseRecordingProvider):
    def download_bulk_daily_bars(self, file_key):
        price = Decimal("500")
        return [
            DailyBar(
                symbol="7203",
                trade_date=date(2026, 2, 10),
                open=price,
                high=price + 10,
                low=price - 10,
                close=price,
                volume=1_000,
                provider="jquants",
                is_adjusted=False,
                adjustment_factor=Decimal("0.5"),
            )
        ]

    def fetch_market_daily_prices(self, start, end):
        bar = make_bar("7203", end, "jquants")
        return [
            DailyBar(
                symbol=bar.symbol,
                trade_date=bar.trade_date,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                provider=bar.provider,
                is_adjusted=True,
                raw_open=Decimal("500"),
                raw_high=Decimal("510"),
                raw_low=Decimal("490"),
                raw_close=Decimal("500"),
                raw_volume=1_000,
                adjustment_factor=Decimal("0.5"),
            )
        ]

    def fetch_daily_prices(self, symbol, start=None, end=None):
        self.calls.append((symbol, start, end))
        return [
            make_bar(symbol, date(2026, 2, 9), "jquants"),
            make_bar(symbol, date(2026, 2, 10), "jquants"),
        ]


def test_daily_batch_fetches_only_after_latest_date_and_records_run(database_url) -> None:
    settings = Settings(database_url=database_url)
    seed = [make_bar("1111", date(2026, 1, 1) + timedelta(days=index)) for index in range(25)]
    upsert_daily_bars(database_url, seed)
    add_watchlist_item(
        database_url,
        symbol="1111",
        provider="test",
        display_name="試験銘柄",
        exchange="試験市場",
        currency="JPY",
    )
    provider = RecordingProvider("test")
    runner = DailyBatchRunner(
        settings,
        provider_factory=lambda _: provider,
        today=lambda: date(2026, 2, 10),
    )

    result = runner.run()

    assert result.status == "success"
    assert provider.calls[0][1] == date(2026, 1, 26)
    assert len(load_daily_bars(database_url, "1111", provider="test")) == 26
    status, finished_at, summary_json = get_pipeline_run(database_url, result.run_id)
    assert status == "success"
    assert finished_at is not None
    assert json.loads(summary_json) == {"succeeded": 2, "failed": 0}
    assert result.items[0].analysis_summary.keys() == {"5", "20"}
    assert result.items[0].analysis_summary["5"]["action"] in {
        "buy_candidate", "watch", "avoid_new_buy", "insufficient_data"
    }
    assert result.items[0].analysis_summary["5"]["engine_version"] == "2.9.0"


def test_failure_of_one_symbol_does_not_discard_other_symbol(database_url) -> None:
    settings = Settings(database_url=database_url)
    for symbol, provider in (("1111", "good"), ("2222", "bad")):
        add_watchlist_item(
            database_url,
            symbol=symbol,
            provider=provider,
            display_name=symbol,
            exchange="試験市場",
            currency="JPY",
        )
    providers = {
        "good": RecordingProvider("good"),
        "bad": RecordingProvider("bad", fail=True),
    }

    result = DailyBatchRunner(
        settings,
        provider_factory=providers.__getitem__,
        today=lambda: date(2026, 2, 10),
    ).run()

    assert result.status == "partial_failure"
    assert result.succeeded == 2
    assert result.failed == 1
    assert load_daily_bars(database_url, "1111", provider="good")
    assert next(item for item in result.items if item.symbol == "2222").error_message


def test_batch_lock_rejects_concurrent_execution(database_url) -> None:
    with (
        BatchDatabaseLock(database_url),
        pytest.raises(BatchAlreadyRunningError),
        BatchDatabaseLock(database_url),
    ):
        pass


def test_light_batch_syncs_references_and_activates_security_code(database_url) -> None:
    settings = Settings(
        database_url=database_url,
        jquants_api_key="test-key",
        jquants_plan="light",
    )
    request_watchlist_registration(database_url, "7203")
    provider = LightRecordingProvider()

    result = DailyBatchRunner(
        settings,
        provider_factory=lambda _: provider,
        today=lambda: date(2026, 2, 10),
    ).run()

    assert result.status == "success"
    assert {item.symbol for item in result.items} == {
        "@TOPIX", "@EARNINGS", "@SCREEN", "7203"
    }
    assert [item.symbol for item in list_watchlist_items(database_url)] == ["7203"]
    assert load_daily_bars(database_url, "TOPIX", provider="jquants")
    assert provider.calls[0] == ("7203", date(2021, 2, 11), date(2026, 2, 10))
    assert [call[0] for call in provider.reference_calls] == [
        "topix", "earnings", "master"
    ]


def test_light_upgrade_backfills_five_years_only_once(database_url) -> None:
    settings = Settings(database_url=database_url, jquants_plan="light")
    upsert_daily_bars(
        database_url, [make_bar("7203", date(2025, 12, 30), provider="jquants")]
    )
    runner = DailyBatchRunner(
        settings,
        provider_factory=lambda _: RecordingProvider("jquants"),
        today=lambda: date(2026, 2, 10),
    )

    first_start, first_end = runner._date_range("7203", "jquants")
    record_data_sync(database_url, runner._history_dataset("7203"), "success")
    next_start, next_end = runner._date_range("7203", "jquants")

    assert (first_start, first_end) == (date(2021, 2, 11), date(2026, 2, 10))
    assert (next_start, next_end) == (date(2025, 12, 31), date(2026, 2, 10))


def test_light_batch_syncs_market_and_stores_screening(database_url) -> None:
    settings = Settings(
        database_url=database_url,
        jquants_api_key="test-key",
        jquants_plan="light",
        market_screening_limit=10,
    )
    provider = UniverseRecordingProvider()

    result = DailyBatchRunner(
        settings,
        provider_factory=lambda _: provider,
        today=lambda: date(2026, 2, 10),
    ).run()

    by_symbol = {item.symbol: item for item in result.items}
    assert result.status == "success"
    assert by_symbol["@MASTER"].upserted == 1
    assert by_symbol["@MARKET"].upserted == 30
    assert by_symbol["@SCREEN"].upserted == 1
    assert len(load_daily_bars(database_url, "7203", provider="jquants")) == 30
    candidates = list_market_candidates(database_url, limit=10)
    assert candidates[0]["sector_33_code"] == "3700"
    assert candidates[0]["sector_33_name"] == "輸送用機器"
    assert candidates[0]["transition_phase"] != "unknown"
    assert candidates[0]["transition_total"] >= 4


def test_daily_market_sync_overwrites_recent_two_weeks(database_url) -> None:
    endpoint = "/equities/bars/daily"
    record_bulk_file(
        database_url,
        file_key="daily-20260208",
        endpoint=endpoint,
        target_date=date(2026, 2, 8),
        status="success",
        row_count=1,
    )
    record_bulk_adjustment(
        database_url,
        "daily-20260208",
        "success",
        row_count=1,
    )
    provider = UniverseRecordingProvider()
    settings = Settings(
        database_url=database_url,
        jquants_api_key="test-key",
        jquants_plan="light",
    )

    DailyBatchRunner(
        settings,
        provider_factory=lambda _: provider,
        today=lambda: date(2026, 2, 10),
    ).run()

    assert provider.bulk_list_calls[0][1:] == (
        date(2026, 1, 27),
        date(2026, 2, 10),
    )


def test_daily_batch_reports_incomplete_historical_bulk_files(database_url) -> None:
    endpoint = "/equities/bars/daily"
    record_bulk_file(
        database_url,
        file_key="historical-202107.csv.gz",
        endpoint=endpoint,
        target_date=date(2021, 7, 31),
        status="success",
        row_count=100,
    )
    settings = Settings(
        database_url=database_url,
        jquants_api_key="test-key",
        jquants_plan="light",
    )

    result = DailyBatchRunner(
        settings,
        provider_factory=lambda _: UniverseRecordingProvider(),
        today=lambda: date(2026, 2, 10),
    ).run()

    market = next(item for item in result.items if item.symbol == "@MARKET")
    assert result.status == "partial_failure"
    assert market.status == "failed"
    assert "未完了のバルクファイルが1件" in market.error_message


def test_empty_bulk_file_is_reported_as_failure(database_url) -> None:
    summary = sync_bulk_daily_bars(
        database_url,
        EmptyUniverseRecordingProvider(),
        date(2026, 2, 10),
        date(2026, 2, 10),
    )

    assert summary == {
        "files": 1,
        "skipped": 0,
        "raw_rows": 0,
        "rows": 0,
        "refreshed_symbols": 0,
        "failed": 1,
        "errors": [
            {
                "file_key": "daily-20260210",
                "message": (
                    "バルクファイルに有効な日足がありません: "
                    "daily-20260210"
                ),
            }
        ],
    }


def test_incremental_split_refreshes_symbol_history(database_url) -> None:
    provider = SplitUniverseRecordingProvider()

    summary = sync_bulk_daily_bars(
        database_url,
        provider,
        date(2026, 2, 10),
        date(2026, 2, 10),
        history_start=date(2021, 2, 11),
    )

    assert summary["raw_rows"] == 1
    assert summary["rows"] == 1
    assert summary["refreshed_symbols"] == 1
    assert provider.calls == [("7203", date(2021, 2, 11), date(2026, 2, 10))]
    assert len(load_daily_bars(database_url, "7203", provider="jquants")) == 2


def test_completed_bulk_file_can_be_forcibly_refreshed(database_url) -> None:
    provider = AdjustedUniverseRecordingProvider()

    first = sync_bulk_daily_bars(
        database_url,
        provider,
        date(2026, 2, 10),
        date(2026, 2, 10),
    )
    skipped = sync_bulk_daily_bars(
        database_url,
        provider,
        date(2026, 2, 10),
        date(2026, 2, 10),
    )
    refreshed = sync_bulk_daily_bars(
        database_url,
        provider,
        date(2026, 2, 10),
        date(2026, 2, 10),
        refresh_completed=True,
    )

    assert first["rows"] == 1
    assert skipped["skipped"] == 1
    assert refreshed["skipped"] == 0
    assert refreshed["rows"] == 1


def test_failed_overlap_refresh_keeps_the_last_good_checkpoint(database_url) -> None:
    provider = AdjustedUniverseRecordingProvider()
    sync_bulk_daily_bars(
        database_url,
        provider,
        date(2026, 2, 10),
        date(2026, 2, 10),
    )

    failed = sync_bulk_daily_bars(
        database_url,
        EmptyUniverseRecordingProvider(),
        date(2026, 2, 10),
        date(2026, 2, 10),
        refresh_completed=True,
    )
    retried = sync_bulk_daily_bars(
        database_url,
        provider,
        date(2026, 2, 10),
        date(2026, 2, 10),
    )

    assert failed["failed"] == 1
    assert retried["skipped"] == 1
