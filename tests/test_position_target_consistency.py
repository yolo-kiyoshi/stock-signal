"""中長期目標と診断欄の抵抗・損益比の整合性を検証する。"""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest
from test_analysis import make_bars
from test_resistance_gate import band

from stock_signal.analysis.engine import RuleBasedAnalysisEngine
from stock_signal.analysis.entry_diagnostics import nearest_resistance
from stock_signal.domain.analysis import InvestmentAction, PositionEntryPhase


def pullback_bars():
    """支持維持と反発が成立する上昇トレンドの日足を作る。"""
    bars = make_bars(
        [*[100 + i * .4 for i in range(80)],
         131, 130, 129, 128, 127, 126.5, 126, 125.5, 127],
        start_date=date.today() - timedelta(days=88),
    )
    bars[-1] = replace(bars[-1], open=Decimal('125.8'),
                       high=Decimal('128'), low=Decimal('124.8'))
    return bars


@pytest.mark.parametrize('resistance', [[], [band(150, 152)], [band(127, 129)]])
def test_position_and_diagnostics_use_same_nearest_target(monkeypatch, resistance):
    monkeypatch.setattr(
        'stock_signal.analysis.entry_diagnostics.resistance_bands',
        lambda *a, **kw: resistance,
    )
    result = RuleBasedAnalysisEngine().analyze('TEST', pullback_bars(), 20)
    assessment = result.position_entry
    diagnostic = result.entry_diagnostics
    decision = result.investment_decision
    assert assessment.phase is PositionEntryPhase.PULLBACK_CANDIDATE
    assert assessment.target_price == diagnostic.resistance_price
    assert decision.expected_target_price == diagnostic.resistance_price
    assert assessment.risk_reward_ratio == pytest.approx(
        diagnostic.resistance_risk_reward_ratio, abs=.00005,
    )
    assert decision.action is InvestmentAction.WATCH
    if resistance and resistance[0].lower == 150:
        assert assessment.target_price < 150
    if resistance and resistance[0].lower == 127:
        assert assessment.risk_reward_ratio == 0


def test_very_close_resistance_is_not_skipped(monkeypatch):
    monkeypatch.setattr(
        'stock_signal.analysis.entry_diagnostics.resistance_bands',
        lambda *a, **kw: [band(127.01, 127.02), band(150, 152)],
    )
    result = RuleBasedAnalysisEngine().analyze('TEST', pullback_bars(), 20)
    assert result.position_entry.target_price == 127.01
    assert result.investment_decision.action is InvestmentAction.WATCH


@pytest.mark.parametrize('ratio,action', [
    (1.496, InvestmentAction.WATCH),
    (1.501, InvestmentAction.BUY_CANDIDATE),
])
def test_unrounded_ratio_controls_purchase_threshold(monkeypatch, ratio, action):
    bars = pullback_bars()
    baseline = RuleBasedAnalysisEngine().analyze('TEST', bars, 20)
    stop = baseline.position_entry.invalidation_price
    target = 127 + (127 - stop) * ratio
    monkeypatch.setattr(
        'stock_signal.analysis.entry_diagnostics.nearest_resistance',
        lambda *a, **kw: ('above', target),
    )
    monkeypatch.setattr(
        'stock_signal.analysis.position_entry.nearest_resistance',
        lambda *a, **kw: ('above', target),
    )
    result = RuleBasedAnalysisEngine().analyze('TEST', bars, 20)
    assert result.position_entry.risk_reward_ratio == pytest.approx(ratio)
    assert result.investment_decision.action is action


def test_sixty_day_high_is_checked_within_position_lookback(monkeypatch):
    monkeypatch.setattr(
        'stock_signal.analysis.entry_diagnostics.resistance_bands', lambda *a, **kw: [],
    )
    bars = make_bars([100] * 130)
    bars[-101] = replace(bars[-101], high=Decimal('150'))
    bars[-41] = replace(bars[-41], high=Decimal('140'))
    for i in range(-21, -1):
        bars[i] = replace(bars[i], high=Decimal('120'))
    bars[-1] = replace(bars[-1], open=Decimal('130'), close=Decimal('130'),
                       high=Decimal('131'), low=Decimal('129'))
    assert nearest_resistance(bars, lookback=120) == ('above', 140)


def test_new_high_does_not_invent_target(monkeypatch):
    monkeypatch.setattr(
        'stock_signal.analysis.entry_diagnostics.resistance_bands', lambda *a, **kw: [],
    )
    bars = make_bars([100] * 130 + [200])
    assert nearest_resistance(bars, lookback=120) == ('not_found', None)
