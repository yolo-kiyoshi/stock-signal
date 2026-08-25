from __future__ import annotations

from collections.abc import Sequence
from math import sqrt
from statistics import fmean, pstdev

from stock_signal.analysis.horizons import get_horizon_profile
from stock_signal.analysis.indicators import (
    simple_moving_average_series,
    wilder_atr,
    wilder_rsi_series,
)
from stock_signal.domain.analysis import AnalysisFactor, Direction
from stock_signal.domain.market_data import DailyBar


def _closes(bars: Sequence[DailyBar]) -> list[float]:
    return [float(bar.close) for bar in bars]


class MovingAverageRule:
    """短期・中期移動平均と中期線の傾きを評価する。"""

    rule_id = "moving_average_trend"

    def evaluate(self, bars: Sequence[DailyBar], horizon_days: int) -> AnalysisFactor | None:
        profile = get_horizon_profile(horizon_days)
        required = profile.moving_long_window + 5
        if len(bars) < required:
            return None
        short_window = profile.moving_short_window
        long_window = profile.moving_long_window
        short = simple_moving_average_series(bars, short_window)[-1]
        long_series = simple_moving_average_series(bars, long_window)
        long = long_series[-1]
        previous_long = long_series[-6]
        if short is None or long is None or previous_long is None:
            return None
        spread = (short / long - 1) * 100
        slope = (long / previous_long - 1) * 100
        if spread > 0.5 and slope > 0:
            return AnalysisFactor(
                self.rule_id,
                "移動平均トレンド",
                Direction.UP,
                24,
                f"{short_window}日線が{long_window}日線を{spread:.2f}%上回り、"
                f"{long_window}日線も上向きです",
            )
        if spread < -0.5 and slope < 0:
            return AnalysisFactor(
                self.rule_id,
                "移動平均トレンド",
                Direction.DOWN,
                24,
                f"{short_window}日線が{long_window}日線を{abs(spread):.2f}%下回り、"
                f"{long_window}日線も下向きです",
            )
        return AnalysisFactor(
            self.rule_id,
            "移動平均トレンド",
            Direction.FLAT,
            14,
            f"{short_window}日線と{long_window}日線の乖離が{spread:.2f}%で、"
            "方向が揃っていません",
        )


class MomentumRule:
    """対象期間に対応した騰落率を評価する。"""

    rule_id = "price_momentum"

    def evaluate(self, bars: Sequence[DailyBar], horizon_days: int) -> AnalysisFactor | None:
        profile = get_horizon_profile(horizon_days)
        window = profile.momentum_window
        if len(bars) <= window:
            return None
        closes = _closes(bars)
        change = (closes[-1] / closes[-window - 1] - 1) * 100
        threshold = profile.momentum_threshold_percent
        if change >= threshold:
            direction, wording = Direction.UP, "上昇"
        elif change <= -threshold:
            direction, wording = Direction.DOWN, "下落"
        else:
            direction, wording = Direction.FLAT, "小幅な変動"
        score = 22 if direction is not Direction.FLAT else 16
        return AnalysisFactor(self.rule_id, "価格モメンタム", direction, score,
                              f"直近{window}営業日の騰落率は{change:+.2f}%で、{wording}です")


class RecentTrendRule:
    """運用スタイルに合う直近窓の値動きをATRと移動平均で評価する。"""

    rule_id = "recent_price_trend"

    def evaluate(self, bars: Sequence[DailyBar], horizon_days: int) -> AnalysisFactor | None:
        profile = get_horizon_profile(horizon_days)
        if len(bars) < 21:
            return None
        closes = _closes(bars)
        atr = wilder_atr(bars)
        if atr is None or atr <= 0:
            return None
        recent_window = profile.recent_window
        average_window = profile.recent_average_window
        threshold = profile.recent_atr_threshold
        change_atr = (closes[-1] - closes[-recent_window - 1]) / atr
        short_average = fmean(closes[-average_window:])
        if change_atr >= threshold and closes[-1] > short_average:
            return AnalysisFactor(
                self.rule_id,
                "直近トレンド",
                Direction.UP,
                profile.recent_score,
                f"直近{recent_window}営業日で{change_atr:+.2f} ATR上昇し、"
                f"終値は{average_window}日平均を上回っています",
            )
        if change_atr <= -threshold and closes[-1] < short_average:
            return AnalysisFactor(
                self.rule_id,
                "直近トレンド",
                Direction.DOWN,
                profile.recent_score,
                f"直近{recent_window}営業日で{change_atr:+.2f} ATR下落し、"
                f"終値は{average_window}日平均を下回っています",
            )
        return AnalysisFactor(
            self.rule_id,
            "直近トレンド",
            Direction.FLAT,
            14,
            f"直近{recent_window}営業日の変動は{change_atr:+.2f} ATRで、"
            "明確な方向を確認できません",
        )


class RsiRule:
    """運用スタイルに対応したRSIで勢いと過熱感を評価する。"""

    rule_id = "relative_strength_index"

    def evaluate(self, bars: Sequence[DailyBar], horizon_days: int) -> AnalysisFactor | None:
        profile = get_horizon_profile(horizon_days)
        window = profile.rsi_window
        if len(bars) < window + 1:
            return None
        rsi = wilder_rsi_series(bars, window)[-1]
        if rsi is None:
            return None
        if 55 <= rsi <= 70:
            return AnalysisFactor(self.rule_id, f"RSI（{window}日）", Direction.UP, 15,
                                  f"RSIは{rsi:.1f}で、上向きの勢いがあります")
        if 30 <= rsi <= 45:
            return AnalysisFactor(self.rule_id, f"RSI（{window}日）", Direction.DOWN, 15,
                                  f"RSIは{rsi:.1f}で、下向きの勢いがあります")
        if rsi > 70:
            return AnalysisFactor(self.rule_id, f"RSI（{window}日）", Direction.FLAT, 10,
                                  f"RSIは{rsi:.1f}です。買われ過ぎは上昇ではなく警戒情報として扱います")
        if rsi < 30:
            return AnalysisFactor(self.rule_id, f"RSI（{window}日）", Direction.FLAT, 10,
                                  f"RSIは{rsi:.1f}です。売られ過ぎは反発予測ではなく警戒情報として扱います")
        return AnalysisFactor(self.rule_id, f"RSI（{window}日）", Direction.FLAT, 12,
                              f"RSIは{rsi:.1f}で、中立圏にあります")


class VolatilityRule:
    """運用スタイルに対応した実現ボラティリティと帯幅を評価する。"""

    rule_id = "volatility_range"

    def evaluate(self, bars: Sequence[DailyBar], horizon_days: int) -> AnalysisFactor | None:
        profile = get_horizon_profile(horizon_days)
        window = profile.moving_long_window
        if len(bars) < window + 1:
            return None
        closes = _closes(bars)[-window:]
        returns = [
            current / previous - 1
            for previous, current in zip(closes, closes[1:], strict=False)
        ]
        annualized = pstdev(returns) * sqrt(252) * 100
        average = fmean(closes)
        band_width = (4 * pstdev(closes) / average) * 100
        if annualized < 12 or band_width < 5:
            return AnalysisFactor(
                self.rule_id,
                "値幅の収縮",
                Direction.FLAT,
                18,
                f"{window}日年率変動率は{annualized:.1f}%、"
                f"帯幅は{band_width:.1f}%で値動きが収縮しています",
            )
        return None


class VolumeConfirmationRule:
    """直近の値動きを出来高が裏付けているか評価する。"""

    rule_id = "volume_confirmation"

    def evaluate(self, bars: Sequence[DailyBar], horizon_days: int) -> AnalysisFactor | None:
        profile = get_horizon_profile(horizon_days)
        recent_window = profile.volume_window
        baseline_window = profile.volume_baseline_window
        if len(bars) < baseline_window + recent_window:
            return None
        recent_volume = fmean(bar.volume for bar in bars[-recent_window:])
        baseline_volume = fmean(
            bar.volume for bar in bars[-baseline_window - recent_window:-recent_window]
        )
        if baseline_volume <= 0 or recent_volume / baseline_volume < 1.2:
            return None
        change = float(bars[-1].close / bars[-recent_window - 1].close - 1) * 100
        if abs(change) < 0.5:
            return None
        direction = Direction.UP if change > 0 else Direction.DOWN
        volume_ratio = recent_volume / baseline_volume
        return AnalysisFactor(
            self.rule_id,
            "出来高による確認",
            direction,
            12,
            f"直近{recent_window}日の出来高は平常時の{volume_ratio:.2f}倍で、"
            f"価格の{change:+.2f}%変動を伴っています",
        )


DEFAULT_RULES = (
    MovingAverageRule(),
    MomentumRule(),
    RecentTrendRule(),
    RsiRule(),
    VolatilityRule(),
    VolumeConfirmationRule(),
)
