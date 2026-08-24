from datetime import date, timedelta
from decimal import Decimal

import pytest

from stock_signal.analysis.historical_validation import (
    HistoricalValidationService,
    calculate_realized_outcome,
    classify_realized_direction,
)
from stock_signal.domain.analysis import AnalysisResult, Direction
from stock_signal.domain.market_data import DailyBar


def _bar(trade_date: date, close: float, *, symbol: str = "7203") -> DailyBar:
    price = Decimal(str(close))
    return DailyBar(
        symbol=symbol,
        trade_date=trade_date,
        open=price,
        high=price + Decimal("1"),
        low=price - Decimal("1"),
        close=price,
        volume=1_000_000,
        provider="jquants",
        is_adjusted=True,
    )


def _business_dates(start: date, count: int) -> list[date]:
    dates = []
    current = start
    while len(dates) < count:
        if current.weekday() < 5:
            dates.append(current)
        current += timedelta(days=1)
    return dates


@pytest.mark.parametrize(
    ("move_atr", "expected"),
    [
        (0.5, Direction.UP),
        (0.49, Direction.FLAT),
        (-0.49, Direction.FLAT),
        (-0.5, Direction.DOWN),
    ],
)
def test_realized_direction_uses_atr_boundary(
    move_atr: float,
    expected: Direction,
) -> None:
    assert classify_realized_direction(move_atr, 0.5) is expected


def test_realized_outcome_uses_as_of_atr_and_market_return() -> None:
    dates = _business_dates(date(2026, 1, 5), 26)
    history = [_bar(day, 100 + index * 0.1) for index, day in enumerate(dates[:25])]
    target = _bar(dates[25], 105)
    market_start = _bar(dates[24], 2000, symbol="TOPIX")
    market_target = _bar(dates[25], 2020, symbol="TOPIX")

    outcome = calculate_realized_outcome(
        history,
        target,
        0.5,
        market_start=market_start,
        market_target=market_target,
    )

    assert outcome is not None
    assert outcome.target_date == dates[25]
    assert outcome.direction is Direction.UP
    assert outcome.return_percent > 0
    assert outcome.market_return_percent == 1.0
    assert outcome.excess_return_percent == pytest.approx(
        outcome.return_percent - 1.0,
        abs=0.001,
    )


def test_historical_service_does_not_pass_future_bars_to_engine(monkeypatch) -> None:
    dates = _business_dates(date(2026, 1, 5), 45)
    bars = [_bar(day, 100 + index * 0.2) for index, day in enumerate(dates)]
    requested = dates[30]
    captured_dates: list[date] = []

    class SpyEngine:
        def __init__(self, **_):
            pass

        def analyze(self, symbol, history, horizon_days, context):
            captured_dates.extend(bar.trade_date for bar in history)
            return AnalysisResult(
                symbol=symbol,
                as_of_date=history[-1].trade_date.isoformat(),
                horizon_days=horizon_days,
                direction=Direction.UP,
                scores={
                    Direction.UP: 70.0,
                    Direction.FLAT: 20.0,
                    Direction.DOWN: 10.0,
                },
                factors=(),
                engine_id="spy",
                engine_version="1.0.0",
            )

    def fake_load(_database_url, symbol, **_kwargs):
        return [] if symbol == "TOPIX" else bars

    monkeypatch.setattr(
        "stock_signal.analysis.historical_validation.RuleBasedAnalysisEngine",
        SpyEngine,
    )
    monkeypatch.setattr(
        "stock_signal.analysis.historical_validation.load_daily_bars",
        fake_load,
    )

    result = HistoricalValidationService("unused").validate(
        "7203",
        requested,
        5,
        "jquants",
    )

    assert result.status == "ready"
    assert max(captured_dates) == requested
    assert all(day <= requested for day in captured_dates)
    assert result.actual is not None
    assert result.actual.target_date == dates[35]


def test_non_trading_date_resolves_to_previous_trading_day(monkeypatch) -> None:
    dates = _business_dates(date(2026, 1, 5), 45)
    bars = [_bar(day, 100 + index * 0.2) for index, day in enumerate(dates)]
    friday = next(day for day in dates[25:] if day.weekday() == 4)
    requested_saturday = friday + timedelta(days=1)

    def fake_load(_database_url, symbol, **_kwargs):
        return [] if symbol == "TOPIX" else bars

    monkeypatch.setattr(
        "stock_signal.analysis.historical_validation.load_daily_bars",
        fake_load,
    )

    result = HistoricalValidationService("unused").validate(
        "7203",
        requested_saturday,
        5,
        "jquants",
    )

    assert result.effective_as_of_date == friday
    assert result.status == "ready"


def test_recent_as_of_reports_that_future_data_is_insufficient(monkeypatch) -> None:
    dates = _business_dates(date(2026, 1, 5), 35)
    bars = [_bar(day, 100 + index * 0.2) for index, day in enumerate(dates)]

    def fake_load(_database_url, symbol, **_kwargs):
        return [] if symbol == "TOPIX" else bars

    monkeypatch.setattr(
        "stock_signal.analysis.historical_validation.load_daily_bars",
        fake_load,
    )

    result = HistoricalValidationService("unused").validate(
        "7203",
        dates[-3],
        5,
        "jquants",
    )

    assert result.status == "insufficient_future_data"
    assert result.analysis is not None
    assert result.actual is None


def test_validate_range_loads_stock_and_market_data_only_once(monkeypatch) -> None:
    dates = _business_dates(date(2025, 10, 1), 100)
    bars = [_bar(day, 100 + index * 0.2) for index, day in enumerate(dates)]
    load_calls: list[str] = []
    progress: list[tuple[int, int, date]] = []

    class StubEngine:
        def __init__(self, **_):
            pass

        def analyze(self, symbol, history, horizon_days, context):
            return AnalysisResult(
                symbol=symbol,
                as_of_date=history[-1].trade_date.isoformat(),
                horizon_days=horizon_days,
                direction=Direction.UP,
                scores={
                    Direction.UP: 70.0,
                    Direction.FLAT: 20.0,
                    Direction.DOWN: 10.0,
                },
                factors=(),
                engine_id="stub",
                engine_version="1.0.0",
            )

    def fake_load(_database_url, symbol, **_kwargs):
        load_calls.append(symbol)
        return [] if symbol == "TOPIX" else bars

    monkeypatch.setattr(
        "stock_signal.analysis.historical_validation.RuleBasedAnalysisEngine",
        StubEngine,
    )
    monkeypatch.setattr(
        "stock_signal.analysis.historical_validation.load_daily_bars",
        fake_load,
    )

    points = HistoricalValidationService("unused").validate_range(
        "7203",
        start=dates[60],
        end=dates[70],
        provider="jquants",
        on_progress=lambda completed, total, as_of: progress.append(
            (completed, total, as_of)
        ),
    )

    assert load_calls == ["7203", "TOPIX"]
    assert len(points) == 11
    assert {result.horizon_days for result in points[0].results} == {5, 20}
    assert all(result.status == "ready" for point in points for result in point.results)
    assert progress == [(1, 11, dates[60]), (11, 11, dates[70])]
