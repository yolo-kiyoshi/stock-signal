from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import case, delete, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from stock_signal.domain.batch import BatchItemResult
from stock_signal.domain.dashboard import (
    Instrument,
    Position,
    PredictionSummary,
    WatchlistItem,
    WatchlistRegistration,
    WatchlistSummary,
)
from stock_signal.domain.market_data import DailyBar, EarningsAnnouncement
from stock_signal.persistence.engine import get_engine
from stock_signal.persistence.migrations import upgrade_database
from stock_signal.persistence.schema import (
    analysis_snapshots,
    app_metadata,
    bulk_files,
    daily_bars,
    data_sync_status,
    earnings_calendar,
    instruments,
    model_versions,
    pipeline_run_items,
    pipeline_runs,
    portfolios,
    positions,
    predictions,
    watchlist_items,
    watchlist_registrations,
    watchlists,
)
from stock_signal.quality import find_corporate_action_gaps

DEFAULT_WATCHLIST = "ウォッチ"
DEFAULT_PORTFOLIO = "メインポートフォリオ"
DAILY_BATCH_LOCK_ID = 0x544F4D4F53484942
DAILY_BAR_UPSERT_CHUNK_SIZE = 1000


def sqlite_path(database_url: str) -> Path:
    """旧SQLiteからの一度限りの移行で使用するファイルパスを返す。"""
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ValueError("SQLite移行元はsqlite:///形式で指定してください")
    return Path(database_url.removeprefix(prefix))


def initialize_database(database_url: str) -> str:
    """PostgreSQLスキーマを最新リビジョンへ更新する。"""
    upgrade_database(database_url)
    return database_url


def validate_daily_bar(bar: DailyBar) -> None:
    if bar.volume < 0:
        raise ValueError(f"volume must not be negative for {bar.symbol} on {bar.trade_date}")
    if min(bar.open, bar.high, bar.low, bar.close) <= 0:
        raise ValueError(f"OHLC prices must be positive for {bar.symbol} on {bar.trade_date}")
    if bar.low > min(bar.open, bar.close) or bar.high < max(bar.open, bar.close):
        raise ValueError(f"OHLC values are inconsistent for {bar.symbol} on {bar.trade_date}")
    if bar.low > bar.high:
        raise ValueError(f"low must not exceed high for {bar.symbol} on {bar.trade_date}")


def _chunks[T](items: Sequence[T], size: int = 5000) -> Iterator[Sequence[T]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def upsert_daily_bars(database_url: str, bars: Sequence[DailyBar]) -> int:
    """日足を検証し、PostgreSQLへ一括UPSERTする。"""
    if not bars:
        return 0
    for bar in bars:
        validate_daily_bar(bar)
    with get_engine(database_url).begin() as connection:
        # 16列×全市場約4,400行を一度に渡すとPostgreSQLのパラメータ上限を
        # 超えるため、安全な単位へ分割する。
        for chunk in _chunks(bars, DAILY_BAR_UPSERT_CHUNK_SIZE):
            values = [
                {
                    "symbol": bar.symbol.strip().upper(),
                    "trade_date": bar.trade_date,
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                    "provider": bar.provider,
                    "is_adjusted": bar.is_adjusted,
                    "raw_open": (
                        bar.raw_open
                        if bar.raw_open is not None
                        else bar.open if not bar.is_adjusted else None
                    ),
                    "raw_high": (
                        bar.raw_high
                        if bar.raw_high is not None
                        else bar.high if not bar.is_adjusted else None
                    ),
                    "raw_low": (
                        bar.raw_low
                        if bar.raw_low is not None
                        else bar.low if not bar.is_adjusted else None
                    ),
                    "raw_close": (
                        bar.raw_close
                        if bar.raw_close is not None
                        else bar.close if not bar.is_adjusted else None
                    ),
                    "raw_volume": (
                        bar.raw_volume
                        if bar.raw_volume is not None
                        else bar.volume if not bar.is_adjusted else None
                    ),
                    "adjustment_factor": bar.adjustment_factor,
                }
                for bar in chunk
            ]
            statement = pg_insert(daily_bars).values(values)
            keep_existing_adjusted = daily_bars.c.is_adjusted.is_(
                True
            ) & statement.excluded.is_adjusted.is_(False)
            connection.execute(
                statement.on_conflict_do_update(
                    constraint="uq_daily_bars_identity",
                    set_={
                        "open": case(
                            (keep_existing_adjusted, daily_bars.c.open),
                            else_=statement.excluded.open,
                        ),
                        "high": case(
                            (keep_existing_adjusted, daily_bars.c.high),
                            else_=statement.excluded.high,
                        ),
                        "low": case(
                            (keep_existing_adjusted, daily_bars.c.low),
                            else_=statement.excluded.low,
                        ),
                        "close": case(
                            (keep_existing_adjusted, daily_bars.c.close),
                            else_=statement.excluded.close,
                        ),
                        "volume": case(
                            (keep_existing_adjusted, daily_bars.c.volume),
                            else_=statement.excluded.volume,
                        ),
                        "is_adjusted": (
                            daily_bars.c.is_adjusted
                            | statement.excluded.is_adjusted
                        ),
                        "raw_open": func.coalesce(
                            statement.excluded.raw_open, daily_bars.c.raw_open
                        ),
                        "raw_high": func.coalesce(
                            statement.excluded.raw_high, daily_bars.c.raw_high
                        ),
                        "raw_low": func.coalesce(
                            statement.excluded.raw_low, daily_bars.c.raw_low
                        ),
                        "raw_close": func.coalesce(
                            statement.excluded.raw_close, daily_bars.c.raw_close
                        ),
                        "raw_volume": func.coalesce(
                            statement.excluded.raw_volume, daily_bars.c.raw_volume
                        ),
                        "adjustment_factor": func.coalesce(
                            statement.excluded.adjustment_factor,
                            daily_bars.c.adjustment_factor,
                        ),
                        "retrieved_at": func.now(),
                    },
                )
            )
    return len(bars)


def load_daily_bars(
    database_url: str,
    symbol: str,
    *,
    provider: str | None = None,
    start: date | None = None,
    end: date | None = None,
    include_unadjusted: bool = False,
) -> list[DailyBar]:
    normalized_symbol = symbol.strip().upper()
    block_jquants = False
    if (
        provider == "jquants"
        and normalized_symbol != "TOPIX"
        and not include_unadjusted
        and daily_bar_quality(database_url, symbol, provider)["status"] != "ready"
    ):
        return []
    if provider is None and normalized_symbol != "TOPIX" and not include_unadjusted:
        block_jquants = daily_bar_quality(
            database_url, normalized_symbol, "jquants"
        )["status"] not in {"ready", "missing"}
    statement = select(
        daily_bars.c.symbol,
        daily_bars.c.trade_date,
        daily_bars.c.open,
        daily_bars.c.high,
        daily_bars.c.low,
        daily_bars.c.close,
        daily_bars.c.volume,
        daily_bars.c.provider,
        daily_bars.c.is_adjusted,
        daily_bars.c.raw_open,
        daily_bars.c.raw_high,
        daily_bars.c.raw_low,
        daily_bars.c.raw_close,
        daily_bars.c.raw_volume,
        daily_bars.c.adjustment_factor,
    ).where(daily_bars.c.symbol == normalized_symbol)
    if provider:
        statement = statement.where(daily_bars.c.provider == provider)
    if start:
        statement = statement.where(daily_bars.c.trade_date >= start)
    if end:
        statement = statement.where(daily_bars.c.trade_date <= end)
    if not include_unadjusted:
        statement = statement.where(
            (daily_bars.c.provider != "jquants")
            | (daily_bars.c.symbol == "TOPIX")
            | daily_bars.c.is_adjusted.is_(True)
        )
        if block_jquants:
            statement = statement.where(daily_bars.c.provider != "jquants")
    statement = statement.order_by(daily_bars.c.trade_date)
    with get_engine(database_url).connect() as connection:
        rows = connection.execute(statement).all()
    bars = [
        DailyBar(
            symbol=row.symbol,
            trade_date=row.trade_date,
            open=Decimal(row.open),
            high=Decimal(row.high),
            low=Decimal(row.low),
            close=Decimal(row.close),
            volume=row.volume,
            provider=row.provider,
            is_adjusted=row.is_adjusted,
            raw_open=Decimal(row.raw_open) if row.raw_open is not None else None,
            raw_high=Decimal(row.raw_high) if row.raw_high is not None else None,
            raw_low=Decimal(row.raw_low) if row.raw_low is not None else None,
            raw_close=Decimal(row.raw_close) if row.raw_close is not None else None,
            raw_volume=row.raw_volume,
            adjustment_factor=(
                Decimal(row.adjustment_factor)
                if row.adjustment_factor is not None
                else None
            ),
        )
        for row in rows
    ]
    jquants_bars = [
        bar
        for bar in bars
        if bar.provider == "jquants" and bar.symbol != "TOPIX"
    ]
    if not include_unadjusted and find_corporate_action_gaps(jquants_bars):
        if provider == "jquants":
            return []
        return [bar for bar in bars if bar.provider != "jquants"]
    return bars


def daily_bar_quality(
    database_url: str,
    symbol: str,
    provider: str,
) -> dict[str, int | str]:
    """銘柄の日足が分析可能な調整済み系列か集計する。"""
    normalized_symbol = symbol.strip().upper()
    base_conditions = (
        daily_bars.c.symbol == normalized_symbol,
        daily_bars.c.provider == provider,
    )
    first_adjusted_statement = select(func.min(daily_bars.c.trade_date)).where(
        *base_conditions,
        daily_bars.c.is_adjusted.is_(True),
    )
    statement = select(
        func.count().label("total"),
        func.count().filter(daily_bars.c.is_adjusted.is_(True)).label("adjusted"),
        func.count().filter(daily_bars.c.is_adjusted.is_(False)).label("unadjusted"),
    ).where(*base_conditions)
    with get_engine(database_url).connect() as connection:
        first_adjusted = connection.scalar(first_adjusted_statement)
        if (
            provider == "jquants"
            and normalized_symbol != "TOPIX"
            and first_adjusted is not None
        ):
            statement = statement.where(
                daily_bars.c.trade_date >= first_adjusted
            )
        row = connection.execute(statement).one()
    total = int(row.total)
    adjusted = int(row.adjusted)
    unadjusted = int(row.unadjusted)
    if provider != "jquants" or normalized_symbol == "TOPIX":
        status = "ready" if total else "missing"
    elif adjusted == total and total:
        status = "ready"
    elif adjusted and unadjusted:
        status = "mixed"
    elif unadjusted:
        status = "unadjusted"
    else:
        status = "missing"
    return {
        "status": status,
        "total": total,
        "adjusted": adjusted,
        "unadjusted": unadjusted,
    }


def list_stored_symbols(database_url: str) -> list[tuple[str, str, int, str, str]]:
    statement = (
        select(
            daily_bars.c.symbol,
            daily_bars.c.provider,
            func.count(),
            func.min(daily_bars.c.trade_date),
            func.max(daily_bars.c.trade_date),
        )
        .group_by(daily_bars.c.symbol, daily_bars.c.provider)
        .order_by(daily_bars.c.symbol, daily_bars.c.provider)
    )
    with get_engine(database_url).connect() as connection:
        rows = connection.execute(statement).all()
    return [
        (row[0], row[1], row[2], row[3].isoformat(), row[4].isoformat())
        for row in rows
    ]


def latest_daily_bar_date(
    database_url: str,
    symbol: str,
    provider: str,
    *,
    adjusted_only: bool = False,
) -> date | None:
    statement = select(func.max(daily_bars.c.trade_date)).where(
        daily_bars.c.symbol == symbol.strip().upper(),
        daily_bars.c.provider == provider,
    )
    if adjusted_only and provider == "jquants" and symbol.strip().upper() != "TOPIX":
        statement = statement.where(daily_bars.c.is_adjusted.is_(True))
    with get_engine(database_url).connect() as connection:
        return connection.scalar(statement)


def earliest_daily_bar_date(
    database_url: str,
    symbol: str,
    provider: str,
    *,
    adjusted_only: bool = False,
) -> date | None:
    """銘柄・取得元ごとの保存済み最初の日足日を返す。"""
    statement = select(func.min(daily_bars.c.trade_date)).where(
        daily_bars.c.symbol == symbol.strip().upper(),
        daily_bars.c.provider == provider,
    )
    if adjusted_only and provider == "jquants" and symbol.strip().upper() != "TOPIX":
        statement = statement.where(daily_bars.c.is_adjusted.is_(True))
    with get_engine(database_url).connect() as connection:
        return connection.scalar(statement)


def latest_provider_trade_date(database_url: str, provider: str) -> date | None:
    """取得元全体で保存済みの最終取引日を返す。"""
    statement = select(func.max(daily_bars.c.trade_date)).where(
        daily_bars.c.provider == provider
    )
    with get_engine(database_url).connect() as connection:
        return connection.scalar(statement)


@contextmanager
def daily_batch_lock(database_url: str) -> Iterator[None]:
    """セッションAdvisory Lockで日次バッチの多重起動を防ぐ。"""
    connection = get_engine(database_url).connect()
    acquired = bool(
        connection.scalar(
            text("SELECT pg_try_advisory_lock(:lock_id)"),
            {"lock_id": DAILY_BATCH_LOCK_ID},
        )
    )
    if not acquired:
        connection.close()
        raise RuntimeError("日次バッチはすでに実行中です")
    try:
        yield
    finally:
        connection.execute(
            text("SELECT pg_advisory_unlock(:lock_id)"),
            {"lock_id": DAILY_BATCH_LOCK_ID},
        )
        connection.close()


def start_pipeline_run(database_url: str, run_id: str, pipeline_name: str) -> None:
    with get_engine(database_url).begin() as connection:
        connection.execute(
            insert(pipeline_runs).values(
                id=run_id,
                pipeline_name=pipeline_name,
                status="running",
                summary_json={},
            )
        )


def record_pipeline_item(
    database_url: str, run_id: str, result: BatchItemResult
) -> None:
    with get_engine(database_url).begin() as connection:
        connection.execute(
            insert(pipeline_run_items).values(
                run_id=run_id,
                symbol=result.symbol,
                provider=result.provider,
                status=result.status,
                received_count=result.received,
                upserted_count=result.upserted,
                first_date=date.fromisoformat(result.first_date) if result.first_date else None,
                last_date=date.fromisoformat(result.last_date) if result.last_date else None,
                error_message=result.error_message,
                analysis_json=result.analysis_summary,
            )
        )


def finish_pipeline_run(
    database_url: str,
    run_id: str,
    status: str,
    summary: dict[str, object],
) -> None:
    with get_engine(database_url).begin() as connection:
        connection.execute(
            update(pipeline_runs)
            .where(pipeline_runs.c.id == run_id)
            .values(status=status, finished_at=func.now(), summary_json=summary)
        )


def get_pipeline_run(database_url: str, run_id: str) -> tuple[str, str | None, str]:
    statement = select(
        pipeline_runs.c.status,
        pipeline_runs.c.finished_at,
        pipeline_runs.c.summary_json,
    ).where(pipeline_runs.c.id == run_id)
    with get_engine(database_url).connect() as connection:
        row = connection.execute(statement).first()
    if row is None:
        raise ValueError("指定された実行履歴がありません")
    finished_at = row.finished_at.isoformat() if row.finished_at else None
    return row.status, finished_at, json.dumps(row.summary_json, ensure_ascii=False)


def list_watchlists(database_url: str) -> list[WatchlistSummary]:
    statement = (
        select(watchlists.c.name, func.count(watchlist_items.c.symbol))
        .outerjoin(watchlist_items, watchlist_items.c.watchlist_id == watchlists.c.id)
        .group_by(watchlists.c.id, watchlists.c.name)
        .order_by(watchlists.c.name)
    )
    with get_engine(database_url).connect() as connection:
        rows = connection.execute(statement)
        return [WatchlistSummary(row[0], row[1]) for row in rows]


def add_watchlist_item(
    database_url: str,
    *,
    symbol: str,
    provider: str,
    display_name: str,
    exchange: str,
    currency: str,
    watchlist_name: str = DEFAULT_WATCHLIST,
) -> None:
    with get_engine(database_url).begin() as connection:
        connection.execute(
            pg_insert(watchlists)
            .values(name=watchlist_name)
            .on_conflict_do_nothing(index_elements=[watchlists.c.name])
        )
        watchlist_id = connection.scalar(
            select(watchlists.c.id).where(watchlists.c.name == watchlist_name)
        )
        statement = pg_insert(watchlist_items).values(
            watchlist_id=watchlist_id,
            symbol=symbol.strip().upper(),
            provider=provider,
            display_name=display_name,
            exchange=exchange,
            currency=currency,
        )
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=[
                    watchlist_items.c.watchlist_id,
                    watchlist_items.c.symbol,
                    watchlist_items.c.provider,
                ],
                set_={
                    "display_name": statement.excluded.display_name,
                    "exchange": statement.excluded.exchange,
                    "currency": statement.excluded.currency,
                },
            )
        )


def list_watchlist_items(
    database_url: str,
    watchlist_name: str = DEFAULT_WATCHLIST,
) -> list[WatchlistItem]:
    statement = (
        select(
            watchlist_items.c.symbol,
            watchlist_items.c.provider,
            watchlist_items.c.display_name,
            watchlist_items.c.exchange,
            watchlist_items.c.currency,
        )
        .join(watchlists, watchlists.c.id == watchlist_items.c.watchlist_id)
        .where(watchlists.c.name == watchlist_name)
        .order_by(watchlist_items.c.sort_order, watchlist_items.c.symbol)
    )
    with get_engine(database_url).connect() as connection:
        return [WatchlistItem(*row) for row in connection.execute(statement)]


def remove_watchlist_item(
    database_url: str,
    symbol: str,
    provider: str,
    watchlist_name: str = DEFAULT_WATCHLIST,
) -> bool:
    normalized = symbol.strip().upper()
    with get_engine(database_url).begin() as connection:
        watchlist_id = connection.scalar(
            select(watchlists.c.id).where(watchlists.c.name == watchlist_name)
        )
        if watchlist_id is None:
            return False
        result = connection.execute(
            delete(watchlist_items).where(
                watchlist_items.c.watchlist_id == watchlist_id,
                watchlist_items.c.symbol == normalized,
                watchlist_items.c.provider == provider,
            )
        )
        if result.rowcount:
            connection.execute(
                delete(watchlist_registrations).where(
                    watchlist_registrations.c.symbol == normalized,
                    watchlist_registrations.c.provider == provider,
                    watchlist_registrations.c.watchlist_name == watchlist_name,
                )
            )
        return bool(result.rowcount)


def request_watchlist_registration(
    database_url: str,
    symbol: str,
    provider: str = "jquants",
    watchlist_name: str = DEFAULT_WATCHLIST,
) -> WatchlistRegistration:
    normalized = symbol.strip().upper()
    with get_engine(database_url).begin() as connection:
        active = connection.execute(
            select(watchlist_items.c.display_name)
            .join(watchlists, watchlists.c.id == watchlist_items.c.watchlist_id)
            .where(
                watchlists.c.name == watchlist_name,
                watchlist_items.c.symbol == normalized,
                watchlist_items.c.provider == provider,
            )
        ).first()
        status = "active" if active else "pending"
        display_name = active.display_name if active else None
        statement = pg_insert(watchlist_registrations).values(
            symbol=normalized,
            provider=provider,
            status=status,
            display_name=display_name,
            error_message=None,
            watchlist_name=watchlist_name,
        )
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=[
                    watchlist_registrations.c.symbol,
                    watchlist_registrations.c.provider,
                    watchlist_registrations.c.watchlist_name,
                ],
                set_={
                    "status": status,
                    "display_name": display_name,
                    "error_message": None,
                    "watchlist_name": watchlist_name,
                    "updated_at": func.now(),
                },
            )
        )
    return get_watchlist_registration(
        database_url,
        normalized,
        provider,
        watchlist_name,
    )


def _registration_from_row(row) -> WatchlistRegistration:
    return WatchlistRegistration(
        row.symbol,
        row.provider,
        row.status,
        row.display_name,
        row.error_message,
        row.requested_at.isoformat(),
        row.updated_at.isoformat(),
        row.watchlist_name,
    )


def get_watchlist_registration(
    database_url: str,
    symbol: str,
    provider: str = "jquants",
    watchlist_name: str = DEFAULT_WATCHLIST,
) -> WatchlistRegistration:
    statement = select(watchlist_registrations).where(
        watchlist_registrations.c.symbol == symbol.strip().upper(),
        watchlist_registrations.c.provider == provider,
        watchlist_registrations.c.watchlist_name == watchlist_name,
    )
    with get_engine(database_url).connect() as connection:
        row = connection.execute(statement).first()
    if row is None:
        raise ValueError("指定された登録申請がありません")
    return _registration_from_row(row)


def list_watchlist_registrations(
    database_url: str,
    statuses: tuple[str, ...] = ("pending", "failed"),
) -> list[WatchlistRegistration]:
    statement = (
        select(watchlist_registrations)
        .where(watchlist_registrations.c.status.in_(statuses))
        .order_by(watchlist_registrations.c.requested_at, watchlist_registrations.c.symbol)
    )
    with get_engine(database_url).connect() as connection:
        return [_registration_from_row(row) for row in connection.execute(statement)]


def update_watchlist_registration(
    database_url: str,
    symbol: str,
    provider: str,
    status: str,
    *,
    display_name: str | None = None,
    error_message: str | None = None,
    watchlist_name: str = DEFAULT_WATCHLIST,
) -> None:
    if status not in {"pending", "active", "failed"}:
        raise ValueError("不正な登録状態です")
    values: dict[str, object] = {
        "status": status,
        "error_message": error_message,
        "updated_at": func.now(),
    }
    if display_name is not None:
        values["display_name"] = display_name
    with get_engine(database_url).begin() as connection:
        connection.execute(
            update(watchlist_registrations)
            .where(
                watchlist_registrations.c.symbol == symbol.strip().upper(),
                watchlist_registrations.c.provider == provider,
                watchlist_registrations.c.watchlist_name == watchlist_name,
            )
            .values(**values)
        )


def replace_earnings_calendar(
    database_url: str,
    announcements: Sequence[EarningsAnnouncement],
    provider: str = "jquants",
) -> int:
    with get_engine(database_url).begin() as connection:
        connection.execute(
            delete(earnings_calendar).where(earnings_calendar.c.provider == provider)
        )
        if announcements:
            connection.execute(
                insert(earnings_calendar),
                [
                    {
                        "symbol": item.symbol,
                        "scheduled_date": item.scheduled_date,
                        "company_name": item.company_name,
                        "fiscal_year": item.fiscal_year,
                        "fiscal_quarter": item.fiscal_quarter,
                        "provider": provider,
                    }
                    for item in announcements
                ],
            )
    return len(announcements)


def next_earnings_date(
    database_url: str,
    symbol: str,
    as_of: date,
    provider: str = "jquants",
) -> date | None:
    statement = select(func.min(earnings_calendar.c.scheduled_date)).where(
        earnings_calendar.c.symbol == symbol.strip().upper(),
        earnings_calendar.c.provider == provider,
        earnings_calendar.c.scheduled_date >= as_of,
    )
    with get_engine(database_url).connect() as connection:
        return connection.scalar(statement)


def record_data_sync(
    database_url: str,
    dataset: str,
    status: str,
    error_message: str | None = None,
) -> None:
    if status not in {"success", "failed"}:
        raise ValueError("不正な同期状態です")
    statement = pg_insert(data_sync_status).values(
        dataset=dataset,
        status=status,
        error_message=error_message,
    )
    with get_engine(database_url).begin() as connection:
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=[data_sync_status.c.dataset],
                set_={
                    "status": statement.excluded.status,
                    "error_message": statement.excluded.error_message,
                    "synced_at": func.now(),
                },
            )
        )


def data_sync_succeeded(database_url: str, dataset: str) -> bool:
    statement = select(data_sync_status.c.status).where(
        data_sync_status.c.dataset == dataset
    )
    with get_engine(database_url).connect() as connection:
        return connection.scalar(statement) == "success"


def upsert_instruments(database_url: str, items: Sequence[dict[str, object]]) -> int:
    """上場銘柄マスタを更新する。"""
    if not items:
        return 0
    with get_engine(database_url).begin() as connection:
        for chunk in _chunks(items):
            statement = pg_insert(instruments).values(list(chunk))
            connection.execute(
                statement.on_conflict_do_update(
                    index_elements=[instruments.c.symbol, instruments.c.provider],
                    set_={
                        "display_name": statement.excluded.display_name,
                        "english_name": statement.excluded.english_name,
                        "market": statement.excluded.market,
                        "sector_17_code": statement.excluded.sector_17_code,
                        "sector_17_name": statement.excluded.sector_17_name,
                        "sector_33_code": statement.excluded.sector_33_code,
                        "sector_33_name": statement.excluded.sector_33_name,
                        "instrument_type": statement.excluded.instrument_type,
                        "is_active": statement.excluded.is_active,
                        "as_of_date": statement.excluded.as_of_date,
                        "updated_at": func.now(),
                    },
                )
            )
    return len(items)


def deactivate_instruments(database_url: str, provider: str) -> None:
    """最新マスタへ存在しない銘柄を識別するため取得元全体を一度非活性にする。"""
    with get_engine(database_url).begin() as connection:
        connection.execute(
            update(instruments)
            .where(instruments.c.provider == provider)
            .values(is_active=False, updated_at=func.now())
        )


def replace_instruments(
    database_url: str,
    provider: str,
    items: Sequence[dict[str, object]],
) -> int:
    """取得元の銘柄マスタを単一トランザクションで置換する。"""
    with get_engine(database_url).begin() as connection:
        connection.execute(
            update(instruments)
            .where(instruments.c.provider == provider)
            .values(is_active=False, updated_at=func.now())
        )
        for chunk in _chunks(items):
            statement = pg_insert(instruments).values(list(chunk))
            connection.execute(
                statement.on_conflict_do_update(
                    index_elements=[instruments.c.symbol, instruments.c.provider],
                    set_={
                        "display_name": statement.excluded.display_name,
                        "english_name": statement.excluded.english_name,
                        "market": statement.excluded.market,
                        "sector_17_code": statement.excluded.sector_17_code,
                        "sector_17_name": statement.excluded.sector_17_name,
                        "sector_33_code": statement.excluded.sector_33_code,
                        "sector_33_name": statement.excluded.sector_33_name,
                        "instrument_type": statement.excluded.instrument_type,
                        "is_active": statement.excluded.is_active,
                        "as_of_date": statement.excluded.as_of_date,
                        "updated_at": func.now(),
                    },
                )
            )
    return len(items)


def search_instruments(
    database_url: str,
    query: str = "",
    *,
    limit: int = 50,
) -> list[Instrument]:
    pattern = f"%{query.strip()}%"
    statement = select(
        instruments.c.symbol,
        instruments.c.provider,
        instruments.c.display_name,
        instruments.c.market,
        instruments.c.sector_33_name,
        instruments.c.instrument_type,
        instruments.c.is_active,
    ).where(instruments.c.is_active.is_(True))
    if query.strip():
        statement = statement.where(
            instruments.c.symbol.ilike(pattern)
            | instruments.c.display_name.ilike(pattern)
        )
    statement = statement.order_by(instruments.c.symbol).limit(min(limit, 200))
    with get_engine(database_url).connect() as connection:
        return [Instrument(*row) for row in connection.execute(statement)]


def get_instruments_by_symbols(
    database_url: str,
    symbols: Sequence[str],
    provider: str = "jquants",
) -> list[Instrument]:
    """指定された証券コードに一致する銘柄マスタを返す。"""
    normalized = {symbol.strip().upper() for symbol in symbols if symbol.strip()}
    if not normalized:
        return []
    statement = select(
        instruments.c.symbol,
        instruments.c.provider,
        instruments.c.display_name,
        instruments.c.market,
        instruments.c.sector_33_name,
        instruments.c.instrument_type,
        instruments.c.is_active,
    ).where(
        instruments.c.symbol.in_(normalized),
        instruments.c.provider == provider,
        instruments.c.is_active.is_(True),
    )
    with get_engine(database_url).connect() as connection:
        return [Instrument(*row) for row in connection.execute(statement)]


def list_analysis_universe(
    database_url: str,
    *,
    provider: str = "jquants",
    limit: int = 500,
) -> list[WatchlistItem]:
    """十分な日足があり売買代金上位の普通株を分析対象として返す。"""
    query = text(
        """
        WITH coverage_start AS (
            SELECT symbol, provider,
                   MIN(trade_date) FILTER (WHERE is_adjusted) AS first_adjusted
            FROM daily_bars
            WHERE provider = :provider AND symbol <> 'TOPIX'
            GROUP BY symbol, provider
        ), recent AS (
            SELECT symbol, provider, close, volume,
                   ROW_NUMBER() OVER (
                       PARTITION BY symbol, provider ORDER BY trade_date DESC
                   ) AS row_number
            FROM daily_bars
            WHERE provider = :provider
              AND symbol <> 'TOPIX'
              AND is_adjusted = true
        ), quality AS (
            SELECT d.symbol, d.provider
            FROM daily_bars d
            JOIN coverage_start c
              ON c.symbol = d.symbol AND c.provider = d.provider
            WHERE d.provider = :provider
              AND d.symbol <> 'TOPIX'
              AND c.first_adjusted IS NOT NULL
              AND d.trade_date >= c.first_adjusted
            GROUP BY d.symbol, d.provider
            HAVING BOOL_AND(d.is_adjusted)
        ), liquid AS (
            SELECT symbol, provider, AVG(close * volume) AS average_turnover
            FROM recent
            WHERE row_number <= 60
            GROUP BY symbol, provider
            HAVING COUNT(*) >= 25
        )
        SELECT i.symbol, i.provider, i.display_name, i.market, 'JPY'
        FROM liquid l
        JOIN quality q ON q.symbol = l.symbol AND q.provider = l.provider
        JOIN instruments i ON i.symbol = l.symbol AND i.provider = l.provider
        WHERE i.is_active = true
          AND i.sector_33_code IS NOT NULL
        ORDER BY l.average_turnover DESC, i.symbol
        LIMIT :limit
        """
    )
    with get_engine(database_url).connect() as connection:
        rows = connection.execute(
            query,
            {"provider": provider, "limit": min(limit, 2000)},
        )
        return [WatchlistItem(*row) for row in rows]


def upsert_analysis_snapshot(
    database_url: str,
    *,
    symbol: str,
    provider: str,
    as_of_date: date,
    horizon_days: int,
    direction: str,
    action: str,
    evidence_score: float,
    analysis_json: dict[str, object],
) -> None:
    statement = pg_insert(analysis_snapshots).values(
        symbol=symbol,
        provider=provider,
        as_of_date=as_of_date,
        horizon_days=horizon_days,
        direction=direction,
        action=action,
        evidence_score=evidence_score,
        analysis_json=analysis_json,
    )
    with get_engine(database_url).begin() as connection:
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=[
                    analysis_snapshots.c.symbol,
                    analysis_snapshots.c.provider,
                    analysis_snapshots.c.as_of_date,
                    analysis_snapshots.c.horizon_days,
                ],
                set_={
                    "direction": statement.excluded.direction,
                    "action": statement.excluded.action,
                    "evidence_score": statement.excluded.evidence_score,
                    "analysis_json": statement.excluded.analysis_json,
                    "created_at": func.now(),
                },
            )
        )


def clear_analysis_snapshots(
    database_url: str,
    *,
    provider: str,
    horizon_days: int,
) -> int:
    """再生成前に同じ取得元・期間の市場分析スナップショットを削除する。"""
    with get_engine(database_url).begin() as connection:
        result = connection.execute(
            delete(analysis_snapshots).where(
                analysis_snapshots.c.provider == provider,
                analysis_snapshots.c.horizon_days == horizon_days,
            )
        )
        return int(result.rowcount or 0)


def list_market_candidates(
    database_url: str,
    *,
    horizon_days: int = 5,
    action: str | None = None,
    limit: int = 100,
) -> list[dict[str, object]]:
    """全市場スクリーニングの最新候補を返す。"""
    coverage_start = (
        select(
            daily_bars.c.symbol,
            daily_bars.c.provider,
            func.min(daily_bars.c.trade_date)
            .filter(daily_bars.c.is_adjusted.is_(True))
            .label("first_adjusted"),
        )
        .where(daily_bars.c.provider == "jquants")
        .group_by(daily_bars.c.symbol, daily_bars.c.provider)
        .subquery()
    )
    adjusted_quality = (
        select(daily_bars.c.symbol, daily_bars.c.provider)
        .join(
            coverage_start,
            (coverage_start.c.symbol == daily_bars.c.symbol)
            & (coverage_start.c.provider == daily_bars.c.provider),
        )
        .where(
            daily_bars.c.provider == "jquants",
            coverage_start.c.first_adjusted.is_not(None),
            daily_bars.c.trade_date >= coverage_start.c.first_adjusted,
        )
        .group_by(daily_bars.c.symbol, daily_bars.c.provider)
        .having(func.bool_and(daily_bars.c.is_adjusted))
        .subquery()
    )
    latest_date = select(func.max(analysis_snapshots.c.as_of_date)).where(
        analysis_snapshots.c.horizon_days == horizon_days,
        analysis_snapshots.c.provider == "jquants",
    ).scalar_subquery()
    statement = (
        select(
            analysis_snapshots.c.symbol,
            instruments.c.display_name,
            analysis_snapshots.c.provider,
            analysis_snapshots.c.as_of_date,
            analysis_snapshots.c.direction,
            analysis_snapshots.c.action,
            analysis_snapshots.c.evidence_score,
            analysis_snapshots.c.analysis_json,
        )
        .join(
            instruments,
            (instruments.c.symbol == analysis_snapshots.c.symbol)
            & (instruments.c.provider == analysis_snapshots.c.provider),
        )
        .join(
            adjusted_quality,
            (adjusted_quality.c.symbol == analysis_snapshots.c.symbol)
            & (adjusted_quality.c.provider == analysis_snapshots.c.provider),
        )
        .where(
            analysis_snapshots.c.horizon_days == horizon_days,
            analysis_snapshots.c.as_of_date == latest_date,
        )
    )
    if action:
        statement = statement.where(analysis_snapshots.c.action == action)
    statement = statement.order_by(
        analysis_snapshots.c.evidence_score.desc(),
        analysis_snapshots.c.symbol,
    ).limit(min(limit, 500))
    with get_engine(database_url).connect() as connection:
        items = []
        for row in connection.execute(statement):
            analysis_data = row[7] or {}
            transition = analysis_data.get("transition_readiness") or {}
            items.append({
                "symbol": row[0],
                "display_name": row[1],
                "provider": row[2],
                "as_of_date": row[3].isoformat(),
                "direction": row[4],
                "action": row[5],
                "evidence_score": row[6],
                "transition_phase": transition.get("phase", "unknown"),
                "transition_summary": transition.get("summary"),
                "transition_satisfied": transition.get("satisfied_conditions"),
                "transition_total": transition.get("total_conditions"),
                "transition_next_condition": transition.get("next_condition"),
                "transition_trigger_price": transition.get("trigger_price"),
            })
        return items


def upsert_position(
    database_url: str,
    *,
    symbol: str,
    provider: str,
    display_name: str,
    quantity: Decimal,
    average_cost: Decimal | None,
    account_type: str = "未設定",
    memo: str | None = None,
    portfolio_name: str = DEFAULT_PORTFOLIO,
) -> None:
    if (
        not quantity.is_finite()
        or quantity < 0
        or (
            average_cost is not None
            and (not average_cost.is_finite() or average_cost < 0)
        )
    ):
        raise ValueError("保有数量と平均取得単価は0以上で指定してください")
    with get_engine(database_url).begin() as connection:
        connection.execute(
            pg_insert(portfolios)
            .values(name=portfolio_name, base_currency="JPY")
            .on_conflict_do_nothing(index_elements=[portfolios.c.name])
        )
        portfolio_id = connection.scalar(
            select(portfolios.c.id).where(portfolios.c.name == portfolio_name)
        )
        statement = pg_insert(positions).values(
            portfolio_id=portfolio_id,
            symbol=symbol.strip().upper(),
            provider=provider,
            display_name=display_name,
            quantity=quantity,
            average_cost=average_cost,
            account_type=account_type,
            memo=memo,
        )
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=[
                    positions.c.portfolio_id,
                    positions.c.symbol,
                    positions.c.provider,
                ],
                set_={
                    "display_name": statement.excluded.display_name,
                    "quantity": statement.excluded.quantity,
                    "average_cost": statement.excluded.average_cost,
                    "account_type": statement.excluded.account_type,
                    "memo": statement.excluded.memo,
                    "updated_at": func.now(),
                },
            )
        )


def list_positions(
    database_url: str,
    portfolio_name: str = DEFAULT_PORTFOLIO,
) -> list[Position]:
    latest = (
        select(
            daily_bars.c.symbol,
            daily_bars.c.provider,
            daily_bars.c.close,
            daily_bars.c.trade_date,
            func.row_number()
            .over(
                partition_by=(daily_bars.c.symbol, daily_bars.c.provider),
                order_by=daily_bars.c.trade_date.desc(),
            )
            .label("row_number"),
        )
        .where(
            (daily_bars.c.provider != "jquants")
            | (daily_bars.c.symbol == "TOPIX")
            | daily_bars.c.is_adjusted.is_(True)
        )
        .subquery()
    )
    statement = (
        select(
            portfolios.c.name,
            positions.c.symbol,
            positions.c.provider,
            positions.c.display_name,
            positions.c.quantity,
            positions.c.average_cost,
            positions.c.account_type,
            positions.c.memo,
            latest.c.close,
            latest.c.trade_date,
        )
        .join(portfolios, portfolios.c.id == positions.c.portfolio_id)
        .outerjoin(
            latest,
            (latest.c.symbol == positions.c.symbol)
            & (latest.c.provider == positions.c.provider)
            & (latest.c.row_number == 1),
        )
        .where(portfolios.c.name == portfolio_name, positions.c.quantity > 0)
        .order_by(positions.c.symbol)
    )
    with get_engine(database_url).connect() as connection:
        rows = connection.execute(statement).all()
    return [
        Position(
            portfolio_name=row[0],
            symbol=row[1],
            provider=row[2],
            display_name=row[3],
            quantity=Decimal(row[4]),
            average_cost=Decimal(row[5]) if row[5] is not None else None,
            account_type=row[6],
            memo=row[7],
            latest_close=Decimal(row[8]) if row[8] is not None else None,
            latest_trade_date=row[9].isoformat() if row[9] else None,
        )
        for row in rows
    ]


def remove_position(
    database_url: str,
    symbol: str,
    provider: str = "jquants",
    portfolio_name: str = DEFAULT_PORTFOLIO,
) -> bool:
    with get_engine(database_url).begin() as connection:
        portfolio_id = connection.scalar(
            select(portfolios.c.id).where(portfolios.c.name == portfolio_name)
        )
        if portfolio_id is None:
            return False
        result = connection.execute(
            delete(positions).where(
                positions.c.portfolio_id == portfolio_id,
                positions.c.symbol == symbol.strip().upper(),
                positions.c.provider == provider,
            )
        )
        return bool(result.rowcount)


def record_bulk_file(
    database_url: str,
    *,
    file_key: str,
    endpoint: str,
    target_date: date,
    status: str,
    row_count: int = 0,
    checksum: str | None = None,
    error_message: str | None = None,
) -> None:
    statement = pg_insert(bulk_files).values(
        file_key=file_key,
        endpoint=endpoint,
        target_date=target_date,
        status=status,
        row_count=row_count,
        checksum=checksum,
        error_message=error_message,
    )
    with get_engine(database_url).begin() as connection:
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=[bulk_files.c.file_key],
                set_={
                    "status": statement.excluded.status,
                    "row_count": statement.excluded.row_count,
                    "checksum": statement.excluded.checksum,
                    "error_message": statement.excluded.error_message,
                    "updated_at": func.now(),
                },
            )
        )


def bulk_file_succeeded(database_url: str, file_key: str) -> bool:
    """日足を保存した正常終了ファイルだけを再取得対象外とする。"""
    statement = select(bulk_files.c.status, bulk_files.c.row_count).where(
        bulk_files.c.file_key == file_key
    )
    with get_engine(database_url).connect() as connection:
        row = connection.execute(statement).one_or_none()
        return row is not None and row.status == "success" and row.row_count > 0


def record_bulk_adjustment(
    database_url: str,
    file_key: str,
    status: str,
    *,
    row_count: int = 0,
    error_message: str | None = None,
) -> None:
    """バルク期間を調整済み日足へ変換した状態を記録する。"""
    with get_engine(database_url).begin() as connection:
        connection.execute(
            update(bulk_files)
            .where(bulk_files.c.file_key == file_key)
            .values(
                adjusted_status=status,
                adjusted_row_count=row_count,
                adjusted_error_message=error_message,
                updated_at=func.now(),
            )
        )


def bulk_adjustment_succeeded(database_url: str, file_key: str) -> bool:
    """調整済み全市場日足の保存まで完了したファイルか返す。"""
    statement = select(
        bulk_files.c.adjusted_status,
        bulk_files.c.adjusted_row_count,
    ).where(bulk_files.c.file_key == file_key)
    with get_engine(database_url).connect() as connection:
        row = connection.execute(statement).one_or_none()
    return bool(
        row is not None
        and row.adjusted_status == "success"
        and row.adjusted_row_count > 0
    )


def latest_bulk_file_date(database_url: str, endpoint: str) -> date | None:
    """正常に取り込んだバルクファイルの最新対象日を返す。"""
    statement = select(func.max(bulk_files.c.target_date)).where(
        bulk_files.c.endpoint == endpoint,
        bulk_files.c.status == "success",
        bulk_files.c.row_count > 0,
        bulk_files.c.adjusted_status == "success",
        bulk_files.c.adjusted_row_count > 0,
    )
    with get_engine(database_url).connect() as connection:
        return connection.scalar(statement)


def bulk_sync_status(database_url: str, endpoint: str) -> dict[str, int]:
    """バルクファイルの調整済み化について、完了・未完了件数を返す。"""
    completed = (
        (bulk_files.c.status == "success")
        & (bulk_files.c.row_count > 0)
        & (bulk_files.c.adjusted_status == "success")
        & (bulk_files.c.adjusted_row_count > 0)
    )
    failed = (bulk_files.c.status == "failed") | (
        bulk_files.c.adjusted_status == "failed"
    )
    statement = select(
        func.count().label("total"),
        func.count().filter(completed).label("completed"),
        func.count().filter(failed).label("failed"),
    ).where(bulk_files.c.endpoint == endpoint)
    with get_engine(database_url).connect() as connection:
        row = connection.execute(statement).one()
    total = int(row.total)
    completed_count = int(row.completed)
    return {
        "total": total,
        "completed": completed_count,
        "incomplete": total - completed_count,
        "failed": int(row.failed),
    }


def list_bulk_sync_issues(
    database_url: str,
    endpoint: str,
    *,
    limit: int = 20,
) -> list[dict[str, object]]:
    """未完了バルクファイルと短縮した失敗理由を返す。"""
    completed = (
        (bulk_files.c.status == "success")
        & (bulk_files.c.row_count > 0)
        & (bulk_files.c.adjusted_status == "success")
        & (bulk_files.c.adjusted_row_count > 0)
    )
    statement = (
        select(
            bulk_files.c.file_key,
            bulk_files.c.target_date,
            bulk_files.c.status,
            bulk_files.c.row_count,
            bulk_files.c.adjusted_status,
            bulk_files.c.adjusted_row_count,
            func.coalesce(
                bulk_files.c.adjusted_error_message,
                bulk_files.c.error_message,
            ).label("error_message"),
        )
        .where(
            bulk_files.c.endpoint == endpoint,
            ~completed,
        )
        .order_by(bulk_files.c.target_date, bulk_files.c.file_key)
        .limit(min(max(limit, 1), 100))
    )
    with get_engine(database_url).connect() as connection:
        rows = connection.execute(statement).all()
    return [
        {
            "file_key": row.file_key,
            "target_date": row.target_date.isoformat(),
            "raw_status": row.status,
            "raw_rows": row.row_count,
            "adjusted_status": row.adjusted_status,
            "adjusted_rows": row.adjusted_row_count,
            "error": (
                str(row.error_message).splitlines()[0][:500]
                if row.error_message
                else "失敗理由が記録されていません"
            ),
        }
        for row in rows
    ]


def list_latest_predictions(
    database_url: str,
    *,
    horizon_days: int,
    direction: str | None = None,
    minimum_probability: float = 0.0,
    watchlist_name: str = DEFAULT_WATCHLIST,
) -> list[PredictionSummary]:
    if horizon_days not in {1, 5, 20}:
        raise ValueError("予測期間は1、5、20営業日のいずれかで指定してください")
    query = text(
        """
        WITH latest AS (
            SELECT p.symbol, p.provider, MAX(p.as_of_date) AS as_of_date
            FROM predictions p
            JOIN model_versions m ON m.id = p.model_version_id
            WHERE p.horizon_days = :horizon AND m.status = 'approved'
            GROUP BY p.symbol, p.provider
        )
        SELECT p.symbol, i.display_name, p.provider, p.as_of_date, p.horizon_days,
               p.probability_up, p.probability_flat, p.probability_down,
               p.predicted_class, p.rank_score, p.model_version_id
        FROM predictions p
        JOIN latest l ON l.symbol = p.symbol AND l.provider = p.provider
                     AND l.as_of_date = p.as_of_date
        JOIN model_versions m ON m.id = p.model_version_id AND m.status = 'approved'
        JOIN watchlist_items i ON i.symbol = p.symbol AND i.provider = p.provider
        JOIN watchlists w ON w.id = i.watchlist_id AND w.name = :watchlist
        WHERE p.horizon_days = :horizon
          AND (:direction IS NULL OR p.predicted_class = :direction)
          AND GREATEST(
              p.probability_up, p.probability_flat, p.probability_down
          ) >= :minimum_probability
        ORDER BY p.rank_score DESC, p.symbol
        """
    )
    with get_engine(database_url).connect() as connection:
        rows = connection.execute(
            query,
            {
                "horizon": horizon_days,
                "watchlist": watchlist_name,
                "direction": direction,
                "minimum_probability": minimum_probability,
            },
        ).all()
    return [
        PredictionSummary(
            row[0],
            row[1],
            row[2],
            row[3].isoformat(),
            row[4],
            row[5],
            row[6],
            row[7],
            row[8],
            row[9],
            row[10],
        )
        for row in rows
    ]


def reset_database(database_url: str) -> None:
    """テスト用に全業務テーブルを空へ戻し、既定値を再作成する。"""
    table_names = [
        bulk_files.name,
        analysis_snapshots.name,
        positions.name,
        portfolios.name,
        watchlist_registrations.name,
        watchlist_items.name,
        watchlists.name,
        earnings_calendar.name,
        data_sync_status.name,
        pipeline_run_items.name,
        pipeline_runs.name,
        predictions.name,
        model_versions.name,
        daily_bars.name,
        instruments.name,
        app_metadata.name,
    ]
    quoted = ", ".join(f'"{name}"' for name in table_names)
    with get_engine(database_url).begin() as connection:
        connection.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))
        connection.execute(insert(app_metadata).values(key="schema_version", value="7"))
        connection.execute(insert(watchlists).values(name=DEFAULT_WATCHLIST))
        connection.execute(
            insert(portfolios).values(name=DEFAULT_PORTFOLIO, base_currency="JPY")
        )
