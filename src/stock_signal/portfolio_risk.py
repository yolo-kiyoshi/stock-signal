"""資金制約、集中度と共通日付のリターンによるリスク分析。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from math import sqrt

from stock_signal.domain.market_data import DailyBar

RISK_FIELDS = (
    "total_capital",
    "cash_balance",
    "risk_budget",
    "max_symbol_weight",
    "max_sector_weight",
    "cost_bps",
    "lot_size",
)


def validate_risk_settings(payload: dict) -> dict:
    """有限値と制約関係を検証し、金額の精度を保って保存する。"""
    if not isinstance(payload, dict) or set(payload) != set(RISK_FIELDS):
        raise ValueError(
            "総資産・現金・許容損失・銘柄上限・業種上限・片道コスト・売買単位を指定してください"
        )
    try:
        values = {key: Decimal(str(payload[key])) for key in RISK_FIELDS}
    except (InvalidOperation, ValueError) as error:
        raise ValueError("設定は有限の数値で指定してください") from error
    if any(not x.is_finite() or x < 0 or x > Decimal("1e15") for x in values.values()):
        raise ValueError("設定は0以上の有限値で指定してください")
    if not (
        values["total_capital"] > 0
        and values["cash_balance"] <= values["total_capital"]
        and values["risk_budget"] <= values["total_capital"]
        and 0 < values["max_symbol_weight"] <= values["max_sector_weight"] <= 1
        and values["cost_bps"] <= 1000
        and 1 <= values["lot_size"] <= 10000
        and values["lot_size"] == values["lot_size"].to_integral_value()
    ):
        raise ValueError("資金・比率（0〜1）・売買単位の関係が不正です")
    return {key: str(value) for key, value in values.items()}


def paired_risk(left: list[DailyBar], right: list[DailyBar], as_of: date) -> dict:
    """同じ開始日・終了日の日次調整リターンを60組まで比較する。"""

    def returns(bars):
        bars = sorted(
            (b for b in bars if b.trade_date <= as_of and b.is_adjusted), key=lambda b: b.trade_date
        )
        return {
            (a.trade_date, b.trade_date): float(b.close / a.close - 1)
            for a, b in zip(bars, bars[1:], strict=False)
            if a.close > 0
        }

    a, b = returns(left), returns(right)
    keys = sorted(a.keys() & b.keys())[-60:]
    result = {"correlation": None, "beta": None, "observations": len(keys)}
    if len(keys) < 40:
        return result
    x, y = [a[k] for k in keys], [b[k] for k in keys]
    mx, my = sum(x) / len(x), sum(y) / len(y)
    vx, vy = sum((v - mx) ** 2 for v in x), sum((v - my) ** 2 for v in y)
    cov = sum((u - mx) * (v - my) for u, v in zip(x, y, strict=True))
    if vx > 1e-15 and vy > 1e-15:
        result.update(correlation=max(-1.0, min(1.0, cov / sqrt(vx * vy))), beta=cov / vy)
    return result


def calculate_size(
    settings: dict | None,
    *,
    price: Decimal | None,
    stop: Decimal | None,
    symbol_value: Decimal,
    sector_value: Decimal,
    valuation_complete: bool,
    eligible: bool,
) -> dict:
    """最も厳しい制約で単位株数を切り下げる。損失上限を保証するものではない。"""
    result = {"status": "missing", "max_shares": None, "limits": {}, "cautions": []}
    if settings is None:
        result["cautions"].append("資金設定が未登録のため上限株数は算出していません")
        return result
    if not valuation_complete or price is None or price <= 0:
        result["cautions"].append("価格・業種・保有評価額が不足または古いため上限株数は未算出です")
        return result
    if stop is None or stop <= 0 or stop >= price:
        result["cautions"].append("有効な損切り目安がなく上限株数は未算出です")
        return result
    v = {key: Decimal(value) for key, value in validate_risk_settings(settings).items()}
    cost = v["cost_bps"] / 10000
    purchase = price * (1 + cost)
    loss = price - stop + (price + stop) * cost
    lot = int(v["lot_size"])
    allowances = {
        "cash": v["cash_balance"] / purchase,
        "risk": v["risk_budget"] / loss,
        "symbol": max(Decimal(0), v["total_capital"] * v["max_symbol_weight"] - symbol_value)
        / purchase,
        "sector": max(Decimal(0), v["total_capital"] * v["max_sector_weight"] - sector_value)
        / purchase,
    }
    result["limits"] = {key: int(value // lot) * lot for key, value in allowances.items()}
    result["max_shares"] = min(result["limits"].values()) if eligible else 0
    result["status"] = "calculated" if eligible else "hold"
    result["cautions"].append(
        "終値基準の分析上限です。窓開け・流動性低下で想定損失を超える場合があります"
    )
    if not eligible:
        result["cautions"].append("総合判定が購入候補ではないため新規購入の分析上限は0株です")
    return result
