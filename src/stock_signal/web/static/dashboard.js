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
  const horizonGuideTitle = document.getElementById("horizon-guide-title");
  const horizonGuideHolding = document.getElementById("horizon-guide-holding");
  const horizonGuidePurpose = document.getElementById("horizon-guide-purpose");
  const horizonGuideCaution = document.getElementById("horizon-guide-caution");
  const indicatorProfileLabel = document.getElementById("indicator-profile-label");
  const indicatorSummary = document.getElementById("indicator-summary");
  const aiReviewRunButton = document.getElementById("ai-review-run");
  const aiReviewTarget = document.getElementById("ai-review-target");
  const aiReviewProfile = document.getElementById("ai-review-profile");
  const aiReviewResult = document.getElementById("ai-review-result");
  const openaiReviewEnabled = body.dataset.openaiReviewEnabled === "true";
  const openaiModel = body.dataset.openaiModel;
  let selectedSymbol = body.dataset.selectedSymbol;
  let selectedProvider = body.dataset.selectedProvider || null;
  let selectedRange = "3m";
  let selectedHorizon = 5;
  let currentChartPayload = null;
  let currentPositionSupports = [];
  let aiReviewController = null;
  const chartIndicatorVisibility = {
    "moving-average": true,
    rsi: true,
    resistance: true,
  };

  const horizonProfiles = {
    5: {
      label: "スイング",
      future_label: "5営業日先",
      holding_period: "数日〜数週間",
      purpose: "転換初動やブレイク後の、新規購入タイミングを確認します。",
      caution: "1日だけの値動きで追わず、出来高と無効化水準も併せて確認します。",
      moving_average_windows: [5, 20],
      rsi_window: 14,
      resistance_lookback: 60,
    },
    20: {
      label: "中長期の買い場",
      future_label: "20営業日先",
      holding_period: "数週間〜数か月",
      purpose: "中期上昇トレンド内の押し目、支持帯の維持、日足反発を確認します。",
      caution: "支持帯への接触だけでは買いにせず、反発と業績・財務を別に確認します。",
      moving_average_windows: [20, 60],
      rsi_window: 28,
      resistance_lookback: 120,
    },
  };

  function updateHorizonGuide(profile = horizonProfiles[selectedHorizon]) {
    horizonGuideTitle.textContent = `${profile.label}・${profile.future_label}`;
    horizonGuideHolding.textContent = profile.holding_period;
    horizonGuidePurpose.textContent = profile.purpose;
    horizonGuideCaution.textContent = profile.caution;
    indicatorProfileLabel.textContent = `${profile.label}基準`;
  }

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

  const movingAverageColors = {
    5: "#c77932",
    20: "#2f6da1",
    60: "#76539a",
  };

  async function renderChart(payload) {
    const bars = payload?.bars || [];
    if (!bars.length) throw new Error("指定期間の日足データがありません");
    const profile = horizonProfiles[selectedHorizon];
    const dates = bars.map((bar) => bar.trade_date);
    const traces = [{
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
    }, {
      type: "bar",
      x: dates,
      y: bars.map((bar) => bar.volume),
      marker: { color: "#8b98a5" },
      name: "出来高",
      xaxis: "x",
      yaxis: "y2",
      hovertemplate: "%{x}<br>出来高 %{y:,}<extra></extra>",
    }];
    const indicators = payload.indicators || {};
    if (chartIndicatorVisibility["moving-average"]) {
      profile.moving_average_windows.forEach((window) => {
        const values = indicators.moving_averages?.[String(window)] || [];
        traces.push({
          type: "scatter",
          mode: "lines",
          x: dates,
          y: values,
          name: `${window}日移動平均`,
          line: { color: movingAverageColors[window], width: 1.7 },
          connectgaps: false,
          xaxis: "x",
          yaxis: "y",
          hovertemplate: `%{x}<br>${window}日線 %{y:.2f}<extra></extra>`,
        });
      });
    }

    const shapes = [];
    const annotations = [];
    const resistance = chartIndicatorVisibility.resistance
      ? indicators.resistance_bands?.[String(profile.resistance_lookback)] || []
      : [];
    resistance.forEach((band, index) => {
      const firstVisibleDate = band.first_touched > dates[0]
        ? band.first_touched
        : dates[0];
      shapes.push({
        type: "rect",
        xref: "x",
        yref: "y",
        x0: firstVisibleDate,
        x1: dates.at(-1),
        y0: band.lower,
        y1: band.upper,
        line: { color: "rgba(183,59,59,.48)", width: 1, dash: "dot" },
        fillcolor: "rgba(183,59,59,.08)",
        layer: "below",
      });
      annotations.push({
        xref: "x",
        yref: "y",
        x: dates.at(-1),
        y: band.center,
        text: `抵抗帯${index + 1} ${formatPrice(band.lower)}〜${formatPrice(band.upper)}・${band.touches}回`,
        showarrow: false,
        xanchor: "right",
        yanchor: "bottom",
        bgcolor: "rgba(255,255,255,.82)",
        bordercolor: "rgba(183,59,59,.3)",
        font: { color: "#8f3030", size: 9 },
      });
    });
    const positionSupports = selectedHorizon === 20
      ? currentPositionSupports
      : [];
    positionSupports.forEach((support, index) => {
      shapes.push({
        type: "rect",
        xref: "x",
        yref: "y",
        x0: dates[0],
        x1: dates.at(-1),
        y0: support.lower,
        y1: support.upper,
        line: { color: "rgba(47,109,161,.55)", width: 1, dash: "dot" },
        fillcolor: support.touched
          ? "rgba(47,109,161,.16)"
          : "rgba(47,109,161,.07)",
        layer: "below",
      });
      annotations.push({
        xref: "x",
        yref: "y",
        x: dates[0],
        y: support.center ?? support.level,
        text: `支持候補${index + 1} ${escapeHtml(support.label)}`,
        showarrow: false,
        xanchor: "left",
        yanchor: "top",
        bgcolor: "rgba(255,255,255,.82)",
        bordercolor: "rgba(47,109,161,.3)",
        font: { color: "#2f6da1", size: 9 },
      });
    });

    const showRsi = chartIndicatorVisibility.rsi;
    if (showRsi) {
      traces.push({
        type: "scatter",
        mode: "lines",
        x: dates,
        y: indicators.rsi?.[String(profile.rsi_window)] || [],
        name: `RSI ${profile.rsi_window}日`,
        line: { color: "#80651c", width: 1.6 },
        connectgaps: false,
        xaxis: "x",
        yaxis: "y3",
        hovertemplate: `%{x}<br>RSI %{y:.1f}<extra></extra>`,
      });
      [30, 70].forEach((level) => shapes.push({
        type: "line",
        xref: "x",
        yref: "y3",
        x0: dates[0],
        x1: dates.at(-1),
        y0: level,
        y1: level,
        line: { color: "rgba(128,101,28,.45)", width: 1, dash: "dot" },
      }));
    }

    const layout = {
      margin: { l: 64, r: 24, t: 42, b: 44 },
      paper_bgcolor: "#ffffff",
      plot_bgcolor: "#ffffff",
      showlegend: chartIndicatorVisibility["moving-average"] || showRsi,
      legend: { orientation: "h", x: 0, y: 1.08, font: { size: 10 } },
      hovermode: "x unified",
      shapes,
      annotations,
      height: showRsi ? 660 : 540,
      xaxis: {
        rangeslider: { visible: false },
        anchor: showRsi ? "y3" : "y2",
        gridcolor: "#edf0f2",
      },
      yaxis: {
        domain: showRsi ? [0.46, 1] : [0.28, 1],
        title: "価格",
        gridcolor: "#edf0f2",
      },
      yaxis2: {
        domain: showRsi ? [0.25, 0.38] : [0, 0.2],
        title: "出来高",
        gridcolor: "#edf0f2",
      },
      yaxis3: showRsi ? {
        domain: [0, 0.17],
        title: `RSI ${profile.rsi_window}`,
        range: [0, 100],
        tickvals: [30, 50, 70],
        gridcolor: "#edf0f2",
      } : undefined,
    };
    await Plotly.react(chartElement, traces, layout, {
      responsive: true,
      displaylogo: false,
      locale: "ja",
    });
    const averageLabel = profile.moving_average_windows
      .map((window) => `${window}日線`)
      .join("・");
    const enabledDescriptions = [];
    if (chartIndicatorVisibility["moving-average"]) enabledDescriptions.push(averageLabel);
    if (showRsi) enabledDescriptions.push(`RSI ${profile.rsi_window}日`);
    if (chartIndicatorVisibility.resistance) {
      enabledDescriptions.push(
        `${profile.resistance_lookback}営業日の抵抗帯候補 ${resistance.length}件`,
      );
    }
    if (positionSupports.length) {
      enabledDescriptions.push(`中長期の支持候補 ${positionSupports.length}件`);
    }
    indicatorSummary.textContent = enabledDescriptions.length
      ? `${enabledDescriptions.join("、")}を表示中。支持・抵抗帯は確定的な価格ではありません。`
      : "追加指標は非表示です。";
  }

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
      currentChartPayload = payload;
      await renderChart(payload);
      priceElement.textContent = formatPrice(bars.at(-1).close);
      sourceElement.textContent = `取得元: ${payload.source.provider} ・ ${
        payload.source.is_adjusted ? "調整済み" : "未調整"
      } ・ 最新 ${payload.freshness.latest_trade_date}`;
      rangeLabel.textContent = `${payload.range.from} 〜 ${payload.range.to}`;
      document.getElementById("from-date").value = payload.range.from;
      document.getElementById("to-date").value = payload.range.to;
    } catch (error) {
      currentChartPayload = null;
      errorElement.textContent = error.message;
      errorElement.hidden = false;
      Plotly.purge(chartElement);
    }
  }

  function rerenderCurrentChart() {
    if (!currentChartPayload) return;
    void renderChart(currentChartPayload).catch((error) => {
      errorElement.textContent = error.message;
      errorElement.hidden = false;
    });
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
    const requestedSymbol = selectedSymbol;
    const requestedProvider = selectedProvider;
    const requestedHorizon = selectedHorizon;
    const response = await fetch(
      `/api/v1/instruments/${encodeURIComponent(requestedSymbol)}/analysis/latest?horizon=${requestedHorizon}${requestedProvider ? `&provider=${encodeURIComponent(requestedProvider)}` : ""}`,
    );
    const payload = await response.json();
    if (
      requestedSymbol !== selectedSymbol
      || requestedProvider !== selectedProvider
      || requestedHorizon !== selectedHorizon
    ) return;
    if (!response.ok || payload.status !== "ready") {
      currentPositionSupports = [];
      rerenderCurrentChart();
      predictionElement.className = "prediction-empty";
      predictionElement.textContent = payload.message || payload.detail || "分析結果を取得できませんでした";
      return;
    }
    predictionElement.className = "analysis-result";
    const horizonProfile = payload.horizon_profile || horizonProfiles[selectedHorizon];
    updateHorizonGuide(horizonProfile);
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
    const positionEntry = payload.position_entry;
    currentPositionSupports = positionEntry?.supports || [];
    rerenderCurrentChart();
    const riskAssessment = positionEntry?.invalidation_price != null
      ? positionEntry
      : transition;
    const positionRiskPerShare = riskAssessment
      ? Number(riskAssessment.current_price) - Number(riskAssessment.invalidation_price)
      : Number.NaN;
    const positionSizePanel = Number.isFinite(positionRiskPerShare) && positionRiskPerShare > 0
      ? `<section class="position-size-calculator" data-current-price="${riskAssessment.current_price}" data-invalidation-price="${riskAssessment.invalidation_price}">
          <div><span>ポジションサイズ計算</span><strong>許容損失から購入上限を確認</strong></div>
          <label>この取引で許容する損失額
            <span><input class="position-risk-input" type="number" min="0" step="1000" inputmode="numeric" placeholder="例：50000"> 円</span>
          </label>
          <dl>
            <div><dt>1株あたりリスク</dt><dd>${formatPrice(positionRiskPerShare)}円</dd></div>
            <div><dt>理論上限</dt><dd class="position-theoretical">—</dd></div>
            <div><dt>100株単位の参考上限</dt><dd class="position-lot-size">—</dd></div>
            <div><dt>必要資金の目安</dt><dd class="position-capital">—</dd></div>
          </dl>
          <p>手数料、スリッページ、ギャップ損失は含みません。購入推奨株数ではありません。銘柄ごとの実際の売買単位も注文前に確認してください。</p>
        </section>`
      : "";
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
    const positionConditions = positionEntry ? positionEntry.conditions.map((condition) => `
      <li class="position-entry-condition ${condition.satisfied ? "satisfied" : "pending"}">
        <span aria-hidden="true">${condition.satisfied ? "✓" : "○"}</span>
        <div><strong>${escapeHtml(condition.label)}</strong><p>${escapeHtml(condition.description)}</p></div>
      </li>`).join("") : "";
    const supportCards = positionEntry ? positionEntry.supports.map((support) => `
      <li class="support-level ${support.touched ? "touched" : "nearby"} ${support.held ? "held" : "broken"}">
        <div><strong>${escapeHtml(support.label)}</strong><span>${support.touched ? (support.held ? "接触・維持" : "接触・割れ") : "接近度"}</span></div>
        <dl>
          <div><dt>中心</dt><dd>${formatPrice(support.level)}</dd></div>
          <div><dt>候補帯</dt><dd>${formatPrice(support.lower)}〜${formatPrice(support.upper)}</dd></div>
          <div><dt>現在値との差</dt><dd>${Number(support.distance_atr).toFixed(2)} ATR</dd></div>
        </dl>
      </li>`).join("") : "";
    const positionEntryPanel = positionEntry ? `
      <section class="position-entry-assessment ${positionEntry.phase}">
        <div class="position-entry-heading">
          <div><span>中長期ポジションの買い場</span><strong>${escapeHtml(positionEntry.phase_label)}</strong></div>
          <div class="position-entry-progress"><span>条件進捗</span><strong>${positionEntry.satisfied_conditions} / ${positionEntry.total_conditions}</strong><small>確率ではありません</small></div>
        </div>
        <p>${escapeHtml(positionEntry.summary)}</p>
        ${positionEntry.next_condition ? `<div class="next-condition"><span>次に必要な条件</span><strong>${escapeHtml(positionEntry.next_condition.label)}</strong><p>${escapeHtml(positionEntry.next_condition.description)}</p></div>` : ""}
        <dl class="position-entry-levels">
          <div><dt>現在値</dt><dd>${formatPrice(positionEntry.current_price)}</dd></div>
          <div><dt>ATR(20)</dt><dd>${formatPrice(positionEntry.atr)}</dd></div>
          <div><dt>参考無効化水準</dt><dd>${positionEntry.invalidation_price == null ? "接触後に表示" : formatPrice(positionEntry.invalidation_price)}</dd></div>
        </dl>
        <h4>支持候補</h4>
        <ul class="support-level-list">${supportCards}</ul>
        <h4>購入候補までの条件</h4>
        <ul class="position-entry-condition-list">${positionConditions}</ul>
        <small>支持帯は買いを保証しません。中期トレンド、終値維持、反発を同時に確認します。</small>
      </section>` : "";
    predictionElement.innerHTML = `
      <section class="investment-decision ${decision.action}">
        <div><span>投資検討区分</span><strong>${actionLabels[decision.action]}</strong></div>
        <div class="evidence-score"><span>根拠の強さ</span><strong>${decision.evidence_score.toFixed(1)}</strong><small>確率ではありません</small></div>
        <p>${escapeHtml(decision.summary)}</p>
        <ul>${reasons}</ul>
      </section>
      ${positionEntryPanel || transitionPanel}
      ${positionSizePanel}
      <div class="analysis-summary">
        <div><span>テクニカル方向</span><strong class="decision ${payload.direction}">${directionLabels[payload.direction]}</strong></div>
        <small>${escapeHtml(horizonProfile.label)}・${escapeHtml(horizonProfile.future_label)} ／ 基準日 ${payload.as_of_date} ・ ${payload.engine.id} v${payload.engine.version}</small>
      </div>
      <p class="direction-explanation">${escapeHtml(horizonProfile.purpose)} テクニカル方向は価格の向き、投資検討区分は出来高・決算・市場環境も含む判断です。</p>
      <div class="probability-grid">${scores}</div>
      <h4>チャートパターンと現在の有効性</h4>
      <div class="pattern-list">${patterns}</div>
      <h4>個別株固有の確認</h4>
      <ul class="check-list">${checks}</ul>
      <h4>判定要因</h4>
      <ul class="factor-list">${factors}</ul>
      <aside class="engine-validation-status">
        <div><span>エンジン信頼度レポート</span><strong>検証実績は未生成</strong></div>
        <p>勝率・プロフィットファクター・最大ドローダウンは、時系列のウォークフォワード検証を保存した後に表示します。根拠の強さは過去成績ではありません。</p>
      </aside>
      <details class="caution-list"><summary>未評価項目と注意事項</summary><ul>${cautions}</ul></details>
      <p class="analysis-disclaimer">各スコアは適用されたルールの重みを正規化した参考値で、上昇・停滞・下落の確率ではありません。</p>`;
    const sizeCalculator = predictionElement.querySelector(".position-size-calculator");
    const riskInput = sizeCalculator?.querySelector(".position-risk-input");
    riskInput?.addEventListener("input", () => {
      const allowedLoss = Number(riskInput.value);
      const currentPrice = Number(sizeCalculator.dataset.currentPrice);
      const invalidationPrice = Number(sizeCalculator.dataset.invalidationPrice);
      const riskPerShare = currentPrice - invalidationPrice;
      const theoretical = allowedLoss > 0 && riskPerShare > 0
        ? Math.floor(allowedLoss / riskPerShare)
        : 0;
      const lotSize = Math.floor(theoretical / 100) * 100;
      sizeCalculator.querySelector(".position-theoretical").textContent = theoretical
        ? `${theoretical.toLocaleString("ja-JP")}株`
        : "—";
      sizeCalculator.querySelector(".position-lot-size").textContent = lotSize
        ? `${lotSize.toLocaleString("ja-JP")}株`
        : "100株未満";
      sizeCalculator.querySelector(".position-capital").textContent = lotSize
        ? `${Math.ceil(lotSize * currentPrice).toLocaleString("ja-JP")}円`
        : "—";
    });
  }

  function resetAiReview() {
    aiReviewController?.abort();
    aiReviewController = null;
    const instrumentName = nameElement.textContent.trim();
    aiReviewTarget.textContent = selectedSymbol
      ? `${selectedSymbol}${instrumentName ? ` ${instrumentName}` : ""}`
      : "銘柄を選択してください";
    const profile = horizonProfiles[selectedHorizon];
    aiReviewProfile.textContent = `${profile.label}・${profile.future_label} ／ ${openaiModel}`;
    aiReviewRunButton.disabled = !openaiReviewEnabled || !selectedSymbol;
    aiReviewRunButton.textContent = "最新情報を検索して確認";
    aiReviewResult.className = "ai-review-empty";
    aiReviewResult.textContent = openaiReviewEnabled
      ? "AIの確認結果はまだありません。実行前にルールベースの投資判断材料を確認してください。"
      : "OPENAI_API_KEYを設定すると、この銘柄の最新情報を検索できます。";
  }

  function safeExternalUrl(value) {
    try {
      const parsed = new URL(value);
      return ["http:", "https:"].includes(parsed.protocol) ? parsed.href : null;
    } catch (_) {
      return null;
    }
  }

  function renderAiReview(payload) {
    const report = document.createElement("article");
    report.className = "ai-review-report";

    const meta = document.createElement("div");
    meta.className = "ai-review-report-meta";
    const generatedAt = new Date(payload.generated_at).toLocaleString("ja-JP");
    [
      `${payload.symbol} ${payload.display_name}`,
      `日足基準 ${payload.technical_as_of_date}`,
      `調査日時 ${generatedAt}`,
      `モデル ${payload.model}`,
    ].forEach((label) => {
      const item = document.createElement("span");
      item.textContent = label;
      meta.append(item);
    });
    report.append(meta);

    const reportText = document.createElement("p");
    reportText.className = "ai-review-report-text";
    (payload.report_segments || [{ text: payload.report_text }]).forEach((segment) => {
      const url = segment.citation ? safeExternalUrl(segment.citation.url) : null;
      if (!url) {
        reportText.append(document.createTextNode(segment.text));
        return;
      }
      const link = document.createElement("a");
      link.href = url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.title = segment.citation.title || "参照情報を開く";
      link.textContent = segment.text || "[出典]";
      reportText.append(link);
    });
    report.append(reportText);

    const uniqueSources = new Map();
    (payload.citations || []).forEach((citation) => {
      const url = safeExternalUrl(citation.url);
      if (url && !uniqueSources.has(url)) uniqueSources.set(url, citation.title || url);
    });
    if (uniqueSources.size) {
      const sources = document.createElement("section");
      sources.className = "ai-review-sources";
      const title = document.createElement("h4");
      title.textContent = `参照情報 ${uniqueSources.size}件`;
      const list = document.createElement("ol");
      uniqueSources.forEach((sourceTitle, url) => {
        const item = document.createElement("li");
        const link = document.createElement("a");
        link.href = url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = sourceTitle;
        item.append(link);
        list.append(item);
      });
      sources.append(title, list);
      report.append(sources);
    } else {
      const citationWarning = document.createElement("p");
      citationWarning.className = "ai-review-disclaimer";
      citationWarning.textContent = "引用付きの関連情報は取得できませんでした。未確認事項として扱ってください。";
      report.append(citationWarning);
    }

    const disclaimer = document.createElement("p");
    disclaimer.className = "ai-review-disclaimer";
    disclaimer.textContent = payload.notice;
    report.append(disclaimer);
    aiReviewResult.replaceChildren(report);
    aiReviewResult.className = "ai-review-result";
  }

  aiReviewRunButton.addEventListener("click", async () => {
    if (!openaiReviewEnabled || !selectedSymbol) return;
    const requestedSymbol = selectedSymbol;
    const requestedHorizon = selectedHorizon;
    const controller = new AbortController();
    aiReviewController?.abort();
    aiReviewController = controller;
    aiReviewRunButton.disabled = true;
    aiReviewRunButton.textContent = "検索・照合しています…";
    aiReviewResult.className = "ai-review-loading";
    aiReviewResult.textContent = "公式発表と関連報道を検索しています。数十秒かかる場合があります。";
    const parameters = new URLSearchParams({ horizon: String(requestedHorizon) });
    if (selectedProvider) parameters.set("provider", selectedProvider);
    try {
      const response = await fetch(
        `/api/v1/instruments/${encodeURIComponent(requestedSymbol)}/ai-investment-review?${parameters}`,
        { method: "POST", signal: controller.signal },
      );
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "AI最終確認を取得できませんでした");
      if (requestedSymbol !== selectedSymbol || requestedHorizon !== selectedHorizon) return;
      renderAiReview(payload);
    } catch (error) {
      if (error.name === "AbortError") return;
      aiReviewResult.className = "ai-review-error";
      aiReviewResult.textContent = error.message;
    } finally {
      if (aiReviewController === controller) {
        aiReviewController = null;
        aiReviewRunButton.disabled = !openaiReviewEnabled || !selectedSymbol;
        aiReviewRunButton.textContent = "最新情報を検索して確認";
      }
    }
  });

  function selectInstrument(button) {
    selectedSymbol = button.dataset.symbol;
    selectedProvider = button.dataset.provider || null;
    currentPositionSupports = [];
    symbolElement.textContent = selectedSymbol;
    nameElement.textContent = button.dataset.name || "";
    resetAiReview();
    document.querySelectorAll(".watchlist-item").forEach((item) => {
      item.classList.toggle("selected", item.dataset.symbol === selectedSymbol);
    });
    document.querySelectorAll(".signal-item").forEach((item) => {
      item.classList.toggle("selected", item.dataset.symbol === selectedSymbol);
    });
    loadChart();
    loadPrediction();
  }

  function reloadWithSelectedSymbol(symbol) {
    const url = new URL(window.location.href);
    url.searchParams.set("symbol", symbol);
    window.location.assign(url.toString());
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
        message.textContent = payload.pending?.length
          ? `${payload.message}。銘柄マスタ同期後に分析できます。`
          : `${payload.message}。保存済みの日足から分析を表示します…`;
        if (!payload.pending?.length) {
          window.setTimeout(() => reloadWithSelectedSymbol(button.dataset.symbol), 700);
        }
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
      message.textContent = payload.added?.length
        ? `${payload.message}。保存済みの日足から分析を表示します…`
        : payload.message;
      if (payload.added?.length) {
        window.setTimeout(() => reloadWithSelectedSymbol(payload.added[0]), 900);
      }
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
  document.querySelectorAll("[data-chart-indicator]").forEach((input) => {
    input.addEventListener("change", () => {
      chartIndicatorVisibility[input.dataset.chartIndicator] = input.checked;
      rerenderCurrentChart();
    });
  });
  document.querySelectorAll(".horizon-controls button").forEach((button) => {
    button.addEventListener("click", () => {
      selectedHorizon = Number(button.dataset.horizon);
      currentPositionSupports = [];
      document.querySelectorAll(".horizon-controls button").forEach((item) => {
        item.classList.toggle("active", item === button);
        item.setAttribute("aria-pressed", String(item === button));
      });
      updateHorizonGuide();
      loadPrediction();
      rerenderCurrentChart();
      resetAiReview();
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
  updateHorizonGuide();
  if (initialButton) selectInstrument(initialButton);
  else resetAiReview();
})();
