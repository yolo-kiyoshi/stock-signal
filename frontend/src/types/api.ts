export type InstrumentListItem = {
  symbol: string;
  provider: string;
  display_name: string;
};

export type PositionItem = InstrumentListItem & {
  quantity: string;
  average_cost: string | null;
  account_type: string;
  memo: string | null;
  latest_close: string | null;
  latest_trade_date: string | null;
};

export type MarketCandidate = InstrumentListItem & {
  action: "buy_candidate" | "watch" | "avoid_new_buy";
  direction: "up" | "flat" | "down";
  evidence_score: number;
  as_of_date: string;
  transition_phase?: string;
  transition_summary?: string | null;
};

export type PlanCapability = {
  key: string;
  label: string;
  status: string;
  message: string;
};

export type DashboardData = {
  watchlist: InstrumentListItem[];
  positions: PositionItem[];
  candidates: Record<"5" | "20", MarketCandidate[]>;
  plan: string;
  capabilities: PlanCapability[];
  aiCapability: AiCapability;
};

export type AiCapability = {
  enabled: boolean;
  status: "ready" | "not_configured";
  model: string;
  max_output_tokens: number;
  message: string;
  notice: string;
};

export type DailyBar = {
  trade_date: string;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: number;
};

export type DailyBarsResponse = {
  range: { from: string; to: string };
  source: { provider: string; is_adjusted: boolean; price_basis: string };
  freshness: { latest_trade_date: string; status: string };
  indicators: {
    moving_averages: Record<string, Array<number | null>>;
    rsi: Record<string, Array<number | null>>;
    resistance_bands: Record<string, ResistanceBand[]>;
  };
  bars: DailyBar[];
};

export type ResistanceBand = {
  lower: number;
  upper: number;
  center: number;
  touches: number;
  first_touched: string;
  last_touched: string;
  distance_percent: number;
};

export type AnalysisCondition = {
  key: string;
  label: string;
  satisfied: boolean;
  description: string;
};

export type AnalysisStage = {
  phase: string;
  phase_label: string;
  satisfied_conditions: number;
  total_conditions: number;
  readiness_score: number;
  summary: string;
  conditions: AnalysisCondition[];
  next_condition: AnalysisCondition | null;
  current_price?: number | null;
  invalidation_price?: number | null;
  target_price?: number | null;
  risk_reward_ratio?: number | null;
};

export type AnalysisPattern = {
  type: string;
  name: string;
  direction: "up" | "flat" | "down";
  fit_score: number;
  description: string;
  lifecycle?: { status: string; status_label: string; summary: string } | null;
};

export type EquityCheck = {
  key: string;
  label: string;
  status: string;
  value: number | string | null;
  unit: string | null;
  description: string;
};

export type AnalysisResponse = {
  symbol: string;
  as_of_date: string;
  horizon_days: number;
  direction: "up" | "flat" | "down";
  scores: Record<"up" | "flat" | "down", number>;
  score_is_probability: false;
  factors: Array<{
    name: string;
    direction: "up" | "flat" | "down";
    score: number;
    description: string;
  }>;
  transition_readiness: AnalysisStage | null;
  position_entry: AnalysisStage | null;
  patterns: AnalysisPattern[];
  equity_checks: EquityCheck[];
  investment_decision: {
    action: "buy_candidate" | "watch" | "avoid_new_buy" | "insufficient_data";
    evidence_score: number;
    summary: string;
    reasons: string[];
    cautions: string[];
  } | null;
  engine: { id: string; version: string };
};

export type AiReviewResponse = {
  status: "ready";
  generated_at: string;
  model: string;
  report_text: string;
  report_segments: Array<{
    text: string;
    citation?: { url: string; title: string };
  }>;
  notice: string;
};
