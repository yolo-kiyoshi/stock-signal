from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import date, timedelta
from types import TracebackType
from typing import cast
from uuid import uuid4

from stock_signal.analysis.decision import LongOnlyDecisionPolicy
from stock_signal.analysis.engine import RuleBasedAnalysisEngine
from stock_signal.analysis.service import AnalysisService
from stock_signal.config import Settings, plan_history_start
from stock_signal.database import (
    add_watchlist_item,
    bulk_sync_status,
    clear_analysis_snapshots,
    daily_bar_quality,
    daily_batch_lock,
    data_sync_succeeded,
    earliest_daily_bar_date,
    finish_pipeline_run,
    latest_bulk_file_date,
    latest_daily_bar_date,
    list_analysis_universe,
    list_positions,
    list_watchlist_items,
    list_watchlist_registrations,
    record_data_sync,
    record_pipeline_item,
    replace_earnings_calendar,
    start_pipeline_run,
    update_watchlist_registration,
    upsert_analysis_snapshot,
    upsert_daily_bars,
)
from stock_signal.domain.batch import BatchItemResult, BatchResult
from stock_signal.market_sync import (
    DAILY_BARS_ENDPOINT,
    sync_bulk_daily_bars,
    sync_instrument_master,
)
from stock_signal.persistence.engine import check_database
from stock_signal.providers.base import (
    LightPlanDataProvider,
    MarketDataError,
    MarketDataProvider,
    MarketUniverseProvider,
)
from stock_signal.providers.factory import create_market_data_provider_for


class BatchAlreadyRunningError(RuntimeError):
    """同じデータベースの日次バッチがすでに動作中である。"""


class BatchDatabaseLock(AbstractContextManager["BatchDatabaseLock"]):
    """PostgreSQL Advisory Lockで日次バッチの多重起動を防ぐ。"""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self._lock = None

    def __enter__(self) -> BatchDatabaseLock:
        self._lock = daily_batch_lock(self.database_url)
        try:
            self._lock.__enter__()
        except RuntimeError as error:
            self._lock = None
            raise BatchAlreadyRunningError("日次バッチはすでに実行中です") from error
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._lock is not None:
            self._lock.__exit__(exc_type, exc_value, traceback)
            self._lock = None


ProviderFactory = Callable[[str], MarketDataProvider]


class DailyBatchRunner:
    """保存済みウォッチリストを差分更新し、分析可能性を検査する。"""

    def __init__(
        self,
        settings: Settings,
        *,
        provider_factory: ProviderFactory | None = None,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.settings = settings
        self.provider_factory = provider_factory or (
            lambda name: create_market_data_provider_for(name, settings)
        )
        self.today = today
        self.analysis_engine = RuleBasedAnalysisEngine(
            decision_policy=LongOnlyDecisionPolicy(
                jquants_plan=settings.jquants_plan
            )
        )
        self.analysis_service = AnalysisService(
            settings.database_url,
            self.analysis_engine,
            jquants_plan=settings.jquants_plan,
        )

    def run(self) -> BatchResult:
        check_database(self.settings.database_url)
        with BatchDatabaseLock(self.settings.database_url):
            run_id = str(uuid4())
            start_pipeline_run(self.settings.database_url, run_id, "daily_market_data")
            item_results: list[BatchItemResult] = []
            try:
                providers: dict[str, MarketDataProvider] = {}
                reference_results = self._sync_light_plan_data(providers)
                reference_results.extend(self._sync_market_universe(providers))
                item_results.extend(reference_results)
                for result in reference_results:
                    record_pipeline_item(self.settings.database_url, run_id, result)
                self._activate_pending_registrations(providers)
                targets = {
                    (item.symbol, item.provider)
                    for item in list_watchlist_items(self.settings.database_url)
                }
                targets.update(
                    (item.symbol, item.provider)
                    for item in list_positions(self.settings.database_url)
                )
                for symbol, provider_name in sorted(targets):
                    result = self._process_item(symbol, provider_name, providers)
                    item_results.append(result)
                    record_pipeline_item(self.settings.database_url, run_id, result)
                screen_result = self._screen_market()
                item_results.append(screen_result)
                record_pipeline_item(
                    self.settings.database_url,
                    run_id,
                    screen_result,
                )
                status = self._overall_status(item_results)
                finish_pipeline_run(
                    self.settings.database_url,
                    run_id,
                    status,
                    {
                        "succeeded": sum(
                            item.status != "failed" for item in item_results
                        ),
                        "failed": sum(item.status == "failed" for item in item_results),
                    },
                )
            except Exception as error:
                finish_pipeline_run(
                    self.settings.database_url,
                    run_id,
                    "failed",
                    {"error": f"{type(error).__name__}: {error}"},
                )
                raise
            return BatchResult(run_id, status, tuple(item_results))

    def _process_item(
        self,
        symbol: str,
        provider_name: str,
        providers: dict[str, MarketDataProvider],
    ) -> BatchItemResult:
        try:
            if provider_name not in providers:
                providers[provider_name] = self.provider_factory(provider_name)
            provider = providers[provider_name]
            start, end = self._date_range(symbol, provider_name)
            if start > end:
                self._record_history_sync(symbol, provider_name)
                return BatchItemResult(
                    symbol,
                    provider_name,
                    "no_updates",
                    analysis_summary=self._analysis_summary(symbol, provider_name),
                )
            bars = provider.fetch_daily_prices(symbol, start=start, end=end)
            stored = upsert_daily_bars(self.settings.database_url, bars)
            if not bars:
                self._record_history_sync(symbol, provider_name)
                return BatchItemResult(
                    symbol,
                    provider_name,
                    "no_updates",
                    analysis_summary=self._analysis_summary(symbol, provider_name),
                )
            analysis_summary = self._analysis_summary(symbol, provider_name)
            self._record_history_sync(symbol, provider_name)
            return BatchItemResult(
                symbol=symbol,
                provider=provider_name,
                status="success",
                received=len(bars),
                upserted=stored,
                first_date=bars[0].trade_date.isoformat(),
                last_date=bars[-1].trade_date.isoformat(),
                analysis_summary=analysis_summary,
            )
        except (MarketDataError, ValueError) as error:
            return BatchItemResult(
                symbol=symbol,
                provider=provider_name,
                status="failed",
                error_message=str(error),
            )

    def _date_range(self, symbol: str, provider: str) -> tuple[date, date]:
        latest = latest_daily_bar_date(
            self.settings.database_url,
            symbol,
            provider,
            adjusted_only=provider == "jquants",
        )
        if provider == "jquants":
            end = self.today() - timedelta(
                days=self.settings.jquants_data_delay_days
            )
            history_dataset = self._history_dataset(symbol)
            history_start = plan_history_start(
                self.today(), self.settings.jquants_history_years
            )
            earliest = earliest_daily_bar_date(
                self.settings.database_url,
                symbol,
                provider,
                adjusted_only=True,
            )
            quality_is_ready = (
                daily_bar_quality(
                    self.settings.database_url,
                    symbol,
                    provider,
                )["status"]
                == "ready"
            )
            history_is_complete = bool(
                quality_is_ready
                and (
                    data_sync_succeeded(self.settings.database_url, history_dataset)
                    or earliest and earliest <= history_start + timedelta(days=7)
                )
            )
            start = (
                latest + timedelta(days=1)
                if latest
                and history_is_complete
                else history_start
            )
            return start, end
        end = self.today()
        start = latest + timedelta(days=1) if latest else self.today() - timedelta(days=140)
        return start, end

    def _history_dataset(self, symbol: str) -> str:
        return (
            f"jquants_history_{self.settings.jquants_history_years}y_"
            f"{symbol.upper()}"
        )

    def _record_history_sync(self, symbol: str, provider: str) -> None:
        if provider == "jquants":
            record_data_sync(
                self.settings.database_url,
                self._history_dataset(symbol),
                "success",
            )

    def _analysis_summary(self, symbol: str, provider: str) -> dict[str, object]:
        """日次実行時点の方向、投資検討区分、パターンを記録する。"""
        summary: dict[str, object] = {}
        for horizon in (5, 20):
            analysis = self.analysis_service.analyze_symbol(
                symbol, horizon, provider
            )
            decision = analysis.investment_decision
            transition = analysis.transition_readiness
            summary[str(horizon)] = {
                "direction": analysis.direction.value,
                "action": decision.action.value if decision else None,
                "evidence_score": decision.evidence_score if decision else None,
                "patterns": [pattern.pattern_type.value for pattern in analysis.patterns],
                "engine_version": analysis.engine_version,
                "transition_phase": transition.phase.value if transition else None,
                "transition_progress": (
                    None
                    if transition is None
                    else [
                        transition.satisfied_conditions,
                        transition.total_conditions,
                    ]
                ),
            }
        return summary

    def _screen_market(self) -> BatchItemResult:
        """流動性上位の全市場銘柄を5営業日基準で分析して保存する。"""
        try:
            universe = list_analysis_universe(
                self.settings.database_url,
                limit=self.settings.market_screening_limit,
            )
            analyses = self.analysis_service.analyze_items(universe, 5)
            clear_analysis_snapshots(
                self.settings.database_url,
                provider="jquants",
                horizon_days=5,
            )
            stored = 0
            for (_, result), item in zip(analyses, universe, strict=True):
                decision = result.investment_decision
                transition = result.transition_readiness
                if result.status != "ready" or not result.as_of_date or decision is None:
                    continue
                upsert_analysis_snapshot(
                    self.settings.database_url,
                    symbol=item.symbol,
                    provider=item.provider,
                    as_of_date=date.fromisoformat(result.as_of_date),
                    horizon_days=5,
                    direction=result.direction.value,
                    action=decision.action.value,
                    evidence_score=decision.evidence_score,
                    analysis_json={
                        "summary": decision.summary,
                        "reasons": list(decision.reasons),
                        "engine_id": result.engine_id,
                        "engine_version": result.engine_version,
                        "transition_readiness": (
                            None
                            if transition is None
                            else {
                                "phase": transition.phase.value,
                                "satisfied_conditions": transition.satisfied_conditions,
                                "total_conditions": transition.total_conditions,
                                "readiness_score": transition.readiness_score,
                                "summary": transition.summary,
                                "next_condition": (
                                    None
                                    if transition.next_condition is None
                                    else {
                                        "label": transition.next_condition.label,
                                        "description": transition.next_condition.description,
                                    }
                                ),
                                "current_price": transition.current_price,
                                "trigger_price": transition.trigger_price,
                                "invalidation_price": transition.invalidation_price,
                                "target_price": transition.target_price,
                                "risk_reward_ratio": transition.risk_reward_ratio,
                            }
                        ),
                    },
                )
                stored += 1
            return BatchItemResult(
                "@SCREEN",
                "rule_based",
                "success" if stored else "no_updates",
                received=len(universe),
                upserted=stored,
            )
        except ValueError as error:
            return BatchItemResult(
                "@SCREEN",
                "rule_based",
                "failed",
                error_message=str(error),
            )

    def _sync_light_plan_data(
        self, providers: dict[str, MarketDataProvider]
    ) -> list[BatchItemResult]:
        """Lightで利用できるTOPIXと決算予定日を日次同期する。"""
        if not self.settings.jquants_api_key:
            return []
        provider = providers.get("jquants")
        if provider is None:
            provider = self.provider_factory("jquants")
            providers["jquants"] = provider
        if not (
            hasattr(provider, "fetch_topix_prices")
            and hasattr(provider, "fetch_earnings_calendar")
        ):
            return []
        reference_provider = provider  # 型検査用にProtocol契約として扱う
        results = []
        if self.settings.jquants_plan != "free":
            results.append(self._sync_topix(reference_provider))
        results.append(self._sync_earnings(reference_provider))
        return results

    def _sync_market_universe(
        self, providers: dict[str, MarketDataProvider]
    ) -> list[BatchItemResult]:
        """Light以上では銘柄マスタと全市場の日次差分を同期する。"""
        if not self.settings.jquants_api_key or self.settings.jquants_plan == "free":
            return []
        provider = providers.get("jquants")
        if provider is None:
            provider = self.provider_factory("jquants")
            providers["jquants"] = provider
        if not all(
            hasattr(provider, method)
            for method in (
                "fetch_instrument_master",
                "list_bulk_files",
                "download_bulk_daily_bars",
                "fetch_market_daily_prices",
            )
        ):
            return []
        universe_provider = cast(MarketUniverseProvider, provider)
        results = []
        try:
            stored = sync_instrument_master(
                self.settings.database_url,
                universe_provider,
                self.today(),
            )
            results.append(
                BatchItemResult(
                    "@MASTER",
                    "jquants",
                    "success",
                    received=stored,
                    upserted=stored,
                )
            )
        except (MarketDataError, ValueError) as error:
            results.append(
                BatchItemResult(
                    "@MASTER",
                    "jquants",
                    "failed",
                    error_message=str(error),
                )
            )
            return results
        bulk_status = bulk_sync_status(
            self.settings.database_url,
            DAILY_BARS_ENDPOINT,
        )
        if bulk_status["incomplete"]:
            incomplete = bulk_status["incomplete"]
            results.append(
                BatchItemResult(
                    "@MARKET",
                    "jquants",
                    "failed",
                    error_message=(
                        f"調整済み化が未完了のバルクファイルが{incomplete}件あります。"
                        "jquants-bulk-bootstrapを再実行してください"
                    ),
                )
            )
            return results
        latest = latest_bulk_file_date(
            self.settings.database_url,
            DAILY_BARS_ENDPOINT,
        )
        end = self.today()
        if latest:
            start = min(
                latest + timedelta(days=1),
                end - timedelta(days=14),
            )
        else:
            start = end - timedelta(days=90)
        if start > end:
            results.append(BatchItemResult("@MARKET", "jquants", "no_updates"))
            return results
        try:
            summary = sync_bulk_daily_bars(
                self.settings.database_url,
                universe_provider,
                start,
                end,
                history_start=plan_history_start(
                    self.today(), self.settings.jquants_history_years
                ),
                refresh_completed=True,
            )
            results.append(
                BatchItemResult(
                    "@MARKET",
                    "jquants",
                    (
                        "failed"
                        if summary["failed"]
                        else "success" if summary["rows"] else "no_updates"
                    ),
                    received=summary["rows"],
                    upserted=summary["rows"],
                    error_message=(
                        f"{summary['failed']}ファイルの取得に失敗しました"
                        if summary["failed"]
                        else None
                    ),
                )
            )
        except (MarketDataError, ValueError) as error:
            results.append(
                BatchItemResult(
                    "@MARKET",
                    "jquants",
                    "failed",
                    error_message=str(error),
                )
            )
        return results

    def _sync_topix(self, provider: LightPlanDataProvider) -> BatchItemResult:
        dataset = "jquants_topix"
        history_dataset = (
            f"jquants_topix_history_{self.settings.jquants_history_years}y"
        )
        try:
            latest = latest_daily_bar_date(
                self.settings.database_url, "TOPIX", "jquants"
            )
            end = self.today()
            start = (
                min(
                    latest + timedelta(days=1),
                    end - timedelta(days=14),
                )
                if latest
                and data_sync_succeeded(
                    self.settings.database_url, history_dataset
                )
                else plan_history_start(end, self.settings.jquants_history_years)
            )
            if start > end:
                record_data_sync(self.settings.database_url, dataset, "success")
                record_data_sync(
                    self.settings.database_url, history_dataset, "success"
                )
                return BatchItemResult("@TOPIX", "jquants", "no_updates")
            bars = list(provider.fetch_topix_prices(start=start, end=end))
            stored = upsert_daily_bars(self.settings.database_url, bars)
            record_data_sync(self.settings.database_url, dataset, "success")
            record_data_sync(
                self.settings.database_url, history_dataset, "success"
            )
            return BatchItemResult(
                "@TOPIX",
                "jquants",
                "success" if bars else "no_updates",
                received=len(bars),
                upserted=stored,
                first_date=bars[0].trade_date.isoformat() if bars else None,
                last_date=bars[-1].trade_date.isoformat() if bars else None,
            )
        except (MarketDataError, ValueError) as error:
            record_data_sync(
                self.settings.database_url, dataset, "failed", str(error)
            )
            return BatchItemResult(
                "@TOPIX", "jquants", "failed", error_message=str(error)
            )

    def _sync_earnings(self, provider: LightPlanDataProvider) -> BatchItemResult:
        dataset = "jquants_earnings_calendar"
        try:
            announcements = list(provider.fetch_earnings_calendar())
            stored = replace_earnings_calendar(
                self.settings.database_url, announcements
            )
            record_data_sync(self.settings.database_url, dataset, "success")
            return BatchItemResult(
                "@EARNINGS",
                "jquants",
                "success",
                received=len(announcements),
                upserted=stored,
            )
        except (MarketDataError, ValueError) as error:
            record_data_sync(
                self.settings.database_url, dataset, "failed", str(error)
            )
            return BatchItemResult(
                "@EARNINGS", "jquants", "failed", error_message=str(error)
            )

    def _activate_pending_registrations(
        self, providers: dict[str, MarketDataProvider]
    ) -> None:
        """証券コードを銘柄マスタで検証してウォッチリストへ昇格する。"""
        if not self.settings.jquants_api_key:
            return
        pending = list_watchlist_registrations(
            self.settings.database_url, statuses=("pending",)
        )
        for registration in pending:
            try:
                provider = providers.get(registration.provider)
                if provider is None:
                    provider = self.provider_factory(registration.provider)
                    providers[registration.provider] = provider
                matches = provider.search_symbols(registration.symbol)
                match = next(
                    (item for item in matches if item.symbol == registration.symbol),
                    None,
                )
                if match is None:
                    update_watchlist_registration(
                        self.settings.database_url,
                        registration.symbol,
                        registration.provider,
                        "failed",
                        error_message=(
                            "J-Quants銘柄マスタに一致する証券コードがありません"
                        ),
                        watchlist_name=registration.watchlist_name,
                    )
                    continue
                add_watchlist_item(
                    self.settings.database_url,
                    symbol=match.symbol,
                    provider=registration.provider,
                    display_name=match.name,
                    exchange=match.market,
                    currency=match.currency,
                    watchlist_name=registration.watchlist_name,
                )
                update_watchlist_registration(
                    self.settings.database_url,
                    match.symbol,
                    registration.provider,
                    "active",
                    display_name=match.name,
                    watchlist_name=registration.watchlist_name,
                )
            except (MarketDataError, ValueError) as error:
                update_watchlist_registration(
                    self.settings.database_url,
                    registration.symbol,
                    registration.provider,
                    "failed",
                    error_message=str(error),
                    watchlist_name=registration.watchlist_name,
                )

    @staticmethod
    def _overall_status(items: list[BatchItemResult]) -> str:
        if not items:
            return "success"
        failures = sum(item.status == "failed" for item in items)
        if failures == len(items):
            return "failed"
        if failures:
            return "partial_failure"
        return "success"


def result_as_json(result: BatchResult) -> str:
    """CLI向けに秘密情報を含まない結果JSONを生成する。"""
    return json.dumps(
        {
            "run_id": result.run_id,
            "status": result.status,
            "succeeded": result.succeeded,
            "failed": result.failed,
            "items": [
                {
                    "symbol": item.symbol,
                    "provider": item.provider,
                    "status": item.status,
                    "received": item.received,
                    "upserted": item.upserted,
                    "first_date": item.first_date,
                    "last_date": item.last_date,
                    "error_message": item.error_message,
                    "analysis": item.analysis_summary,
                }
                for item in result.items
            ],
        },
        ensure_ascii=False,
        indent=2,
    )
