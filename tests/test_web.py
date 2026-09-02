from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

import stock_signal.web.app as web_app_module
from stock_signal.ai_review import InvestmentReview, ReviewCitation
from stock_signal.database import (
    replace_earnings_calendar,
    replace_instruments,
    upsert_daily_bars,
)
from stock_signal.domain.market_data import DailyBar, EarningsAnnouncement
from stock_signal.web.app import app


def test_json_api_can_require_bff_bearer_token(monkeypatch) -> None:
    token = "local-test-token-with-at-least-32-characters"
    monkeypatch.setattr(
        web_app_module,
        "settings",
        replace(
            web_app_module.settings,
            api_auth_required=True,
            internal_api_token=token,
        ),
    )
    with TestClient(app) as client:
        unauthorized = client.get("/api/v1/health")
        authorized = client.get(
            "/api/v1/health",
            headers={"Authorization": f"Bearer {token}"},
        )
        removed_legacy_page = client.get("/")

    assert unauthorized.status_code == 401
    assert unauthorized.json()["detail"] == "API認証が必要です"
    assert unauthorized.headers["www-authenticate"] == "Bearer"
    assert authorized.status_code == 200
    assert removed_legacy_page.status_code == 404


def test_ai_review_capability_is_explicit_when_key_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        web_app_module,
        "settings",
        replace(web_app_module.settings, openai_api_key=None),
    )
    with TestClient(app) as client:
        response = client.get("/api/v1/ai-investment-review/capability")

    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is False
    assert payload["status"] == "not_configured"
    assert payload["model"] == web_app_module.settings.openai_model
    assert payload["max_output_tokens"] == 6_000
    assert "利用料金" in payload["notice"]


def test_ai_review_execution_is_rejected_before_api_key_configuration(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        web_app_module,
        "settings",
        replace(web_app_module.settings, openai_api_key=None),
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/instruments/7203/ai-investment-review?horizon=5"
        )

    assert response.status_code == 503
    assert "OPENAI_API_KEY" in response.json()["detail"]


def test_ai_review_endpoint_combines_saved_analysis_and_citations(
    database_url,
    monkeypatch,
) -> None:
    replace_instruments(
        database_url,
        "jquants",
        [_instrument("7203", "トヨタ自動車")],
    )
    upsert_daily_bars(
        database_url,
        [
            DailyBar(
                symbol="7203",
                trade_date=date(2026, 7, 16) + timedelta(days=index),
                open=Decimal(str(2_800 + index)),
                high=Decimal(str(2_810 + index)),
                low=Decimal(str(2_790 + index)),
                close=Decimal(str(2_805 + index)),
                volume=1_000_000,
                provider="jquants",
                is_adjusted=True,
            )
            for index in range(40)
        ],
    )

    class FakeReviewService:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def review(self, **kwargs) -> InvestmentReview:
            assert kwargs["technical_context"]["score_is_probability"] is False
            report_text = "公式情報を確認しました。[出典]"
            citation_start = report_text.index("[出典]")
            return InvestmentReview(
                symbol="7203",
                display_name="トヨタ自動車",
                horizon_days=5,
                technical_as_of_date=kwargs["technical_context"]["as_of_date"],
                generated_at="2026-08-25T09:00:00+00:00",
                model="gpt-test",
                response_id="resp_test",
                report_text=report_text,
                citations=(
                    ReviewCitation(
                        citation_start,
                        citation_start + len("[出典]"),
                        "https://example.com/official",
                        "公式情報",
                    ),
                ),
                search_performed=True,
            )

    monkeypatch.setattr(
        web_app_module,
        "settings",
        replace(web_app_module.settings, openai_api_key="test-key"),
    )
    monkeypatch.setattr(
        web_app_module,
        "OpenAIInvestmentReviewService",
        FakeReviewService,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/instruments/7203/ai-investment-review"
            "?horizon=5&provider=jquants"
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["search_performed"] is True
    assert payload["report_segments"][1]["citation"]["url"] == (
        "https://example.com/official"
    )


def test_daily_bar_api_returns_aligned_chart_indicators(database_url) -> None:
    upsert_daily_bars(
        database_url,
        [
            DailyBar(
                symbol="7203",
                trade_date=date(2026, 4, 1) + timedelta(days=index),
                open=Decimal(str(2_800 + index)),
                high=Decimal(str(2_810 + index)),
                low=Decimal(str(2_790 + index)),
                close=Decimal(str(2_805 + index)),
                volume=1_000_000 + index * 1_000,
                provider="jquants",
                is_adjusted=True,
            )
            for index in range(100)
        ],
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/instruments/7203/daily-bars?range=1m&provider=jquants"
        )

    assert response.status_code == 200
    payload = response.json()
    bars = payload["bars"]
    indicators = payload["indicators"]
    assert bars
    assert set(indicators["moving_averages"]) == {"5", "20", "60"}
    assert set(indicators["rsi"]) == {"14", "28"}
    assert set(indicators["resistance_bands"]) == {"60", "120"}
    assert all(
        len(series) == len(bars)
        for series in indicators["moving_averages"].values()
    )
    assert all(len(series) == len(bars) for series in indicators["rsi"].values())
    # 表示期間より前の日足も計算するため、先頭から指標を描画できる。
    assert indicators["moving_averages"]["60"][0] is not None
    assert indicators["rsi"]["28"][0] is not None
    assert indicators["definitions"]["score_is_probability"] is False


def test_candidates_are_rule_based_and_not_probabilities() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/candidates?horizon=5")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"ready", "no_candidates"}
    assert payload["method"] == "rule_based"
    assert "確率ではありません" in payload["notice"]


def test_latest_analysis_returns_factors_when_data_exists() -> None:
    with TestClient(app) as client:
        watchlist = client.get("/api/v1/watchlist").json()["items"]
        if not watchlist:
            return
        response = client.get(
            f"/api/v1/instruments/{watchlist[0]['symbol']}/analysis/latest?horizon=5"
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["score_is_probability"] is False
    assert payload["engine"]["id"] == "rule_based_technical"
    assert payload["horizon_profile"]["key"] == "swing"
    assert payload["horizon_profile"]["holding_period"] == "数日〜数週間"
    assert set(payload["scores"]) == {"up", "flat", "down"}
    if payload["status"] != "ready":
        assert payload["transition_readiness"] is None
        return
    assert payload["investment_decision"]["score_is_probability"] is False
    assert payload["investment_decision"]["action"] in {
        "buy_candidate", "watch", "avoid_new_buy"
    }
    assert payload["investment_decision"]["entry_stage"] in {
        "setup_confirmed", "conditional_entry", "entry_ready",
        "wait_for_pullback", "avoid", "not_applicable",
    }
    assert "execution_risk_reward_ratio" in payload["investment_decision"]
    transition = payload["transition_readiness"]
    assert transition["score_is_probability"] is False
    assert 0 <= transition["satisfied_conditions"] <= transition["total_conditions"]
    assert transition["phase"] in {
        "falling", "bottoming", "preparing", "one_gate_remaining",
        "early_reversal", "breakout_confirmed", "uptrend", "caution",
    }
    assert len(transition["conditions"]) >= 4
    assert isinstance(payload["patterns"], list)
    assert payload["position_entry"] is None
    if payload["patterns"]:
        lifecycle = payload["patterns"][0]["lifecycle"]
        assert lifecycle["status"] in {
            "entry_window", "overextended", "monitoring", "weakening",
            "target_reached", "failed", "expired"
        }
        assert lifecycle["maximum_monitoring_days"] == 20
        assert "breakout_distance_atr" in lifecycle
        assert "target_progress_percent" in lifecycle
        assert "remaining_risk_reward_ratio" in lifecycle
        assert "execution_stop_price" in lifecycle
        assert "execution_risk_reward_ratio" in lifecycle
    assert {check["key"] for check in payload["equity_checks"]} >= {
        "data_freshness", "volume_ratio", "gap_atr", "liquidity_score", "market_trend_score",
        "relative_strength", "relative_strength_short", "beta_topix",
        "sector_trend_score", "days_to_earnings",
        "disclosure_event",
    }


def test_invalid_horizon_is_rejected() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/candidates?horizon=3")

    assert response.status_code == 422


def test_invalid_candidate_action_is_rejected() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/candidates?action=short_sell")

    assert response.status_code == 422


def test_light_plan_capabilities_are_explicit() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/data-plan")

    assert response.status_code == 200
    payload = response.json()
    assert payload["plan"] == "light"
    assert payload["rate_limit_per_minute"] == 60
    capabilities = {item["key"]: item for item in payload["capabilities"]}
    assert capabilities["daily_prices"]["status"] == "enabled"
    assert capabilities["bulk_daily_prices"]["status"] == "pending_sync"
    assert capabilities["sector_index"]["status"] == "plan_unavailable"
    assert capabilities["tdnet"]["status"] == "addon_required"


def test_invalid_security_code_registration_is_rejected() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/watchlist/registrations", json={"symbol": "12-4"}
        )

    assert response.status_code == 422
    assert "4文字の半角英数字" in response.json()["detail"]


def test_removing_unknown_watchlist_item_returns_not_found() -> None:
    with TestClient(app) as client:
        response = client.delete(
            "/api/v1/watchlist/ZZZZ?provider=jquants"
        )

    assert response.status_code == 404


def _instrument(symbol: str, name: str) -> dict[str, object]:
    return {
        "symbol": symbol,
        "provider": "jquants",
        "display_name": name,
        "english_name": None,
        "market": "プライム",
        "sector_17_code": "9",
        "sector_17_name": "電機・精密",
        "sector_33_code": "3650",
        "sector_33_name": "電気機器",
        "instrument_type": "stock",
        "is_active": True,
        "as_of_date": date(2026, 8, 14),
    }


def test_bulk_watchlist_registration_uses_synced_master(database_url) -> None:
    replace_instruments(
        database_url,
        "jquants",
        [_instrument("7203", "トヨタ自動車")],
    )
    upsert_daily_bars(
        database_url,
        [
            DailyBar(
                symbol="7203",
                trade_date=date(2026, 7, 21) + timedelta(days=index),
                open=Decimal(str(2800 + index)),
                high=Decimal(str(2810 + index)),
                low=Decimal(str(2790 + index)),
                close=Decimal(str(2805 + index)),
                volume=1_000_000,
                provider="jquants",
                is_adjusted=True,
            )
            for index in range(25)
        ],
    )
    next_earnings = date.today() + timedelta(days=5)
    replace_earnings_calendar(
        database_url,
        [EarningsAnnouncement("7203", next_earnings, "トヨタ自動車")],
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/watchlists/ウォッチ/items/bulk",
            json={"symbols": ["7203", "186A"]},
        )
        watchlist = client.get("/api/v1/watchlist").json()["items"]
        analysis = client.get(
            "/api/v1/instruments/7203/analysis/latest?horizon=5&provider=jquants"
        ).json()

    assert response.status_code == 202
    assert response.json()["added"] == ["7203"]
    assert response.json()["pending"] == ["186A"]
    assert [item["symbol"] for item in watchlist] == ["7203"]
    assert watchlist[0]["sector_33_code"] == "3650"
    assert watchlist[0]["sector_33_name"] == "電気機器"
    assert watchlist[0]["market"] == "プライム"
    assert watchlist[0]["instrument_type"] == "stock"
    assert watchlist[0]["sector_17_name"] == "電機・精密"
    assert watchlist[0]["days_to_earnings"] == 5
    assert watchlist[0]["next_earnings_date"] == next_earnings.isoformat()
    assert watchlist[0]["liquidity_rank"] == "very_high"
    assert watchlist[0]["freshness_status"] in {"fresh", "stale"}
    assert analysis["status"] == "ready"


def test_position_can_be_saved_without_adding_watchlist(database_url) -> None:
    replace_instruments(
        database_url,
        "jquants",
        [_instrument("6758", "ソニーグループ")],
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/portfolio/positions",
            json={"symbol": "6758", "quantity": "100", "average_cost": "3500"},
        )
        positions = client.get("/api/v1/portfolio/positions").json()["items"]
        watchlist = client.get("/api/v1/watchlist").json()["items"]

    assert response.status_code == 200
    assert positions[0]["symbol"] == "6758"
    assert positions[0]["sector_33_code"] == "3650"
    assert positions[0]["sector_33_name"] == "電気機器"
    assert positions[0]["market"] == "プライム"
    assert positions[0]["sector_17_name"] == "電機・精密"
    assert positions[0]["liquidity_rank"] == "unknown"
    assert positions[0]["freshness_status"] == "missing"
    assert watchlist == []
