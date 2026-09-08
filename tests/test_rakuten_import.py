from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from stock_signal.database import list_positions, replace_instruments, upsert_position
from stock_signal.importers.rakuten import RakutenCsvError, parse_rakuten_asset_balance
from stock_signal.web.app import app


def _csv_bytes(*rows: str, encoding: str = "cp932") -> bytes:
    lines = [
        '"■資産合計欄"',
        '"資産合計","1,000"',
        "",
        '"■ 保有商品詳細 (すべて）"',
        '"種別","銘柄コード・ティッカー","銘柄","口座","保有数量",'
        '"［単位］","平均取得価額","［単位］","現在値"',
        *rows,
        "",
        '"■参考為替レート"',
        '"米ドル","150","円/USD"',
    ]
    return ("\r\n".join(lines) + "\r\n").encode(encoding)


def _instrument(symbol: str, name: str) -> dict[str, object]:
    return {
        "symbol": symbol,
        "provider": "jquants",
        "display_name": name,
        "english_name": None,
        "market": "プライム",
        "sector_17_code": "1",
        "sector_17_name": "食品",
        "sector_33_code": "0050",
        "sector_33_name": "水産・農林業",
        "instrument_type": "stock",
        "is_active": True,
        "as_of_date": date(2026, 9, 8),
    }


def test_rakuten_csv_merges_accounts_and_skips_unsupported_assets() -> None:
    content = _csv_bytes(
        '"国内株式","7203","トヨタ自動車","特定","100","株","1,000","円","2,000"',
        '"国内株式","7203","トヨタ自動車","NISA成長投資枠","50","株","1,300","円","2,000"',
        '"国内株式","１８６ａ","アストロスケール","特定","20","株","800","円","900"',
        '"米国株式","TEST","テスト","特定","1","株","10","USD","11"',
        '"投資信託","","インデックス","NISA","1000","口","20,000","円","21,000"',
    )

    result = parse_rakuten_asset_balance(content)

    assert result.encoding == "cp932"
    assert result.detail_rows == 5
    assert result.domestic_rows == 3
    assert result.duplicate_rows == 1
    assert result.skipped_types == {"投資信託": 1, "米国株式": 1}
    assert result.issues == ()
    assert [position.symbol for position in result.positions] == ["186A", "7203"]
    toyota = result.positions[1]
    assert toyota.quantity == Decimal("150")
    assert toyota.average_cost == Decimal("1100.000000")
    assert toyota.account_type == "複数口座（特定・NISA成長投資枠）"


def test_rakuten_csv_reports_invalid_domestic_row_without_guessing() -> None:
    result = parse_rakuten_asset_balance(
        _csv_bytes(
            '"国内株式","7203","トヨタ自動車","特定","数量不明","株","1,000","円","2,000"'
        )
    )

    assert result.positions == ()
    assert result.domestic_rows == 1
    assert result.issues[0].symbol == "7203"
    assert "保有数量" in result.issues[0].message


def test_rakuten_csv_keeps_position_when_average_cost_is_unavailable() -> None:
    result = parse_rakuten_asset_balance(
        _csv_bytes(
            '"国内株式","7203","トヨタ自動車","特定","100","株","-","円","2,000"'
        )
    )

    assert result.issues == ()
    assert result.positions[0].quantity == Decimal("100")
    assert result.positions[0].average_cost is None


def test_rakuten_csv_rejects_unrelated_csv() -> None:
    with pytest.raises(RakutenCsvError, match="保有商品詳細"):
        parse_rakuten_asset_balance(b"code,name\n7203,Toyota\n")


def test_rakuten_csv_preview_and_apply_are_safe_and_idempotent(database_url) -> None:
    replace_instruments(
        database_url,
        "jquants",
        [
            _instrument("7203", "トヨタ自動車"),
            _instrument("186A", "アストロスケールホールディングス"),
            _instrument("6758", "ソニーグループ"),
        ],
    )
    upsert_position(
        database_url,
        symbol="6758",
        provider="jquants",
        display_name="ソニーグループ",
        quantity=Decimal("10"),
        average_cost=Decimal("3000"),
    )
    content = _csv_bytes(
        '"国内株式","7203","CSV上のトヨタ","特定","100","株","2,500","円","2,800"',
        '"国内株式","186A","アストロスケール","NISA成長投資枠","20","株","800","円","900"',
        '"国内株式","9999","マスタ未登録","特定","1","株","100","円","100"',
        '"米国株式","TEST","テスト","特定","1","株","10","USD","11"',
    )

    with TestClient(app) as client:
        preview = client.post(
            "/api/v1/portfolio/imports/rakuten/preview",
            content=content,
            headers={"Content-Type": "text/csv"},
        )
        before_apply = client.get("/api/v1/portfolio/positions").json()["items"]
        applied = client.post(
            "/api/v1/portfolio/imports/rakuten/apply",
            content=content,
            headers={"Content-Type": "text/csv"},
        )
        applied_again = client.post(
            "/api/v1/portfolio/imports/rakuten/apply",
            content=content,
            headers={"Content-Type": "text/csv"},
        )

    assert preview.status_code == 200
    assert preview.json()["summary"] == {
        "detail_rows": 4,
        "domestic_rows": 3,
        "parsed_symbols": 3,
        "importable_symbols": 2,
        "unmatched_symbols": 1,
        "invalid_domestic_rows": 0,
        "ignored_rows": 1,
        "duplicate_rows": 0,
    }
    assert [item["symbol"] for item in before_apply] == ["6758"]
    assert applied.status_code == 200
    assert applied.json()["saved_symbols"] == 2
    assert applied_again.status_code == 200
    positions = list_positions(database_url)
    assert [position.symbol for position in positions] == ["186A", "6758", "7203"]
    assert next(position for position in positions if position.symbol == "7203").display_name == (
        "トヨタ自動車"
    )
    toyota = next(position for position in positions if position.symbol == "7203")
    assert toyota.quantity == Decimal("100")
