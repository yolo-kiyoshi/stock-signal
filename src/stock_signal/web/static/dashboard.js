(() => {
  const body = document.body;
  const sidebar = document.getElementById("app-sidebar");
  const sidebarToggle = document.getElementById("sidebar-toggle");
  const chartElement = document.getElementById("price-chart");
  const errorElement = document.getElementById("chart-error");
  const symbolElement = document.getElementById("instrument-symbol");
  const nameElement = document.getElementById("instrument-name");
  const sourceElement = document.getElementById("source-label");
  const priceElement = document.getElementById("latest-price");
  const rangeLabel = document.getElementById("chart-range-label");
  const predictionElement = document.getElementById("prediction-content");
  const sidebarSearchInput = document.getElementById("sidebar-search-input");
  const sidebarSearchClear = document.getElementById("sidebar-search-clear");
  const sidebarSearchEmpty = document.getElementById("sidebar-search-empty");
  let selectedSymbol = body.dataset.selectedSymbol;
  let selectedProvider = body.dataset.selectedProvider || null;
  let selectedRange = "3m";
  let selectedHorizon = 5;

  function setSidebarCollapsed(collapsed) {
    body.classList.toggle("sidebar-collapsed", collapsed);
    sidebarToggle.setAttribute("aria-expanded", String(!collapsed));
    sidebarToggle.setAttribute(
      "aria-label",
      collapsed ? "銘柄ナビゲーションを開く" : "銘柄ナビゲーションを閉じる",
    );
    sidebarToggle.querySelector(".toggle-label").textContent = collapsed
      ? "銘柄一覧を開く"
      : "銘柄一覧を閉じる";
    sidebar.inert = collapsed;
    sidebar.setAttribute("aria-hidden", String(collapsed));
  }

  sidebarToggle.addEventListener("click", () => {
    setSidebarCollapsed(!body.classList.contains("sidebar-collapsed"));
  });
  setSidebarCollapsed(window.matchMedia("(max-width: 700px)").matches);

  const formatPrice = (value) => new Intl.NumberFormat("ja-JP", {
    maximumFractionDigits: 4,
  }).format(Number(value));

  async function loadChart({ from = null, to = null } = {}) {
    if (!selectedSymbol) return;
    errorElement.hidden = true;
    const parameters = new URLSearchParams({ range: selectedRange });
    if (selectedProvider) parameters.set("provider", selectedProvider);
    if (from) parameters.set("from", from);
    if (to) parameters.set("to", to);
    try {
      const response = await fetch(
        `/api/v1/instruments/${encodeURIComponent(selectedSymbol)}/daily-bars?${parameters}`,
      );
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "日足データを取得できませんでした");
      const bars = payload.bars;
      if (!bars.length) throw new Error("指定期間の日足データがありません");
      const dates = bars.map((bar) => bar.trade_date);
      const candlestick = {
        type: "candlestick",
        x: dates,
        open: bars.map((bar) => Number(bar.open)),
        high: bars.map((bar) => Number(bar.high)),
        low: bars.map((bar) => Number(bar.low)),
        close: bars.map((bar) => Number(bar.close)),
        name: selectedSymbol,
        increasing: { line: { color: "#16784a" } },
        decreasing: { line: { color: "#b73b3b" } },
        xaxis: "x",
        yaxis: "y",
      };
      const volume = {
        type: "bar",
        x: dates,
        y: bars.map((bar) => bar.volume),
        marker: { color: "#8b98a5" },
        name: "出来高",
        xaxis: "x",
        yaxis: "y2",
        hovertemplate: "%{x}<br>出来高 %{y:,}<extra></extra>",
      };
      const layout = {
        margin: { l: 64, r: 24, t: 16, b: 44 },
        paper_bgcolor: "#ffffff",
        plot_bgcolor: "#ffffff",
        showlegend: false,
        hovermode: "x unified",
        xaxis: { rangeslider: { visible: false }, anchor: "y2", gridcolor: "#edf0f2" },
        yaxis: { domain: [0.28, 1], title: "価格", gridcolor: "#edf0f2" },
        yaxis2: { domain: [0, 0.2], title: "出来高", gridcolor: "#edf0f2" },
      };
      await Plotly.react(chartElement, [candlestick, volume], layout, {
        responsive: true,
        displaylogo: false,
        locale: "ja",
      });
      priceElement.textContent = formatPrice(bars.at(-1).close);
      sourceElement.textContent = `取得元: ${payload.source.provider} ・ ${
        payload.source.is_adjusted ? "調整済み" : "未調整"
      } ・ 最新 ${payload.freshness.latest_trade_date}`;
      rangeLabel.textContent = `${payload.range.from} 〜 ${payload.range.to}`;
      document.getElementById("from-date").value = payload.range.from;
      document.getElementById("to-date").value = payload.range.to;
    } catch (error) {
      errorElement.textContent = error.message;
      errorElement.hidden = false;
      Plotly.purge(chartElement);
    }
  }

  const directionLabels = { up: "上昇", flat: "停滞", down: "下落" };
  const actionLabels = {
    buy_candidate: "購入候補",
    watch: "様子見",
    avoid_new_buy: "新規購入回避",
    insufficient_data: "データ不足",
  };
  const checkStatusLabels = {
    evaluated: "評価済み",
    partial: "一部評価",
    pending_data: "同期待ち",
    plan_unavailable: "プラン対象外",
    addon_required: "アドオン未契約",
    unavailable: "評価不能",
  };
  const breakoutKindLabels = {
    normal: "通常ブレイク",
    gap_driven: "ギャップ主導",
    not_evaluated: "未評価",
  };
  const lifecycleStatusLabels = {
    entry_window: "新規検討期間",
    monitoring: "保有監視期間",
    weakening: "勢い弱化",
    target_reached: "目標到達",
    failed: "ブレイク失敗",
    expired: "期限切れ",
  };
  const lifecycleGuidanceLabels = {
    consider_entry: "新規購入を検討可",
    hold_and_monitor: "保有なら継続監視",
    take_profit_review: "利益確定条件を確認",
    exit_review: "撤退条件を確認",
    ignore_old_signal: "新規判断に使用しない",
  };
  const transitionPhaseLabels = {
    falling: "下降継続",
    bottoming: "底固め観察",
    preparing: "転換準備",
    one_gate_remaining: "あと1条件",
    early_reversal: "転換初動",
    uptrend: "上昇継続",
    caution: "警戒",
    unknown: "未評価",
  };
  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
  const metric = (value, suffix = "") => value === null || value === undefined
    ? "—"
    : `${Number(value).toFixed(2)}${suffix}`;

  const normalizeSearchText = (value) => String(value ?? "")
    .normalize("NFKC")
    .toLocaleLowerCase("ja-JP")
    .replaceAll(/\s+/g, "");
  const matchesSidebarSearch = (item, query) => {
    if (!query) return true;
    return normalizeSearchText(`${item.dataset.symbol || ""}${item.dataset.name || ""}`).includes(query);
  };

  function updateSidebarSearchEmpty(visibleCount) {
    const hasQuery = normalizeSearchText(sidebarSearchInput.value).length > 0;
    sidebarSearchClear.hidden = !hasQuery;
    sidebarSearchEmpty.hidden = !hasQuery || visibleCount > 0;
  }

  function applySidebarSearch() {
    const activePanel = document.querySelector(".sidebar-panel:not([hidden])");
    if (!activePanel) return;
    if (activePanel.id === "candidates-panel") {
      applyCandidateFilters();
      return;
    }
    const query = normalizeSearchText(sidebarSearchInput.value);
    const rows = [...activePanel.querySelectorAll(".watchlist-row")];
    let visibleCount = 0;
    rows.forEach((row) => {
      const item = row.querySelector(".watchlist-item");
      const visible = Boolean(item) && matchesSidebarSearch(item, query);
      row.hidden = !visible;
      if (visible) visibleCount += 1;
    });
    updateSidebarSearchEmpty(visibleCount);
  }

  function selectSidebarTab(selectedTab) {
    document.querySelectorAll(".sidebar-tabs [role='tab']").forEach((tab) => {
      const selected = tab === selectedTab;
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
      document.getElementById(tab.dataset.sidebarPanel).hidden = !selected;
    });
    applySidebarSearch();
  }

  document.querySelectorAll(".sidebar-tabs [role='tab']").forEach((tab, index, tabs) => {
    tab.addEventListener("click", () => selectSidebarTab(tab));
    tab.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
      event.preventDefault();
      const offset = event.key === "ArrowRight" ? 1 : -1;
      const nextTab = tabs[(index + offset + tabs.length) % tabs.length];
      selectSidebarTab(nextTab);
      nextTab.focus();
    });
  });

  async function loadPrediction() {
    if (!selectedSymbol) return;
    const response = await fetch(
      `/api/v1/instruments/${encodeURIComponent(selectedSymbol)}/analysis/latest?horizon=${selectedHorizon}${selectedProvider ? `&provider=${encodeURIComponent(selectedProvider)}` : ""}`,
    );
    const payload = await response.json();
    if (!response.ok || payload.status !== "ready") {
      predictionElement.className = "prediction-empty";
      predictionElement.textContent = payload.message || payload.detail || "分析結果を取得できませんでした";
      return;
    }
    predictionElement.className = "analysis-result";
    const decision = payload.investment_decision || {
      action: "watch",
      evidence_score: payload.scores[payload.direction],
      summary: "投資検討区分は生成されていません",
      reasons: [],
      cautions: ["分析エンジンの出力契約を確認してください"],
    };
    const scores = ["up", "flat", "down"].map((direction) => `
      <div class="probability-item ${direction === payload.direction ? "winner" : ""}">
        <span>${directionLabels[direction]}</span><strong>${payload.scores[direction].toFixed(1)}</strong><small>判定スコア</small>
      </div>`).join("");
    const factors = payload.factors.map((factor) => `
      <li class="factor-item ${factor.direction}">
        <div class="factor-heading">
          <span class="factor-direction">${directionLabels[factor.direction]}</span>
          <span class="factor-score">+${factor.score}</span>
        </div>
        <strong>${escapeHtml(factor.name)}</strong>
        <p>${escapeHtml(factor.description)}</p>
      </li>`).join("");
    const detectedPatterns = payload.patterns || [];
    const patterns = detectedPatterns.length ? detectedPatterns.map((pattern) => {
      const lifecycle = pattern.lifecycle;
      const lifecyclePanel = lifecycle ? `
        <section class="pattern-lifecycle ${lifecycle.status}">
          <div>
            <span>${lifecycleStatusLabels[lifecycle.status]}</span>
            <strong>${lifecycleGuidanceLabels[lifecycle.guidance]}</strong>
          </div>
          <p>${escapeHtml(lifecycle.summary)}</p>
          <dl class="lifecycle-grid">
            <div><dt>ブレイク後</dt><dd>${lifecycle.trading_days_since_breakout}営業日</dd></div>
            <div><dt>新規検討の残り</dt><dd>${lifecycle.entry_days_remaining}営業日</dd></div>
            <div><dt>目標水準</dt><dd>${formatPrice(lifecycle.target_price)}</dd></div>
            <div><dt>無効化水準</dt><dd>${formatPrice(lifecycle.invalidation_price)}</dd></div>
            <div><dt>ブレイク後騰落</dt><dd>${Number(lifecycle.post_breakout_return_percent).toFixed(2)}%</dd></div>
            <div><dt>直近の勢い</dt><dd>${metric(lifecycle.recent_momentum_atr, " ATR")}</dd></div>
          </dl>
          <small>新規検討 ${lifecycle.entry_window_days}営業日以内 ・ 監視上限 ${lifecycle.maximum_monitoring_days}営業日</small>
        </section>` : "";
      return `
      <article class="pattern-card ${pattern.direction}">
        <div class="pattern-title"><strong>${escapeHtml(pattern.name)}</strong><span>一致度 ${pattern.fit_score.toFixed(1)}</span></div>
        <p>${escapeHtml(pattern.description)}</p>
        ${lifecyclePanel}
        <dl class="metric-grid">
          <div><dt>形成期間</dt><dd>${pattern.duration_days}営業日</dd></div>
          <div><dt>ブレイク水準</dt><dd>${formatPrice(pattern.breakout_level)}</dd></div>
          <div><dt>ブレイク幅</dt><dd>${metric(pattern.breakout_atr, " ATR")}</dd></div>
          <div><dt>出来高倍率</dt><dd>${metric(pattern.volume_ratio, "倍")}</dd></div>
          <div><dt>窓開け</dt><dd>${metric(pattern.gap_atr, " ATR")}</dd></div>
          <div><dt>事前トレンド</dt><dd>${metric(pattern.prior_trend_score, "点")}</dd></div>
        </dl>
        <small>${breakoutKindLabels[pattern.breakout_kind]} ・ 検出日 ${pattern.detected_at}</small>
      </article>`;
    }).join("") : '<p class="inline-empty">直近25営業日に発生した対象パターンはありません。</p>';
    const checks = (payload.equity_checks || []).map((check) => `
      <li class="check-item ${check.status}">
        <div><strong>${escapeHtml(check.label)}</strong><span>${checkStatusLabels[check.status]}</span></div>
        <p>${escapeHtml(check.description)}</p>
      </li>`).join("");
    const reasons = decision.reasons.map((reason) => `<li>${escapeHtml(reason)}</li>`).join("");
    const cautions = decision.cautions.map((caution) => `<li>${escapeHtml(caution)}</li>`).join("");
    const transition = payload.transition_readiness;
    const transitionConditions = transition ? transition.conditions.map((condition) => `
      <li class="transition-condition ${condition.satisfied ? "satisfied" : "pending"}">
        <span aria-hidden="true">${condition.satisfied ? "✓" : "○"}</span>
        <div><strong>${escapeHtml(condition.label)}</strong><p>${escapeHtml(condition.description)}</p></div>
      </li>`).join("") : "";
    const transitionPanel = transition ? `
      <section class="transition-readiness ${transition.phase}">
        <div class="transition-heading">
          <div><span>上昇転換の段階</span><strong>${transitionPhaseLabels[transition.phase]}</strong></div>
          <div class="transition-progress"><span>条件進捗</span><strong>${transition.satisfied_conditions} / ${transition.total_conditions}</strong><small>確率ではありません</small></div>
        </div>
        <p>${escapeHtml(transition.summary)}</p>
        ${transition.next_condition ? `<div class="next-condition"><span>次に必要な条件</span><strong>${escapeHtml(transition.next_condition.label)}</strong><p>${escapeHtml(transition.next_condition.description)}</p></div>` : ""}
        <dl class="transition-levels">
          <div><dt>転換水準</dt><dd>${formatPrice(transition.trigger_price)}</dd></div>
          <div><dt>無効化水準</dt><dd>${formatPrice(transition.invalidation_price)}</dd></div>
          <div><dt>参考目標</dt><dd>${formatPrice(transition.target_price)}</dd></div>
          <div><dt>参考R/R</dt><dd>${metric(transition.risk_reward_ratio)}</dd></div>
        </dl>
        <ul class="transition-condition-list">${transitionConditions}</ul>
      </section>` : "";
    predictionElement.innerHTML = `
      <section class="investment-decision ${decision.action}">
        <div><span>投資検討区分</span><strong>${actionLabels[decision.action]}</strong></div>
        <div class="evidence-score"><span>根拠の強さ</span><strong>${decision.evidence_score.toFixed(1)}</strong><small>確率ではありません</small></div>
        <p>${escapeHtml(decision.summary)}</p>
        <ul>${reasons}</ul>
      </section>
      ${transitionPanel}
      <div class="analysis-summary">
        <div><span>テクニカル方向</span><strong class="decision ${payload.direction}">${directionLabels[payload.direction]}</strong></div>
        <small>基準日 ${payload.as_of_date} ・ ${payload.engine.id} v${payload.engine.version}</small>
      </div>
      <p class="direction-explanation">テクニカル方向は現在の値動き、投資検討区分は出来高・決算・市場環境も含む判断です。上昇でも確認不足なら「様子見」になります。</p>
      <div class="probability-grid">${scores}</div>
      <h4>チャートパターンと現在の有効性</h4>
      <div class="pattern-list">${patterns}</div>
      <h4>個別株固有の確認</h4>
      <ul class="check-list">${checks}</ul>
      <h4>判定要因</h4>
      <ul class="factor-list">${factors}</ul>
      <details class="caution-list"><summary>未評価項目と注意事項</summary><ul>${cautions}</ul></details>
      <p class="analysis-disclaimer">各スコアは適用されたルールの重みを正規化した参考値で、上昇・停滞・下落の確率ではありません。</p>`;
  }

  function selectInstrument(button) {
    selectedSymbol = button.dataset.symbol;
    selectedProvider = button.dataset.provider || null;
    symbolElement.textContent = selectedSymbol;
    nameElement.textContent = button.dataset.name || "";
    document.querySelectorAll(".watchlist-item").forEach((item) => {
      item.classList.toggle("selected", item.dataset.symbol === selectedSymbol);
    });
    document.querySelectorAll(".signal-item").forEach((item) => {
      item.classList.toggle("selected", item.dataset.symbol === selectedSymbol);
    });
    loadChart();
    loadPrediction();
  }

  document.querySelectorAll(".watchlist-item").forEach((button) => {
    button.addEventListener("click", () => selectInstrument(button));
  });
  document.querySelectorAll(".watchlist-delete:not(.position-delete)").forEach((button) => {
    button.addEventListener("click", async () => {
      const label = `${button.dataset.symbol} ${button.dataset.name}`;
      if (!window.confirm(`${label}をウォッチリストから削除しますか？\n保存済みの日足データは削除されません。`)) return;
      button.disabled = true;
      try {
        const parameters = new URLSearchParams({ provider: button.dataset.provider });
        const response = await fetch(
          `/api/v1/watchlist/${encodeURIComponent(button.dataset.symbol)}?${parameters}`,
          { method: "DELETE" },
        );
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || "削除できませんでした");
        window.location.reload();
      } catch (error) {
        window.alert(error.message);
        button.disabled = false;
      }
    });
  });
  document.querySelectorAll(".position-delete").forEach((button) => {
    button.addEventListener("click", async () => {
      if (!window.confirm(`${button.dataset.symbol} ${button.dataset.name}を保有銘柄から削除しますか？`)) return;
      button.disabled = true;
      try {
        const parameters = new URLSearchParams({ provider: button.dataset.provider });
        const response = await fetch(
          `/api/v1/portfolio/positions/${encodeURIComponent(button.dataset.symbol)}?${parameters}`,
          { method: "DELETE" },
        );
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || "削除できませんでした");
        window.location.reload();
      } catch (error) {
        window.alert(error.message);
        button.disabled = false;
      }
    });
  });
  document.querySelectorAll(".signal-item").forEach((button) => {
    button.addEventListener("click", () => {
      const watchlistButton = document.querySelector(
        `.watchlist-item[data-symbol="${CSS.escape(button.dataset.symbol)}"]`,
      );
      selectInstrument(watchlistButton || button);
    });
  });
  let candidateScope = document.querySelector(".candidate-scopes button[aria-pressed='true']")?.dataset.scope || "watchlist";
  let candidateAction = document.querySelector(".direction-tabs button[aria-selected='true']")?.dataset.action || "buy_candidate";
  let candidateTransition = document.getElementById("transition-phase-filter")?.value || "all";
  function applyCandidateFilters() {
    const query = normalizeSearchText(sidebarSearchInput.value);
    const scopeCandidates = [...document.querySelectorAll(".signal-item")]
      .filter((item) => item.dataset.scope === candidateScope);
    let visibleCandidateCount = 0;
    document.querySelectorAll(".signal-item").forEach((item) => {
      const visible = (
        item.dataset.scope === candidateScope
        && item.dataset.action === candidateAction
        && (candidateTransition === "all" || item.dataset.transitionPhase === candidateTransition)
        && matchesSidebarSearch(item, query)
      );
      const row = item.closest(".market-signal-row") || item;
      row.hidden = !visible;
      if (row !== item) item.hidden = false;
      if (visible) visibleCandidateCount += 1;
    });
    document.querySelectorAll("[data-scope-empty]").forEach((item) => {
      item.hidden = item.dataset.scopeEmpty !== candidateScope || scopeCandidates.length > 0;
    });
    document.querySelectorAll(".direction-tabs [data-action] span").forEach((count) => {
      count.textContent = candidateScope === "market" ? count.dataset.marketCount : count.dataset.watchlistCount;
    });
    document.querySelectorAll("[data-candidate-scope-note]").forEach((note) => {
      note.hidden = note.dataset.candidateScopeNote !== candidateScope;
    });
    document.getElementById("candidate-filter-empty").hidden = (
      query.length > 0 || visibleCandidateCount > 0 || scopeCandidates.length === 0
    );
    updateSidebarSearchEmpty(visibleCandidateCount);
  }
  document.querySelectorAll(".candidate-scopes button").forEach((button) => {
    button.addEventListener("click", () => {
      candidateScope = button.dataset.scope;
      document.querySelectorAll(".candidate-scopes button").forEach((item) => {
        item.setAttribute("aria-pressed", String(item === button));
      });
      applyCandidateFilters();
    });
  });
  document.querySelectorAll(".direction-tabs button").forEach((button) => {
    button.addEventListener("click", () => {
      candidateAction = button.dataset.action;
      document.querySelectorAll(".direction-tabs button").forEach((item) => {
        item.setAttribute("aria-selected", String(item === button));
      });
      applyCandidateFilters();
    });
  });
  document.getElementById("transition-phase-filter")?.addEventListener("change", (event) => {
    candidateTransition = event.currentTarget.value;
    applyCandidateFilters();
  });
  sidebarSearchInput.addEventListener("input", applySidebarSearch);
  sidebarSearchClear.addEventListener("click", () => {
    sidebarSearchInput.value = "";
    applySidebarSearch();
    sidebarSearchInput.focus();
  });
  applyCandidateFilters();
  document.querySelectorAll(".market-watch-add:not(.is-added)").forEach((button) => {
    button.addEventListener("click", async () => {
      const message = document.getElementById("candidate-action-message");
      button.disabled = true;
      button.classList.add("is-loading");
      button.querySelector("[aria-hidden='true']").textContent = "…";
      message.hidden = false;
      message.className = "candidate-action-message";
      message.textContent = `${button.dataset.symbol}をウォッチリストへ追加しています…`;
      try {
        const response = await fetch(`/api/v1/watchlists/${encodeURIComponent("ウォッチ")}/items/bulk`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ symbols: [button.dataset.symbol] }),
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || "ウォッチリストへ追加できませんでした");
        button.classList.remove("is-loading");
        button.classList.add("is-added");
        button.querySelector("[aria-hidden='true']").textContent = payload.pending?.length ? "…" : "✓";
        button.title = payload.pending?.length ? "銘柄確認待ち" : "ウォッチリスト追加済み";
        message.classList.add("success");
        message.textContent = `${payload.message}。画面を更新します…`;
        window.setTimeout(() => window.location.reload(), 700);
      } catch (error) {
        button.disabled = false;
        button.classList.remove("is-loading");
        button.querySelector("[aria-hidden='true']").textContent = "＋";
        message.classList.add("error");
        message.textContent = error.message;
      }
    });
  });
  document.getElementById("symbol-registration-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = document.getElementById("registration-symbol");
    const message = document.getElementById("registration-message");
    const submit = event.currentTarget.querySelector("button[type='submit']");
    message.className = "registration-message";
    message.textContent = "登録を受け付けています…";
    submit.disabled = true;
    try {
      const symbols = input.value
        .split(/[\s,、]+/)
        .map((value) => value.trim().toUpperCase())
        .filter(Boolean);
      const response = await fetch(`/api/v1/watchlists/${encodeURIComponent("ウォッチ")}/items/bulk`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbols }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "登録できませんでした");
      message.classList.add("success");
      message.textContent = payload.message;
      window.setTimeout(() => window.location.reload(), 900);
    } catch (error) {
      message.classList.add("error");
      message.textContent = error.message;
      submit.disabled = false;
    }
  });
  document.getElementById("position-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = document.getElementById("position-message");
    const submit = event.currentTarget.querySelector("button[type='submit']");
    message.className = "registration-message";
    message.textContent = "保有情報を保存しています…";
    submit.disabled = true;
    try {
      const response = await fetch("/api/v1/portfolio/positions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          symbol: document.getElementById("position-symbol").value.trim(),
          quantity: document.getElementById("position-quantity").value,
          average_cost: document.getElementById("position-average-cost").value || null,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "保存できませんでした");
      message.classList.add("success");
      message.textContent = payload.message;
      window.setTimeout(() => window.location.reload(), 700);
    } catch (error) {
      message.classList.add("error");
      message.textContent = error.message;
      submit.disabled = false;
    }
  });
  document.querySelectorAll(".period-controls button").forEach((button) => {
    button.addEventListener("click", () => {
      selectedRange = button.dataset.range;
      document.querySelectorAll(".period-controls button").forEach((item) => {
        item.classList.toggle("active", item === button);
      });
      loadChart();
    });
  });
  document.querySelectorAll(".horizon-controls button").forEach((button) => {
    button.addEventListener("click", () => {
      selectedHorizon = Number(button.dataset.horizon);
      document.querySelectorAll(".horizon-controls button").forEach((item) => {
        item.classList.toggle("active", item === button);
      });
      loadPrediction();
    });
  });
  document.getElementById("custom-range-form").addEventListener("submit", (event) => {
    event.preventDefault();
    loadChart({
      from: document.getElementById("from-date").value,
      to: document.getElementById("to-date").value,
    });
  });

  const initialButton = document.querySelector(`.watchlist-item[data-symbol="${CSS.escape(selectedSymbol)}"]`);
  if (initialButton) selectInstrument(initialButton);
})();
