from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config


def upgrade_database(database_url: str) -> None:
    """Alembicマイグレーションを最新まで適用する。"""
    project_root = Path(__file__).resolve().parents[3]
    configuration = Config(project_root / "alembic.ini")
    configuration.set_main_option("script_location", str(project_root / "migrations"))
    configuration.set_main_option("sqlalchemy.url", database_url)
    configuration.attributes["database_url"] = database_url
    command.upgrade(configuration, "head")
