from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Engine, create_engine, text


@lru_cache(maxsize=8)
def get_engine(database_url: str) -> Engine:
    """接続先ごとのコネクションプールを返す。"""
    return create_engine(
        database_url,
        pool_pre_ping=True,
        pool_recycle=1800,
        future=True,
    )


def check_database(database_url: str) -> None:
    """PostgreSQLへの接続とマイグレーション適用を確認する。"""
    with get_engine(database_url).connect() as connection:
        connection.execute(text("SELECT 1 FROM app_metadata LIMIT 1"))
