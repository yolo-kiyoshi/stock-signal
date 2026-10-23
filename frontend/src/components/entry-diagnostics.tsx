import type { EntryDiagnostics } from "@/types/api";

export const entryStrategyLabels: Record<string, string> = {
  breakout: "ブレイク追随",
  swing_pullback: "スイング押し目反発",
  position_pullback: "中長期押し目",
};

function value(number: number | null | undefined, unit: string, signed = false) {
  if (number == null || !Number.isFinite(number)) return "未算出";
  return `${signed && number > 0 ? "+" : ""}${number.toFixed(2)}${unit}`;
}

export function EntryDiagnosticsView({ diagnostics, asOfDate, targetRiskReward, targetPrice }: {
  diagnostics?: EntryDiagnostics | null;
  asOfDate: string;
  targetRiskReward?: number | null;
  targetPrice?: number | null;
}) {
  if (!diagnostics) return <span className="entry-diagnostics">価格位置・抵抗余地・損切り幅：未算出（再分析後に表示）</span>;
  const d = diagnostics;
  const resistance = d.resistance_status === "within_band" ? "抵抗帯内・突破未確認（距離0）"
    : d.resistance_status === "not_found" ? "上方の抵抗未検出・余地未算出"
    : d.resistance_status === "unavailable" ? "履歴不足・未算出"
    : `${value(d.resistance_room_percent, "%")} / ${value(d.resistance_room_atr, " ATR")}`;
  return (
    <span className="entry-diagnostics" aria-label={`${asOfDate}時点の価格位置とリスク幅`}>
      <span title="中期的な過熱を比較する指標です。数値だけで反落や安全性を判定しません。">20日線乖離 <b>{value(d.sma20_distance_percent, "%", true)}</b> / {value(d.sma20_distance_atr, " ATR", true)}</span>
      <span>10日騰落 {value(d.return_10d_percent, "%", true)}・20日騰落 {value(d.return_20d_percent, "%", true)}</span>
      <span>20日変動 {value(d.rise_20d_atr, " ATR", true)}</span>
      <span className="risk-comparison">
      <span title="形状の目標値幅 ÷ 損切り幅。到達予測や期待収益ではありません。">目標値幅ベースのR/R <b>{value(targetRiskReward, "")}</b><small>形状目標 {value(targetPrice, "円")}</small></span>
      <span title={`抵抗帯の手前または過去高値まで。抵抗未検出は上値余地が無限という意味ではありません。参考水準 ${value(d.resistance_price, "円")}`}>抵抗まで <b>{resistance}</b></span>
      <span title="抵抗までの値幅 ÷ 損切り幅。帯内の0は上昇余地がないという予測ではありません。">抵抗までのR/R <b>{value(d.resistance_risk_reward_ratio, "")}</b></span>
      </span>
      <span title={`採用した分析経路の損切り目安 ${value(d.stop_price, "円")}。約定価格や損失上限を保証しません。`}>損切り幅 <b>{value(d.stop_width_percent, "%")}</b> / {value(d.stop_width_atr, " ATR")}</span>
      <span className="entry-diagnostics-date">日足 {asOfDate} 時点・分析情報</span>
    </span>
  );
}
