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
    if expected == InvestmentAction.BUY_CANDIDATE:
        assert result.market_gate_diagnostic is None
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


def falling_market(bars):
    """評価日にだけ下落したTOPIX入力を作る。"""
    market = [replace(bar, symbol="TOPIX", open=Decimal("100"), high=Decimal("101"),
                      low=Decimal("99"), close=Decimal("100")) for bar in bars]
    market[-1] = replace(market[-1], low=Decimal("96"), close=Decimal("97"))
    return AnalysisContext(market_bars=tuple(market), jquants_plan="light")


def test_market_only_diagnostic_preserves_real_decision_and_market_score():
    from stock_signal.web.app import _serialize_analysis

    bars = make_rectangle_breakout(direction=Direction.UP)
    result = RuleBasedAnalysisEngine().analyze("TEST", bars, 5, falling_market(bars))
    assert result.investment_decision.action == InvestmentAction.WATCH
    assert "市場全体が下向き" in result.investment_decision.summary
    diagnostic = result.market_gate_diagnostic
    assert diagnostic.status == "market_only"
    assert diagnostic.market_trend_score == -15
    assert next(c.value for c in result.equity_checks if c.key == "market_trend_score") == -15
    payload = _serialize_analysis(result)
    assert payload["market_gate_diagnostic"]["status"] == "market_only"
    assert payload["investment_decision"]["action"] == "watch"


@pytest.mark.parametrize("volume", [0, 700])
def test_market_diagnostic_does_not_hide_volume_failure(volume):
    bars = make_rectangle_breakout(direction=Direction.UP, volume=volume)
    result = RuleBasedAnalysisEngine().analyze("TEST", bars, 5, falling_market(bars))
    assert result.investment_decision.action == InvestmentAction.WATCH
    assert result.market_gate_diagnostic.status == "other_conditions_pending"
    assert "出来高" in result.market_gate_diagnostic.other_conditions_summary


def test_market_diagnostic_checks_upper_resistance(monkeypatch):
    from test_resistance_gate import band, install_bands

    bars = make_rectangle_breakout(direction=Direction.UP)
    install_bands(monkeypatch, [band(104, 105)])
    result = RuleBasedAnalysisEngine().analyze("TEST", bars, 5, falling_market(bars))
    assert result.market_gate_diagnostic.status == "other_conditions_pending"
    assert "抵抗帯" in result.market_gate_diagnostic.other_conditions_summary
    assert "市場全体が下向き" in result.investment_decision.summary


def test_market_diagnostic_checks_earnings_and_current_candle():
    bars = make_rectangle_breakout(direction=Direction.UP)
    context = replace(falling_market(bars), next_earnings_date=bars[-1].trade_date,
                      earnings_synced=True)
    result = RuleBasedAnalysisEngine().analyze("TEST", bars, 5, context)
    assert result.market_gate_diagnostic.status == "other_conditions_pending"
    assert "決算" in result.market_gate_diagnostic.other_conditions_summary

    bars[-1] = replace(bars[-1], open=Decimal("104"), high=Decimal("107"))
    result = RuleBasedAnalysisEngine().analyze("TEST", bars, 5, falling_market(bars))
    assert result.market_gate_diagnostic.status == "other_conditions_pending"
    assert "買い支え" in result.market_gate_diagnostic.other_conditions_summary


def test_no_market_diagnostic_for_missing_market_or_insufficient_bars():
    bars = make_rectangle_breakout(direction=Direction.UP)
    assert RuleBasedAnalysisEngine().analyze("TEST", bars, 5).market_gate_diagnostic is None
    assert RuleBasedAnalysisEngine().analyze(
        "TEST", bars[:10], 5, falling_market(bars),
    ).market_gate_diagnostic is None


def test_market_diagnostic_evaluates_independent_swing_pullback():
    from test_swing_pullback import NoPatterns, UpRule, pullback_bars

    from stock_signal.analysis.decision import LongOnlyDecisionPolicy

    bars = pullback_bars()
    engine = RuleBasedAnalysisEngine(
        rules=[UpRule()], pattern_detector=NoPatterns(),
        decision_policy=LongOnlyDecisionPolicy(today=lambda: bars[-1].trade_date),
    )
    result = engine.analyze("TEST", bars, 5, falling_market(bars))
    assert result.investment_decision.entry_strategy == "swing_pullback"
    assert result.investment_decision.action == InvestmentAction.WATCH
    assert result.market_gate_diagnostic.status == "market_only"


def test_position_diagnostic_keeps_reward_risk_requirement():
    from datetime import date

    from test_analysis import make_bars

    prices = [100 + index * .4 for index in range(80)]
    prices += [131, 130, 129, 128, 127, 126.5, 126, 125.5, 127]
    bars = make_bars(prices, start_date=date.today() - timedelta(days=88))
    bars[-1] = replace(bars[-1], open=Decimal("125.8"), high=Decimal("128"),
                       low=Decimal("124.8"))
    context = falling_market(bars)
    market = list(context.market_bars)
    market[-1] = replace(market[-1], low=Decimal("89"), close=Decimal("90"))
    result = RuleBasedAnalysisEngine().analyze(
        "TEST", bars, 20, replace(context, market_bars=tuple(market)),
    )
    assert result.investment_decision.action == InvestmentAction.WATCH
    assert result.market_gate_diagnostic.status == "other_conditions_pending"
    assert "上値余地" in result.market_gate_diagnostic.other_conditions_summary
