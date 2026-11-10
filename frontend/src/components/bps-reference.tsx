"use client";

import { useState, type FormEvent } from "react";
import type { ResearchAssessment } from "@/types/api";

export function BpsReference({ reference, symbol }: {
  reference: NonNullable<ResearchAssessment["financials"]["bps_reference"]>;
  symbol: string;
}) {
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    await save("POST", {
      fingerprint: reference.fingerprint,
      source_disclosure_no: reference.source_disclosure_no,
      evidence_url: form.get("evidence_url"), notes: form.get("notes"),
      capital_checked: form.has("capital"), basis_checked: form.has("basis"),
      equity_checked: form.has("equity"),
    });
  }
  async function save(method: string, body?: object) {
    setSaving(true); setMessage("");
    try {
      const response = await fetch(`/api/backend/financials/${encodeURIComponent(symbol)}/bps-review`, {
        method, headers: { "Content-Type": "application/json" },
        body: body ? JSON.stringify(body) : undefined,
      });
      if (!response.ok) {
        const error = await response.json();
        throw new Error(typeof error.detail === "string" ? error.detail : "確認内容を見直してください");
      }
      window.location.reload();
    } catch (error) { setMessage(error instanceof Error ? error.message : "保存に失敗しました"); }
    finally { setSaving(false); }
  }
  return <section aria-label="BPSの参照元と資料確認">
    <h4>BPSの参照元と確認</h4>
    <p>{reference.reason}</p>
    <p>採用BPS：{reference.value == null ? "未採用" : `${reference.value.toLocaleString("ja-JP")}円`}
      ／参照元の記載値：{reference.source_value == null ? "なし" : `${reference.source_value.toLocaleString("ja-JP")}円`}</p>
    <p>基準日：{reference.source_period_end ?? "不明"}／開示日：{reference.source_disclosed_date ?? "不明"}
      ／開示番号：{reference.source_disclosure_no ?? "なし"}／書類：{reference.source_doc_type ?? "なし"}</p>
    <p>基準日からの参照期限：{reference.max_age_days}日。手動確認は最長{reference.review_max_age_days}日間有効です。
      新しい開示や分割等の検出で失効します。最新決算の欠損を過去値で自動補完するものではありません。</p>
    {reference.review && <div>
      <p>資料確認日：{reference.review.checked_on}／状態：{
        reference.review_status === "valid" ? "有効" : reference.review_status === "expired" ? "期限切れ" : "失効・取消済み"}</p>
      {reference.review.evidence_url && <p><a href={reference.review.evidence_url} target="_blank" rel="noreferrer">確認根拠の資料</a></p>}
      {reference.review.notes && <p>{reference.review.notes}</p>}
      {reference.review_status === "valid" && <button type="button" disabled={saving} onClick={() => save("DELETE")}>BPS確認を取り消す</button>}
    </div>}
    {reference.reviewable && <details><summary>資料を確認して過去BPSの参照を許可</summary>
      <p>下記を確認した場合だけ記録してください。他の財務・イベント・資金条件は解除されません。
        保存後に画面を再読み込みして再分析します。市場一覧は次回の一括再分析まで再分析待ちになります。</p>
      <form onSubmit={submit} className="research-settings">
        <label>根拠資料URL<input name="evidence_url" type="url" required maxLength={2000} /></label>
        <label>対象期間・確認した内容<textarea name="notes" required minLength={10} maxLength={2000} /></label>
        <label><input type="checkbox" name="capital" required />BPS基準日から本日までの分割・併合・増減資・自己株式の変化を確認し、過去値の参照に影響がない</label>
        <label><input type="checkbox" name="basis" required />連結区分・会計基準・訂正内容に整合性がある</label>
        <label><input type="checkbox" name="equity" required />その後の損失等による純資産の重要な変化がなく、参照可能と確認した</label>
        <button disabled={saving} type="submit">確認根拠を保存して再分析</button>
      </form>
    </details>}
    {message && <p role="status">{message}</p>}
  </section>;
}
