from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from stock_signal.domain.market_data import DailyBar


def _safe_filename(symbol: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", symbol.strip()).strip("-.")
    if not value:
        raise ValueError("symbol cannot be converted to a safe filename")
    return value.lower()


def render_candlestick_report(
    bars: Sequence[DailyBar],
    output_directory: Path,
) -> Path:
    if not bars:
        raise ValueError("cannot render a candlestick chart without daily bars")

    symbol = bars[0].symbol
    if any(bar.symbol != symbol for bar in bars):
        raise ValueError("all daily bars in a chart must have the same symbol")
    providers = {bar.provider for bar in bars}
    if len(providers) != 1:
        raise ValueError("all daily bars in a chart must use the same provider")

    output_directory.mkdir(parents=True, exist_ok=True)
    output_path = output_directory / f"{_safe_filename(symbol)}.html"
    dates = [bar.trade_date for bar in bars]

    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.04,
        row_heights=[0.76, 0.24],
        subplot_titles=("Price", "Volume"),
    )
    figure.add_trace(
        go.Candlestick(
            x=dates,
            open=[float(bar.open) for bar in bars],
            high=[float(bar.high) for bar in bars],
            low=[float(bar.low) for bar in bars],
            close=[float(bar.close) for bar in bars],
            name=symbol,
            increasing_line_color="#15803d",
            decreasing_line_color="#dc2626",
        ),
        row=1,
        col=1,
    )
    figure.add_trace(
        go.Bar(
            x=dates,
            y=[bar.volume for bar in bars],
            name="Volume",
            marker_color="#64748b",
            hovertemplate="%{x|%Y-%m-%d}<br>Volume %{y:,}<extra></extra>",
        ),
        row=2,
        col=1,
    )
    adjusted_label = "adjusted" if all(bar.is_adjusted for bar in bars) else "unadjusted"
    figure.update_layout(
        title={
            "text": (
                f"{symbol} — Daily OHLCV"
                f"<br><sup>Source: {bars[0].provider}; {adjusted_label}; "
                f"{dates[0]} to {dates[-1]}</sup>"
            ),
            "x": 0.02,
        },
        template="plotly_white",
        height=720,
        margin={"l": 70, "r": 30, "t": 90, "b": 50},
        hovermode="x unified",
        showlegend=False,
        xaxis_rangeslider_visible=False,
    )
    figure.update_yaxes(title_text="Price", fixedrange=False, row=1, col=1)
    figure.update_yaxes(title_text="Volume", fixedrange=False, row=2, col=1)
    figure.update_xaxes(title_text="Trade date", row=2, col=1)

    figure.write_html(
        output_path,
        include_plotlyjs=True,
        full_html=True,
        config={"displaylogo": False, "responsive": True},
        auto_open=False,
    )
    return output_path
