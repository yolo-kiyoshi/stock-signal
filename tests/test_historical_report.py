from datetime import date

from stock_signal.analysis.historical_validation import (
    HistoricalValidationPoint,
    HistoricalValidationResult,
    RealizedOutcome,
)
from stock_signal.charts.historical_fit import render_historical_fit_report
from stock_signal.domain.analysis import (
    AnalysisResult,
    Direction,
    InvestmentAction,
    InvestmentDecision,
)


def _result(
    as_of: date,
    horizon: int,
    predicted: Direction,
    actual: Direction,
    return_percent: float,
) -> HistoricalValidationResult:
    decision = InvestmentDecision(
        InvestmentAction.WATCH,
        55.0,
        "テスト用の様子見判定",
        (),
        (),
    )
    analysis = AnalysisResult(
        symbol="7203",
        as_of_date=as_of.isoformat(),
        horizon_days=horizon,
        direction=predicted,
        scores={
            Direction.UP: 50.0,
            Direction.FLAT: 30.0,
            Direction.DOWN: 20.0,
        },
        factors=(),
        engine_id="rule_based_technical",
        engine_version="2.6.0",
        investment_decision=decision,
    )
    outcome = RealizedOutcome(
        direction=actual,
        start_date=as_of,
        target_date=date(2026, 6, 30) if horizon == 5 else date(2026, 7, 22),
        start_close=2500.0,
        target_close=2550.0,
        return_percent=return_percent,
        move_atr=0.8,
        threshold_atr=0.5 if horizon == 5 else 1.0,
        market_return_percent=1.0,
        excess_return_percent=return_percent - 1.0,
    )
    return HistoricalValidationResult(
        symbol="7203",
        provider="jquants",
        requested_as_of_date=as_of,
        effective_as_of_date=as_of,
        horizon_days=horizon,
        status="ready",
        message="検証完了",
        analysis=analysis,
        actual=outcome,
        direction_matched=predicted is actual,
    )


def test_render_historical_fit_report_compares_both_horizons(tmp_path) -> None:
    as_of = date(2026, 6, 23)
    points = (
        HistoricalValidationPoint(
            as_of,
            (
                _result(as_of, 5, Direction.UP, Direction.UP, 2.0),
                _result(as_of, 20, Direction.FLAT, Direction.DOWN, -4.0),
            ),
        ),
    )

    output_path = render_historical_fit_report(
        "7203",
        "jquants",
        points,
        tmp_path,
        display_name="トヨタ自動車 <普通株>",
    )

    assert output_path.name == "7203-historical-fit-20260623-20260623.html"
    content = output_path.read_text(encoding="utf-8")
    assert "過去当てはめ実績" in content
    assert "スイング・5営業日後" in content
    assert "中長期・20営業日後" in content
    assert "方向一致率" in content
    assert "2026-06-23" in content
    assert "一致" in content
    assert "不一致" in content
    assert "トヨタ自動車 &lt;普通株&gt;" in content
    assert "rule_based_technical v2.6.0" in content
    assert "勝率、利益率、将来確率ではありません" in content
    assert "<script" not in content
