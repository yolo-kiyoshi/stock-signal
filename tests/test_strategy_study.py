"""固定条件の時系列評価で、境界・未来情報・金融計算・欠損を検証する。"""

import json
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from test_analysis import make_bars

from stock_signal.analysis.historical_validation import RealizedOutcome
from stock_signal.analysis.strategy_study import (
    StrategyStudyService,
    StudyPlan,
    event_metrics,
    write_study_report,
)
from stock_signal.cli import main
from stock_signal.domain.analysis import (
    ANALYSIS_ENGINE_VERSION,
    AnalysisResult,
    Direction,
    InvestmentAction,
    InvestmentDecision,
)


def configuration(**changes):
    data = {
        "symbols": ["7203"], "horizons": [5], "provider": "jquants",
        "cost_bps": 10, "engine_version": ANALYSIS_ENGINE_VERSION,
        "selection_notes": "検証専用の人工データ。実市場の収益性は評価しない。",
        "windows": [{"name": "前半", "start": "2026-01-31", "end": "2026-02-14"}],
    }
    return data | changes


@pytest.mark.parametrize("changes", [
    {"cost_bps": float("nan")}, {"cost_bps": -1}, {"cost_bps": 10000},
    {"cost_bps": True}, {"symbols": []}, {"symbols": ["7203", "7203"]},
    {"horizons": [5, 5]}, {"horizons": [1]}, {"provider": "unknown"},
    {"selection_notes": ""}, {"engine_version": "previous"}, {"unknown": 1},
    {"windows": [{"name": "逆転", "start": "2026-02-01", "end": "2026-01-01"}]},
    {"windows": [{"name": "前", "start": "2026-01-01", "end": "2026-02-01"},
                 {"name": "後", "start": "2026-02-01", "end": "2026-03-01"}]},
])
def test_plan_rejects_ambiguous_or_invalid_conditions(changes):
    with pytest.raises(ValueError):
        StudyPlan.from_dict(configuration(**changes))


def test_costs_apply_to_entry_and_exit_not_just_return_subtraction():
    outcome = RealizedOutcome(
        Direction.UP, date(2026, 1, 1), date(2026, 1, 7), 100, 110,
        10, 5, .5, 2, 8,
        maximum_favorable_excursion_percent=12, maximum_adverse_excursion_percent=-4,
        stop_hit=True, target_hit=True,
    )
    metrics = event_metrics(outcome, 10)
    assert metrics["gross_return_percent"] == pytest.approx(10)
    expected = (110 * .999 / (100 * 1.001) - 1) * 100
    assert metrics["net_return_percent"] == pytest.approx(expected)
    assert metrics["net_excess_return_percent"] == pytest.approx(expected - 2)
    assert metrics["stop_hit"] is True
    assert metrics["mae_percent"] == -4
    missing = event_metrics(replace(outcome, market_return_percent=None), 0)
    assert missing["net_excess_return_percent"] is None


def install_engine(monkeypatch, schedule):
    calls = []

    class ScheduledEngine:
        def __init__(self, decision_policy):
            self.policy = decision_policy

        def analyze(self, symbol, bars, horizon, context):
            day = bars[-1].trade_date
            assert self.policy.today() == day
            assert all(b.trade_date <= day for b in context.market_bars)
            assert not context.earnings_synced
            assert context.next_earnings_date is None
            calls.append((day, tuple(b.trade_date for b in bars)))
            strategy = schedule.get(day)
            return AnalysisResult(
                symbol, day.isoformat(), horizon, Direction.UP, {}, (),
                "rule_based_technical", ANALYSIS_ENGINE_VERSION,
                investment_decision=InvestmentDecision(
                    InvestmentAction.BUY_CANDIDATE if strategy else InvestmentAction.WATCH,
                    90, "検証用判定", (), (), entry_strategy=strategy,
                    execution_stop_price=98, expected_target_price=103,
                ),
            )

    monkeypatch.setattr(
        "stock_signal.analysis.historical_validation.RuleBasedAnalysisEngine", ScheduledEngine,
    )
    return calls


def data():
    bars = [replace(b, symbol="7203", provider="jquants", is_adjusted=True)
            for b in make_bars([100] * 60)]
    market = [replace(b, symbol="TOPIX") for b in bars]
    return bars, market


def test_events_do_not_cross_period_end_or_depend_on_future_maturity(monkeypatch):
    bars, market = data()
    schedule = {bars[i].trade_date: "breakout" for i in [30, 31, 43, 44]}
    calls = install_engine(monkeypatch, schedule)
    plan = StudyPlan.from_dict(configuration())
    service = StrategyStudyService("unused")
    report = service.evaluate_loaded(plan, {"7203": bars}, market)
    assert len(report["events"]) == 2
    assert [e["status"] for e in report["events"]] == ["ready", "insufficient_future_data"]
    summary = report["summary"][0]
    assert summary["candidate_days"] == 4
    assert summary["mature_events"] == 1
    assert summary["events"] == 2
    assert report["events"][0]["entry_date"] == bars[31].trade_date.isoformat()
    assert report["events"][0]["exit_date"] == bars[35].trade_date.isoformat()
    assert summary["mean_net_return_percent"] < 0
    assert summary["positive_return_fraction"] == 0
    assert all(day <= date(2026, 2, 14) for day, _ in calls)
    assert all(max(history) == day for day, history in calls)
    # 終端より後の価格を変えても結果・入力ハッシュを変えない。
    changed = [b if i <= 44 else replace(b, close=Decimal("999"))
               for i, b in enumerate(bars)]
    other = service.evaluate_loaded(plan, {"7203": changed}, market)
    assert other["events"] == report["events"]
    assert other["input_sha256"] == report["input_sha256"]


def test_continuing_candidate_at_start_is_not_new_and_strategy_change_is_new(monkeypatch):
    bars, market = data()
    schedule = {bars[i].trade_date: "breakout" for i in [29, 30, 31]}
    schedule[bars[32].trade_date] = "swing_pullback"
    install_engine(monkeypatch, schedule)
    report = StrategyStudyService("unused").evaluate_loaded(
        StudyPlan.from_dict(configuration()), {"7203": bars}, market,
    )
    assert len(report["events"]) == 1
    assert report["events"][0]["strategy"] == "swing_pullback"
    assert report["summary"][0]["candidate_days"] == 2
    assert report["summary"][0]["events"] == 0
    assert report["summary"][0]["mean_net_return_percent"] is None


def test_empty_and_missing_benchmark_are_not_fabricated(monkeypatch):
    bars, _ = data()
    install_engine(monkeypatch, {bars[30].trade_date: "breakout"})
    plan = StudyPlan.from_dict(configuration(symbols=["7203", "9999"]))
    report = StrategyStudyService("unused").evaluate_loaded(plan, {"7203": bars}, [])
    assert report["summary"][0]["mature_events"] == 1
    assert report["summary"][0]["benchmark_events"] == 0
    assert report["summary"][0]["mean_net_excess_return_percent"] is None
    missing = next(c for c in report["coverage"] if c["symbol"] == "9999")
    assert missing["status"] == "no_data_in_window"
    assert missing["evaluation_days"] == 0
    # 未生成の指標がNaNではなくJSON nullで保存できる。
    json.dumps(report, allow_nan=False)


def test_windows_are_separate_and_input_identity_is_recorded(monkeypatch):
    bars, market = data()
    install_engine(monkeypatch, {bars[i].trade_date: "breakout" for i in [30, 45]})
    plan = StudyPlan.from_dict(configuration(windows=[
        {"name": "前半", "start": "2026-01-31", "end": "2026-02-14"},
        {"name": "後半", "start": "2026-02-15", "end": "2026-03-01"},
    ]))
    report = StrategyStudyService("unused").evaluate_loaded(plan, {"7203": bars}, market)
    assert [e["window"] for e in report["events"]] == ["前半", "後半"]
    assert all(e["status"] == "ready" for e in report["events"])
    assert len(report["plan_sha256"]) == 64
    assert set(report["input_sha256"]) == {"7203", "TOPIX"}
    changed = replace(plan, cost_bps=20)
    other = StrategyStudyService("unused").evaluate_loaded(changed, {"7203": bars}, market)
    assert other["plan_sha256"] != report["plan_sha256"]
    assert other["input_sha256"] == report["input_sha256"]


def test_report_and_cli_do_not_overwrite_results(tmp_path, monkeypatch, capsys):
    bars, market = data()
    install_engine(monkeypatch, {})
    loads = []

    def load(database_url, symbol, provider):
        loads.append((symbol, provider))
        return market if symbol == "TOPIX" else bars

    monkeypatch.setattr("stock_signal.analysis.strategy_study.load_daily_bars", load)
    plan_path, output = tmp_path / "plan.json", tmp_path / "result.json"
    plan_path.write_text(json.dumps(configuration()))
    args = ["strategy-study", "--plan", str(plan_path), "--output", str(output)]
    assert main(args) == 0
    assert loads == [("7203", "jquants"), ("TOPIX", "jquants")]
    saved = output.read_bytes()
    assert json.loads(saved)["plan"]["engine_version"] == ANALYSIS_ENGINE_VERSION
    assert main(args) == 2
    assert output.read_bytes() == saved
    assert len(loads) == 2
    with pytest.raises(ValueError, match="既に"):
        write_study_report({}, output)
    assert "既に" in capsys.readouterr().out


def test_real_engine_and_saved_prices_produce_a_costed_breakout_event():
    from datetime import timedelta

    from test_analysis import make_rectangle_breakout

    bars = make_rectangle_breakout(direction=Direction.UP)
    signal_day = bars[-1].trade_date
    future = make_bars([104, 105, 106, 107, 108], start_date=signal_day + timedelta(days=1))
    bars = [replace(b, symbol="7203", provider="jquants", is_adjusted=True)
            for b in bars + future]
    plan = StudyPlan.from_dict(configuration(windows=[{
        "name": "実エンジン確認", "start": signal_day.isoformat(),
        "end": bars[-1].trade_date.isoformat(),
    }]))
    report = StrategyStudyService("unused").evaluate_loaded(plan, {"7203": bars}, [])
    first = report["events"][0]
    assert first["signal_date"] == signal_day.isoformat()
    assert first["strategy"] == "breakout"
    assert first["status"] == "ready"
    assert first["net_return_percent"] == pytest.approx((108 * .999 / (104 * 1.001) - 1) * 100)
