"""終了した下降パターンと新しい押し目を独立して評価する回帰検証。"""

from dataclasses import replace
from datetime import date, timedelta

import pytest
from test_analysis import make_rectangle_breakout
from test_swing_pullback import analyze, pullback_bars

from stock_signal.analysis.decision import LongOnlyDecisionPolicy
from stock_signal.analysis.engine import RuleBasedAnalysisEngine
from stock_signal.domain.analysis import (
    CheckStatus,
    Direction,
    InvestmentAction,
    PatternLifecycleStatus,
    PatternType,
    PositionEntryCondition,
)


@pytest.fixture
def inputs():
    result = analyze(pullback_bars())
    bearish = RuleBasedAnalysisEngine().analyze(
        "TEST", make_rectangle_breakout(direction=Direction.DOWN), 5,
    )
    lead = max(bearish.patterns, key=lambda p: p.fit_score)
    life = next(p for p in bearish.pattern_lifecycles if p.pattern_type == lead.pattern_type)
    return result, lead, life


def decide(inputs, horizon, status, *, checks=None, assessment=None, life_date=None):
    result, lead, life = inputs
    lifecycle = replace(life, status=status)
    if life_date is not None:
        lifecycle = replace(lifecycle, detected_at=life_date)
    assessment = assessment or result.swing_pullback
    return LongOnlyDecisionPolicy().decide(
        result.direction, result.scores, (lead,), (lifecycle,),
        result.transition_readiness, assessment if horizon == 20 else None,
        checks or result.equity_checks, horizon,
        swing_pullback=assessment if horizon == 5 else None,
    )


@pytest.mark.parametrize("horizon", [5, 20])
@pytest.mark.parametrize("status", list(PatternLifecycleStatus))
def test_only_inactive_bearish_allows_new_pullback(inputs, horizon, status):
    decision = decide(inputs, horizon, status)
    inactive = status in {
        PatternLifecycleStatus.FAILED, PatternLifecycleStatus.WEAKENING,
        PatternLifecycleStatus.TARGET_REACHED, PatternLifecycleStatus.EXPIRED,
    }
    assert decision.action is (
        InvestmentAction.BUY_CANDIDATE if inactive else InvestmentAction.AVOID_NEW_BUY
    )
    if inactive:
        expected = inputs[0].swing_pullback
        assert decision.execution_stop_price == expected.invalidation_price
        assert decision.expected_target_price == expected.target_price
        assert decision.execution_risk_reward_ratio == expected.risk_reward_ratio
        assert decision.entry_strategy == (
            "swing_pullback" if horizon == 5 else "position_pullback"
        )
        assert any("終了・弱化" in caution for caution in decision.cautions)


@pytest.mark.parametrize("horizon", [5, 20])
@pytest.mark.parametrize("key,value", [
    ("market_trend_score", -11), ("days_to_earnings", 3),
    ("relative_strength", -6), ("relative_strength_short", -4),
    ("execution_safety", 1), ("data_freshness", 10),
])
def test_new_pullback_keeps_risk_checks(inputs, horizon, key, value):
    checks = tuple(
        replace(c, value=value, status=(
            CheckStatus.UNAVAILABLE if key == "data_freshness" else c.status
        )) if c.key == key else c for c in inputs[0].equity_checks
    )
    assert decide(
        inputs, horizon, PatternLifecycleStatus.EXPIRED, checks=checks,
    ).action is InvestmentAction.WATCH


@pytest.mark.parametrize("horizon", [5, 20])
@pytest.mark.parametrize("ratio", [None, 1.49])
def test_new_pullback_keeps_reward_risk_requirement(inputs, horizon, ratio):
    assessment = replace(inputs[0].swing_pullback, risk_reward_ratio=ratio)
    assert decide(
        inputs, horizon, PatternLifecycleStatus.FAILED, assessment=assessment,
    ).action is InvestmentAction.WATCH


def test_inactive_bearish_does_not_hide_current_top_watch(inputs):
    assessment = replace(
        inputs[0].swing_pullback,
        conditions=(PositionEntryCondition(
            "head_and_shoulders_watch", "三尊候補", False, "三尊候補を確認中です",
        ),),
    )
    decision = decide(inputs, 20, PatternLifecycleStatus.EXPIRED, assessment=assessment)
    assert decision.action is InvestmentAction.WATCH
    assert "三尊候補" in decision.summary


def test_lifecycle_must_match_detection_date(inputs):
    assert decide(
        inputs, 5, PatternLifecycleStatus.EXPIRED,
        life_date=(date.fromisoformat(inputs[2].detected_at) - timedelta(days=1)).isoformat(),
    ).action is InvestmentAction.AVOID_NEW_BUY


def test_filter_does_not_revive_older_patterns_or_ignore_active_peer(inputs):
    _, lead, life = inputs
    old_date = (date.fromisoformat(lead.detected_at) - timedelta(days=1)).isoformat()
    old = replace(lead, detected_at=old_date)
    expired = replace(life, status=PatternLifecycleStatus.EXPIRED)
    assert LongOnlyDecisionPolicy._current_patterns((old, lead), (expired,)) == []
    assert LongOnlyDecisionPolicy._current_patterns((lead,), ()) == [lead]
    bullish = replace(lead, direction=Direction.UP)
    assert LongOnlyDecisionPolicy._current_patterns((bullish,), (expired,)) == [bullish]

    active_peer = replace(lead, pattern_type=PatternType.DOUBLE_TOP)
    assert LongOnlyDecisionPolicy._current_patterns(
        (lead, active_peer), (expired,),
    ) == [active_peer]


def test_bearish_peer_does_not_suppress_current_bullish_confirmation():
    bars = make_rectangle_breakout(direction=Direction.UP)
    result = RuleBasedAnalysisEngine().analyze("TEST", bars, 5)
    bullish = max(result.patterns, key=lambda p: p.fit_score)
    bearish = replace(
        bullish, direction=Direction.DOWN, fit_score=100,
        pattern_type=PatternType.DOUBLE_TOP,
    )
    bars[-1] = replace(bars[-1], close=bars[-2].close)
    checks = LongOnlyDecisionPolicy().equity_checks(bars, (bullish, bearish))
    assert next(c for c in checks if c.key == "swing_confirmation").value == 1
