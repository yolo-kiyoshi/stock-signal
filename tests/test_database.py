from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from stock_signal.database import (
    add_watchlist_item,
    bulk_adjustment_succeeded,
    bulk_file_succeeded,
    bulk_sync_status,
    daily_bar_quality,
    data_sync_succeeded,
    get_watchlist_registration,
    initialize_database,
    latest_bulk_file_date,
    list_bulk_sync_issues,
    list_positions,
    list_stored_symbols,
    list_watchlist_items,
    list_watchlist_registrations,
    list_watchlists,
    load_daily_bars,
    next_earnings_date,
    record_bulk_adjustment,
    record_bulk_file,
    record_data_sync,
    remove_position,
    remove_watchlist_item,
    replace_earnings_calendar,
    replace_instruments,
    request_watchlist_registration,
    sqlite_path,
    update_watchlist_registration,
    upsert_daily_bars,
    upsert_position,
    validate_daily_bar,
)
from stock_signal.domain.market_data import DailyBar, EarningsAnnouncement
from stock_signal.persistence.engine import get_engine
from stock_signal.persistence.schema import app_metadata


def test_initialize_database(database_url) -> None:
    initialized_url = initialize_database(database_url)

    assert initialized_url == database_url
    with get_engine(database_url).connect() as connection:
        version = connection.scalar(
            select(app_metadata.c.value).where(app_metadata.c.key == "schema_version")
        )
    assert version == "7"


def test_sqlite_path() -> None:
    assert sqlite_path("sqlite:///data/app.db").as_posix() == "data/app.db"


def _bar(**overrides) -> DailyBar:
    values = {
        "symbol": "TM",
        "trade_date": date(2026, 8, 14),
        "open": Decimal("200"),
        "high": Decimal("205"),
        "low": Decimal("198"),
        "close": Decimal("203"),
        "volume": 1_000_000,
        "provider": "alpha_vantage",
        "is_adjusted": False,
    }
    values.update(overrides)
    if not values["is_adjusted"]:
        values.setdefault("raw_open", values["open"])
        values.setdefault("raw_high", values["high"])
        values.setdefault("raw_low", values["low"])
        values.setdefault("raw_close", values["close"])
        values.setdefault("raw_volume", values["volume"])
    return DailyBar(**values)


def test_upsert_and_load_daily_bars(database_url) -> None:
    assert upsert_daily_bars(database_url, [_bar()]) == 1
    assert upsert_daily_bars(database_url, [_bar(close=Decimal("204"))]) == 1

    bars = load_daily_bars(database_url, "tm", provider="alpha_vantage")
    assert len(bars) == 1
    assert bars[0].close == Decimal("204")
    assert list_stored_symbols(database_url) == [
        ("TM", "alpha_vantage", 1, "2026-08-14", "2026-08-14")
    ]


def test_daily_bar_upsert_splits_large_market_payload(database_url) -> None:
    bars = [
        _bar(
            symbol=f"{index:04d}",
            provider="jquants",
            is_adjusted=True,
        )
        for index in range(2200)
    ]

    assert upsert_daily_bars(database_url, bars) == 2200
    assert len(list_stored_symbols(database_url)) == 2200


def test_validate_daily_bar_rejects_inconsistent_ohlc() -> None:
    with pytest.raises(ValueError, match="inconsistent"):
        validate_daily_bar(_bar(high=Decimal("201"), close=Decimal("203")))


def test_watchlist_registration_moves_from_pending_to_active(database_url) -> None:
    pending = request_watchlist_registration(database_url, "130a")
    add_watchlist_item(
        database_url,
        symbol="130A",
        provider="jquants",
        display_name="試験株式会社",
        exchange="プライム",
        currency="JPY",
    )
    update_watchlist_registration(
        database_url, "130A", "jquants", "active", display_name="試験株式会社"
    )

    active = get_watchlist_registration(database_url, "130A")
    assert pending.status == "pending"
    assert active.status == "active"
    assert active.display_name == "試験株式会社"
    assert list_watchlist_registrations(database_url, statuses=("active",)) == [active]


def test_earnings_calendar_and_sync_status_are_persisted(database_url) -> None:
    announcements = [
        EarningsAnnouncement(
            symbol="7203",
            scheduled_date=date(2026, 8, 20),
            company_name="トヨタ自動車",
            fiscal_year="2027-03",
            fiscal_quarter="1Q",
        )
    ]

    assert replace_earnings_calendar(database_url, announcements) == 1
    record_data_sync(database_url, "jquants_earnings_calendar", "success")

    assert next_earnings_date(database_url, "7203", date(2026, 8, 15)) == date(2026, 8, 20)
    assert data_sync_succeeded(database_url, "jquants_earnings_calendar") is True


def test_zero_row_bulk_success_is_retried(database_url) -> None:
    endpoint = "/equities/bars/daily"
    record_bulk_file(
        database_url,
        file_key="zero.csv.gz",
        endpoint=endpoint,
        target_date=date(2026, 7, 31),
        status="success",
        row_count=0,
    )

    assert bulk_file_succeeded(database_url, "zero.csv.gz") is False
    assert latest_bulk_file_date(database_url, endpoint) is None
    assert bulk_sync_status(database_url, endpoint) == {
        "total": 1,
        "completed": 0,
        "incomplete": 1,
        "failed": 0,
    }
    assert list_bulk_sync_issues(database_url, endpoint) == [
        {
            "file_key": "zero.csv.gz",
            "target_date": "2026-07-31",
            "raw_status": "success",
            "raw_rows": 0,
            "adjusted_status": "pending",
            "adjusted_rows": 0,
            "error": "失敗理由が記録されていません",
        }
    ]

    record_bulk_file(
        database_url,
        file_key="stored.csv.gz",
        endpoint=endpoint,
        target_date=date(2026, 8, 14),
        status="success",
        row_count=100,
    )

    assert bulk_file_succeeded(database_url, "stored.csv.gz") is True
    assert latest_bulk_file_date(database_url, endpoint) is None

    record_bulk_adjustment(
        database_url,
        "stored.csv.gz",
        "success",
        row_count=100,
    )

    assert bulk_adjustment_succeeded(database_url, "stored.csv.gz") is True
    assert latest_bulk_file_date(database_url, endpoint) == date(2026, 8, 14)
    assert bulk_sync_status(database_url, endpoint) == {
        "total": 2,
        "completed": 1,
        "incomplete": 1,
        "failed": 0,
    }


def test_jquants_raw_bar_is_replaced_by_adjusted_bar_without_losing_raw_value(
    database_url,
) -> None:
    raw = _bar(
        symbol="5803",
        provider="jquants",
        open=Decimal("4400"),
        high=Decimal("4500"),
        low=Decimal("4350"),
        close=Decimal("4445"),
        is_adjusted=False,
        adjustment_factor=Decimal("0.1666666667"),
    )
    adjusted = _bar(
        symbol="5803",
        provider="jquants",
        open=Decimal("4400"),
        high=Decimal("4500"),
        low=Decimal("4350"),
        close=Decimal("4445"),
        is_adjusted=True,
        adjustment_factor=Decimal("0.1666666667"),
    )

    upsert_daily_bars(database_url, [raw])

    assert load_daily_bars(database_url, "5803", provider="jquants") == []
    assert daily_bar_quality(database_url, "5803", "jquants")["status"] == "unadjusted"

    upsert_daily_bars(database_url, [adjusted])
    bars = load_daily_bars(database_url, "5803", provider="jquants")

    assert daily_bar_quality(database_url, "5803", "jquants")["status"] == "ready"
    assert bars[0].close == Decimal("4445")
    assert bars[0].raw_close == Decimal("4445")

    retried_raw = _bar(
        symbol="5803",
        provider="jquants",
        open=Decimal("4450"),
        high=Decimal("4550"),
        low=Decimal("4400"),
        close=Decimal("4500"),
        is_adjusted=False,
        adjustment_factor=Decimal("0.1666666667"),
    )
    upsert_daily_bars(database_url, [retried_raw])
    retried = load_daily_bars(database_url, "5803", provider="jquants")

    assert retried[0].close == Decimal("4445")
    assert retried[0].raw_close == Decimal("4500")


def test_raw_rows_before_adjusted_plan_boundary_do_not_block_analysis(
    database_url,
) -> None:
    old_raw = _bar(
        symbol="5803",
        provider="jquants",
        trade_date=date(2021, 8, 2),
        is_adjusted=False,
    )
    covered_adjusted = _bar(
        symbol="5803",
        provider="jquants",
        trade_date=date(2021, 8, 17),
        is_adjusted=True,
    )
    upsert_daily_bars(database_url, [old_raw, covered_adjusted])

    quality = daily_bar_quality(database_url, "5803", "jquants")
    bars = load_daily_bars(database_url, "5803", provider="jquants")

    assert quality == {
        "status": "ready",
        "total": 1,
        "adjusted": 1,
        "unadjusted": 0,
    }
    assert [bar.trade_date for bar in bars] == [date(2021, 8, 17)]


def test_raw_rows_inside_adjusted_coverage_still_block_analysis(database_url) -> None:
    adjusted = _bar(
        symbol="5803",
        provider="jquants",
        trade_date=date(2021, 8, 17),
        is_adjusted=True,
    )
    uncovered = _bar(
        symbol="5803",
        provider="jquants",
        trade_date=date(2021, 8, 18),
        is_adjusted=False,
    )
    upsert_daily_bars(database_url, [adjusted, uncovered])

    assert daily_bar_quality(database_url, "5803", "jquants")["status"] == "mixed"
    assert load_daily_bars(database_url, "5803", provider="jquants") == []


def test_remove_watchlist_item_keeps_daily_bars(database_url) -> None:
    add_watchlist_item(
        database_url,
        symbol="TM",
        provider="alpha_vantage",
        display_name="トヨタ自動車 ADR",
        exchange="NYSE",
        currency="USD",
    )
    upsert_daily_bars(database_url, [_bar()])

    assert remove_watchlist_item(database_url, "TM", "alpha_vantage") is True
    assert remove_watchlist_item(database_url, "TM", "alpha_vantage") is False
    assert load_daily_bars(database_url, "TM", provider="alpha_vantage") == [_bar()]
    assert list_watchlist_items(database_url) == []


def test_named_watchlists_keep_registration_state_separate(database_url) -> None:
    first = request_watchlist_registration(
        database_url,
        "7203",
        watchlist_name="高配当",
    )
    second = request_watchlist_registration(
        database_url,
        "7203",
        watchlist_name="成長株",
    )

    assert first.watchlist_name == "高配当"
    assert second.watchlist_name == "成長株"
    assert len(list_watchlist_registrations(database_url)) == 2


def test_instrument_master_and_positions_are_managed_independently(database_url) -> None:
    replace_instruments(
        database_url,
        "jquants",
        [{
            "symbol": "7203",
            "provider": "jquants",
            "display_name": "トヨタ自動車",
            "english_name": "TOYOTA MOTOR CORPORATION",
            "market": "プライム",
            "sector_17_code": "6",
            "sector_17_name": "自動車・輸送機",
            "sector_33_code": "3700",
            "sector_33_name": "輸送用機器",
            "instrument_type": "stock",
            "is_active": True,
            "as_of_date": date(2026, 8, 14),
        }],
    )
    upsert_daily_bars(
        database_url,
        [_bar(
            symbol="7203",
            provider="jquants",
            close=Decimal("2830"),
            high=Decimal("2850"),
            low=Decimal("2780"),
            open=Decimal("2800"),
            is_adjusted=True,
        )],
    )
    upsert_position(
        database_url,
        symbol="7203",
        provider="jquants",
        display_name="トヨタ自動車",
        quantity=Decimal("100"),
        average_cost=Decimal("2500"),
        account_type="特定",
    )

    positions = list_positions(database_url)

    assert positions[0].symbol == "7203"
    assert positions[0].latest_close == Decimal("2830")
    assert list_watchlist_items(database_url) == []
    assert {item.name for item in list_watchlists(database_url)} == {"ウォッチ"}
    assert remove_position(database_url, "7203") is True
    assert list_positions(database_url) == []
