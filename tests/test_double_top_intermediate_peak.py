import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from stock_signal.analysis.decision import LongOnlyDecisionPolicy
from stock_signal.analysis.engine import RuleBasedAnalysisEngine
from stock_signal.analysis.patterns import TechnicalPatternDetector
from stock_signal.domain.analysis import InvestmentAction, PatternType
from stock_signal.domain.market_data import DailyBar


def jsp_bars():
    """途中に小さな戻り高値を挟むJSPの調整済み保存日足。"""
    records = json.loads((Path(__file__).parent / "fixtures/jsp_20260909.json").read_text())
    return [
        DailyBar(
            symbol="7942",
            provider="jquants",
            trade_date=date.fromisoformat(r["date"]),
            open=Decimal(r["open"]),
            high=Decimal(r["high"]),
            low=Decimal(r["low"]),
            close=Decimal(r["close"]),
            volume=r["volume"],
            is_adjusted=True,
        )
        for r in records
    ]


def test_jsp_double_top_with_intermediate_peak():
    bars = jsp_bars()
    patterns = TechnicalPatternDetector().detect(bars)
    top = next(p for p in patterns if p.pattern_type is PatternType.DOUBLE_TOP)
    assert top.formation_start == "2026-08-18"
    assert top.formation_end == "2026-09-01"
    assert top.detected_at == "2026-09-04"
    assert top.breakout_level == 3025
    for horizon in (5, 20):
        result = RuleBasedAnalysisEngine(
            decision_policy=LongOnlyDecisionPolicy(today=lambda: date(2026, 9, 9))
        ).analyze("7942", bars, horizon)
        assert result.investment_decision.action is InvestmentAction.WATCH
        assert "目標水準へ到達" in result.investment_decision.summary
        breakout = RuleBasedAnalysisEngine(
            decision_policy=LongOnlyDecisionPolicy(today=lambda: date(2026, 9, 4))
        ).analyze("7942", [b for b in bars if b.trade_date <= date(2026, 9, 4)], horizon)
        assert breakout.investment_decision.action is InvestmentAction.AVOID_NEW_BUY


def test_jsp_before_neckline_break_is_not_completed():
    bars = [b for b in jsp_bars() if b.trade_date <= date(2026, 9, 3)]
    assert not any(
        p.pattern_type is PatternType.DOUBLE_TOP and p.formation_start == "2026-08-18"
        for p in TechnicalPatternDetector().detect(bars)
    )
