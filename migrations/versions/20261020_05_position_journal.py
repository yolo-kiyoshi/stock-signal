"""保有残高の履歴、投資ストーリー、チャットレビューを追加する。"""

from alembic import op

from stock_signal.persistence.schema import (
    investment_theses,
    portfolio_imports,
    position_review_contexts,
    position_reviews,
)

revision = "20261020_05"
down_revision = "20260906_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """既存の保有情報を維持して履歴テーブルを追加する。"""
    for table in (
        portfolio_imports,
        investment_theses,
        position_review_contexts,
        position_reviews,
    ):
        table.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    """参照順に履歴テーブルを削除する。"""
    for table in (
        position_reviews,
        position_review_contexts,
        investment_theses,
        portfolio_imports,
    ):
        table.drop(op.get_bind(), checkfirst=True)
