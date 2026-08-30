"use client";

import {
  CandlestickSeries,
  ColorType,
  createChart,
  HistogramSeries,
  LineStyle,
  LineSeries,
  type Time,
} from "lightweight-charts";
import { useEffect, useRef } from "react";

import type { DailyBarsResponse } from "@/types/api";

type Props = {
  payload: DailyBarsResponse;
  horizon: 5 | 20;
  indicators: {
    movingAverage: boolean;
    rsi: boolean;
    resistance: boolean;
  };
};

const movingAverageColors: Record<number, string> = {
  5: "#b56b2f",
  20: "#3476a5",
  60: "#75549a",
};

export function PriceChart({ payload, horizon, indicators }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || payload.bars.length === 0) return;

    const chart = createChart(container, {
      width: container.clientWidth,
      height: indicators.rsi ? 560 : 460,
      layout: {
        background: { type: ColorType.Solid, color: "#fffdf8" },
        textColor: "#38433f",
      },
      grid: {
        vertLines: { color: "#eef0e9" },
        horzLines: { color: "#eef0e9" },
      },
      rightPriceScale: { borderColor: "#d8ddd5" },
      timeScale: { borderColor: "#d8ddd5", timeVisible: false },
    });
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: "#267551",
      downColor: "#b5453e",
      wickUpColor: "#267551",
      wickDownColor: "#b5453e",
      borderVisible: false,
    });
    candles.setData(payload.bars.map((bar) => ({
      time: bar.trade_date as Time,
      open: Number(bar.open),
      high: Number(bar.high),
      low: Number(bar.low),
      close: Number(bar.close),
    })));

    const volume = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
      color: "rgba(87, 103, 98, .28)",
    });
    chart.priceScale("volume").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    volume.setData(payload.bars.map((bar) => ({
      time: bar.trade_date as Time,
      value: bar.volume,
      color: Number(bar.close) >= Number(bar.open)
        ? "rgba(38, 117, 81, .28)"
        : "rgba(181, 69, 62, .28)",
    })));

    if (indicators.movingAverage) {
      const windows = horizon === 5 ? [5, 20] : [20, 60];
      windows.forEach((window) => {
        const series = chart.addSeries(LineSeries, {
          color: movingAverageColors[window],
          lineWidth: 2,
          priceLineVisible: false,
          lastValueVisible: false,
          title: `${window}日線`,
        });
        const values = payload.indicators.moving_averages[String(window)] ?? [];
        series.setData(values.flatMap((value, index) => (
          value == null
            ? []
            : [{ time: payload.bars[index].trade_date as Time, value }]
        )));
      });
    }

    if (indicators.resistance) {
      const lookback = horizon === 5 ? "60" : "120";
      const resistanceBands = (payload.indicators.resistance_bands[lookback] ?? [])
        .toSorted((left, right) => Math.abs(left.distance_percent) - Math.abs(right.distance_percent))
        .slice(0, 3);
      resistanceBands.forEach((band, index) => {
        const series = chart.addSeries(LineSeries, {
          color: index === 0 ? "rgba(169, 87, 55, .72)" : "rgba(169, 87, 55, .38)",
          lineStyle: LineStyle.Dashed,
          lineWidth: index === 0 ? 2 : 1,
          priceLineVisible: false,
          lastValueVisible: false,
          title: `抵抗帯 ${band.touches}回接触`,
        });
        series.setData([
          { time: payload.bars[0].trade_date as Time, value: band.center },
          { time: payload.bars.at(-1)!.trade_date as Time, value: band.center },
        ]);
      });
    }

    if (indicators.rsi) {
      const rsiWindow = horizon === 5 ? "14" : "28";
      const rsiSeries = chart.addSeries(LineSeries, {
        color: "#75549a",
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: true,
        title: `RSI ${rsiWindow}日`,
      }, 1);
      const values = payload.indicators.rsi[rsiWindow] ?? [];
      rsiSeries.setData(values.flatMap((value, index) => (
        value == null
          ? []
          : [{ time: payload.bars[index].trade_date as Time, value }]
      )));
      for (const value of [30, 70]) {
        rsiSeries.createPriceLine({
          price: value,
          color: "rgba(104, 115, 111, .45)",
          lineStyle: LineStyle.Dotted,
          lineWidth: 1,
          axisLabelVisible: true,
          title: value === 70 ? "過熱目安" : "売られ過ぎ目安",
        });
      }
      chart.panes()[1]?.setHeight(130);
    }
    chart.timeScale().fitContent();

    const resizeObserver = new ResizeObserver(() => {
      chart.applyOptions({ width: container.clientWidth });
    });
    resizeObserver.observe(container);
    return () => {
      resizeObserver.disconnect();
      chart.remove();
    };
  }, [payload, horizon, indicators]);

  return <div ref={containerRef} className="price-chart" aria-label="日足チャート" />;
}
