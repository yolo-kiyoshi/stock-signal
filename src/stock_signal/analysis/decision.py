from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date
from statistics import median

from stock_signal.analysis.horizons import get_horizon_profile
from stock_signal.analysis.market_relative import calculate_market_relative_metrics
from stock_signal.domain.analysis import (
    AnalysisContext,
    BreakoutKind,
    CheckStatus,
    Direction,
    EquityCheck,
    InvestmentAction,
    InvestmentDecision,
    PatternDetection,
    PatternLifecycleAssessment,
    PatternLifecycleStatus,
    TransitionPhase,
    TransitionReadiness,
)
from stock_signal.domain.market_data import DailyBar


class LongOnlyDecisionPolicy:
    """分析結果をロング中心の検討区分へ変換する決定論的ポリシー。"""

    volume_confirmation_threshold = 1.5
    minimum_fit_score = 65.0
    minimum_breakout_atr = 0.1

    def __init__(
        self,
        *,
        today: Callable[[], date] = date.today,
        maximum_data_age_days: int = 7,
        jquants_plan: str = "light",
    ) -> None:
        self.today = today
        self.maximum_data_age_days = maximum_data_age_days
        self.jquants_plan = jquants_plan

    def equity_checks(
        self,
        bars: Sequence[DailyBar],
        patterns: Sequence[PatternDetection],
        context: AnalysisContext | None = None,
        horizon_days: int = 5,
    ) -> tuple[EquityCheck, ...]:
        context = context or AnalysisContext(jquants_plan=self.jquants_plan)
        latest = patterns[0] if patterns else None
        if latest and latest.volume_ratio is not None:
            volume = EquityCheck(
                "volume_ratio",
                "ブレイク時出来高",
                CheckStatus.EVALUATED,
                latest.volume_ratio,
                "倍",
                "過去60営業日（不足時は取得可能な20営業日以上）の"
                "中央値との倍率です",
            )
        else:
            volume = EquityCheck(
                "volume_ratio",
                "ブレイク時出来高",
                CheckStatus.UNAVAILABLE if latest else CheckStatus.EVALUATED,
                None,
                "倍",
                "出来高の比較に必要な履歴が不足しています"
                if latest
                else "直近に完成済みブレイクパターンがないため対象外です",
            )

        if latest and latest.gap_atr is not None:
            gap = EquityCheck(
                "gap_atr",
                "窓開け",
                CheckStatus.EVALUATED,
                latest.gap_atr,
                "ATR",
                "絶対値が1.5を超える場合はイベント主導の"
                "可能性として扱います",
            )
        else:
            gap = EquityCheck(
                "gap_atr",
                "窓開け",
                CheckStatus.UNAVAILABLE if latest else CheckStatus.EVALUATED,
                None,
                "ATR",
                "ATR計算に必要な履歴が不足しています"
                if latest
                else "直近に完成済みブレイクパターンがないため対象外です",
            )

        turnovers = [float(bar.close) * bar.volume for bar in bars[-60:] if bar.volume > 0]
        is_jpy_equity = bool(bars) and bars[-1].provider == "jquants"
        if len(turnovers) >= 20 and is_jpy_equity:
            turnover = median(turnovers)
            if turnover >= 1_000_000_000:
                liquidity_score = 100.0
            elif turnover >= 300_000_000:
                liquidity_score = 75.0
            elif turnover >= 100_000_000:
                liquidity_score = 50.0
            else:
                liquidity_score = 25.0
            liquidity = EquityCheck(
                "liquidity_score",
                "流動性",
                CheckStatus.PARTIAL,
                liquidity_score,
                "点",
                f"売買代金中央値は約{turnover / 1_000_000:.0f}百万円です。"
                "スプレッドを含まない暫定評価です",
            )
        else:
            description = (
                "日足に通貨情報がない取得元では、売買代金の閾値を適用しません"
                if turnovers and not is_jpy_equity
                else "売買代金の評価に必要な20営業日分の出来高がありません"
            )
            liquidity = EquityCheck(
                "liquidity_score",
                "流動性",
                CheckStatus.UNAVAILABLE,
                None,
                "点",
                description,
            )

        if bars:
            age_days = (self.today() - bars[-1].trade_date).days
            if 0 <= age_days <= self.maximum_data_age_days:
                freshness = EquityCheck(
                    "data_freshness",
                    "データ鮮度",
                    CheckStatus.EVALUATED,
                    float(age_days),
                    "日",
                    f"最新日足は{age_days}日前で、判断上限の"
                    f"{self.maximum_data_age_days}日以内です",
                )
            else:
                freshness = EquityCheck(
                    "data_freshness",
                    "データ鮮度",
                    CheckStatus.UNAVAILABLE,
                    float(age_days),
                    "日",
                    f"最新日足が{age_days}日前のため、購入候補には使用しません",
                )
        else:
            freshness = EquityCheck(
                "data_freshness",
                "データ鮮度",
                CheckStatus.UNAVAILABLE,
                None,
                "日",
                "日足データがありません",
            )

        market = self._market_check(context, bars, horizon_days)
        relative_strength, beta = self._relative_checks(
            context, bars, horizon_days
        )
        earnings = self._earnings_check(context, bars, horizon_days)
        if context.jquants_plan in {"standard", "premium"}:
            sector = EquityCheck(
                "sector_trend_score",
                "業種指数対比",
                CheckStatus.PENDING_DATA,
                None,
                None,
                "契約プランでは利用できますが、業種指数はまだ同期されていません",
            )
        else:
            sector = EquityCheck(
                "sector_trend_score",
                "業種指数対比",
                CheckStatus.PLAN_UNAVAILABLE,
                None,
                None,
                "Lightプラン対象外です。正式な業種指数にはStandard以上が必要です",
            )
        disclosure = EquityCheck(
            "disclosure_event",
            "適時開示との関連",
            (
                CheckStatus.PLAN_UNAVAILABLE
                if context.jquants_plan == "free"
                else CheckStatus.ADDON_REQUIRED
            ),
            None,
            None,
            (
                "Freeプランでは利用できません。有償プランとTDnetアドオンが必要です"
                if context.jquants_plan == "free"
                else "TDnetアドオン未契約のため、決算以外の開示要因は判定できません"
            ),
        )
        return (
            freshness,
            volume,
            gap,
            liquidity,
            market,
            relative_strength,
            beta,
            sector,
            earnings,
            disclosure,
        )

    def decide(
        self,
        direction: Direction,
        scores: dict[Direction, float],
        patterns: Sequence[PatternDetection],
        lifecycles: Sequence[PatternLifecycleAssessment],
        transition: TransitionReadiness,
        checks: Sequence[EquityCheck],
        horizon_days: int,
    ) -> InvestmentDecision:
        profile = get_horizon_profile(horizon_days)
        latest_patterns = self._latest_patterns(patterns)
        lifecycle_by_type = {item.pattern_type: item for item in lifecycles}
        bullish = [item for item in latest_patterns if item.direction is Direction.UP]
        bearish = [item for item in latest_patterns if item.direction is Direction.DOWN]
        freshness = next(check for check in checks if check.key == "data_freshness")
        evidence_score = scores.get(direction, 0.0)
        cautions = [
            check.description
            for check in checks
            if check.status in {
                CheckStatus.UNAVAILABLE,
                CheckStatus.PENDING_DATA,
                CheckStatus.PLAN_UNAVAILABLE,
                CheckStatus.ADDON_REQUIRED,
            }
        ]
        cautions.append(
            "この区分は分析情報であり、注文や利益を"
            "保証するものではありません"
        )
        cautions.append(
            f"{profile.label}は{profile.future_label}を確認する固定ルールであり、"
            "銘柄別の利益を保証する最適期間ではありません"
        )
        cautions.append(profile.caution)
        relative_strength = next(
            check for check in checks if check.key == "relative_strength"
        )
        if relative_strength.value is not None and relative_strength.value < -3:
            cautions.append(
                f"対象期間の相対力はTOPIX比{relative_strength.value:+.2f}%で、"
                "市場より弱い状態です"
            )

        if bullish and bearish:
            return InvestmentDecision(
                InvestmentAction.WATCH,
                round(max(item.fit_score for item in latest_patterns), 1),
                "同じ時点で上向き・下向きのパターンが競合しています",
                tuple(item.description for item in latest_patterns),
                tuple(cautions),
            )

        if bearish:
            lead = max(bearish, key=lambda item: item.fit_score)
            lifecycle = lifecycle_by_type.get(lead.pattern_type)
            if lifecycle and lifecycle.status in {
                PatternLifecycleStatus.FAILED,
                PatternLifecycleStatus.WEAKENING,
                PatternLifecycleStatus.TARGET_REACHED,
                PatternLifecycleStatus.EXPIRED,
            }:
                return InvestmentDecision(
                    InvestmentAction.WATCH,
                    round((lead.fit_score + evidence_score) / 2, 1),
                    lifecycle.summary,
                    (
                        lead.description,
                        "古い、弱まった、または完了した下降パターンだけでは"
                        "売買を判断しません",
                    ),
                    tuple(cautions),
                )
            return InvestmentDecision(
                InvestmentAction.AVOID_NEW_BUY,
                round((lead.fit_score + evidence_score) / 2, 1),
                "下降パターン完成のため、新規購入と買い増しを"
                "いったん避けます",
                (
                    lead.description,
                    "下降判定は空売りの実行指示には使用しません",
                ),
                tuple(cautions),
            )

        if bullish:
            lead = max(bullish, key=lambda item: item.fit_score)
            lifecycle = lifecycle_by_type.get(lead.pattern_type)
            evidence_score = round((lead.fit_score + evidence_score) / 2, 1)
            reasons = [lead.description]
            if lifecycle is None:
                return InvestmentDecision(
                    InvestmentAction.WATCH,
                    evidence_score,
                    "パターン後の状態を評価できないため、新規購入を保留します",
                    tuple(reasons),
                    tuple(cautions),
                )
            reasons.append(lifecycle.summary)
            if lifecycle.status is PatternLifecycleStatus.FAILED:
                return InvestmentDecision(
                    InvestmentAction.AVOID_NEW_BUY,
                    evidence_score,
                    "上抜けが失敗したため新規購入を避け、保有中なら撤退条件を確認します",
                    tuple(reasons),
                    tuple(cautions),
                )
            if lifecycle.status is PatternLifecycleStatus.WEAKENING:
                return InvestmentDecision(
                    InvestmentAction.AVOID_NEW_BUY,
                    evidence_score,
                    "上抜け後の勢いが反転したため、新規購入を避けて再評価します",
                    tuple(reasons),
                    tuple(cautions),
                )
            if lifecycle.status is PatternLifecycleStatus.TARGET_REACHED:
                return InvestmentDecision(
                    InvestmentAction.WATCH,
                    evidence_score,
                    "値幅目標へ到達したため、新規追随より利益確定条件を確認します",
                    tuple(reasons),
                    tuple(cautions),
                )
            if lifecycle.status is PatternLifecycleStatus.EXPIRED:
                return InvestmentDecision(
                    (
                        InvestmentAction.AVOID_NEW_BUY
                        if direction is Direction.DOWN
                        else InvestmentAction.WATCH
                    ),
                    evidence_score,
                    "パターンの監視期限を過ぎたため、現在のテクニカル要因で判断し直します",
                    tuple(reasons),
                    tuple(cautions),
                )
            if lifecycle.status is PatternLifecycleStatus.MONITORING:
                return InvestmentDecision(
                    InvestmentAction.WATCH,
                    evidence_score,
                    "新規購入の初期期間を過ぎたため、保有管理として経過を監視します",
                    tuple(reasons),
                    tuple(cautions),
                )
            if freshness.status is CheckStatus.UNAVAILABLE:
                return InvestmentDecision(
                    InvestmentAction.WATCH,
                    evidence_score,
                    "日足データが古いため、更新後の再確認を優先します",
                    tuple(reasons),
                    tuple(cautions),
                )
            earnings = next(check for check in checks if check.key == "days_to_earnings")
            if (
                earnings.value is not None
                and 0 <= earnings.value <= profile.earnings_exclusion_days
            ):
                reasons.append(
                    f"{int(earnings.value)}日後に決算発表予定があります"
                )
                return InvestmentDecision(
                    InvestmentAction.WATCH,
                    evidence_score,
                    "分析期間内に決算発表があるため、通過後の確認を優先します",
                    tuple(reasons),
                    tuple(cautions),
                )
            market = next(check for check in checks if check.key == "market_trend_score")
            if market.value is not None and market.value < -10:
                reasons.append(f"TOPIXトレンドは{market.value:.1f}点です")
                return InvestmentDecision(
                    InvestmentAction.WATCH,
                    evidence_score,
                    "市場全体が下向きのため、上抜けの定着を確認します",
                    tuple(reasons),
                    tuple(cautions),
                )
            if lead.breakout_kind is BreakoutKind.GAP_DRIVEN:
                return InvestmentDecision(
                    InvestmentAction.WATCH,
                    evidence_score,
                    "大きな窓開けを伴うため、イベント内容の確認を"
                    "優先します",
                    tuple(reasons),
                    (
                        "決算・適時開示の種別は現在のデータだけでは"
                        "特定できません",
                        *cautions,
                    ),
                )
            if (
                lead.volume_ratio is None
                or lead.volume_ratio < self.volume_confirmation_threshold
            ):
                reasons.append("出来高倍率1.5倍の確認条件を満たしていません")
                return InvestmentDecision(
                    InvestmentAction.WATCH,
                    evidence_score,
                    "上向きパターンは完成していますが、"
                    "出来高の裏付けが不足しています",
                    tuple(reasons),
                    tuple(cautions),
                )
            if lead.fit_score < self.minimum_fit_score:
                reasons.append("形の一致度が購入候補の基準に達していません")
                return InvestmentDecision(
                    InvestmentAction.WATCH, evidence_score,
                    "パターンの形を追加観察します", tuple(reasons), tuple(cautions)
                )
            if lead.breakout_atr is None or lead.breakout_atr < self.minimum_breakout_atr:
                reasons.append(
                    "ブレイク幅が0.1 ATRの確認条件を満たしていません"
                )
                return InvestmentDecision(
                    InvestmentAction.WATCH,
                    evidence_score,
                    "上抜け幅が小さいため、定着を確認します",
                    tuple(reasons),
                    tuple(cautions),
                )
            if horizon_days == 20 and direction is Direction.DOWN:
                reasons.append("20日・60日を中心とする中期方向が下向きです")
                return InvestmentDecision(
                    InvestmentAction.WATCH,
                    evidence_score,
                    "上抜けは確認しましたが、中期トレンドの改善を待ちます",
                    tuple(reasons),
                    tuple(cautions),
                )
            reasons.append(f"出来高が平常時中央値の{lead.volume_ratio:.2f}倍です")
            reasons.append(f"上抜け幅は{lead.breakout_atr:.2f} ATRです")
            return InvestmentDecision(
                InvestmentAction.BUY_CANDIDATE,
                evidence_score,
                "パターン、出来高、上抜け幅が購入候補の"
                "条件を満たしました",
                tuple(reasons),
                tuple(cautions),
            )

        if transition.phase is TransitionPhase.EARLY_REVERSAL:
            reasons = [condition.description for condition in transition.conditions]
            if freshness.status is CheckStatus.UNAVAILABLE:
                return InvestmentDecision(
                    InvestmentAction.WATCH,
                    round(transition.readiness_score, 1),
                    "日足データが古いため、更新後に転換初動を再確認します",
                    tuple(reasons),
                    tuple(cautions),
                )
            if horizon_days == 20 and direction is Direction.DOWN:
                reasons.append("中期テクニカル方向がまだ下向きです")
                return InvestmentDecision(
                    InvestmentAction.WATCH,
                    round(transition.readiness_score, 1),
                    "転換初動は確認しましたが、中期方向の改善を待ちます",
                    tuple(reasons),
                    tuple(cautions),
                )
            if transition.current_price < transition.trigger_price:
                reasons.append(
                    f"終値{transition.current_price:.2f}は転換水準"
                    f"{transition.trigger_price:.2f}の直前です"
                )
                return InvestmentDecision(
                    InvestmentAction.WATCH,
                    round(transition.readiness_score, 1),
                    "転換水準へ接近し出来高も増えていますが、上抜け前です",
                    tuple(reasons),
                    tuple(cautions),
                )
            if transition.risk_reward_ratio is not None:
                reasons.append(
                    f"参考リスクリワードは{transition.risk_reward_ratio:.2f}です"
                )
            return InvestmentDecision(
                InvestmentAction.WATCH,
                round(transition.readiness_score, 1),
                "転換初動は確認しましたが、完成ブレイクの価格・出来高条件を待ちます",
                tuple(reasons),
                tuple(cautions),
            )
        if transition.phase is TransitionPhase.ONE_GATE_REMAINING:
            next_condition = transition.next_condition
            return InvestmentDecision(
                InvestmentAction.WATCH,
                round(transition.readiness_score, 1),
                "上昇転換の条件はあと1つです",
                (() if next_condition is None else (next_condition.description,)),
                tuple(cautions),
            )
        if transition.phase in {
            TransitionPhase.BOTTOMING,
            TransitionPhase.PREPARING,
        }:
            return InvestmentDecision(
                InvestmentAction.WATCH,
                round(transition.readiness_score, 1),
                transition.summary,
                tuple(
                    condition.description
                    for condition in transition.conditions
                    if not condition.satisfied
                ),
                tuple(cautions),
            )
        if direction is Direction.DOWN:
            return InvestmentDecision(
                InvestmentAction.AVOID_NEW_BUY,
                round(evidence_score, 1),
                "下落要因が優勢なため、新規購入をいったん避けます",
                (
                    "完成済み下降パターンはありませんが、"
                    "複数のテクニカル要因が下向きです",
                ),
                tuple(cautions),
            )
        return InvestmentDecision(
            InvestmentAction.WATCH,
            round(evidence_score, 1),
            "完成済みブレイクパターンを待ちます",
            ("移動平均などの方向だけでは購入候補にしません",),
            tuple(cautions),
        )

    @staticmethod
    def _latest_patterns(patterns: Sequence[PatternDetection]) -> list[PatternDetection]:
        if not patterns:
            return []
        latest_date = max(item.detected_at for item in patterns)
        return [item for item in patterns if item.detected_at == latest_date]

    @staticmethod
    def _relative_checks(
        context: AnalysisContext,
        bars: Sequence[DailyBar],
        horizon_days: int,
    ) -> tuple[EquityCheck, EquityCheck]:
        profile = get_horizon_profile(horizon_days)
        market_bars = [
            bar for bar in context.market_bars
            if not bars or bar.trade_date <= bars[-1].trade_date
        ]
        metrics = calculate_market_relative_metrics(
            bars, market_bars, profile.market_window
        )
        if metrics is None:
            unavailable = (
                CheckStatus.PLAN_UNAVAILABLE
                if context.jquants_plan == "free"
                else CheckStatus.PENDING_DATA
            )
            message = (
                "TOPIXとの共通取引日が不足しているため、まだ計算できません"
            )
            return (
                EquityCheck(
                    "relative_strength",
                    "TOPIX相対力",
                    unavailable,
                    None,
                    "%",
                    message,
                ),
                EquityCheck(
                    "beta_topix",
                    "対TOPIXベータ",
                    unavailable,
                    None,
                    "倍",
                    message,
                ),
            )
        relative = EquityCheck(
            "relative_strength",
            "TOPIX相対力",
            CheckStatus.EVALUATED,
            metrics.relative_strength_percent,
            "%",
            f"{metrics.window}営業日で対象株{metrics.stock_return_percent:+.2f}%、"
            f"TOPIX{metrics.market_return_percent:+.2f}%との差です",
        )
        beta = EquityCheck(
            "beta_topix",
            "対TOPIXベータ",
            CheckStatus.EVALUATED if metrics.beta is not None else CheckStatus.UNAVAILABLE,
            metrics.beta,
            "倍",
            (
                f"{metrics.window}営業日の共通取引日リターンから計算した"
                f"単回帰ベータは{metrics.beta:.2f}です"
                if metrics.beta is not None
                else "TOPIXの日次変動がないためベータを計算できません"
            ),
        )
        return relative, beta

    @staticmethod
    def _market_check(
        context: AnalysisContext,
        bars: Sequence[DailyBar],
        horizon_days: int,
    ) -> EquityCheck:
        profile = get_horizon_profile(horizon_days)
        if context.jquants_plan == "free":
            return EquityCheck(
                "market_trend_score",
                "TOPIXトレンド",
                CheckStatus.PLAN_UNAVAILABLE,
                None,
                "点",
                "Freeプラン対象外です。TOPIX四本値にはLight以上が必要です",
            )
        market_bars = [
            bar for bar in context.market_bars
            if not bars or bar.trade_date <= bars[-1].trade_date
        ]
        if len(market_bars) <= profile.market_window:
            return EquityCheck(
                "market_trend_score",
                "TOPIXトレンド",
                CheckStatus.PENDING_DATA,
                None,
                "点",
                "Lightプランで利用可能です。TOPIX日足の同期後に有効になります",
            )
        change = float(
            market_bars[-1].close
            / market_bars[-profile.market_window - 1].close
            - 1
        )
        score_multiplier = 1000 / abs(profile.market_minimum_return_percent)
        score = max(-100.0, min(100.0, change * score_multiplier))
        return EquityCheck(
            "market_trend_score",
            "TOPIXトレンド",
            CheckStatus.EVALUATED,
            round(score, 1),
            "点",
            f"TOPIXの{profile.market_window}営業日騰落率は"
            f"{change * 100:+.2f}%です",
        )

    @staticmethod
    def _earnings_check(
        context: AnalysisContext,
        bars: Sequence[DailyBar],
        horizon_days: int,
    ) -> EquityCheck:
        profile = get_horizon_profile(horizon_days)
        if not context.earnings_synced:
            return EquityCheck(
                "days_to_earnings",
                "決算までの日数",
                CheckStatus.PENDING_DATA,
                None,
                "日",
                "Lightプランで利用可能です。決算予定日の同期後に有効になります",
            )
        if context.next_earnings_date is None or not bars:
            return EquityCheck(
                "days_to_earnings",
                "決算までの日数",
                CheckStatus.EVALUATED,
                None,
                "日",
                "現在取得できる将来の決算発表予定はありません",
            )
        days = (context.next_earnings_date - bars[-1].trade_date).days
        if 0 <= days <= profile.earnings_exclusion_days:
            status = CheckStatus.PARTIAL
            description = (
                f"次回決算予定まで{days}日です。"
                f"{profile.earnings_exclusion_days}日以内は"
                "購入候補を様子見へ変更します"
            )
        elif 0 <= days <= horizon_days:
            status = CheckStatus.PARTIAL
            description = (
                f"次回決算予定まで{days}日です。分析期間内ですが警告表示にとどめます"
            )
        else:
            status = CheckStatus.EVALUATED
            description = f"次回決算予定まで{days}日です"
        return EquityCheck(
            "days_to_earnings",
            "決算までの日数",
            status,
            float(days),
            "日",
            description,
        )
