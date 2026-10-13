from sqlalchemy import text

from stock_signal.database import list_analysis_universe, list_market_candidates
from stock_signal.persistence.engine import get_engine


def test_unlimited_universe_and_candidate_pages(database_url):
    """旧上限を超えて選出し、ページ間の欠落・重複を防ぐ。"""
    with get_engine(database_url).begin() as connection:
        connection.execute(
            text("""
            INSERT INTO instruments
                (symbol, provider, display_name, market, sector_33_code, as_of_date)
            SELECT n::text, 'jquants', '試験銘柄', 'プライム', '3700', DATE '2026-08-01'
            FROM generate_series(1000, 3000) n
        """)
        )
        connection.execute(
            text("""
            INSERT INTO daily_bars
                (symbol, provider, trade_date, open, high, low, close, volume, is_adjusted)
            SELECT n::text, 'jquants', DATE '2026-08-01' + d,
                   100, 101, 99, 100, n, true
            FROM generate_series(1000, 3000) n CROSS JOIN generate_series(0, 24) d
        """)
        )
        connection.execute(
            text("""
            INSERT INTO analysis_snapshots
                (symbol, provider, as_of_date, horizon_days, direction,
                 action, evidence_score, analysis_json)
            SELECT n::text, 'jquants', DATE '2026-08-25', 5, 'flat',
                   'watch', 50, '{}'::jsonb
            FROM generate_series(1000, 3000) n
        """)
        )
    assert len(list_analysis_universe(database_url)) == 2001
    assert len(list_analysis_universe(database_url, limit=2001)) == 2001
    limited = list_analysis_universe(database_url, limit=10)
    assert len(limited) == 10
    assert limited[0].symbol == "3000"
    first = list_market_candidates(database_url, limit=500)
    second = list_market_candidates(database_url, limit=500, offset=500)
    last = list_market_candidates(database_url, limit=500, offset=2000)
    assert len(first) == len(second) == 500
    assert len(last) == 1
    assert not {item["symbol"] for item in first} & {item["symbol"] for item in second}
    assert list_market_candidates(database_url, offset=2001) == []
    assert list_market_candidates(database_url, action="buy_candidate", offset=500) == []
