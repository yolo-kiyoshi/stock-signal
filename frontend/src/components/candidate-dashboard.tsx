"use client";

import { candidateLabels, candidateUniverse, matchesCandidate } from "@/lib/candidate-summary";
import type { CandidateAction } from "@/lib/candidate-summary";
import type { DashboardData } from "@/types/api";

const columns: CandidateAction[] = ["buy_candidate", "watch", "avoid_new_buy", "unassessed", "all"];
const checks: CandidateAction[] = ["financial_pending", "financial_caution", "market_only"];
export function CandidateDashboard({ data, selectedHorizon, selectedAction, onSelect, refreshing, onRefresh }: {
  data: DashboardData; selectedHorizon: 5 | 20 | null; selectedAction: CandidateAction;
  onSelect: (horizon: 5 | 20, action: CandidateAction) => void; refreshing: boolean; onRefresh: () => void;
}) {
  return <section className="candidate-dashboard" aria-label="投資検討区分のダッシュボード">
    <div className="candidate-dashboard-heading"><div><h2>投資検討区分</h2><p>分析対象全体・件数を選択すると該当銘柄を表示</p></div>
      <button type="button" className="list-tool-button" disabled={refreshing} onClick={onRefresh}>{refreshing ? "読み込み中…" : "保存済み結果を再読み込み"}</button>
    </div>
    <div className="candidate-dashboard-grid">
      <div className="candidate-dashboard-labels" aria-hidden="true"><span>分析軸</span>{columns.map(action => <span key={action}>{candidateLabels[action]}</span>)}</div>
      {([5, 20] as const).map(horizon => {
        const key = String(horizon) as "5" | "20";
        const items = candidateUniverse(data, horizon);
        const failed = data.candidateErrors?.[key];
        const unknownUniverse = !data.candidateUniverse;
        const generated = data.candidates[key].length > 0;
        const dates = [...new Set(data.candidates[key].map(item => item.as_of_date))].sort();
        const savedAt = data.candidateUniverse?.saved_at[key];
        const name = horizon === 5 ? "スイング" : "中期押し目";
        return <div className="candidate-dashboard-axis" key={horizon}>
          <div className="candidate-dashboard-row">
            <div className="candidate-axis-title"><strong>{name}</strong><small>{horizon}営業日先</small><small>基準日 {failed ? "取得失敗" : dates.length ? dates.join(" / ") : "未生成"}</small></div>
            {columns.map(action => {
              const unavailable = failed || (unknownUniverse && ["all", "unassessed"].includes(action));
              const notGenerated = !generated && !["all", "unassessed"].includes(action);
              return <button key={action} type="button" disabled={unavailable || notGenerated || refreshing}
                className={`candidate-count ${action === "buy_candidate" ? "candidate-count-buy" : ""}`}
                aria-pressed={selectedHorizon === horizon && selectedAction === action}
                aria-label={`${name}・${candidateLabels[action]}${unavailable ? "：取得失敗" : notGenerated ? "：未生成" : `：${items.filter(item => matchesCandidate(item, action)).length}銘柄`}`}
                onClick={() => onSelect(horizon, action)}>
                <span className="candidate-count-label">{candidateLabels[action]}</span>
                <strong>{unavailable ? "取得失敗" : notGenerated ? "未生成" : items.filter(item => matchesCandidate(item, action)).length.toLocaleString("ja-JP")}</strong>
                {!unavailable && !notGenerated && <small>銘柄</small>}
              </button>;
            })}
          </div>
          <details className="candidate-checks"><summary>確認が必要な銘柄・保存状態{failed ? "（取得失敗）" : `（再分析待ち ${items.filter(item => item.screening_status === "needs_reanalysis").length}銘柄）`}</summary>
            <p>最終保存日時：{savedAt ? new Date(savedAt).toLocaleString("ja-JP", { timeZone: "Asia/Tokyo" }) + "（日本時間）" : "未取得"}。再読み込みは分析の再実行ではありません。</p>
            <div>{checks.map(action => <button type="button" key={action} disabled={failed || !generated || refreshing} aria-pressed={selectedHorizon === horizon && selectedAction === action} onClick={() => onSelect(horizon, action)}>{candidateLabels[action]}：{failed ? "取得失敗" : !generated ? "未生成" : `${items.filter(item => matchesCandidate(item, action)).length}銘柄`}</button>)}</div>
            <p>確認状況には重複があります。投資検討区分の件数には加算しません。</p>
          </details>
        </div>;
      })}
    </div>
    <p className="candidate-dashboard-note">対象は有効な登録銘柄と最新分析に含まれる銘柄です。未判定は各軸の基準日の結果がない銘柄。両軸には同じ銘柄が含まれるため合算できません。件数は購入の推奨度ではありません。</p>
  </section>;
}
