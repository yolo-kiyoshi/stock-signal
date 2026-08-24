from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from stock_signal.analysis.decision import LongOnlyDecisionPolicy
from stock_signal.analysis.engine import RuleBasedAnalysisEngine
from stock_signal.analysis.horizons import get_horizon_profile
from stock_signal.analysis.indicators import wilder_atr
from stock_signal.database import load_daily_bars
from stock_signal.domain.analysis import AnalysisContext, AnalysisResult, Direction
from stock_signal.domain.market_data import DailyBar


type ValidationStatus = Literal[
    "ready",
    "no_data",
    "insufficient_history",
    "insufficient_future_data",
]


@dataclass(frozen=True, slots=True)
class RealizedOutcome:
    """判定日から指定営業日後までに実現した値動き。"""

    direction: Direction
    start_date: date
    target_date: date
    start_close: float
    target_close: float
    return_percent: float
    move_atr: float
    threshold_atr: float
    market_return_percent: float | None
    excess_return_percent: float | None


@dataclass(frozen=True, slots=True)
class HistoricalValidationResult:
    """過去時点の分析と、その後に実現した結果の照合。"""

    symbol: str
    provider: str | None
    requested_as_of_date: date
    effective_as_of_date: date | None
    horizon_days: int
    status: ValidationStatus
    message: str
    analysis: AnalysisResult | None = None
    actual: RealizedOutcome | None = None
    direction_matched: bool | None = None


@dataclass(frozen=True, slots=True)
class HistoricalValidationPoint:
    """一つの判定日に対する複数運用スタイルの検証結果。"""

    as_of_date: date
    results: tuple[HistoricalValidationResult, ...]


def classify_realized_direction(move_atr: float, threshold_atr: float) -> Direction:
    """ATRで標準化した値動きを上昇・停滞・下落へ分類する。"""
    if threshold_atr <= 0:
        raise ValueError("実績分類のATR閾値は0より大きくしてください")
    if move_atr >= threshold_atr:
        return Direction.UP
    if move_atr <= -threshold_atr:
        return Direction.DOWN
    return Direction.FLAT


def calculate_realized_outcome(
    history: list[DailyBar],
    target_bar: DailyBar,
    threshold_atr: float,
    *,
    market_start: DailyBar | None = None,
    market_target: DailyBar | None = None,
) -> RealizedOutcome | None:
    """判定日時点のATRだけを使い、その後の実績を分類する。"""
    if not history:
        return None
    atr = wilder_atr(history, 20)
    if atr is None or atr <= 0:
        return None
    start_bar = history[-1]
    start_close = float(start_bar.close)
    target_close = float(target_bar.close)
    return_percent = (target_close / start_close - 1) * 100
    move_atr = (target_close - start_close) / atr

    market_return = None
    excess_return = None
    if market_start is not None and market_target is not None:
        market_start_close = float(market_start.close)
        market_return = (float(market_target.close) / market_start_close - 1) * 100
        excess_return = return_percent - market_return

    return RealizedOutcome(
        direction=classify_realized_direction(move_atr, threshold_atr),
        start_date=start_bar.trade_date,
        target_date=target_bar.trade_date,
        start_close=round(start_close, 4),
        target_close=round(target_close, 4),
        return_percent=round(return_percent, 3),
        move_atr=round(move_atr, 3),
        threshold_atr=threshold_atr,
        market_return_percent=(None if market_return is None else round(market_return, 3)),
        excess_return_percent=(None if excess_return is None else round(excess_return, 3)),
    )


class HistoricalValidationService:
    """保存済み日足を使い、未来情報を遮断して過去判定を再現する。"""

    def __init__(self, database_url: str, *, jquants_plan: str = "light") -> None:
        self.database_url = database_url
        self.jquants_plan = jquants_plan

    def validate(
        self,
        symbol: str,
        requested_as_of_date: date,
        horizon_days: int,
        provider: str | None = None,
    ) -> HistoricalValidationResult:
        normalized_symbol = symbol.strip().upper()
        all_bars = load_daily_bars(
            self.database_url,
            normalized_symbol,
            provider=provider,
        )
        if not all_bars:
            return HistoricalValidationResult(
                normalized_symbol,
                provider,
                requested_as_of_date,
                None,
                horizon_days,
                "no_data",
                "指定された銘柄の日足データがありません",
            )

        resolved_provider = provider or all_bars[-1].provider
        bars = [bar for bar in all_bars if bar.provider == resolved_provider]
        market_bars = load_daily_bars(
            self.database_url,
            "TOPIX",
            provider="jquants",
        )
        return self._validate_loaded(
            normalized_symbol,
            resolved_provider,
            requested_as_of_date,
            horizon_days,
            bars,
            market_bars,
        )

    def validate_range(
        self,
        symbol: str,
        *,
        start: date | None = None,
        end: date | None = None,
        horizons: tuple[int, ...] = (5, 20),
        provider: str | None = None,
        on_progress: Callable[[int, int, date], None] | None = None,
    ) -> tuple[HistoricalValidationPoint, ...]:
        """一度読み込んだ日足で、指定期間の各取引日を検証する。"""
        if not horizons:
            raise ValueError("検証する運用スタイルを1つ以上指定してください")
        for horizon in horizons:
            get_horizon_profile(horizon)
        if start is not None and end is not None and start > end:
            raise ValueError("開始日は終了日以前にしてください")

        normalized_symbol = symbol.strip().upper()
        all_bars = load_daily_bars(
            self.database_url,
            normalized_symbol,
            provider=provider,
        )
        if not all_bars:
            raise ValueError(f"{normalized_symbol}の日足データがありません")
        resolved_provider = provider or all_bars[-1].provider
        bars = [bar for bar in all_bars if bar.provider == resolved_provider]
        latest_date = bars[-1].trade_date
        effective_end = min(end or latest_date, latest_date)
        effective_start = start or effective_end - timedelta(days=365)
        requested_dates = [
            bar.trade_date
            for bar in bars
            if effective_start <= bar.trade_date <= effective_end
        ]
        if not requested_dates:
            raise ValueError("指定期間内の日足データがありません")

        market_bars = load_daily_bars(
            self.database_url,
            "TOPIX",
            provider="jquants",
        )
        points = []
        total = len(requested_dates)
        for completed, requested_date in enumerate(requested_dates, start=1):
            points.append(
                HistoricalValidationPoint(
                    as_of_date=requested_date,
                    results=tuple(
                        self._validate_loaded(
                            normalized_symbol,
                            resolved_provider,
                            requested_date,
                            horizon,
                            bars,
                            market_bars,
                        )
                        for horizon in horizons
                    ),
                )
            )
            if on_progress is not None and (
                completed == 1 or completed % 25 == 0 or completed == total
            ):
                on_progress(completed, total, requested_date)
        return tuple(points)

    def _validate_loaded(
        self,
        normalized_symbol: str,
        resolved_provider: str,
        requested_as_of_date: date,
        horizon_days: int,
        bars: list[DailyBar],
        market_bars: list[DailyBar],
    ) -> HistoricalValidationResult:
        """読み込み済み日足を、未来側と過去側に分離して検証する。"""
        profile = get_horizon_profile(horizon_days)
        eligible_indices = [
            index
            for index, bar in enumerate(bars)
            if bar.trade_date <= requested_as_of_date
        ]
        if not eligible_indices:
            return HistoricalValidationResult(
                normalized_symbol,
                resolved_provider,
                requested_as_of_date,
                None,
                horizon_days,
                "no_data",
                "指定日以前の日足データがありません",
            )

        as_of_index = eligible_indices[-1]
        effective_as_of = bars[as_of_index].trade_date
        history = bars[: as_of_index + 1]
        market_history = [
            bar for bar in market_bars if bar.trade_date <= effective_as_of
        ]

        # 現在日ではなく判定日を「今日」とし、古い過去日足を鮮度不足にしない。
        engine = RuleBasedAnalysisEngine(
            decision_policy=LongOnlyDecisionPolicy(
                today=lambda: effective_as_of,
                jquants_plan=self.jquants_plan,
            )
        )
        # 決算予定は時点スナップショットを保持していないため、未来情報混入を避けて未評価にする。
        context = AnalysisContext(
            market_bars=tuple(market_history),
            next_earnings_date=None,
            earnings_synced=False,
            jquants_plan=self.jquants_plan,
        )
        analysis = engine.analyze(
            normalized_symbol,
            history,
            horizon_days,
            context,
        )
        if analysis.status != "ready":
            return HistoricalValidationResult(
                normalized_symbol,
                resolved_provider,
                requested_as_of_date,
                effective_as_of,
                horizon_days,
                "insufficient_history",
                analysis.message or "判定に必要な過去日足が不足しています",
                analysis=analysis,
            )

        target_index = as_of_index + horizon_days
        if target_index >= len(bars):
            available_future_days = len(bars) - as_of_index - 1
            return HistoricalValidationResult(
                normalized_symbol,
                resolved_provider,
                requested_as_of_date,
                effective_as_of,
                horizon_days,
                "insufficient_future_data",
                (
                    f"実績判定には{horizon_days}営業日後の日足が必要です"
                    f"（現在{available_future_days}営業日後まで）"
                ),
                analysis=analysis,
            )

        target_bar = bars[target_index]
        market_by_date = {bar.trade_date: bar for bar in market_bars}
        actual = calculate_realized_outcome(
            history,
            target_bar,
            profile.recent_atr_threshold,
            market_start=market_by_date.get(effective_as_of),
            market_target=market_by_date.get(target_bar.trade_date),
        )
        if actual is None:
            return HistoricalValidationResult(
                normalized_symbol,
                resolved_provider,
                requested_as_of_date,
                effective_as_of,
                horizon_days,
                "insufficient_history",
                "実績分類に必要な20営業日分のATRを計算できません",
                analysis=analysis,
            )

        return HistoricalValidationResult(
            normalized_symbol,
            resolved_provider,
            requested_as_of_date,
            effective_as_of,
            horizon_days,
            "ready",
            "過去時点の判定と、その後の実績を照合しました",
            analysis=analysis,
            actual=actual,
            direction_matched=analysis.direction is actual.direction,
        )
