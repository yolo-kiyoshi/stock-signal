"""過去BPSの資料確認を記録する認証付きAPI。"""

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from stock_signal.database import load_daily_bars
from stock_signal.research import assess_financials
from stock_signal.research_store import save_bps_review

Symbol = Annotated[str, Path(pattern=r"^[0-9A-Z]{4}$")]


class BpsReviewInput(BaseModel):
    """対象の出典と確認項目を明示した資料確認。数値の上書きは認めない。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_disclosure_no: str = Field(min_length=1, max_length=80)
    evidence_url: HttpUrl
    notes: str = Field(min_length=10, max_length=2000)
    capital_checked: Literal[True]
    basis_checked: Literal[True]
    equity_checked: Literal[True]


def bps_review_router(database_url: str) -> APIRouter:
    """認証は共通ミドルウェアで適用し、資料URLは取得せず記録だけする。"""
    router = APIRouter(prefix="/api/v1/financials")

    @router.post("/{symbol}/bps-review")
    def confirm(symbol: Symbol, payload: BpsReviewInput):
        bars = load_daily_bars(database_url, symbol, provider="jquants")
        financials = assess_financials(database_url, symbol, bars)
        reference = financials.get("bps_reference", {})
        if (
            not bars or (date.today() - bars[-1].trade_date).days > 4
            or financials["sync"]["status"] != "success"
            or financials["event_status"] == "hold"
            or not reference.get("reviewable")
            or reference.get("fingerprint") != payload.fingerprint
            or reference.get("source_disclosure_no") != payload.source_disclosure_no
        ):
            raise HTTPException(status_code=409, detail=
                                "確認対象・データが更新されたか、資料確認で解除できない状態です。"
                                "最新の分析と財務同期を確認してください")
        value = payload.model_dump(mode="json")
        value.update(checked_on=date.today().isoformat(),
                     source_period_end=reference["source_period_end"],
                     source_value=reference["source_value"],
                     latest_disclosure_no=reference["latest_disclosure_no"])
        save_bps_review(database_url, symbol, value)
        return {"status": "saved", "message": "確認を保存しました。全条件を再分析してください"}

    @router.delete("/{symbol}/bps-review")
    def revoke(symbol: Symbol):
        save_bps_review(database_url, symbol, {
            "status": "revoked", "checked_on": date.today().isoformat(),
        })
        return {"status": "revoked"}

    return router
