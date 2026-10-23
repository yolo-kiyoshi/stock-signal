"use client";

import { useEffect, useState } from "react";
import type { FormEvent } from "react";

type ThesisPayload = {
  story: string;
  horizon: string;
  indicators: string;
  support_conditions: string;
  invalidation_conditions: string;
  position_constraints: string;
  change_reason: string;
  investment_date: string | null;
};
type Thesis = { id: string; version: number; created_at: string; payload: ThesisPayload };
type Review = {
  id: string;
  created_at: string;
  needs_review: boolean;
  context_as_of: string;
  holding: { quantity: string; average_cost: string | null };
  thesis_version: number | null;
  payload: {
    decision: "increase" | "reduce" | "hold" | "defer";
    thesis_assessment: "strengthened" | "unchanged" | "weakened" | "unknown";
    summary: string;
    changes_since_previous: string;
    supporting_facts: string;
    opposing_facts: string;
    price_and_position: string;
    next_conditions: string;
    unknowns: string;
    evidence: { title: string; url: string; published_date: string | null; event_date: string | null; checked_at: string; finding: string }[];
  };
};
type Journal = { theses: Thesis[]; reviews: Review[] };
const decisionLabels = { increase: "増額検討", reduce: "縮小検討", hold: "維持", defer: "判断保留" };
const assessmentLabels = { strengthened: "強まった", unchanged: "変わらない", weakened: "弱まった", unknown: "未確認" };
const thesisFields = [
  ["story", "投資ストーリー", "なぜこの企業が伸びると考えたか", true],
  ["horizon", "想定期間", "次の決算まで、1〜3年など", true],
  ["indicators", "確認する指標", "売上成長、利益率、受注など", false],
  ["support_conditions", "ストーリーを支持する条件", "何が確認できれば想定どおりか", false],
  ["invalidation_conditions", "ストーリーが崩れる条件", "何が起きたら見直すか", false],
  ["position_constraints", "ポジションの制約", "銘柄上限比率や追加資金など。未設定なら空欄", false],
  ["change_reason", "記録・更新の理由", "初回登録の経緯、前回から考えを変えた理由", true],
] as const;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/backend/portfolio/${path}`, { cache: "no-store", ...init });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(typeof data.detail === "string" ? data.detail : "入力内容を確認してください");
  }
  return data as T;
}

export function PositionJournal({ symbol, provider }: { symbol: string; provider: string }) {
  const [data, setData] = useState<Journal | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const base = `journal/${encodeURIComponent(symbol)}`;
  const query = `?provider=${encodeURIComponent(provider)}`;

  useEffect(() => {
    let active = true;
    request<Journal>(base + query).then((result) => {
      if (active) setData(result);
    }).catch((reason) => {
      if (active) setError(reason.message);
    });
    return () => { active = false; };
  }, [base, query]);

  async function refresh() {
    setData(await request<Journal>(base + query));
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const payload = Object.fromEntries(form.entries());
    setBusy(true); setError(""); setMessage("");
    try {
      await request(`${base}/theses${query}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...payload, investment_date: payload.investment_date || null,
          expected_version: data?.theses[0]?.version ?? 0 }),
      });
      await refresh();
      setMessage("投資ストーリーを新しい版として保存しました。");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "保存に失敗しました"); }
    finally { setBusy(false); }
  }

  async function download() {
    setBusy(true); setError(""); setMessage("");
    try {
      const context = await request<{ context_id: string }>(`${base}/context${query}`, { method: "POST" });
      const url = URL.createObjectURL(new Blob([JSON.stringify(context, null, 2)], { type: "application/json" }));
      const link = document.createElement("a");
      link.href = url; link.download = `position-${symbol}-${context.context_id}.json`;
      link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
      setMessage("資料を保存しました。Codexでこの資料を使い、最新情報を調査するよう依頼してください。");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "資料生成に失敗しました"); }
    finally { setBusy(false); }
  }

  const latest = data?.theses[0];
  return (
    <section className="position-journal stage-card">
      <div className="section-heading"><div><span className="eyebrow">保有銘柄の記録</span><h3>投資ストーリーとチャット分析</h3></div>
        <button type="button" disabled={busy} onClick={() => { setError(""); refresh().catch(() => setError("再読み込みに失敗しました")); }}>履歴を再読み込み</button>
      </div>
      <p>ストーリーを保存し、Codexに「保有銘柄を最新情報でレビューして」と依頼できます。資料作成ではLLM APIを呼びません。結果は投資助言ではなく分析情報です。</p>
      {error && <p role="alert" className="decision-warning">{error}</p>}
      {message && <p role="status">{message}</p>}
      {!data && !error && <p>記録を読み込んでいます…</p>}
      {data && <>
        <details>
          <summary>{latest ? `投資ストーリーを確認・更新（第${latest.version}版）` : "投資ストーリーを登録"}</summary>
          <form key={latest?.id ?? "new"} onSubmit={save} className="journal-form">
            {thesisFields.map(([name, label, placeholder, required]) => <label key={name}>{label}{required ? "（必須）" : ""}
              <textarea name={name} placeholder={placeholder} required={required} maxLength={10000} rows={name === "story" ? 4 : 2}
                defaultValue={name === "change_reason" ? "" : latest?.payload[name] ?? ""} />
            </label>)}
            <label>投資日（不明なら空欄）<input type="date" name="investment_date" defaultValue={latest?.payload.investment_date ?? ""} /></label>
            <button disabled={busy} type="submit">{busy ? "処理中…" : "新しい版として保存"}</button>
          </form>
        </details>
        {data.theses.length > 0 && <details><summary>投資ストーリーの履歴（{data.theses.length}版）</summary>
          {data.theses.map((thesis) => <article key={thesis.id} className="journal-history">
            <strong>第{thesis.version}版 · {new Date(thesis.created_at).toLocaleString("ja-JP")}</strong>
            {thesisFields.map(([name, label]) => <p key={name}><b>{label}：</b>{thesis.payload[name] || "未登録"}</p>)}
            <p>投資日：{thesis.payload.investment_date ?? "未確認"}</p>
          </article>)}
        </details>}
        <button type="button" disabled={busy} onClick={download}>Codex向け分析資料をダウンロード</button>
        <p className="import-note">基準日時点の保有情報を含みます。Codexから資料を直接生成することもできます。</p>
        <h4>保存済みレビュー</h4>
        {data.reviews.length === 0 && <p>レビューは未生成です。分析区分や確率は表示しません。</p>}
        {data.reviews.map((review, index) => <details key={review.id} open={index === 0} className="journal-history">
          <summary>{decisionLabels[review.payload.decision]} · {new Date(review.created_at).toLocaleString("ja-JP")}{review.needs_review ? " · 再確認が必要" : ""}</summary>
          {review.needs_review && <p className="decision-warning">保有・ストーリー・資金設定または日付が変わっています。過去の分析として表示しています。</p>}
          <p>資料基準日：{review.context_as_of}／ストーリー：{assessmentLabels[review.payload.thesis_assessment]}</p>
          <p>参照時点の保有：{review.holding.quantity}株／平均取得価格：{review.holding.average_cost ?? "未登録"}円／ストーリー：{review.thesis_version ? `第${review.thesis_version}版` : "未登録"}</p>
          {([
            ["summary", "要約"], ["changes_since_previous", "前回からの変化"],
            ["supporting_facts", "支持材料"], ["opposing_facts", "反対材料"],
            ["price_and_position", "価格と保有比率の確認"], ["next_conditions", "次に確認する条件"],
            ["unknowns", "未確認事項"],
          ] as const).map(([key, label]) => <p key={key}><b>{label}：</b>{review.payload[key] || "記載なし"}</p>)}
          <ul>{review.payload.evidence.map((source, i) => <li key={i}>
            <a href={source.url} target="_blank" rel="noopener noreferrer">{source.title}</a>
            <p>{source.finding}</p><small>公表：{source.published_date ?? "未確認"}／出来事：{source.event_date ?? "未確認"}／確認：{new Date(source.checked_at).toLocaleString("ja-JP")}</small>
          </li>)}</ul>
        </details>)}
      </>}
    </section>
  );
}
