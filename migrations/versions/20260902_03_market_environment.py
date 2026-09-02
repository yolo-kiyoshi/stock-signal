"""寄り付き前の市場環境データを追加する。

Revision ID: 20260902_03
Revises: 20260816_02
Create Date: 2026-09-02
"""

from alembic import op
from sqlalchemy.dialects.postgresql import insert

from stock_signal.persistence.schema import (
    app_metadata,
    market_observations,
    market_regime_snapshots,
)

revision = "20260902_03"
down_revision = "20260816_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """外部指標と日次の市場環境判定を保存できるようにする。"""
    bind = op.get_bind()
    market_observations.create(bind=bind, checkfirst=True)
    market_regime_snapshots.create(bind=bind, checkfirst=True)
    bind.execute(
        insert(app_metadata)
        .values(key="schema_version", value="8")
        .on_conflict_do_update(
            index_elements=[app_metadata.c.key],
            set_={"value": "8"},
        )
    )


def downgrade() -> None:
    """市場環境テーブルを削除する。"""
    market_regime_snapshots.drop(bind=op.get_bind(), checkfirst=True)
    market_observations.drop(bind=op.get_bind(), checkfirst=True)

