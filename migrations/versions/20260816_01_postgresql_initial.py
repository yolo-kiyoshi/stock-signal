"""PostgreSQL初期スキーマを作成する。

Revision ID: 20260816_01
Revises:
Create Date: 2026-08-16
"""

from alembic import op
from sqlalchemy.dialects.postgresql import insert

from stock_signal.persistence.schema import (
    app_metadata,
    metadata,
    portfolios,
    watchlists,
)

revision = "20260816_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """全テーブルを作成し、既定リストを登録する。"""
    bind = op.get_bind()
    metadata.create_all(bind=bind)
    bind.execute(
        insert(app_metadata)
        .values(key="schema_version", value="6")
        .on_conflict_do_update(
            index_elements=[app_metadata.c.key],
            set_={"value": "6"},
        )
    )
    bind.execute(insert(watchlists).values(name="ウォッチ").on_conflict_do_nothing())
    bind.execute(
        insert(portfolios)
        .values(name="メインポートフォリオ", base_currency="JPY")
        .on_conflict_do_nothing()
    )


def downgrade() -> None:
    """初期スキーマを削除する。"""
    metadata.drop_all(bind=op.get_bind())
