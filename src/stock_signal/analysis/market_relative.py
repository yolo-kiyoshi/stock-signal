from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import fmean

from stock_signal.domain.market_data import DailyBar


@dataclass(frozen=True, slots=True)
class MarketRelativeMetrics:
    """同一取引日の終値から計算した対市場指標。"""

    window: int
    stock_return_percent: float
    market_return_percent: float
    relative_strength_percent: float
    beta: float | None


def calculate_market_relative_metrics(
    stock_bars: Sequence[DailyBar],
    market_bars: Sequence[DailyBar],
    window: int,
) -> MarketRelativeMetrics | None:
    """共通取引日の騰落率から相対力と単回帰ベータを計算する。"""
    stock_by_date = {bar.trade_date: float(bar.close) for bar in stock_bars}
    market_by_date = {bar.trade_date: float(bar.close) for bar in market_bars}
    dates = sorted(stock_by_date.keys() & market_by_date.keys())
    if len(dates) <= window:
        return None
    dates = dates[-window - 1:]
    stock_closes = [stock_by_date[item] for item in dates]
    market_closes = [market_by_date[item] for item in dates]
    stock_returns = [
        current / previous - 1
        for previous, current in zip(
            stock_closes[:-1], stock_closes[1:], strict=True
        )
    ]
    market_returns = [
        current / previous - 1
        for previous, current in zip(
            market_closes[:-1], market_closes[1:], strict=True
        )
    ]
    market_mean = fmean(market_returns)
    stock_mean = fmean(stock_returns)
    market_variance = fmean(
        (value - market_mean) ** 2 for value in market_returns
    )
    beta = None
    if market_variance > 0:
        covariance = fmean(
            (stock - stock_mean) * (market - market_mean)
            for stock, market in zip(stock_returns, market_returns, strict=True)
        )
        beta = covariance / market_variance
    stock_return = (stock_closes[-1] / stock_closes[0] - 1) * 100
    market_return = (market_closes[-1] / market_closes[0] - 1) * 100
    return MarketRelativeMetrics(
        window,
        round(stock_return, 2),
        round(market_return, 2),
        round(stock_return - market_return, 2),
        round(beta, 2) if beta is not None else None,
    )

