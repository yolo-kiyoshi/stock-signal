"""判定の監査で判明した境界条件と二段階スクリーニングを検証する。"""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from test_analysis import make_bars, make_rectangle_breakout
from test_light_research import report

from stock_signal.analysis.decision import LongOnlyDecisionPolicy
from stock_signal.analysis.engine import RuleBasedAnalysisEngine
from stock_signal.analysis.lifecycle import PatternLifecycleEvaluator
from stock_signal.analysis.patterns import TechnicalPatternDetector
from stock_signal.analysis.position_entry import PositionEntryEvaluator
from stock_signal.batch import DailyBatchRunner
from stock_signal.config import Settings
from stock_signal.database import replace_market_analysis_snapshots, upsert_daily_bars
from stock_signal.domain.analysis import Direction, InvestmentAction, PatternLifecycleStatus
from stock_signal.domain.dashboard import WatchlistItem
from stock_signal.fundamentals import evaluate_financials
from stock_signal.persistence.engine import get_engine
from stock_signal.persistence.schema import analysis_snapshots
from stock_signal.providers.base import MarketDataResponseError
from stock_signal.providers.jquants import JQuantsProvider


def test_target_uses_detected_formation_not_earlier_extremes():
    bars = make_rectangle_breakout(direction=Direction.UP)
    pattern = TechnicalPatternDetector().detect(bars)[0]
    evaluator = PatternLifecycleEvaluator()
    original = evaluator.evaluate(bars, [pattern])[0]
    bars[-32] = replace(bars[-32], low=Decimal("1"))
    changed = evaluator.evaluate(bars, [pattern])[0]
    assert pattern.pattern_height == 4
    assert original.target_price == changed.target_price == 106


@pytest.mark.parametrize("terminal", ["failed", "target"])
def test_terminal_pattern_does_not_revive_after_return_to_entry_price(terminal):
    bars = make_rectangle_breakout(direction=Direction.UP)
    pattern = TechnicalPatternDetector().detect(bars)[0]
    last = bars[-1]
    first = replace(
        last,
        trade_date=last.trade_date + timedelta(days=1),
        close=Decimal("90") if terminal == "failed" else Decimal("103"),
        high=Decimal("107"),
        low=Decimal("89") if terminal == "failed" else Decimal("102"),
    )
    rebound = replace(last, trade_date=last.trade_date + timedelta(days=2))
    result = PatternLifecycleEvaluator().evaluate([*bars, first, rebound], [pattern])[0]
    assert result.status == (
        PatternLifecycleStatus.FAILED
        if terminal == "failed"
        else PatternLifecycleStatus.TARGET_REACHED
    )
    assert result.entry_days_remaining == 0


def test_stop_above_current_price_never_produces_candidate():
    bars = make_rectangle_breakout(direction=Direction.UP)
    result = RuleBasedAnalysisEngine().analyze("TEST", bars, 5)
    lifecycles = [
        replace(
            row,
            execution_stop_price=104,
            execution_risk_reward_ratio=None,
            status=PatternLifecycleStatus.ENTRY_WINDOW,
        )
        for row in result.pattern_lifecycles
    ]
    decision = LongOnlyDecisionPolicy().decide(
        result.direction,
        result.scores,
        result.patterns,
        lifecycles,
        result.transition_readiness,
        None,
        result.equity_checks,
        5,
    )
    assert decision.action == InvestmentAction.WATCH
    assert "損切り" in decision.summary


@pytest.mark.parametrize("volume,opening", [(0, 103), (2000, 120)])
def test_current_bar_safety_is_checked_even_after_original_breakout(volume, opening):
    bars = make_rectangle_breakout(direction=Direction.UP)
    result = RuleBasedAnalysisEngine().analyze("TEST", bars, 5)
    bars.append(
        replace(
            bars[-1],
            trade_date=bars[-1].trade_date + timedelta(days=1),
            volume=volume,
            open=Decimal(opening),
            high=Decimal(max(104, opening)),
        )
    )
    policy = LongOnlyDecisionPolicy(today=lambda: bars[-1].trade_date)
    checks = policy.equity_checks(bars, result.patterns)
    decision = policy.decide(
        result.direction,
        result.scores,
        result.patterns,
        result.pattern_lifecycles,
        result.transition_readiness,
        None,
        checks,
        5,
    )
    assert decision.action == InvestmentAction.WATCH


def test_repeated_low_is_counted_from_most_recent_touch():
    bars = make_bars([100] * 70)
    result = RuleBasedAnalysisEngine().analyze("TEST", bars, 5)
    stability = next(c for c in result.transition_readiness.conditions if c.key == "low_stability")
    assert stability.current_value == 0
    assert not stability.satisfied


def test_no_price_range_is_insufficient_instead_of_aborting_batch():
    bars = [replace(b, high=b.close, low=b.close) for b in make_bars([100] * 75)]
    assert RuleBasedAnalysisEngine().analyze("TEST", bars, 20).status == "insufficient_data"


def test_broken_support_is_not_reported_as_held_on_recovery():
    bars = make_bars([102, 100, 98, 102])
    # 最終日は支持帯と接触しないため、2日前の接触後の割れを検出する。
    result = PositionEntryEvaluator()._price_level("fixed", "支持帯", 100, bars, 1)
    assert result.touched
    assert not result.held


def test_dividend_revision_does_not_erase_earnings_forecast():
    records = [
        report(),
        report(
            DiscNo="dividend",
            DiscDate="2026-08-02",
            DocType="DividendForecastRevision",
            FEPS="",
            FOP="",
        ),
    ]
    result = evaluate_financials(records, date(2026, 8, 3), 1000)
    assert result["metrics"]["forecast_per"] == 10


def test_unchanged_later_forecast_does_not_erase_recent_downgrade():
    records = [
        report(),
        report(DiscNo="revision", DiscDate="2026-08-02", DocType="EarnForecastRevision", FOP="400"),
        report(DiscNo="later", DiscDate="2026-08-03", FOP="400"),
    ]
    assert evaluate_financials(records, date(2026, 8, 4), 1000)["event_status"] == "hold"


def test_forecast_only_disclosures_still_gate_negative_events():
    records = [
        report(DocType="EarnForecastRevision"),
        report(DiscNo="second", DiscDate="2026-08-02", DocType="EarnForecastRevision", FOP="400"),
    ]
    result = evaluate_financials(records, date(2026, 8, 3), 1000)
    assert result["status"] == "missing"
    assert result["event_status"] == "hold"


@pytest.mark.parametrize(
    "category,expected", [("011", "stock"), ("014", "etf"), ("013", "reit"), ("999", "unknown")]
)
def test_master_uses_official_product_category(category, expected):
    class Http:
        def get_json(self, *args, **kwargs):
            return {"data": [{"Code": "16150", "CoName": "試験", "ProdCat": category}]}

    provider = JQuantsProvider("test", http_client=Http(), minimum_request_interval=0)
    assert provider.fetch_instrument_master(date.today())[0].instrument_type == expected


@pytest.mark.parametrize("fail", [False, True])
def test_screening_fetches_only_technical_candidates_once_and_preserves_failures(
    database_url,
    monkeypatch,
    fail,
):
    bars = [
        replace(b, symbol="7203", provider="jquants", is_adjusted=True, raw_close=b.close)
        for b in make_rectangle_breakout(direction=Direction.UP)
    ]
    upsert_daily_bars(database_url, bars)
    result = RuleBasedAnalysisEngine().analyze("7203", bars, 5)
    assert result.investment_decision.action == InvestmentAction.BUY_CANDIDATE
    calls = []

    class Provider:
        def fetch_financial_summaries(self, symbol):
            calls.append(symbol)
            if fail:
                raise MarketDataResponseError("試験用の取得失敗")
            return []

    runner = DailyBatchRunner(
        Settings(database_url=database_url), provider_factory=lambda _: Provider()
    )
    items = [WatchlistItem("7203", "jquants", "試験", "プライム", "JPY")]
    monkeypatch.setattr("stock_signal.batch.list_analysis_universe", lambda *a, **k: items)
    monkeypatch.setattr(
        runner.analysis_service,
        "analyze_items",
        lambda items, horizon, **kwargs: [("試験", replace(result, horizon_days=horizon))],
    )
    output = runner._screen_market()
    assert calls == ["7203"]
    assert output.status == ("failed" if fail else "success")
    with get_engine(database_url).connect() as connection:
        rows = connection.execute(select(analysis_snapshots)).mappings().all()
    long = next(r for r in rows if r["horizon_days"] == 20)
    assert long["analysis_json"]["research"]["screening_status"] == "data_pending"
    assert long["action"] == "watch"


def test_replacing_snapshots_rolls_back_if_new_rows_are_invalid(database_url):
    record = dict(
        symbol="7203",
        provider="jquants",
        as_of_date=date.today(),
        horizon_days=5,
        direction="up",
        action="watch",
        evidence_score=50,
        analysis_json={},
    )
    replace_market_analysis_snapshots(database_url, [record])
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        replace_market_analysis_snapshots(database_url, [record, record])
    with get_engine(database_url).connect() as connection:
        assert len(connection.execute(select(analysis_snapshots)).all()) == 1


@pytest.mark.parametrize(
    "opening,high,low,close,expected",
    [
        (103, 104, 101, 102, InvestmentAction.WATCH),
        (105, 106, 102, 103, InvestmentAction.WATCH),
        (103, 104, 101, 103.5, InvestmentAction.BUY_CANDIDATE),
    ],
)
def test_swing_requires_current_breakout_hold_and_no_lower_half_bearish_close(
    opening, high, low, close, expected,
):
    bars = make_rectangle_breakout(direction=Direction.UP)
    original = RuleBasedAnalysisEngine().analyze("TEST", bars, 5)
    assert original.investment_decision.action == InvestmentAction.BUY_CANDIDATE
    bars.append(replace(
        bars[-1], trade_date=bars[-1].trade_date + timedelta(days=1),
        open=Decimal(str(opening)), high=Decimal(str(high)),
        low=Decimal(str(low)), close=Decimal(str(close)),
    ))
    policy = LongOnlyDecisionPolicy(today=lambda: bars[-1].trade_date)
    decision = policy.decide(
        original.direction, original.scores, original.patterns,
        original.pattern_lifecycles,
        replace(original.transition_readiness, current_price=close),
        None, policy.equity_checks(bars, original.patterns), 5,
    )
    assert decision.action == expected
    if expected == InvestmentAction.WATCH:
        assert "買い支え" in decision.summary
