"""財務の時点整合、資金制約、欠損時の判定を検証する。"""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from stock_signal.fundamentals import evaluate_financials, normalize_summary, number
from stock_signal.market_environment import RuleBasedMarketRegimeEngine
from stock_signal.portfolio_risk import calculate_size, paired_risk, validate_risk_settings
from stock_signal.providers.base import MarketDataResponseError
from stock_signal.providers.jquants import JQuantsProvider
from stock_signal.research_store import load_disclosures, save_disclosures
from stock_signal.web.app import app


def report(year=2026, **changes):
    return {
        "Code": "72030",
        "DiscNo": str(year),
        "DiscDate": f"{year}-08-01",
        "DocType": "1QFinancialStatements_Consolidated_IFRS",
        "CurPerType": "1Q",
        "CurPerSt": f"{year}-04-01",
        "CurPerEn": f"{year}-06-30",
        "CurFYEn": f"{year + 1}-03-31",
        "Sales": "1000",
        "OP": "100",
        "NP": "60",
        "EPS": "10",
        "BPS": "500",
        "EqAR": "0.4",
        "ROE": "0.12",
        "FOP": "500",
        "FEPS": "100",
        **changes,
    }


def settings(**changes):
    return validate_risk_settings(
        dict(
            total_capital="1000000",
            cash_balance="500000",
            risk_budget="10000",
            max_symbol_weight=".3",
            max_sector_weight=".5",
            cost_bps="10",
            lot_size="100",
            **changes,
        )
    )


def test_financials_use_comparable_yoy_and_company_forecast():
    result = evaluate_financials(
        [report(2025), report(Sales="1200", OP="150")], date(2026, 8, 3), 1000
    )
    assert result["status"] == "reviewed"
    assert result["metrics"]["sales_yoy_percent"] == pytest.approx(20)
    assert result["metrics"]["op_yoy_percent"] == pytest.approx(50)
    assert result["metrics"]["operating_margin_percent"] == 12.5
    assert result["metrics"]["margin_change_pp"] == 2.5
    assert result["metrics"]["forecast_per"] == 10
    assert result["metrics"]["equity_ratio_percent"] == 40
    assert result["metrics"]["roe_percent"] == 12
    assert result["metrics"]["pbr"] == 2


def test_disclosure_day_and_future_are_excluded():
    r = report()
    assert evaluate_financials([r], date(2026, 8, 1), 1000)["status"] == "missing"
    assert evaluate_financials([r], date(2026, 7, 31), 1000)["status"] == "missing"


@pytest.mark.parametrize(
    "changes",
    [
        dict(CurPerType="2Q"),
        dict(CurPerSt="2025-01-01"),
        dict(DocType="1QFinancialStatements_NonConsolidated"),
    ],
)
def test_incomparable_periods_are_not_used_for_growth(changes):
    result = evaluate_financials([report(2025, **changes), report()], date(2026, 8, 3), 1000)
    assert result["metrics"]["sales_yoy_percent"] is None
    assert result["status"] == "partial"


@pytest.mark.parametrize("value", ["", None, "NaN", "Infinity", "-Infinity", "invalid"])
def test_non_finite_or_missing_values(value):
    assert number(value) is None


def test_losses_missing_forecast_and_split_are_not_cheap_values():
    r = evaluate_financials(
        [report(2025, OP="-10"), report(OP="-20", FEPS="-5")],
        date(2026, 8, 3),
        1000,
        split_since_report=True,
    )
    assert r["status"] == "caution"
    assert r["metrics"]["op_yoy_percent"] is None
    assert r["metrics"]["forecast_per"] is None
    assert r["metrics"]["pbr"] is None
    assert r["metrics"]["eps_yoy_percent"] is None


def test_forecast_revision_same_year_and_recent_event():
    revision = report(
        DiscNo="20260802",
        DiscDate="2026-08-02",
        DocType="EarnForecastRevision",
        FOP="400",
        FEPS="80",
    )
    r = evaluate_financials([report(), revision], date(2026, 8, 3), 1000)
    assert r["event_status"] == "hold"
    assert r["metrics"]["forecast_op_revision_percent"] == pytest.approx(-20)
    assert r["disclosed_date"] == "2026-08-01"
    revision["CurFYEn"] = "2028-03-31"
    assert evaluate_financials([report(), revision], date(2026, 8, 3), 1000)["events"] == []


def test_old_period_correction_does_not_replace_newest_period():
    correction = report(2025, DiscNo="later", DiscDate="2026-08-02")
    r = evaluate_financials([report(), correction], date(2026, 8, 3), 1000)
    assert r["disclosed_date"] == "2026-08-01"


def test_disclosures_are_idempotent_and_original_records_are_preserved(database_url):
    save_disclosures(database_url, [report()])
    save_disclosures(database_url, [report(OP="999")])
    records = load_disclosures(database_url, "7203", date(2026, 8, 3))
    assert len(records) == 1
    assert records[0]["OP"] == "100"
    assert load_disclosures(database_url, "7203", date(2026, 8, 1)) == []


def size(**changes):
    args = dict(
        settings=settings(),
        price=Decimal("1000"),
        stop=Decimal("950"),
        symbol_value=Decimal("200000"),
        sector_value=Decimal("300000"),
        valuation_complete=True,
        eligible=True,
    )
    args.update(changes)
    return calculate_size(**args)


def test_size_includes_costs_cash_and_existing_concentration():
    r = size()
    assert r["limits"] == {"cash": 400, "risk": 100, "symbol": 0, "sector": 100}
    assert r["max_shares"] == 0
    r = size(symbol_value=Decimal("0"))
    assert r["max_shares"] == 100


@pytest.mark.parametrize(
    "changes",
    [
        dict(settings=None),
        dict(valuation_complete=False),
        dict(stop=None),
        dict(stop=Decimal("1100")),
    ],
)
def test_unknown_inputs_do_not_invent_share_limits(changes):
    assert size(**changes)["max_shares"] is None


def test_non_candidate_has_zero_new_shares():
    assert size(eligible=False)["max_shares"] == 0


@pytest.mark.parametrize(
    "key,value",
    [
        ("cash_balance", "NaN"),
        ("risk_budget", "-1"),
        ("max_symbol_weight", "2"),
        ("lot_size", "1.5"),
        ("cost_bps", "Infinity"),
        ("total_capital", "0"),
    ],
)
def test_invalid_settings_rejected(key, value):
    payload = settings()
    payload[key] = value
    with pytest.raises(ValueError):
        validate_risk_settings(payload)


def bars_for_risk():
    from stock_signal.domain.market_data import DailyBar

    prices = [Decimal("100")]
    for i in range(65):
        prices.append(prices[-1] * (1 + Decimal(str((i % 7 - 3) / 100))))
    return [
        DailyBar(
            "TEST",
            date(2026, 1, 1) + timedelta(days=i),
            p,
            p,
            p,
            p,
            100,
            "jquants",
            True,
            raw_close=p,
        )
        for i, p in enumerate(prices)
    ]


def test_paired_risk_aligns_return_intervals_and_ignores_future():
    bars = bars_for_risk()
    as_of = bars[-2].trade_date
    result = paired_risk(bars, bars, as_of)
    assert result["observations"] == 60
    assert result["correlation"] == pytest.approx(1)
    assert result["beta"] == pytest.approx(1)
    assert paired_risk(bars[:20], bars, as_of)["beta"] is None
    flat = [replace(b, close=Decimal("100")) for b in bars]
    assert paired_risk(flat, bars, as_of)["correlation"] is None
    gaps = bars[::2]
    assert paired_risk(gaps, bars, as_of)["observations"] == 0


def test_financial_provider_paginates_and_never_uses_premium_cursor():
    class Http:
        calls = []

        def get_json(self, url, params, timeout, *, headers):
            self.calls.append((url, params))
            return (
                {"data": [report()], "pagination_key": "next"}
                if len(self.calls) == 1
                else {"data": [report(2025)]}
            )

    http = Http()
    provider = JQuantsProvider("test", http_client=http, minimum_request_interval=0)
    records = provider.fetch_financial_summaries("7203")
    assert len(records) == 2
    assert records[0]["Code"] == "7203"
    assert http.calls[0][0].endswith("/fins/summary")
    assert http.calls[1][1] == {"code": "7203", "pagination_key": "next"}


def test_financial_provider_rejects_bad_payload():
    class Http:
        def get_json(self, *args, **kwargs):
            return {"data": [{"Code": "72030"}]}

    with pytest.raises(MarketDataResponseError):
        JQuantsProvider(
            "test", http_client=Http(), minimum_request_interval=0
        ).fetch_financial_summaries("7203")
    assert "api_key" not in normalize_summary({**report(), "api_key": "not-for-storage"})


def test_settings_api_round_trip_and_validation():
    with TestClient(app) as client:
        assert client.get("/api/v1/portfolio/risk-settings").json()["settings"] is None
        assert client.put("/api/v1/portfolio/risk-settings", json=settings()).status_code == 200
        assert client.get("/api/v1/portfolio/risk-settings").json()["settings"] == settings()
        assert client.put("/api/v1/portfolio/risk-settings", json={}).status_code == 422


def test_market_observations_from_future_are_excluded():
    from stock_signal.domain.market_environment import MarketObservation

    observation = MarketObservation("spy", "米株", date(2026, 9, 4), 100, 100, "USD", "test")
    snapshot = RuleBasedMarketRegimeEngine().evaluate(
        [observation], decision_at=datetime(2026, 9, 2, 0, 0, tzinfo=UTC)
    )
    assert snapshot.coverage_ratio == 0


def test_financial_failure_is_recorded_without_deleting_history(database_url):
    from stock_signal.research_store import financial_sync_state, sync_financials

    class Provider:
        def fetch_financial_summaries(self, symbol):
            raise MarketDataResponseError('取得失敗')
    save_disclosures(database_url, [report()])
    with pytest.raises(MarketDataResponseError):
        sync_financials(database_url, Provider(), '7203')
    assert financial_sync_state(database_url, '7203')['status'] == 'failed'
    assert len(load_disclosures(database_url, '7203', date(2026, 8, 3))) == 1


def test_long_term_missing_financials_gates_candidate_without_probability(database_url):
    from stock_signal.domain.analysis import (
        AnalysisResult,
        Direction,
        InvestmentAction,
        InvestmentDecision,
    )
    from stock_signal.research import enrich_analysis

    bars = bars_for_risk()
    bars = [replace(b, trade_date=date.today() - timedelta(days=len(bars) - i - 1))
            for i, b in enumerate(bars)]
    result = AnalysisResult('TEST', date.today().isoformat(), 20, Direction.UP,
                            {Direction.UP: 80}, (), 'test', 'test',
                            investment_decision=InvestmentDecision(
                                InvestmentAction.BUY_CANDIDATE, 80, '候補', (), ()))
    enriched = enrich_analysis(database_url, result, bars, provider='jquants')
    assert enriched.investment_decision.action == InvestmentAction.WATCH
    assert enriched.direction == result.direction
    assert enriched.scores == result.scores
    assert enriched.research['portfolio']['max_shares'] is None
    assert enriched.research['financials']['status'] == 'missing'


def test_today_disclosure_holds_swing_even_before_financial_values_are_used(database_url):
    from stock_signal.domain.analysis import (
        AnalysisResult,
        Direction,
        InvestmentAction,
        InvestmentDecision,
    )
    from stock_signal.research import enrich_analysis

    save_disclosures(database_url, [report(DiscDate=date.today().isoformat())])
    bar = replace(bars_for_risk()[-1], symbol='7203', trade_date=date.today())
    result = AnalysisResult('7203', date.today().isoformat(), 5, Direction.UP,
                            {Direction.UP: 80}, (), 'test', 'test',
                            investment_decision=InvestmentDecision(
                                InvestmentAction.BUY_CANDIDATE, 80, '候補', (), ()))
    enriched = enrich_analysis(database_url, result, [bar], provider='jquants')
    assert enriched.investment_decision.action == InvestmentAction.WATCH
    assert enriched.research['financials']['event_status'] == 'hold'
    assert enriched.research['financials']['disclosed_date'] is None
