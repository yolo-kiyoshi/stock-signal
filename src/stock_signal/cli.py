from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path

from stock_signal.analysis.historical_validation import HistoricalValidationService
from stock_signal.batch import BatchAlreadyRunningError, DailyBatchRunner, result_as_json
from stock_signal.charts.candlestick import render_candlestick_report
from stock_signal.charts.historical_fit import render_historical_fit_report
from stock_signal.config import ConfigurationError, Settings, plan_history_start
from stock_signal.database import (
    add_watchlist_item,
    bulk_sync_status,
    get_instruments_by_symbols,
    initialize_database,
    list_bulk_sync_issues,
    list_stored_symbols,
    list_watchlist_items,
    load_daily_bars,
    upsert_daily_bars,
)
from stock_signal.market_sync import sync_bulk_daily_bars, sync_instrument_master
from stock_signal.persistence.sqlite_import import import_sqlite_database
from stock_signal.portfolios import JQUANTS_INITIAL_INSTRUMENTS
from stock_signal.providers.base import MarketDataError
from stock_signal.providers.factory import create_market_data_provider
from stock_signal.providers.jquants import JQuantsProvider


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stock-signal",
        description="日本株の日次テクニカル分析",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("health", help="設定を読み込めることを確認する")
    subparsers.add_parser("config", help="秘密情報を除いた設定を表示する")
    subparsers.add_parser("init-db", help="PostgreSQLを最新スキーマへ更新する")
    migrate_parser = subparsers.add_parser(
        "migrate-sqlite",
        help="旧SQLiteの保存データをPostgreSQLへ移行する",
    )
    migrate_parser.add_argument("--source", type=Path, required=True, help="SQLiteファイル")

    search_parser = subparsers.add_parser("search-symbol", help="取得元の銘柄記号を検索する")
    search_parser.add_argument("keywords", help="企業名または銘柄記号")

    fetch_parser = subparsers.add_parser("fetch", help="取得可能な日足OHLCVを取得する")
    fetch_parser.add_argument("symbol", help="取得元固有の銘柄記号")
    fetch_parser.add_argument("--start", type=date.fromisoformat, help="開始日（YYYY-MM-DD）")
    fetch_parser.add_argument("--end", type=date.fromisoformat, help="終了日（YYYY-MM-DD）")

    ingest_parser = subparsers.add_parser("ingest", help="日足OHLCVを取得・検証・保存する")
    ingest_parser.add_argument("symbol", help="取得元固有の銘柄記号")
    ingest_parser.add_argument("--start", type=date.fromisoformat, help="開始日（YYYY-MM-DD）")
    ingest_parser.add_argument("--end", type=date.fromisoformat, help="終了日（YYYY-MM-DD）")

    subparsers.add_parser("list-symbols", help="PostgreSQLに保存された銘柄を一覧表示する")

    jquants_parser = subparsers.add_parser(
        "jquants-bootstrap",
        help="異業種10銘柄のJ-Quants日足を取得して登録する",
    )
    jquants_parser.add_argument("--start", type=date.fromisoformat, help="開始日（YYYY-MM-DD）")
    jquants_parser.add_argument("--end", type=date.fromisoformat, help="終了日（YYYY-MM-DD）")

    bulk_parser = subparsers.add_parser(
        "jquants-bulk-bootstrap",
        help="Lightのバルクダウンロードで全市場の日足を初期取得する",
    )
    bulk_parser.add_argument("--start", type=date.fromisoformat, help="開始日（YYYY-MM-DD）")
    bulk_parser.add_argument("--end", type=date.fromisoformat, help="終了日（YYYY-MM-DD）")
    subparsers.add_parser(
        "jquants-bulk-status",
        help="全市場日足のバルク保存・調整済み化の進捗を表示する",
    )

    master_parser = subparsers.add_parser(
        "jquants-master-sync",
        help="J-Quantsの全上場銘柄マスタを同期する",
    )
    master_parser.add_argument("--date", type=date.fromisoformat, help="基準日（YYYY-MM-DD）")

    watchlist_parser = subparsers.add_parser(
        "watchlist-add", help="ウォッチリストへ銘柄を追加する"
    )
    watchlist_parser.add_argument("symbol", help="取得元固有の銘柄記号")
    watchlist_parser.add_argument("--name", required=True, help="画面表示名")
    watchlist_parser.add_argument("--exchange", default="未設定", help="取引所")
    watchlist_parser.add_argument("--currency", default="未設定", help="通貨")

    chart_parser = subparsers.add_parser("chart", help="ローソク足HTMLレポートを生成する")
    chart_parser.add_argument("symbol", help="保存済みの取得元固有銘柄記号")
    chart_parser.add_argument("--output-dir", type=Path, default=Path("reports"))

    historical_report_parser = subparsers.add_parser(
        "historical-report",
        help="スイング・中長期の過去当てはめ実績HTMLを生成する",
    )
    historical_report_parser.add_argument("symbol", help="保存済みの4文字の証券コード")
    historical_report_parser.add_argument(
        "--provider",
        default="jquants",
        help="保存済みデータの取得元（既定: jquants）",
    )
    historical_report_parser.add_argument(
        "--from",
        dest="start",
        type=date.fromisoformat,
        help="判定期間の開始日（YYYY-MM-DD、未指定時は直近1年）",
    )
    historical_report_parser.add_argument(
        "--to",
        dest="end",
        type=date.fromisoformat,
        help="判定期間の終了日（YYYY-MM-DD）",
    )
    historical_report_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports"),
    )

    daily_parser = subparsers.add_parser("daily", help="日次分析パイプラインを実行する")
    daily_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="外部サービスを呼ばずに予定処理を表示する",
    )
    return parser


def _run_command(args: argparse.Namespace, settings: Settings) -> int:
    if args.command == "health":
        print("stock-signal: 正常")
        return 0

    if args.command == "config":
        print(json.dumps(settings.safe_dict(), indent=2, sort_keys=True))
        return 0

    if args.command == "init-db":
        initialize_database(settings.database_url)
        print("PostgreSQLを最新スキーマへ更新しました")
        return 0

    if args.command == "migrate-sqlite":
        initialize_database(settings.database_url)
        counts = import_sqlite_database(args.source, settings.database_url)
        print(json.dumps(counts, ensure_ascii=False, indent=2))
        return 0

    if args.command == "search-symbol":
        provider = create_market_data_provider(settings)
        matches = provider.search_symbols(args.keywords)
        print(
            json.dumps(
                [
                    {
                        "symbol": match.symbol,
                        "name": match.name,
                        "market": match.market,
                        "currency": match.currency,
                        "match_score": str(match.match_score),
                    }
                    for match in matches
                ],
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0

    if args.command == "fetch":
        provider = create_market_data_provider(settings)
        bars = provider.fetch_daily_prices(args.symbol, start=args.start, end=args.end)
        summary = {
            "symbol": args.symbol.upper(),
            "bars": len(bars),
            "first_date": bars[0].trade_date.isoformat() if bars else None,
            "last_date": bars[-1].trade_date.isoformat() if bars else None,
            "adjusted": bars[0].is_adjusted if bars else False,
        }
        print(json.dumps(summary, indent=2))
        return 0

    if args.command == "ingest":
        provider = create_market_data_provider(settings)
        bars = provider.fetch_daily_prices(args.symbol, start=args.start, end=args.end)
        stored = upsert_daily_bars(settings.database_url, bars)
        print(
            json.dumps(
                {
                    "symbol": args.symbol.upper(),
                    "received": len(bars),
                    "upserted": stored,
                    "database": "設定済みPostgreSQLデータベース",
                },
                indent=2,
            )
        )
        return 0

    if args.command == "list-symbols":
        rows = list_stored_symbols(settings.database_url)
        print(
            json.dumps(
                [
                    {
                        "symbol": symbol,
                        "provider": provider,
                        "bars": count,
                        "first_date": first_date,
                        "last_date": last_date,
                    }
                    for symbol, provider, count, first_date, last_date in rows
                ],
                indent=2,
            )
        )
        return 0

    if args.command == "jquants-bootstrap":
        provider = JQuantsProvider(
            api_key=settings.jquants_api_key,
            minimum_request_interval=settings.jquants_request_interval_seconds,
        )
        end = args.end or date.today() - timedelta(
            days=settings.jquants_data_delay_days
        )
        start = args.start or plan_history_start(
            date.today(), settings.jquants_history_years
        )
        if start > end:
            raise ValueError("開始日は終了日以前にしてください")
        summaries = []
        for instrument in JQUANTS_INITIAL_INSTRUMENTS:
            bars = provider.fetch_daily_prices(instrument.symbol, start=start, end=end)
            if not bars:
                raise ValueError(
                    f"{instrument.symbol} {instrument.display_name}の日足を取得できませんでした"
                )
            stored = upsert_daily_bars(settings.database_url, bars)
            add_watchlist_item(
                settings.database_url,
                symbol=instrument.symbol,
                provider=provider.provider_name,
                display_name=instrument.display_name,
                exchange=f"東京証券取引所・{instrument.industry}",
                currency="JPY",
            )
            summaries.append(
                {
                    "symbol": instrument.symbol,
                    "name": instrument.display_name,
                    "industry": instrument.industry,
                    "bars": stored,
                    "first_date": bars[0].trade_date.isoformat(),
                    "last_date": bars[-1].trade_date.isoformat(),
                }
            )
            print(f"取得完了: {instrument.symbol} {instrument.display_name} {stored}件")
        print(json.dumps(summaries, ensure_ascii=False, indent=2))
        return 0

    if args.command == "jquants-master-sync":
        provider = JQuantsProvider(
            api_key=settings.jquants_api_key,
            minimum_request_interval=settings.jquants_request_interval_seconds,
        )
        count = sync_instrument_master(
            settings.database_url,
            provider,
            args.date or date.today(),
        )
        print(json.dumps({"instruments": count}, ensure_ascii=False, indent=2))
        return 0

    if args.command == "jquants-bulk-bootstrap":
        if settings.jquants_plan == "free":
            raise ValueError("バルクダウンロードにはJ-Quants Light以上が必要です")
        provider = JQuantsProvider(
            api_key=settings.jquants_api_key,
            minimum_request_interval=settings.jquants_request_interval_seconds,
        )
        end = args.end or date.today()
        start = args.start or plan_history_start(end, settings.jquants_history_years)
        sync_instrument_master(settings.database_url, provider, end)
        summary = sync_bulk_daily_bars(
            settings.database_url,
            provider,
            start,
            end,
            history_start=plan_history_start(end, settings.jquants_history_years),
            on_progress=lambda message: print(
                message, file=sys.stderr, flush=True
            ),
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if summary["failed"] == 0 else 1

    if args.command == "jquants-bulk-status":
        status = bulk_sync_status(
            settings.database_url,
            "/equities/bars/daily",
        )
        payload = {
            **status,
            "issues": list_bulk_sync_issues(
                settings.database_url,
                "/equities/bars/daily",
            ),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.command == "watchlist-add":
        add_watchlist_item(
            settings.database_url,
            symbol=args.symbol,
            provider=settings.market_data_provider,
            display_name=args.name,
            exchange=args.exchange,
            currency=args.currency,
        )
        print(f"ウォッチリストへ追加しました: {args.symbol.upper()} {args.name}")
        return 0

    if args.command == "chart":
        bars = load_daily_bars(
            settings.database_url,
            args.symbol,
            provider=settings.market_data_provider,
        )
        output_path = render_candlestick_report(bars, args.output_dir)
        print(f"ローソク足レポートを生成しました: {output_path}")
        return 0

    if args.command == "historical-report":
        service = HistoricalValidationService(
            settings.database_url,
            jquants_plan=settings.jquants_plan,
        )
        points = service.validate_range(
            args.symbol,
            start=args.start,
            end=args.end,
            provider=args.provider,
            on_progress=lambda completed, total, as_of: print(
                f"過去当てはめを検証中: {completed}/{total}（{as_of}）",
                file=sys.stderr,
                flush=True,
            ),
        )
        instruments = get_instruments_by_symbols(
            settings.database_url,
            [args.symbol],
            provider=args.provider,
        )
        output_path = render_historical_fit_report(
            args.symbol,
            args.provider,
            points,
            args.output_dir,
            display_name=(instruments[0].display_name if instruments else None),
        )
        print(f"過去当てはめ実績レポートを生成しました: {output_path}")
        return 0

    if args.command == "daily":
        if args.dry_run:
            items = list_watchlist_items(settings.database_url)
            print(
                json.dumps(
                    {
                        "mode": "dry-run",
                        "pipeline": "差分取得 -> 検証 -> 保存 -> ルール分析",
                        "targets": [
                            {"symbol": item.symbol, "provider": item.provider}
                            for item in items
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        result = DailyBatchRunner(settings).run()
        print(result_as_json(result))
        return 0 if result.status == "success" else 1

    raise AssertionError(f"Unhandled command: {args.command}")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = Settings.from_env()
    except ConfigurationError as error:
        print(f"設定エラー: {error}")
        return 2
    try:
        return _run_command(args, settings)
    except (BatchAlreadyRunningError, MarketDataError, ValueError) as error:
        print(f"市場データエラー: {error}")
        return 2
