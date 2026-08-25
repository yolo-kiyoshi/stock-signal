from __future__ import annotations

from collections.abc import Sequence
from statistics import fmean

from stock_signal.analysis.indicators import simple_moving_average_series, wilder_atr
from stock_signal.domain.analysis import (
    PositionEntryAssessment,
    PositionEntryCondition,
    PositionEntryPhase,
    PositionSupportLevel,
)
from stock_signal.domain.market_data import DailyBar


class PositionEntryEvaluator:
    """中期上昇トレンド内の押し目と支持帯反発を機械的に評価する。"""

    support_band_atr = 0.35
    approaching_support_atr = 1.0
    extended_from_support_atr = 1.5
    trend_break_atr = 0.5
    invalidation_buffer_atr = 0.5

    def evaluate(self, bars: Sequence[DailyBar]) -> PositionEntryAssessment:
        ordered = tuple(sorted(bars, key=lambda bar: bar.trade_date))
        if len(ordered) < 70:
            raise ValueError(
                "中長期の押し目評価には70営業日以上の日足が必要です"
            )
        atr = wilder_atr(ordered, 20)
        if atr is None or atr <= 0:
            raise ValueError("中長期の押し目評価に必要なATRを計算できません")

        closes = [float(bar.close) for bar in ordered]
        latest = ordered[-1]
        current = closes[-1]
        ma20 = simple_moving_average_series(ordered, 20)[-1]
        ma60_series = simple_moving_average_series(ordered, 60)
        ma60 = ma60_series[-1]
        ma60_previous = ma60_series[-6]
        if ma20 is None or ma60 is None or ma60_previous is None:
            raise ValueError(
                "中長期の押し目評価に必要な移動平均を計算できません"
            )

        average_order = ma20 >= ma60
        long_average_rising = ma60 > ma60_previous
        trend_floor_held = current >= ma60 - self.trend_break_atr * atr

        supports = [
            self._price_level(
                "moving_average_20",
                "20日移動平均",
                ma20,
                ordered,
                atr,
            ),
            self._price_level(
                "moving_average_60",
                "60日移動平均",
                ma60,
                ordered,
                atr,
            ),
        ]
        recent_low = min(float(bar.low) for bar in ordered[-21:-1])
        supports.append(
            self._price_level(
                "recent_20_day_low",
                "直近20日安値",
                recent_low,
                ordered,
                atr,
            )
        )
        former_resistance = self._former_resistance_support(ordered, atr)
        if former_resistance is not None:
            supports.append(former_resistance)

        relevant_supports = tuple(
            sorted(
                (
                    support
                    for support in supports
                    if support.level <= current + self.approaching_support_atr * atr
                ),
                key=lambda support: abs(support.distance_atr),
            )
        )
        if not relevant_supports:
            relevant_supports = tuple(
                sorted(supports, key=lambda support: abs(support.distance_atr))[:1]
            )
        held_touches = [
            support for support in relevant_supports if support.touched and support.held
        ]

        day_range = float(latest.high - latest.low)
        close_position = (
            (current - float(latest.low)) / day_range if day_range > 0 else 0.5
        )
        rebound_confirmed = (
            current > closes[-2]
            and current >= float(latest.open)
            and close_position >= 0.6
        )
        support_touched_and_held = bool(held_touches)
        conditions = (
            PositionEntryCondition(
                "average_order",
                "20日線が60日線以上",
                average_order,
                f"20日線{ma20:.2f}、60日線{ma60:.2f}です",
            ),
            PositionEntryCondition(
                "long_average_rising",
                "60日線が上向き",
                long_average_rising,
                (
                    f"60日線は5営業日前から"
                    f"{(ma60 / ma60_previous - 1) * 100:+.2f}%変化しています"
                ),
            ),
            PositionEntryCondition(
                "trend_floor_held",
                "中期トレンドを維持",
                trend_floor_held,
                f"終値は60日線から{(current - ma60) / atr:+.2f} ATRです",
            ),
            PositionEntryCondition(
                "support_held",
                "支持候補へ接触し終値で維持",
                support_touched_and_held,
                (
                    f"{held_touches[0].label}を終値で維持しました"
                    if held_touches
                    else "支持候補への接触と終値での維持を確認中です"
                ),
            ),
            PositionEntryCondition(
                "rebound_confirmation",
                "日足で反発を確認",
                rebound_confirmed,
                (
                    "前日終値を上回る陽線で、日中値幅の上側40%に引けました"
                    if rebound_confirmed
                    else (
                        "前日終値を上回る陽線と、"
                        "日中値幅の上側での引けを待ちます"
                    )
                ),
            ),
        )
        satisfied = sum(condition.satisfied for condition in conditions)
        unmet = [condition for condition in conditions if not condition.satisfied]
        trend_preserved = average_order and long_average_rising and trend_floor_held
        nearest_distance = min(abs(item.distance_atr) for item in relevant_supports)

        if not trend_preserved:
            phase = PositionEntryPhase.TREND_BROKEN
            summary = (
                "中期上昇トレンドを維持できていないため、"
                "安値だけでは買い場にしません"
            )
        elif support_touched_and_held and rebound_confirmed:
            phase = PositionEntryPhase.PULLBACK_CANDIDATE
            summary = (
                "中期上昇トレンド内で支持候補への接触と"
                "日足反発を確認しました"
            )
        elif any(support.touched for support in relevant_supports):
            phase = PositionEntryPhase.SUPPORT_TEST
            summary = (
                "支持候補を試しています。"
                "終値維持と反発確認を待ちます"
            )
        elif nearest_distance <= self.approaching_support_atr:
            phase = PositionEntryPhase.APPROACHING_SUPPORT
            summary = "中期上昇トレンド内で支持候補へ近づいています"
        elif current - ma20 >= self.extended_from_support_atr * atr:
            phase = PositionEntryPhase.TREND_EXTENDED
            summary = (
                "上昇トレンドですが支持候補から離れており、"
                "追随購入を急ぎません"
            )
        else:
            phase = PositionEntryPhase.NO_SETUP
            summary = (
                "中期トレンドは維持していますが、"
                "明確な押し目条件はありません"
            )

        invalidation = None
        if held_touches:
            invalidation = max(
                0.01,
                held_touches[0].lower - self.invalidation_buffer_atr * atr,
            )
        return PositionEntryAssessment(
            phase=phase,
            satisfied_conditions=satisfied,
            total_conditions=len(conditions),
            readiness_score=round(satisfied / len(conditions) * 100, 1),
            summary=summary,
            next_condition=unmet[0] if unmet else None,
            conditions=conditions,
            supports=relevant_supports,
            current_price=round(current, 4),
            atr=round(atr, 4),
            invalidation_price=(
                round(invalidation, 4) if invalidation is not None else None
            ),
        )

    def _price_level(
        self,
        key: str,
        label: str,
        level: float,
        bars: Sequence[DailyBar],
        atr: float,
    ) -> PositionSupportLevel:
        lower = level - self.support_band_atr * atr
        upper = level + self.support_band_atr * atr
        latest = bars[-1]
        current = float(latest.close)
        touched = float(latest.low) <= upper and float(latest.high) >= lower
        held = current >= lower
        return PositionSupportLevel(
            key=key,
            label=label,
            level=round(level, 4),
            lower=round(lower, 4),
            upper=round(upper, 4),
            distance_atr=round((current - level) / atr, 2),
            touched=touched,
            held=held,
            description=(
                f"{label}{level:.2f}を含む±{self.support_band_atr:.2f} ATR帯です"
            ),
        )

    def _former_resistance_support(
        self,
        bars: Sequence[DailyBar],
        atr: float,
    ) -> PositionSupportLevel | None:
        """繰り返し高値を上抜けた後、現在値より下にある帯を探す。"""
        start = max(2, len(bars) - 120)
        end = len(bars) - 3
        peaks: list[tuple[int, float]] = []
        for index in range(start, end):
            high = float(bars[index].high)
            neighbours = [
                float(bars[item].high)
                for item in range(index - 2, index + 3)
                if item != index
            ]
            if high >= max(neighbours):
                peaks.append((index, high))
        clusters: list[list[tuple[int, float]]] = []
        for peak in sorted(peaks, key=lambda item: item[1]):
            for cluster in clusters:
                center = fmean(value for _, value in cluster)
                if abs(peak[1] - center) <= 0.5 * atr:
                    cluster.append(peak)
                    break
            else:
                clusters.append([peak])

        latest = bars[-1]
        current = float(latest.close)
        candidates: list[PositionSupportLevel] = []
        for cluster in clusters:
            if len(cluster) < 2:
                continue
            center = fmean(value for _, value in cluster)
            last_peak = max(index for index, _ in cluster)
            if center > current + self.approaching_support_atr * atr:
                continue
            broke_above = any(
                float(bar.close) > center + 0.35 * atr
                for bar in bars[last_peak + 1:-1]
            )
            if not broke_above:
                continue
            candidates.append(
                self._price_level(
                    "former_resistance",
                    "上抜け後の旧抵抗帯",
                    center,
                    bars,
                    atr,
                )
            )
        return min(
            candidates,
            key=lambda item: abs(item.distance_atr),
            default=None,
        )
