from datetime import date, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from stock_signal.database import (
    add_watchlist_item,
    replace_instruments,
    upsert_daily_bars,
)
from stock_signal.domain.market_data import DailyBar
from stock_signal.web.app import app


def test_dashboard_is_japanese() -> None:
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "TOMOSHIBIYORI" in response.text
    assert "日足から、明日の判断に小さな灯を。" in response.text
    assert 'lang="ja"' in response.text
    assert 'id="sidebar-toggle"' in response.text
    assert 'id="app-sidebar"' in response.text
    assert 'data-selected-provider="' in response.text
    assert 'role="tablist" aria-label="銘柄一覧の切り替え"' in response.text
    assert 'id="watchlist-panel"' in response.text
    assert 'id="positions-panel"' in response.text
    assert 'id="candidates-panel"' in response.text
    assert 'id="sidebar-search-input"' in response.text
    assert "証券コード・銘柄名で検索" in response.text
    assert 'role="tooltip"' in response.text
    assert "保有銘柄の価格基準について" in response.text
    assert "分析候補の注意事項" in response.text
    assert "証券コードをまとめて追加" in response.text
    assert "市場全体" in response.text
    assert 'id="transition-phase-filter"' in response.text
    assert "あと1条件" in response.text
    assert "J-Quantsの調整済み日足は全市場分を保存しています" in response.text
    assert "流動性上位" in response.text
    assert "Light対象外。Standard以上" in response.text
    assert 'aria-label="運用スタイル"' in response.text
    assert "スイング" in response.text
    assert "中長期の買い場" in response.text
    assert 'data-horizon="1"' not in response.text


def test_hidden_elements_have_priority_over_layout_styles() -> None:
    with TestClient(app) as client:
        response = client.get("/static/dashboard.css")

    assert response.status_code == 200
    assert "[hidden] { display: none !important; }" in response.text
    assert ".watchlist-delete" in response.text
    assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in response.text
    assert "overflow: hidden" in response.text
    assert "overflow-y: auto" in response.text
    assert "scrollbar-gutter: stable" in response.text
    assert ".sidebar-search" in response.text
    assert ".help-tooltip-content" in response.text
    assert "flex-direction: column" in response.text
    assert ".market-watch-add" in response.text
    assert ".market-signal-row" in response.text
    assert ".position-size-calculator" in response.text


def test_market_candidate_watchlist_control_is_wired() -> None:
    with TestClient(app) as client:
        response = client.get("/static/dashboard.js")

    assert response.status_code == 200
    assert '.market-watch-add:not(.is-added)' in response.text
    assert "/api/v1/watchlists/" in response.text
    assert "ウォッチリストへ追加しています" in response.text
    assert "テクニカル方向は現在の値動き" in response.text
    assert "確率ではありません" in response.text
    assert "transition-phase-filter" in response.text
    assert "updateHorizonGuide" in response.text
    assert "企業価値は評価しない" in response.text
    assert "reloadWithSelectedSymbol" in response.text
    assert "許容損失から購入上限" in response.text
    assert "エンジン信頼度レポート" in response.text
    assert "検証実績は未生成" in response.text


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
    transition = payload["transition_readiness"]
    assert transition["score_is_probability"] is False
    assert 0 <= transition["satisfied_conditions"] <= transition["total_conditions"]
    assert transition["phase"] in {
        "falling", "bottoming", "preparing", "one_gate_remaining",
        "early_reversal", "uptrend", "caution",
    }
    assert len(transition["conditions"]) >= 4
    assert isinstance(payload["patterns"], list)
    if payload["patterns"]:
        lifecycle = payload["patterns"][0]["lifecycle"]
        assert lifecycle["status"] in {
            "entry_window", "monitoring", "weakening", "target_reached", "failed", "expired"
        }
        assert lifecycle["maximum_monitoring_days"] == 20
    assert {check["key"] for check in payload["equity_checks"]} >= {
        "data_freshness", "volume_ratio", "gap_atr", "liquidity_score", "market_trend_score",
        "relative_strength", "beta_topix", "sector_trend_score", "days_to_earnings",
        "disclosure_event",
    }


def test_dashboard_can_select_a_newly_added_watchlist_symbol(database_url) -> None:
    add_watchlist_item(
        database_url,
        symbol="7203",
        provider="jquants",
        display_name="トヨタ自動車",
        exchange="プライム",
        currency="JPY",
    )

    with TestClient(app) as client:
        response = client.get("/?symbol=7203")

    assert response.status_code == 200
    assert 'data-selected-symbol="7203"' in response.text


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
    assert watchlist == []
