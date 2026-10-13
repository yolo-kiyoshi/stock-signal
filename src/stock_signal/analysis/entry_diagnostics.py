from __future__ import annotations

from collections.abc import Sequence
from statistics import fmean

from stock_signal.analysis.indicators import resistance_bands, wilder_atr
from stock_signal.domain.analysis import EntryDiagnostics
from stock_signal.domain.market_data import DailyBar


def nearest_resistance(
    bars: Sequence[DailyBar], *, lookback: int = 60,
) -> tuple[str, float | None]:
    """抵抗帯の手前と過去高値の近い方を返す。高値更新時に目標を補完しない。"""
    if len(bars) < 21:
        return "unavailable", None
    current = float(bars[-1].close)
    bands = resistance_bands(bars, lookback=lookback)
    if any(band.lower <= current <= band.upper for band in bands):
        return "within_band", current
    levels = [band.lower for band in bands if band.lower > current]
    for window in (20, lookback):
        if len(bars) > window:
            high = max(float(bar.high) for bar in bars[-window - 1:-1])
            if high > current:
                levels.append(high)
    return ("above", min(levels)) if levels else ("not_found", None)


def calculate_entry_diagnostics(
    bars: Sequence[DailyBar], stop_price: float | None, *, horizon_days: int = 5,
) -> EntryDiagnostics | None:
    """基準日までの日足と採用戦略の損切り目安から比較指標を計算する。"""
    if not bars:
        return None
    current = float(bars[-1].close)
    if current <= 0:
        return None
    atr = wilder_atr(bars)
    atr = atr if atr is not None and atr > 0 else None
    ma20 = fmean(float(bar.close) for bar in bars[-20:]) if len(bars) >= 20 else None

    def period_return(window: int) -> float | None:
        base = float(bars[-window - 1].close) if len(bars) > window else 0
        return (current / base - 1) * 100 if base > 0 else None

    status, resistance = nearest_resistance(bars, lookback=60 if horizon_days == 5 else 120)
    room = resistance - current if resistance is not None else None
    valid_stop = stop_price if stop_price is not None and 0 < stop_price < current else None
    width = current - valid_stop if valid_stop is not None else None

    def rounded(value: float | None) -> float | None:
        return round(value, 4) if value is not None else None

    return EntryDiagnostics(
        current_price=current,
        atr=rounded(atr),
        sma20_distance_percent=rounded((current / ma20 - 1) * 100 if ma20 else None),
        sma20_distance_atr=rounded((current - ma20) / atr if ma20 and atr else None),
        return_10d_percent=rounded(period_return(10)),
        return_20d_percent=rounded(period_return(20)),
        rise_20d_atr=rounded(
            (current - float(bars[-21].close)) / atr if len(bars) > 20 and atr else None
        ),
        resistance_status=status,
        resistance_price=rounded(resistance),
        resistance_room_percent=rounded(room / current * 100 if room is not None else None),
        resistance_room_atr=rounded(room / atr if room is not None and atr else None),
        stop_price=valid_stop,
        stop_width_percent=rounded(width / current * 100 if width is not None else None),
        stop_width_atr=rounded(width / atr if width is not None and atr else None),
        resistance_risk_reward_ratio=rounded(
            room / width if room is not None and width is not None else None
        ),
    )
