"""投資ストーリーとチャットレビューの認証付きAPIルート。"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from stock_signal.position_journal import (
    JournalConflict,
    ReviewInput,
    ThesisInput,
    create_context,
    journal,
    save_review,
    save_thesis,
)

Provider = Annotated[str, Query(pattern=r"^[a-z0-9_]{1,40}$")]


def journal_router(database_url: str) -> APIRouter:
    """既存APIの認証ミドルウェア内にルートを追加する。"""
    router = APIRouter(prefix="/api/v1/portfolio")

    def execute(function, *args):
        try:
            return function(database_url, *args)
        except JournalConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.get("/journal/{symbol}")
    def read_journal(symbol: str, provider: Provider = "jquants"):
        return execute(journal, symbol.upper(), provider)

    @router.post("/journal/{symbol}/theses")
    def write_thesis(symbol: str, payload: ThesisInput, provider: Provider = "jquants"):
        return execute(save_thesis, symbol.upper(), provider, payload)

    @router.post("/journal/{symbol}/context")
    def prepare_context(symbol: str, provider: Provider = "jquants"):
        return execute(create_context, symbol.upper(), provider)

    @router.post("/reviews")
    def write_review(payload: ReviewInput):
        return execute(save_review, payload)

    return router
