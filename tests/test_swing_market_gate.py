"""スイングの市場条件が候補を保留する境界と未来参照を検証する。"""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from test_analysis import make_rectangle_breakout

from stock_signal.analysis.engine import RuleBasedAnalysisEngine
from stock_signal.domain.analysis import AnalysisContext, Direction, InvestmentAction


@pytest.mark.parametrize(
    "market_return,expected",
    [
        (0, InvestmentAction.BUY_CANDIDATE),
        (-1.9, InvestmentAction.BUY_CANDIDATE),
        (-2.0, InvestmentAction.BUY_CANDIDATE),
        (-2.1, InvestmentAction.WATCH),
        (-3.01, InvestmentAction.WATCH),
    ],
)
def test_market_gate_can_hold_otherwise_valid_swing(market_return, expected):
    bars = make_rectangle_breakout(direction=Direction.UP)
    market = [replace(b, symbol="TOPIX", close=Decimal("100")) for b in bars]
    market[-1] = replace(market[-1], close=Decimal("100") + Decimal(str(market_return)))
    result = RuleBasedAnalysisEngine().analyze(
        "TEST", bars, 5, AnalysisContext(market_bars=tuple(market), jquants_plan="light")
    )
    check = next(c for c in result.equity_checks if c.key == "market_trend_score")
    assert check.value == pytest.approx(round(market_return * 5, 1))
    assert result.investment_decision.action == expected
    if expected == InvestmentAction.WATCH:
        assert "市場全体が下向き" in result.investment_decision.summary


def test_future_topix_does_not_change_swing_decision():
    bars = make_rectangle_breakout(direction=Direction.UP)
    market = [replace(b, symbol="TOPIX", close=Decimal("100")) for b in bars]
    market.append(
        replace(market[-1], trade_date=bars[-1].trade_date + timedelta(days=1), close=Decimal("50"))
    )
    result = RuleBasedAnalysisEngine().analyze(
        "TEST", bars, 5, AnalysisContext(market_bars=tuple(market), jquants_plan="light")
    )
    assert result.investment_decision.action == InvestmentAction.BUY_CANDIDATE
