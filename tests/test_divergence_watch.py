"""監視ルールの時間順序・失効優先・期限を検証する。"""

from dataclasses import replace
from decimal import Decimal

from test_analysis import make_bars

from stock_signal.analysis.divergence import RsiDivergence
from stock_signal.analysis.divergence_watch import (
    DivergenceWatch,
    create_divergence_watch,
    evaluate_divergence_watch,
)


def fixture():
    bars = make_bars([100] * 45)
    watch = DivergenceWatch(bars[30].trade_date.isoformat(), 95, 102, 94, 4)
    bars[31] = replace(
        bars[31], open=Decimal(102), close=Decimal(104), high=Decimal(105), low=Decimal(100)
    )
    return bars, watch


def test_confirmation_requires_later_bar_and_does_not_use_future():
    bars, watch = fixture()
    assert evaluate_divergence_watch(watch, bars[:31]) == ("watching", None)
    expected = ("confirmed", bars[31].trade_date.isoformat())
    assert evaluate_divergence_watch(watch, bars[:32]) == expected
    assert evaluate_divergence_watch(watch, bars) == expected


def test_invalidation_precedes_confirmation_and_never_revives():
    bars, watch = fixture()
    bars[31] = replace(bars[31], low=Decimal(94))
    assert evaluate_divergence_watch(watch, bars) == (
        "invalidated",
        bars[31].trade_date.isoformat(),
    )


def test_expiry_and_zero_volume():
    bars, watch = fixture()
    bars[31] = replace(bars[31], volume=0)
    assert evaluate_divergence_watch(watch, bars[:41]) == ("watching", None)
    assert evaluate_divergence_watch(watch, bars) == ("expired", bars[41].trade_date.isoformat())


def test_levels_are_frozen_at_confirmation():
    bars, _ = fixture()
    signal = RsiDivergence(
        "bullish",
        bars[20].trade_date.isoformat(),
        bars[28].trade_date.isoformat(),
        bars[30].trade_date.isoformat(),
        96,
        95,
        25,
        32,
    )
    expected = create_divergence_watch(bars[:31], signal)
    bars[40] = replace(bars[40], high=Decimal(999))
    assert create_divergence_watch(bars, signal) == expected


def test_trade_gap_stop_and_costs():
    from stock_signal.analysis.divergence_watch import evaluate_watch_trade

    bars, watch = fixture()
    bars[33] = replace(bars[33], open=Decimal(90), low=Decimal(88))
    result = evaluate_watch_trade(bars, 30, 5, watch)
    assert result["status"] == "ready"
    assert result["exit_price"] == 90
    assert result["stopped"]
    assert abs(result["net_percent"] - (90 * 0.999 / (102 * 1.001) - 1) * 100) < 1e-9
    assert evaluate_watch_trade(bars, 44, 5, watch)["status"] == "unmatured"
    bars[31] = replace(bars[31], open=Decimal(90))
    assert evaluate_watch_trade(bars, 30, 5, watch)["status"] == "entry_skipped"
