from __future__ import annotations

import re
from contextlib import asynccontextmanager
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from secrets import compare_digest
from threading import Lock
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from stock_signal.ai_review import (
    InvestmentReviewError,
    OpenAIInvestmentReviewService,
    review_text_segments,
)
from stock_signal.analysis.horizons import get_horizon_profile
from stock_signal.analysis.indicators import (
    resistance_bands,
    simple_moving_average_series,
    wilder_rsi_series,
)
from stock_signal.analysis.service import AnalysisService
from stock_signal.config import Settings
from stock_signal.database import (
    add_watchlist_item,
    bulk_sync_status,
    data_sync_succeeded,
    get_instruments_by_symbols,
    latest_bulk_file_date,
    list_latest_predictions,
    list_market_candidates,
    list_positions,
    list_watchlist_items,
    list_watchlist_registrations,
    list_watchlists,
    load_daily_bars,
    remove_position,
    remove_watchlist_item,
    request_watchlist_registration,
    search_instruments,
    upsert_position,
)
from stock_signal.persistence.engine import check_database

settings = Settings.from_env()
analysis_service = AnalysisService(
    settings.database_url, jquants_plan=settings.jquants_plan
)
ai_review_lock = Lock()


@asynccontextmanager
async def lifespan(_: FastAPI):
    check_database(settings.database_url)
    yield


app = FastAPI(
    title="TOMOSHIBIYORI",
    description=(
        "保有銘柄、ウォッチリスト、市場全体候補の日足と分析情報を確認するアプリケーション"
    ),
    version="0.9.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def require_internal_api_token(request: Request, call_next):
    """外部公開するJSON APIをNext.jsのBFFからの呼び出しに限定する。"""
    if settings.api_auth_required and request.url.path.startswith("/api/v1/"):
        authorization = request.headers.get("authorization", "")
        scheme, _, supplied_token = authorization.partition(" ")
        expected_token = settings.internal_api_token or ""
        authenticated = (
            scheme.lower() == "bearer"
            and bool(supplied_token)
            and compare_digest(supplied_token, expected_token)
        )
        if not authenticated:
            return JSONResponse(
                status_code=401,
                content={"detail": "API認証が必要です"},
                headers={"WWW-Authenticate": "Bearer"},
            )
    return await call_next(request)

TRANSITION_PHASE_LABELS = {
    "falling": "下降継続",
    "bottoming": "底固め観察",
    "preparing": "転換準備",
    "one_gate_remaining": "あと1条件",
    "early_reversal": "転換初動",
    "uptrend": "上昇継続",
    "caution": "警戒",
    "unknown": "未評価",
}
POSITION_ENTRY_PHASE_LABELS = {
    "pullback_candidate": "押し目反発を確認",
    "support_test": "支持候補を試す",
    "approaching_support": "支持候補へ接近",
    "trend_extended": "支持帯から上方乖離",
    "trend_broken": "中期トレンド未維持",
    "no_setup": "押し目条件なし",
}
def _plan_capabilities() -> list[dict[str, object]]:
    """画面とAPIで共通利用するJ-Quants契約機能を返す。"""
    paid_plan = settings.jquants_plan != "free"
    full_indices = settings.jquants_plan in {"standard", "premium"}
    topix_synced = data_sync_succeeded(settings.database_url, "jquants_topix")
    earnings_synced = data_sync_succeeded(
        settings.database_url, "jquants_earnings_calendar"
    )
    bulk_status = bulk_sync_status(
        settings.database_url,
        "/equities/bars/daily",
    )
    bulk_synced = (
        data_sync_succeeded(settings.database_url, "jquants_bulk_daily_bars")
        and bulk_status["total"] > 0
        and bulk_status["incomplete"] == 0
        and latest_bulk_file_date(
            settings.database_url,
            "/equities/bars/daily",
        )
        is not None
    )
    return [
        {
            "key": "daily_prices",
            "label": "最新の日足",
            "status": "enabled",
            "message": (
                "有償プランで当日までの株価四本値を取得します"
                if paid_plan
                else "Freeプランでは12週間遅延した株価四本値を取得します"
            ),
        },
        {
            "key": "bulk_daily_prices",
            "label": "全市場の日足",
            "status": (
                "plan_unavailable"
                if not paid_plan
                else "ready" if bulk_synced else "pending_sync"
            ),
            "message": (
                "Light以上が必要です"
                if not paid_plan
                else "全市場の調整済み日足を同期済み"
                if bulk_synced
                else (
                    f"調整済み化が未完了のファイルが"
                    f"{bulk_status['incomplete']}件あります"
                    if bulk_status["incomplete"]
                    else "バルク取得後に調整済み日足へ同期します"
                )
            ),
        },
        {
            "key": "topix",
            "label": "TOPIX対比",
            "status": (
                "plan_unavailable"
                if not paid_plan
                else "ready" if topix_synced else "pending_sync"
            ),
            "message": (
                "Light以上が必要です"
                if not paid_plan
                else "同期済み" if topix_synced else "次回の日次バッチで同期します"
            ),
        },
        {
            "key": "earnings",
            "label": "決算予定日",
            "status": "ready" if earnings_synced else "pending_sync",
            "message": (
                "同期済み" if earnings_synced else "次回の日次バッチで同期します"
            ),
        },
        {
            "key": "sector_index",
            "label": "業種指数対比",
            "status": "pending_sync" if full_indices else "plan_unavailable",
            "message": (
                "契約対象ですが、業種指数Providerは未実装です"
                if full_indices
                else "Light対象外。Standard以上で有効化できます"
            ),
        },
        {
            "key": "tdnet",
            "label": "適時開示との関連",
            "status": "addon_required" if paid_plan else "plan_unavailable",
            "message": (
                "TDnetアドオン契約後に有効化できます"
                if paid_plan
                else "有償プランとTDnetアドオンが必要です"
            ),
        },
    ]


def _serialize_prediction(prediction) -> dict[str, object]:
    return {
        "symbol": prediction.symbol,
        "display_name": prediction.display_name,
        "provider": prediction.provider,
        "as_of_date": prediction.as_of_date,
        "horizon_days": prediction.horizon_days,
        "probability_up": prediction.probability_up,
        "probability_flat": prediction.probability_flat,
        "probability_down": prediction.probability_down,
        "predicted_class": prediction.predicted_class,
        "rank_score": prediction.rank_score,
        "model_version_id": prediction.model_version_id,
    }


def _serialize_analysis(result, display_name: str | None = None) -> dict[str, object]:
    decision = result.investment_decision
    horizon_profile = get_horizon_profile(result.horizon_days)
    lifecycle_by_type = {
        lifecycle.pattern_type: lifecycle for lifecycle in result.pattern_lifecycles
    }

    def serialize_lifecycle(pattern_type):
        lifecycle = lifecycle_by_type.get(pattern_type)
        if lifecycle is None:
            return None
        return {
            "status": lifecycle.status.value,
            "guidance": lifecycle.guidance.value,
            "trading_days_since_breakout": lifecycle.trading_days_since_breakout,
            "entry_window_days": lifecycle.entry_window_days,
            "maximum_monitoring_days": lifecycle.maximum_monitoring_days,
            "entry_days_remaining": lifecycle.entry_days_remaining,
            "current_close": lifecycle.current_close,
            "breakout_close": lifecycle.breakout_close,
            "target_price": lifecycle.target_price,
            "invalidation_price": lifecycle.invalidation_price,
            "post_breakout_return_percent": lifecycle.post_breakout_return_percent,
            "recent_momentum_atr": lifecycle.recent_momentum_atr,
            "summary": lifecycle.summary,
        }

    transition = result.transition_readiness
    position_entry = result.position_entry

    def serialize_condition(condition):
        return {
            "key": condition.key,
            "label": condition.label,
            "satisfied": condition.satisfied,
            "required": condition.required,
            "description": condition.description,
            "current_value": condition.current_value,
            "target_value": condition.target_value,
            "unit": condition.unit,
        }

    return {
        "symbol": result.symbol,
        "display_name": display_name,
        "as_of_date": result.as_of_date,
        "horizon_days": result.horizon_days,
        "horizon_profile": {
            "key": horizon_profile.key,
            "label": horizon_profile.label,
            "future_label": horizon_profile.future_label,
            "holding_period": horizon_profile.holding_period,
            "purpose": horizon_profile.purpose,
            "caution": horizon_profile.caution,
            "minimum_bars": horizon_profile.minimum_bars,
        },
        "direction": result.direction.value,
        "scores": {direction.value: score for direction, score in result.scores.items()},
        "factors": [
            {
                "rule_id": factor.rule_id,
                "name": factor.name,
                "direction": factor.direction.value,
                "score": factor.score,
                "description": factor.description,
            }
            for factor in result.factors
        ],
        "engine": {"id": result.engine_id, "version": result.engine_version},
        "status": result.status,
        "message": result.message,
        "score_is_probability": False,
        "transition_readiness": None if transition is None else {
            "phase": transition.phase.value,
            "phase_label": TRANSITION_PHASE_LABELS[transition.phase.value],
            "satisfied_conditions": transition.satisfied_conditions,
            "total_conditions": transition.total_conditions,
            "readiness_score": transition.readiness_score,
            "summary": transition.summary,
            "next_condition": (
                None
                if transition.next_condition is None
                else serialize_condition(transition.next_condition)
            ),
            "conditions": [
                serialize_condition(condition) for condition in transition.conditions
            ],
            "current_price": transition.current_price,
            "trigger_price": transition.trigger_price,
            "invalidation_price": transition.invalidation_price,
            "target_price": transition.target_price,
            "risk_reward_ratio": transition.risk_reward_ratio,
            "score_is_probability": False,
        },
        "position_entry": None if position_entry is None else {
            "phase": position_entry.phase.value,
            "phase_label": POSITION_ENTRY_PHASE_LABELS[position_entry.phase.value],
            "satisfied_conditions": position_entry.satisfied_conditions,
            "total_conditions": position_entry.total_conditions,
            "readiness_score": position_entry.readiness_score,
            "summary": position_entry.summary,
            "next_condition": (
                None
                if position_entry.next_condition is None
                else {
                    "key": position_entry.next_condition.key,
                    "label": position_entry.next_condition.label,
                    "satisfied": position_entry.next_condition.satisfied,
                    "description": position_entry.next_condition.description,
                }
            ),
            "conditions": [
                {
                    "key": condition.key,
                    "label": condition.label,
                    "satisfied": condition.satisfied,
                    "description": condition.description,
                }
                for condition in position_entry.conditions
            ],
            "supports": [
                {
                    "key": support.key,
                    "label": support.label,
                    "level": support.level,
                    "lower": support.lower,
                    "upper": support.upper,
                    "distance_atr": support.distance_atr,
                    "touched": support.touched,
                    "held": support.held,
                    "description": support.description,
                }
                for support in position_entry.supports
            ],
            "current_price": position_entry.current_price,
            "atr": position_entry.atr,
            "invalidation_price": position_entry.invalidation_price,
            "score_is_probability": False,
        },
        "patterns": [
            {
                "type": pattern.pattern_type.value,
                "name": pattern.name,
                "direction": pattern.direction.value,
                "detected_at": pattern.detected_at,
                "fit_score": pattern.fit_score,
                "duration_days": pattern.duration_days,
                "breakout_level": pattern.breakout_level,
                "breakout_atr": pattern.breakout_atr,
                "volume_ratio": pattern.volume_ratio,
                "gap_atr": pattern.gap_atr,
                "breakout_kind": pattern.breakout_kind.value,
                "prior_trend_score": pattern.prior_trend_score,
                "description": pattern.description,
                "lifecycle": serialize_lifecycle(pattern.pattern_type),
            }
            for pattern in result.patterns
        ],
        "equity_checks": [
            {
                "key": check.key,
                "label": check.label,
                "status": check.status.value,
                "value": check.value,
                "unit": check.unit,
                "description": check.description,
            }
            for check in result.equity_checks
        ],
        "investment_decision": None if decision is None else {
            "action": decision.action.value,
            "evidence_score": decision.evidence_score,
            "summary": decision.summary,
            "reasons": list(decision.reasons),
            "cautions": list(decision.cautions),
            "score_is_probability": False,
        },
    }


def _validate_horizon(horizon: int) -> None:
    if horizon not in {1, 5, 20}:
        raise HTTPException(
            status_code=422,
            detail="予測期間は1、5、20営業日のいずれかで指定してください",
        )


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/watchlist")
def watchlist() -> dict[str, object]:
    items = list_watchlist_items(settings.database_url)
    return {
        "name": "ウォッチ",
        "items": [
            {
                "symbol": item.symbol,
                "provider": item.provider,
                "display_name": item.display_name,
                "exchange": item.exchange,
                "currency": item.currency,
            }
            for item in items
        ],
    }


@app.get("/api/v1/market-candidates")
def market_candidates_api(
    action: str | None = Query(
        None,
        pattern="^(buy_candidate|watch|avoid_new_buy)$",
    ),
    horizon: int = Query(5),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, object]:
    _validate_horizon(horizon)
    return {
        "scope": "market",
        "horizon_days": horizon,
        "items": list_market_candidates(
            settings.database_url,
            horizon_days=horizon,
            action=action,
            limit=limit,
        ),
    }


@app.get("/api/v1/watchlists")
def watchlist_collections() -> dict[str, object]:
    return {
        "items": [
            {"name": item.name, "item_count": item.item_count}
            for item in list_watchlists(settings.database_url)
        ]
    }


@app.get("/api/v1/instruments")
def instruments_search(
    query: str = Query("", max_length=100),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, object]:
    items = search_instruments(settings.database_url, query, limit=limit)
    return {
        "items": [
            {
                "symbol": item.symbol,
                "provider": item.provider,
                "display_name": item.display_name,
                "market": item.market,
                "sector": item.sector_33_name,
                "instrument_type": item.instrument_type,
            }
            for item in items
        ]
    }


@app.post("/api/v1/watchlists/{watchlist_name}/items/bulk")
async def add_watchlist_items_bulk(
    watchlist_name: str,
    request: Request,
) -> JSONResponse:
    watchlist_name = watchlist_name.strip()
    if not watchlist_name or len(watchlist_name) > 100:
        raise HTTPException(
            status_code=422,
            detail="ウォッチリスト名は1文字以上100文字以下で指定してください",
        )
    try:
        payload = await request.json()
    except ValueError as error:
        raise HTTPException(status_code=422, detail="JSON形式で指定してください") from error
    raw_symbols = payload.get("symbols") if isinstance(payload, dict) else None
    if not isinstance(raw_symbols, list):
        raise HTTPException(status_code=422, detail="symbolsは配列で指定してください")
    symbols = list(dict.fromkeys(str(value).strip().upper() for value in raw_symbols))
    if not symbols or len(symbols) > 200:
        raise HTTPException(status_code=422, detail="証券コードは1件以上200件以下にしてください")
    invalid = [symbol for symbol in symbols if not re.fullmatch(r"[0-9A-Z]{4}", symbol)]
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"4文字の半角英数字ではない証券コードがあります: {', '.join(invalid)}",
        )
    matched = get_instruments_by_symbols(settings.database_url, symbols)
    matched_by_symbol = {item.symbol: item for item in matched}
    for item in matched:
        add_watchlist_item(
            settings.database_url,
            symbol=item.symbol,
            provider=item.provider,
            display_name=item.display_name,
            exchange=item.market,
            currency="JPY",
            watchlist_name=watchlist_name,
        )
    pending = [symbol for symbol in symbols if symbol not in matched_by_symbol]
    for symbol in pending:
        request_watchlist_registration(
            settings.database_url,
            symbol,
            watchlist_name=watchlist_name,
        )
    return JSONResponse(
        status_code=202 if pending else 200,
        content={
            "added": [item.symbol for item in matched],
            "pending": pending,
            "message": (
                f"{len(matched)}銘柄を追加し、{len(pending)}銘柄を確認待ちにしました"
                if pending
                else f"{len(matched)}銘柄を追加しました"
            ),
        },
    )


@app.get("/api/v1/portfolio/positions")
def portfolio_positions() -> dict[str, object]:
    items = list_positions(settings.database_url)
    return {
        "items": [
            {
                "symbol": item.symbol,
                "provider": item.provider,
                "display_name": item.display_name,
                "quantity": str(item.quantity),
                "average_cost": str(item.average_cost) if item.average_cost is not None else None,
                "account_type": item.account_type,
                "memo": item.memo,
                "latest_close": str(item.latest_close) if item.latest_close is not None else None,
                "latest_trade_date": item.latest_trade_date,
            }
            for item in items
        ]
    }


@app.post("/api/v1/portfolio/positions")
async def create_or_update_position(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
        symbol = str(payload.get("symbol", "")).strip().upper()
        quantity = Decimal(str(payload.get("quantity", "")))
        raw_cost = payload.get("average_cost")
        average_cost = Decimal(str(raw_cost)) if raw_cost not in {None, ""} else None
    except (ValueError, TypeError, InvalidOperation, AttributeError) as error:
        raise HTTPException(status_code=422, detail="保有銘柄の入力値が不正です") from error
    if not re.fullmatch(r"[0-9A-Z]{4}", symbol):
        raise HTTPException(status_code=422, detail="証券コードは4文字で指定してください")
    if (
        not quantity.is_finite()
        or quantity < 0
        or (
            average_cost is not None
            and (not average_cost.is_finite() or average_cost < 0)
        )
    ):
        raise HTTPException(
            status_code=422,
            detail="保有数量と平均取得単価は0以上で指定してください",
        )
    matches = get_instruments_by_symbols(settings.database_url, [symbol])
    if not matches:
        raise HTTPException(
            status_code=404,
            detail="銘柄マスタにありません。先に日次バッチで銘柄マスタを同期してください",
        )
    item = matches[0]
    upsert_position(
        settings.database_url,
        symbol=item.symbol,
        provider=item.provider,
        display_name=item.display_name,
        quantity=quantity,
        average_cost=average_cost,
        account_type=str(payload.get("account_type") or "未設定")[:40],
        memo=str(payload.get("memo"))[:500] if payload.get("memo") else None,
    )
    return JSONResponse(
        content={"symbol": item.symbol, "status": "saved", "message": "保有銘柄を保存しました"}
    )


@app.delete("/api/v1/portfolio/positions/{symbol}")
def delete_position(
    symbol: str,
    provider: Annotated[str, Query(pattern=r"^[a-z0-9_]+$")] = "jquants",
) -> dict[str, object]:
    if not remove_position(settings.database_url, symbol, provider):
        raise HTTPException(status_code=404, detail="指定された保有銘柄がありません")
    return {"symbol": symbol.upper(), "status": "removed"}


@app.delete("/api/v1/watchlist/{symbol}")
def delete_watchlist_item(
    symbol: str,
    provider: Annotated[str, Query(pattern=r"^[a-z0-9_]+$")] = "jquants",
) -> dict[str, object]:
    removed = remove_watchlist_item(settings.database_url, symbol, provider)
    if not removed:
        raise HTTPException(
            status_code=404,
            detail="指定された銘柄はウォッチリストにありません",
        )
    return {
        "symbol": symbol.strip().upper(),
        "provider": provider,
        "status": "removed",
        "message": "ウォッチリストから削除しました。保存済み日足は維持されます",
    }


@app.get("/api/v1/data-plan")
def data_plan() -> dict[str, object]:
    return {
        "provider": "jquants",
        "plan": settings.jquants_plan,
        "rate_limit_per_minute": settings.jquants_rate_limit_per_minute,
        "history_years": settings.jquants_history_years,
        "capabilities": _plan_capabilities(),
    }


@app.get("/api/v1/watchlist/registrations")
def watchlist_registrations() -> dict[str, object]:
    registrations = list_watchlist_registrations(
        settings.database_url, statuses=("pending", "failed", "active")
    )
    return {
        "items": [
            {
                "symbol": item.symbol,
                "provider": item.provider,
                "status": item.status,
                "display_name": item.display_name,
                "error_message": item.error_message,
                "requested_at": item.requested_at,
                "updated_at": item.updated_at,
                "watchlist_name": item.watchlist_name,
            }
            for item in registrations
        ]
    }


@app.post("/api/v1/watchlist/registrations")
async def create_watchlist_registration(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except ValueError as error:
        raise HTTPException(
            status_code=422, detail="JSON形式で指定してください"
        ) from error
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=422, detail="JSONオブジェクトで指定してください"
        )
    symbol = str(payload.get("symbol", "")).strip()
    symbol = symbol.upper()
    if not re.fullmatch(r"[0-9A-Z]{4}", symbol):
        raise HTTPException(
            status_code=422,
            detail="証券コードは4文字の半角英数字で指定してください",
        )
    registration = request_watchlist_registration(
        settings.database_url, symbol, provider="jquants"
    )
    is_active = registration.status == "active"
    return JSONResponse(
        status_code=200 if is_active else 202,
        content={
            "symbol": registration.symbol,
            "status": registration.status,
            "display_name": registration.display_name,
            "message": (
                "すでにウォッチリストへ登録されています"
                if is_active
                else (
                    "登録を受け付けました。次回の日次バッチで"
                    "銘柄確認と日足取得を行います"
                )
            ),
        },
    )


@app.get("/api/v1/instruments/{symbol}/daily-bars")
def daily_bars(
    symbol: str,
    provider: str | None = None,
    range_name: Annotated[
        str,
        Query(alias="range", pattern="^(1m|3m|6m|1y|all)$"),
    ] = "3m",
    from_date: Annotated[date | None, Query(alias="from")] = None,
    to_date: Annotated[date | None, Query(alias="to")] = None,
) -> dict[str, object]:
    all_bars = load_daily_bars(settings.database_url, symbol, provider=provider)
    if not all_bars:
        stored_bars = load_daily_bars(
            settings.database_url,
            symbol,
            provider=provider,
            include_unadjusted=True,
        )
        if stored_bars and any(
            bar.provider == "jquants" and not bar.is_adjusted
            for bar in stored_bars
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "未調整の日足が保存されています。"
                    "jquants-bulk-bootstrapを再実行して調整済み日足へ更新してください"
                ),
            )
        if any(bar.provider == "jquants" for bar in stored_bars):
            raise HTTPException(
                status_code=409,
                detail=(
                    "調整済み日足の品質検査で株式分割相当の段差を検出しました。"
                    "jquants-bulk-bootstrapを再実行してください"
                ),
            )
        raise HTTPException(
            status_code=404,
            detail="指定された銘柄の日足データがありません",
        )

    resolved_provider = provider or all_bars[-1].provider
    all_bars = [bar for bar in all_bars if bar.provider == resolved_provider]
    latest_date = all_bars[-1].trade_date
    if to_date is None:
        to_date = latest_date
    if from_date is None:
        days_by_range = {"1m": 31, "3m": 93, "6m": 186, "1y": 366}
        from_date = (
            all_bars[0].trade_date
            if range_name == "all"
            else to_date - timedelta(days=days_by_range[range_name])
        )
    if from_date > to_date:
        raise HTTPException(
            status_code=422,
            detail="開始日は終了日以前にしてください",
        )

    bars = [bar for bar in all_bars if from_date <= bar.trade_date <= to_date]
    source_bar = bars[-1] if bars else all_bars[-1]
    calculation_bars = [bar for bar in all_bars if bar.trade_date <= to_date]
    calculation_index = {
        bar.trade_date: index for index, bar in enumerate(calculation_bars)
    }

    def visible_values(values: tuple[float | None, ...]) -> list[float | None]:
        visible = [values[calculation_index[bar.trade_date]] for bar in bars]
        return [round(value, 4) if value is not None else None for value in visible]

    moving_averages = {
        str(window): visible_values(
            simple_moving_average_series(calculation_bars, window)
        )
        for window in (5, 20, 60)
    }
    rsi_values = {
        str(window): visible_values(wilder_rsi_series(calculation_bars, window))
        for window in (14, 28)
    }

    def serialize_resistance(lookback: int) -> list[dict[str, object]]:
        return [
            {
                "lower": band.lower,
                "upper": band.upper,
                "center": band.center,
                "touches": band.touches,
                "first_touched": band.first_touched.isoformat(),
                "last_touched": band.last_touched.isoformat(),
                "distance_percent": band.distance_percent,
            }
            for band in resistance_bands(calculation_bars, lookback=lookback)
        ]

    is_adjusted = all(
        bar.is_adjusted or bar.symbol == "TOPIX" for bar in all_bars
    )
    return {
        "instrument": {"symbol": source_bar.symbol},
        "range": {"from": from_date.isoformat(), "to": to_date.isoformat()},
        "source": {
            "provider": source_bar.provider,
            "is_adjusted": is_adjusted,
            "price_basis": "adjusted" if is_adjusted else "raw",
        },
        "freshness": {
            "latest_trade_date": latest_date.isoformat(),
            "status": "fresh" if latest_date == to_date else "historical",
        },
        "indicators": {
            "moving_averages": moving_averages,
            "rsi": rsi_values,
            "resistance_bands": {
                "60": serialize_resistance(60),
                "120": serialize_resistance(120),
            },
            "definitions": {
                "moving_average": "終値の単純移動平均",
                "rsi": "Wilder平滑化RSI",
                "resistance": (
                    "局所高値をATRで集約した2回以上接触の候補帯。"
                    "明確に上抜けた帯は除外"
                ),
                "score_is_probability": False,
            },
        },
        "bars": [
            {
                "trade_date": bar.trade_date.isoformat(),
                "open": str(bar.open),
                "high": str(bar.high),
                "low": str(bar.low),
                "close": str(bar.close),
                "volume": bar.volume,
            }
            for bar in bars
        ],
    }


@app.get("/api/v1/candidates")
def candidates(
    direction: str | None = Query(None, pattern="^(up|flat|down)$"),
    action: str | None = Query(
        None, pattern="^(buy_candidate|watch|avoid_new_buy|insufficient_data)$"
    ),
    horizon: int = Query(5),
    minimum_score: float = Query(0, ge=0, le=100),
) -> dict[str, object]:
    _validate_horizon(horizon)
    analyses = analysis_service.analyze_watchlist(horizon)
    if direction:
        analyses = [item for item in analyses if item[1].direction.value == direction]
    if action:
        analyses = [
            item for item in analyses
            if item[1].investment_decision
            and item[1].investment_decision.action.value == action
        ]
    analyses = [
        item for item in analyses
        if (
            item[1].investment_decision.evidence_score
            if item[1].investment_decision
            else item[1].winning_score
        ) >= minimum_score
    ]
    return {
        "horizon_days": horizon,
        "status": "ready" if analyses else "no_candidates",
        "method": "rule_based",
        "notice": (
            "判定スコアと根拠の強さは参考値であり、"
            "将来の確率ではありません"
        ),
        "items": [_serialize_analysis(result, name) for name, result in analyses],
    }


@app.get("/api/v1/instruments/{symbol}/analysis/latest")
def latest_analysis(
    symbol: str,
    horizon: int = Query(5),
    provider: str | None = None,
) -> dict[str, object]:
    _validate_horizon(horizon)
    result = analysis_service.analyze_symbol(symbol, horizon, provider)
    if not result.as_of_date:
        raise HTTPException(
            status_code=404,
            detail="指定された銘柄の日足データがありません",
        )
    return _serialize_analysis(result)


@app.get("/api/v1/ai-investment-review/capability")
def ai_investment_review_capability() -> dict[str, object]:
    configured = bool(settings.openai_api_key)
    return {
        "status": "ready" if configured else "not_configured",
        "enabled": configured,
        "model": settings.openai_model,
        "max_output_tokens": settings.openai_max_output_tokens,
        "search": "openai_responses_web_search",
        "message": (
            "指定銘柄の最新関連情報を検索できます"
            if configured
            else "OPENAI_API_KEYを設定すると利用できます"
        ),
        "notice": (
            "実行ごとにOpenAI APIとWeb検索の利用料金が"
            "発生する場合があります"
        ),
    }


@app.post("/api/v1/instruments/{symbol}/ai-investment-review")
def create_ai_investment_review(
    symbol: str,
    horizon: int = Query(5),
    provider: str | None = None,
) -> dict[str, object]:
    """指定銘柄のテクニカル結果を、最新の外部情報と照合する。"""
    _validate_horizon(horizon)
    if not settings.openai_api_key:
        raise HTTPException(
            status_code=503,
            detail="OPENAI_API_KEYが未設定のため、AI最終確認を実行できません",
        )
    bars = load_daily_bars(settings.database_url, symbol, provider=provider)
    if not bars:
        raise HTTPException(
            status_code=404,
            detail="指定された銘柄の日足データがありません",
        )
    resolved_provider = provider or bars[-1].provider
    result = analysis_service.analyze_symbol(symbol, horizon, resolved_provider)
    instruments = get_instruments_by_symbols(
        settings.database_url,
        [symbol],
        provider=resolved_provider,
    )
    display_name = instruments[0].display_name if instruments else symbol.upper()
    serialized = _serialize_analysis(result, display_name)
    technical_context = {
        key: serialized[key]
        for key in (
            "symbol",
            "display_name",
            "as_of_date",
            "horizon_days",
            "horizon_profile",
            "direction",
            "scores",
            "score_is_probability",
            "factors",
            "patterns",
            "equity_checks",
            "transition_readiness",
            "position_entry",
            "investment_decision",
            "engine",
        )
    }
    try:
        service = OpenAIInvestmentReviewService(
            settings.openai_api_key,
            settings.openai_model,
            max_output_tokens=settings.openai_max_output_tokens,
        )
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail=(
                "OpenAI SDKを初期化できません。"
                "Dockerイメージを再ビルドしてください"
            ),
        ) from error
    if not ai_review_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="別のAI最終確認を実行中です。完了後に再実行してください",
        )
    try:
        review = service.review(
            symbol=result.symbol,
            display_name=display_name,
            horizon_days=horizon,
            provider=resolved_provider,
            technical_context=technical_context,
        )
    except InvestmentReviewError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    finally:
        ai_review_lock.release()
    return {
        "status": "ready",
        "symbol": review.symbol,
        "display_name": review.display_name,
        "horizon_days": review.horizon_days,
        "technical_as_of_date": review.technical_as_of_date,
        "generated_at": review.generated_at,
        "model": review.model,
        "response_id": review.response_id,
        "search_performed": review.search_performed,
        "report_text": review.report_text,
        "report_segments": review_text_segments(
            review.report_text,
            review.citations,
        ),
        "citations": [
            {
                "start_index": citation.start_index,
                "end_index": citation.end_index,
                "url": citation.url,
                "title": citation.title,
            }
            for citation in review.citations
        ],
        "notice": (
            "AIによる補助分析であり、投資助言、利益保証、"
            "自動注文ではありません"
        ),
    }


@app.get("/api/v1/instruments/{symbol}/predictions/latest")
def latest_prediction(
    symbol: str,
    horizon: int = Query(5),
) -> dict[str, object]:
    _validate_horizon(horizon)
    predictions = list_latest_predictions(
        settings.database_url,
        horizon_days=horizon,
        minimum_probability=0,
    )
    prediction = next((item for item in predictions if item.symbol == symbol.upper()), None)
    if prediction is None:
        return {
            "symbol": symbol.upper(),
            "horizon_days": horizon,
            "status": "not_generated",
            "message": "承認済みモデルによる予測はまだ生成されていません",
        }
    return {"status": "ready", **_serialize_prediction(prediction)}


@app.get("/api/v1/reference-signals")
def reference_signals(
    direction: str | None = Query(None, pattern="^(up|flat|down)$"),
) -> dict[str, object]:
    analyses = analysis_service.analyze_watchlist(5)
    if direction:
        analyses = [item for item in analyses if item[1].direction.value == direction]
    return {
        "status": "reference_only",
        "notice": "互換用APIです。判定スコアは将来の確率ではありません",
        "items": [_serialize_analysis(result, name) for name, result in analyses],
    }
