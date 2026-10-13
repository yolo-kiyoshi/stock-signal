import { backendJson } from "@/lib/backend";
import type {
  AiCapability,
  DashboardData,
  InstrumentListItem,
  MarketCandidate,
  MarketEnvironment,
  PlanCapability,
  PositionItem,
} from "@/types/api";

async function loadMarketCandidates(horizon: 5 | 20): Promise<{ items: MarketCandidate[] }> {
  const items: MarketCandidate[] = [];
  const limit = 500;
  for (let offset = 0; ; offset += limit) {
    const page = await backendJson<{ items: MarketCandidate[]; next_offset: number | null }>(
      `/api/v1/market-candidates?horizon=${horizon}&limit=${limit}&offset=${offset}`,
    );
    items.push(...page.items);
    if (page.next_offset === null) return { items };
    if (page.next_offset !== offset + limit) {
      throw new Error("分析候補のページ情報を確認できません。APIを再起動してください。");
    }
  }
}

export async function loadDashboardData(): Promise<DashboardData> {
  const [watchlist, positions, swing, position, dataPlan, aiCapability, marketEnvironment] = await Promise.all([
    backendJson<{ items: InstrumentListItem[] }>("/api/v1/watchlist"),
    backendJson<{ items: PositionItem[] }>("/api/v1/portfolio/positions"),
    loadMarketCandidates(5),
    loadMarketCandidates(20),
    backendJson<{ plan: string; capabilities: PlanCapability[] }>(
      "/api/v1/data-plan",
    ),
    backendJson<AiCapability>("/api/v1/ai-investment-review/capability"),
    backendJson<MarketEnvironment>("/api/v1/market-environment/latest"),
  ]);

  return {
    watchlist: watchlist.items,
    positions: positions.items,
    candidates: { "5": swing.items, "20": position.items },
    plan: dataPlan.plan,
    capabilities: dataPlan.capabilities,
    aiCapability,
    marketEnvironment,
  };
}
