"""財務開示履歴と資金制約を追加する。"""

from alembic import op

from stock_signal.persistence.schema import financial_disclosures, portfolio_risk_settings

revision = "20260906_04"
down_revision = "20260902_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """既存データを維持して分析用テーブルを追加する。"""
    from stock_signal.research_store import invalidate_candidate_snapshots

    invalidate_candidate_snapshots(op.get_bind())
    financial_disclosures.create(op.get_bind(), checkfirst=True)
    portfolio_risk_settings.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    """追加テーブルを削除する。"""
    portfolio_risk_settings.drop(op.get_bind(), checkfirst=True)
    financial_disclosures.drop(op.get_bind(), checkfirst=True)
