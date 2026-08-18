from datetime import date
from decimal import Decimal

from stock_signal.domain.market_data import DailyBar
from stock_signal.quality import find_corporate_action_gaps


def _bar(
    trade_date: date,
    open_price: str,
    close_price: str,
    *,
    factor: str = "1",
    raw_open: str | None = None,
    raw_close: str | None = None,
) -> DailyBar:
    opening = Decimal(open_price)
    closing = Decimal(close_price)
    return DailyBar(
        symbol="5803",
        trade_date=trade_date,
        open=opening,
        high=max(opening, closing),
        low=min(opening, closing),
        close=closing,
        volume=1_000_000,
        provider="jquants",
        is_adjusted=True,
        raw_open=Decimal(raw_open) if raw_open is not None else None,
        raw_close=Decimal(raw_close) if raw_close is not None else None,
        adjustment_factor=Decimal(factor),
    )


def test_detects_split_ratio_left_in_adjusted_series() -> None:
    bars = [
        _bar(date(2026, 3, 27), "27500", "27630"),
        _bar(date(2026, 3, 30), "4445", "4480", factor="0.1666666667"),
    ]

    issues = find_corporate_action_gaps(bars)

    assert len(issues) == 1
    assert issues[0].symbol == "5803"


def test_accepts_continuous_adjusted_series_across_split() -> None:
    bars = [
        _bar(date(2026, 3, 27), "4583.33", "4605"),
        _bar(date(2026, 3, 30), "4445", "4480", factor="0.1666666667"),
    ]

    assert find_corporate_action_gaps(bars) == []


def test_accepts_small_split_when_adjusted_and_raw_ratios_differ() -> None:
    bars = [
        _bar(
            date(2022, 3, 29),
            "1000",
            "1000",
            raw_open="1100",
            raw_close="1100",
        ),
        _bar(
            date(2022, 3, 30),
            "995",
            "1005",
            factor="0.9090909091",
            raw_open="995",
            raw_close="1005",
        ),
    ]

    assert find_corporate_action_gaps(bars) == []


def test_detects_small_split_when_adjusted_values_still_equal_raw_values() -> None:
    bars = [
        _bar(
            date(2022, 3, 29),
            "1100",
            "1100",
            raw_open="1100",
            raw_close="1100",
        ),
        _bar(
            date(2022, 3, 30),
            "995",
            "1005",
            factor="0.9090909091",
            raw_open="995",
            raw_close="1005",
        ),
    ]

    assert len(find_corporate_action_gaps(bars)) == 1
