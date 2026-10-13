from __future__ import annotations

from collections.abc import Sequence
from itertools import combinations

from stock_signal.analysis.patterns import _local_extrema
from stock_signal.domain.analysis import PositionEntryCondition
from stock_signal.domain.market_data import DailyBar


def head_and_shoulders_watch(bars: Sequence[DailyBar]) -> PositionEntryCondition | None:
    """直近の未完成三尊候補と、固定した二つの谷による監視条件を返す。"""
    window = tuple(sorted(bars, key=lambda bar: bar.trade_date))[-100:]
    if len(window) < 30:
        return None
    highs = [float(bar.high) for bar in window]
    lows = [float(bar.low) for bar in window]
    peaks = _local_extrema(highs, maximum=True)[-16:]
    candidates = []
    for left, head, right in combinations(peaks, 3):
        if not (head - left >= 5 and right - head >= 5 and right - left <= 60):
            continue
        if len(window) - 1 - right > 20:
            continue
        if not 0.45 <= (head - left) / (right - head) <= 2.2:
            continue
        similarity = abs(highs[left] / highs[right] - 1)
        if similarity > 0.03 or highs[head] < max(highs[left], highs[right]) * 1.025:
            continue
        if highs[head] != max(highs[left : right + 1]):
            continue
        if left < 5 or highs[left] <= min(lows[max(0, left - 20) : left]) * 1.03:
            continue
        neck_left = min(range(left + 1, head), key=lows.__getitem__)
        neck_right = min(range(head + 1, right), key=lows.__getitem__)
        slope = (lows[neck_right] - lows[neck_left]) / (neck_right - neck_left)

        # 完成済みや一度否定された候補を、未完成の待機理由に再利用しない。
        if any(
            float(window[index].close) < lows[neck_left] + slope * (index - neck_left)
            or highs[index] > highs[right]
            for index in range(right + 1, len(window))
        ):
            continue
        level = lows[neck_left] + slope * (len(window) - 1 - neck_left)
        if level <= 0:
            continue
        description = (
            f"三尊候補：左肩{window[left].trade_date}（{highs[left]:.2f}）、"
            f"頭{window[head].trade_date}（{highs[head]:.2f}）、"
            f"右肩{window[right].trade_date}（{highs[right]:.2f}）。"
            f"谷は{window[neck_left].trade_date}（{lows[neck_left]:.2f}）と"
            f"{window[neck_right].trade_date}（{lows[neck_right]:.2f}）に固定。"
            f"{window[-1].trade_date}時点のネックラインは{level:.2f}"
            f"（1営業日あたり{slope:+.2f}）です。"
            "待つ条件：終値での下抜けと、その後の回復有無を確認します。"
            f"直近の谷{lows[neck_right]:.2f}割れとネックライン割れは別に評価します。"
            f"右肩{highs[right]:.2f}の上抜けで仮説を弱め、"
            f"頭{highs[head]:.2f}の上抜けで撤回します。"
            "形状だけで下落や最適な購入時期は断定できません。"
            "中長期の候補評価は利益成長・配当の持続性・価格妥当性も別途確認します。"
        )
        candidates.append((right, -similarity, description))
    if not candidates:
        return None
    return PositionEntryCondition(
        "head_and_shoulders_watch", "三尊候補の解消を確認", False, max(candidates)[2]
    )
