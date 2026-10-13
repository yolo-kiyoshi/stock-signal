"use client";

import { useEffect, useState, type FormEvent } from "react";
import type { ResearchAssessment } from "@/types/api";

const labels: Record<string, string> = {
  total_capital: "運用総資産（保有評価額＋現金・円）", cash_balance: "現金残高（円）",
  risk_budget: "1回の許容損失（円）", max_symbol_weight: "1銘柄の上限比率（0〜1）",
  max_sector_weight: "1業種の上限比率（0〜1）", cost_bps: "片道コスト（bp・手数料＋滑り）",
  lot_size: "売買単位（株・対象銘柄に合わせて設定）",
};
const metricLabels: Record<string, string> = {
  sales_yoy_percent: "売上高 前年同期比（%）", op_yoy_percent: "営業利益 前年同期比（%）",
  profit_yoy_percent: "純利益 前年同期比（%）", eps_yoy_percent: "EPS 前年同期比（%）",
  operating_margin_percent: "営業利益率（%）", margin_change_pp: "営業利益率変化（ポイント）",
  equity_ratio_percent: "自己資本比率（%）", roe_percent: "開示ROE（%）",
  operating_cash_flow: "営業キャッシュフロー（円）", cash_equivalents: "現金同等物（円）",
  forecast_per: "会社予想PER（倍）", pbr: "PBR（倍）",
  forecast_op_revision_percent: "営業利益予想 修正率（%）", forecast_fiscal_year_end: "会社予想の年度末",
};
const statusLabels: Record<string, string> = {
  missing: "未取得・未設定", partial: "一部確認", stale: "更新確認が必要", caution: "警戒",
  reviewed: "数値確認済み", hold: "保留", unknown: "未確認", calculated: "算出済み",
  data_pending: "データ取得待ち", metrics_missing: "算出に必要な項目不足", review_required: "比較・内容確認が必要",
};

export function ResearchPanel({ research }: { research?: ResearchAssessment | null }) {
  const [settings, setSettings] = useState<Record<string, string>>({});
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    let active = true;
    fetch("/api/backend/portfolio/risk-settings").then(async (response) => {
      if (!response.ok) throw new Error("資金設定を取得できませんでした");
      const payload = await response.json();
      if (active) setSettings(payload.settings ?? {});
    }).catch((error) => { if (active) setMessage(error.message); });
    return () => { active = false; };
  }, []);
  async function save(event: FormEvent) {
    event.preventDefault(); setSaving(true); setMessage("");
    try {
      const response = await fetch("/api/backend/portfolio/risk-settings", {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(settings),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ?? "保存に失敗しました");
      window.location.reload();
    } catch (error) { setMessage(error instanceof Error ? error.message : "保存に失敗しました"); }
    finally { setSaving(false); }
  }
  const finance = research?.financials;
  const portfolio = research?.portfolio;
  return <section className="stage-card" aria-label="財務と保有リスク">
    <div className="section-heading"><h3>企業・イベント・保有リスク</h3><span>Lightデータによる分析情報</span></div>
    <div className="detail-grid">
      <article className="detail-card">
        <h4>企業品質・業績・評価倍率：{statusLabels[finance?.status ?? "missing"]}</h4>
        <p>採用した決算開示日：{finance?.disclosed_date ?? "未取得"}／分析日：{finance?.as_of_date ?? "—"}</p>
        {finance?.issues?.map((issue) => <p key={`${issue.category}:${issue.message}`}><strong>{statusLabels[issue.category] ?? issue.category}：</strong>{issue.message}</p>)}
        <dl className="research-metrics">{Object.entries(finance?.metrics ?? {}).map(([key, value]) =>
          <div key={key}><dt>{metricLabels[key] ?? key}</dt><dd>{value == null ? "未算出" : typeof value === "number" ? value.toLocaleString("ja-JP", { maximumFractionDigits: 2 }) : value}</dd></div>)}</dl>
        <h4>開示イベント：{statusLabels[finance?.event_status ?? "unknown"]}</h4>
        {[...(finance?.events ?? []), ...(finance?.cautions ?? [])].map((text) => <p key={text}>{text}</p>)}
      </article>
      <article className="detail-card">
        <h4>資金と集中度を反映した分析上限</h4>
        <strong>{portfolio?.max_shares == null ? "未算出" : `${portfolio.max_shares.toLocaleString("ja-JP")}株`}</strong>
        <dl className="research-metrics">{Object.entries(portfolio?.limits ?? {}).map(([key, value]) => <div key={key}><dt>{{ cash: "現金制約", risk: "許容損失制約", symbol: "銘柄集中制約", sector: "業種集中制約" }[key] ?? key}</dt><dd>{value.toLocaleString("ja-JP")}株</dd></div>)}</dl>
        <p>対TOPIXベータ：{portfolio?.topix_risk.beta?.toFixed(2) ?? "未算出"}（共通リターン{portfolio?.topix_risk.observations ?? 0}組）</p>
        {portfolio?.correlations.map((row) => <p key={row.symbol}>{row.symbol}との相関：{row.correlation?.toFixed(2) ?? "未算出"}（{row.observations}組）</p>)}
        {portfolio?.cautions.map((text) => <p key={text}>{text}</p>)}
        <details><summary>資金・許容損失・上限比率を設定</summary>
          <p>現在の保有登録と同じ範囲の資産を入力してください。相関は参考表示で、株数の倍率には換算しません。</p>
          <form onSubmit={save} className="research-settings">{Object.entries(labels).map(([key, label]) => <label key={key}>{label}<input type="number" min="0" step="any" required value={settings[key] ?? ""} onChange={(event) => setSettings({ ...settings, [key]: event.target.value })} /></label>)}<button disabled={saving} type="submit">保存して再分析</button></form>
        </details>
        {message && <p role="status">{message}</p>}
      </article>
    </div>
  </section>;
}
