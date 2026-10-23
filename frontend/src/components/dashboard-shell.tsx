"use client";

import { useRouter } from "next/navigation";
import { useEffect, useLayoutEffect, useMemo, useRef, useState, useTransition } from "react";
import type { FormEvent } from "react";

import { ResearchPanel } from "@/components/research-panel";
import { PositionJournal } from "@/components/position-journal";
import { EntryDiagnosticsView, entryStrategyLabels } from "@/components/entry-diagnostics";
import { PriceChart } from "@/components/price-chart";
import type {
  AiReviewResponse,
  AnalysisResponse,
  DashboardData,
  DailyBarsResponse,
  InstrumentListItem,
  MarketCandidate,
  PositionItem,
  RakutenImportResponse,
} from "@/types/api";

type Tab = "positions" | "watchlist" | "candidates";
type CandidateAction = "all" | MarketCandidate["action"];
type ChartRange = "1m" | "3m" | "6m" | "1y" | "all";
type NavItem = InstrumentListItem & Partial<MarketCandidate> & Partial<PositionItem>;
type ListFilters = {
  market: string;
  instrumentType: string;
  sector17: string;
  sector33: string;
  earnings: "all" | "within7" | "within30" | "scheduled" | "unscheduled";
  liquidity: string;
  freshness: string;
};
type ListSort = "symbol" | "capital_stock" | "employees" | "sales" | "market_cap";

const defaultListFilters: ListFilters = {
  market: "all",
  instrumentType: "all",
  sector17: "all",
  sector33: "all",
  earnings: "all",
  liquidity: "all",
  freshness: "all",
};

const directionLabels = { up: "上昇", flat: "停滞", down: "下落" } as const;
const actionLabels = {
  buy_candidate: "購入候補",
  watch: "様子見",
  avoid_new_buy: "新規購入回避",
  insufficient_data: "データ不足",
} as const;
const entryStageLabels: Record<string, string> = {
  setup_confirmed: "形を確認",
  conditional_entry: "条件付き",
  entry_ready: "買い条件が整う",
  wait_for_pullback: "押し目待ち",
  avoid: "購入回避",
  not_applicable: "買い条件なし",
};
const instrumentTypeLabels: Record<string, string> = {
  stock: "株式",
  etf: "ETF",
  reit: "REIT",
};
const liquidityLabels: Record<string, string> = {
  very_high: "流動性 最高",
  high: "流動性 高",
  medium: "流動性 中",
  low: "流動性 低",
  unknown: "流動性 未評価",
};
const freshnessLabels: Record<string, string> = {
  fresh: "日足 最新",
  stale: "日足 要確認",
  missing: "日足 未取得",
};
const rangeLabels: Record<ChartRange, string> = {
  "1m": "1か月",
  "3m": "3か月",
  "6m": "6か月",
  "1y": "1年",
  all: "全期間",
};
const horizonProfiles = {
  5: {
    label: "スイング",
    future: "5営業日先",
    holding: "数日〜数週間の新規購入タイミング",
    purpose: "ブレイク追随と押し目反発を別々に確認",
    caution: "企業価値は評価しません。出来高と無効化水準も確認してください。",
  },
  20: {
    label: "中長期の買い場",
    future: "20営業日先",
    holding: "数週間〜数か月の買い場確認",
    purpose: "中期上昇トレンド内の押し目と支持帯を確認",
    caution: "企業価値は評価しません。反発と業績・財務を別に確認してください。",
  },
} as const;

function normalize(value: string): string {
  return value.normalize("NFKC").toLowerCase().replace(/\s/g, "");
}

function uniqueValues(items: NavItem[], pick: (item: NavItem) => string | null | undefined): string[] {
  return [...new Set(items.map(pick).filter((value): value is string => Boolean(value)))].sort(
    (left, right) => left.localeCompare(right, "ja"),
  );
}

function earningsTag(item: NavItem): string {
  if (item.days_to_earnings == null) return "決算 未定";
  if (item.days_to_earnings === 0) return "決算 当日";
  return `決算 ${item.days_to_earnings}日`;
}

function freshnessTag(item: NavItem): string {
  if (item.freshness_status === "missing" || item.data_age_days == null) return "日足 未取得";
  if (item.data_age_days === 0) return "日足 最新";
  return `日足 ${item.data_age_days}日前`;
}

function initialInstrument(data: DashboardData): NavItem | null {
  return data.positions[0] ?? data.watchlist[0] ?? data.candidates["5"][0] ?? null;
}

async function apiJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api/backend${path.replace(/^\/api\/v1/, "")}`, {
    ...init,
    cache: "no-store",
  });
  const payload = await response.json().catch(() => ({})) as T & { detail?: string };
  if (!response.ok) {
    throw new Error(payload.detail ?? `処理に失敗しました（${response.status}）`);
  }
  return payload;
}

function parseSymbols(value: string): string[] {
  return [...new Set(
    value.normalize("NFKC").toUpperCase().split(/[\s,、]+/).filter(Boolean),
  )];
}

export function DashboardShell({ data }: { data: DashboardData }) {
  const router = useRouter();
  const [refreshing, startRefresh] = useTransition();
  const mainPanelRef = useRef<HTMLElement>(null);
  const preservedScrollTopRef = useRef<number | null>(null);
  const firstInstrument = initialInstrument(data);
  const [tab, setTab] = useState<Tab>(
    data.positions.length ? "positions" : data.watchlist.length ? "watchlist" : "candidates",
  );
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [horizon, setHorizon] = useState<5 | 20>(5);
  const [candidateHorizon, setCandidateHorizon] = useState<5 | 20>(5);
  const [query, setQuery] = useState("");
  const [pagination, setPagination] = useState({ key: "", page: 0 });
  const [candidateAction, setCandidateAction] = useState<CandidateAction>("all");
  const [listFilters, setListFilters] = useState<ListFilters>(defaultListFilters);
  const [listSort, setListSort] = useState<ListSort>("symbol");
  const [selected, setSelected] = useState<NavItem | null>(firstInstrument);
  const [chartRange, setChartRange] = useState<ChartRange>("6m");
  const [customRange, setCustomRange] = useState<{ from: string; to: string } | null>(null);
  const [indicators, setIndicators] = useState({
    movingAverage: true,
    rsi: true,
    resistance: true,
    support: true,
  });
  const [bars, setBars] = useState<DailyBarsResponse | null>(null);
  const [analysis, setAnalysis] = useState<AnalysisResponse | null>(null);
  const [loading, setLoading] = useState(Boolean(firstInstrument));
  const [error, setError] = useState<string | null>(null);
  const [sidebarMessage, setSidebarMessage] = useState<string | null>(null);
  const [mutating, setMutating] = useState(false);
  const [registrationInput, setRegistrationInput] = useState("");
  const [positionSymbol, setPositionSymbol] = useState("");
  const [positionQuantity, setPositionQuantity] = useState("");
  const [positionCost, setPositionCost] = useState("");
  const rakutenFileInputRef = useRef<HTMLInputElement>(null);
  const [rakutenFile, setRakutenFile] = useState<File | null>(null);
  const [rakutenAsOf, setRakutenAsOf] = useState("");
  const [rakutenPreview, setRakutenPreview] = useState<RakutenImportResponse | null>(null);
  const [rakutenLoading, setRakutenLoading] = useState(false);
  const [aiReview, setAiReview] = useState<AiReviewResponse | null>(null);
  const [aiLoading, setAiLoading] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);

  const candidateItems = data.candidates[String(candidateHorizon) as "5" | "20"];
  const candidateHoldReasons = Object.entries(candidateItems.reduce<Record<string, number>>((counts, item) => {
    if (item.action !== "buy_candidate") {
      const reason = item.judgment_reason || "条件未充足・詳細を確認してください";
      counts[reason] = (counts[reason] ?? 0) + 1;
    }
    return counts;
  }, {})).sort((left, right) => right[1] - left[1]).slice(0, 3);
  const actionFilteredCandidates = candidateAction === "all"
    ? candidateItems
    : candidateItems.filter((item) => item.action === candidateAction);
  const activeItems: NavItem[] = tab === "positions"
    ? data.positions
    : tab === "watchlist"
      ? data.watchlist
      : actionFilteredCandidates;
  const filterSource = tab === "candidates" ? candidateItems : activeItems;
  const filterOptions = useMemo(() => ({
    markets: uniqueValues(filterSource, (item) => item.market),
    instrumentTypes: uniqueValues(filterSource, (item) => item.instrument_type),
    sectors17: uniqueValues(filterSource, (item) => item.sector_17_name),
    sectors33: uniqueValues(filterSource, (item) => item.sector_33_name),
  }), [filterSource]);
  const filteredItems = useMemo(() => {
    const normalizedQuery = normalize(query);
    const filtered = activeItems.filter((item) => {
      const searchTarget = normalize([
        item.symbol,
        item.display_name,
        item.market,
        item.instrument_type ? instrumentTypeLabels[item.instrument_type] ?? item.instrument_type : "",
        item.sector_17_name,
        item.sector_33_name,
      ].filter(Boolean).join(""));
      if (normalizedQuery && !searchTarget.includes(normalizedQuery)) return false;
      if (listFilters.market !== "all" && item.market !== listFilters.market) return false;
      if (listFilters.instrumentType !== "all" && item.instrument_type !== listFilters.instrumentType) return false;
      if (listFilters.sector17 !== "all" && item.sector_17_name !== listFilters.sector17) return false;
      if (listFilters.sector33 !== "all" && item.sector_33_name !== listFilters.sector33) return false;
      if (listFilters.liquidity !== "all" && item.liquidity_rank !== listFilters.liquidity) return false;
      if (listFilters.freshness !== "all" && item.freshness_status !== listFilters.freshness) return false;
      if (listFilters.earnings === "scheduled" && item.days_to_earnings == null) return false;
      if (listFilters.earnings === "unscheduled" && item.days_to_earnings != null) return false;
      if (listFilters.earnings === "within7" && (item.days_to_earnings == null || item.days_to_earnings > 7)) return false;
      if (listFilters.earnings === "within30" && (item.days_to_earnings == null || item.days_to_earnings > 30)) return false;
      return true;
    });
    if (listSort === "symbol") return filtered.sort((left, right) => left.symbol.localeCompare(right.symbol, "ja"));
    return filtered.sort((left, right) => {
      const field = listSort as keyof Pick<NavItem, "capital_stock" | "employees" | "sales" | "market_cap">;
      const leftValue = left[field] == null ? null : Number(left[field]);
      const rightValue = right[field] == null ? null : Number(right[field]);
      if (leftValue == null && rightValue == null) return left.symbol.localeCompare(right.symbol, "ja");
      if (leftValue == null) return 1;
      if (rightValue == null) return -1;
      return rightValue - leftValue || left.symbol.localeCompare(right.symbol, "ja");
    });
  }, [activeItems, listFilters, query, listSort]);
  const pageKey = JSON.stringify([tab, candidateHorizon, candidateAction, query, listFilters, listSort]);
  const pageCount = Math.max(1, Math.ceil(filteredItems.length / 100));
  const currentPage = pagination.key === pageKey ? Math.min(pagination.page, pageCount - 1) : 0;
  const visibleItems = filteredItems.slice(currentPage * 100, (currentPage + 1) * 100);
  const activeFilterCount = Object.values(listFilters).filter((value) => value !== "all").length;
  const hasListConstraints = activeFilterCount > 0 || query.trim().length > 0
    || (tab === "candidates" && candidateAction !== "all");
  function clearListConstraints() {
    setQuery("");
    setListFilters(defaultListFilters);
    setCandidateAction("all");
  }
  const watchedSymbols = useMemo(
    () => new Set(data.watchlist.map((item) => `${item.provider}:${item.symbol}`)),
    [data.watchlist],
  );

  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    const barParameters = new URLSearchParams({ provider: selected.provider });
    if (customRange) {
      barParameters.set("range", "all");
      barParameters.set("from", customRange.from);
      barParameters.set("to", customRange.to);
    } else {
      barParameters.set("range", chartRange);
    }
    const analysisParameters = new URLSearchParams({
      provider: selected.provider,
      horizon: String(horizon),
    });
    Promise.all([
      apiJson<DailyBarsResponse>(
        `/api/v1/instruments/${selected.symbol}/daily-bars?${barParameters}`,
        { signal: controller.signal },
      ),
      apiJson<AnalysisResponse>(
        `/api/v1/instruments/${selected.symbol}/analysis/latest?${analysisParameters}`,
        { signal: controller.signal },
      ),
    ]).then(([barPayload, analysisPayload]) => {
      setBars(barPayload);
      setAnalysis(analysisPayload);
    }).catch((reason: unknown) => {
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      setError(reason instanceof Error ? reason.message : "分析結果を取得できませんでした");
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [selected, horizon, chartRange, customRange]);

  useLayoutEffect(() => {
    if (loading || preservedScrollTopRef.current == null) return;
    if (bars && analysis && mainPanelRef.current) {
      mainPanelRef.current.scrollTop = preservedScrollTopRef.current;
    }
    preservedScrollTopRef.current = null;
  }, [loading, bars, analysis, error]);

  function preserveMainScroll(): void {
    if (preservedScrollTopRef.current == null && mainPanelRef.current) {
      preservedScrollTopRef.current = mainPanelRef.current.scrollTop;
    }
  }

  function beginSelection(item: NavItem): void {
    preserveMainScroll();
    setLoading(true);
    setError(null);
    setBars(null);
    setAnalysis(null);
    setAiReview(null);
    setAiError(null);
    setSelected(item);
  }

  function selectNavigationItem(item: NavItem): void {
    if (tab === "candidates" && horizon !== candidateHorizon) {
      setHorizon(candidateHorizon);
    }
    beginSelection(item);
  }

  function switchTab(nextTab: Tab): void {
    setTab(nextTab);
    setQuery("");
    setListFilters(defaultListFilters);
    const items = nextTab === "positions"
      ? data.positions
      : nextTab === "watchlist"
        ? data.watchlist
        : actionFilteredCandidates;
    if (nextTab === "candidates") setHorizon(candidateHorizon);
    if (items[0]) beginSelection(items[0]);
  }

  function switchDetailHorizon(nextHorizon: 5 | 20): void {
    preserveMainScroll();
    setLoading(Boolean(selected));
    setError(null);
    setBars(null);
    setAnalysis(null);
    setAiReview(null);
    setHorizon(nextHorizon);
  }

  function switchCandidateHorizon(nextHorizon: 5 | 20): void {
    preserveMainScroll();
    const nextCandidates = data.candidates[String(nextHorizon) as "5" | "20"];
    const filteredCandidates = candidateAction === "all"
      ? nextCandidates
      : nextCandidates.filter((item) => item.action === candidateAction);
    const sameInstrument = filteredCandidates.find(
      (item) => item.symbol === selected?.symbol && item.provider === selected.provider,
    );
    const nextSelection = sameInstrument ?? filteredCandidates[0] ?? null;
    setCandidateHorizon(nextHorizon);
    setHorizon(nextHorizon);
    setError(null);
    setBars(null);
    setAnalysis(null);
    setAiReview(null);
    if (nextSelection) {
      setLoading(true);
      setSelected(nextSelection);
    } else {
      setSelected(null);
      setLoading(false);
    }
  }

  async function runMutation(action: () => Promise<unknown>, success: string): Promise<boolean> {
    setMutating(true);
    setSidebarMessage(null);
    try {
      await action();
      setSidebarMessage(success);
      router.refresh();
      return true;
    } catch (reason) {
      setSidebarMessage(reason instanceof Error ? reason.message : "処理に失敗しました");
      return false;
    } finally {
      setMutating(false);
    }
  }

  async function registerWatchlist(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const symbols = parseSymbols(registrationInput);
    if (!symbols.length || symbols.length > 200) {
      setSidebarMessage("証券コードは1〜200件で入力してください");
      return;
    }
    const succeeded = await runMutation(
      () => apiJson("/api/v1/watchlists/%E3%82%A6%E3%82%A9%E3%83%83%E3%83%81/items/bulk", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbols }),
      }),
      `${symbols.length}件の登録処理を受け付けました`,
    );
    if (succeeded) setRegistrationInput("");
  }

  async function savePosition(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const succeeded = await runMutation(
      () => apiJson("/api/v1/portfolio/positions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          symbol: positionSymbol.normalize("NFKC").toUpperCase(),
          quantity: positionQuantity,
          average_cost: positionCost || null,
        }),
      }),
      "保有銘柄を保存しました",
    );
    if (succeeded) {
      setPositionSymbol("");
      setPositionQuantity("");
      setPositionCost("");
    }
  }

  function selectRakutenFile(file: File | null): void {
    setRakutenPreview(null);
    setSidebarMessage(null);
    if (file && file.size > 2 * 1024 * 1024) {
      setRakutenFile(null);
      setSidebarMessage("CSVファイルは2MB以下にしてください");
      if (rakutenFileInputRef.current) rakutenFileInputRef.current.value = "";
      return;
    }
    setRakutenFile(file);
  }

  async function previewRakutenImport(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!rakutenFile) {
      setSidebarMessage("楽天証券からダウンロードしたCSVを選択してください");
      return;
    }
    setRakutenLoading(true);
    setSidebarMessage(null);
    try {
      const result = await apiJson<RakutenImportResponse>(
        "/api/v1/portfolio/imports/rakuten/preview",
        { method: "POST", headers: { "Content-Type": "text/csv" }, body: rakutenFile },
      );
      setRakutenPreview(result);
    } catch (reason) {
      setRakutenPreview(null);
      setSidebarMessage(reason instanceof Error ? reason.message : "CSVを確認できませんでした");
    } finally {
      setRakutenLoading(false);
    }
  }

  async function applyRakutenImport(): Promise<void> {
    if (!rakutenFile || !rakutenPreview?.summary.importable_symbols) return;
    setRakutenLoading(true);
    setSidebarMessage(null);
    try {
      const result = await apiJson<RakutenImportResponse>(
        `/api/v1/portfolio/imports/rakuten/apply${rakutenAsOf ? `?holdings_as_of=${encodeURIComponent(rakutenAsOf)}` : ""}`,
        { method: "POST", headers: { "Content-Type": "text/csv" }, body: rakutenFile },
      );
      setSidebarMessage(result.message ?? "保有銘柄を登録・更新しました");
      setRakutenPreview(null);
      setRakutenFile(null);
      if (rakutenFileInputRef.current) rakutenFileInputRef.current.value = "";
      router.refresh();
    } catch (reason) {
      setSidebarMessage(reason instanceof Error ? reason.message : "CSVを取り込めませんでした");
    } finally {
      setRakutenLoading(false);
    }
  }

  async function addCandidateToWatchlist(item: NavItem): Promise<void> {
    await runMutation(
      () => apiJson("/api/v1/watchlists/%E3%82%A6%E3%82%A9%E3%83%83%E3%83%81/items/bulk", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbols: [item.symbol] }),
      }),
      `${item.symbol}をウォッチへ追加しました`,
    );
  }

  async function removeItem(item: NavItem, source: "watchlist" | "positions"): Promise<void> {
    const listLabel = source === "watchlist" ? "ウォッチ" : "保有";
    if (!window.confirm(`${item.symbol} ${item.display_name}を${listLabel}から削除しますか？`)) return;
    const path = source === "watchlist"
      ? `/api/v1/watchlist/${item.symbol}?provider=${item.provider}`
      : `/api/v1/portfolio/positions/${item.symbol}?provider=${item.provider}`;
    const succeeded = await runMutation(
      () => apiJson(path, { method: "DELETE" }),
      `${item.symbol}を${listLabel}から削除しました`,
    );
    if (succeeded && selected?.symbol === item.symbol) {
      const sourceItems = source === "watchlist" ? data.watchlist : data.positions;
      const next = sourceItems.find((candidate) => candidate.symbol !== item.symbol)
        ?? data.candidates[String(horizon) as "5" | "20"][0]
        ?? null;
      if (next) {
        beginSelection(next);
      } else {
        setSelected(null);
        setBars(null);
        setAnalysis(null);
        setLoading(false);
      }
    }
  }

  async function runAiReview(): Promise<void> {
    if (!selected || !data.aiCapability.enabled) return;
    setAiLoading(true);
    setAiError(null);
    setAiReview(null);
    const parameters = new URLSearchParams({
      provider: selected.provider,
      horizon: String(horizon),
    });
    try {
      setAiReview(await apiJson<AiReviewResponse>(
        `/api/v1/instruments/${selected.symbol}/ai-investment-review?${parameters}`,
        { method: "POST" },
      ));
    } catch (reason) {
      setAiError(reason instanceof Error ? reason.message : "AI最終確認に失敗しました");
    } finally {
      setAiLoading(false);
    }
  }

  function applyCustomRange(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const from = String(form.get("from") ?? "");
    const to = String(form.get("to") ?? "");
    if (!from || !to || from > to) {
      setError("開始日と終了日を正しい順序で指定してください");
      return;
    }
    preserveMainScroll();
    setLoading(Boolean(selected));
    setError(null);
    setCustomRange({ from, to });
  }

  function switchChartRange(range: ChartRange): void {
    preserveMainScroll();
    setLoading(Boolean(selected));
    setCustomRange(null);
    setChartRange(range);
  }

  const decision = analysis?.investment_decision;
  const stage = analysis?.position_entry ?? analysis?.transition_readiness;
  const latestBar = bars?.bars.at(-1);
  const horizonProfile = horizonProfiles[horizon];

  return (
    <div className={`workspace${sidebarCollapsed ? " sidebar-is-collapsed" : ""}`}>
      <aside className={`instrument-sidebar${sidebarCollapsed ? " collapsed" : ""}`}>
        <div className="sidebar-toolbar">
          <strong>銘柄ナビゲーション</strong>
          <button
            type="button"
            className="sidebar-collapse"
            aria-label={sidebarCollapsed ? "サイドバーを開く" : "サイドバーを畳む"}
            aria-expanded={!sidebarCollapsed}
            onClick={() => setSidebarCollapsed((value) => !value)}
          >{sidebarCollapsed ? "›" : "‹"}</button>
        </div>
        <div className="sidebar-content">
          <div className="sidebar-tabs" role="tablist" aria-label="銘柄一覧">
            {([
              ["positions", "保有", data.positions.length],
              ["watchlist", "ウォッチ", data.watchlist.length],
              ["candidates", "分析候補", candidateItems.length],
            ] as const).map(([key, label, count]) => (
              <button key={key} type="button" role="tab" aria-selected={tab === key} className={tab === key ? "active" : ""} onClick={() => switchTab(key)}>
                <span>{label}</span><strong>{count}</strong>
              </button>
            ))}
          </div>

          {tab === "positions" && (
            <div className="position-editors">
              <details className="sidebar-editor">
                <summary>1銘柄を登録・更新</summary>
                <form onSubmit={savePosition}>
                  <input value={positionSymbol} onChange={(event) => setPositionSymbol(event.target.value)} placeholder="証券コード" maxLength={4} required />
                  <input value={positionQuantity} onChange={(event) => setPositionQuantity(event.target.value)} placeholder="数量" inputMode="decimal" required />
                  <input value={positionCost} onChange={(event) => setPositionCost(event.target.value)} placeholder="平均取得単価（任意）" inputMode="decimal" />
                  <button type="submit" disabled={mutating || rakutenLoading}>保存</button>
                </form>
              </details>
              <details className="sidebar-editor rakuten-importer">
                <summary>楽天証券CSVから取り込む</summary>
                <form onSubmit={previewRakutenImport}>
                  <label className="csv-file-field">
                    <span>資産残高（すべて）のCSV</span>
                    <input
                      ref={rakutenFileInputRef}
                      type="file"
                      accept=".csv,text/csv"
                      onChange={(event) => selectRakutenFile(event.target.files?.[0] ?? null)}
                      required
                    />
                  </label>
                  <label>CSVの保有基準日（不明なら空欄）
                    <input type="date" value={rakutenAsOf} onChange={(event) => setRakutenAsOf(event.target.value)} />
                  </label>
                  <button type="submit" disabled={!rakutenFile || rakutenLoading || mutating}>
                    {rakutenLoading && !rakutenPreview ? "確認中…" : "内容を確認"}
                  </button>
                </form>
                <p className="import-note">国内株式だけを対象にし、CSVにない既存保有は削除しません。CSVファイル自体は保存しません。</p>
                {rakutenPreview && (
                  <div className="import-preview" aria-live="polite">
                    <dl>
                      <div><dt>登録・更新</dt><dd>{rakutenPreview.summary.importable_symbols}銘柄</dd></div>
                      <div><dt>対象外</dt><dd>{rakutenPreview.summary.ignored_rows}件</dd></div>
                      <div><dt>要確認</dt><dd>{rakutenPreview.summary.unmatched_symbols + rakutenPreview.summary.invalid_domestic_rows}件</dd></div>
                    </dl>
                    <div className="import-position-list">
                      {rakutenPreview.positions.map((position) => (
                        <div key={position.symbol} className={position.status === "ready" ? "" : "has-warning"}>
                          <span><strong>{position.symbol}</strong> {position.display_name}</span>
                          <small>{Number(position.quantity).toLocaleString("ja-JP")}株・{position.account_type}</small>
                          {position.previous_quantity != null && <small>前回 {position.previous_quantity}株 → 今回 {position.quantity}株／平均取得価格 {position.previous_average_cost ?? "未登録"}円 → {position.average_cost ?? "未登録"}円</small>}
                          {position.status !== "ready" && <em>{position.message}</em>}
                        </div>
                      ))}
                    </div>
                    {Object.keys(rakutenPreview.skipped_types).length > 0 && (
                      <p>{Object.entries(rakutenPreview.skipped_types).map(([name, count]) => `${name} ${count}件`).join("、")}は対象外です。</p>
                    )}
                    {rakutenPreview.summary.duplicate_rows > 0 && <p>同一コードの{rakutenPreview.summary.duplicate_rows}行を数量加重平均で統合します。</p>}
                    {!!rakutenPreview.missing_positions?.length && <div className="decision-warning">
                      <strong>今回のCSVに見当たらない保有</strong>
                      <ul>{rakutenPreview.missing_positions.map((position) => <li key={`${position.provider}-${position.symbol}`}>{position.symbol} {position.display_name} · {position.quantity}株</li>)}</ul>
                      <p>売却済みか、CSVの出力範囲外かを確認してください。保有は自動削除しません。売却済みの場合は保有一覧の削除ボタンで照合を完了できます。</p>
                    </div>}
                    <button
                      type="button"
                      className="import-apply-button"
                      disabled={!rakutenPreview.summary.importable_symbols || rakutenLoading || mutating}
                      onClick={applyRakutenImport}
                    >
                      {rakutenLoading ? "登録中…" : `${rakutenPreview.summary.importable_symbols}銘柄を登録・更新`}
                    </button>
                  </div>
                )}
              </details>
            </div>
          )}
          {tab === "watchlist" && (
            <details className="sidebar-editor">
              <summary>証券コードをまとめて追加</summary>
              <form onSubmit={registerWatchlist}>
                <textarea value={registrationInput} onChange={(event) => setRegistrationInput(event.target.value)} placeholder="7203, 5803, 7951" rows={3} required />
                <button type="submit" disabled={mutating}>ウォッチへ追加</button>
              </form>
            </details>
          )}
          {tab === "candidates" && (
            <div className="candidate-controls">
              <details className="candidate-overview">
                <summary>保存済みの分析結果・集計 <span>日足 {candidateItems[0]?.as_of_date ?? "未生成"}</span></summary>
                <div className="candidate-heading">
                  <span>日足 {candidateItems[0]?.as_of_date ?? "未生成"}</span>
                  <button type="button" className="list-tool-button" disabled={refreshing}
                    onClick={() => startRefresh(() => router.refresh())}>
                    <span aria-hidden="true">↻</span> {refreshing ? "再読み込み中…" : "一覧を再読み込み"}
                  </button>
                </div>
                <p className="candidate-refresh-note" role="status">
                  {refreshing ? "保存済みの最新一覧を読み込んでいます" : "保存済みの分析結果を表示"}
                </p>
                <dl className="candidate-summary" aria-label="絞り込み前の候補件数">
                  <div><dt>テクニカル通過</dt><dd>{candidateItems.filter((item) => item.technical_action === "buy_candidate").length}<small>件</small></dd></div>
                  <div className="candidate-summary-buy"><dt>購入候補</dt><dd>{candidateItems.filter((item) => item.action === "buy_candidate").length}<small>件</small></dd></div>
                  {Object.entries({ data_pending: "データ取得待ち", metrics_missing: "算出に必要な項目不足", review_required: "比較・内容確認が必要" }).map(([status, label]) => <div key={status}><dt>{label}</dt><dd>{candidateItems.filter((item) => item.screening_status === status).length}<small>件</small></dd></div>)}
                  <div><dt>リスク保留</dt><dd>{candidateItems.filter((item) => item.screening_status === "risk_hold").length}<small>件</small></dd></div>
                </dl>
                {candidateItems.length > 0 && !candidateItems.some((item) => item.action === "buy_candidate") && (
                  <details className="candidate-refresh-note">
                    <summary>購入候補0件の理由（{candidateItems.length}銘柄を分析済み）</summary>
                    <p>表示日の条件を満たす銘柄がありません。過去日の候補は含めていません。</p>
                    <ul>{candidateHoldReasons.map(([reason, count]) => <li key={reason}>{reason}：{count}件</li>)}</ul>
                  </details>
                )}
              </details>
              {candidateItems.some((item) => item.screening_status === "needs_reanalysis") && <p className="candidate-refresh-note">再分析待ちの銘柄は購入候補から除外しています。再分析が完了すると一覧に反映されます。</p>}
              <div className="candidate-horizon">
                <span>候補の分析軸</span>
                <div role="group" aria-label="分析候補の運用スタイル">
                  <button type="button" aria-pressed={candidateHorizon === 5} onClick={() => switchCandidateHorizon(5)}>スイング</button>
                  <button type="button" aria-pressed={candidateHorizon === 20} onClick={() => switchCandidateHorizon(20)}>中長期</button>
                </div>
              </div>
              <div className="candidate-filters" role="group" aria-label="投資検討区分">
                {(["all", "buy_candidate", "watch", "avoid_new_buy"] as const).map((action) => (
                  <button type="button" key={action} aria-pressed={candidateAction === action} onClick={() => setCandidateAction(action)}>{action === "all" ? "すべて" : actionLabels[action]}</button>
                ))}
              </div>
            </div>
          )}

          <div className="sidebar-search-tools">
            <label className="sidebar-search"><span>銘柄を検索</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="証券コード・銘柄名・分類" /></label>
            <details className="list-filter-panel">
              <summary>
                <span>絞り込み</span>
                {activeFilterCount > 0 && <strong>{activeFilterCount}</strong>}
                <small>{activeFilterCount ? "適用中" : "条件を指定"}</small>
              </summary>
              <div className="list-filter-grid">
                <label><span>市場区分</span><select value={listFilters.market} onChange={(event) => setListFilters((current) => ({ ...current, market: event.target.value }))}><option value="all">すべて</option>{filterOptions.markets.map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
                <label><span>商品種別</span><select value={listFilters.instrumentType} onChange={(event) => setListFilters((current) => ({ ...current, instrumentType: event.target.value }))}><option value="all">すべて</option>{filterOptions.instrumentTypes.map((value) => <option key={value} value={value}>{instrumentTypeLabels[value] ?? value}</option>)}</select></label>
                <label><span>TOPIX-17</span><select value={listFilters.sector17} onChange={(event) => setListFilters((current) => ({ ...current, sector17: event.target.value }))}><option value="all">すべて</option>{filterOptions.sectors17.map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
                <label><span>東証33業種</span><select value={listFilters.sector33} onChange={(event) => setListFilters((current) => ({ ...current, sector33: event.target.value }))}><option value="all">すべて</option>{filterOptions.sectors33.map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
                <label><span>決算予定</span><select value={listFilters.earnings} onChange={(event) => setListFilters((current) => ({ ...current, earnings: event.target.value as ListFilters["earnings"] }))}><option value="all">すべて</option><option value="within7">7日以内</option><option value="within30">30日以内</option><option value="scheduled">予定あり</option><option value="unscheduled">未定</option></select></label>
                <label><span>流動性</span><select value={listFilters.liquidity} onChange={(event) => setListFilters((current) => ({ ...current, liquidity: event.target.value }))}><option value="all">すべて</option>{(["very_high", "high", "medium", "low", "unknown"] as const).map((value) => <option key={value} value={value}>{liquidityLabels[value]}</option>)}</select></label>
                <label><span>データ鮮度</span><select value={listFilters.freshness} onChange={(event) => setListFilters((current) => ({ ...current, freshness: event.target.value }))}><option value="all">すべて</option>{(["fresh", "stale", "missing"] as const).map((value) => <option key={value} value={value}>{freshnessLabels[value]}</option>)}</select></label>
              </div>
            </details>
            <div className="list-results-toolbar">
              <span role="status">表示 <strong>{filteredItems.length}</strong> / {filterSource.length}件</span>
              <label className="list-sort-control"><span>並び順</span><select value={listSort} onChange={(event) => setListSort(event.target.value as ListSort)}>
                <option value="symbol">証券コード順</option>
                <option value="capital_stock">資本金が大きい順</option>
                <option value="employees">従業員数が多い順</option>
                <option value="sales">売上高が大きい順</option>
                <option value="market_cap">時価総額が大きい順</option>
              </select></label>
              <button type="button" className="list-tool-button" disabled={!hasListConstraints}
                onClick={clearListConstraints}>絞り込みを解除</button>
            </div>
          </div>
          {pageCount > 1 && <nav className="list-results-toolbar" aria-label="銘柄一覧のページ切り替え">
            <button type="button" className="list-tool-button" disabled={currentPage === 0}
              onClick={() => setPagination({ key: pageKey, page: currentPage - 1 })}>前へ</button>
            <span role="status">{currentPage + 1} / {pageCount}ページ（100件ずつ）</span>
            <button type="button" className="list-tool-button" disabled={currentPage + 1 >= pageCount}
              onClick={() => setPagination({ key: pageKey, page: currentPage + 1 })}>次へ</button>
          </nav>}
          {sidebarMessage && <p className="sidebar-message" aria-live="polite">{sidebarMessage}</p>}
          <div className={`instrument-list${tab === "candidates" ? " candidate-list" : ""}`}>
            {visibleItems.map((item) => {
              const watched = watchedSymbols.has(`${item.provider}:${item.symbol}`);
              return (
                <div className="instrument-row" key={`${item.provider}-${item.symbol}`}>
                  <button type="button" aria-current={selected?.symbol === item.symbol && selected?.provider === item.provider ? "true" : undefined} title={item.judgment_reason ?? undefined} className={selected?.symbol === item.symbol ? "instrument active" : "instrument"} onClick={() => selectNavigationItem(item)}>
                    <span className="instrument-copy">
                      <span className="instrument-symbol-line">
                        <strong>{item.symbol}</strong>
                      </span>
                      <span className="instrument-name">{item.display_name}</span>
                      {tab !== "candidates" && (
                      <span className="instrument-tags">
                        {tab === "positions" && item.quantity && (
                          <em title={item.average_cost ? `平均取得単価 ${Number(item.average_cost).toLocaleString("ja-JP")}円` : "平均取得単価は未登録"}>
                            {item.account_type ?? "口座未設定"}・{Number(item.quantity).toLocaleString("ja-JP")}株
                          </em>
                        )}
                        <em title="市場区分">{item.market ?? "市場 未分類"}</em>
                        <em title="商品種別">{item.instrument_type ? instrumentTypeLabels[item.instrument_type] ?? item.instrument_type : "種別 未分類"}</em>
                        <em title={`TOPIX-17分類 ${item.sector_17_code ?? ""}`.trim()}>17: {item.sector_17_name ?? "未分類"}</em>
                        <em title={`東証33業種 ${item.sector_33_code ?? ""}`.trim()}>33: {item.sector_33_name ?? "未分類"}</em>
                        <em className={item.days_to_earnings != null && item.days_to_earnings <= 7 ? "tag-warning" : ""} title={item.next_earnings_date ? `次回決算予定 ${item.next_earnings_date}` : "取得できる将来の決算予定はありません"}>{earningsTag(item)}</em>
                        <em className={item.liquidity_rank === "low" ? "tag-caution" : ""} title={item.median_turnover ? `直近60日売買代金中央値 ${Number(item.median_turnover).toLocaleString("ja-JP")}円` : "直近の分析結果に基づく流動性区分"}>{liquidityLabels[item.liquidity_rank ?? "unknown"]}</em>
                        <em className={item.freshness_status === "fresh" ? "tag-fresh" : "tag-caution"} title={item.latest_trade_date ? `最新日足 ${item.latest_trade_date}` : "日足データがありません"}>{freshnessTag(item)}</em>
                        {item.capital_stock != null && <em title="資本金">資本 {Number(item.capital_stock).toLocaleString("ja-JP")}</em>}
                        {item.employees != null && <em title="従業員数">従業員 {item.employees.toLocaleString("ja-JP")}人</em>}
                        {item.sales != null && <em title="売上高">売上 {Number(item.sales).toLocaleString("ja-JP")}</em>}
                        {item.market_cap != null && <em title="時価総額">時価総額 {Number(item.market_cap).toLocaleString("ja-JP")}</em>}
                      </span>
                      )}
                      {tab === "candidates" && (item.capital_stock != null || item.employees != null || item.sales != null || item.market_cap != null) && (
                        <span className="instrument-tags">
                          {item.capital_stock != null && <em title="資本金">資本 {Number(item.capital_stock).toLocaleString("ja-JP")}</em>}
                          {item.employees != null && <em title="従業員数">従業員 {item.employees.toLocaleString("ja-JP")}人</em>}
                          {item.sales != null && <em title="売上高">売上 {Number(item.sales).toLocaleString("ja-JP")}</em>}
                          {item.market_cap != null && <em title="時価総額">時価総額 {Number(item.market_cap).toLocaleString("ja-JP")}</em>}
                        </span>
                      )}
                    </span>
                    <span className="instrument-status">
                    {tab !== "candidates" && ["data_pending", "metrics_missing", "review_required"].includes(item.screening_status ?? "") && <small className="pending-label">{{ data_pending: "データ取得待ち", metrics_missing: "算出に必要な項目不足", review_required: "比較・内容確認が必要" }[item.screening_status as string]}</small>}
                    {item.action && <small className={`action-${item.action}`}>{actionLabels[item.action]}{item.action === "watch" && item.entry_stage ? `・${item.screening_status === "needs_reanalysis" ? "再分析待ち" : entryStageLabels[item.entry_stage] ?? item.entry_stage}` : ""}</small>}
                    </span>
                  </button>
                  <button type="button" className={`row-action${watched ? " is-added" : ""}`} aria-label={`${item.symbol}をウォッチへ追加`} title={watched ? "ウォッチ登録済み" : "ウォッチへ追加"} disabled={watched || mutating} onClick={() => addCandidateToWatchlist(item)}>{watched ? "✓" : "+"}</button>
                  {tab === "watchlist" && <button type="button" className="row-action destructive" aria-label={`${item.symbol}をウォッチから削除`} disabled={mutating} onClick={() => removeItem(item, "watchlist")}>×</button>}
                  {tab === "positions" && <button type="button" className="row-action destructive" aria-label={`${item.symbol}を保有から削除`} disabled={mutating} onClick={() => removeItem(item, "positions")}>×</button>}
                </div>
              );
            })}
            {!filteredItems.length && <div className="empty-list">
              <strong>{!filterSource.length ? "一覧はまだありません" : "条件に合う銘柄はありません"}</strong>
              <p>{!filterSource.length
                ? tab === "candidates" ? "データ更新後に一覧を再読み込みしてください。"
                  : tab === "positions" ? "上の登録欄から保有銘柄を追加できます。" : "上の登録欄や分析候補から銘柄を追加できます。"
                : "検索・絞り込みを解除すると、ほかの区分の銘柄も確認できます。"}</p>
              {hasListConstraints && <button type="button" className="list-tool-button" onClick={clearListConstraints}>すべての銘柄を表示</button>}
            </div>}
          </div>
        </div>
      </aside>

      <main ref={mainPanelRef} className="main-panel">
        <section className="plan-strip" aria-label="データ契約状況">
          <div><span>データプラン</span><strong>J-Quants {data.plan}</strong></div>
          {data.capabilities.slice(0, 4).map((capability) => (
            <div key={capability.key} title={capability.message}><span>{capability.label}</span><strong className={`status-${capability.status}`}>{capability.status === "ready" || capability.status === "enabled" ? "利用可" : "確認"}</strong></div>
          ))}
        </section>

        <section className={`market-environment-banner regime-${data.marketEnvironment.regime}`}>
          <div className="market-environment-summary">
            <span className="eyebrow">寄り付き前の外部環境</span>
            <strong>{data.marketEnvironment.regime_label}</strong>
            {data.marketEnvironment.risk_score != null && (
              <small>
                警戒スコア {data.marketEnvironment.risk_score.toFixed(0)} / 100
                ・取得率 {Math.round((data.marketEnvironment.coverage_ratio ?? 0) * 100)}%
              </small>
            )}
          </div>
          <div className="market-environment-reasons">
            {data.marketEnvironment.reasons.slice(0, 3).map((reason) => (
              <span key={reason}>{reason}</span>
            ))}
            {!data.marketEnvironment.reasons.length && (
              <span>{data.marketEnvironment.message}</span>
            )}
          </div>
          <div className="market-environment-meta">
            <span>{data.marketEnvironment.decision_date ?? "未取得"}</span>
            <button
              type="button"
              className="help-button"
              title={[
                data.marketEnvironment.message,
                ...data.marketEnvironment.cautions,
                "テクニカル方向とは別の新規購入リスクです。",
              ].join("\n")}
              aria-label="外部環境判定の説明"
            >?</button>
          </div>
        </section>

        <section className="instrument-header">
          <div className="instrument-identity">
            <span className="eyebrow">選択銘柄</span><h2>{selected ? `${selected.symbol} ${selected.display_name}` : "銘柄を選択してください"}</h2>
            {selected && (
              <span className="instrument-tags">
                {selected.quantity && (
                  <em title={selected.average_cost ? `平均取得単価 ${Number(selected.average_cost).toLocaleString("ja-JP")}円` : "平均取得単価は未登録"}>
                    {selected.account_type ?? "口座未設定"}・{Number(selected.quantity).toLocaleString("ja-JP")}株
                  </em>
                )}
                <em title="市場区分">{selected.market ?? "市場 未分類"}</em>
                <em title="商品種別">{selected.instrument_type ? instrumentTypeLabels[selected.instrument_type] ?? selected.instrument_type : "種別 未分類"}</em>
                <em title={`TOPIX-17分類 ${selected.sector_17_code ?? ""}`.trim()}>17: {selected.sector_17_name ?? "未分類"}</em>
                <em title={`東証33業種 ${selected.sector_33_code ?? ""}`.trim()}>33: {selected.sector_33_name ?? "未分類"}</em>
                <em className={selected.days_to_earnings != null && selected.days_to_earnings <= 7 ? "tag-warning" : ""} title={selected.next_earnings_date ? `次回決算予定 ${selected.next_earnings_date}` : "取得できる将来の決算予定はありません"}>{earningsTag(selected)}</em>
                <em className={selected.liquidity_rank === "low" ? "tag-caution" : ""} title={selected.median_turnover ? `直近60日売買代金中央値 ${Number(selected.median_turnover).toLocaleString("ja-JP")}円` : "直近の分析結果に基づく流動性区分"}>{liquidityLabels[selected.liquidity_rank ?? "unknown"]}</em>
                <em className={selected.freshness_status === "fresh" ? "tag-fresh" : "tag-caution"} title={selected.latest_trade_date ? `最新日足 ${selected.latest_trade_date}` : "日足データがありません"}>{freshnessTag(selected)}</em>
              </span>
            )}
            {selected?.screening_status && ["data_pending", "metrics_missing", "review_required"].includes(selected.screening_status) && <p>{{ data_pending: "データ取得待ち", metrics_missing: "算出に必要な項目不足", review_required: "比較・内容確認が必要" }[selected.screening_status]}</p>}
            {bars && <p>{bars.range.from}〜{bars.range.to}・{bars.source.is_adjusted ? "調整済み" : "未調整"}</p>}
          </div>
          <div className="latest-price"><span>最新終値</span><strong>{latestBar ? `${Number(latestBar.close).toLocaleString("ja-JP", { maximumFractionDigits: 2 })}円` : "—"}</strong><small>{bars?.freshness.latest_trade_date ?? "データ未読込"}</small></div>
          <div className="analysis-context">
            <div className="analysis-context-label"><span>分析スタイル</span><button type="button" className="help-button" title={horizonProfile.caution} aria-label={`分析スタイルの注意: ${horizonProfile.caution}`}>?</button></div>
            <div className="horizon-switch" role="group" aria-label="運用スタイル">
              <button type="button" aria-pressed={horizon === 5} className={horizon === 5 ? "active" : ""} onClick={() => switchDetailHorizon(5)}><strong>スイング</strong><span>5営業日先</span></button>
              <button type="button" aria-pressed={horizon === 20} className={horizon === 20 ? "active" : ""} onClick={() => switchDetailHorizon(20)}><strong>中長期の買い場</strong><span>20営業日先</span></button>
            </div>
            <div className="analysis-context-copy"><strong>{horizonProfile.purpose}</strong><span>{horizonProfile.holding}</span></div>
          </div>
        </section>

        {selected && tab === "positions" && <PositionJournal key={`${selected.provider}-${selected.symbol}-${data.positions.map((p) => `${p.symbol}:${p.quantity}:${p.average_cost}`).join(",")}`} symbol={selected.symbol} provider={selected.provider} />}
        {loading && <div className="loading-card">日足と分析結果を読み込んでいます…</div>}
        {error && <div className="error-card">{error}</div>}
        {!loading && !error && bars && analysis && (
          <>
            <section className="chart-card">
              <div className="section-heading chart-heading"><div><span className="eyebrow">価格と需給</span><h3>日足・出来高</h3></div><p>{customRange ? "期間指定" : rangeLabels[chartRange]}・{horizon === 5 ? "スイング基準" : "中長期基準"}</p></div>
              <div className="chart-controls">
                <div className="range-switch" role="group" aria-label="表示期間">
                  {(Object.keys(rangeLabels) as ChartRange[]).map((range) => <button type="button" key={range} className={!customRange && chartRange === range ? "active" : ""} onClick={() => switchChartRange(range)}>{rangeLabels[range]}</button>)}
                </div>
                <div className="indicator-switches" aria-label="テクニカル指標">
                  {([["movingAverage", "移動平均線"], ["rsi", "RSI"], ["resistance", "抵抗帯"], ["support", "支持帯"]] as const).map(([key, label]) => <label key={key}><input type="checkbox" checked={indicators[key]} onChange={(event) => setIndicators((current) => ({ ...current, [key]: event.target.checked }))} />{label}</label>)}
                </div>
                <details className="custom-range-control"><summary>日付を指定</summary><form onSubmit={applyCustomRange}><label>開始日<input type="date" name="from" defaultValue={bars.range.from} /></label><label>終了日<input type="date" name="to" defaultValue={bars.range.to} /></label><button type="submit">適用</button></form></details>
              </div>
              <PriceChart payload={bars} horizon={horizon} indicators={indicators} />
            </section>

            <section className="decision-grid">
              <article className={`decision-card action-${decision?.action ?? "insufficient_data"}`}>
                <span className="eyebrow">投資検討区分</span><strong>{decision ? actionLabels[decision.action] : "判定不能"}</strong><p>{decision?.summary ?? "投資検討区分を生成できませんでした"}</p><small>根拠の強さ {decision?.evidence_score.toFixed(1) ?? "—"}・確率ではありません</small>
                {analysis.research?.financials.status === "caution" && <div className="decision-warning"><strong>財務注意</strong><ul>{analysis.research.financials.cautions.map((caution) => <li key={caution}>{caution}</li>)}</ul></div>}
                {!analysis.equity_checks.some((check) => check.key === "days_to_earnings" && check.value != null) && <p className="decision-warning">決算予定未取得：分析期間内に決算がないことを確認できていません。</p>}
                {analysis.equity_checks.filter((check) => check.key === "upper_resistance_confirmation").map((check) => <p className="decision-warning" key={check.key}>{check.description}</p>)}
                {!!decision?.cautions.length && <ul className="compact-notes">{decision.cautions.slice(0, 3).map((caution) => <li key={caution}>{caution}</li>)}</ul>}
              </article>
              <article className={`entry-stage-card entry-${decision?.entry_stage ?? "not_applicable"}`}>
                <span className="eyebrow">上抜け検出と購入条件</span>
                <small>上抜け検出：{analysis.patterns.some((pattern) => pattern.direction === "up") ? "検出済み（有効性は下の条件を確認）" : "未検出"}</small>
                <small>購入条件：{decision?.action === "buy_candidate" && decision.entry_stage === "entry_ready" ? "充足（分析ルール上）" : decision?.action === "watch" ? "確認待ち" : "未充足・判定対象外"}</small>
                <strong>{analysis.equity_checks.some((check) => check.key === "upper_resistance_confirmation") && decision?.action === "watch" ? "上位抵抗帯の突破確認待ち" : decision?.entry_stage_label ?? "未評価"}</strong>
                <small>{decision?.entry_strategy ? entryStrategyLabels[decision.entry_strategy] ?? "判定経路未確認" : "判定経路未算出"}</small>
                <div className="execution-metrics">
                  <span>形の強さ <b>{decision?.setup_score?.toFixed(1) ?? "—"}</b></span>
                  <span title="技術条件の参考スコアです。抵抗帯・財務・イベントなどの追加確認を含む購入条件の充足率ではありません。">技術条件スコア <b>{decision?.entry_score?.toFixed(1) ?? "—"}</b></span>
                </div>
                <small>上昇形状と「今買う条件」を別々に評価しています。</small>
                <EntryDiagnosticsView diagnostics={analysis.entry_diagnostics} asOfDate={analysis.as_of_date} targetRiskReward={decision?.execution_risk_reward_ratio} targetPrice={decision?.expected_target_price} />
              </article>
              <article className={`direction-card direction-${analysis.direction}`}>
                <span className="eyebrow">現在のテクニカル方向</span><strong>{directionLabels[analysis.direction]}</strong><div className="score-row">{(["up", "flat", "down"] as const).map((direction) => <span key={direction}>{directionLabels[direction]} {analysis.scores[direction].toFixed(1)}</span>)}</div><small>価格の向きであり、投資検討区分とは異なります・{analysis.as_of_date}</small>
              </article>
            </section>

            <ResearchPanel research={analysis.research} />

            {stage && (
              <section className="stage-card">
                <div className="section-heading"><div><span className="eyebrow">条件進捗</span><h3>{stage.phase_label}</h3></div><strong>{stage.satisfied_conditions} / {stage.total_conditions}</strong></div><p>{stage.summary}</p>
                <ul className="condition-list">{stage.conditions.map((condition) => <li key={condition.key} className={condition.satisfied ? "satisfied" : "pending"}><span aria-hidden="true">{condition.satisfied ? "✓" : "○"}</span><div><strong>{condition.label}</strong><p>{condition.description}</p></div></li>)}</ul>

              </section>
            )}

            {analysis.swing_pullback && (
              <section className="stage-card">
                <div className="section-heading"><div><span className="eyebrow">ブレイクとは独立した確認</span><h3>スイングの押し目反発</h3></div><strong>{analysis.swing_pullback.satisfied_conditions} / {analysis.swing_pullback.total_conditions}</strong></div>
                <p>{analysis.swing_pullback.summary}</p>
                <p>形の確認後も、抵抗までのR/R・地合い・決算・現在足の窓開けを確認します。達成率は上昇確率ではありません。</p>
                <ul className="condition-list">{analysis.swing_pullback.conditions.map((condition) => <li key={condition.key} className={condition.satisfied ? "satisfied" : "pending"}><span aria-hidden="true">{condition.satisfied ? "✓" : "○"}</span><div><strong>{condition.label}</strong><p>{condition.description}</p></div></li>)}</ul>
              </section>
            )}

            <section className="factor-card">
              <div className="section-heading"><div><span className="eyebrow">説明可能性</span><h3>主な判定要因</h3></div><p>上位{Math.min(6, analysis.factors.length)}件</p></div>
              <div className="factor-grid">{analysis.factors.slice(0, 6).map((factor) => <article key={`${factor.name}-${factor.direction}`} className={`direction-${factor.direction}`}><span>{directionLabels[factor.direction]} +{factor.score}</span><strong>{factor.name}</strong><p>{factor.description}</p></article>)}</div>
            </section>

            <section className="detail-grid">
              <article className="detail-card">
                <div className="section-heading"><div><span className="eyebrow">形の検出</span><h3>チャートパターン</h3></div></div>
                {analysis.patterns.length ? <ul className="detail-list">{analysis.patterns.slice(0, 4).map((pattern) => <li key={`${pattern.type}-${pattern.name}`}><div><strong>{pattern.name}</strong><span>一致度 {pattern.fit_score.toFixed(1)}</span></div><p>{pattern.lifecycle?.summary ?? pattern.description}</p></li>)}</ul> : <p className="empty-detail">有効な完成パターンはありません。</p>}
              </article>
              <article className="detail-card">
                <div className="section-heading"><div><span className="eyebrow">個別株固有</span><h3>確認事項</h3></div></div>
                <ul className="detail-list">{analysis.equity_checks.slice(0, 6).map((check) => <li key={check.key}><div><strong>{check.label}</strong><span className={`check-${check.status}`}>{check.value == null ? check.status : `${check.value}${check.unit ?? ""}`}</span></div><p>{check.description}</p></li>)}</ul>
              </article>
            </section>

            <section className="ai-review-card">
              <div className="section-heading"><div><span className="eyebrow">最新情報との照合</span><h3>AI最終確認</h3></div><span className={`ai-status ${data.aiCapability.enabled ? "ready" : "disabled"}`}>{data.aiCapability.enabled ? "利用可能" : "APIキー未設定"}</span></div>
              <p>ルールベース分析を起点に、最新の公式発表や関連報道を検索して矛盾とリスクを整理します。</p>
              <div className="ai-action"><small>{selected?.symbol}・{horizonProfile.label}・{data.aiCapability.model}</small><button type="button" disabled={!data.aiCapability.enabled || aiLoading} onClick={runAiReview}>{aiLoading ? "検索・照合しています…" : "最新情報を検索して確認"}</button></div>
              {!data.aiCapability.enabled && <p className="ai-note">`.env`へOPENAI_API_KEYを設定すると有効になります。</p>}
              {aiError && <p className="error-message">{aiError}</p>}
              {aiReview && <div className="ai-report">{aiReview.report_segments.map((segment, index) => segment.citation ? <a key={`${segment.text}-${index}`} href={segment.citation.url} target="_blank" rel="noreferrer">{segment.text}</a> : <span key={`${segment.text}-${index}`}>{segment.text}</span>)}</div>}
            </section>
          </>
        )}
      </main>
    </div>
  );
}
