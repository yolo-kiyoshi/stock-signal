from __future__ import annotations

from collections.abc import Sequence
from math import sqrt
from statistics import fmean, pstdev

from stock_signal.domain.analysis import AnalysisFactor, Direction
from stock_signal.domain.market_data import DailyBar


def _closes(bars: Sequence[DailyBar]) -> list[float]:
    return [float(bar.close) for bar in bars]


class MovingAverageRule:
    """短期・中期移動平均と中期線の傾きを評価する。"""

    rule_id = "moving_average_trend"

    def evaluate(self, bars: Sequence[DailyBar], horizon_days: int) -> AnalysisFactor | None:
        if len(bars) < 25:
            return None
        closes = _closes(bars)
        short_window = 5 if horizon_days <= 5 else 10
        short = fmean(closes[-short_window:])
        medium = fmean(closes[-20:])
        previous_medium = fmean(closes[-25:-5])
        spread = (short / medium - 1) * 100
        slope = (medium / previous_medium - 1) * 100
        if spread > 0.5 and slope > 0:
            return AnalysisFactor(self.rule_id, "移動平均トレンド", Direction.UP, 24,
                                  f"短期線が20日線を{spread:.2f}%上回り、20日線も上向きです")
        if spread < -0.5 and slope < 0:
            return AnalysisFactor(self.rule_id, "移動平均トレンド", Direction.DOWN, 24,
                                  f"短期線が20日線を{abs(spread):.2f}%下回り、20日線も下向きです")
        return AnalysisFactor(self.rule_id, "移動平均トレンド", Direction.FLAT, 14,
                              f"短期線と20日線の乖離が{spread:.2f}%で、明確な方向が揃っていません")


class MomentumRule:
    """対象期間に対応した騰落率を評価する。"""

    rule_id = "price_momentum"

    def evaluate(self, bars: Sequence[DailyBar], horizon_days: int) -> AnalysisFactor | None:
        window = {1: 3, 5: 10, 20: 20}[horizon_days]
        if len(bars) <= window:
            return None
        closes = _closes(bars)
        change = (closes[-1] / closes[-window - 1] - 1) * 100
        threshold = {1: 1.0, 5: 2.0, 20: 4.0}[horizon_days]
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
    """直近3営業日の値動きをATRと5日平均で評価する。"""

    rule_id = "recent_price_trend"

    def evaluate(self, bars: Sequence[DailyBar], horizon_days: int) -> AnalysisFactor | None:
        if len(bars) < 21:
            return None
        closes = _closes(bars)
        true_ranges = []
        for index in range(len(bars) - 20, len(bars)):
            previous_close = float(bars[index - 1].close)
            high = float(bars[index].high)
            low = float(bars[index].low)
            true_ranges.append(
                max(high - low, abs(high - previous_close), abs(low - previous_close))
            )
        atr = fmean(true_ranges)
        if atr <= 0:
            return None
        change_atr = (closes[-1] - closes[-4]) / atr
        short_average = fmean(closes[-5:])
        if change_atr >= 0.5 and closes[-1] > short_average:
            return AnalysisFactor(
                self.rule_id,
                "直近トレンド",
                Direction.UP,
                30,
                f"直近3営業日で{change_atr:+.2f} ATR上昇し、終値は5日平均を上回っています",
            )
        if change_atr <= -0.5 and closes[-1] < short_average:
            return AnalysisFactor(
                self.rule_id,
                "直近トレンド",
                Direction.DOWN,
                30,
                f"直近3営業日で{change_atr:+.2f} ATR下落し、終値は5日平均を下回っています",
            )
        return AnalysisFactor(
            self.rule_id,
            "直近トレンド",
            Direction.FLAT,
            14,
            f"直近3営業日の変動は{change_atr:+.2f} ATRで、明確な短期方向を確認できません",
        )


class RsiRule:
    """14日RSIで勢いと過熱感を評価する。"""

    rule_id = "rsi_14"

    def evaluate(self, bars: Sequence[DailyBar], horizon_days: int) -> AnalysisFactor | None:
        if len(bars) < 15:
            return None
        closes = _closes(bars)
        changes = [
            current - previous
            for previous, current in zip(closes[-15:-1], closes[-14:], strict=True)
        ]
        average_gain = fmean(max(change, 0) for change in changes)
        average_loss = fmean(max(-change, 0) for change in changes)
        rsi = 100.0 if average_loss == 0 else 100 - 100 / (1 + average_gain / average_loss)
        if 55 <= rsi <= 70:
            return AnalysisFactor(self.rule_id, "RSI（14日）", Direction.UP, 15,
                                  f"RSIは{rsi:.1f}で、上向きの勢いがあります")
        if 30 <= rsi <= 45:
            return AnalysisFactor(self.rule_id, "RSI（14日）", Direction.DOWN, 15,
                                  f"RSIは{rsi:.1f}で、下向きの勢いがあります")
        if rsi > 70:
            return AnalysisFactor(self.rule_id, "RSI（14日）", Direction.FLAT, 10,
                                  f"RSIは{rsi:.1f}です。買われ過ぎは上昇ではなく警戒情報として扱います")
        if rsi < 30:
            return AnalysisFactor(self.rule_id, "RSI（14日）", Direction.FLAT, 10,
                                  f"RSIは{rsi:.1f}です。売られ過ぎは反発予測ではなく警戒情報として扱います")
        return AnalysisFactor(self.rule_id, "RSI（14日）", Direction.FLAT, 12,
                              f"RSIは{rsi:.1f}で、中立圏にあります")


class VolatilityRule:
    """20日実現ボラティリティとボリンジャー帯幅を評価する。"""

    rule_id = "volatility_range"

    def evaluate(self, bars: Sequence[DailyBar], horizon_days: int) -> AnalysisFactor | None:
        if len(bars) < 21:
            return None
        closes = _closes(bars)[-20:]
        returns = [
            current / previous - 1
            for previous, current in zip(closes, closes[1:], strict=False)
        ]
        annualized = pstdev(returns) * sqrt(252) * 100
        average = fmean(closes)
        band_width = (4 * pstdev(closes) / average) * 100
        if annualized < 12 or band_width < 5:
            return AnalysisFactor(self.rule_id, "値幅の収縮", Direction.FLAT, 18,
                                  f"20日年率変動率は{annualized:.1f}%、帯幅は{band_width:.1f}%で値動きが収縮しています")
        return None


class VolumeConfirmationRule:
    """直近の値動きを出来高が裏付けているか評価する。"""

    rule_id = "volume_confirmation"

    def evaluate(self, bars: Sequence[DailyBar], horizon_days: int) -> AnalysisFactor | None:
        if len(bars) < 21:
            return None
        recent_volume = fmean(bar.volume for bar in bars[-3:])
        baseline_volume = fmean(bar.volume for bar in bars[-20:-3])
        if baseline_volume <= 0 or recent_volume / baseline_volume < 1.2:
            return None
        change = float(bars[-1].close / bars[-4].close - 1) * 100
        if abs(change) < 0.5:
            return None
        direction = Direction.UP if change > 0 else Direction.DOWN
        volume_ratio = recent_volume / baseline_volume
        return AnalysisFactor(
            self.rule_id,
            "出来高による確認",
            direction,
            12,
            f"直近3日の出来高は平常時の{volume_ratio:.2f}倍で、"
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
