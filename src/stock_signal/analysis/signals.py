from __future__ import annotations

from stock_signal.database import list_watchlist_items, load_daily_bars
from stock_signal.domain.dashboard import TechnicalSignal


def calculate_reference_signals(database_url: str) -> list[TechnicalSignal]:
    """確率ではない参考テクニカルシグナルを計算する。"""
    signals: list[TechnicalSignal] = []
    for item in list_watchlist_items(database_url):
        bars = load_daily_bars(database_url, item.symbol, provider=item.provider)
        if len(bars) < 20:
            continue
        closes = [float(bar.close) for bar in bars]
        sma5 = sum(closes[-5:]) / 5
        sma20 = sum(closes[-20:]) / 20
        difference_percent = (sma5 / sma20 - 1) * 100
        direction = "up" if difference_percent >= 0 else "down"
        strength = min(100.0, round(abs(difference_percent) * 12.5, 1))
        change_percent = None
        if len(closes) >= 2:
            change_percent = round((closes[-1] / closes[-2] - 1) * 100, 2)
        signals.append(
            TechnicalSignal(
                symbol=item.symbol,
                display_name=item.display_name,
                provider=item.provider,
                as_of_date=bars[-1].trade_date.isoformat(),
                direction=direction,
                strength=strength,
                last_close=closes[-1],
                change_percent=change_percent,
                sma5=round(sma5, 4),
                sma20=round(sma20, 4),
                note="5日移動平均と20日移動平均の乖離に基づく参考値（確率ではありません）",
            )
        )
    return sorted(signals, key=lambda signal: signal.strength, reverse=True)
