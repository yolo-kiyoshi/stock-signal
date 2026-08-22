from __future__ import annotations

from collections.abc import Sequence
from statistics import fmean, median

from stock_signal.analysis.horizons import HorizonProfile, get_horizon_profile
from stock_signal.analysis.indicators import wilder_atr
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


def _local_troughs(values: Sequence[float]) -> list[int]:
    return [
        index
        for index in range(2, len(values) - 2)
        if values[index] == min(values[index - 2:index + 3])
        and values[index - 2:index + 3].count(values[index]) == 1
    ]


class TransitionReadinessEvaluator:
    """底固めから初動までを、説明可能な条件の進捗として評価する。"""

    maximum_below_trigger_atr = 0.2
    maximum_above_trigger_atr = 0.1
    initial_volume_ratio_threshold = 1.2
    minimum_pattern_height_atr = 0.5
    invalidation_atr = 0.5
    invalidation_buffer_atr = 0.1

    def evaluate(
        self,
        bars: Sequence[DailyBar],
        patterns: Sequence[PatternDetection],
        lifecycles: Sequence[PatternLifecycleAssessment],
        direction: Direction,
        context: AnalysisContext,
        horizon_days: int,
    ) -> TransitionReadiness:
        profile = get_horizon_profile(horizon_days)
        ordered = tuple(sorted(bars, key=lambda bar: bar.trade_date))
        if len(ordered) < profile.minimum_bars:
            raise ValueError(
                f"{profile.label}の転換準備度には"
                f"{profile.minimum_bars}営業日以上の日足が必要です"
            )
        closes = [float(bar.close) for bar in ordered]
        lows = [float(bar.low) for bar in ordered]
        current_close = closes[-1]
        atr = wilder_atr(ordered)
        if atr is None:
            raise ValueError("ATRの計算に必要な日足が不足しています")
        risk_unit = max(atr, current_close * 0.005, 0.01)
        trigger_price, trigger_name, formation_low = self._trigger_level(
            ordered, patterns, profile.trigger_lookback
        )
        pattern_height = trigger_price - formation_low
        pattern_height_atr = pattern_height / risk_unit

        recent_lows = lows[-profile.stability_lookback:]
        low_position = recent_lows.index(min(recent_lows))
        days_since_low = len(recent_lows) - 1 - low_position
        stability = TransitionCondition(
            "low_stability",
            "安値更新の停止",
            days_since_low >= profile.stability_days,
            True,
            (
                f"{profile.stability_lookback}日安値から"
                f"{days_since_low}営業日経過しています"
                if days_since_low
                else f"本日{profile.stability_lookback}日安値を更新しており、"
                "底固めを確認できません"
            ),
            float(days_since_low),
            float(profile.stability_days),
            "営業日",
        )

        momentum_window = profile.recent_window
        average_window = profile.recent_average_window
        short_average = fmean(closes[-average_window:])
        recent_momentum = (
            closes[-1] - closes[-momentum_window - 1]
        ) / risk_unit
        prior_momentum = (
            closes[-momentum_window - 1]
            - closes[-momentum_window * 2 - 1]
        ) / risk_unit
        short_trend_met = (
            current_close > short_average
            and recent_momentum >= 0
            and recent_momentum > prior_momentum
        )
        short_trend = TransitionCondition(
            "short_trend",
            "短期モメンタム改善" if horizon_days <= 5 else "中期モメンタム改善",
            short_trend_met,
            True,
            (
                f"終値は{average_window}日平均{short_average:.2f}を上回り、"
                f"{momentum_window}日モメンタムは{recent_momentum:+.2f} ATRです"
                if short_trend_met
                else f"終値{current_close:.2f}、{average_window}日平均"
                f"{short_average:.2f}、{momentum_window}日モメンタム"
                f"{recent_momentum:+.2f} ATRを確認中です"
            ),
            round(recent_momentum, 2),
            0.0,
            "ATR",
        )

        distance_to_trigger_atr = (trigger_price - current_close) / risk_unit
        near_trigger_met = (
            -self.maximum_above_trigger_atr
            <= distance_to_trigger_atr
            <= self.maximum_below_trigger_atr
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
            self.maximum_below_trigger_atr,
            "ATR下以内",
        )

        pattern_structure = TransitionCondition(
            "pattern_structure",
            "形成値幅の有効性",
            pattern_height_atr >= self.minimum_pattern_height_atr,
            True,
            (
                f"形成値幅は{pattern_height_atr:.2f} ATRです"
                if pattern_height_atr >= self.minimum_pattern_height_atr
                else f"形成値幅は{pattern_height_atr:.2f} ATRで、"
                f"最低{self.minimum_pattern_height_atr:.1f} ATRに届きません"
            ),
            round(pattern_height_atr, 2),
            self.minimum_pattern_height_atr,
            "ATR以上",
        )

        bullish = [pattern for pattern in patterns if pattern.direction is Direction.UP]
        lead_bullish = max(bullish, key=lambda pattern: pattern.fit_score) if bullish else None
        prior_volumes = [bar.volume for bar in ordered[-61:-1] if bar.volume > 0]
        volume_ratio = lead_bullish.volume_ratio if lead_bullish else None
        if volume_ratio is None and len(prior_volumes) >= 20:
            baseline_volume = median(prior_volumes)
            if baseline_volume > 0:
                volume_ratio = ordered[-1].volume / baseline_volume
        initial_volume_confirmed = (
            volume_ratio is not None
            and volume_ratio >= self.initial_volume_ratio_threshold
        )
        initial_volume = TransitionCondition(
            "initial_volume",
            "初動出来高",
            initial_volume_confirmed,
            True,
            (
                f"出来高は平常時の{volume_ratio:.2f}倍です。"
                f"初動基準は{self.initial_volume_ratio_threshold:.1f}倍です"
                if volume_ratio is not None
                else "初動を確認できる出来高履歴が不足しています"
            ),
            round(volume_ratio, 2) if volume_ratio is not None else None,
            self.initial_volume_ratio_threshold,
            "倍以上",
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
            pattern_structure,
            initial_volume,
            normal_gap,
        ]
        market_condition = self._market_condition(context, ordered, profile)
        if market_condition is not None:
            conditions.append(market_condition)
        earnings_condition = self._earnings_condition(
            context, ordered, profile
        )
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
            near_trigger.satisfied and initial_volume.satisfied,
            len(unsatisfied),
        )
        summary = self._summary(phase, unsatisfied)

        invalidation_price = max(
            0.01,
            min(
                formation_low,
                trigger_price - self.invalidation_atr * risk_unit,
            )
            - self.invalidation_buffer_atr * risk_unit,
        )
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
            current_price=round(current_close, 4),
            trigger_price=round(trigger_price, 4),
            invalidation_price=round(invalidation_price, 4),
            target_price=round(target_price, 4),
            risk_reward_ratio=round(risk_reward, 2) if risk_reward is not None else None,
        )

    @staticmethod
    def _trigger_level(
        bars: Sequence[DailyBar],
        patterns: Sequence[PatternDetection],
        trigger_lookback: int,
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
        recent = bars[-trigger_lookback - 1:-1]
        return (
            max(float(bar.high) for bar in recent),
            f"直近{trigger_lookback}日高値",
            min(float(bar.low) for bar in recent),
        )

    @staticmethod
    def _market_condition(
        context: AnalysisContext,
        bars: Sequence[DailyBar],
        profile: HorizonProfile,
    ) -> TransitionCondition | None:
        market = [
            bar for bar in context.market_bars if bar.trade_date <= bars[-1].trade_date
        ]
        if len(market) <= profile.market_window:
            return None
        change = float(
            market[-1].close / market[-profile.market_window - 1].close - 1
        ) * 100
        return TransitionCondition(
            "market_environment",
            "市場環境",
            change >= profile.market_minimum_return_percent,
            True,
            f"TOPIXの{profile.market_window}営業日騰落率は{change:+.2f}%です",
            round(change, 2),
            profile.market_minimum_return_percent,
            "%以上",
        )

    @staticmethod
    def _earnings_condition(
        context: AnalysisContext,
        bars: Sequence[DailyBar],
        profile: HorizonProfile,
    ) -> TransitionCondition | None:
        if not context.earnings_synced or context.next_earnings_date is None:
            return None
        days = (context.next_earnings_date - bars[-1].trade_date).days
        return TransitionCondition(
            "earnings_buffer",
            "決算までの余裕",
            days < 0 or days > profile.earnings_exclusion_days,
            True,
            f"次回決算予定まで{days}日です",
            float(days),
            float(profile.earnings_exclusion_days),
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
