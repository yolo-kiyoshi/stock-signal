from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HorizonProfile:
    """投資スタイルごとの分析期間と参照窓をまとめた設定。"""

    horizon_days: int
    key: str
    label: str
    future_label: str
    holding_period: str
    purpose: str
    caution: str
    minimum_bars: int
    moving_short_window: int
    moving_long_window: int
    rsi_window: int
    momentum_window: int
    momentum_threshold_percent: float
    recent_window: int
    recent_average_window: int
    recent_atr_threshold: float
    recent_score: float
    volume_window: int
    volume_baseline_window: int
    stability_lookback: int
    stability_days: int
    trigger_lookback: int
    market_window: int
    market_minimum_return_percent: float
    earnings_exclusion_days: int


_PROFILES = {
    1: HorizonProfile(
        1,
        "tactical_reference",
        "翌営業日の参考",
        "1営業日先",
        "売買判断の主用途にはしない",
        "既存APIとの互換性と翌日のイベントリスク確認に限定します。",
        "日中売買や翌日の騰落を保証する分析ではありません。",
        25,
        5,
        20,
        14,
        3,
        1.0,
        3,
        5,
        0.5,
        30.0,
        3,
        20,
        20,
        5,
        10,
        20,
        -2.0,
        5,
    ),
    5: HorizonProfile(
        5,
        "swing",
        "スイング",
        "5営業日先",
        "数日〜数週間",
        "転換初動やブレイク後の、新規購入タイミングを確認します。",
        "1日だけの値動きで追わず、出来高と無効化水準も併せて確認します。",
        25,
        5,
        20,
        14,
        10,
        2.0,
        3,
        5,
        0.5,
        30.0,
        3,
        20,
        20,
        5,
        10,
        20,
        -2.0,
        5,
    ),
    20: HorizonProfile(
        20,
        "position",
        "中長期の買い場",
        "20営業日先",
        "数週間〜数か月",
        "長期保有を始める前に、中期トレンドと底固めを確認します。",
        "企業価値は評価しないため、長期保有の可否は業績・財務と別に判断します。",
        70,
        20,
        60,
        28,
        60,
        8.0,
        10,
        20,
        1.0,
        26.0,
        10,
        60,
        60,
        10,
        20,
        60,
        -5.0,
        5,
    ),
}


def get_horizon_profile(horizon_days: int) -> HorizonProfile:
    """営業日数に対応する分析プロファイルを返す。"""
    try:
        return _PROFILES[horizon_days]
    except KeyError as error:
        raise ValueError(
            "分析期間は1、5、20営業日のいずれかで指定してください"
        ) from error
