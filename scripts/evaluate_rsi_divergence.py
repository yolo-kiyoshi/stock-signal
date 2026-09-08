"""保存日足でRSI逆行の条件追加を比較する探索的検証。Docker内で実行する。"""
import json
from dataclasses import asdict
from pathlib import Path
from statistics import mean

from stock_signal.analysis.decision import LongOnlyDecisionPolicy
from stock_signal.analysis.divergence import detect_rsi_divergence
from stock_signal.analysis.engine import RuleBasedAnalysisEngine
from stock_signal.config import Settings
from stock_signal.database import list_analysis_universe, load_daily_bars
from stock_signal.domain.analysis import AnalysisContext

settings = Settings.from_env()
market = load_daily_bars(settings.database_url, "TOPIX", provider="jquants")
market_by_date = {b.trade_date: b for b in market}
universe = list_analysis_universe(settings.database_url, limit=20)
rows = []
latest = {}
for item in universe:
    bars = load_daily_bars(settings.database_url, item.symbol, provider="jquants")
    latest[item.symbol] = [asdict(d) for d in detect_rsi_divergence(bars)]
    for horizon, window in ((5, 14), (20, 28)):
        previous = dict.fromkeys(("baseline", "exclude_bearish", "require_bullish"), False)
        for i in range(max(80, len(bars) - 252 - horizon), len(bars) - horizon):
            history = bars[:i + 1]
            day = bars[i].trade_date
            engine = RuleBasedAnalysisEngine(
                decision_policy=LongOnlyDecisionPolicy(today=lambda day=day: day)
            )
            result = engine.analyze(item.symbol, history, horizon, AnalysisContext(
                market_bars=tuple(b for b in market if b.trade_date <= day),
            ))
            buy = result.investment_decision.action.value == "buy_candidate"
            divergences = {d.direction for d in detect_rsi_divergence(history, window)}
            variants = {
                "baseline": buy,
                "exclude_bearish": buy and "bearish" not in divergences,
                "require_bullish": buy and "bullish" in divergences,
            }
            entry, end = bars[i + 1], bars[i + horizon]
            entry_price = float(entry.open)
            path = bars[i + 1:i + horizon + 1]
            gross = (float(end.close) / entry_price - 1) * 100
            benchmark = None
            if entry.trade_date in market_by_date and end.trade_date in market_by_date:
                benchmark = (
                    float(market_by_date[end.trade_date].close)
                    / float(market_by_date[entry.trade_date].open) - 1
                ) * 100
            for variant, eligible in variants.items():
                # 連続候補は各方式の初日のみ。別の取引の重なりは残る。
                if eligible and not previous[variant]:
                    rows.append({
                        "symbol": item.symbol, "horizon": horizon, "variant": variant,
                        "date": day.isoformat(), "net_percent": gross - 0.2,
                        "excess_percent": gross - benchmark if benchmark is not None else None,
                        "mae_percent": (min(float(b.low) for b in path) / entry_price - 1) * 100,
                    })
                previous[variant] = eligible
    print("完了", item.symbol, flush=True)
summary = []
for horizon in (5, 20):
    for variant in ("baseline", "exclude_bearish", "require_bullish"):
        selected = [r for r in rows if r["horizon"] == horizon and r["variant"] == variant]
        excess = [r["excess_percent"] for r in selected if r["excess_percent"] is not None]
        summary.append({
            "horizon": horizon, "variant": variant, "events": len(selected),
            "mean_net_percent": mean(r["net_percent"] for r in selected) if selected else None,
            "mean_mae_percent": mean(r["mae_percent"] for r in selected) if selected else None,
            "benchmark_events": len(excess),
            "mean_excess_percent": mean(excess) if excess else None,
        })
output = {"universe": [i.symbol for i in universe], "summary": summary, "events": rows,
          "latest_divergences": latest,
          "limitations": "現在の流動性上位20銘柄、直近最大252評価日。生存者・選択バイアスあり。"
          "各方式で連続候補初日のみ。取引重複あり。片道10bp、固定期間終値決済。"
          "損切り決済・財務・保有制約・当時の決算予定なし。独立した検証期間なし。"}
Path('/app/reports/rsi-divergence-study.json').write_text(
    json.dumps(output, ensure_ascii=False, indent=2)
)
print(json.dumps(summary, ensure_ascii=False, indent=2))
