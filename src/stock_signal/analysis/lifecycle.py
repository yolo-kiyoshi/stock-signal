from __future__ import annotations

from collections.abc import Sequence
from statistics import fmean

from stock_signal.analysis.indicators import wilder_atr
from stock_signal.domain.analysis import (
    Direction,
    PatternDetection,
    PatternGuidance,
    PatternLifecycleAssessment,
    PatternLifecycleStatus,
)
from stock_signal.domain.market_data import DailyBar


class PatternLifecycleEvaluator:
    """価格形状の検出後に、有効期間・失敗・目標到達を独立評価する。"""

    entry_window_days = 3
    maximum_monitoring_days = 20
    invalidation_atr = 0.5
    invalidation_buffer_atr = 0.1
    weakening_atr = 0.5
    maximum_entry_distance_atr = 1.0
    maximum_target_progress = 0.5
    minimum_remaining_risk_reward_ratio = 1.0
    minimum_execution_risk_reward_ratio = 1.2
    maximum_three_day_momentum_atr = 2.5
    maximum_distance_from_sma5_atr = 1.5

    def evaluate(
        self,
        bars: Sequence[DailyBar],
        patterns: Sequence[PatternDetection],
    ) -> tuple[PatternLifecycleAssessment, ...]:
        ordered = tuple(sorted(bars, key=lambda bar: bar.trade_date))
        if not ordered:
            return ()
        return tuple(
            assessment
            for pattern in patterns
            if (assessment := self._evaluate_one(ordered, pattern)) is not None
        )

    def _evaluate_one(
        self,
        bars: Sequence[DailyBar],
        pattern: PatternDetection,
    ) -> PatternLifecycleAssessment | None:
        breakout_index = next(
            (
                index
                for index, bar in enumerate(bars)
                if bar.trade_date.isoformat() == pattern.detected_at
            ),
            None,
        )
        if breakout_index is None:
            return None

        elapsed = len(bars) - 1 - breakout_index
        current_close = float(bars[-1].close)
        breakout_close = float(bars[breakout_index].close)
        current_atr = wilder_atr(bars)
        breakout_atr = wilder_atr(bars, end=breakout_index)
        if breakout_atr is None or breakout_atr <= 0:
            return None
        risk_unit = breakout_atr
        direction_sign = 1 if pattern.direction is Direction.UP else -1

        formation_length = max(20, pattern.duration_days + 5)
        formation = bars[max(0, breakout_index - formation_length) : breakout_index]
        if pattern.formation_start and pattern.formation_end:
            formation = [
                bar
                for bar in bars[:breakout_index]
                if pattern.formation_start <= bar.trade_date.isoformat() <= pattern.formation_end
            ]
        if not formation:
            return None
        if pattern.direction is Direction.UP:
            extreme = min(float(bar.low) for bar in formation)
            pattern_height = pattern.breakout_level - extreme
            invalidation_price = max(
                0.01,
                min(
                    extreme,
                    pattern.breakout_level - self.invalidation_atr * risk_unit,
                )
                - self.invalidation_buffer_atr * risk_unit,
            )
        else:
            extreme = max(float(bar.high) for bar in formation)
            pattern_height = extreme - pattern.breakout_level
            invalidation_price = (
                max(
                    extreme,
                    pattern.breakout_level + self.invalidation_atr * risk_unit,
                )
                + self.invalidation_buffer_atr * risk_unit
            )
        pattern_height = (
            pattern.pattern_height if pattern.pattern_height is not None else pattern_height
        )
        if pattern_height <= 0:
            return None
        target_price = pattern.breakout_level + direction_sign * pattern_height

        recent_start = max(breakout_index, len(bars) - 4)
        recent_base = float(bars[recent_start].close)
        recent_momentum_atr = (
            direction_sign * (current_close - recent_base) / current_atr
            if current_atr is not None and current_atr > 0
            else None
        )
        signed_target_distance = direction_sign * (current_close - target_price)
        signed_invalidation_distance = direction_sign * (current_close - invalidation_price)
        breakout_distance_atr = (
            direction_sign * (current_close - pattern.breakout_level) / risk_unit
            if risk_unit > 0
            else None
        )
        target_progress = direction_sign * (current_close - pattern.breakout_level) / pattern_height
        remaining_reward = direction_sign * (target_price - current_close)
        current_risk = direction_sign * (current_close - invalidation_price)
        remaining_risk_reward_ratio = (
            max(0.0, remaining_reward) / current_risk if current_risk > 0 else None
        )
        if pattern.direction is Direction.UP:
            execution_stop_price = max(
                invalidation_price,
                pattern.breakout_level - self.invalidation_atr * risk_unit,
            )
        else:
            execution_stop_price = min(
                invalidation_price,
                pattern.breakout_level + self.invalidation_atr * risk_unit,
            )
        execution_risk = direction_sign * (current_close - execution_stop_price)
        execution_risk_reward_ratio = (
            max(0.0, remaining_reward) / execution_risk if execution_risk > 0 else None
        )
        three_day_base = float(bars[max(0, len(bars) - 4)].close)
        three_day_momentum_atr = (
            direction_sign * (current_close - three_day_base) / current_atr
            if current_atr is not None and current_atr > 0
            else None
        )
        sma5 = fmean(float(bar.close) for bar in bars[-5:])
        distance_from_sma5_atr = (
            direction_sign * (current_close - sma5) / current_atr
            if current_atr is not None and current_atr > 0
            else None
        )

        extension_reasons: list[str] = []
        if (
            breakout_distance_atr is not None
            and breakout_distance_atr > self.maximum_entry_distance_atr
        ):
            extension_reasons.append(f"転換水準から{breakout_distance_atr:.2f} ATR離れています")
        if target_progress >= self.maximum_target_progress:
            extension_reasons.append(f"想定値幅の{target_progress * 100:.0f}%を消化しています")
        if (
            three_day_momentum_atr is not None
            and three_day_momentum_atr > self.maximum_three_day_momentum_atr
        ):
            extension_reasons.append(f"3営業日で{three_day_momentum_atr:.2f} ATR進んでいます")
        if (
            distance_from_sma5_atr is not None
            and distance_from_sma5_atr > self.maximum_distance_from_sma5_atr
        ):
            extension_reasons.append(f"5日線から{distance_from_sma5_atr:.2f} ATR離れています")
        if (
            execution_risk_reward_ratio is not None
            and execution_risk_reward_ratio < self.minimum_execution_risk_reward_ratio
        ):
            extension_reasons.append(
                f"実行用リワード／リスクが{execution_risk_reward_ratio:.2f}です"
            )
        if (
            extension_reasons
            and remaining_risk_reward_ratio is not None
            and remaining_risk_reward_ratio < self.minimum_remaining_risk_reward_ratio
        ):
            extension_reasons.append(f"残存リワード／リスクが{remaining_risk_reward_ratio:.2f}です")

        terminal = None
        # 確定足の翌日以降を順に追う。形成完了前の日中高安は到達とみなさない。
        for bar in bars[breakout_index + 1 :]:
            failed = direction_sign * (float(bar.close) - invalidation_price) < 0
            reached = (
                float(bar.high) >= target_price
                if direction_sign == 1
                else float(bar.low) <= target_price
            )
            if failed or reached:
                terminal = "failed" if failed else "target"
                break
        if terminal == "failed" or (terminal is None and signed_invalidation_distance < 0):
            status = PatternLifecycleStatus.FAILED
            guidance = PatternGuidance.EXIT_REVIEW
            summary = "検出後の終値が無効化水準を越えて逆行しました。失敗扱いを維持します"
        elif terminal == "target" or signed_target_distance >= 0:
            status = PatternLifecycleStatus.TARGET_REACHED
            guidance = PatternGuidance.TAKE_PROFIT_REVIEW
            summary = "価格が形状の値幅から計算した目標水準へ到達しました"
        elif elapsed > self.maximum_monitoring_days:
            status = PatternLifecycleStatus.EXPIRED
            guidance = PatternGuidance.IGNORE_OLD_SIGNAL
            summary = "監視上限を過ぎたため、このパターンを新規判断へ使いません"
        elif recent_momentum_atr is not None and recent_momentum_atr <= -self.weakening_atr:
            status = PatternLifecycleStatus.WEAKENING
            guidance = PatternGuidance.EXIT_REVIEW
            summary = "直近の値動きがパターン方向へ逆行しており、勢いが弱まっています"
        elif extension_reasons:
            status = PatternLifecycleStatus.OVEREXTENDED
            guidance = PatternGuidance.HOLD_AND_MONITOR
            movement = "上抜け" if pattern.direction is Direction.UP else "下抜け"
            summary = (
                f"{movement}は有効ですが、"
                + "、".join(extension_reasons)
                + "。新規追随より押し目または戻りを待ちます"
            )
        elif elapsed <= self.entry_window_days:
            status = PatternLifecycleStatus.ENTRY_WINDOW
            guidance = PatternGuidance.CONSIDER_ENTRY
            summary = "新規購入を検討できる初期確認期間内です"
        else:
            status = PatternLifecycleStatus.MONITORING
            guidance = PatternGuidance.HOLD_AND_MONITOR
            summary = "新規追随より、目標・無効化水準までの推移を監視する期間です"

        return PatternLifecycleAssessment(
            pattern_type=pattern.pattern_type,
            detected_at=pattern.detected_at,
            status=status,
            guidance=guidance,
            trading_days_since_breakout=elapsed,
            entry_window_days=self.entry_window_days,
            maximum_monitoring_days=self.maximum_monitoring_days,
            entry_days_remaining=(
                0
                if status is not PatternLifecycleStatus.ENTRY_WINDOW
                else max(0, self.entry_window_days - elapsed)
            ),
            current_close=round(current_close, 4),
            breakout_close=round(breakout_close, 4),
            target_price=round(target_price, 4),
            invalidation_price=round(invalidation_price, 4),
            breakout_distance_atr=(
                round(breakout_distance_atr, 2) if breakout_distance_atr is not None else None
            ),
            target_progress_percent=round(target_progress * 100, 1),
            remaining_risk_reward_ratio=(
                round(remaining_risk_reward_ratio, 2)
                if remaining_risk_reward_ratio is not None
                else None
            ),
            post_breakout_return_percent=round((current_close / breakout_close - 1) * 100, 2),
            recent_momentum_atr=(
                round(recent_momentum_atr, 2) if recent_momentum_atr is not None else None
            ),
            summary=summary,
            execution_stop_price=round(execution_stop_price, 4),
            execution_risk_reward_ratio=(
                round(execution_risk_reward_ratio, 2)
                if execution_risk_reward_ratio is not None
                else None
            ),
            three_day_momentum_atr=(
                round(three_day_momentum_atr, 2) if three_day_momentum_atr is not None else None
            ),
            distance_from_sma5_atr=(
                round(distance_from_sma5_atr, 2) if distance_from_sma5_atr is not None else None
            ),
        )
