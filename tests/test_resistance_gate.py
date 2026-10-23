"""上抜け検出と抵抗帯の突破条件を独立して検証する。"""

from dataclasses import replace
from datetime import date

import pytest
from test_analysis import make_bars, make_rectangle_breakout

from stock_signal.analysis.engine import RuleBasedAnalysisEngine
from stock_signal.analysis.entry_diagnostics import calculate_entry_diagnostics
from stock_signal.analysis.indicators import ResistanceBand
from stock_signal.analysis.resistance_gate import apply_breakout_resistance_gate
from stock_signal.domain.analysis import Direction, EntryStage, InvestmentAction, InvestmentDecision
from stock_signal.web.app import _serialize_analysis


def candidate():
    return InvestmentDecision(
        action=InvestmentAction.BUY_CANDIDATE,
        evidence_score=96,
        summary="上抜けを検出しました",
        reasons=(),
        cautions=(),
        entry_stage=EntryStage.ENTRY_READY,
        execution_stop_price=98,
        expected_target_price=110,
        execution_risk_reward_ratio=5,
        entry_strategy="breakout",
    )


def band(lower=99, upper=101):
    return ResistanceBand(lower, upper, (lower + upper) / 2, 3, date(2026, 1, 1),
                          date(2026, 1, 10), 0)


def install_bands(monkeypatch, bands):
    monkeypatch.setattr(
        "stock_signal.analysis.resistance_gate.resistance_bands",
        lambda *args, **kwargs: bands,
    )


@pytest.mark.parametrize("lower,upper", [(99, 101), (101, 103), (99, 100)])
def test_unbroken_band_before_target_holds_candidate(monkeypatch, lower, upper):
    install_bands(monkeypatch, [band(lower, upper)])
    original = candidate()
    decision, checks = apply_breakout_resistance_gate(original, make_bars([100] * 30), 5)
    assert decision.action == InvestmentAction.WATCH
    assert decision.entry_stage == EntryStage.CONDITIONAL_ENTRY
    assert "突破確認待ち" in decision.summary
    assert checks[0].value == upper
    assert decision.execution_stop_price == original.execution_stop_price
    assert decision.expected_target_price == original.expected_target_price
    assert decision.execution_risk_reward_ratio == 5


@pytest.mark.parametrize("bands", [[], [band(97, 99.99)], [band(110, 112)]])
def test_cleared_or_beyond_target_band_does_not_block(monkeypatch, bands):
    install_bands(monkeypatch, bands)
    original = candidate()
    assert apply_breakout_resistance_gate(original, make_bars([100] * 30), 5) == (original, ())


@pytest.mark.parametrize("action", [InvestmentAction.WATCH, InvestmentAction.AVOID_NEW_BUY])
def test_other_hold_or_avoid_reason_is_preserved(monkeypatch, action):
    install_bands(monkeypatch, [band()])
    original = replace(candidate(), action=action, summary="別条件による保留")
    decision, checks = apply_breakout_resistance_gate(original, make_bars([100] * 30), 5)
    assert decision.action == action
    assert decision.summary == original.summary
    assert checks


@pytest.mark.parametrize("strategy", ["swing_pullback", "position_pullback", None])
def test_support_strategies_are_not_changed(monkeypatch, strategy):
    install_bands(monkeypatch, [band()])
    original = replace(candidate(), entry_strategy=strategy)
    assert apply_breakout_resistance_gate(original, make_bars([100] * 30), 5) == (original, ())


def test_missing_inputs_do_not_invent_target_or_confirmation():
    original = replace(candidate(), expected_target_price=None)
    assert apply_breakout_resistance_gate(original, make_bars([100] * 30), 5) == (original, ())
    assert apply_breakout_resistance_gate(candidate(), [], 5) == (candidate(), ())


def test_engine_retains_detection_but_api_reports_pending_conditions(monkeypatch):
    bars = make_rectangle_breakout(direction=Direction.UP)
    before = RuleBasedAnalysisEngine().analyze("TEST", bars, 5)
    assert before.investment_decision.action == InvestmentAction.BUY_CANDIDATE
    install_bands(monkeypatch, [band(102.5, 104)])
    after = RuleBasedAnalysisEngine().analyze("TEST", bars, 5)
    assert after.patterns == before.patterns
    assert after.transition_readiness == before.transition_readiness
    assert after.scores == before.scores
    payload = _serialize_analysis(after)
    assert payload["investment_decision"]["action"] == "watch"
    assert payload["investment_decision"]["entry_stage"] == "conditional_entry"
    assert payload["score_is_probability"] is False
    assert any(c["key"] == "upper_resistance_confirmation" for c in payload["equity_checks"])


def test_target_rr_and_resistance_room_are_independent(monkeypatch):
    bars = make_bars([100] * 30)
    monkeypatch.setattr(
        "stock_signal.analysis.entry_diagnostics.resistance_bands", lambda *a, **kw: [band()],
    )
    diagnostics = calculate_entry_diagnostics(bars, candidate().execution_stop_price)
    assert candidate().execution_risk_reward_ratio == (110 - 100) / (100 - 98)
    assert diagnostics.resistance_room_percent == 0
    assert diagnostics.resistance_risk_reward_ratio == 0
    assert diagnostics.stop_width_percent == 2
