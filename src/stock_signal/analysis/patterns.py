from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import fmean, median

from stock_signal.domain.analysis import (
    BreakoutKind,
    Direction,
    PatternDetection,
    PatternType,
)
from stock_signal.domain.market_data import DailyBar


def _atr(bars: Sequence[DailyBar], end: int, window: int = 20) -> float | None:
    """endを含めず、その直前までのATRを計算する。"""
    start = max(1, end - window)
    if end - start < window:
        return None
    true_ranges = []
    for index in range(start, end):
        previous_close = float(bars[index - 1].close)
        high = float(bars[index].high)
        low = float(bars[index].low)
        true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    return fmean(true_ranges)


def _linear_slope(values: Sequence[float]) -> float:
    count = len(values)
    if count < 2:
        return 0.0
    x_mean = (count - 1) / 2
    y_mean = fmean(values)
    denominator = sum((index - x_mean) ** 2 for index in range(count))
    if denominator == 0:
        return 0.0
    return sum(
        (index - x_mean) * (value - y_mean) for index, value in enumerate(values)
    ) / denominator


def _percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    index = round((len(ordered) - 1) * fraction)
    return ordered[index]


def _local_extrema(values: Sequence[float], *, maximum: bool) -> list[int]:
    extrema = []
    for index in range(2, len(values) - 2):
        neighbourhood = values[index - 2:index + 3]
        target = max(neighbourhood) if maximum else min(neighbourhood)
        if values[index] == target and neighbourhood.count(target) == 1:
            extrema.append(index)
    return extrema


@dataclass(frozen=True, slots=True)
class _BreakoutContext:
    index: int
    atr: float | None
    volume_ratio: float | None
    gap_atr: float | None
    breakout_kind: BreakoutKind


class TechnicalPatternDetector:
    """価格形状だけに依存する、完成済みパターンの検出器。"""

    scan_days = 25
    formation_days = 30

    @staticmethod
    def _crossed(
        bars: Sequence[DailyBar],
        context: _BreakoutContext,
        level: float,
        threshold: float,
        direction: Direction,
    ) -> bool:
        """水準の外側にいるだけでなく、その日に水準を跨いだかを判定する。"""
        if context.index == 0:
            return False
        previous_close = float(bars[context.index - 1].close)
        close = float(bars[context.index].close)
        if direction is Direction.UP:
            return previous_close <= level + threshold and close > level + threshold
        return previous_close >= level - threshold and close < level - threshold

    def detect(self, bars: Sequence[DailyBar]) -> tuple[PatternDetection, ...]:
        ordered = tuple(sorted(bars, key=lambda bar: bar.trade_date))
        if len(ordered) < 25:
            return ()
        found: list[PatternDetection] = []
        first_breakout = max(24, len(ordered) - self.scan_days)
        for breakout_index in range(first_breakout, len(ordered)):
            context = self._context(ordered, breakout_index)
            found.extend(self._detect_range_patterns(ordered, context))
            found.extend(self._detect_double_patterns(ordered, context))
            found.extend(self._detect_head_and_shoulders(ordered, context))

        unique: dict[PatternType, PatternDetection] = {}
        for pattern in found:
            unique[pattern.pattern_type] = pattern
        ordered_patterns = sorted(
            unique.values(), key=lambda item: (item.detected_at, item.fit_score), reverse=True
        )
        if not ordered_patterns:
            return ()
        latest_date = ordered_patterns[0].detected_at
        return tuple(item for item in ordered_patterns if item.detected_at == latest_date)

    def _context(self, bars: Sequence[DailyBar], index: int) -> _BreakoutContext:
        atr = _atr(bars, index)
        prior_volumes = [bar.volume for bar in bars[max(0, index - 60):index] if bar.volume > 0]
        volume_ratio = None
        if len(prior_volumes) >= 20:
            baseline = median(prior_volumes)
            volume_ratio = bars[index].volume / baseline if baseline > 0 else None
        gap_atr = None
        breakout_kind = BreakoutKind.NOT_EVALUATED
        if atr and atr > 0 and index > 0:
            gap_atr = (float(bars[index].open) - float(bars[index - 1].close)) / atr
            breakout_kind = (
                BreakoutKind.GAP_DRIVEN if abs(gap_atr) > 1.5 else BreakoutKind.NORMAL
            )
        return _BreakoutContext(index, atr, volume_ratio, gap_atr, breakout_kind)

    def _common_fields(
        self,
        bars: Sequence[DailyBar],
        context: _BreakoutContext,
        level: float,
        direction: Direction,
        formation_start: int,
    ) -> dict[str, object]:
        close = float(bars[context.index].close)
        signed_distance = close - level if direction is Direction.UP else level - close
        breakout_atr = (
            signed_distance / context.atr if context.atr is not None and context.atr > 0 else None
        )
        prior_start = max(0, formation_start - 20)
        prior = bars[prior_start:formation_start]
        prior_trend = None
        if len(prior) >= 10:
            first = fmean(float(bar.close) for bar in prior[:5])
            last = fmean(float(bar.close) for bar in prior[-5:])
            prior_trend = max(-100.0, min(100.0, (last / first - 1) * 500))
        return {
            "detected_at": bars[context.index].trade_date.isoformat(),
            "breakout_level": round(level, 4),
            "breakout_atr": round(breakout_atr, 2) if breakout_atr is not None else None,
            "volume_ratio": (
                round(context.volume_ratio, 2) if context.volume_ratio is not None else None
            ),
            "gap_atr": round(context.gap_atr, 2) if context.gap_atr is not None else None,
            "breakout_kind": context.breakout_kind,
            "prior_trend_score": round(prior_trend, 1) if prior_trend is not None else None,
        }

    def _detect_range_patterns(
        self, bars: Sequence[DailyBar], context: _BreakoutContext
    ) -> list[PatternDetection]:
        start = max(0, context.index - self.formation_days)
        formation = bars[start:context.index]
        if len(formation) < 20:
            return []
        highs = [float(bar.high) for bar in formation]
        lows = [float(bar.low) for bar in formation]
        average_price = fmean(float(bar.close) for bar in formation)
        upper_slope = _linear_slope(highs) / average_price
        lower_slope = _linear_slope(lows) / average_price
        resistance = _percentile(highs, 0.9)
        support = _percentile(lows, 0.1)
        tolerance = (context.atr or average_price * 0.015) * 0.4
        upper_touches = sum(abs(value - resistance) <= tolerance for value in highs)
        lower_touches = sum(abs(value - support) <= tolerance for value in lows)
        threshold = (context.atr or average_price * 0.01) * 0.1
        duration = len(formation)
        results: list[PatternDetection] = []

        flat_upper = abs(upper_slope) <= 0.0015
        flat_lower = abs(lower_slope) <= 0.0015
        if flat_upper and flat_lower and upper_touches >= 2 and lower_touches >= 2:
            fit = min(95.0, 62 + min(upper_touches + lower_touches, 10) * 3)
            if self._crossed(
                bars, context, resistance, threshold, Direction.UP
            ):
                common = self._common_fields(bars, context, resistance, Direction.UP, start)
                results.append(PatternDetection(
                    PatternType.RECTANGLE_BREAKOUT_UP, "レクタングル上放れ", Direction.UP,
                    fit_score=fit, duration_days=duration,
                    description=(
                        f"約{duration}営業日の価格帯上限{resistance:.2f}を"
                        "終値で上抜けました"
                    ),
                    **common,
                ))
            elif self._crossed(
                bars, context, support, threshold, Direction.DOWN
            ):
                common = self._common_fields(bars, context, support, Direction.DOWN, start)
                results.append(PatternDetection(
                    PatternType.RECTANGLE_BREAKOUT_DOWN,
                    "レクタングル下放れ",
                    Direction.DOWN,
                    fit_score=fit, duration_days=duration,
                    description=(
                        f"約{duration}営業日の価格帯下限{support:.2f}を"
                        "終値で下抜けました"
                    ),
                    **common,
                ))

        ascending_breakout = (
            flat_upper and lower_slope >= 0.001 and lower_touches >= 2
            and self._crossed(bars, context, resistance, threshold, Direction.UP)
        )
        if ascending_breakout:
            fit = min(95.0, 66 + min(lower_touches, 6) * 4)
            common = self._common_fields(bars, context, resistance, Direction.UP, start)
            results.append(PatternDetection(
                PatternType.ASCENDING_TRIANGLE,
                "アセンディング・トライアングル",
                Direction.UP,
                fit_score=fit, duration_days=duration,
                description=(
                    f"上値抵抗{resistance:.2f}と切り上がる安値を形成し、"
                    "終値で上抜けました"
                ),
                **common,
            ))
        descending_breakout = (
            flat_lower and upper_slope <= -0.001 and upper_touches >= 2
            and self._crossed(bars, context, support, threshold, Direction.DOWN)
        )
        if descending_breakout:
            fit = min(95.0, 66 + min(upper_touches, 6) * 4)
            common = self._common_fields(bars, context, support, Direction.DOWN, start)
            results.append(PatternDetection(
                PatternType.DESCENDING_TRIANGLE,
                "ディセンディング・トライアングル",
                Direction.DOWN,
                fit_score=fit, duration_days=duration,
                description=(
                    f"下値支持{support:.2f}と切り下がる高値を形成し、"
                    "終値で下抜けました"
                ),
                **common,
            ))
        return results

    def _detect_double_patterns(
        self, bars: Sequence[DailyBar], context: _BreakoutContext
    ) -> list[PatternDetection]:
        start = max(0, context.index - 60)
        formation = bars[start:context.index]
        if len(formation) < 20:
            return []
        highs = [float(bar.high) for bar in formation]
        lows = [float(bar.low) for bar in formation]
        peaks = _local_extrema(highs, maximum=True)
        troughs = _local_extrema(lows, maximum=False)
        close = float(bars[context.index].close)
        threshold = (context.atr or close * 0.01) * 0.1
        results: list[PatternDetection] = []
        recent_peaks = peaks[-8:]
        peak_pairs = list(zip(recent_peaks, recent_peaks[1:], strict=False))[::-1]
        for left, right in peak_pairs:
            similarity = abs(highs[left] / highs[right] - 1)
            has_higher_middle = max(highs[left + 1:right], default=0) > max(
                highs[left], highs[right]
            ) * 1.015
            if 5 <= right - left <= 35 and similarity <= 0.03 and not has_higher_middle:
                neckline = min(lows[left:right + 1])
                if self._crossed(
                    bars, context, neckline, threshold, Direction.DOWN
                ):
                    fit = max(60.0, min(95.0, 92 - similarity * 700))
                    common = self._common_fields(
                        bars, context, neckline, Direction.DOWN, start + left
                    )
                    results.append(PatternDetection(
                        PatternType.DOUBLE_TOP, "ダブルトップ", Direction.DOWN,
                        fit_score=round(fit, 1), duration_days=right - left,
                        description=(
                            "近い水準の2高値を形成後、"
                            f"ネックライン{neckline:.2f}を下抜けました"
                        ),
                        **common,
                    ))
                    break
        recent_troughs = troughs[-8:]
        trough_pairs = list(zip(recent_troughs, recent_troughs[1:], strict=False))[::-1]
        for left, right in trough_pairs:
            similarity = abs(lows[left] / lows[right] - 1)
            has_lower_middle = min(lows[left + 1:right], default=float("inf")) < min(
                lows[left], lows[right]
            ) * 0.985
            if 5 <= right - left <= 35 and similarity <= 0.03 and not has_lower_middle:
                neckline = max(highs[left:right + 1])
                if self._crossed(
                    bars, context, neckline, threshold, Direction.UP
                ):
                    fit = max(60.0, min(95.0, 92 - similarity * 700))
                    common = self._common_fields(
                        bars, context, neckline, Direction.UP, start + left
                    )
                    results.append(PatternDetection(
                        PatternType.DOUBLE_BOTTOM, "ダブルボトム", Direction.UP,
                        fit_score=round(fit, 1), duration_days=right - left,
                        description=(
                            "近い水準の2安値を形成後、"
                            f"ネックライン{neckline:.2f}を上抜けました"
                        ),
                        **common,
                    ))
                    break
        return results

    def _detect_head_and_shoulders(
        self, bars: Sequence[DailyBar], context: _BreakoutContext
    ) -> list[PatternDetection]:
        start = max(0, context.index - 70)
        formation = bars[start:context.index]
        if len(formation) < 25:
            return []
        highs = [float(bar.high) for bar in formation]
        lows = [float(bar.low) for bar in formation]
        peaks = _local_extrema(highs, maximum=True)
        troughs = _local_extrema(lows, maximum=False)
        close = float(bars[context.index].close)
        threshold = (context.atr or close * 0.01) * 0.1
        results: list[PatternDetection] = []

        recent_peaks = peaks[-8:]
        peak_triples = list(
            zip(recent_peaks, recent_peaks[1:], recent_peaks[2:], strict=False)
        )[::-1]
        for left, head, right in peak_triples:
            shoulder_similarity = abs(highs[left] / highs[right] - 1)
            spacing_ratio = (head - left) / max(right - head, 1)
            head_margin = min(highs[head] / highs[left] - 1, highs[head] / highs[right] - 1)
            left_neck = [index for index in troughs if left < index < head]
            right_neck = [index for index in troughs if head < index < right]
            if (
                left_neck and right_neck and shoulder_similarity <= 0.06
                and head_margin >= 0.025
                and 0.45 <= spacing_ratio <= 2.2
            ):
                neckline = fmean((lows[left_neck[-1]], lows[right_neck[0]]))
                if self._crossed(
                    bars, context, neckline, threshold, Direction.DOWN
                ):
                    raw_fit = 90 - shoulder_similarity * 400 - abs(1 - spacing_ratio) * 8
                    fit = max(60.0, min(95.0, raw_fit))
                    common = self._common_fields(
                        bars, context, neckline, Direction.DOWN, start + left
                    )
                    results.append(PatternDetection(
                        PatternType.HEAD_AND_SHOULDERS_TOP,
                        "ヘッド＆ショルダーズ・トップ",
                        Direction.DOWN,
                        fit_score=round(fit, 1), duration_days=right - left,
                        description=(
                            "3高値と中央の高いヘッドを形成後、"
                            f"ネックライン{neckline:.2f}を下抜けました"
                        ),
                        **common,
                    ))
                    break

        recent_troughs = troughs[-8:]
        trough_triples = list(
            zip(recent_troughs, recent_troughs[1:], recent_troughs[2:], strict=False)
        )[::-1]
        for left, head, right in trough_triples:
            shoulder_similarity = abs(lows[left] / lows[right] - 1)
            spacing_ratio = (head - left) / max(right - head, 1)
            head_margin = min(lows[left] / lows[head] - 1, lows[right] / lows[head] - 1)
            left_neck = [index for index in peaks if left < index < head]
            right_neck = [index for index in peaks if head < index < right]
            if (
                left_neck and right_neck and shoulder_similarity <= 0.06
                and head_margin >= 0.025
                and 0.45 <= spacing_ratio <= 2.2
            ):
                neckline = fmean((highs[left_neck[-1]], highs[right_neck[0]]))
                if self._crossed(
                    bars, context, neckline, threshold, Direction.UP
                ):
                    raw_fit = 90 - shoulder_similarity * 400 - abs(1 - spacing_ratio) * 8
                    fit = max(60.0, min(95.0, raw_fit))
                    common = self._common_fields(
                        bars, context, neckline, Direction.UP, start + left
                    )
                    results.append(PatternDetection(
                        PatternType.HEAD_AND_SHOULDERS_BOTTOM,
                        "ヘッド＆ショルダーズ・ボトム",
                        Direction.UP,
                        fit_score=round(fit, 1), duration_days=right - left,
                        description=(
                            "3安値と中央の深いヘッドを形成後、"
                            f"ネックライン{neckline:.2f}を上抜けました"
                        ),
                        **common,
                    ))
                    break
        return results
