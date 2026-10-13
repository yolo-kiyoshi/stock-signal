from dataclasses import replace
from decimal import Decimal

from test_analysis import make_bars

from stock_signal.analysis.position_entry import PositionEntryEvaluator
from stock_signal.analysis.top_watch import head_and_shoulders_watch
from stock_signal.domain.analysis import PositionEntryPhase


def candidate_bars():
    """右下がりの谷と同水準の肩を持つ合成日足。"""
    anchors = [
        (0, 2400),
        (30, 2800),
        (40, 3041),
        (50, 2886.5),
        (55, 3130),
        (60, 2836.5),
        (75, 3036),
        (80, 2898),
    ]
    prices = []
    for (start, first), (end, last) in zip(anchors, anchors[1:], strict=False):
        prices.extend(
            first + (last - first) * (i - start) / (end - start) for i in range(start, end)
        )
    return make_bars([*prices, anchors[-1][1]])


def test_sloping_neckline_and_wait_conditions():
    bars = candidate_bars()
    result = head_and_shoulders_watch(bars)
    assert result is not None
    assert not result.satisfied
    assert "2735.50" in result.description
    assert "-5.00" in result.description
    assert "3037.00" in result.description
    assert "3131.00" in result.description
    assert "別に評価" in result.description
    assessment = PositionEntryEvaluator().evaluate(bars)
    assert assessment.phase is PositionEntryPhase.SUPPORT_TEST
    assert assessment.next_condition.key == "head_and_shoulders_watch"
    assert "待つ" in assessment.summary


def test_recent_low_break_is_not_sloping_neckline_break():
    bars = candidate_bars()
    bars[-1] = replace(bars[-1], close=Decimal("2800"), low=Decimal("2799"))
    assert head_and_shoulders_watch(bars) is not None


def test_completed_or_invalidated_pattern_is_not_pending():
    for price in (2700, 3200):
        bars = candidate_bars()
        bars[-1] = replace(
            bars[-1],
            open=Decimal(price),
            close=Decimal(price),
            low=Decimal(price - 1),
            high=Decimal(price + 1),
        )
        assert head_and_shoulders_watch(bars) is None


def test_no_pattern_or_insufficient_data():
    assert head_and_shoulders_watch([]) is None
    assert head_and_shoulders_watch(make_bars(list(range(100, 200)))) is None


def test_candidate_overrides_bullish_position_decision():
    from stock_signal.analysis.decision import LongOnlyDecisionPolicy
    from stock_signal.domain.analysis import Direction, InvestmentAction

    bars = candidate_bars()
    policy = LongOnlyDecisionPolicy(today=lambda: bars[-1].trade_date)
    assessment = PositionEntryEvaluator().evaluate(bars)
    checks = policy.equity_checks(bars, (), horizon_days=20)
    decision = policy._decide(
        Direction.UP, {Direction.UP: 90}, (), (), None, assessment, checks, 20
    )
    assert decision.action is InvestmentAction.WATCH
    assert "待つ" in decision.summary
    assert "撤回" in decision.reasons[0]
