from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from statistics import fmean

from stock_signal.domain.market_data import DailyBar


@dataclass(frozen=True, slots=True)
class ResistanceBand:
    """複数の局所高値が集中する、上値抵抗の候補帯。"""

    lower: float
    upper: float
    center: float
    touches: int
    first_touched: date
    last_touched: date
    distance_percent: float


def simple_moving_average_series(
    bars: Sequence[DailyBar],
    window: int,
) -> tuple[float | None, ...]:
    """終値の単純移動平均を、入力日足と同じ長さで返す。"""
    if window <= 0:
        raise ValueError("移動平均期間は1以上で指定してください")
    closes = [float(bar.close) for bar in bars]
    values: list[float | None] = [None] * len(closes)
    rolling_sum = 0.0
    for index, close in enumerate(closes):
        rolling_sum += close
        if index >= window:
            rolling_sum -= closes[index - window]
        if index >= window - 1:
            values[index] = rolling_sum / window
    return tuple(values)


def wilder_rsi_series(
    bars: Sequence[DailyBar],
    window: int = 14,
) -> tuple[float | None, ...]:
    """Wilder平滑化によるRSIを、入力日足と同じ長さで返す。"""
    if window <= 0:
        raise ValueError("RSI期間は1以上で指定してください")
    closes = [float(bar.close) for bar in bars]
    values: list[float | None] = [None] * len(closes)
    if len(closes) < window + 1:
        return tuple(values)

    changes = [
        current - previous
        for previous, current in zip(closes, closes[1:], strict=False)
    ]
    average_gain = fmean(max(change, 0.0) for change in changes[:window])
    average_loss = fmean(max(-change, 0.0) for change in changes[:window])

    def rsi_value() -> float:
        if average_loss == 0:
            return 100.0 if average_gain > 0 else 50.0
        relative_strength = average_gain / average_loss
        return 100 - 100 / (1 + relative_strength)

    values[window] = rsi_value()
    for change_index in range(window, len(changes)):
        change = changes[change_index]
        average_gain = (
            average_gain * (window - 1) + max(change, 0.0)
        ) / window
        average_loss = (
            average_loss * (window - 1) + max(-change, 0.0)
        ) / window
        values[change_index + 1] = rsi_value()
    return tuple(values)


def _local_highs(
    bars: Sequence[DailyBar],
    *,
    start: int,
    span: int,
) -> list[tuple[int, float]]:
    candidates = []
    for index in range(max(start, span), len(bars) - span):
        value = float(bars[index].high)
        before = [float(bars[item].high) for item in range(index - span, index)]
        after = [float(bars[item].high) for item in range(index + 1, index + span + 1)]
        if value >= max(before) and value >= max(after) and (
            value > max(before) or value > max(after)
        ):
            if candidates and index - candidates[-1][0] <= span:
                if value > candidates[-1][1]:
                    candidates[-1] = (index, value)
                continue
            candidates.append((index, value))
    return candidates


def resistance_bands(
    bars: Sequence[DailyBar],
    *,
    lookback: int,
    minimum_touches: int = 2,
    maximum_bands: int = 3,
    pivot_span: int = 2,
    cluster_tolerance_atr: float = 0.5,
    band_padding_atr: float = 0.2,
) -> tuple[ResistanceBand, ...]:
    """ATRで近接する局所高値をまとめ、現在の上値抵抗候補を返す。"""
    if lookback <= 0:
        raise ValueError("抵抗帯の参照期間は1以上で指定してください")
    if minimum_touches < 2:
        raise ValueError("抵抗帯には2回以上の接触が必要です")
    if maximum_bands <= 0:
        raise ValueError("抵抗帯の最大表示数は1以上で指定してください")
    if pivot_span <= 0:
        raise ValueError("局所高値の前後期間は1以上で指定してください")
    if cluster_tolerance_atr <= 0 or band_padding_atr <= 0:
        raise ValueError("抵抗帯のATR倍率は0より大きい値で指定してください")
    if len(bars) < 21:
        return ()
    atr = wilder_atr(bars, 20)
    if atr is None or atr <= 0:
        return ()

    start = max(0, len(bars) - lookback)
    peaks = _local_highs(bars, start=start, span=pivot_span)
    if not peaks:
        return ()
    tolerance = atr * cluster_tolerance_atr
    clusters: list[list[tuple[int, float]]] = []
    for peak in sorted(peaks, key=lambda item: item[1]):
        nearest = min(
            clusters,
            key=lambda cluster: abs(peak[1] - fmean(value for _, value in cluster)),
            default=None,
        )
        if nearest is not None:
            center = fmean(value for _, value in nearest)
            if abs(peak[1] - center) <= tolerance:
                nearest.append(peak)
                continue
        clusters.append([peak])

    current_close = float(bars[-1].close)
    if current_close <= 0:
        return ()
    candidates = []
    for cluster in clusters:
        if len(cluster) < minimum_touches:
            continue
        center = fmean(value for _, value in cluster)
        maximum_deviation = max(abs(value - center) for _, value in cluster)
        half_width = max(atr * band_padding_atr, maximum_deviation)
        lower = center - half_width
        upper = center + half_width
        # 終値で明確に上抜けた帯は、抵抗の役割を終えたものとする。
        if current_close > upper + atr * 0.1:
            continue
        indexes = [index for index, _ in cluster]
        candidates.append(
            ResistanceBand(
                lower=round(lower, 4),
                upper=round(upper, 4),
                center=round(center, 4),
                touches=len(cluster),
                first_touched=bars[min(indexes)].trade_date,
                last_touched=bars[max(indexes)].trade_date,
                distance_percent=round((center / current_close - 1) * 100, 2),
            )
        )
    return tuple(
        sorted(candidates, key=lambda band: abs(band.center - current_close))[
            :maximum_bands
        ]
    )


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
