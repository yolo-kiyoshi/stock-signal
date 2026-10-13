from __future__ import annotations

from collections.abc import Sequence
from statistics import fmean, median

from stock_signal.analysis.entry_diagnostics import nearest_resistance
from stock_signal.analysis.indicators import simple_moving_average_series, wilder_atr
from stock_signal.analysis.position_entry import PositionEntryEvaluator
from stock_signal.domain.analysis import (
    PositionEntryAssessment,
    PositionEntryCondition,
    PositionEntryPhase,
)
from stock_signal.domain.market_data import DailyBar


class SwingPullbackEvaluator:
    """スイングの押し目反発をブレイクの経過日数から独立して評価する。"""

    def evaluate(self, bars: Sequence[DailyBar]) -> PositionEntryAssessment | None:
        ordered = tuple(sorted(bars, key=lambda bar: bar.trade_date))
        if len(ordered) < 30:
            return None
        atr = wilder_atr(ordered)
        if atr is None or atr <= 0:
            return None
        closes = [float(bar.close) for bar in ordered]
        current = closes[-1]
        latest = ordered[-1]
        ma20_series = simple_moving_average_series(ordered, 20)
        ma20, prior_ma20 = ma20_series[-1], ma20_series[-6]
        assert ma20 is not None and prior_ma20 is not None
        trend = fmean(closes[-10:]) >= ma20 and ma20 > prior_ma20 and current >= ma20 - .5 * atr

        # 戻り期間より前の20日線を固定し、現在足による支持位置の移動を避ける。
        support_evaluator = PositionEntryEvaluator()
        anchor = fmean(closes[-23:-3])
        supports = [support_evaluator._price_level(
            "swing_sma20", "押し目前の20日線", anchor, ordered, atr,
        )]
        former = support_evaluator._former_resistance_support(ordered[:-3], atr)
        if former is not None:
            supports.append(support_evaluator._price_level(
                "swing_former_resistance", "押し目前の旧抵抗帯", former.level, ordered, atr,
            ))
        held = [support for support in supports if support.touched and support.held]
        # 直近3本より前に高値と支持帯より上の価格が存在したことを確認する。
        prior_peak = max(float(bar.high) for bar in ordered[-13:-3])
        pullback_low = min(float(bar.low) for bar in ordered[-3:])
        returned = prior_peak - pullback_low >= .75 * atr
        held = [
            support for support in held
            if any(float(bar.close) > support.upper for bar in ordered[-13:-3])
            and all(float(bar.close) >= support.lower for bar in ordered[-3:])
        ]
        chosen = min(held, key=lambda item: abs(item.distance_atr), default=None)
        close_position = (
            (current - float(latest.low)) / float(latest.high - latest.low)
            if latest.high > latest.low else 0
        )
        rebound = current > closes[-2] and current > float(latest.open) and close_position >= .6
        lows = [float(bar.low) for bar in ordered[-3:]]
        stabilized = lows[-1] >= lows[-2]
        baseline = median(bar.volume for bar in ordered[-21:-1])
        volume_ratio = latest.volume / baseline if baseline > 0 else None
        participation = volume_ratio is not None and volume_ratio >= .8
        near_support = chosen is not None and 0 <= current - chosen.level <= 1.5 * atr
        not_extended = (current - ma20) / atr <= 2 and (current - closes[-4]) / atr <= 2.5
        specifications = (
            ("swing_trend", "10日線・上向き20日線を維持", trend,
             "10日線が20日線以上、20日線が5日前より上、終値が20日線−0.5 ATR以上を確認します"),
            ("swing_return", "高値から支持候補へ戻る", returned and chosen is not None,
             "支持帯の上から0.75 ATR以上戻り、直近3本で接触後の終値維持を確認します"),
            ("swing_low_stable", "安値切り下げが停止", stabilized,
             "当日安値が前日安値以上になるまで待ちます"),
            ("swing_rebound", "陽線で反発を確認", rebound,
             "前日終値を上回る陽線で、日中値幅の上側40%に引けることを確認します"),
            ("swing_volume", "反発出来高を確認", participation,
             "当日出来高が直前20営業日中央値の0.8倍以上であることを確認します"),
            ("swing_distance", "支持帯から追いかけ過ぎない", near_support and not_extended,
             "支持中心から1.5 ATR以内、20日線から2 ATR以内、3日上昇2.5 ATR以内を確認します"),
        )
        conditions = tuple(PositionEntryCondition(*values) for values in specifications)
        unmet = [condition for condition in conditions if not condition.satisfied]
        if not trend:
            phase = PositionEntryPhase.TREND_BROKEN
        elif not unmet:
            phase = PositionEntryPhase.PULLBACK_CANDIDATE
        elif not not_extended:
            phase = PositionEntryPhase.TREND_EXTENDED
        elif any(support.touched for support in supports):
            phase = PositionEntryPhase.SUPPORT_TEST
        else:
            phase = PositionEntryPhase.NO_SETUP
        stop = max(.01, min(chosen.lower, pullback_low) - .5 * atr) if chosen else None
        _, target = nearest_resistance(ordered)
        ratio = (
            (target - current) / (current - stop)
            if target is not None and stop is not None and stop < current < target else None
        )
        summary = (
            "スイングの押し目で支持維持・安値切り下げ停止・陽線反発を確認しました"
            if not unmet else f"スイング押し目待ち：{unmet[0].label}が未確認です"
        )
        return PositionEntryAssessment(
            phase=phase, satisfied_conditions=len(conditions) - len(unmet),
            total_conditions=len(conditions),
            readiness_score=round((len(conditions) - len(unmet)) / len(conditions) * 100, 1),
            summary=summary, next_condition=unmet[0] if unmet else None,
            conditions=conditions, supports=tuple(supports), current_price=current,
            atr=atr, invalidation_price=stop, target_price=target,
            risk_reward_ratio=ratio, volume_ratio=volume_ratio,
            support_touch_age_days=chosen.touch_age_days if chosen else None,
        )
