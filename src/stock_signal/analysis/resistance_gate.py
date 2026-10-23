from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from stock_signal.analysis.indicators import resistance_bands
from stock_signal.domain.analysis import (
    CheckStatus,
    EntryStage,
    EquityCheck,
    InvestmentAction,
    InvestmentDecision,
)
from stock_signal.domain.market_data import DailyBar


def apply_breakout_resistance_gate(
    decision: InvestmentDecision,
    bars: Sequence[DailyBar],
    horizon_days: int,
) -> tuple[InvestmentDecision, tuple[EquityCheck, ...]]:
    """上抜け検出を維持し、形状目標までの未突破抵抗帯を購入条件と分離する。"""
    if decision.entry_strategy != "breakout" or not bars:
        return decision, ()
    target = decision.expected_target_price
    current = float(bars[-1].close)
    if target is None or target <= current:
        return decision, ()
    lookback = 60 if horizon_days == 5 else 120
    bands = resistance_bands(bars, lookback=lookback)
    blocking = [band for band in bands if band.upper >= current and band.lower < target]
    if not blocking:
        return decision, ()
    band = min(blocking, key=lambda item: item.lower)
    reason = (
        f"上抜け検出済みですが、形状目標までに未突破の上位抵抗帯"
        f"{band.lower:,.2f}〜{band.upper:,.2f}円（{lookback}営業日参照、"
        f"{band.touches}回接触）があります。"
        "終値で帯の上端を超えた後、ほかの購入条件も再評価します"
    )
    check = EquityCheck(
        key="upper_resistance_confirmation",
        label="上位抵抗帯の突破確認",
        status=CheckStatus.PARTIAL,
        value=band.upper,
        unit="円",
        description=reason,
    )
    if decision.action == InvestmentAction.BUY_CANDIDATE:
        decision = replace(
            decision,
            action=InvestmentAction.WATCH,
            entry_stage=EntryStage.CONDITIONAL_ENTRY,
            summary="上抜け検出済み・購入条件は上位抵抗帯の突破確認待ちです",
            reasons=decision.reasons + (reason,),
            cautions=decision.cautions + (reason,),
        )
    else:
        # 下落・失敗・ほかの保留理由を上書きしない。
        decision = replace(decision, cautions=decision.cautions + (reason,))
    return decision, (check,)
