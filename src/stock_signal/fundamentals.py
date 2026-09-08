"""Light財務サマリーを開示時点に沿って評価する。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation


def number(value: object) -> float | None:
    """欠損・非有限値を計算へ入れない。"""
    try:
        result = Decimal(str(value))
        return float(result) if result.is_finite() and abs(result) < Decimal("1e100") else None
    except InvalidOperation:
        return None


def normalize_summary(record: dict) -> dict:
    """APIの識別子と日付を検証し、秘密情報を含まない財務項目だけ保持する。"""
    required = ("Code", "DiscNo", "DiscDate", "DocType")
    if any(not record.get(key) for key in required):
        raise ValueError("財務サマリーの必須項目が不足しています")
    date.fromisoformat(record["DiscDate"])
    fields = required + (
        "DiscTime",
        "CurPerType",
        "CurPerSt",
        "CurPerEn",
        "CurFYSt",
        "CurFYEn",
        "NxtFYEn",
        "Sales",
        "OP",
        "NP",
        "EPS",
        "BPS",
        "EqAR",
        "CFO",
        "CashEq",
        "FEPS",
        "FOP",
        "NxFEPS",
        "NxFOP",
        "ROE",
        "ShEq",
        "RetroRst",
        "SigChgInC",
    )
    result = {key: str(record[key]) for key in fields if record.get(key) is not None}
    code = result["Code"]
    result["Code"] = code[:4] if len(code) == 5 and code.endswith("0") else code
    return result


def _forecast_history(visible: list[dict], as_of: date) -> tuple[list, list[str]]:
    """配当のみの修正を除き、同年度・同じ連結区分の予想変更を保持する。"""
    forecasts, events = [], []
    previous = {}
    for record in visible:
        if (
            "FinancialStatements" not in record["DocType"]
            and "EarnForecastRevision" not in record["DocType"]
        ):
            continue
        for end_key, op_key, eps_key in [
            ("CurFYEn", "FOP", "FEPS"),
            ("NxtFYEn", "NxFOP", "NxFEPS"),
        ]:
            end = record.get(end_key)
            if not end:
                continue
            operating, eps = number(record.get(op_key)), number(record.get(eps_key))
            if end_key == "NxtFYEn" and operating is None and eps is None:
                continue
            key = (end, "NonConsolidated" in record["DocType"])
            old = previous.get(key)
            revision = (
                (operating / old - 1) * 100
                if operating is not None and old is not None and old > 0
                else None
            )
            if (
                revision is not None
                and revision < -0.01
                and (as_of - date.fromisoformat(record["DiscDate"])).days <= 30
            ):
                events.append("直近30日以内に営業利益の会社予想が下方修正されています")
            previous[key] = operating
            if end >= as_of.isoformat():
                forecasts.append((record, end, operating, eps, revision))
    return forecasts, list(dict.fromkeys(events))


def evaluate_financials(
    records: list[dict], as_of: date, price: float | None, *, split_since_report: bool = False
) -> dict:
    """開示当日は使用せず、同じ期間・会計基準の累計値だけ前年と比較する。"""
    visible = sorted(
        (r for r in records if r["DiscDate"] < as_of.isoformat()),
        key=lambda r: (r["DiscDate"], r.get("DiscTime", ""), r["DiscNo"]),
    )
    reports = [r for r in visible if "FinancialStatements" in r["DocType"]]
    result = {
        "status": "missing",
        "as_of_date": as_of.isoformat(),
        "disclosed_date": None,
        "metrics": {},
        "cautions": [],
        "event_status": "unknown",
        "events": [],
    }
    forecasts, result["events"] = _forecast_history(visible, as_of)
    result["event_status"] = "hold" if result["events"] else "unknown"
    if not reports:
        result["cautions"].append("利用可能な決算短信がなく企業品質は未確認です")
        return result
    latest = max(
        reports,
        key=lambda r: (r.get("CurPerEn", ""), r["DiscDate"], r.get("DiscTime", ""), r["DiscNo"]),
    )
    result["disclosed_date"] = latest["DiscDate"]
    age = (as_of - date.fromisoformat(latest["DiscDate"])).days
    result["status"] = "stale" if age > 150 else "partial"
    previous = []
    for r in reports:
        if r.get("CurPerType") != latest.get("CurPerType") or r["DocType"] != latest["DocType"]:
            continue
        try:
            distance = (
                date.fromisoformat(latest["CurPerEn"]) - date.fromisoformat(r["CurPerEn"])
            ).days
            duration = (
                date.fromisoformat(latest["CurPerEn"]) - date.fromisoformat(latest["CurPerSt"])
            ).days
            old_duration = (
                date.fromisoformat(r["CurPerEn"]) - date.fromisoformat(r["CurPerSt"])
            ).days
        except (ValueError, KeyError):
            continue
        if 350 <= distance <= 380 and abs(duration - old_duration) <= 7:
            previous.append(r)
    prior = previous[-1] if previous else {}
    metrics = result["metrics"]
    for key, label in [
        ("Sales", "sales_yoy_percent"),
        ("OP", "op_yoy_percent"),
        ("NP", "profit_yoy_percent"),
        ("EPS", "eps_yoy_percent"),
    ]:
        current, old = number(latest.get(key)), number(prior.get(key))
        metrics[label] = (
            (current / old - 1) * 100
            if current is not None and old is not None and old > 0
            else None
        )
    sales, op, profit = (number(latest.get(k)) for k in ("Sales", "OP", "NP"))
    metrics["operating_margin_percent"] = (
        op / sales * 100 if op is not None and sales and sales > 0 else None
    )
    old_sales, old_op = number(prior.get("Sales")), number(prior.get("OP"))
    metrics["margin_change_pp"] = (
        metrics["operating_margin_percent"] - old_op / old_sales * 100
        if metrics["operating_margin_percent"] is not None
        and old_sales
        and old_sales > 0
        and old_op is not None
        else None
    )
    for key, label in [("EqAR", "equity_ratio_percent"), ("ROE", "roe_percent")]:
        value = number(latest.get(key))
        metrics[label] = value * 100 if value is not None else None
    metrics["operating_cash_flow"] = number(latest.get("CFO"))
    metrics["cash_equivalents"] = number(latest.get("CashEq"))
    # 有効な最も近い年度の最新予想を使い、翌々期との混同を防ぐ。
    target_year = min((row[1] for row in forecasts), default=None)
    matching = [row for row in forecasts if row[1] == target_year]
    forecast = matching[-1] if matching else None
    metrics["forecast_per"] = None
    metrics["forecast_op_revision_percent"] = None
    if forecast:
        r, end, forecast_op, eps, revision = forecast
        metrics["forecast_fiscal_year_end"] = end
        metrics["forecast_op_revision_percent"] = revision
        if price and eps and eps > 0 and not split_since_report:
            metrics["forecast_per"] = price / eps
    bps = number(latest.get("BPS"))
    metrics["pbr"] = price / bps if price and bps and bps > 0 and not split_since_report else None
    if split_since_report:
        metrics["eps_yoy_percent"] = None
        result["cautions"].append(
            "株式分割等の一株指標の整合を確認できないためPER・PBR・EPS成長率は未評価です"
        )
    deteriorating = (
        (op is not None and op <= 0)
        or (profit is not None and profit <= 0)
        or (metrics["op_yoy_percent"] is not None and metrics["op_yoy_percent"] < -10)
    )
    if age > 150:
        result["cautions"].append("財務開示から150日を超えています")
    elif deteriorating:
        result["status"] = "caution"
        result["cautions"].append("赤字または営業利益の前年同期比10%超減少を検出しました")
    elif (
        op is not None
        and profit is not None
        and metrics["sales_yoy_percent"] is not None
        and metrics["op_yoy_percent"] is not None
        and metrics["forecast_per"] is not None
        and metrics["pbr"] is not None
    ):
        result["status"] = "reviewed"
    if metrics.get("forecast_per") is not None and metrics["forecast_per"] > 40:
        result["status"] = "caution"
        result["cautions"].append("会社予想PERが40倍を超えています（業種横断の暫定確認水準）")
    if latest.get("RetroRst") == "true" or latest.get("SigChgInC") == "true":
        result["status"] = "partial"
        result["cautions"].append("修正再表示または連結範囲変更があり単純比較の確認が必要です")
    result["event_status"] = "hold" if result["events"] else "partial"
    result["cautions"].append(
        "財務数値による限定的確認です。適時開示本文・希薄化・事業リスクは未確認です"
    )
    return result
