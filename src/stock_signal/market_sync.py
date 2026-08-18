from __future__ import annotations

import re
from calendar import monthrange
from collections.abc import Callable, Sequence
from datetime import date
from decimal import Decimal
from typing import TypedDict

from sqlalchemy.exc import SQLAlchemyError

from stock_signal.database import (
    bulk_adjustment_succeeded,
    bulk_file_succeeded,
    record_bulk_adjustment,
    record_bulk_file,
    record_data_sync,
    replace_instruments,
    upsert_daily_bars,
)
from stock_signal.domain.market_data import DailyBar
from stock_signal.providers.base import MarketDataError, MarketUniverseProvider
from stock_signal.quality import find_corporate_action_gaps

DAILY_BARS_ENDPOINT = "/equities/bars/daily"


class BulkSyncError(TypedDict):
    file_key: str
    message: str


class BulkSyncSummary(TypedDict):
    files: int
    skipped: int
    raw_rows: int
    rows: int
    refreshed_symbols: int
    failed: int
    errors: list[BulkSyncError]


def _concise_error(error: Exception) -> str:
    """SQL全文を除き、運用ログへ表示できる長さの原因を返す。"""
    original = getattr(error, "orig", None)
    message = str(original if original is not None else error)
    first_line = next(
        (line.strip() for line in message.splitlines() if line.strip()),
        error.__class__.__name__,
    )
    return first_line[:500]


def _bulk_file_period(file_key: str, target_date: date) -> tuple[date, date]:
    """日次・月次バルクファイルが含む期間を返す。"""
    monthly = re.search(r"(?<!\d)(20\d{2})(\d{2})(?!\d)", file_key)
    if "/historical/" in file_key and monthly:
        year = int(monthly[1])
        month = int(monthly[2])
        return date(year, month, 1), date(year, month, monthrange(year, month)[1])
    return target_date, target_date


def _split_symbols(bars: Sequence[DailyBar]) -> set[str]:
    """調整係数が1以外となった銘柄を返す。"""
    return {
        bar.symbol
        for bar in bars
        if bar.adjustment_factor is not None
        and bar.adjustment_factor > 0
        and bar.adjustment_factor != Decimal("1")
    }


def sync_instrument_master(
    database_url: str,
    provider: MarketUniverseProvider,
    as_of: date,
) -> int:
    """全上場銘柄マスタを原子的に更新する。"""
    items = list(provider.fetch_instrument_master(as_of))
    if not items:
        raise ValueError(
            "J-Quants銘柄マスタが空のため、既存マスタを更新しません"
        )
    records = [
        {
            "symbol": item.symbol,
            "provider": "jquants",
            "display_name": item.name,
            "english_name": item.english_name,
            "market": item.market,
            "sector_17_code": item.sector_17_code,
            "sector_17_name": item.sector_17_name,
            "sector_33_code": item.sector_33_code,
            "sector_33_name": item.sector_33_name,
            "instrument_type": item.instrument_type,
            "is_active": True,
            "as_of_date": item.as_of_date,
        }
        for item in items
    ]
    stored = replace_instruments(database_url, "jquants", records)
    record_data_sync(database_url, "jquants_instrument_master", "success")
    return stored


def sync_bulk_daily_bars(
    database_url: str,
    provider: MarketUniverseProvider,
    start: date,
    end: date,
    *,
    history_start: date | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> BulkSyncSummary:
    """バルク未調整値とREST APIの調整済み日足を再開可能に保存する。"""
    files = list(provider.list_bulk_files(DAILY_BARS_ENDPOINT, start, end))
    summary: BulkSyncSummary = {
        "files": len(files),
        "skipped": 0,
        "raw_rows": 0,
        "rows": 0,
        "refreshed_symbols": 0,
        "failed": 0,
        "errors": [],
    }
    refresh_from = history_start or start
    incremental_sync = start > refresh_from
    for file_number, item in enumerate(files, start=1):
        if on_progress:
            on_progress(
                f"[{file_number}/{len(files)}] 調整済み日足を処理中: {item.key}"
            )
        raw_succeeded = bulk_file_succeeded(database_url, item.key)
        adjusted_succeeded = bulk_adjustment_succeeded(database_url, item.key)
        if raw_succeeded and adjusted_succeeded:
            summary["skipped"] += 1
            continue
        try:
            bulk_bars = []
            if not raw_succeeded:
                record_bulk_file(
                    database_url,
                    file_key=item.key,
                    endpoint=item.endpoint,
                    target_date=item.target_date,
                    status="running",
                )
                bulk_bars = list(provider.download_bulk_daily_bars(item.key))
                bulk_bars = [
                    bar for bar in bulk_bars if start <= bar.trade_date <= end
                ]
                if not bulk_bars:
                    raise ValueError(
                        f"バルクファイルに有効な日足がありません: {item.key}"
                    )
                raw_stored = upsert_daily_bars(database_url, bulk_bars)
                record_bulk_file(
                    database_url,
                    file_key=item.key,
                    endpoint=item.endpoint,
                    target_date=item.target_date,
                    status="success",
                    row_count=raw_stored,
                )
                summary["raw_rows"] += raw_stored

            adjusted_bars = []
            if not adjusted_succeeded:
                record_bulk_adjustment(database_url, item.key, "running")
                if bulk_bars and all(bar.is_adjusted for bar in bulk_bars):
                    adjusted_bars = bulk_bars
                else:
                    period_start, period_end = _bulk_file_period(
                        item.key, item.target_date
                    )
                    period_start = max(period_start, start)
                    period_end = min(period_end, end)
                    adjusted_bars = list(
                        provider.fetch_market_daily_prices(period_start, period_end)
                    )
                if not adjusted_bars or not all(
                    bar.is_adjusted for bar in adjusted_bars
                ):
                    raise ValueError(
                        f"調整済み日足を取得できませんでした: {item.key}"
                    )
                issues = find_corporate_action_gaps(adjusted_bars)
                if issues:
                    issue = issues[0]
                    raise ValueError(
                        "調整済み日足に株式分割相当の段差が残っています: "
                        f"{issue.symbol} {issue.previous_date} -> {issue.trade_date} "
                        f"調整済み比率={issue.price_ratio:.4f} "
                        f"未調整比率={issue.raw_price_ratio} "
                        f"調整係数={issue.adjustment_factor}"
                    )
                stored = upsert_daily_bars(database_url, adjusted_bars)
                split_symbols = (
                    _split_symbols(adjusted_bars) if incremental_sync else set()
                )
                for symbol in sorted(split_symbols):
                    history = list(
                        provider.fetch_daily_prices(
                            symbol,
                            start=refresh_from,
                            end=end,
                        )
                    )
                    if not history or not all(bar.is_adjusted for bar in history):
                        raise ValueError(
                            f"{symbol}の株式分割後の全履歴を"
                            "取得できませんでした"
                        )
                    issues = find_corporate_action_gaps(history)
                    if issues:
                        issue = issues[0]
                        raise ValueError(
                            f"{symbol}の調整済み全履歴に"
                            "株式分割相当の段差が残っています: "
                            f"{issue.previous_date} -> {issue.trade_date} "
                            f"調整済み比率={issue.price_ratio:.4f} "
                            f"未調整比率={issue.raw_price_ratio} "
                            f"調整係数={issue.adjustment_factor}"
                        )
                    upsert_daily_bars(database_url, history)
                record_bulk_adjustment(
                    database_url,
                    item.key,
                    "success",
                    row_count=stored,
                )
                summary["rows"] += stored
                summary["refreshed_symbols"] += len(split_symbols)
        except (MarketDataError, SQLAlchemyError, ValueError) as error:
            error_message = _concise_error(error)
            if not bulk_file_succeeded(database_url, item.key):
                record_bulk_file(
                    database_url,
                    file_key=item.key,
                    endpoint=item.endpoint,
                    target_date=item.target_date,
                    status="failed",
                    error_message=error_message,
                )
            else:
                record_bulk_adjustment(
                    database_url,
                    item.key,
                    "failed",
                    error_message=error_message,
                )
            summary["failed"] += 1
            if len(summary["errors"]) < 10:
                summary["errors"].append(
                    {"file_key": item.key, "message": error_message}
                )
            if on_progress:
                on_progress(
                    f"[{file_number}/{len(files)}] 失敗: {error_message}"
                )
    record_data_sync(
        database_url,
        "jquants_bulk_daily_bars",
        "success" if summary["failed"] == 0 else "failed",
        (
            None
            if summary["failed"] == 0
            else f"{summary['failed']}ファイルの取得に失敗しました"
        ),
    )
    return summary
