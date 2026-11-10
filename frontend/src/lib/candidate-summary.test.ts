import assert from "node:assert/strict";
import test from "node:test";
import { candidateUniverse, matchesCandidate } from "./candidate-summary.ts";
import type { DashboardData, MarketCandidate } from "../types/api.ts";

const instrument = (symbol: string) => ({ symbol, provider: "jquants", display_name: symbol });
const result = (symbol: string, action: MarketCandidate["action"], extra = {}) => ({
  ...instrument(symbol), action, as_of_date: "2026-11-05", ...extra,
});
const data = {
  candidateUniverse: { items: [instrument("1"), instrument("2"), instrument("3")], saved_at: {} },
  candidates: {
    "5": [result("1", "buy_candidate"), result("2", "watch", { screening_status: "data_pending", market_gate_diagnostic: { status: "market_only" } })],
    "20": [result("1", "avoid_new_buy")],
  },
} as unknown as DashboardData;

test("各分析軸で重複を除き、未判定を含めた区分合計が対象数と一致する", () => {
  for (const horizon of [5, 20] as const) {
    const items = candidateUniverse(data, horizon);
    assert.equal(items.length, 3);
    assert.equal(["buy_candidate", "watch", "avoid_new_buy", "unassessed"].reduce((sum, action) => sum + items.filter(item => matchesCandidate(item, action as Parameters<typeof matchesCandidate>[1])).length, 0), items.length);
  }
  assert.equal(candidateUniverse(data, 5).filter(item => matchesCandidate(item, "unassessed")).length, 1);
  assert.equal(candidateUniverse(data, 20).filter(item => matchesCandidate(item, "unassessed")).length, 2);
});
test("確認状況の重複は投資検討区分の件数に加算しない", () => {
  const items = candidateUniverse(data, 5);
  assert.equal(items.filter(item => matchesCandidate(item, "watch")).length, 1);
  assert.equal(items.filter(item => matchesCandidate(item, "financial_pending")).length, 1);
  assert.equal(items.filter(item => matchesCandidate(item, "market_only")).length, 1);
});
test("全ページ分を集計し、マスタにない分析済み銘柄も保持する", () => {
  const many = { ...data, candidates: { ...data.candidates, "5": Array.from({ length: 1200 }, (_, i) => result(String(i), "watch")) } } as unknown as DashboardData;
  assert.equal(candidateUniverse(many, 5).length, 1200);
});
test("結果が未生成なら登録銘柄はすべて未判定になる", () => {
  const empty = { ...data, candidates: { "5": [], "20": [] } };
  assert.equal(candidateUniverse(empty, 5).filter(item => matchesCandidate(item, "unassessed")).length, 3);
});
test("取得失敗を未判定銘柄に置き換えない", () => {
  const failed = { ...data, candidateErrors: { "5": true } };
  assert.deepEqual(candidateUniverse(failed, 5), []);
  assert.equal(candidateUniverse(failed, 20).length, 3);
});
