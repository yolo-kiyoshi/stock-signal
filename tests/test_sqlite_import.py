import sqlite3

from stock_signal.database import load_daily_bars
from stock_signal.persistence.sqlite_import import import_sqlite_database


def test_import_reads_uncheckpointed_wal(database_url, tmp_path) -> None:
    source = tmp_path / "legacy.db"
    writer = sqlite3.connect(source)
    try:
        writer.execute("PRAGMA journal_mode = WAL")
        writer.execute("PRAGMA wal_autocheckpoint = 0")
        writer.execute(
            """
            CREATE TABLE daily_bars (
                symbol TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                open TEXT NOT NULL,
                high TEXT NOT NULL,
                low TEXT NOT NULL,
                close TEXT NOT NULL,
                volume INTEGER NOT NULL,
                provider TEXT NOT NULL,
                is_adjusted INTEGER NOT NULL
            )
            """
        )
        writer.commit()
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        writer.execute(
            """
            INSERT INTO daily_bars VALUES
            ('7203', '2026-08-14', '2800', '2850', '2780', '2830',
             1234000, 'jquants', 1)
            """
        )
        writer.commit()

        counts = import_sqlite_database(source, database_url)
    finally:
        writer.close()

    bars = load_daily_bars(database_url, "7203", provider="jquants")
    assert counts["daily_bars"] == 1
    assert bars[0].close == 2830
    assert bars[0].volume == 1_234_000
