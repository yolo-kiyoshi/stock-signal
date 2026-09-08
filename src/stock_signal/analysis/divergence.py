"""確定した価格の山・谷と同日のRSIから通常のダイバージェンスを検出する。"""

from collections.abc import Sequence
from dataclasses import dataclass

from stock_signal.analysis.indicators import wilder_atr, wilder_rsi_series
from stock_signal.domain.market_data import DailyBar


@dataclass(frozen=True)
class RsiDivergence:
    """右側2本で確認後に利用できる逆行。売買シグナルではない。"""

    direction: str
    first_date: str
    second_date: str
    confirmed_date: str
    first_price: float
    second_price: float
    first_rsi: float
    second_rsi: float


def detect_rsi_divergence(
    bars: Sequence[DailyBar], window: int = 14,
) -> tuple[RsiDivergence, ...]:
    """直近60本の連続した極値を比較する。閾値は検証用の固定仮説。"""
    ordered = sorted(bars, key=lambda b: b.trade_date)
    if len(ordered) < window + 10:
        return ()
    rsi = wilder_rsi_series(ordered, window)
    found = []
    for direction in ("bullish", "bearish"):
        values = [float(b.low if direction == "bullish" else b.high) for b in ordered]
        pivots = []
        for i in range(max(window, len(ordered) - 60, 2), len(ordered) - 2):
            neighbors = values[i - 2:i] + values[i + 1:i + 3]
            extreme = min(neighbors) if direction == "bullish" else max(neighbors)
            if (values[i] < extreme if direction == "bullish" else values[i] > extreme):
                pivots.append(i)
        if len(pivots) < 2:
            continue
        left, right = pivots[-2:]
        if right - left < 5 or len(ordered) - 1 - (right + 2) > 10:
            continue
        first, second = rsi[left], rsi[right]
        atr = wilder_atr(ordered[:right + 1])
        if first is None or second is None or not atr or atr <= 0:
            continue
        sign = 1 if direction == "bullish" else -1
        if sign * (values[left] - values[right]) < 0.1 * atr:
            continue
        if sign * (second - first) < 5:
            continue
        if any(sign * (price - values[right]) < 0 for price in values[right + 1:]):
            continue
        found.append(RsiDivergence(
            direction, ordered[left].trade_date.isoformat(),
            ordered[right].trade_date.isoformat(), ordered[right + 2].trade_date.isoformat(),
            values[left], values[right], first, second,
        ))
    return tuple(found)
