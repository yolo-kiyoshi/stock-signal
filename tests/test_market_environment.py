from datetime import UTC, date, datetime

import pytest

from stock_signal.database import (
    load_latest_market_regime_snapshot,
    save_market_regime_snapshot,
)
from stock_signal.domain.analysis import (
    AnalysisResult,
    Direction,
    EntryStage,
    InvestmentAction,
    InvestmentDecision,
)
from stock_signal.domain.market_environment import MarketObservation, MarketRegime
from stock_signal.market_environment import (
    RuleBasedMarketRegimeEngine,
    apply_market_regime_gate,
    latest_market_regime_for_analysis,
)
from stock_signal.providers.alpha_vantage import AlphaVantageProvider
from stock_signal.providers.base import MarketDataResponseError


def _observation(
    key: str,
    label: str,
    value: float,
    previous: float,
    unit: str = "%",
) -> MarketObservation:
    return MarketObservation(
        indicator_key=key,
        label=label,
        observation_date=date(2026, 9, 1),
        value=value,
        previous_value=previous,
        unit=unit,
        source="test",
    )


def _severe_snapshot():
    return RuleBasedMarketRegimeEngine().evaluate(
        [
            _observation("spy", "S&P 500 ETF", 99.0, 100.0, "USD"),
            _observation("qqq", "NASDAQ 100 ETF", 98.5, 100.0, "USD"),
            _observation("wti", "WTI原油", 94.5, 90.0, "USD/barrel"),
            _observation("us10y", "米10年債利回り", 4.79, 4.73),
            _observation("usdjpy", "ドル円", 150.0, 149.5, "JPY"),
        ],
        decision_at=datetime(2026, 9, 2, 8, 30, tzinfo=UTC),
    )


def test_market_regime_detects_combined_external_shock() -> None:
    snapshot = _severe_snapshot()

    assert snapshot.regime == MarketRegime.SEVERE
    assert snapshot.risk_score >= 50
    assert snapshot.coverage_ratio == 1
    assert {item.key for item in snapshot.components} == {
        "us_equities",
        "oil",
        "us_yield",
        "fx",
    }


def test_market_regime_is_unavailable_when_coverage_is_too_low() -> None:
    snapshot = RuleBasedMarketRegimeEngine().evaluate(
        [_observation("spy", "S&P 500 ETF", 99, 100, "USD")],
        decision_at=datetime(2026, 9, 2, 8, 30, tzinfo=UTC),
    )

    assert snapshot.regime == MarketRegime.UNAVAILABLE
    assert snapshot.coverage_ratio == 0.2


def test_severe_regime_gates_new_buy_without_changing_direction() -> None:
    decision = InvestmentDecision(
        action=InvestmentAction.BUY_CANDIDATE,
        evidence_score=80,
        summary="テクニカル条件を満たしています",
        reasons=("上昇条件",),
        cautions=(),
        entry_stage=EntryStage.ENTRY_READY,
    )
    result = AnalysisResult(
        symbol="7203",
        as_of_date="2026-09-02",
        horizon_days=5,
        direction=Direction.UP,
        scores={Direction.UP: 70, Direction.FLAT: 20, Direction.DOWN: 10},
        factors=(),
        engine_id="test",
        engine_version="test",
        investment_decision=decision,
    )

    gated = apply_market_regime_gate(result, _severe_snapshot())

    assert gated.direction == Direction.UP
    assert gated.investment_decision is not None
    assert gated.investment_decision.action == InvestmentAction.WATCH
    assert gated.investment_decision.entry_stage == EntryStage.CONDITIONAL_ENTRY
    assert "外部環境" in gated.investment_decision.summary


def test_market_regime_snapshot_round_trip(database_url) -> None:
    snapshot = _severe_snapshot()
    save_market_regime_snapshot(database_url, snapshot)

    loaded = load_latest_market_regime_snapshot(
        database_url,
        on_or_before=date(2026, 9, 2),
    )

    assert loaded is not None
    assert loaded.regime == MarketRegime.SEVERE
    assert loaded.observations[0].indicator_key == "spy"
    assert loaded.components[0].key == "us_equities"
    assert latest_market_regime_for_analysis(database_url, date(2026, 9, 2)) is not None
    assert latest_market_regime_for_analysis(database_url, date(2026, 9, 3)) is None


class FakeHttpClient:
    def __init__(self, responses) -> None:
        self.responses = list(responses)

    def get_json(self, _url, _params, _timeout):
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_alpha_vantage_fetches_preopen_indicators() -> None:
    def equity(current, previous):
        return {
            "Time Series (Daily)": {
                "2026-09-01": {"4. close": str(current)},
                "2026-08-31": {"4. close": str(previous)},
            }
        }

    def macro(current, previous):
        return {
            "data": [
                {"date": "2026-09-01", "value": str(current)},
                {"date": "2026-08-31", "value": str(previous)},
            ]
        }
    fx = {
        "Time Series FX (Daily)": {
            "2026-09-01": {"4. close": "150.0"},
            "2026-08-31": {"4. close": "149.0"},
        }
    }
    provider = AlphaVantageProvider(
        "test",
        http_client=FakeHttpClient(
            [equity(99, 100), equity(98, 100), macro(94, 90), macro(4.8, 4.7), fx]
        ),
    )

    observations = provider.fetch_market_environment()

    assert [item.indicator_key for item in observations] == [
        "spy",
        "qqq",
        "wti",
        "us10y",
        "usdjpy",
    ]
    assert observations[2].change_percent is not None
    assert observations[3].change_value == pytest.approx(0.1)


def test_alpha_vantage_keeps_available_indicators_when_one_fails() -> None:
    equity = {
        "Time Series (Daily)": {
            "2026-09-01": {"4. close": "99"},
            "2026-08-31": {"4. close": "100"},
        }
    }
    macro = {
        "data": [
            {"date": "2026-09-01", "value": "90"},
            {"date": "2026-08-31", "value": "89"},
        ]
    }
    fx = {
        "Time Series FX (Daily)": {
            "2026-09-01": {"4. close": "150"},
            "2026-08-31": {"4. close": "149"},
        }
    }
    provider = AlphaVantageProvider(
        "test",
        http_client=FakeHttpClient(
            [
                equity,
                {"unexpected": "QQQだけ取得失敗"},
                macro,
                macro,
                fx,
            ]
        ),
    )

    observations = provider.fetch_market_environment()

    assert "spy" in {item.indicator_key for item in observations}
    assert "qqq" not in {item.indicator_key for item in observations}


def test_alpha_vantage_fails_when_all_indicators_are_unavailable() -> None:
    provider = AlphaVantageProvider(
        "test",
        http_client=FakeHttpClient([{"unexpected": True}] * 5),
    )

    with pytest.raises(MarketDataResponseError, match="外部指標"):
        provider.fetch_market_environment()
