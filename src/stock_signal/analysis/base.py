from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from stock_signal.domain.analysis import (
    AnalysisContext,
    AnalysisFactor,
    AnalysisResult,
    Direction,
    PatternDetection,
    PatternLifecycleAssessment,
    TransitionReadiness,
)
from stock_signal.domain.market_data import DailyBar


class AnalysisRule(Protocol):
    """単一のチャート分析ルールが満たす契約。"""

    rule_id: str

    def evaluate(
        self, bars: Sequence[DailyBar], horizon_days: int
    ) -> AnalysisFactor | None: ...


class AnalysisEngine(Protocol):
    """ルール方式と将来のモデル方式に共通する分析契約。"""

    engine_id: str
    version: str

    def analyze(
        self,
        symbol: str,
        bars: Sequence[DailyBar],
        horizon_days: int,
        context: AnalysisContext | None = None,
    ) -> AnalysisResult: ...


class PatternDetector(Protocol):
    """価格形状の検出方式を交換するための契約。"""

    def detect(self, bars: Sequence[DailyBar]) -> tuple[PatternDetection, ...]: ...


class PatternLifecycleEvaluatorProtocol(Protocol):
    """検出後のパターン状態を交換可能に評価する契約。"""

    def evaluate(
        self,
        bars: Sequence[DailyBar],
        patterns: Sequence[PatternDetection],
    ) -> tuple[PatternLifecycleAssessment, ...]: ...


class TransitionReadinessEvaluatorProtocol(Protocol):
    """上昇転換前の条件進捗を交換可能に評価する契約。"""

    def evaluate(
        self,
        bars: Sequence[DailyBar],
        patterns: Sequence[PatternDetection],
        lifecycles: Sequence[PatternLifecycleAssessment],
        direction: Direction,
        context: AnalysisContext,
    ) -> TransitionReadiness: ...
