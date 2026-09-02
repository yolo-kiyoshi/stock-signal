from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from stock_signal.database import (
    load_latest_market_regime_snapshot,
    save_market_regime_snapshot,
)
from stock_signal.domain.analysis import (
    AnalysisResult,
    EntryStage,
    InvestmentAction,
)
from stock_signal.domain.market_environment import (
    MarketObservation,
    MarketRegime,
    MarketRegimeSnapshot,
    MarketRiskComponent,
)
from stock_signal.providers.base import MarketEnvironmentProvider

JST = ZoneInfo("Asia/Tokyo")


def _component_level(score: float, severe_at: float, caution_at: float) -> MarketRegime:
    if score >= severe_at:
        return MarketRegime.SEVERE
    if score >= caution_at:
        return MarketRegime.CAUTION
    return MarketRegime.NORMAL


class RuleBasedMarketRegimeEngine:
    """海外株・原油・金利・為替を固定ルールで寄り付き前警戒へ変換する。"""

    version = "market-regime-v1.0.0"
    expected_indicators = frozenset({"spy", "qqq", "wti", "us10y", "usdjpy"})

    def evaluate(
        self,
        observations: list[MarketObservation],
        *,
        decision_at: datetime,
    ) -> MarketRegimeSnapshot:
        by_key = {item.indicator_key: item for item in observations}
        usable = {
            key: item
            for key, item in by_key.items()
            if (decision_at.date() - item.observation_date).days <= 4
        }
        cautions = [
            f"{item.label}は{item.observation_date.isoformat()}時点のためスコアから除外しました"
            for item in by_key.values()
            if item.indicator_key not in usable
        ]
        coverage = len(self.expected_indicators & usable.keys()) / len(
            self.expected_indicators
        )
        components = (
            self._us_equity_component(usable),
            self._oil_component(usable),
            self._yield_component(usable),
            self._fx_component(usable),
        )
        components = tuple(item for item in components if item is not None)
        score = min(100.0, sum(item.score for item in components))
        if coverage < 0.4:
            regime = MarketRegime.UNAVAILABLE
            cautions.append("必要な外部指標の40%未満しか取得できず、市場環境を判定できません")
        elif score >= 50:
            regime = MarketRegime.SEVERE
        elif score >= 25:
            regime = MarketRegime.CAUTION
        else:
            regime = MarketRegime.NORMAL
        reasons = tuple(
            item.description for item in components if item.score > 0
        ) or ("取得できた外部指標に大きな同時変動はありません",)
        return MarketRegimeSnapshot(
            decision_date=decision_at.astimezone(JST).date(),
            decision_at=decision_at,
            regime=regime,
            risk_score=round(score, 1),
            coverage_ratio=round(coverage, 2),
            components=components,
            reasons=reasons,
            cautions=tuple(cautions),
            observations=tuple(observations),
            engine_version=self.version,
        )

    @staticmethod
    def _us_equity_component(
        observations: dict[str, MarketObservation],
    ) -> MarketRiskComponent | None:
        changes = [
            item.change_percent
            for key in ("spy", "qqq")
            if (item := observations.get(key)) is not None
            and item.change_percent is not None
        ]
        if not changes:
            return None
        worst = min(changes)
        score = 30.0 if worst <= -2 else 22.0 if worst <= -1 else 10.0 if worst <= -0.5 else 0.0
        return MarketRiskComponent(
            key="us_equities",
            label="米国株",
            score=score,
            level=_component_level(score, 30, 10),
            description=f"米国株の弱い方の前日騰落率は{worst:+.2f}%です",
            indicator_keys=tuple(key for key in ("spy", "qqq") if key in observations),
        )

    @staticmethod
    def _oil_component(
        observations: dict[str, MarketObservation],
    ) -> MarketRiskComponent | None:
        item = observations.get("wti")
        if item is None or item.change_percent is None:
            return None
        change = item.change_percent
        score = 25.0 if change >= 5 else 18.0 if change >= 3 else 8.0 if change >= 1.5 else 0.0
        return MarketRiskComponent(
            key="oil",
            label="原油",
            score=score,
            level=_component_level(score, 25, 8),
            description=f"WTIは前回値から{change:+.2f}%、{item.value:.2f}ドルです",
            indicator_keys=("wti",),
        )

    @staticmethod
    def _yield_component(
        observations: dict[str, MarketObservation],
    ) -> MarketRiskComponent | None:
        item = observations.get("us10y")
        if item is None or item.change_value is None:
            return None
        basis_points = item.change_value * 100
        score = (
            25.0
            if basis_points >= 10
            else 18.0
            if basis_points >= 5
            else 8.0
            if basis_points >= 2
            else 0.0
        )
        return MarketRiskComponent(
            key="us_yield",
            label="米国金利",
            score=score,
            level=_component_level(score, 25, 8),
            description=f"米10年債利回りは前回値から{basis_points:+.1f}bp、{item.value:.3f}%です",
            indicator_keys=("us10y",),
        )

    @staticmethod
    def _fx_component(
        observations: dict[str, MarketObservation],
    ) -> MarketRiskComponent | None:
        item = observations.get("usdjpy")
        if item is None or item.change_percent is None:
            return None
        change = item.change_percent
        magnitude = abs(change)
        score = 10.0 if magnitude >= 1.5 else 6.0 if magnitude >= 0.8 else 0.0
        return MarketRiskComponent(
            key="fx",
            label="ドル円",
            score=score,
            level=_component_level(score, 10, 6),
            description=f"ドル円は前回値から{change:+.2f}%、{item.value:.2f}円です",
            indicator_keys=("usdjpy",),
        )


class MarketEnvironmentService:
    """外部指標の取得、ルール判定、保存を一つの処理として実行する。"""

    def __init__(
        self,
        database_url: str,
        provider: MarketEnvironmentProvider,
        *,
        engine: RuleBasedMarketRegimeEngine | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.database_url = database_url
        self.provider = provider
        self.engine = engine or RuleBasedMarketRegimeEngine()
        self.now = now or (lambda: datetime.now(UTC))

    def run(self) -> MarketRegimeSnapshot:
        current = self.now()
        observations = list(self.provider.fetch_market_environment())
        snapshot = self.engine.evaluate(observations, decision_at=current)
        save_market_regime_snapshot(self.database_url, snapshot)
        return snapshot


def apply_market_regime_gate(
    result: AnalysisResult,
    snapshot: MarketRegimeSnapshot | None,
) -> AnalysisResult:
    """テクニカル方向を維持したまま、強い外部警戒だけ新規購入へ反映する。"""
    decision = result.investment_decision
    if decision is None or snapshot is None or snapshot.regime != MarketRegime.SEVERE:
        return result
    caution = (
        f"寄り付き前の市場環境は強い警戒（{snapshot.risk_score:.0f}点）です。"
        "テクニカル方向とは別に、新規購入の延期または株数縮小を検討してください"
    )
    if decision.action != InvestmentAction.BUY_CANDIDATE:
        return replace(
            result,
            investment_decision=replace(
                decision,
                cautions=(*decision.cautions, caution),
            ),
        )
    return replace(
        result,
        investment_decision=replace(
            decision,
            action=InvestmentAction.WATCH,
            summary=f"外部環境が強い警戒のため購入を待ちます。{decision.summary}",
            cautions=(*decision.cautions, caution, *snapshot.reasons[:2]),
            entry_stage=EntryStage.CONDITIONAL_ENTRY,
        ),
    )


def latest_market_regime_for_analysis(
    database_url: str,
    as_of: date | None,
) -> MarketRegimeSnapshot | None:
    """未来参照を避け、分析日以前の最新スナップショットだけを返す。"""
    if as_of is None:
        return None
    snapshot = load_latest_market_regime_snapshot(database_url, on_or_before=as_of)
    return snapshot if snapshot and snapshot.decision_date == as_of else None


def market_regime_as_dict(
    snapshot: MarketRegimeSnapshot | None,
) -> dict[str, object]:
    """CLIとJSON APIで共通利用する日本語表示用データへ変換する。"""
    if snapshot is None:
        return {
            "status": "not_collected",
            "regime": "unavailable",
            "regime_label": "未評価",
            "message": "寄り付き前の市場環境はまだ取得されていません",
            "components": [],
            "observations": [],
            "reasons": [],
            "cautions": ["stock-signal preopenを実行すると取得できます"],
        }
    labels = {
        MarketRegime.NORMAL: "平常",
        MarketRegime.CAUTION: "警戒",
        MarketRegime.SEVERE: "強い警戒",
        MarketRegime.UNAVAILABLE: "未評価",
    }
    return {
        "status": "ready",
        "decision_date": snapshot.decision_date.isoformat(),
        "decision_at": snapshot.decision_at.isoformat(),
        "regime": snapshot.regime.value,
        "regime_label": labels[snapshot.regime],
        "risk_score": snapshot.risk_score,
        "coverage_ratio": snapshot.coverage_ratio,
        "message": (
            "テクニカル方向とは別に、新規購入時の外部環境を表します"
        ),
        "components": [
            {
                "key": item.key,
                "label": item.label,
                "score": item.score,
                "level": item.level.value,
                "description": item.description,
            }
            for item in snapshot.components
        ],
        "observations": [
            {
                "key": item.indicator_key,
                "label": item.label,
                "observation_date": item.observation_date.isoformat(),
                "value": item.value,
                "previous_value": item.previous_value,
                "change_value": item.change_value,
                "change_percent": item.change_percent,
                "unit": item.unit,
                "source": item.source,
            }
            for item in snapshot.observations
        ],
        "reasons": list(snapshot.reasons),
        "cautions": list(snapshot.cautions),
        "engine_version": snapshot.engine_version,
    }
