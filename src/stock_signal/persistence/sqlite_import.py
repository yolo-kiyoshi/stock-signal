from __future__ import annotations

import json
import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy.dialects.postgresql import insert as pg_insert

from stock_signal.database import (
    DEFAULT_WATCHLIST,
    add_watchlist_item,
    record_data_sync,
    replace_earnings_calendar,
    request_watchlist_registration,
    update_watchlist_registration,
    upsert_daily_bars,
)
from stock_signal.domain.market_data import DailyBar, EarningsAnnouncement
from stock_signal.persistence.engine import get_engine
from stock_signal.persistence.schema import pipeline_run_items, pipeline_runs


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _parse_datetime(value: str | None) -> datetime | None:
    """SQLiteに文字列保存された日時をPostgreSQLの日時型へ変換する。"""
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@contextmanager
def _open_sqlite_snapshot(source: Path) -> Iterator[sqlite3.Connection]:
    """読み取り専用マウント上のSQLiteとWALを一時領域から安全に読む。"""
    with TemporaryDirectory(prefix="tomoshibiyori-sqlite-") as directory:
        snapshot = Path(directory) / source.name
        shutil.copy2(source, snapshot)
        wal_file = Path(f"{source}-wal")
        if wal_file.is_file():
            shutil.copy2(wal_file, Path(f"{snapshot}-wal"))
        connection = sqlite3.connect(snapshot)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        try:
            yield connection
        finally:
            connection.close()


def import_sqlite_database(source: Path, database_url: str) -> dict[str, int]:
    """旧SQLiteの利用者データをPostgreSQLへ一度だけ移行する。"""
    if not source.is_file():
        raise ValueError(f"SQLite移行元が見つかりません: {source}")
    counts = {
        "daily_bars": 0,
        "watchlist_items": 0,
        "registrations": 0,
        "earnings": 0,
        "sync_status": 0,
        "pipeline_runs": 0,
    }
    with _open_sqlite_snapshot(source) as sqlite:
        if _table_exists(sqlite, "daily_bars"):
            rows = sqlite.execute(
                """
                SELECT symbol, trade_date, open, high, low, close,
                       volume, provider, is_adjusted
                FROM daily_bars ORDER BY trade_date, symbol
                """
            ).fetchall()
            bars = [
                DailyBar(
                    symbol=row["symbol"],
                    trade_date=date.fromisoformat(row["trade_date"]),
                    open=Decimal(row["open"]),
                    high=Decimal(row["high"]),
                    low=Decimal(row["low"]),
                    close=Decimal(row["close"]),
                    volume=row["volume"],
                    provider=row["provider"],
                    is_adjusted=bool(row["is_adjusted"]),
                )
                for row in rows
            ]
            counts["daily_bars"] = upsert_daily_bars(database_url, bars)

        if _table_exists(sqlite, "watchlist_items"):
            rows = sqlite.execute(
                """
                SELECT w.name, i.symbol, i.provider, i.display_name,
                       i.exchange, i.currency
                FROM watchlist_items i JOIN watchlists w ON w.id = i.watchlist_id
                """
            ).fetchall()
            for row in rows:
                list_name = DEFAULT_WATCHLIST if row["name"] == "メイン" else row["name"]
                add_watchlist_item(
                    database_url,
                    symbol=row["symbol"],
                    provider=row["provider"],
                    display_name=row["display_name"],
                    exchange=row["exchange"],
                    currency=row["currency"],
                    watchlist_name=list_name,
                )
            counts["watchlist_items"] = len(rows)

        if _table_exists(sqlite, "watchlist_registrations"):
            rows = sqlite.execute(
                """
                SELECT symbol, provider, status, display_name, error_message
                FROM watchlist_registrations
                """
            ).fetchall()
            for row in rows:
                request_watchlist_registration(
                    database_url,
                    row["symbol"],
                    row["provider"],
                )
                update_watchlist_registration(
                    database_url,
                    row["symbol"],
                    row["provider"],
                    row["status"],
                    display_name=row["display_name"],
                    error_message=row["error_message"],
                )
            counts["registrations"] = len(rows)

        if _table_exists(sqlite, "earnings_calendar"):
            rows = sqlite.execute(
                """
                SELECT symbol, scheduled_date, company_name,
                       fiscal_year, fiscal_quarter, provider
                FROM earnings_calendar
                """
            ).fetchall()
            grouped: dict[str, list[EarningsAnnouncement]] = {}
            for row in rows:
                grouped.setdefault(row["provider"], []).append(
                    EarningsAnnouncement(
                        symbol=row["symbol"],
                        scheduled_date=date.fromisoformat(row["scheduled_date"]),
                        company_name=row["company_name"],
                        fiscal_year=row["fiscal_year"],
                        fiscal_quarter=row["fiscal_quarter"],
                    )
                )
            for provider, announcements in grouped.items():
                replace_earnings_calendar(database_url, announcements, provider)
            counts["earnings"] = len(rows)

        if _table_exists(sqlite, "data_sync_status"):
            rows = sqlite.execute(
                "SELECT dataset, status, error_message FROM data_sync_status"
            ).fetchall()
            for row in rows:
                record_data_sync(
                    database_url,
                    row["dataset"],
                    row["status"],
                    row["error_message"],
                )
            counts["sync_status"] = len(rows)

        if _table_exists(sqlite, "pipeline_runs"):
            run_rows = sqlite.execute("SELECT * FROM pipeline_runs").fetchall()
            item_rows = sqlite.execute("SELECT * FROM pipeline_run_items").fetchall()
            with get_engine(database_url).begin() as connection:
                for row in run_rows:
                    values = dict(row)
                    values["summary_json"] = json.loads(values["summary_json"] or "{}")
                    values["started_at"] = _parse_datetime(values.get("started_at"))
                    values["finished_at"] = _parse_datetime(values.get("finished_at"))
                    connection.execute(
                        pg_insert(pipeline_runs)
                        .values(**values)
                        .on_conflict_do_nothing(index_elements=[pipeline_runs.c.id])
                    )
                for row in item_rows:
                    values = dict(row)
                    values["first_date"] = (
                        date.fromisoformat(values["first_date"])
                        if values["first_date"]
                        else None
                    )
                    values["last_date"] = (
                        date.fromisoformat(values["last_date"])
                        if values["last_date"]
                        else None
                    )
                    values["analysis_json"] = (
                        json.loads(values["analysis_json"])
                        if values["analysis_json"]
                        else None
                    )
                    connection.execute(
                        pg_insert(pipeline_run_items)
                        .values(**values)
                        .on_conflict_do_nothing(
                            index_elements=[
                                pipeline_run_items.c.run_id,
                                pipeline_run_items.c.symbol,
                                pipeline_run_items.c.provider,
                            ]
                        )
                    )
            counts["pipeline_runs"] = len(run_rows)
    return counts
