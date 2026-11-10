"""BPSの出典と、過去値を利用するための確認範囲を管理する。"""

import hashlib
import json
import os
from datetime import date, timedelta


def bps_limits() -> tuple[int, int]:
    """基準日からの参照期限と手動確認の期限を設定から読む。"""
    age = int(os.getenv("BPS_MAX_AGE_DAYS", "450"))
    review = int(os.getenv("BPS_REVIEW_MAX_AGE_DAYS", "30"))
    if not 1 <= age <= 730 or not 1 <= review <= 90:
        raise ValueError("BPS参照期限は1〜730日、確認期限は1〜90日で指定してください")
    return age, review


def resolve_bps(
    reports: list[dict], latest: dict, as_of: date, *,
    capital_events: list[str] | None = None, coverage_start: date | None = None,
    review: dict | None = None, max_age_days: int = 450, review_max_age_days: int = 30,
    context_records: list[dict] | None = None,
) -> dict:
    """過去値を無条件に補完せず、出典・期限・確認根拠を返す。"""
    from stock_signal.fundamentals import number

    visible = sorted(
        (r for r in reports if r["DiscDate"] < as_of.isoformat()),
        key=lambda r: (r.get("CurPerEn", ""), r["DiscDate"], r.get("DiscTime", ""), r["DiscNo"]),
    )
    # 四半期接頭辞だけを除き、連結区分と会計基準は完全一致を要求する。
    basis = latest["DocType"].split("FinancialStatements", 1)[-1]
    compatible = [r for r in visible
                  if r["DocType"].split("FinancialStatements", 1)[-1] == basis
                  and r.get("CurPerEn", "") <= latest.get("CurPerEn", "")
                  and (number(r.get("BPS")) or 0) > 0]
    current = number(latest.get("BPS"))
    source = latest if current is not None else (compatible[-1] if compatible else None)
    result = {
        "status": "missing", "value": None, "source_value": None,
        "source_disclosure_no": None, "source_disclosed_date": None,
        "source_period_end": None, "source_doc_type": None,
        "latest_disclosure_no": latest["DiscNo"], "max_age_days": max_age_days,
        "review_max_age_days": review_max_age_days, "reviewable": False,
        "reason": "同じ連結区分・会計基準の正のBPSがありません",
        "valid_until": None,
        "review": review, "review_status": "none" if review is None else "invalidated",
    }
    if source is None:
        return result
    value = number(source.get("BPS"))
    result.update(source_value=value, source_disclosure_no=source["DiscNo"],
                  source_disclosed_date=source["DiscDate"],
                  source_period_end=source.get("CurPerEn"), source_doc_type=source["DocType"])
    fingerprint = hashlib.sha256(json.dumps(
        {"reports": sorted(context_records or visible, key=lambda r: r["DiscNo"]),
         "capital_events": sorted(capital_events or []),
         "source": source["DiscNo"], "max_age_days": max_age_days},
        sort_keys=True, ensure_ascii=False,
    ).encode()).hexdigest()
    result["fingerprint"] = fingerprint
    try:
        period_end = date.fromisoformat(source["CurPerEn"])
    except (ValueError, KeyError):
        result["reason"] = "BPSの基準日を確認できません"
        return result
    if not value or value <= 0:
        result["reason"] = "最新決算のBPSが正ではないため過去値で代用しません"
        return result
    if not 0 <= (as_of - period_end).days <= max_age_days:
        result.update(status="expired", reason="BPSの基準日が参照期限外です")
        return result
    result["valid_until"] = (period_end + timedelta(days=max_age_days)).isoformat()
    if source == latest:
        result.update(status="current", value=value, reason="最新決算に記載されたBPSです")
        return result
    source_order = (source["CurPerEn"], source["DiscDate"], source.get("DiscTime", ""),
                    source["DiscNo"])
    subsequent = [r for r in visible if (
        r.get("CurPerEn", ""), r["DiscDate"], r.get("DiscTime", ""), r["DiscNo"],
    ) > source_order]
    blockers = []
    for key, label in (
        ("RetroRst", "修正再表示"), ("SigChgInC", "連結範囲変更"),
        ("MatChgSub", "重要な子会社の異動"), ("ChgByASRev", "会計基準改正による変更"),
        ("ChgNoASRev", "会計方針変更"), ("ChgAcEst", "会計上の見積り変更"),
    ):
        if any(r.get(key) == "true" for r in subsequent):
            blockers.append(label)
    if any(r["DocType"].split("FinancialStatements", 1)[-1] != basis for r in subsequent):
        blockers.append("連結区分・会計基準の変更")
    if any(number(r.get("NP")) is not None and number(r["NP"]) < 0 for r in subsequent):
        blockers.append("純利益の赤字")
    for key, label in (("ShOutFY", "発行済株式数"), ("TrShFY", "自己株式数"), ("Cap", "資本金")):
        values = {number(r.get(key)) for r in [source, *subsequent]} - {None}
        if len(values) > 1:
            blockers.append(f"{label}の変化")
    changed = any(event[:10] > source["CurPerEn"] for event in capital_events or [])
    if changed:
        blockers.append("株価調整係数の変化（分割等）")
    if blockers:
        result.update(status="blocked", reason="・".join(blockers) + "を検出したため、"
                      "過去BPSは採用しません。新しい基準のBPSを確認してください")
        return result
    result.update(status="review_required", reviewable=True,
                  reason="過去BPSの基準日以降の分割・増減資・自己株式・連結区分と"
                  "純資産の重要な変化を資料で確認してください")
    # 同じ貸借対照表基準日の別開示だけは、日足範囲の確認後に自動参照できる。
    same_balance = all(
        number(source.get(key)) is not None
        and number(source.get(key)) == number(latest.get(key))
        for key in ("ShEq", "ShOutFY", "TrShFY")
    )
    if (source["CurPerEn"] == latest.get("CurPerEn") and capital_events is not None
            and coverage_start is not None and coverage_start <= period_end and same_balance):
        result.update(status="same_period", value=value, reviewable=False,
                      reason="同じ基準日・連結区分・会計基準の開示BPSを参照しています")
    elif review and review.get("fingerprint") == fingerprint:
        checked = date.fromisoformat(review["checked_on"])
        if 0 <= (as_of - checked).days <= review_max_age_days:
            result.update(status="historical_reviewed", value=value, reviewable=False,
                          review_status="valid", reason="資料確認済みの過去BPSを参照しています。"
                          "最新決算のBPSではありません")
            result["valid_until"] = min(
                result["valid_until"], (checked + timedelta(days=review_max_age_days)).isoformat(),
            )
        else:
            result["review_status"] = "expired"
    return result
