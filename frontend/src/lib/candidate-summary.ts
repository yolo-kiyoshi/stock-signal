import type { DashboardData, InstrumentListItem, MarketCandidate } from "@/types/api";

export type CandidateAction = "all" | "unassessed" | "market_only" | "financial_pending" | "financial_caution" | MarketCandidate["action"];
export const candidateLabels: Record<CandidateAction, string> = {
  all: "対象銘柄", buy_candidate: "購入候補", watch: "様子見", avoid_new_buy: "新規購入回避",
  unassessed: "未判定", financial_pending: "財務確認待ち", financial_caution: "財務・イベント注意", market_only: "技術条件：TOPIXのみ保留",
};
export type CandidateItem = InstrumentListItem & Partial<MarketCandidate>;
export function matchesCandidate(item: CandidateItem, action: CandidateAction): boolean {
  if (action === "all") return true;
  if (action === "unassessed") return !item.action;
  if (action === "market_only") return item.market_gate_diagnostic?.status === "market_only";
  if (action === "financial_pending") return ["data_pending", "metrics_missing", "review_required"].includes(item.screening_status ?? "");
  if (action === "financial_caution") return item.screening_status === "financial_caution";
  return item.action === action;
}
export function candidateUniverse(data: DashboardData, horizon: 5 | 20): CandidateItem[] {
  const key = String(horizon) as "5" | "20";
  if (data.candidateErrors?.[key]) return [];
  const items = new Map<string, CandidateItem>();
  for (const item of data.candidateUniverse?.items ?? []) items.set(`${item.provider}:${item.symbol}`, item);
  for (const item of data.candidates[key]) items.set(`${item.provider}:${item.symbol}`, item);
  return [...items.values()];
}
