"""RSI逆行の確定時刻と極値更新後の扱いを検証する。"""
from dataclasses import replace
from decimal import Decimal

import pytest
from test_analysis import make_bars

from stock_signal.analysis.divergence import detect_rsi_divergence


@pytest.mark.parametrize("bearish", [False, True])
def test_divergence_requires_two_right_bars_and_expires_on_new_extreme(monkeypatch, bearish):
    prices = [100] * 40
    prices[25], prices[33] = (110, 112) if bearish else (90, 88)
    bars = make_bars(prices)
    rsi = [50.0] * 40
    rsi[25], rsi[33] = (75, 65) if bearish else (25, 35)
    monkeypatch.setattr(
        "stock_signal.analysis.divergence.wilder_rsi_series", lambda b, w: tuple(rsi[:len(b)])
    )
    assert not detect_rsi_divergence(bars[:35])
    result = detect_rsi_divergence(bars[:36])
    assert len(result) == 1
    assert result[0].direction == ("bearish" if bearish else "bullish")
    assert result[0].confirmed_date == bars[35].trade_date.isoformat()
    assert detect_rsi_divergence(bars)[0] == result[0]
    bars[-1] = replace(bars[-1], high=Decimal(120)) if bearish else replace(
        bars[-1], low=Decimal(80)
    )
    assert not detect_rsi_divergence(bars)


def test_constant_price_or_insufficient_history_has_no_divergence():
    assert not detect_rsi_divergence(make_bars([100] * 70))
    assert not detect_rsi_divergence(make_bars([100] * 10))
