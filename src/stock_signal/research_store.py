"""財務開示と個人の資金設定を保存する。"""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from stock_signal.fundamentals import normalize_summary
from stock_signal.persistence.engine import get_engine
from stock_signal.persistence.schema import financial_disclosures, portfolio_risk_settings


def save_disclosures(database_url: str, records: list[dict]) -> int:
    """開示番号別に追記し、後日取得した内容で過去の記録を上書きしない。"""
    records = [normalize_summary(r) for r in records]
    with get_engine(database_url).begin() as connection:
        for r in records:
            connection.execute(
                insert(financial_disclosures)
                .values(
                    symbol=r["Code"],
                    provider="jquants",
                    disclosure_no=r["DiscNo"],
                    disclosed_date=date.fromisoformat(r["DiscDate"]),
                    payload=r,
                )
                .on_conflict_do_nothing()
            )
    return len(records)


def load_disclosures(database_url: str, symbol: str, as_of: date) -> list[dict]:
    """分析日より前の開示を返す。取得日時は別途保存し後検証に備える。"""
    with get_engine(database_url).connect() as connection:
        return list(
            connection.scalars(
                select(financial_disclosures.c.payload).where(
                    financial_disclosures.c.symbol == symbol,
                    financial_disclosures.c.disclosed_date < as_of,
                )
            )
        )


def load_risk_settings(database_url: str) -> dict | None:
    """未設定なら架空の資金を補完しない。"""
    with get_engine(database_url).connect() as connection:
        return connection.scalar(
            select(portfolio_risk_settings.c.payload).where(
                portfolio_risk_settings.c.name == "default"
            )
        )


def save_risk_settings(database_url: str, payload: dict) -> None:
    """検証済みの資金設定を保存する。"""
    with get_engine(database_url).begin() as connection:
        invalidate_candidate_snapshots(connection)
        connection.execute(
            insert(portfolio_risk_settings)
            .values(name="default", payload=payload)
            .on_conflict_do_update(
                index_elements=["name"], set_={"payload": payload, "updated_at": func.now()}
            )
        )


def invalidate_candidate_snapshots(connection) -> None:
    """資金・保有の変更後、日次候補の旧購入判定を再利用しない。"""
    from sqlalchemy import update

    from stock_signal.persistence.schema import analysis_snapshots

    connection.execute(
        update(analysis_snapshots)
        .where(analysis_snapshots.c.action == "buy_candidate")
        .values(
            action="watch",
            analysis_json=analysis_snapshots.c.analysis_json.op("||")(
                {
                    "summary": ("資金・保有または判定仕様の変更後のため、"
                                "銘柄を選択して再分析してください"),
                    "entry_stage": "not_applicable",
                    "research_stale": True,
                }
            ),
        )
    )


def sync_financials(database_url: str, provider, symbol: str) -> int:
    """取得結果と同期日時を保存し、失敗時も成功扱いにしない。"""
    from stock_signal.database import record_data_sync
    from stock_signal.providers.base import MarketDataError

    dataset = f'jquants_financials:{symbol}'
    try:
        count = save_disclosures(database_url, provider.fetch_financial_summaries(symbol))
    except (MarketDataError, ValueError):
        record_data_sync(database_url, dataset, 'failed', '財務データを同期できませんでした')
        raise
    record_data_sync(database_url, dataset, 'success')
    return count


def financial_sync_state(database_url: str, symbol: str) -> dict:
    """同期そのものの鮮度と財務開示日の鮮度を区別する。"""
    from datetime import UTC, datetime

    from stock_signal.persistence.schema import data_sync_status

    with get_engine(database_url).connect() as connection:
        row = connection.execute(select(data_sync_status).where(
            data_sync_status.c.dataset == f'jquants_financials:{symbol}')).mappings().first()
    if row is None:
        return {'status': 'missing', 'synced_at': None}
    status = row['status']
    if status == 'success' and (datetime.now(UTC) - row['synced_at']).total_seconds() > 4 * 86400:
        status = 'stale'
    return {'status': status, 'synced_at': row['synced_at'].isoformat()}
