"""旧判定が購入候補に残らないことと、保存データからの再分析を検証する。"""

from dataclasses import replace
from datetime import date, timedelta

import pytest
from sqlalchemy import insert, update
from test_analysis import make_bars, make_rectangle_breakout

from stock_signal.analysis.service import AnalysisService
from stock_signal.batch import DailyBatchRunner
from stock_signal.config import Settings
from stock_signal.database import list_market_candidates, upsert_daily_bars
from stock_signal.domain.analysis import ANALYSIS_ENGINE_VERSION, Direction
from stock_signal.persistence.engine import get_engine
from stock_signal.persistence.schema import analysis_snapshots, instruments


def seed_snapshots(database_url, horizon):
    variants = [
        ("3397", "2.15.0", False),
        ("1001", None, False),
        ("1002", ANALYSIS_ENGINE_VERSION, True),
        ("1003", ANALYSIS_ENGINE_VERSION, False),
        ("1004", ANALYSIS_ENGINE_VERSION, False),
    ]
    with get_engine(database_url).begin() as conn:
        conn.execute(insert(instruments), [
            {"symbol": symbol, "provider": "jquants", "display_name": "試験銘柄",
             "as_of_date": date.today(), "market": "プライム", "sector_33_code": "6100"}
            for symbol, _, _ in variants
        ])
        conn.execute(insert(analysis_snapshots), [
            {"symbol": symbol, "provider": "jquants", "as_of_date": date.today(),
             "horizon_days": horizon, "direction": "up", "action": "buy_candidate",
             "evidence_score": 100 - index, "analysis_json": {
                 "engine_version": version, "research_stale": stale,
                 "entry_stage": "entry_ready", "summary": "購入条件充足",
                 "research": {"technical_action": "buy_candidate", "screening_status": "candidate"},
             }}
            for index, (symbol, version, stale) in enumerate(variants)
        ])


@pytest.mark.parametrize("horizon", [5, 20])
def test_old_candidates_excluded_before_filter_and_pagination(database_url, horizon):
    seed_snapshots(database_url, horizon)
    all_items = list_market_candidates(database_url, horizon_days=horizon)
    stale = [item for item in all_items if item["symbol"] in {"3397", "1001", "1002"}]
    assert all(item["action"] == "watch" for item in stale)
    assert all(item["entry_stage"] == "not_applicable" for item in stale)
    assert all(item["screening_status"] == "needs_reanalysis" for item in stale)
    assert all(item["technical_action"] is None for item in stale)
    assert all("再分析待ち" in item["judgment_reason"] for item in stale)
    first = list_market_candidates(database_url, horizon_days=horizon,
                                   action="buy_candidate", limit=1)
    second = list_market_candidates(database_url, horizon_days=horizon,
                                    action="buy_candidate", limit=1, offset=1)
    assert [item["symbol"] for item in first + second] == ["1003", "1004"]
    assert {item["symbol"] for item in list_market_candidates(
        database_url, horizon_days=horizon, action="watch",
    )} == {"3397", "1001", "1002"}


def test_reanalysis_uses_saved_data_and_matches_detail(database_url):
    seed_snapshots(database_url, 5)
    bars = [replace(bar, symbol="3397", provider="jquants", is_adjusted=True)
            for bar in make_rectangle_breakout(direction=Direction.UP)]
    upsert_daily_bars(database_url, bars)

    def forbidden_provider(name):
        raise AssertionError("再分析時に外部プロバイダーを生成してはいけません")

    runner = DailyBatchRunner(Settings(database_url=database_url),
                              provider_factory=forbidden_provider)
    result = runner.reanalyze_market()
    assert result.status == "success"
    assert result.analysis_summary["financial_syncs"] == 0
    assert result.upserted == 2
    for horizon in (5, 20):
        items = list_market_candidates(database_url, horizon_days=horizon)
        assert len(items) == 1
        saved = items[0]
        detail = AnalysisService(database_url).analyze_symbol("3397", horizon, "jquants")
        assert saved["action"] == detail.investment_decision.action.value
        assert saved["entry_stage"] == detail.investment_decision.entry_stage.value
        assert saved["judgment_reason"] == detail.investment_decision.summary
        assert saved["engine_version"] == ANALYSIS_ENGINE_VERSION
        assert saved["screening_status"] != "needs_reanalysis"


def test_new_daily_bar_invalidates_current_version_buy_before_filter(database_url):
    seed_snapshots(database_url, 5)
    with get_engine(database_url).begin() as conn:
        conn.execute(update(analysis_snapshots).values(as_of_date=date.today() - timedelta(days=1)))
    upsert_daily_bars(database_url, [replace(
        make_bars([100], start_date=date.today())[0], symbol="1003", provider="jquants",
    )])
    buys = list_market_candidates(database_url, action="buy_candidate")
    assert [item["symbol"] for item in buys] == ["1004"]
    row = next(item for item in list_market_candidates(database_url) if item["symbol"] == "1003")
    assert row["action"] == "watch"
    assert row["screening_status"] == "needs_reanalysis"
