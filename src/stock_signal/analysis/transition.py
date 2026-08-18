from __future__ import annotations

from collections.abc import Sequence
from statistics import fmean, median

from stock_signal.domain.analysis import (
    AnalysisContext,
    Direction,
    PatternDetection,
    PatternLifecycleAssessment,
    PatternLifecycleStatus,
    TransitionCondition,
    TransitionPhase,
    TransitionReadiness,
)
from stock_signal.domain.market_data import DailyBar


def _atr(bars: Sequence[DailyBar], window: int = 20) -> float:
    true_ranges = []
    for index in range(len(bars) - window, len(bars)):
        previous_close = float(bars[index - 1].close)
        high = float(bars[index].high)
        low = float(bars[index].low)
        true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    return fmean(true_ranges)


def _local_troughs(values: Sequence[float]) -> list[int]:
    return [
        index
        for index in range(2, len(values) - 2)
        if values[index] == min(values[index - 2:index + 3])
        and values[index - 2:index + 3].count(values[index]) == 1
    ]


class TransitionReadinessEvaluator:
    """底固めから初動までを、説明可能な条件の進捗として評価する。"""

    stability_days = 5
    near_trigger_atr = 0.5
    maximum_post_trigger_atr = 1.0
    breakout_atr = 0.1
    volume_ratio_threshold = 1.5
    earnings_buffer_days = 5

    def evaluate(
        self,
        bars: Sequence[DailyBar],
        patterns: Sequence[PatternDetection],
        lifecycles: Sequence[PatternLifecycleAssessment],
        direction: Direction,
        context: AnalysisContext,
    ) -> TransitionReadiness:
        ordered = tuple(sorted(bars, key=lambda bar: bar.trade_date))
        if len(ordered) < 21:
            raise ValueError("転換準備度の評価には21営業日以上の日足が必要です")
        closes = [float(bar.close) for bar in ordered]
        lows = [float(bar.low) for bar in ordered]
        current_close = closes[-1]
        atr = _atr(ordered)
        risk_unit = max(atr, current_close * 0.005, 0.01)
        trigger_price, trigger_name, formation_low = self._trigger_level(
            ordered, patterns
        )

        recent_lows = lows[-20:]
        low_position = recent_lows.index(min(recent_lows))
        days_since_low = len(recent_lows) - 1 - low_position
        stability = TransitionCondition(
            "low_stability",
            "安値更新の停止",
            days_since_low >= self.stability_days,
            True,
            (
                f"20日安値から{days_since_low}営業日経過しています"
                if days_since_low
                else "本日20日安値を更新しており、底固めを確認できません"
            ),
            float(days_since_low),
            float(self.stability_days),
            "営業日",
        )

        short_average = fmean(closes[-5:])
        recent_momentum = (closes[-1] - closes[-4]) / risk_unit
        prior_momentum = (closes[-4] - closes[-7]) / risk_unit
        short_trend_met = (
            current_close > short_average
            and recent_momentum >= 0
            and recent_momentum > prior_momentum
        )
        short_trend = TransitionCondition(
            "short_trend",
            "短期モメンタム改善",
            short_trend_met,
            True,
            (
                f"終値は5日平均{short_average:.2f}を上回り、"
                f"3日モメンタムは{recent_momentum:+.2f} ATRです"
                if short_trend_met
                else f"終値{current_close:.2f}、5日平均{short_average:.2f}、"
                f"3日モメンタム{recent_momentum:+.2f} ATRを確認中です"
            ),
            round(recent_momentum, 2),
            0.0,
            "ATR",
        )

        distance_to_trigger_atr = (trigger_price - current_close) / risk_unit
        near_trigger_met = (
            -self.maximum_post_trigger_atr
            <= distance_to_trigger_atr
            <= self.near_trigger_atr
        )
        near_trigger = TransitionCondition(
            "near_trigger",
            "転換水準への接近",
            near_trigger_met,
            True,
            (
                f"{trigger_name}{trigger_price:.2f}まで"
                f"{distance_to_trigger_atr:+.2f} ATRです"
            ),
            round(distance_to_trigger_atr, 2),
            self.near_trigger_atr,
            "ATR以内",
        )

        bullish = [pattern for pattern in patterns if pattern.direction is Direction.UP]
        lead_bullish = max(bullish, key=lambda pattern: pattern.fit_score) if bullish else None
        prior_volumes = [bar.volume for bar in ordered[-61:-1] if bar.volume > 0]
        volume_ratio = lead_bullish.volume_ratio if lead_bullish else None
        if volume_ratio is None and len(prior_volumes) >= 20:
            baseline_volume = median(prior_volumes)
            if baseline_volume > 0:
                volume_ratio = ordered[-1].volume / baseline_volume
        breakout_price = trigger_price + self.breakout_atr * risk_unit
        price_confirmed = (
            lead_bullish.breakout_atr is not None
            and lead_bullish.breakout_atr >= self.breakout_atr
            if lead_bullish
            else current_close >= breakout_price
        )
        volume_confirmed = (
            volume_ratio is not None and volume_ratio >= self.volume_ratio_threshold
        )
        breakout_confirmation = TransitionCondition(
            "breakout_confirmation",
            "価格と出来高の転換確認",
            price_confirmed and volume_confirmed,
            True,
            (
                f"終値{breakout_price:.2f}以上かつ出来高{self.volume_ratio_threshold:.1f}倍"
                f"が必要です（現在{current_close:.2f}、"
                f"{volume_ratio:.2f}倍）"
                if volume_ratio is not None
                else f"終値{breakout_price:.2f}以上と出来高履歴の確認が必要です"
            ),
            round(current_close, 4),
            round(breakout_price, 4),
            "円",
        )

        gap_atr = (
            float(ordered[-1].open) - float(ordered[-2].close)
        ) / risk_unit
        normal_gap = TransitionCondition(
            "normal_gap",
            "大きな窓開けがない",
            abs(gap_atr) <= 1.5,
            True,
            f"当日の窓開けは{gap_atr:+.2f} ATRです",
            round(gap_atr, 2),
            1.5,
            "絶対ATR以下",
        )

        conditions = [
            stability,
            short_trend,
            near_trigger,
            breakout_confirmation,
            normal_gap,
        ]
        market_condition = self._market_condition(context, ordered)
        if market_condition is not None:
            conditions.append(market_condition)
        earnings_condition = self._earnings_condition(context, ordered)
        if earnings_condition is not None:
            conditions.append(earnings_condition)

        required = [condition for condition in conditions if condition.required]
        satisfied = sum(condition.satisfied for condition in required)
        unsatisfied = [condition for condition in required if not condition.satisfied]
        phase = self._phase(
            direction,
            patterns,
            lifecycles,
            stability.satisfied,
            short_trend.satisfied,
            breakout_confirmation.satisfied,
            len(unsatisfied),
        )
        summary = self._summary(phase, unsatisfied)

        invalidation_price = max(
            formation_low,
            trigger_price - 0.5 * risk_unit,
        )
        pattern_height = max(trigger_price - formation_low, risk_unit)
        target_price = trigger_price + pattern_height
        downside = current_close - invalidation_price
        upside = target_price - current_close
        risk_reward = upside / downside if downside > 0 and upside > 0 else None
        return TransitionReadiness(
            phase=phase,
            satisfied_conditions=satisfied,
            total_conditions=len(required),
            readiness_score=round(satisfied / len(required) * 100, 1),
            summary=summary,
            next_condition=unsatisfied[0] if unsatisfied else None,
            conditions=tuple(conditions),
            trigger_price=round(trigger_price, 4),
            invalidation_price=round(invalidation_price, 4),
            target_price=round(target_price, 4),
            risk_reward_ratio=round(risk_reward, 2) if risk_reward is not None else None,
        )

    @staticmethod
    def _trigger_level(
        bars: Sequence[DailyBar], patterns: Sequence[PatternDetection]
    ) -> tuple[float, str, float]:
        bullish = [pattern for pattern in patterns if pattern.direction is Direction.UP]
        if bullish:
            lead = max(bullish, key=lambda pattern: pattern.fit_score)
            start = max(0, len(bars) - lead.duration_days - 6)
            formation_low = min(float(bar.low) for bar in bars[start:-1])
            return lead.breakout_level, "ブレイク水準", formation_low

        formation = bars[-61:-1]
        lows = [float(bar.low) for bar in formation]
        highs = [float(bar.high) for bar in formation]
        troughs = _local_troughs(lows)
        for left, right in reversed(list(zip(troughs, troughs[1:], strict=False))):
            similarity = abs(lows[left] / lows[right] - 1)
            if 5 <= right - left <= 35 and similarity <= 0.05:
                neckline = max(highs[left:right + 1])
                return neckline, "仮ネックライン", min(lows[left], lows[right])
        recent = bars[-11:-1]
        return (
            max(float(bar.high) for bar in recent),
            "直近10日高値",
            min(float(bar.low) for bar in recent),
        )

    @staticmethod
    def _market_condition(
        context: AnalysisContext, bars: Sequence[DailyBar]
    ) -> TransitionCondition | None:
        market = [
            bar for bar in context.market_bars if bar.trade_date <= bars[-1].trade_date
        ]
        if len(market) < 21:
            return None
        change = float(market[-1].close / market[-21].close - 1) * 100
        return TransitionCondition(
            "market_environment",
            "市場環境",
            change >= -2.0,
            True,
            f"TOPIXの20営業日騰落率は{change:+.2f}%です",
            round(change, 2),
            -2.0,
            "%以上",
        )

    def _earnings_condition(
        self, context: AnalysisContext, bars: Sequence[DailyBar]
    ) -> TransitionCondition | None:
        if not context.earnings_synced or context.next_earnings_date is None:
            return None
        days = (context.next_earnings_date - bars[-1].trade_date).days
        return TransitionCondition(
            "earnings_buffer",
            "決算までの余裕",
            days < 0 or days > self.earnings_buffer_days,
            True,
            f"次回決算予定まで{days}日です",
            float(days),
            float(self.earnings_buffer_days),
            "日超",
        )

    @staticmethod
    def _phase(
        direction: Direction,
        patterns: Sequence[PatternDetection],
        lifecycles: Sequence[PatternLifecycleAssessment],
        stable: bool,
        short_trend: bool,
        breakout_confirmed: bool,
        unmet_count: int,
    ) -> TransitionPhase:
        lifecycle_by_type = {item.pattern_type: item for item in lifecycles}
        for pattern in patterns:
            lifecycle = lifecycle_by_type.get(pattern.pattern_type)
            if lifecycle and lifecycle.status in {
                PatternLifecycleStatus.WEAKENING,
                PatternLifecycleStatus.FAILED,
            }:
                return TransitionPhase.CAUTION
        if breakout_confirmed and unmet_count == 0:
            return TransitionPhase.EARLY_REVERSAL
        if unmet_count == 1:
            return TransitionPhase.ONE_GATE_REMAINING
        if direction is Direction.UP:
            return TransitionPhase.UPTREND
        if stable and short_trend:
            return TransitionPhase.PREPARING
        if stable:
            return TransitionPhase.BOTTOMING
        return TransitionPhase.FALLING

    @staticmethod
    def _summary(
        phase: TransitionPhase, unsatisfied: Sequence[TransitionCondition]
    ) -> str:
        labels = {
            TransitionPhase.FALLING: "安値更新が続いており、転換待ちです",
            TransitionPhase.BOTTOMING: "安値更新は止まり、底固めを観察中です",
            TransitionPhase.PREPARING: "底固めと短期改善を確認し、転換水準を待っています",
            TransitionPhase.ONE_GATE_REMAINING: "転換条件はあと1つです",
            TransitionPhase.EARLY_REVERSAL: "価格と出来高による転換初動を確認しました",
            TransitionPhase.UPTREND: "すでに上昇方向で、先回り段階は過ぎています",
            TransitionPhase.CAUTION: "パターンの勢い弱化または失敗を警戒します",
        }
        summary = labels[phase]
        if phase is TransitionPhase.ONE_GATE_REMAINING and unsatisfied:
            return f"{summary}：{unsatisfied[0].label}"
        return summary
