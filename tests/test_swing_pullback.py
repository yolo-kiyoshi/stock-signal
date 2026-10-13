from dataclasses import replace
from decimal import Decimal

import pytest
from test_analysis import make_bars, make_custom_bars, make_rectangle_breakout

from stock_signal.analysis.decision import LongOnlyDecisionPolicy
from stock_signal.analysis.engine import RuleBasedAnalysisEngine
from stock_signal.analysis.entry_diagnostics import calculate_entry_diagnostics, nearest_resistance
from stock_signal.analysis.swing_pullback import SwingPullbackEvaluator
from stock_signal.domain.analysis import (
    AnalysisFactor,
    Direction,
    InvestmentAction,
    PatternLifecycleStatus,
    PositionEntryPhase,
)


def pullback_bars():
    """上昇後に20日線へ戻り、高値まで余地を残して反発する日足。"""
    rows = [(100 + i * .4, 101 + i * .4, 99 + i * .4, 100 + i * .4, 1000)
            for i in range(40)]
    rows += [(113, 113.5, 111, 112, 1000), (112, 112.3, 110.8, 111.7, 900),
             (111.8, 112.6, 111, 112.4, 1000)]
    return make_custom_bars(rows)


class NoPatterns:
    def detect(self, bars):
        return ()


class UpRule:
    def evaluate(self, bars, horizon_days):
        return AnalysisFactor("test_up", "上昇テスト", Direction.UP, 100, "上昇方向を固定")


def analyze(bars):
    return RuleBasedAnalysisEngine(
        rules=[UpRule()], pattern_detector=NoPatterns(),
        decision_policy=LongOnlyDecisionPolicy(today=lambda: bars[-1].trade_date),
    ).analyze("TEST", bars, 5)


def test_diagnostics_financial_calculations_and_missing_values():
    bars = make_bars([100] * 30)
    d = calculate_entry_diagnostics(bars, 98)
    assert d.current_price == 100
    assert d.atr == 2
    assert d.sma20_distance_atr == 0
    assert d.return_10d_percent == 0
    assert d.return_20d_percent == 0
    assert d.stop_width_percent == 2
    assert d.stop_width_atr == 1
    assert d.resistance_room_percent == 1
    assert d.resistance_risk_reward_ratio == .5
    for stop in (None, 0, -1, 100, 101):
        assert calculate_entry_diagnostics(bars, stop).stop_width_atr is None
    short = calculate_entry_diagnostics(bars[:10], None)
    assert short.sma20_distance_atr is None
    assert short.return_20d_percent is None
    assert short.resistance_status == "unavailable"
    assert calculate_entry_diagnostics([], None) is None
    rising = calculate_entry_diagnostics(make_bars(list(range(100, 140))), None)
    assert rising.return_20d_percent == round((139 / 119 - 1) * 100, 4)


def test_no_resistance_is_not_unlimited_upside():
    bars = make_bars([100] * 30 + [110])
    assert nearest_resistance(bars) == ("not_found", None)
    d = calculate_entry_diagnostics(bars, 108)
    assert d.resistance_room_percent is None
    assert d.resistance_risk_reward_ratio is None


def test_inside_resistance_has_zero_room(monkeypatch):
    from types import SimpleNamespace

    import stock_signal.analysis.entry_diagnostics as module

    monkeypatch.setattr(module, "resistance_bands", lambda *args, **kwargs: (
        SimpleNamespace(lower=99, upper=101),
    ))
    d = calculate_entry_diagnostics(make_bars([100] * 30), 98)
    assert d.resistance_status == "within_band"
    assert d.resistance_room_percent == 0
    assert d.resistance_risk_reward_ratio == 0


def test_pullback_can_enter_without_completed_breakout():
    bars = pullback_bars()
    result = analyze(bars)
    p = result.swing_pullback
    assert p.phase is PositionEntryPhase.PULLBACK_CANDIDATE
    assert all(c.satisfied for c in p.conditions)
    assert p.invalidation_price == pytest.approx(min(p.supports[0].lower, 110.8) - .5 * p.atr)
    assert p.risk_reward_ratio == pytest.approx(
        (p.target_price - 112.4) / (112.4 - p.invalidation_price)
    )
    assert p.risk_reward_ratio >= 1.5
    assert result.investment_decision.action is InvestmentAction.BUY_CANDIDATE
    assert result.investment_decision.entry_strategy == "swing_pullback"
    assert result.investment_decision.execution_stop_price == p.invalidation_price
    assert result.entry_diagnostics.stop_width_atr == round(
        (112.4 - p.invalidation_price) / p.atr, 4,
    )
    assert result.position_entry is None


@pytest.mark.parametrize("change", ["support_break", "falling_low", "no_rebound", "low_volume"])
def test_incomplete_pullback_does_not_enter(change):
    bars = pullback_bars()
    if change == "support_break":
        bars[-2] = replace(bars[-2], close=Decimal("110.5"), low=Decimal("110"))
    elif change == "falling_low":
        bars[-1] = replace(bars[-1], low=Decimal("110.7"))
    elif change == "no_rebound":
        bars[-1] = replace(bars[-1], open=Decimal("112.5"))
    else:
        bars[-1] = replace(bars[-1], volume=0)
    result = analyze(bars)
    assert result.swing_pullback.phase is not PositionEntryPhase.PULLBACK_CANDIDATE
    assert result.investment_decision.action is not InvestmentAction.BUY_CANDIDATE


def test_insufficient_pullback_history_is_not_fabricated():
    assert SwingPullbackEvaluator().evaluate(pullback_bars()[:29]) is None
    result = analyze(pullback_bars()[:29])
    assert result.swing_pullback is None
    assert result.investment_decision.entry_strategy is None


@pytest.mark.parametrize("key,value", [
    ("market_trend_score", -11), ("days_to_earnings", 3),
    ("relative_strength_short", -4), ("execution_safety", 1),
])
def test_pullback_does_not_bypass_shared_risk_gates(key, value):
    result = analyze(pullback_bars())
    checks = tuple(replace(c, value=value) if c.key == key else c for c in result.equity_checks)
    decision = LongOnlyDecisionPolicy().decide(
        result.direction, result.scores, (), (), result.transition_readiness, None,
        checks, 5, swing_pullback=result.swing_pullback,
    )
    assert decision.action is InvestmentAction.WATCH
    assert decision.entry_strategy == "swing_pullback"


@pytest.mark.parametrize("ratio", [None, 1.49])
def test_resistance_reward_risk_is_required(ratio):
    result = analyze(pullback_bars())
    decision = LongOnlyDecisionPolicy().decide(
        result.direction, result.scores, (), (), result.transition_readiness, None,
        result.equity_checks, 5,
        swing_pullback=replace(result.swing_pullback, risk_reward_ratio=ratio),
    )
    assert decision.action is InvestmentAction.WATCH
    assert "上値余地" in decision.summary


def test_expired_breakout_window_can_use_a_new_pullback_but_failed_cannot():
    result = analyze(pullback_bars())
    breakout = RuleBasedAnalysisEngine().analyze(
        "TEST", make_rectangle_breakout(direction=Direction.UP), 5,
    )
    lead = max(breakout.patterns, key=lambda p: p.fit_score)
    life = next(p for p in breakout.pattern_lifecycles if p.pattern_type == lead.pattern_type)
    policy = LongOnlyDecisionPolicy()
    for status, expected in [
        (PatternLifecycleStatus.MONITORING, InvestmentAction.BUY_CANDIDATE),
        (PatternLifecycleStatus.FAILED, InvestmentAction.AVOID_NEW_BUY),
    ]:
        decision = policy.decide(
            Direction.UP, result.scores, (lead,), (replace(life, status=status),),
            result.transition_readiness, None, result.equity_checks, 5,
            swing_pullback=result.swing_pullback,
        )
        assert decision.action is expected
        if expected is InvestmentAction.BUY_CANDIDATE:
            assert decision.entry_strategy == "swing_pullback"


def test_future_bars_do_not_change_past_evaluation():
    from stock_signal.analysis.historical_validation import HistoricalValidationService

    bars = pullback_bars()
    future = make_bars([200, 201, 202, 203, 204],
                       start_date=bars[-1].trade_date.replace(year=2027))
    service = HistoricalValidationService("unused")
    expected = service._validate_loaded("TEST", "test", bars[-1].trade_date, 5, bars, [])
    actual = service._validate_loaded("TEST", "test", bars[-1].trade_date, 5, bars + future, [])
    assert actual.analysis.swing_pullback == expected.analysis.swing_pullback
    assert actual.analysis.entry_diagnostics == expected.analysis.entry_diagnostics


def test_api_serializes_diagnostics_and_independent_strategy():
    import json

    from stock_signal.web.app import _serialize_analysis

    result = analyze(pullback_bars())
    payload = json.loads(json.dumps(_serialize_analysis(result)))
    assert payload["investment_decision"]["entry_strategy"] == "swing_pullback"
    assert payload["swing_pullback"]["phase"] == "pullback_candidate"
    assert payload["swing_pullback"]["score_is_probability"] is False
    assert payload["entry_diagnostics"]["stop_width_atr"] is not None
    assert payload["score_is_probability"] is False
    legacy = _serialize_analysis(replace(result, swing_pullback=None, entry_diagnostics=None))
    assert legacy["entry_diagnostics"] is None
    assert legacy["swing_pullback"] is None


def test_down_direction_and_bearish_pattern_do_not_promote_pullback():
    result = analyze(pullback_bars())
    breakout = RuleBasedAnalysisEngine().analyze(
        "TEST", make_rectangle_breakout(direction=Direction.DOWN), 5,
    )
    for direction, patterns, lifecycles in [
        (Direction.DOWN, (), ()),
        (Direction.UP, breakout.patterns, breakout.pattern_lifecycles),
    ]:
        decision = LongOnlyDecisionPolicy().decide(
            direction, result.scores, patterns, lifecycles, result.transition_readiness, None,
            result.equity_checks, 5, swing_pullback=result.swing_pullback,
        )
        assert decision.action is not InvestmentAction.BUY_CANDIDATE
        assert decision.entry_strategy != "swing_pullback"


def test_breakout_candidate_keeps_its_existing_strategy():
    result = analyze(pullback_bars())
    breakout = RuleBasedAnalysisEngine().analyze(
        "TEST", make_rectangle_breakout(direction=Direction.UP), 5,
    )
    decision = LongOnlyDecisionPolicy().decide(
        breakout.direction, breakout.scores, breakout.patterns, breakout.pattern_lifecycles,
        breakout.transition_readiness, None, breakout.equity_checks, 5,
        swing_pullback=result.swing_pullback,
    )
    assert decision.action is InvestmentAction.BUY_CANDIDATE
    assert decision.entry_strategy == "breakout"
