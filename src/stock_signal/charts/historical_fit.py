from __future__ import annotations

import html
import re
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from stock_signal.analysis.historical_validation import HistoricalValidationPoint
from stock_signal.domain.analysis import Direction

_DIRECTION_LABELS = {
    Direction.UP: "上昇",
    Direction.FLAT: "停滞",
    Direction.DOWN: "下落",
}
_ACTION_LABELS = {
    "buy_candidate": "購入候補",
    "watch": "様子見",
    "avoid_new_buy": "新規購入回避",
    "insufficient_data": "データ不足",
}
_HORIZON_LABELS = {5: "スイング", 20: "中長期"}


def _safe_filename(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-.")
    if not normalized:
        raise ValueError("証券コードを安全なファイル名へ変換できません")
    return normalized.lower()


def _result_for(point: HistoricalValidationPoint, horizon: int):
    return next(
        (result for result in point.results if result.horizon_days == horizon),
        None,
    )


def _completed_results(
    points: Sequence[HistoricalValidationPoint],
    horizon: int,
):
    return [
        result
        for point in points
        if (result := _result_for(point, horizon)) is not None
        and result.status == "ready"
        and result.actual is not None
    ]


def _render_summary(
    points: Sequence[HistoricalValidationPoint],
    horizon: int,
) -> str:
    results = _completed_results(points, horizon)
    matches = sum(result.direction_matched is True for result in results)
    match_rate = matches / len(results) * 100 if results else None
    actual_counts = {
        direction: sum(result.actual.direction is direction for result in results)
        for direction in Direction
    }
    rate_text = "—" if match_rate is None else f"{match_rate:.1f}%"
    events = [result for result in results if result.event_started]
    target_hits = sum(result.actual.target_hit is True for result in events)
    stop_hits = sum(result.actual.stop_hit is True for result in events)
    average_return = (
        sum(result.actual.return_percent for result in events) / len(events)
        if events
        else None
    )
    average_return_text = "—" if average_return is None else f"{average_return:+.2f}%"
    label = _HORIZON_LABELS[horizon]
    return f"""
      <article class="summary-card horizon-{horizon}">
        <div><span>{label}</span><strong>{horizon}営業日後</strong></div>
        <dl>
          <div><dt>検証件数</dt><dd>{len(results)}件</dd></div>
          <div><dt>方向一致</dt><dd>{matches}件</dd></div>
          <div><dt>方向一致率</dt><dd>{rate_text}</dd></div>
          <div><dt>購入イベント</dt><dd>{len(events)}件</dd></div>
          <div><dt>目標／損切り到達</dt><dd>{target_hits}／{stop_hits}件</dd></div>
          <div><dt>イベント平均騰落</dt><dd>{average_return_text}</dd></div>
        </dl>
        <p>実績内訳：上昇 {actual_counts[Direction.UP]}・停滞
          {actual_counts[Direction.FLAT]}・下落 {actual_counts[Direction.DOWN]}</p>
      </article>
    """


def _render_result_cells(point: HistoricalValidationPoint, horizon: int) -> str:
    result = _result_for(point, horizon)
    if result is None or result.status != "ready" or result.actual is None:
        message = "検証対象外" if result is None else result.message
        return (
            '<td class="unavailable">—</td>'
            '<td class="unavailable">—</td>'
            f'<td class="unavailable" title="{html.escape(message)}">対象外</td>'
            '<td class="number unavailable">—</td>'
        )

    decision = result.analysis.investment_decision if result.analysis else None
    action = (
        _ACTION_LABELS.get(decision.action.value, decision.action.value)
        if decision is not None
        else "未判定"
    )
    predicted = result.analysis.direction if result.analysis else Direction.FLAT
    actual = result.actual
    match_class = "matched" if result.direction_matched else "not-matched"
    match_label = "一致" if result.direction_matched else "不一致"
    event_label = "<b>新規イベント</b>" if result.event_started else ""
    path_metrics = (
        f"MFE {actual.maximum_favorable_excursion_percent:+.2f}% / "
        f"MAE {actual.maximum_adverse_excursion_percent:+.2f}%"
        if actual.maximum_favorable_excursion_percent is not None
        and actual.maximum_adverse_excursion_percent is not None
        else f"{actual.move_atr:+.2f} ATR"
    )
    return f"""
      <td><span class="direction {predicted.value}">{_DIRECTION_LABELS[predicted]}</span>
        <small>{html.escape(action)} {event_label}</small></td>
      <td><span class="direction {actual.direction.value}">
        {_DIRECTION_LABELS[actual.direction]}</span>
        <small>{actual.target_date.isoformat()}</small></td>
      <td><span class="match {match_class}">{match_label}</span></td>
      <td class="number">{actual.return_percent:+.2f}%<small>{path_metrics}</small></td>
    """


def render_historical_fit_report(
    symbol: str,
    provider: str,
    points: Sequence[HistoricalValidationPoint],
    output_directory: Path,
    *,
    display_name: str | None = None,
) -> Path:
    """スイング・中長期の過去当てはめ実績を単独HTMLへ出力する。"""
    if not points:
        raise ValueError("過去当てはめ実績を出力できる判定日がありません")
    ordered = sorted(points, key=lambda point: point.as_of_date)
    visible_points = [
        point
        for point in ordered
        if any(
            result.status == "ready" and result.actual is not None
            for result in point.results
        )
    ]
    if not visible_points:
        raise ValueError("指定期間には実績日までそろった検証結果がありません")

    first_date = ordered[0].as_of_date
    last_date = ordered[-1].as_of_date
    output_directory.mkdir(parents=True, exist_ok=True)
    output_path = output_directory / (
        f"{_safe_filename(symbol)}-historical-fit-"
        f"{first_date:%Y%m%d}-{last_date:%Y%m%d}.html"
    )
    title_name = f" {html.escape(display_name)}" if display_name else ""
    rows = "".join(
        f"""
        <tr>
          <th scope="row">{point.as_of_date.isoformat()}</th>
          {_render_result_cells(point, 5)}
          {_render_result_cells(point, 20)}
        </tr>
        """
        for point in reversed(visible_points)
    )
    skipped = len(ordered) - len(visible_points)
    generated_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    engine_versions = sorted({
        f"{result.analysis.engine_id} v{result.analysis.engine_version}"
        for point in ordered
        for result in point.results
        if result.analysis is not None
    })
    engine_label = "、".join(engine_versions) if engine_versions else "未判定"
    document = f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(symbol.upper())} 過去当てはめ実績</title>
  <style>
    :root {{ --bg:#f5f2ec; --surface:#fff; --text:#27231f; --muted:#756d63;
      --border:#e2ddd4; --up:#16784a; --down:#b73b3b; --flat:#80651c; --accent:#a95c21; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text);
      font-family:ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
    main {{ width:min(1500px,calc(100% - 32px)); margin:24px auto 48px; }}
    header,.method,.table-panel {{ padding:22px; border:1px solid var(--border);
      border-radius:14px; background:var(--surface); }}
    h1 {{ margin:4px 0 6px; font-size:25px; }}
    h2 {{ margin:0 0 8px; font-size:16px; }}
    p {{ line-height:1.65; }} .eyebrow,small,.meta,.method p {{ color:var(--muted); }}
    .eyebrow {{ margin:0; font-size:11px; letter-spacing:.08em; }}
    .meta {{ margin:0; font-size:11px; }}
    .summary {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; margin:16px 0; }}
    .summary-card {{ padding:18px; border:1px solid var(--border);
      border-left:5px solid var(--accent);
      border-radius:12px; background:var(--surface); }}
    .summary-card>div span,.summary-card>div strong {{ display:block; }}
    .summary-card>div span {{ color:var(--muted); font-size:11px; }}
    .summary-card>div strong {{ margin-top:2px; font-size:18px; }}
    .summary-card dl {{ display:grid; grid-template-columns:repeat(3,1fr); gap:8px;
      margin:14px 0 8px; }}
    .summary-card dl div {{ padding:10px; border-radius:8px; background:#faf8f4; }}
    dt {{ color:var(--muted); font-size:10px; }} dd {{ margin:3px 0 0; font-weight:700; }}
    .summary-card p {{ margin:0; color:var(--muted); font-size:11px; }}
    .method {{ margin-bottom:16px; }} .method p {{ margin:0; font-size:12px; }}
    .table-panel {{ overflow:hidden; }} .table-scroll {{ overflow-x:auto; }}
    table {{ width:100%; min-width:1080px; border-collapse:collapse; font-size:12px; }}
    caption {{ padding:0 0 12px; text-align:left; color:var(--muted); font-size:11px; }}
    th,td {{ padding:11px 9px; border-bottom:1px solid var(--border); text-align:left;
      vertical-align:middle; }}
    thead th {{ position:sticky; top:0; background:#faf8f4; color:var(--muted); font-size:10px; }}
    thead tr:first-child th {{ text-align:center; color:var(--text); font-size:11px; }}
    tbody th {{ white-space:nowrap; }}
    td small,.number small {{ display:block; margin-top:3px; font-size:9px; }}
    td small b {{ color:var(--accent); margin-left:4px; }}
    .direction,.match {{ display:inline-block; padding:4px 7px; border-radius:999px;
      font-weight:700; }}
    .direction.up {{ background:#e7f4ed; color:var(--up); }}
    .direction.down {{ background:#f9e9e9; color:var(--down); }}
    .direction.flat {{ background:#f5f0df; color:var(--flat); }}
    .match.matched {{ background:#e7f4ed; color:var(--up); }}
    .match.not-matched {{ background:#f9e9e9; color:var(--down); }}
    .number {{ text-align:right; font-variant-numeric:tabular-nums; }}
    .unavailable {{ color:var(--muted); }}
    footer {{ margin-top:12px; color:var(--muted); font-size:10px; line-height:1.6; }}
    @media (max-width:700px) {{ .summary {{ grid-template-columns:1fr; }}
      .summary-card dl {{ grid-template-columns:1fr 1fr; }}
      main {{ width:min(100% - 20px,1500px); }} }}
    @media print {{ body {{ background:#fff; }} main {{ width:100%; margin:0; }}
      header,.method,.table-panel,.summary-card {{ break-inside:avoid; box-shadow:none; }} }}
  </style>
</head>
<body>
<main>
  <header>
    <p class="eyebrow">TOMOSHIBIYORI・保存済み日足による時点検証</p>
    <h1>{html.escape(symbol.upper())}{title_name} 過去当てはめ実績</h1>
    <p class="meta">取得元 {html.escape(provider)} ／ 判定期間 {first_date}〜{last_date} ／
      エンジン {html.escape(engine_label)} ／ 作成 {generated_at}</p>
  </header>
  <section class="summary" aria-label="運用スタイル別集計">
    {_render_summary(ordered, 5)}
    {_render_summary(ordered, 20)}
  </section>
  <section class="method">
    <h2>読み方</h2>
    <p>現在のエンジン版を各判定日へ遡及適用し、その日以前の日足だけでテクニカル方向を
      再計算しています。約定価格は判定の翌営業日始値とし、スイングは5営業日後、
      中長期は20営業日後の終値と比較します。連続する購入候補は初日を一つの購入イベントとして集計します。
      実績方向は判定日時点のWilder ATRを使い、
      スイングは±0.5 ATR、中長期は±1.0 ATR以上を上昇・下落、その内側を停滞とします。
      MFEは期間中の最大上昇、MAEは最大逆行です。同一日に目標と損切りの双方へ触れた場合は、
      保守的に損切りが先と扱います。一致率は方向3分類の当てはまりであり、
      売買戦略の勝率、利益率、将来確率ではありません。</p>
  </section>
  <section class="table-panel">
    <h2>判定日別の結果</h2>
    <div class="table-scroll">
      <table>
        <caption>実績が未確定または履歴不足の判定日 {skipped}件を詳細表から除外</caption>
        <thead>
          <tr><th rowspan="2">判定日</th><th colspan="4">スイング・5営業日後</th>
            <th colspan="4">中長期・20営業日後</th></tr>
          <tr><th>当時の判定</th><th>実績</th><th>照合</th><th>騰落率</th>
            <th>当時の判定</th><th>実績</th><th>照合</th><th>騰落率</th></tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
  </section>
  <footer>本レポートは保存済みデータによる分析情報であり、投資助言や利益保証ではありません。
    過去時点の決算予定履歴、手数料、税金、スリッページは含みません。
    株価は現在DBに保存されている最新の遡及調整済み系列を使用します。</footer>
</main>
</body>
</html>
"""
    output_path.write_text(document, encoding="utf-8")
    return output_path
