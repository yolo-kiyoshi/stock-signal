from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from stock_signal.domain.market_data import DailyBar


class Direction(StrEnum):
    """チャート分析の判定方向。"""

    UP = "up"
    FLAT = "flat"
    DOWN = "down"


class PatternType(StrEnum):
    """機械的に検出する完成済みチャートパターン。"""

    RECTANGLE_BREAKOUT_UP = "rectangle_breakout_up"
    RECTANGLE_BREAKOUT_DOWN = "rectangle_breakout_down"
    ASCENDING_TRIANGLE = "ascending_triangle"
    DESCENDING_TRIANGLE = "descending_triangle"
    DOUBLE_TOP = "double_top"
    DOUBLE_BOTTOM = "double_bottom"
    HEAD_AND_SHOULDERS_TOP = "head_and_shoulders_top"
    HEAD_AND_SHOULDERS_BOTTOM = "head_and_shoulders_bottom"


class BreakoutKind(StrEnum):
    """ブレイクが通常変動か大きな窓開けを伴うかの分類。"""

    NORMAL = "normal"
    GAP_DRIVEN = "gap_driven"
    NOT_EVALUATED = "not_evaluated"


class CheckStatus(StrEnum):
    """個別株固有の確認項目の評価状態。"""

    EVALUATED = "evaluated"
    PARTIAL = "partial"
    PENDING_DATA = "pending_data"
    PLAN_UNAVAILABLE = "plan_unavailable"
    ADDON_REQUIRED = "addon_required"
    UNAVAILABLE = "unavailable"


class InvestmentAction(StrEnum):
    """ロング中心で利用する検討上の区分。"""

    BUY_CANDIDATE = "buy_candidate"
    WATCH = "watch"
    AVOID_NEW_BUY = "avoid_new_buy"
    INSUFFICIENT_DATA = "insufficient_data"


class PatternLifecycleStatus(StrEnum):
    """ブレイク発生後のパターンの状態。"""

    ENTRY_WINDOW = "entry_window"
    MONITORING = "monitoring"
    WEAKENING = "weakening"
    TARGET_REACHED = "target_reached"
    FAILED = "failed"
    EXPIRED = "expired"


class PatternGuidance(StrEnum):
    """パターン単体から導く、注文ではない確認行動。"""

    CONSIDER_ENTRY = "consider_entry"
    HOLD_AND_MONITOR = "hold_and_monitor"
    TAKE_PROFIT_REVIEW = "take_profit_review"
    EXIT_REVIEW = "exit_review"
    IGNORE_OLD_SIGNAL = "ignore_old_signal"


class TransitionPhase(StrEnum):
    """下降から上昇へ移る過程を表す段階。"""

    FALLING = "falling"
    BOTTOMING = "bottoming"
    PREPARING = "preparing"
    ONE_GATE_REMAINING = "one_gate_remaining"
    EARLY_REVERSAL = "early_reversal"
    UPTREND = "uptrend"
    CAUTION = "caution"


class PositionEntryPhase(StrEnum):
    """中長期ポジションを開始する前の押し目評価段階。"""

    PULLBACK_CANDIDATE = "pullback_candidate"
    SUPPORT_TEST = "support_test"
    APPROACHING_SUPPORT = "approaching_support"
    TREND_EXTENDED = "trend_extended"
    TREND_BROKEN = "trend_broken"
    NO_SETUP = "no_setup"


@dataclass(frozen=True, slots=True)
class PatternDetection:
    """完成済みパターンとブレイク時点の客観的な特徴量。"""

    pattern_type: PatternType
    name: str
    direction: Direction
    detected_at: str
    fit_score: float
    duration_days: int
    breakout_level: float
    breakout_atr: float | None
    volume_ratio: float | None
    gap_atr: float | None
    breakout_kind: BreakoutKind
    prior_trend_score: float | None
    description: str


@dataclass(frozen=True, slots=True)
class PatternLifecycleAssessment:
    """検出済みパターンを現在時点で再評価した結果。"""

    pattern_type: PatternType
    detected_at: str
    status: PatternLifecycleStatus
    guidance: PatternGuidance
    trading_days_since_breakout: int
    entry_window_days: int
    maximum_monitoring_days: int
    entry_days_remaining: int
    current_close: float
    breakout_close: float
    target_price: float
    invalidation_price: float
    post_breakout_return_percent: float
    recent_momentum_atr: float | None
    summary: str


@dataclass(frozen=True, slots=True)
class TransitionCondition:
    """転換判断に使う一つの確認条件。"""

    key: str
    label: str
    satisfied: bool
    required: bool
    description: str
    current_value: float | None = None
    target_value: float | None = None
    unit: str | None = None


@dataclass(frozen=True, slots=True)
class TransitionReadiness:
    """転換準備の進捗。確率ではなく条件の達成状況を表す。"""

    phase: TransitionPhase
    satisfied_conditions: int
    total_conditions: int
    readiness_score: float
    summary: str
    next_condition: TransitionCondition | None
    conditions: tuple[TransitionCondition, ...]
    current_price: float
    trigger_price: float
    invalidation_price: float
    target_price: float
    risk_reward_ratio: float | None


@dataclass(frozen=True, slots=True)
class PositionSupportLevel:
    """中長期の押し目評価に使う一つの支持候補。"""

    key: str
    label: str
    level: float
    lower: float
    upper: float
    distance_atr: float
    touched: bool
    held: bool
    description: str


@dataclass(frozen=True, slots=True)
class PositionEntryCondition:
    """中長期の押し目候補に必要な確認条件。"""

    key: str
    label: str
    satisfied: bool
    description: str


@dataclass(frozen=True, slots=True)
class PositionEntryAssessment:
    """中期上昇トレンド内の支持帯接触と反発を評価した結果。"""

    phase: PositionEntryPhase
    satisfied_conditions: int
    total_conditions: int
    readiness_score: float
    summary: str
    next_condition: PositionEntryCondition | None
    conditions: tuple[PositionEntryCondition, ...]
    supports: tuple[PositionSupportLevel, ...]
    current_price: float
    atr: float
    invalidation_price: float | None


@dataclass(frozen=True, slots=True)
class EquityCheck:
    """出来高など、個別株として追加確認する項目。"""

    key: str
    label: str
    status: CheckStatus
    value: float | None
    unit: str | None
    description: str


@dataclass(frozen=True, slots=True)
class InvestmentDecision:
    """分析結果をロング中心の検討区分へ変換した結果。"""

    action: InvestmentAction
    evidence_score: float
    summary: str
    reasons: tuple[str, ...]
    cautions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AnalysisContext:
    """市場・イベントなど個別株以外の分析入力。"""

    market_bars: tuple[DailyBar, ...] = ()
    next_earnings_date: date | None = None
    earnings_synced: bool = False
    jquants_plan: str = "light"


@dataclass(frozen=True, slots=True)
class AnalysisFactor:
    """判定を支持する、人が読める根拠。"""

    rule_id: str
    name: str
    direction: Direction
    score: float
    description: str


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    """分析方式に依存しない共通の判定結果。"""

    symbol: str
    as_of_date: str
    horizon_days: int
    direction: Direction
    scores: dict[Direction, float]
    factors: tuple[AnalysisFactor, ...]
    engine_id: str
    engine_version: str
    status: str = "ready"
    message: str | None = None
    patterns: tuple[PatternDetection, ...] = ()
    pattern_lifecycles: tuple[PatternLifecycleAssessment, ...] = ()
    transition_readiness: TransitionReadiness | None = None
    position_entry: PositionEntryAssessment | None = None
    equity_checks: tuple[EquityCheck, ...] = ()
    investment_decision: InvestmentDecision | None = None

    @property
    def winning_score(self) -> float:
        return self.scores.get(self.direction, 0.0)
