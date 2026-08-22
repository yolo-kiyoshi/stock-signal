from __future__ import annotations

from collections.abc import Sequence
from statistics import fmean

from stock_signal.domain.market_data import DailyBar


def true_range(current: DailyBar, previous: DailyBar) -> float:
    """当日値幅と前日終値からTRを計算する。"""
    high = float(current.high)
    low = float(current.low)
    previous_close = float(previous.close)
    return max(high - low, abs(high - previous_close), abs(low - previous_close))


def wilder_atr(
    bars: Sequence[DailyBar],
    window: int = 20,
    *,
    end: int | None = None,
) -> float | None:
    """Wilder平滑化でATRを計算する。endを指定した場合はその位置を含めない。"""
    stop = len(bars) if end is None else min(end, len(bars))
    if window <= 0:
        raise ValueError("ATR期間は1以上で指定してください")
    if stop < window + 1:
        return None
    ranges = [
        true_range(bars[index], bars[index - 1])
        for index in range(1, stop)
    ]
    atr = fmean(ranges[:window])
    for value in ranges[window:]:
        atr = (atr * (window - 1) + value) / window
    return atr

