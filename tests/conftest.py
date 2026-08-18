from __future__ import annotations

import os

import pytest

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://tomoshibiyori:test-password@test-db:5432/"
    "tomoshibiyori_test",
)

# Webアプリの設定はテスト収集時に読み込まれるため、各テストモジュールより先に固定する。
os.environ["DATABASE_URL"] = TEST_DATABASE_URL


@pytest.fixture(scope="session")
def database_url() -> str:
    """開発DBとは分離したPostgreSQLテストDBを初期化する。"""
    from stock_signal.database import initialize_database

    initialize_database(TEST_DATABASE_URL)
    return TEST_DATABASE_URL


@pytest.fixture(autouse=True)
def clean_database(database_url: str) -> None:
    """テスト間でデータが混ざらないよう業務テーブルを初期化する。"""
    from stock_signal.database import reset_database

    reset_database(database_url)
