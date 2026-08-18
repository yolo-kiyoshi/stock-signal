from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from stock_signal.analysis.base import (
    AnalysisRule,
    PatternDetector,
    PatternLifecycleEvaluatorProtocol,
    TransitionReadinessEvaluatorProtocol,
)
from stock_signal.analysis.decision import LongOnlyDecisionPolicy
from stock_signal.analysis.lifecycle import PatternLifecycleEvaluator
from stock_signal.analysis.patterns import TechnicalPatternDetector
from stock_signal.analysis.rules import DEFAULT_RULES
from stock_signal.analysis.transition import TransitionReadinessEvaluator
from stock_signal.domain.analysis import (
    AnalysisContext,
    AnalysisFactor,
    AnalysisResult,
    Direction,
    InvestmentAction,
    InvestmentDecision,
    PatternLifecycleAssessment,
    PatternLifecycleStatus,
)
from stock_signal.domain.market_data import DailyBar


class RuleBasedAnalysisEngine:
    """注入されたルールを集計する、決定論的な分析エンジン。"""

    engine_id = "rule_based_technical"
    version = "2.3.0"
    minimum_bars = 25

    def __init__(
        self,
        rules: Sequence[AnalysisRule] = DEFAULT_RULES,
        pattern_detector: PatternDetector | None = None,
        lifecycle_evaluator: PatternLifecycleEvaluatorProtocol | None = None,
        transition_evaluator: TransitionReadinessEvaluatorProtocol | None = None,
        decision_policy: LongOnlyDecisionPolicy | None = None,
    ) -> None:
        self.rules = tuple(rules)
        self.pattern_detector = pattern_detector or TechnicalPatternDetector()
        self.lifecycle_evaluator = lifecycle_evaluator or PatternLifecycleEvaluator()
        self.transition_evaluator = transition_evaluator or TransitionReadinessEvaluator()
        self.decision_policy = decision_policy or LongOnlyDecisionPolicy()

    def analyze(
        self,
        symbol: str,
        bars: Sequence[DailyBar],
        horizon_days: int,
        context: AnalysisContext | None = None,
    ) -> AnalysisResult:
        if horizon_days not in {1, 5, 20}:
            raise ValueError(
                "分析期間は1、5、20営業日のいずれかで指定してください"
            )
        ordered = sorted(bars, key=lambda bar: bar.trade_date)
        as_of_date = ordered[-1].trade_date.isoformat() if ordered else ""
        if len(ordered) < self.minimum_bars:
            return AnalysisResult(
                symbol=symbol.upper(), as_of_date=as_of_date, horizon_days=horizon_days,
                direction=Direction.FLAT, scores={direction: 0.0 for direction in Direction},
                factors=(), engine_id=self.engine_id, engine_version=self.version,
                status="insufficient_data",
                message=(
                    f"分析には最低{self.minimum_bars}営業日の日足が必要です"
                    f"（現在{len(ordered)}件）"
                ),
                investment_decision=InvestmentDecision(
                    InvestmentAction.INSUFFICIENT_DATA,
                    0.0,
                    "投資検討区分を判定できません",
                    (),
                    ("必要な日足データを取得してから再分析してください",),
                ),
            )

        factors = tuple(
            factor for rule in self.rules if (factor := rule.evaluate(ordered, horizon_days))
        )
        patterns = self.pattern_detector.detect(ordered)
        pattern_lifecycles = self.lifecycle_evaluator.evaluate(ordered, patterns)
        if patterns:
            latest_date = max(pattern.detected_at for pattern in patterns)
            latest_patterns = [
                pattern for pattern in patterns if pattern.detected_at == latest_date
            ]
            lifecycle_by_type = {
                item.pattern_type: item for item in pattern_lifecycles
            }
            factors += tuple(
                self._pattern_factor(pattern, lifecycle_by_type.get(pattern.pattern_type))
                for pattern in latest_patterns
            )
        totals: dict[Direction, float] = defaultdict(float)
        for factor in factors:
            totals[factor.direction] += factor.score
        directional_gap = abs(totals[Direction.UP] - totals[Direction.DOWN])
        if directional_gap < 12 or max(totals[Direction.UP], totals[Direction.DOWN]) < 20:
            direction = Direction.FLAT
            if directional_gap < 12:
                factors += (AnalysisFactor(
                    "balanced_evidence", "方向感の拮抗", Direction.FLAT, 20,
                    "上昇要因と下落要因が拮抗しており、"
                    "明確な方向を判定できません",
                ),)
                totals[Direction.FLAT] += 20
        else:
            direction = (
                Direction.UP
                if totals[Direction.UP] > totals[Direction.DOWN]
                else Direction.DOWN
            )

        analysis_context = context or AnalysisContext(
            jquants_plan=self.decision_policy.jquants_plan
        )
        total = sum(totals.values()) or 1
        scores = {item: round(totals[item] / total * 100, 1) for item in Direction}
        transition_readiness = self.transition_evaluator.evaluate(
            ordered,
            patterns,
            pattern_lifecycles,
            direction,
            analysis_context,
        )
        checks = self.decision_policy.equity_checks(
            ordered, patterns, analysis_context
        )
        decision = self.decision_policy.decide(
            direction,
            scores,
            patterns,
            pattern_lifecycles,
            transition_readiness,
            checks,
            horizon_days,
        )
        return AnalysisResult(
            symbol=symbol.upper(), as_of_date=as_of_date, horizon_days=horizon_days,
            direction=direction, scores=scores,
            factors=tuple(sorted(factors, key=lambda item: item.score, reverse=True)),
            engine_id=self.engine_id, engine_version=self.version,
            patterns=patterns,
            pattern_lifecycles=pattern_lifecycles,
            transition_readiness=transition_readiness,
            equity_checks=checks,
            investment_decision=decision,
        )

    @staticmethod
    def _pattern_factor(
        pattern,
        lifecycle: PatternLifecycleAssessment | None,
    ) -> AnalysisFactor:
        if lifecycle is None:
            return AnalysisFactor(
                f"chart_pattern:{pattern.pattern_type.value}",
                pattern.name,
                Direction.FLAT,
                10.0,
                "パターン後の状態を評価できないため、方向の根拠には加えません",
            )
        if lifecycle.status is PatternLifecycleStatus.FAILED:
            reverse = Direction.DOWN if pattern.direction is Direction.UP else Direction.UP
            return AnalysisFactor(
                f"chart_pattern_failed:{pattern.pattern_type.value}",
                f"{pattern.name}の失敗",
                reverse,
                32.0,
                lifecycle.summary,
            )
        if lifecycle.status is PatternLifecycleStatus.WEAKENING:
            reverse = Direction.DOWN if pattern.direction is Direction.UP else Direction.UP
            return AnalysisFactor(
                f"chart_pattern_weakening:{pattern.pattern_type.value}",
                f"{pattern.name}後の勢い弱化",
                reverse,
                30.0,
                lifecycle.summary,
            )
        if lifecycle.status is PatternLifecycleStatus.ENTRY_WINDOW:
            return AnalysisFactor(
                f"chart_pattern:{pattern.pattern_type.value}",
                pattern.name,
                pattern.direction,
                round(20 + pattern.fit_score * 0.15, 1),
                f"{pattern.description}。{lifecycle.summary}",
            )
        if lifecycle.status is PatternLifecycleStatus.MONITORING:
            return AnalysisFactor(
                f"chart_pattern_monitoring:{pattern.pattern_type.value}",
                f"{pattern.name}の経過観察",
                Direction.FLAT,
                14.0,
                f"{lifecycle.summary}。過去パターンは現在方向へ加点しません",
            )
        return AnalysisFactor(
            f"chart_pattern_inactive:{pattern.pattern_type.value}",
            f"{pattern.name}の事後評価",
            Direction.FLAT,
            18.0,
            lifecycle.summary,
        )
