from datetime import date
from decimal import Decimal

from stock_signal.charts.candlestick import render_candlestick_report
from stock_signal.domain.market_data import DailyBar


def test_render_candlestick_report(tmp_path) -> None:
    bars = [
        DailyBar(
            symbol="TM",
            trade_date=date(2026, 8, 14),
            open=Decimal("200"),
            high=Decimal("205"),
            low=Decimal("198"),
            close=Decimal("203"),
            volume=1_000_000,
            provider="alpha_vantage",
            is_adjusted=False,
        )
    ]

    output_path = render_candlestick_report(bars, tmp_path)

    assert output_path.name == "tm.html"
    content = output_path.read_text()
    assert "Daily OHLCV" in content
    assert "alpha_vantage" in content
    assert "unadjusted" in content
    assert "plotly" in content.lower()
