"""BPSの資料確認履歴を追加する。"""

from alembic import op

from stock_signal.persistence.schema import bps_reviews

revision = "20261027_06"
down_revision = "20261020_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """既存データを維持して確認履歴を追加する。"""
    bps_reviews.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    """確認履歴テーブルを削除する。"""
    bps_reviews.drop(op.get_bind(), checkfirst=True)
