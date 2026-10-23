"""投資仮説・保有資料・チャットレビューの保存と金融計算を検証する。"""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, insert, select

from stock_signal.cli import main
from stock_signal.database import bulk_upsert_positions, remove_position, upsert_position
from stock_signal.persistence.engine import get_engine
from stock_signal.persistence.schema import (
    daily_bars,
    investment_theses,
    portfolio_imports,
    position_review_contexts,
    position_reviews,
)
from stock_signal.position_journal import (
    JournalConflict,
    ReviewInput,
    ThesisInput,
    create_context,
    export_context,
    journal,
    save_review,
    save_thesis,
    today,
    valuation,
)
from stock_signal.web.app import app


def _record(symbol="7203", quantity="100"):
    return {
        "symbol": symbol,
        "provider": "jquants",
        "display_name": "テスト銘柄",
        "quantity": Decimal(quantity),
        "average_cost": Decimal("1000.25"),
        "account_type": "特定",
    }


def _seed(database_url, *, as_of=True, story=True):
    record = _record()
    bulk_upsert_positions(
        database_url,
        [record],
        import_metadata={
            "source": "rakuten_asset_balance",
            "holdings_as_of": today().isoformat() if as_of else None,
            "imported_positions": [record],
            "missing_positions": [],
            "issues": [],
        },
    )
    with get_engine(database_url).begin() as connection:
        connection.execute(
            insert(daily_bars).values(
                symbol="7203",
                provider="jquants",
                trade_date=today(),
                open=1100,
                high=1200,
                low=1050,
                close=Decimal("550.5"),
                volume=1000,
                is_adjusted=True,
                raw_close=Decimal("1100.75"),
                adjustment_factor=1,
            )
        )
    if story:
        save_thesis(database_url, "7203", "jquants", _thesis())


def _thesis(version=0, story="新事業の利益成長を想定"):
    return ThesisInput(
        expected_version=version,
        story=story,
        horizon="1〜3年",
        change_reason="仮説を記録",
        invalidation_conditions="利益率の低下",
    )


def _review(context, decision="hold"):
    now = datetime.now(UTC)
    return ReviewInput(
        context_id=context["context_id"],
        decision=decision,
        thesis_assessment="unchanged",
        summary="事実を確認した分析情報",
        changes_since_previous="初回レビュー",
        supporting_facts="事業計画",
        opposing_facts="利益率低下の可能性",
        price_and_position="保存済み残高を参照",
        next_conditions="次回決算の利益率",
        unknowns="次期の受注は未確認",
        researched_at=now,
        evidence=[
            {
                "title": "テスト出典",
                "url": "https://example.com/ir",
                "checked_at": now,
                "finding": "テスト用の事業計画",
            }
        ],
    )


def test_valuation_uses_decimal_and_preserves_unknown_cost():
    result = valuation(
        Decimal("3"),
        Decimal("100.10"),
        Decimal("101.25"),
        holdings_date=today(),
        price_date=today(),
        as_of=today(),
        split_since_holdings=False,
    )
    assert result == {"market_value": "303.75", "unrealized_pnl": "3.45", "reason": None}
    result = valuation(
        Decimal("3"),
        None,
        Decimal("101.25"),
        holdings_date=today(),
        price_date=today(),
        as_of=today(),
        split_since_holdings=False,
    )
    assert result["market_value"] == "303.75"
    assert result["unrealized_pnl"] is None


@pytest.mark.parametrize(
    "case", ["missing_date", "missing_price", "old", "future", "split", "before"]
)
def test_valuation_does_not_invent_inconsistent_values(case):
    args = dict(
        quantity=Decimal(100),
        cost=Decimal(1000),
        price=Decimal(1100),
        holdings_date=today(),
        price_date=today(),
        as_of=today(),
        split_since_holdings=False,
    )
    if case == "missing_date":
        args["holdings_date"] = None
    elif case == "missing_price":
        args["price"] = None
    elif case == "old":
        args["holdings_date"] = today() - timedelta(days=10)
        args["price_date"] = today() - timedelta(days=5)
    elif case == "future":
        args["price_date"] = today() + timedelta(days=1)
    elif case == "split":
        args["split_since_holdings"] = True
    else:
        args["price_date"] = today() - timedelta(days=1)
    result = valuation(**args)
    assert result["market_value"] is None
    assert result["unrealized_pnl"] is None
    assert result["reason"]


def test_thesis_history_conflict_and_reimport_preservation(database_url):
    _seed(database_url)
    second = save_thesis(database_url, "7203", "jquants", _thesis(1, "受注の伸びを確認"))
    assert second["version"] == 2
    with pytest.raises(JournalConflict):
        save_thesis(database_url, "7203", "jquants", _thesis(1))
    bulk_upsert_positions(database_url, [_record(quantity="200")])
    history = journal(database_url, "7203", "jquants")["theses"]
    assert [t["version"] for t in history] == [2, 1]
    assert history[1]["payload"]["story"] == "新事業の利益成長を想定"
    remove_position(database_url, "7203", "jquants")
    assert len(journal(database_url, "7203", "jquants")["theses"]) == 2


def test_context_uses_raw_price_and_immutable_snapshot(database_url, tmp_path):
    _seed(database_url)
    context = create_context(database_url, "7203", "jquants")
    assert context["valuation"]["market_value"] == "110075.000000000000"
    assert Decimal(context["valuation"]["unrealized_pnl"]) == Decimal("10050")
    assert context["decision_blockers"] == []
    assert context["technical_snapshots"] == []
    assert "probability" not in context
    target = export_context(context, tmp_path)
    assert json.loads((target / "context.json").read_text()) == context
    with pytest.raises(ValidationError):
        ReviewInput.model_validate_json((target / "review.json").read_text())
    upsert_position(database_url, **_record(quantity="200"))
    save_thesis(database_url, "7203", "jquants", _thesis(1, "更新後"))
    with get_engine(database_url).connect() as connection:
        original = connection.scalar(select(position_review_contexts.c.payload))
    assert original["holding"]["quantity"] == "100.000000"
    assert original["thesis"]["version"] == 1
    with pytest.raises(JournalConflict):
        save_review(database_url, _review(context))


def test_review_idempotency_history_and_freshness(database_url):
    _seed(database_url)
    context = create_context(database_url, "7203", "jquants")
    review = _review(context)
    saved = save_review(database_url, review)
    assert save_review(database_url, review)["id"] == saved["id"]
    changed = review.model_copy(update={"summary": "異なる内容"})
    with pytest.raises(JournalConflict):
        save_review(database_url, changed)
    assert journal(database_url, "7203", "jquants")["reviews"][0]["needs_review"] is False
    save_thesis(database_url, "7203", "jquants", _thesis(1, "新しい仮説"))
    assert journal(database_url, "7203", "jquants")["reviews"][0]["needs_review"] is True
    second = create_context(database_url, "7203", "jquants")
    assert second["previous_review"]["id"] == saved["id"]


@pytest.mark.parametrize("as_of,story", [(False, True), (True, False)])
def test_missing_premises_only_allow_defer(database_url, as_of, story):
    _seed(database_url, as_of=as_of, story=story)
    context = create_context(database_url, "7203", "jquants")
    assert context["decision_blockers"]
    with pytest.raises(ValueError, match="判断保留"):
        save_review(database_url, _review(context))
    assert save_review(database_url, _review(context, "defer"))["payload"]["decision"] == "defer"


def test_missing_csv_holding_blocks_even_if_price_is_present(database_url):
    _seed(database_url)
    record = _record("6758")
    bulk_upsert_positions(
        database_url,
        [record],
        import_metadata={
            "holdings_as_of": today().isoformat(),
            "imported_positions": [record],
            "missing_positions": [{"symbol": "7203", "provider": "jquants"}],
        },
    )
    context = create_context(database_url, "7203", "jquants")
    assert context["missing_from_latest_csv"] is True
    with pytest.raises(ValueError, match="判断保留"):
        save_review(database_url, _review(context))


@pytest.mark.parametrize(
    "field,value",
    [
        ("probability", 0.8),
        ("decision", "buy"),
        ("evidence", []),
        ("researched_at", "2099-01-01T00:00:00+09:00"),
        ("researched_at", "2026-01-01T00:00:00"),
    ],
)
def test_review_input_rejects_unsupported_or_unverified_output(field, value):
    payload = _review({"context_id": "00000000-0000-0000-0000-000000000000"}).model_dump(
        mode="json"
    )
    payload[field] = value
    with pytest.raises(ValidationError):
        ReviewInput.model_validate(payload)


def test_review_rejects_fake_url_and_old_evidence(database_url):
    _seed(database_url)
    context = create_context(database_url, "7203", "jquants")
    payload = _review(context).model_dump(mode="json")
    payload["evidence"][0]["url"] = "javascript:alert(1)"
    with pytest.raises(ValidationError):
        ReviewInput.model_validate(payload)
    payload["evidence"][0]["url"] = "https://example.com/ir"
    payload["evidence"][0]["checked_at"] = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    with pytest.raises(ValueError, match="出典を再確認"):
        save_review(database_url, ReviewInput.model_validate(payload))


def test_journal_api_roundtrip_and_conflict(database_url):
    _seed(database_url, story=False)
    with TestClient(app) as client:
        path = "/api/v1/portfolio/journal/7203"
        assert client.get(path).json() == {"theses": [], "reviews": []}
        payload = _thesis().model_dump(mode="json")
        assert client.post(path + "/theses", json=payload).status_code == 200
        assert client.post(path + "/theses", json=payload).status_code == 409
        context = client.post(path + "/context").json()
        review = _review(context).model_dump(mode="json")
        assert client.post("/api/v1/portfolio/reviews", json=review).status_code == 200
        assert len(client.get(path).json()["reviews"]) == 1
        assert client.post(path + "/theses", json={"story": "不足"}).status_code == 422


def test_journal_api_authentication(database_url, monkeypatch):
    from dataclasses import replace
    from importlib import import_module

    module = import_module("stock_signal.web.app")
    monkeypatch.setattr(
        module,
        "settings",
        replace(module.settings, api_auth_required=True, internal_api_token="test-only-token"),
    )
    with TestClient(app) as client:
        for path in ("journal/7203", "journal/7203/context", "reviews"):
            assert client.post("/api/v1/portfolio/" + path, json={}).status_code == 401
        assert client.get("/api/v1/portfolio/journal/7203").status_code == 401


def test_import_snapshot_rolls_back_with_holdings(database_url, monkeypatch):
    from stock_signal import position_journal

    def fail(*args):
        raise ValueError("履歴保存失敗")

    monkeypatch.setattr(position_journal, "record_import", fail)
    with pytest.raises(ValueError, match="履歴保存失敗"):
        bulk_upsert_positions(database_url, [_record()], import_metadata={})
    with get_engine(database_url).connect() as connection:
        from stock_signal.persistence.schema import positions

        assert connection.scalar(select(func.count()).select_from(positions)) == 0
        assert connection.scalar(select(func.count()).select_from(portfolio_imports)) == 0


def test_cli_context_and_review_roundtrip(database_url, tmp_path, capsys):
    _seed(database_url)
    assert main(["position-context", "7203", "--output-dir", str(tmp_path)]) == 0
    directory = next(tmp_path.iterdir())
    context = json.loads((directory / "context.json").read_text())
    (directory / "review.json").write_text(_review(context).model_dump_json())
    assert main(["position-review-save", "--input", str(directory / "review.json")]) == 0
    assert main(["position-journal", "7203"]) == 0
    assert "テスト銘柄" in (directory / "context.md").read_text()
    with get_engine(database_url).connect() as connection:
        assert connection.scalar(select(func.count()).select_from(position_reviews)) == 1
        assert connection.scalar(select(func.count()).select_from(investment_theses)) == 1


def _risk():
    return {
        "total_capital": "1000000",
        "cash_balance": "100000",
        "risk_budget": "10000",
        "max_symbol_weight": "0.2",
        "max_sector_weight": "0.4",
        "cost_bps": "10",
        "lot_size": "100",
    }


def test_weight_and_increase_require_real_risk_settings(database_url):
    from stock_signal.position_journal import position_weight
    from stock_signal.research_store import save_risk_settings

    assert position_weight("110075", _risk()) == "0.110075"
    assert position_weight("950000", _risk()) is None
    assert position_weight("110075", None) is None
    _seed(database_url)
    context = create_context(database_url, "7203", "jquants")
    with pytest.raises(ValueError, match="資金制約"):
        save_review(database_url, _review(context, "increase"))
    save_risk_settings(database_url, _risk())
    with pytest.raises(JournalConflict):
        save_review(database_url, _review(context))
    context = create_context(database_url, "7203", "jquants")
    assert context["valuation"]["portfolio_weight"] == "0.110075000000"
    assert (
        save_review(database_url, _review(context, "increase"))["payload"]["decision"] == "increase"
    )
    save_risk_settings(database_url, {**_risk(), "max_symbol_weight": "0.1"})
    context = create_context(database_url, "7203", "jquants")
    with pytest.raises(ValueError, match="余力"):
        save_review(database_url, _review(context, "increase"))


def test_csv_preview_diff_and_import_basis_date(database_url):
    from stock_signal.persistence.schema import instruments

    _seed(database_url)
    upsert_position(database_url, **_record("6758", "20"))
    with get_engine(database_url).begin() as connection:
        connection.execute(
            insert(instruments).values(
                symbol="7203",
                provider="jquants",
                display_name="テスト銘柄",
                market="プライム",
                as_of_date=today(),
            )
        )
    content = (
        '"■ 保有商品詳細 (すべて）"\n'
        '"種別","銘柄コード・ティッカー","銘柄","口座","保有数量",'
        '"［単位］","平均取得価額","［単位］","現在値"\n'
        '"国内株式","7203","テスト銘柄","特定","150","株","1200","円","1300"\n'
        '\n"■参考為替レート"\n'
    ).encode("cp932")
    with TestClient(app) as client:
        base = "/api/v1/portfolio/imports/rakuten"
        preview = client.post(base + "/preview", content=content).json()
        assert preview["positions"][0]["previous_quantity"] == "100.000000"
        assert preview["positions"][0]["quantity"] == "150"
        assert preview["missing_positions"][0]["symbol"] == "6758"
        future = (today() + timedelta(days=1)).isoformat()
        assert (
            client.post(base + f"/apply?holdings_as_of={future}", content=content).status_code
            == 422
        )
        applied = client.post(base + f"/apply?holdings_as_of={today()}", content=content)
        assert applied.status_code == 200
        context = create_context(database_url, "7203", "jquants")
        assert context["holdings_as_of"] == today().isoformat()
        assert context["thesis"]["version"] == 1
        assert context["holding"]["quantity"] == "150.000000"
        missing_context = create_context(database_url, "6758", "jquants")
        assert missing_context["missing_from_latest_csv"] is True


def test_manual_holding_change_does_not_reuse_csv_date(database_url):
    _seed(database_url)
    upsert_position(database_url, **_record(quantity="200"))
    context = create_context(database_url, "7203", "jquants")
    assert context["holdings_as_of"] is None
    assert context["valuation"]["market_value"] is None
