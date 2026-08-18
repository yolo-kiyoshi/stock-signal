from __future__ import annotations

from stock_signal.analysis.base import AnalysisEngine
from stock_signal.analysis.decision import LongOnlyDecisionPolicy
from stock_signal.analysis.engine import RuleBasedAnalysisEngine
from stock_signal.database import (
    data_sync_succeeded,
    list_watchlist_items,
    load_daily_bars,
    next_earnings_date,
)
from stock_signal.domain.analysis import AnalysisContext, AnalysisResult
from stock_signal.domain.dashboard import WatchlistItem


_ACTION_RANK = {
    "buy_candidate": 3,
    "avoid_new_buy": 2,
    "watch": 1,
    "insufficient_data": 0,
}


class AnalysisService:
    """データ取得と分析方式を仲介するアプリケーションサービス。"""

    def __init__(
        self,
        database_url: str,
        engine: AnalysisEngine | None = None,
        *,
        jquants_plan: str = "light",
    ) -> None:
        self.database_url = database_url
        self.jquants_plan = jquants_plan
        self.engine = engine or RuleBasedAnalysisEngine(
            decision_policy=LongOnlyDecisionPolicy(jquants_plan=jquants_plan)
        )

    def analyze_symbol(
        self, symbol: str, horizon_days: int, provider: str | None = None
    ) -> AnalysisResult:
        bars = load_daily_bars(self.database_url, symbol, provider=provider)
        as_of = bars[-1].trade_date if bars else None
        resolved_provider = provider or (bars[-1].provider if bars else None)
        context = AnalysisContext(
            market_bars=tuple(load_daily_bars(
                self.database_url, "TOPIX", provider="jquants"
            )),
            next_earnings_date=(
                next_earnings_date(self.database_url, symbol, as_of)
                if as_of and resolved_provider == "jquants"
                else None
            ),
            earnings_synced=data_sync_succeeded(
                self.database_url, "jquants_earnings_calendar"
            ),
            jquants_plan=self.jquants_plan,
        )
        return self.engine.analyze(symbol, bars, horizon_days, context)

    def analyze_watchlist(self, horizon_days: int) -> list[tuple[str, AnalysisResult]]:
        results = self.analyze_items(
            list_watchlist_items(self.database_url),
            horizon_days,
        )
        return sorted(
            results,
            key=lambda item: (
                _ACTION_RANK.get(
                    item[1].investment_decision.action.value
                    if item[1].investment_decision else "",
                    0,
                ),
                item[1].investment_decision.evidence_score
                if item[1].investment_decision else item[1].winning_score,
            ),
            reverse=True,
        )

    def analyze_items(
        self,
        items: list[WatchlistItem],
        horizon_days: int,
    ) -> list[tuple[str, AnalysisResult]]:
        """複数銘柄を共通の市場コンテキストで効率的に分析する。"""
        market_bars = tuple(
            load_daily_bars(self.database_url, "TOPIX", provider="jquants")
        )
        earnings_synced = data_sync_succeeded(
            self.database_url, "jquants_earnings_calendar"
        )
        results = []
        for item in items:
            bars = load_daily_bars(
                self.database_url,
                item.symbol,
                provider=item.provider,
            )
            as_of = bars[-1].trade_date if bars else None
            context = AnalysisContext(
                market_bars=market_bars,
                next_earnings_date=(
                    next_earnings_date(self.database_url, item.symbol, as_of)
                    if as_of and item.provider == "jquants"
                    else None
                ),
                earnings_synced=earnings_synced,
                jquants_plan=self.jquants_plan,
            )
            results.append(
                (
                    item.display_name,
                    self.engine.analyze(item.symbol, bars, horizon_days, context),
                )
            )
        return results
