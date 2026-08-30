import { backendJson } from "@/lib/backend";
import type {
  AiCapability,
  DashboardData,
  InstrumentListItem,
  MarketCandidate,
  PlanCapability,
  PositionItem,
} from "@/types/api";

export async function loadDashboardData(): Promise<DashboardData> {
  const [watchlist, positions, swing, position, dataPlan, aiCapability] = await Promise.all([
    backendJson<{ items: InstrumentListItem[] }>("/api/v1/watchlist"),
    backendJson<{ items: PositionItem[] }>("/api/v1/portfolio/positions"),
    backendJson<{ items: MarketCandidate[] }>(
      "/api/v1/market-candidates?horizon=5&limit=500",
    ),
    backendJson<{ items: MarketCandidate[] }>(
      "/api/v1/market-candidates?horizon=20&limit=500",
    ),
    backendJson<{ plan: string; capabilities: PlanCapability[] }>(
      "/api/v1/data-plan",
    ),
    backendJson<AiCapability>("/api/v1/ai-investment-review/capability"),
  ]);

  return {
    watchlist: watchlist.items,
    positions: positions.items,
    candidates: { "5": swing.items, "20": position.items },
    plan: dataPlan.plan,
    capabilities: dataPlan.capabilities,
    aiCapability,
  };
}
