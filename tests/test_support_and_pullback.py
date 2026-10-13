import json
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

from test_analysis import make_custom_bars

from stock_signal.analysis.decision import LongOnlyDecisionPolicy
from stock_signal.analysis.engine import RuleBasedAnalysisEngine
from stock_signal.analysis.indicators import support_bands
from stock_signal.analysis.position_entry import PositionEntryEvaluator
from stock_signal.domain.analysis import InvestmentAction, PatternType
from stock_signal.domain.market_data import DailyBar


def test_support_requires_repeated_lows_and_excludes_broken_band():
    rows = [(100, 101, 90 if i in {25, 35, 45} else 99, 100, 1000) for i in range(55)]
    bands = support_bands(make_custom_bars(rows), lookback=40)
    assert len(bands) == 1
    assert bands[0].touches == 3
    assert bands[0].lower <= 90 <= bands[0].upper
    assert bands[0].distance_percent == -10
    rows[-1] = (85, 86, 84, 85, 1000)
    assert support_bands(make_custom_bars(rows), lookback=40) == ()
    assert support_bands(make_custom_bars(rows[:20]), lookback=40) == ()


def shimojima_bars():
    """2026年9月9日までの保存済み調整日足による回帰用データ。"""
    records = json.loads((Path(__file__).parent / "fixtures/shimojima_20260909.json").read_text())
    return [
        DailyBar(
            symbol="7482",
            provider="jquants",
            trade_date=date.fromisoformat(row["date"]),
            open=Decimal(row["open"]),
            high=Decimal(row["high"]),
            low=Decimal(row["low"]),
            close=Decimal(row["close"]),
            volume=row["volume"],
            is_adjusted=True,
        )
        for row in records
    ]


def test_shimojima_rebound_does_not_override_falling_lows():
    bars = shimojima_bars()
    result = RuleBasedAnalysisEngine(
        decision_policy=LongOnlyDecisionPolicy(today=lambda: date(2026, 9, 9))
    ).analyze("7482", bars, 20)
    assert result.investment_decision.action is InvestmentAction.WATCH
    assert "安値切り下げ" in result.investment_decision.summary
    assert not any(p.pattern_type is PatternType.DOUBLE_TOP for p in result.patterns)
    conditions = {c.key: c for c in result.position_entry.conditions}
    assert conditions["rebound_confirmation"].satisfied
    assert not conditions["falling_lows_stopped"].satisfied
    assert "1490.00→1489.00→1488.00" in conditions["falling_lows_stopped"].description


def test_low_stabilization_removes_only_falling_lows_gate():
    bars = shimojima_bars()
    bars[-1] = replace(bars[-1], low=Decimal("1490"))
    result = PositionEntryEvaluator().evaluate(bars)
    assert next(c for c in result.conditions if c.key == "falling_lows_stopped").satisfied
