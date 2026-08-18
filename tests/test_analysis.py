from datetime import date, timedelta
from decimal import Decimal

from stock_signal.analysis.engine import RuleBasedAnalysisEngine
from stock_signal.analysis.patterns import TechnicalPatternDetector
from stock_signal.domain.analysis import (
    AnalysisContext,
    Direction,
    InvestmentAction,
    PatternLifecycleStatus,
    PatternType,
    TransitionPhase,
)
from stock_signal.domain.market_data import DailyBar


def make_bars(
    prices: list[float],
    volumes: list[int] | None = None,
    start_date: date = date(2026, 1, 1),
) -> list[DailyBar]:
    volumes = volumes or [1000] * len(prices)
    return [
        DailyBar(
            symbol="TEST",
            trade_date=start_date + timedelta(days=index),
            open=Decimal(str(price)),
            high=Decimal(str(price + 1)),
            low=Decimal(str(price - 1)),
            close=Decimal(str(price)),
            volume=volumes[index],
            provider="test",
            is_adjusted=False,
        )
        for index, price in enumerate(prices)
    ]


def make_rectangle_breakout(
    *, direction: Direction, gap_driven: bool = False, volume: int = 2000
) -> list[DailyBar]:
    """十分な履歴、30日レンジ、最終日のブレイクを作る。"""
    bars = make_bars(
        [95 + index * 0.1 for index in range(40)],
        start_date=date.today() - timedelta(days=70),
    )
    start = bars[-1].trade_date + timedelta(days=1)
    for index in range(30):
        close = 101 if index % 4 < 2 else 99
        bars.append(DailyBar(
            symbol="TEST",
            trade_date=start + timedelta(days=index),
            open=Decimal(str(close)),
            high=Decimal(str(close + 1)),
            low=Decimal(str(close - 1)),
            close=Decimal(str(close)),
            volume=1000,
            provider="test",
            is_adjusted=False,
        ))
    close = 104 if direction is Direction.UP else 96
    normal_open = 101 if direction is Direction.UP else 99
    opening = (108 if direction is Direction.UP else 92) if gap_driven else normal_open
    bars.append(DailyBar(
        symbol="TEST",
        trade_date=start + timedelta(days=30),
        open=Decimal(str(opening)),
        high=Decimal(str(max(opening, close) + 1)),
        low=Decimal(str(min(opening, close) - 1)),
        close=Decimal(str(close)),
        volume=volume,
        provider="test",
        is_adjusted=False,
    ))
    return bars


def make_custom_bars(rows: list[tuple[float, float, float, float, int]]) -> list[DailyBar]:
    return [
        DailyBar(
            symbol="TEST",
            trade_date=date(2026, 1, 1) + timedelta(days=index),
            open=Decimal(str(opening)),
            high=Decimal(str(high)),
            low=Decimal(str(low)),
            close=Decimal(str(close)),
            volume=volume,
            provider="test",
            is_adjusted=False,
        )
        for index, (opening, high, low, close, volume) in enumerate(rows)
    ]


def test_uptrend_has_readable_factors_and_non_probability_scores() -> None:
    result = RuleBasedAnalysisEngine().analyze(
        "TEST", make_bars([100 + index * 0.8 for index in range(40)]), 5
    )

    assert result.status == "ready"
    assert result.direction == Direction.UP
    assert result.scores[Direction.UP] > result.scores[Direction.DOWN]
    assert any(factor.rule_id == "moving_average_trend" for factor in result.factors)
    assert all(factor.description for factor in result.factors)


def test_insufficient_data_is_explicit() -> None:
    result = RuleBasedAnalysisEngine().analyze("TEST", make_bars([100] * 10), 5)

    assert result.status == "insufficient_data"
    assert "最低25営業日" in result.message


def test_flat_prices_are_judged_as_flat() -> None:
    prices = [100 + ((index % 4) - 2) * 0.05 for index in range(40)]
    bars = make_bars(
        prices,
        start_date=date.today() - timedelta(days=len(prices) - 1),
    )
    result = RuleBasedAnalysisEngine().analyze("TEST", bars, 5)

    assert result.direction == Direction.FLAT
    assert any(factor.direction == Direction.FLAT for factor in result.factors)


def test_engine_can_receive_replacement_rules() -> None:
    class AlwaysDownRule:
        rule_id = "test_rule"

        def evaluate(self, bars, horizon_days):
            from stock_signal.domain.analysis import AnalysisFactor

            return AnalysisFactor(
                self.rule_id, "テスト規則", Direction.DOWN, 50, "下落要因です"
            )

    result = RuleBasedAnalysisEngine(rules=(AlwaysDownRule(),)).analyze(
        "TEST", make_bars([100] * 30), 5
    )

    assert result.direction == Direction.DOWN
    assert result.engine_id == "rule_based_technical"


def test_confirmed_rectangle_breakout_becomes_buy_candidate() -> None:
    result = RuleBasedAnalysisEngine().analyze(
        "TEST", make_rectangle_breakout(direction=Direction.UP), 5
    )

    assert result.investment_decision is not None
    assert result.investment_decision.action is InvestmentAction.BUY_CANDIDATE
    assert result.patterns[0].pattern_type is PatternType.RECTANGLE_BREAKOUT_UP
    assert result.patterns[0].volume_ratio == 2.0
    assert result.patterns[0].breakout_atr is not None
    assert result.patterns[0].breakout_atr >= 0.1
    assert result.pattern_lifecycles[0].status is PatternLifecycleStatus.ENTRY_WINDOW


def test_breakout_is_an_event_and_recent_reversal_weakens_signal() -> None:
    bars = make_rectangle_breakout(direction=Direction.UP)
    start = bars[-1].trade_date
    for offset, close in enumerate((104.2, 103.7, 103.2, 102.8), start=1):
        bars.append(DailyBar(
            symbol="TEST",
            trade_date=start + timedelta(days=offset),
            open=Decimal(str(close + 0.2)),
            high=Decimal(str(close + 1)),
            low=Decimal(str(close - 1)),
            close=Decimal(str(close)),
            volume=1000,
            provider="test",
            is_adjusted=False,
        ))

    result = RuleBasedAnalysisEngine().analyze("TEST", bars, 5)

    assert result.patterns[0].detected_at == bars[-5].trade_date.isoformat()
    assert result.pattern_lifecycles[0].trading_days_since_breakout == 4
    assert result.pattern_lifecycles[0].status is PatternLifecycleStatus.WEAKENING
    assert result.direction is Direction.DOWN
    assert result.scores[Direction.DOWN] > result.scores[Direction.UP]
    assert result.investment_decision is not None
    assert result.investment_decision.action is InvestmentAction.AVOID_NEW_BUY
    assert "勢い" in result.investment_decision.summary


def test_old_pattern_expires_and_is_not_reused_for_new_entry() -> None:
    bars = make_rectangle_breakout(direction=Direction.UP)
    start = bars[-1].trade_date
    for offset in range(1, 22):
        close = 104.0 + (offset % 2) * 0.1
        bars.append(DailyBar(
            symbol="TEST",
            trade_date=start + timedelta(days=offset),
            open=Decimal(str(close)),
            high=Decimal(str(close + 0.5)),
            low=Decimal(str(close - 0.5)),
            close=Decimal(str(close)),
            volume=1000,
            provider="test",
            is_adjusted=False,
        ))

    result = RuleBasedAnalysisEngine().analyze("TEST", bars, 5)

    assert result.pattern_lifecycles[0].status is PatternLifecycleStatus.EXPIRED
    assert result.investment_decision is not None
    assert result.investment_decision.action is not InvestmentAction.BUY_CANDIDATE
    assert "監視期限" in result.investment_decision.summary


def test_transition_readiness_exposes_the_one_remaining_condition() -> None:
    prices = (
        [120 - index * 0.8 for index in range(20)]
        + [104, 103, 102, 101, 100, 100.2, 100.4, 100.3, 100.5, 100.4]
        + [100.5, 100.7, 101, 101.4, 102, 102.8, 103.7, 104.7, 105.8, 106.9]
    )

    result = RuleBasedAnalysisEngine().analyze("TEST", make_bars(prices), 5)
    transition = result.transition_readiness

    assert transition is not None
    assert transition.phase is TransitionPhase.ONE_GATE_REMAINING
    assert transition.satisfied_conditions == 4
    assert transition.total_conditions == 5
    assert transition.readiness_score == 80.0
    assert transition.next_condition is not None
    assert transition.next_condition.key == "breakout_confirmation"
    assert result.investment_decision is not None
    assert result.investment_decision.action is InvestmentAction.WATCH
    assert "あと1つ" in result.investment_decision.summary


def test_confirmed_transition_can_be_an_early_buy_candidate() -> None:
    prices = (
        [120 - index * 0.8 for index in range(20)]
        + [104, 103, 102, 101, 100, 100.2, 100.4, 100.3, 100.5, 100.4]
        + [100.5, 100.7, 101, 101.4, 102, 102.8, 103.7, 104.7, 105.8, 108]
    )
    volumes = [1000] * 39 + [2000]

    result = RuleBasedAnalysisEngine().analyze(
        "TEST",
        make_bars(
            prices,
            volumes,
            start_date=date.today() - timedelta(days=len(prices) - 1),
        ),
        5,
    )
    transition = result.transition_readiness

    assert transition is not None
    assert transition.phase is TransitionPhase.EARLY_REVERSAL
    assert transition.satisfied_conditions == transition.total_conditions
    assert transition.risk_reward_ratio is not None
    assert transition.risk_reward_ratio >= 1.5
    assert result.patterns == ()
    assert result.investment_decision is not None
    assert result.investment_decision.action is InvestmentAction.BUY_CANDIDATE
    assert "転換の初動" in result.investment_decision.summary


def test_gap_driven_breakout_requires_event_review() -> None:
    result = RuleBasedAnalysisEngine().analyze(
        "TEST", make_rectangle_breakout(direction=Direction.UP, gap_driven=True), 5
    )

    assert result.investment_decision is not None
    assert result.investment_decision.action is InvestmentAction.WATCH
    assert "窓開け" in result.investment_decision.summary


def test_bearish_pattern_is_avoid_not_short_instruction() -> None:
    result = RuleBasedAnalysisEngine().analyze(
        "TEST", make_rectangle_breakout(direction=Direction.DOWN), 5
    )

    assert result.investment_decision is not None
    assert result.investment_decision.action is InvestmentAction.AVOID_NEW_BUY
    assert any("空売り" in reason for reason in result.investment_decision.reasons)


def test_missing_market_sector_and_earnings_are_explicit() -> None:
    result = RuleBasedAnalysisEngine().analyze(
        "TEST", make_rectangle_breakout(direction=Direction.UP), 5
    )

    checks = {check.key: check.status.value for check in result.equity_checks}
    assert checks["market_trend_score"] == "pending_data"
    assert checks["sector_trend_score"] == "plan_unavailable"
    assert checks["days_to_earnings"] == "pending_data"
    assert checks["disclosure_event"] == "addon_required"
    assert checks["data_freshness"] == "evaluated"


def test_light_context_evaluates_topix_and_upcoming_earnings() -> None:
    bars = make_rectangle_breakout(direction=Direction.UP)
    market_bars = [
        DailyBar(
            symbol="TOPIX",
            trade_date=bars[-21 + index].trade_date,
            open=Decimal(str(100 + index)),
            high=Decimal(str(101 + index)),
            low=Decimal(str(99 + index)),
            close=Decimal(str(100 + index)),
            volume=0,
            provider="jquants",
            is_adjusted=False,
        )
        for index in range(21)
    ]
    context = AnalysisContext(
        market_bars=tuple(market_bars),
        next_earnings_date=bars[-1].trade_date + timedelta(days=3),
        earnings_synced=True,
        jquants_plan="light",
    )

    result = RuleBasedAnalysisEngine().analyze("TEST", bars, 5, context)

    checks = {check.key: check for check in result.equity_checks}
    assert checks["market_trend_score"].status.value == "evaluated"
    assert checks["days_to_earnings"].value == 3
    assert result.investment_decision is not None
    assert result.investment_decision.action is InvestmentAction.WATCH
    assert "決算" in result.investment_decision.summary


def test_stale_data_cannot_become_buy_candidate() -> None:
    bars = make_rectangle_breakout(direction=Direction.UP)
    stale_bars = [
        DailyBar(
            symbol=bar.symbol,
            trade_date=bar.trade_date - timedelta(days=30),
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            provider=bar.provider,
            is_adjusted=bar.is_adjusted,
        )
        for bar in bars
    ]

    result = RuleBasedAnalysisEngine().analyze("TEST", stale_bars, 5)

    assert result.investment_decision is not None
    assert result.investment_decision.action is InvestmentAction.WATCH
    assert "古い" in result.investment_decision.summary


def test_mvp_pattern_families_are_detected_by_objective_shapes() -> None:
    base = []
    for index in range(40):
        close = 95 + index * 0.1
        base.append((close, close + 1, close - 1, close, 1000))

    triangle = []
    for index in range(30):
        low = 94 + index * 0.18
        close = (low + 102) / 2
        triangle.append((close, 102, low, close, 1000))
    triangle_patterns = TechnicalPatternDetector().detect(
        make_custom_bars([*base, *triangle, (102, 105, 101, 104, 2000)])
    )

    double_values = [
        100, 99, 98, 96, 94, 92, 94, 97, 100, 102, 100, 98, 96, 94, 92.5,
        94, 97, 100, 102, 101, 100, 99, 100, 101, 102, 102.5, 102.7, 102.8,
        102.9, 103,
    ]
    double_rows = [(value, value + 1, value - 1, value, 1000) for value in double_values]
    double_patterns = TechnicalPatternDetector().detect(
        make_custom_bars([*base, *double_rows, (103, 106, 102, 105, 2000)])
    )

    head_values = [
        90, 92, 94, 96, 99, 102, 99, 96, 94, 96, 100, 104, 108, 104, 100,
        96, 95, 97, 100, 102, 99, 98, 97, 96, 96, 96, 96, 96, 96, 96,
    ]
    head_rows = [(value, value + 1, value - 1, value, 1000) for value in head_values]
    head_patterns = TechnicalPatternDetector().detect(
        make_custom_bars([*base, *head_rows, (91, 92, 87, 89, 2000)])
    )

    assert any(
        pattern.pattern_type is PatternType.ASCENDING_TRIANGLE
        for pattern in triangle_patterns
    )
    assert any(
        pattern.pattern_type is PatternType.DOUBLE_BOTTOM for pattern in double_patterns
    )
    assert any(
        pattern.pattern_type is PatternType.HEAD_AND_SHOULDERS_TOP
        for pattern in head_patterns
    )
