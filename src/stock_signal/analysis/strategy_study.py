from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from statistics import mean, median

from stock_signal.analysis.historical_validation import HistoricalValidationService, RealizedOutcome
from stock_signal.database import load_daily_bars
from stock_signal.domain.analysis import ANALYSIS_ENGINE_VERSION, InvestmentAction
from stock_signal.domain.market_data import DailyBar

STUDY_VERSION = "1.0.0"


@dataclass(frozen=True)
class EvaluationWindow:
    """判定日と実績日の両方を収める、重複しない評価期間。"""

    name: str
    start: date
    end: date


@dataclass(frozen=True)
class StudyPlan:
    """結果を見てから暗黙に変更しない評価条件。未使用期間であることは保証しない。"""

    symbols: tuple[str, ...]
    horizons: tuple[int, ...]
    windows: tuple[EvaluationWindow, ...]
    cost_bps: float
    engine_version: str
    selection_notes: str
    provider: str = "jquants"

    @classmethod
    def from_dict(cls, data: dict) -> StudyPlan:
        """未知の項目、曖昧な対象、期間重複、非有限のコストを拒否する。"""
        required = {"symbols", "horizons", "windows", "cost_bps", "engine_version",
                    "selection_notes", "provider"}
        if not isinstance(data, dict) or set(data) != required:
            raise ValueError("検証設定の必須項目が不足しているか、未知の項目があります")
        symbols = data["symbols"]
        if (
            not isinstance(symbols, list) or not symbols
            or any(not isinstance(s, str) or not re.fullmatch(r"[0-9A-Z]{4}", s)
                   for s in symbols)
            or len(set(symbols)) != len(symbols)
        ):
            raise ValueError("銘柄は重複しない4文字の証券コードで明示してください")
        horizons = data["horizons"]
        if (
            not isinstance(horizons, list) or not horizons
            or any(type(h) is not int or h not in (5, 20) for h in horizons)
            or len(set(horizons)) != len(horizons)
        ):
            raise ValueError("評価期間は重複しない5・20営業日を指定してください")
        cost = data["cost_bps"]
        if type(cost) not in (int, float) or not math.isfinite(cost) or not 0 <= cost < 10000:
            raise ValueError("片道コストbpは0以上10000未満の有限数にしてください")
        if data["provider"] != "jquants":
            raise ValueError("この検証の取得元はjquantsに固定してください")
        if data["engine_version"] != ANALYSIS_ENGINE_VERSION:
            raise ValueError("指定したエンジン版と実行中の版が一致しません")
        notes = data["selection_notes"]
        if not isinstance(notes, str) or not notes.strip():
            raise ValueError("銘柄・期間の選定理由と、既に結果を見た範囲を記載してください")
        windows = []
        if not isinstance(data["windows"], list) or not data["windows"]:
            raise ValueError("評価期間を1つ以上指定してください")
        for item in data["windows"]:
            if not isinstance(item, dict) or set(item) != {"name", "start", "end"}:
                raise ValueError("評価期間にはname・start・endだけを指定してください")
            if not isinstance(item["name"], str) or not item["name"].strip():
                raise ValueError("評価期間の名前が必要です")
            try:
                start, end = date.fromisoformat(item["start"]), date.fromisoformat(item["end"])
            except (ValueError, TypeError) as error:
                raise ValueError("評価期間の日付はYYYY-MM-DD形式で指定してください") from error
            if start > end or (windows and start <= windows[-1].end):
                raise ValueError("評価期間は時間順に並べ、重複を避けてください")
            if any(w.name == item["name"] for w in windows):
                raise ValueError("評価期間の名前は重複できません")
            windows.append(EvaluationWindow(item["name"], start, end))
        return cls(tuple(sorted(symbols)), tuple(sorted(horizons)), tuple(windows),
                   float(cost), data["engine_version"], notes.strip())


def fingerprint(value: object) -> str:
    """条件・入力データの同一性を比較するための識別値を計算する。"""
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str,
                         allow_nan=False, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def event_metrics(outcome: RealizedOutcome, cost_bps: float) -> dict:
    """翌始値購入・期末終値売却の両側へコストを適用する。途中決済は行わない。"""
    if not math.isfinite(cost_bps) or not 0 <= cost_bps < 10000:
        raise ValueError("片道コストbpが不正です")
    entry, exit_price = outcome.start_close, outcome.target_close
    if not all(math.isfinite(p) and p > 0 for p in (entry, exit_price)):
        raise ValueError("実績価格は正の有限数である必要があります")
    cost = cost_bps / 10000
    gross = (exit_price / entry - 1) * 100
    net = (exit_price * (1 - cost) / (entry * (1 + cost)) - 1) * 100
    benchmark = outcome.market_return_percent
    return {
        "entry_date": outcome.start_date.isoformat(),
        "exit_date": outcome.target_date.isoformat(),
        "gross_return_percent": gross,
        "net_return_percent": net,
        "market_return_percent": benchmark,
        "net_excess_return_percent": None if benchmark is None else net - benchmark,
        "mfe_percent": outcome.maximum_favorable_excursion_percent,
        "mae_percent": outcome.maximum_adverse_excursion_percent,
        "stop_hit": outcome.stop_hit,
        "target_hit": outcome.target_hit,
    }


def summarize(events: list[dict], candidate_days: int) -> dict:
    """成熟済みイベントだけを集計し、標本がない指標は未算出とする。"""
    ready = [e for e in events if e["status"] == "ready"]
    returns = [e["net_return_percent"] for e in ready]
    excess = [e["net_excess_return_percent"] for e in ready
              if e["net_excess_return_percent"] is not None]
    stops = [e["stop_hit"] for e in ready if e["stop_hit"] is not None]
    mae = [e["mae_percent"] for e in ready if e["mae_percent"] is not None]
    mfe = [e["mfe_percent"] for e in ready if e["mfe_percent"] is not None]
    return {
        "candidate_days": candidate_days,
        "events": len(events),
        "status_counts": dict(Counter(e["status"] for e in events)),
        "mature_events": len(ready),
        "mean_gross_return_percent": mean(e["gross_return_percent"] for e in ready)
        if ready else None,
        "mean_net_return_percent": mean(returns) if returns else None,
        "median_net_return_percent": median(returns) if returns else None,
        "positive_return_fraction": mean(r > 0 for r in returns) if returns else None,
        "benchmark_events": len(excess),
        "mean_net_excess_return_percent": mean(excess) if excess else None,
        "mean_mae_percent": mean(mae) if mae else None,
        "mean_mfe_percent": mean(mfe) if mfe else None,
        "stop_evaluated_events": len(stops),
        "stop_hit_fraction": mean(stops) if stops else None,
    }


class StrategyStudyService:
    """保存日足を読み取り、固定した期間と判定経路ごとのイベントを比較する。"""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.validation = HistoricalValidationService(database_url, jquants_plan="light")

    def run(self, plan: StudyPlan) -> dict:
        """外部取得・保有参照・候補更新をせず、明示された銘柄だけを読み込む。"""
        datasets = {s: load_daily_bars(self.database_url, s, provider=plan.provider)
                    for s in plan.symbols}
        market = load_daily_bars(self.database_url, "TOPIX", provider="jquants")
        return self.evaluate_loaded(plan, datasets, market)

    def evaluate_loaded(
        self, plan: StudyPlan, datasets: dict[str, list[DailyBar]], market: list[DailyBar],
    ) -> dict:
        """各期間の終端で実績を打ち切り、開始直前の候補継続状態も確認する。"""
        # 直接構築した設定も、CLIと同じ入力検証を通す。
        plan = StudyPlan.from_dict(json.loads(json.dumps(asdict(plan), default=str)))
        end = plan.windows[-1].end
        inputs = {s: sorted((b for b in datasets.get(s, []) if b.trade_date <= end),
                            key=lambda b: b.trade_date) for s in plan.symbols}
        market = sorted((b for b in market if b.trade_date <= end), key=lambda b: b.trade_date)
        for symbol, bars in [*inputs.items(), ("TOPIX", market)]:
            if len({b.trade_date for b in bars}) != len(bars):
                raise ValueError("同一銘柄の日足に取引日の重複があります")
            if any(b.symbol != symbol or b.provider != plan.provider for b in bars):
                raise ValueError("検証設定と日足の銘柄・取得元が一致しません")
        events, coverage, summaries = [], [], []
        for window in plan.windows:
            window_market = [b for b in market if b.trade_date <= window.end]
            counts: Counter = Counter()
            window_events = []
            for symbol, all_bars in inputs.items():
                bars = [b for b in all_bars if b.trade_date <= window.end]
                indices = [i for i, b in enumerate(bars) if b.trade_date >= window.start]
                for horizon in plan.horizons:
                    statuses: Counter = Counter()
                    previous_strategy = None
                    # 期間初日に継続中の候補を新規イベントとして数えない。
                    preceding = [indices[0] - 1] if indices and indices[0] > 0 else []
                    for i in preceding + indices:
                        day = bars[i].trade_date
                        result = self.validation._validate_loaded(
                            symbol, plan.provider, day, horizon, bars, window_market,
                        )
                        analysis = result.analysis
                        decision = analysis.investment_decision if analysis else None
                        strategy = (
                            decision.entry_strategy or "unclassified"
                            if analysis and analysis.status == "ready" and decision
                            and decision.action is InvestmentAction.BUY_CANDIDATE else None
                        )
                        started = strategy is not None and strategy != previous_strategy
                        previous_strategy = strategy
                        if day < window.start:
                            continue
                        statuses[result.status] += 1
                        if strategy is not None:
                            counts[(horizon, strategy)] += 1
                        if not started:
                            continue
                        event = {"window": window.name, "symbol": symbol,
                                 "horizon": horizon, "strategy": strategy,
                                 "signal_date": day.isoformat(), "status": result.status}
                        if result.status == "ready" and result.actual is not None:
                            event.update(event_metrics(result.actual, plan.cost_bps))
                        window_events.append(event)
                    coverage.append({
                        "window": window.name, "symbol": symbol, "horizon": horizon,
                        "evaluation_days": len(indices), "status_counts": dict(statuses),
                        "status": "evaluated" if indices else "no_data_in_window",
                    })
            for horizon in plan.horizons:
                strategies = ("breakout", "swing_pullback", "unclassified") if horizon == 5 else (
                    "position_pullback", "unclassified",
                )
                for strategy in strategies:
                    selected = [e for e in window_events
                                if e["horizon"] == horizon and e["strategy"] == strategy]
                    summaries.append({"window": window.name, "horizon": horizon,
                                      "strategy": strategy,
                                      **summarize(selected, counts[(horizon, strategy)])})
            events.extend(window_events)
        return {
            "study_version": STUDY_VERSION,
            "generated_at": datetime.now(UTC).isoformat(),
            "plan": json.loads(json.dumps(asdict(plan), default=str)),
            "plan_sha256": fingerprint(asdict(plan)),
            "input_sha256": {s: fingerprint([asdict(b) for b in bars])
                             for s, bars in [*inputs.items(), ("TOPIX", market)]},
            "summary": summaries, "coverage": coverage, "events": events,
            "limitations": [
                "分析情報であり、投資助言や将来確率ではありません。",
                "固定ルールの時系列イベント評価です。学習・最適化・未使用期間の証明は行いません。",
                "判定経路は異なる事例の集計です。経路変更による因果的な改善を示しません。",
                "翌始値から固定期間終値まで保有し、両側に指定コストを適用します。",
                "損切り・目標への到達は診断だけで、途中決済を損益へ反映しません。",
                "期間終端または保存日足不足で満期に届かないイベントは収益集計から除外します。",
                "財務・資金・過去の決算予定・寄り付き前市場環境は含みません。",
                "現在保存された遡及調整済み系列を使用し、当時の配信内容は再現しません。",
                "銘柄選択・生存者バイアス、重複保有期間が残ります。ポートフォリオ成績ではありません。",
                "TOPIX比較は同じ開始日始値・終了日終値です。欠損は未算出とします。",
                "入力ハッシュは同一性の確認用です。原データの保存や同時更新の防止は行いません。",
            ],
        }


def write_study_report(report: dict, output: Path) -> None:
    """既存の結果を上書きせず、日本語を保持したJSONを保存する。"""
    payload = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("x", encoding="utf-8") as stream:
            stream.write(payload)
    except FileExistsError as error:
        raise ValueError(
            "出力先には既に結果があります。別のファイル名を指定してください"
        ) from error
