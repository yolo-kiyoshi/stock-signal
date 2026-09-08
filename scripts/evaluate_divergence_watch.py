"""強気逆行の即時評価と反転確認待ちを比較する、読み取り専用のイベント検証。"""

import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from statistics import mean

from stock_signal.analysis.divergence import detect_rsi_divergence
from stock_signal.analysis.divergence_watch import (
    create_divergence_watch,
    evaluate_divergence_watch,
    evaluate_watch_trade,
)
from stock_signal.config import Settings
from stock_signal.database import list_analysis_universe, load_daily_bars

settings = Settings.from_env()
universe = list_analysis_universe(settings.database_url, limit=100)
market = load_daily_bars(settings.database_url, "TOPIX", provider="jquants")
market_dates = sorted({b.trade_date for b in market})
cutoff = market_dates[-252].isoformat()
start = market_dates[-504].isoformat()
market_by_date = {b.trade_date.isoformat(): b for b in market}
seeds, trades, current = [], [], []
for n, item in enumerate(universe):
    bars = load_daily_bars(settings.database_url, item.symbol, provider="jquants")
    index_by_date = {b.trade_date.isoformat(): i for i, b in enumerate(bars)}
    busy_until = -1
    for i in range(80, len(bars)):
        day = bars[i].trade_date.isoformat()
        if day < start or i <= busy_until:
            continue
        signals = [
            d
            for d in detect_rsi_divergence(bars[: i + 1])
            if d.direction == "bullish" and d.confirmed_date == day
        ]
        if not signals:
            continue
        watch = create_divergence_watch(bars[: i + 1], signals[0])
        if watch is None:
            continue
        state, terminal_date = evaluate_divergence_watch(watch, bars[: i + 12])
        terminal = index_by_date[terminal_date] if terminal_date else len(bars) - 1
        busy_until = terminal
        period = "earlier" if day < cutoff else "later"
        key = f"{item.symbol}:{day}"
        seeds.append(
            {
                "id": key,
                "symbol": item.symbol,
                "period": period,
                "state": state,
                "terminal_date": terminal_date,
                **asdict(watch),
            }
        )
        if state == "watching":
            current.append(seeds[-1])
        for variant, index in (("immediate", i), ("confirmed", terminal)):
            if variant == "confirmed" and state != "confirmed":
                continue
            for horizon in (5, 20):
                result = evaluate_watch_trade(bars, index, horizon, watch)
                result.update(
                    {
                        "id": key,
                        "symbol": item.symbol,
                        "period": period,
                        "variant": variant,
                        "horizon": horizon,
                    }
                )
                if result["status"] == "ready":
                    m_entry = market_by_date.get(result["entry_date"])
                    m_exit = market_by_date.get(result["exit_date"])
                    result["excess_percent"] = (
                        result["net_percent"]
                        - (float(m_exit.close) / float(m_entry.open) - 1) * 100
                        if m_entry and m_exit
                        else None
                    )
                trades.append(result)
    if (n + 1) % 10 == 0:
        print("処理済み銘柄", n + 1, flush=True)
summary = []
for period in ("all", "earlier", "later"):
    for horizon in (5, 20):
        for variant in ("immediate", "confirmed"):
            selected = [
                t
                for t in trades
                if t["horizon"] == horizon
                and t["variant"] == variant
                and (period == "all" or t["period"] == period)
            ]
            ready = [t for t in selected if t["status"] == "ready"]
            excess = [t["excess_percent"] for t in ready if t["excess_percent"] is not None]
            summary.append(
                {
                    "period": period,
                    "horizon": horizon,
                    "variant": variant,
                    "trades": len(ready),
                    "status_counts": dict(Counter(t["status"] for t in selected)),
                    "mean_net_percent": mean(t["net_percent"] for t in ready) if ready else None,
                    "stop_rate": mean(t["stopped"] for t in ready) if ready else None,
                    "mean_horizon_mae": mean(t["horizon_mae_percent"] for t in ready)
                    if ready
                    else None,
                    "benchmark_count": len(excess),
                    "mean_excess_percent": mean(excess) if excess else None,
                }
            )
output = {
    "start": start,
    "split": cutoff,
    "as_of": market_dates[-1].isoformat(),
    "universe": [i.symbol for i in universe],
    "states": dict(Counter(s["state"] for s in seeds)),
    "summary": summary,
    "seeds": seeds,
    "trades": trades,
    "current_watches": current,
}
Path("/app/reports/divergence-watch-study.json").write_text(
    json.dumps(output, ensure_ascii=False, indent=2)
)
print(json.dumps({"states": output["states"], "summary": summary}, ensure_ascii=False, indent=2))
