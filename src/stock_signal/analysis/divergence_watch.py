"""強気RSI逆行を起点に固定価格水準で反転を待つ検証用の状態評価。"""

from collections.abc import Sequence
from dataclasses import dataclass

from stock_signal.analysis.divergence import RsiDivergence
from stock_signal.analysis.indicators import wilder_atr
from stock_signal.domain.market_data import DailyBar


@dataclass(frozen=True)
class DivergenceWatch:
    """監視開始時点で固定する水準。購入区分とは独立した研究用情報。"""

    confirmed_date: str
    second_low: float
    trigger: float
    stop: float
    atr: float


def create_divergence_watch(
    bars: Sequence[DailyBar],
    signal: RsiDivergence,
) -> DivergenceWatch | None:
    """確認日までの足だけから、2安値間の戻り高値とATRを固定する。"""
    if signal.direction != "bullish":
        return None
    known = sorted(
        (b for b in bars if b.trade_date.isoformat() <= signal.confirmed_date),
        key=lambda b: b.trade_date,
    )
    if not known or known[-1].trade_date.isoformat() != signal.confirmed_date:
        return None
    between = [
        b for b in known if signal.first_date < b.trade_date.isoformat() < signal.second_date
    ]
    atr = wilder_atr(known)
    if not between or not atr or atr <= 0:
        return None
    return DivergenceWatch(
        signal.confirmed_date,
        signal.second_price,
        max(float(b.high) for b in between) + 0.1 * atr,
        max(0.01, signal.second_price - 0.1 * atr),
        atr,
    )


def evaluate_divergence_watch(
    watch: DivergenceWatch,
    bars: Sequence[DailyBar],
) -> tuple[str, str | None]:
    """翌足から10本監視し、最初の失効・反転確認を保持する。"""
    from statistics import median

    known = sorted(bars, key=lambda b: b.trade_date)
    elapsed = 0
    for i, bar in enumerate(known):
        if bar.trade_date.isoformat() <= watch.confirmed_date:
            continue
        elapsed += 1
        if elapsed > 10:
            return "expired", bar.trade_date.isoformat()
        if float(bar.low) < watch.second_low:
            return "invalidated", bar.trade_date.isoformat()
        if i < 20:
            continue
        previous = known[i - 1]
        volumes = [b.volume for b in known[i - 20 : i]]
        baseline = median(volumes)
        if (
            float(bar.close) > watch.trigger
            and bar.close > bar.open
            and bar.close > previous.close
            and bar.close >= (bar.high + bar.low) / 2
            and baseline > 0
            and bar.volume >= baseline
            and abs(float(bar.open - previous.close)) <= 1.5 * watch.atr
        ):
            return "confirmed", bar.trade_date.isoformat()
    return "watching", None


def evaluate_watch_trade(
    bars: Sequence[DailyBar],
    signal_index: int,
    horizon: int,
    watch: DivergenceWatch,
) -> dict:
    """翌始値から期限までの研究用損益。窓開けストップと片道10bpを考慮する。"""
    if horizon < 1 or signal_index < 0:
        raise ValueError("期間と判定位置が不正です")
    if signal_index + horizon >= len(bars):
        return {"status": "unmatured"}
    entry = bars[signal_index + 1]
    price = float(entry.open)
    if price <= watch.stop or abs(price - float(bars[signal_index].close)) > 1.5 * watch.atr:
        return {"status": "entry_skipped"}
    path = bars[signal_index + 1 : signal_index + horizon + 1]
    exit_bar, exit_price, stopped = path[-1], float(path[-1].close), False
    visited = []
    for b in path:
        visited.append(b)
        if float(b.open) <= watch.stop or float(b.low) <= watch.stop:
            exit_bar, exit_price, stopped = b, min(float(b.open), watch.stop), True
            break
    net = (exit_price * 0.999 / (price * 1.001) - 1) * 100
    # ストップ足の日中順序は不明なため、MFE/MAEは期限までの参考値を別途返す。
    return {
        "status": "ready",
        "entry_date": entry.trade_date.isoformat(),
        "exit_date": exit_bar.trade_date.isoformat(),
        "net_percent": net,
        "entry_price": price,
        "exit_price": exit_price,
        "stopped": stopped,
        "holding_bars": len(visited),
        "horizon_mae_percent": (min(float(b.low) for b in path) / price - 1) * 100,
        "horizon_mfe_percent": (max(float(b.high) for b in path) / price - 1) * 100,
    }
