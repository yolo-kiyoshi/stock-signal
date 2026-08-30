"""日足の未調整値とバルク調整状態を追加する。

Revision ID: 20260816_02
Revises: 20260816_01
Create Date: 2026-08-16
"""

import sqlalchemy as sa
from alembic import op

revision = "20260816_02"
down_revision = "20260816_01"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    """未調整OHLCVを保持し、調整済み化を再開可能にする。"""
    daily_columns = _column_names("daily_bars")
    daily_column_definitions = {
        "raw_open": sa.Numeric(20, 6),
        "raw_high": sa.Numeric(20, 6),
        "raw_low": sa.Numeric(20, 6),
        "raw_close": sa.Numeric(20, 6),
        "raw_volume": sa.BigInteger(),
        "adjustment_factor": sa.Numeric(20, 10),
    }
    for name, column_type in daily_column_definitions.items():
        if name not in daily_columns:
            op.add_column("daily_bars", sa.Column(name, column_type))
    op.execute(
        """
        UPDATE daily_bars
        SET raw_open = open,
            raw_high = high,
            raw_low = low,
            raw_close = close,
            raw_volume = volume
        WHERE is_adjusted = false
        """
    )

    bulk_columns = _column_names("bulk_files")
    if "adjusted_status" not in bulk_columns:
        op.add_column(
            "bulk_files",
            sa.Column(
                "adjusted_status",
                sa.String(20),
                nullable=False,
                server_default="pending",
            ),
        )
    if "adjusted_row_count" not in bulk_columns:
        op.add_column(
            "bulk_files",
            sa.Column(
                "adjusted_row_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )
    if "adjusted_error_message" not in bulk_columns:
        op.add_column(
            "bulk_files", sa.Column("adjusted_error_message", sa.Text())
        )
    # 未調整価格から生成済みの派生データは調整済み日足の投入後に再計算する。
    op.execute("DELETE FROM analysis_snapshots")
    op.execute("DELETE FROM predictions")
    op.execute(
        """
        INSERT INTO app_metadata (key, value)
        VALUES ('schema_version', '7')
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """
    )


def downgrade() -> None:
    """調整済み日足対応で追加した列を削除する。"""
    bulk_columns = _column_names("bulk_files")
    for name in (
        "adjusted_error_message",
        "adjusted_row_count",
        "adjusted_status",
    ):
        if name in bulk_columns:
            op.drop_column("bulk_files", name)
    daily_columns = _column_names("daily_bars")
    for name in (
        "adjustment_factor",
        "raw_volume",
        "raw_close",
        "raw_low",
        "raw_high",
        "raw_open",
    ):
        if name in daily_columns:
            op.drop_column("daily_bars", name)
