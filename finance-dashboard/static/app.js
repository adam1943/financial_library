const state = {
  summary: null,
  candidates: [],
  reports: null,
  portfolio: null,
  fundAnalyst: null,
  afterClose: null,
  config: null,
  configPath: null,
  sourceTemplates: [],
  providerTemplates: [],
  sourceCapabilities: [],
  view: "overview",
  showAttempt: false,
  autoUpdateAttempted: false,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

const fmt = (value, digits = 2) => {
  if (value === null || value === undefined || value === "") return "-";
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  return number.toFixed(digits);
};

const escapeHtml = (value) =>
  String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      if (payload.error) message = payload.error;
    } catch (_error) {
      // Keep the HTTP status when a failed response has no JSON body.
    }
    throw new Error(message);
  }
  return response.json();
}

function applyConfigPayload(payload) {
  state.config = payload.config || {};
  state.configPath = payload.path || null;
  state.sourceTemplates = payload.source_templates || [];
  state.providerTemplates = payload.provider_templates || [];
  state.sourceCapabilities = payload.source_capabilities || [];
}

async function loadAll() {
  const [summary, candidates, config, portfolio, fundAnalyst, afterClose] = await Promise.all([
    fetchJson("/api/summary"),
    fetchJson("/api/candidates"),
    fetchJson("/api/config"),
    fetchJson("/api/portfolio"),
    fetchJson("/api/fund-analyst"),
    fetchJson("/api/after-close"),
  ]);
  state.summary = summary;
  state.candidates = candidates.candidates || [];
  state.portfolio = portfolio;
  state.fundAnalyst = fundAnalyst;
  state.afterClose = afterClose;
  applyConfigPayload(config);
  render();
  maybeAutoUpdate();
  maybeAutoAfterClose();
}

function render() {
  renderMeta();
  renderOverview();
  renderCandidates();
  renderReports();
  renderConfig();
  renderPortfolio();
  renderFundAnalyst();
  renderAfterClose();
}

function renderMeta() {
  const meta = state.summary?.meta || {};
  $("#lastUpdated").textContent = meta.latest_mtime ? `更新 ${meta.latest_mtime}` : "尚未更新";
  const running = state.summary?.update_state?.running;
  const status = state.summary?.update_state;
  $("#runStatus").textContent = running
    ? `${status.mode === "offline" ? "离线" : "联网"}更新中`
    : status?.finished_at
      ? `上次 ${status.returncode === 0 ? "成功" : "失败"} ${status.finished_at}`
      : "空闲";
}

function renderOverview() {
  const summary = state.summary;
  if (!summary) return;
  $("#metricCandidates").textContent = summary.counts.candidates;
  $("#metricAssets").textContent = `${summary.counts.stocks || 0} / ${summary.counts.funds || 0}`;
  $("#metricSupported").textContent = summary.counts.supported || 0;
  $("#metricSourceFail").textContent = summary.counts.source_failures || 0;
  $("#candidateCount").textContent = `${summary.counts.watchlist} 个观察标的 · ${summary.counts.source_items} 条舆情`;
  $("#briefMeta").textContent = summary.meta?.latest_mtime || "";

  const priorityCandidates = summary.priority_candidates?.length ? summary.priority_candidates : (summary.top_candidates || []);
  const leaders = priorityCandidates.filter((item) => item.action !== "暂缓").slice(0, 3);
  const heatWords = summary.heat_keywords || [];
  const failures = summary.counts.source_failures || 0;
  $("#briefList").innerHTML = [
    {
      label: "优先研究",
      value: leaders.length ? leaders.map((item) => `${item.name || item.symbol}(${item.action || inferClientAction(item)})`).join("、") : "暂无高支撑候选",
      tone: "good",
    },
    {
      label: "舆情热词",
      value: heatWords.length ? heatWords.slice(0, 4).map((item) => `${item.name} ${fmt(item.heat_score, 0)}`).join(" / ") : "暂无热词",
      tone: "info",
    },
    {
      label: "数据状态",
      value: failures ? `${failures} 个当前来源失败，已保留历史缓存` : "当前来源全部正常",
      tone: failures ? "warn" : "good",
    },
  ].map((item) => `
    <div class="brief-item ${item.tone}">
      <span>${escapeHtml(item.label)}</span>
      <strong>${escapeHtml(item.value)}</strong>
    </div>
  `).join("");

  $("#heatKeywordList").innerHTML = heatWords.slice(0, 5)
    .map((item) => `
      <div class="heat-keyword">
        <div>
          <strong>${escapeHtml(item.name)}</strong>
          <span>${escapeHtml(item.type === "symbol" ? "标的" : "主题")} · ${item.count} 次 · ${item.source_count} 源</span>
          ${item.symbols?.length ? `<span>${item.symbols.slice(0, 3).map(escapeHtml).join(" / ")}</span>` : ""}
        </div>
        <b>${fmt(item.heat_score, 0)}</b>
      </div>`)
    .join("") || `<div class="empty">暂无热词</div>`;

  $("#priorityList").innerHTML = priorityCandidates
    .filter((row) => row.data_status === "quote_ok" || row.data_status === "quote_cached")
    .slice(0, 5)
    .map((row) => {
      const width = Math.max(0, Math.min(100, Number(row.total_score || 0)));
      return `
        <div class="priority-item">
          <div class="name-cell">
            <strong>${escapeHtml(row.name || row.symbol)} <span>${escapeHtml(row.symbol)}</span></strong>
            <span>${assetLabel(row.asset_type)} · ${statusLabel(row.data_status)}</span>
            <div class="mini-tags">
              <span class="action-chip ${actionClass(row.action || inferClientAction(row))}">${escapeHtml(row.action || inferClientAction(row))}</span>
              ${row.provider ? `<span class="tag">行情: ${escapeHtml(row.provider)}</span>` : ""}
              ${row.heat_score !== null && row.heat_score !== undefined ? `<span class="tag">热度 ${fmt(row.heat_score, 0)}</span>` : ""}
              ${(row.news_sources_list || []).map((src) => `<span class="tag">舆情: ${escapeHtml(src)}</span>`).join("")}
            </div>
            ${row.action_reason ? `<p class="micro-copy">${escapeHtml(row.action_reason)}</p>` : ""}
          </div>
          <div class="score-bar" aria-label="评分"><i style="--w:${width}%"></i></div>
          <div class="score-number">${fmt(row.total_score)}</div>
        </div>`;
    })
    .join("") || `<div class="empty">暂无候选项</div>`;

  renderQuality();
  renderNewsPreview();
  renderSentiment();

  $("#themeList").innerHTML = (summary.themes || []).slice(0, 4)
    .map((item) => `
      <div class="theme-row">
        <span class="theme-pill">${escapeHtml(item.name)}</span>
        <span class="muted">${item.count} 项 · 均分 ${fmt(item.avg_score)}</span>
      </div>`)
    .join("") || `<div class="empty">暂无主题</div>`;

  $("#pulseRows").innerHTML = (summary.market_pulse || []).slice(0, 6)
    .map((row) => `
      <tr>
        <td><strong>${escapeHtml(row.name || row.symbol)}</strong></td>
        <td class="num ${Number(row.change_pct || 0) >= 0 ? "positive" : "negative"}">${fmt(row.change_pct)}%</td>
      </tr>`)
    .join("") || `<tr><td colspan="2" class="empty">暂无市场脉冲</td></tr>`;

  $("#snapshotRows").innerHTML = (summary.snapshots || [])
    .map((row) => `
      <tr>
        <td><strong>${escapeHtml(row.name || row.symbol)}</strong><br><span class="muted">${escapeHtml(row.symbol)}</span></td>
        <td>${assetLabel(row.asset_type)}</td>
        <td class="num">${fmt(row.price, 3)}</td>
        <td class="num ${Number(row.change_pct || 0) >= 0 ? "positive" : "negative"}">${fmt(row.change_pct)}%</td>
        <td>${escapeHtml(row.provider || "-")}</td>
      </tr>`)
    .join("") || `<tr><td colspan="5" class="empty">暂无行情快照</td></tr>`;
}

function inferClientAction(row) {
  const risk = Number(row.risk_penalty || 0);
  const total = Number(row.total_score || 0);
  const mentions = Number(row.mention_count || 0);
  const change = Number(row.change_pct || 0);
  const hasQuote = ["quote_ok", "quote_cached"].includes(row.data_status);
  if (risk >= 16) return "谨慎回避";
  if (!hasQuote) return "仅作线索";
  if (total >= 68 && mentions >= 2 && change >= 0 && risk <= 8) return "积极跟踪";
  if (total >= 58 && risk <= 10) return "建仓观察";
  if (total >= 45) return "持续关注";
  return "暂缓";
}

function assetLabel(value) {
  return { stock: "股票", fund: "基金", index: "指数" }[value] || value || "-";
}

function statusLabel(value) {
  return {
    quote_ok: "有行情",
    quote_cached: "缓存行情",
    watchlist_only: "待验证",
    news_only: "仅舆情",
    latest_quote: "最新行情",
    candidate_price: "候选行情",
    manual_price: "手动现价",
    manual_amount: "手动金额",
    live_eastmoney: "即时行情",
    live_tushare: "TuShare日线",
    live_fundgz: "基金估值",
    live_quote: "即时行情",
    missing_price: "缺行情",
  }[value] || value || "未知";
}

function confidenceClass(row) {
  if (row.data_status === "quote_ok" && row.mention_count > 0) return "good";
  if (row.data_status === "quote_ok" || row.mention_count > 0) return "warn";
  return "low";
}

function renderQuality() {
  const summary = state.summary || {};
  const statuses = summary.source_status?.statuses || [];
  const failed = statuses.filter((item) => !item.ok);
  const statusCounts = summary.status_counts || {};
  $("#qualityList").innerHTML = `
    <div class="quality-row"><span>行情+舆情支撑</span><strong>${summary.counts?.supported || 0}</strong></div>
    <div class="quality-row"><span>有行情候选</span><strong>${statusCounts.quote_ok || 0}</strong></div>
    <div class="quality-row"><span>待验证候选</span><strong>${statusCounts.watchlist_only || 0}</strong></div>
    <div class="quality-row ${failed.length ? "bad" : "good"}"><span>失败来源</span><strong>${failed.length}</strong></div>
  `;
}

function renderNewsPreview() {
  const items = state.summary?.news_items || [];
  $("#newsPreview").innerHTML = items.slice(0, 3).map((item, index) => newsCard(item, index, true)).join("") || `<div class="empty">暂无舆情新闻</div>`;
}

function newsCard(item, index = 0, compact = false) {
  const themes = (item.themes || []).map((theme) => `<span class="tag">${escapeHtml(theme)}</span>`).join(" ");
  const symbols = (item.symbols || []).map((symbol) => `<span class="tag">${escapeHtml(symbol)}</span>`).join(" ");
  const risk = Number(item.risk_count || 0) > 0 ? `<span class="risk-badge">风险关键词 ${item.risk_count}</span>` : "";
  const heat = item.heat_score !== null && item.heat_score !== undefined ? `<span class="heat-badge">热度 ${fmt(item.heat_score, 0)}</span>` : "";
  const points = (item.key_points || []).slice(0, compact ? 1 : 3).map((point) => `<li>${escapeHtml(point)}</li>`).join("");
  return `
    <article class="news-item clickable ${compact ? "mini" : ""}" data-news-index="${index}" tabindex="0">
      <div class="news-top">
        <strong>${escapeHtml(item.title)}</strong>
        <div class="news-badges">${heat}${risk}</div>
      </div>
      ${points ? `<ul class="key-points">${points}</ul>` : `<p>${escapeHtml(item.summary || "")}</p>`}
      <div class="news-meta">
        <span>来源: ${escapeHtml(item.source_label || item.source || "-")}</span>
        <span>${escapeHtml(item.published_at || item.fetched_at || "")}</span>
      </div>
      <div class="mini-tags">${themes}${symbols}</div>
    </article>`;
}

function renderSentiment() {
  const summary = state.summary || {};
  const news = [...(summary.recent_news_items || summary.news_items || [])].sort((a, b) => Number(b.heat_score || 0) - Number(a.heat_score || 0));
  $("#newsCount").textContent = `${news.length} 条`;
  $("#newsList").innerHTML = news.map((item, index) => newsCard(item, index)).join("") || `<div class="empty">暂无舆情新闻</div>`;
  const statuses = summary.source_status?.statuses || [];
  $("#sourceStatusList").innerHTML = statuses.map((item) => `
      <div class="source-status ${item.ok ? "ok" : "fail"}">
        <div>
          <strong>${escapeHtml(item.name)}</strong>
          <p>${escapeHtml(item.detail || "")}</p>
        </div>
      <span>${item.ok ? "OK" : "FAIL"} · ${item.count || 0}</span>
    </div>`).join("") || `<div class="empty">暂无来源状态</div>`;
  const failedRecent = summary.source_status?.failed_history || [];
  if (failedRecent.length) {
    $("#sourceStatusList").insertAdjacentHTML("beforeend", `
      <div class="history-head">${escapeHtml(summary.source_status?.history_note || "历史波动，不代表当前失败")}</div>
      ${failedRecent.slice(0, 5).map((item) => `
        <div class="source-status fail">
          <div>
            <strong>${escapeHtml(item.name)}</strong>
            <p>${escapeHtml(item.detail || "")}</p>
          </div>
          <span>${escapeHtml(item.run_at || "")}</span>
        </div>`).join("")}
    `);
  }
  $("#riskList").innerHTML = (summary.risk_items || [])
    .map((item) => `
      <div class="risk-item">
        <strong>${escapeHtml(item.title)}</strong>
        <span class="muted">来源: ${escapeHtml(item.source || "")}</span>
        <p>${escapeHtml(item.summary || "")}</p>
      </div>`)
    .join("") || `<div class="empty">暂无明显风险关键词</div>`;
}

function renderCandidates() {
  const query = ($("#searchInput").value || "").trim().toLowerCase();
  const asset = $("#assetFilter").value;
  const theme = $("#themeFilter").value;
  const assets = [...new Set(state.candidates.map((row) => row.asset_type).filter(Boolean))].sort();
  const themes = [...new Set(state.candidates.flatMap((row) => row.themes_list || []))].sort();

  syncOptions($("#assetFilter"), assets, "全部类型");
  syncOptions($("#themeFilter"), themes, "全部主题");

  const rows = state.candidates.filter((row) => {
    const haystack = `${row.symbol} ${row.name} ${row.provider || ""} ${row.data_status || ""} ${(row.news_sources_list || []).join(" ")} ${(row.matched_news_list || []).join(" ")} ${(row.themes_list || []).join(" ")}`.toLowerCase();
    const matchQuery = !query || haystack.includes(query);
    const matchAsset = !asset || row.asset_type === asset;
    const matchTheme = !theme || (row.themes_list || []).includes(theme);
    return matchQuery && matchAsset && matchTheme;
  });

  $("#candidateRows").innerHTML = rows
    .map((row, index) => `
      <tr>
        <td class="num">${index + 1}</td>
        <td><strong>${escapeHtml(row.name || row.symbol)}</strong><br><span class="muted">${escapeHtml(row.symbol)}</span></td>
        <td><span class="type-chip">${assetLabel(row.asset_type)}</span></td>
        <td class="num">${fmt(row.price, 3)}</td>
        <td class="num ${Number(row.change_pct || 0) >= 0 ? "positive" : "negative"}">${fmt(row.change_pct)}%</td>
        <td class="num"><strong>${fmt(row.total_score)}</strong></td>
        <td><span class="action-chip ${actionClass(row.action || inferClientAction(row))}">${escapeHtml(row.action || inferClientAction(row))}</span></td>
        <td class="num">${fmt(row.heat_score, 0)}</td>
        <td><span class="status-chip ${confidenceClass(row)}">${statusLabel(row.data_status)} · ${escapeHtml(row.confidence || "-")}</span></td>
        <td>${escapeHtml(row.provider || "-")}</td>
        <td>${(row.news_sources_list || []).map((item) => `<span class="tag">${escapeHtml(item)}</span>`).join(" ") || "-"}</td>
        <td class="num">${fmt(row.sentiment_score)}</td>
        <td class="num">${fmt(row.momentum_score)}</td>
        <td class="num">${fmt(row.method_fit_score)}</td>
        <td class="num ${Number(row.risk_penalty || 0) > 0 ? "risk-text" : ""}">${fmt(row.risk_penalty)}</td>
        <td>${(row.themes_list || []).map((item) => `<span class="tag">${escapeHtml(item)}</span>`).join(" ") || "-"}</td>
        <td class="news-cell">${(row.matched_news_list || []).slice(0, 2).map((item) => `<div>${escapeHtml(item)}</div>`).join("") || "-"}</td>
      </tr>`)
    .join("") || `<tr><td colspan="17" class="empty">没有匹配的候选项</td></tr>`;
}

function actionClass(action) {
  if (action === "积极跟踪" || action === "建仓观察") return "good";
  if (action === "持续关注" || action === "仅作线索") return "watch";
  if (action === "谨慎回避") return "risk";
  return "neutral";
}

function syncOptions(select, values, firstLabel) {
  const current = select.value;
  const html = [`<option value="">${firstLabel}</option>`]
    .concat(values.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`))
    .join("");
  if (select.innerHTML !== html) select.innerHTML = html;
  if (values.includes(current)) select.value = current;
}

function renderReports() {
  if (!state.reports) return;
  const markdown = state.showAttempt ? state.reports.latest_attempt : state.reports.latest;
  $("#reportContent").innerHTML = markdownToHtml(markdown || "暂无报告");
  $("#showAttempt").textContent = state.showAttempt ? "查看有效报告" : "查看最近尝试";
  $("#reportList").innerHTML = (state.reports.reports || [])
    .map((report) => `
      <div class="report-item">
        <button data-report="${escapeHtml(report.name)}">
          <strong>${escapeHtml(report.name)}</strong><br>
          <span class="muted">${escapeHtml(report.mtime)}${report.failed ? " · 失败尝试" : ""}</span>
        </button>
      </div>`)
    .join("") || `<div class="empty">暂无历史报告</div>`;
}

function markdownToHtml(markdown) {
  const lines = String(markdown || "").split(/\r?\n/);
  const html = [];
  let inTable = false;
  let inList = false;
  let tableHeaders = [];

  const closeList = () => {
    if (inList) html.push("</ul>");
    inList = false;
  };
  const closeTable = () => {
    if (inTable) html.push("</tbody></table></div>");
    inTable = false;
    tableHeaders = [];
  };

  for (const line of lines) {
    if (/^\|.*\|$/.test(line.trim())) {
      closeList();
      const cells = line.trim().slice(1, -1).split("|").map((cell) => cell.trim());
      if (cells.every((cell) => /^:?-{3,}:?$/.test(cell))) continue;
      if (!inTable) {
        inTable = true;
        tableHeaders = cells;
        html.push("<div class=\"table-wrap\"><table><thead><tr>");
        html.push(cells.map((cell) => `<th>${inlineMd(cell)}</th>`).join(""));
        html.push("</tr></thead><tbody>");
      } else {
        html.push("<tr>");
        html.push(cells.map((cell) => `<td>${inlineMd(cell)}</td>`).join(""));
        html.push("</tr>");
      }
      continue;
    }
    closeTable();
    if (line.startsWith("# ")) {
      closeList();
      html.push(`<h1>${inlineMd(line.slice(2))}</h1>`);
    } else if (line.startsWith("## ")) {
      closeList();
      html.push(`<h2>${inlineMd(line.slice(3))}</h2>`);
    } else if (line.startsWith("- ")) {
      if (!inList) {
        html.push("<ul>");
        inList = true;
      }
      html.push(`<li>${inlineMd(line.slice(2))}</li>`);
    } else if (line.startsWith("> ")) {
      closeList();
      html.push(`<blockquote>${inlineMd(line.slice(2))}</blockquote>`);
    } else if (line.trim()) {
      closeList();
      html.push(`<p>${inlineMd(line)}</p>`);
    } else {
      closeList();
    }
  }
  closeList();
  closeTable();
  return html.join("");
}

function inlineMd(text) {
  return escapeHtml(text)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
}

function providerLabel(value) {
  const template = state.providerTemplates.find((item) => item.provider === value);
  return template?.label || value || "-";
}

function showConfigNotice(message, tone = "good") {
  const notice = $("#configNotice");
  if (!notice) return;
  notice.textContent = message;
  notice.className = `notice ${tone}`;
  notice.hidden = false;
  window.clearTimeout(showConfigNotice.timer);
  showConfigNotice.timer = window.setTimeout(() => {
    notice.hidden = true;
  }, 3600);
}

async function postConfig(action, payload) {
  const result = await fetchJson(`/api/config/${action}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  applyConfigPayload(result);
  state.summary = await fetchJson("/api/summary");
  renderConfig();
  renderOverview();
  return result;
}

async function handleConfigAction(action, payload, successMessage) {
  try {
    await postConfig(action, payload);
    showConfigNotice(successMessage);
  } catch (error) {
    showConfigNotice(`配置保存失败: ${error.message}`, "bad");
  }
}

function readForm(form) {
  const data = new FormData(form);
  const payload = {};
  for (const [key, value] of data.entries()) {
    payload[key] = String(value).trim();
  }
  for (const input of form.querySelectorAll('input[type="checkbox"]')) {
    payload[input.name] = input.checked;
  }
  return payload;
}

function syncProviderOptions() {
  const select = $("#providerSelect");
  if (!select) return;
  const templates = state.providerTemplates.length
    ? state.providerTemplates
    : [
        { provider: "eastmoney_stock", label: "东方财富行情" },
        { provider: "fundgz", label: "天天基金估值" },
        { provider: "stooq", label: "Stooq 全球行情" },
      ];
  const current = select.value || "eastmoney_stock";
  const html = templates
    .map((item) => `<option value="${escapeHtml(item.provider)}">${escapeHtml(item.label)}</option>`)
    .join("");
  if (select.innerHTML !== html) select.innerHTML = html;
  select.value = templates.some((item) => item.provider === current) ? current : templates[0]?.provider || "";
  updateProviderHint();
}

function updateProviderHint() {
  const select = $("#providerSelect");
  const hint = $("#providerHint");
  if (!select || !hint) return;
  const template = state.providerTemplates.find((item) => item.provider === select.value);
  hint.textContent = template?.hint || "";
}

function renderConfig() {
  if (!state.config || !state.summary) return;
  syncProviderOptions();
  const newsSources = state.config.news_sources || [];
  const watchlist = state.config.watchlist || [];
  const enabledSources = newsSources.filter((item) => item.enabled !== false).length;
  const enabledWatch = watchlist.filter((item) => item.enabled !== false).length;
  $("#sourceCount").textContent = `${enabledSources}/${newsSources.length} 启用`;
  $("#watchCount").textContent = `${enabledWatch}/${watchlist.length} 启用`;

  $("#watchlist").innerHTML = (state.config.watchlist || [])
    .map((item, index) => {
      const enabled = item.enabled !== false;
      return `
      <div class="watch-item ${enabled ? "" : "disabled"}">
        <div class="config-item-main">
          <strong>${escapeHtml(item.name || item.symbol)}</strong>
          <div class="mini-tags">
            <span class="tag">${escapeHtml(item.symbol)}</span>
            <span class="tag">${assetLabel(item.asset_type)}</span>
            <span class="tag">${escapeHtml(item.market || "-")}</span>
            <span class="tag">行情: ${escapeHtml(providerLabel(item.provider))}</span>
          </div>
        </div>
        <div class="config-actions">
          <button
            class="switch ${enabled ? "on" : ""}"
            data-config-action="toggle-watch"
            data-index="${index}"
            data-enabled="${enabled ? "false" : "true"}"
            aria-pressed="${enabled ? "true" : "false"}"
            title="${enabled ? "停用标的" : "启用标的"}"
          ><span></span></button>
          <button class="icon-button small danger" data-config-action="remove-watch" data-index="${index}" title="删除标的">×</button>
        </div>
      </div>`;
    })
    .join("") || `<div class="empty">暂无观察池</div>`;

  $("#sourceList").innerHTML = (state.config.news_sources || [])
    .map((item, index) => {
      const enabled = item.enabled !== false;
      const sourceUrl = item.url || "";
      const urlView = /^https?:\/\//i.test(sourceUrl)
        ? `<a href="${escapeHtml(sourceUrl)}" target="_blank" rel="noreferrer">${escapeHtml(sourceUrl)}</a>`
        : `<span class="local-source">${escapeHtml(sourceUrl || "local-adapter")}</span>`;
      return `
      <div class="source-item ${enabled ? "" : "disabled"}">
        <div class="config-item-main">
          <strong>${escapeHtml(item.name)}</strong>
          <span class="muted">${enabled ? "启用" : "停用"} · ${escapeHtml(item.type || "rss")}</span>
          ${urlView}
        </div>
        <div class="config-actions">
          <button
            class="switch ${enabled ? "on" : ""}"
            data-config-action="toggle-source"
            data-index="${index}"
            data-enabled="${enabled ? "false" : "true"}"
            aria-pressed="${enabled ? "true" : "false"}"
            title="${enabled ? "停用来源" : "启用来源"}"
          ><span></span></button>
          <button class="icon-button small danger" data-config-action="remove-source" data-index="${index}" title="删除来源">×</button>
        </div>
      </div>`;
    })
    .join("") || `<div class="empty">暂无数据源</div>`;

  const existingSourceUrls = new Set(newsSources.map((item) => item.url));
  $("#sourceTemplateList").innerHTML = (state.sourceTemplates || [])
    .map((item) => {
      const added = existingSourceUrls.has(item.url);
      const sourceKind = item.url?.startsWith("akshare://") ? "本地适配" : item.type || "rss";
      return `
      <div class="template-item ${added ? "disabled" : ""}">
        <div>
          <strong>${escapeHtml(item.name)}</strong>
          <p>${escapeHtml(sourceKind)} · ${escapeHtml(item.url)}</p>
        </div>
        <button
          class="text-button"
          data-config-action="add-source-template"
          data-template-id="${escapeHtml(item.id)}"
          ${added ? "disabled" : ""}
        >${added ? "已添加" : "添加"}</button>
      </div>`;
    })
    .join("") || `<div class="empty">暂无来源模板</div>`;

  $("#providerTemplateList").innerHTML = `
    <div class="provider-head">行情接口</div>
    ${(state.providerTemplates || []).map((item) => `
      <div class="provider-item">
        <strong>${escapeHtml(item.label)}</strong>
        <span>${escapeHtml(item.provider)}</span>
        <p>${escapeHtml(item.hint || "")}</p>
      </div>`).join("") || `<div class="empty">暂无行情接口模板</div>`}
  `;

  $("#sourceCapabilityList").innerHTML = (state.sourceCapabilities || [])
    .map((item) => `
      <div class="capability-item ${capabilityClass(item.status)}">
        <div>
          <strong>${escapeHtml(item.name)}</strong>
          <p>${escapeHtml(item.detail || "")}</p>
        </div>
        <span>${escapeHtml(item.category)} · ${escapeHtml(item.status)}</span>
      </div>`)
    .join("") || `<div class="empty">暂无接口能力信息</div>`;

  $("#methodList").innerHTML = (state.summary.methods || [])
    .map((item) => `
      <div class="method-item">
        <strong>${escapeHtml(item.method_name || item.user_label)}</strong>
        <p>${escapeHtml(item.rule_text || "")}</p>
        <div>${(item.tags || []).map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join(" ")}</div>
        <span class="muted">${escapeHtml(item.risk_control || "")}</span>
      </div>`)
    .join("") || `<div class="empty">暂无交易方法</div>`;
}

function capabilityClass(status) {
  if (status === "已接入") return "ready";
  if (status === "谨慎") return "caution";
  if (status === "可选") return "optional";
  return "pending";
}

function fmtMoney(value, digits = 2) {
  if (value === null || value === undefined || value === "") return "-";
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  return number.toLocaleString("zh-CN", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function pnlClass(value) {
  const number = Number(value || 0);
  if (number > 0) return "positive";
  if (number < 0) return "negative";
  return "";
}

function sideLabel(value) {
  return { buy: "买入", sell: "卖出", dividend: "分红", transfer: "转入", adjust: "调整" }[value] || value || "-";
}

function showPortfolioNotice(message, tone = "good") {
  const notice = $("#portfolioNotice");
  if (!notice) return;
  notice.textContent = message;
  notice.className = `notice ${tone}`;
  notice.hidden = false;
  window.clearTimeout(showPortfolioNotice.timer);
  showPortfolioNotice.timer = window.setTimeout(() => {
    notice.hidden = true;
  }, 4200);
}

function renderPortfolio() {
  const portfolio = state.portfolio;
  if (!portfolio) return;
  const metrics = portfolio.metrics || {};
  $("#pfMarketValue").textContent = fmtMoney(metrics.market_value);
  $("#pfTotalPnl").textContent = `${fmtMoney(metrics.total_pnl)}${metrics.total_pnl_pct === null || metrics.total_pnl_pct === undefined ? "" : ` / ${fmt(metrics.total_pnl_pct)}%`}`;
  $("#pfTotalPnl").className = pnlClass(metrics.total_pnl);
  $("#pfUnrealized").textContent = fmtMoney(metrics.unrealized_pnl);
  $("#pfUnrealized").className = pnlClass(metrics.unrealized_pnl);
  const style = portfolio.style || {};
  $("#pfStyle").textContent = `${style.win_rate === null || style.win_rate === undefined ? "-" : `${fmt(style.win_rate)}%`} / ${style.payoff_ratio || "-"}`;
  $("#holdingPath").textContent = portfolio.paths?.holdings || "";
  $("#tradePath").textContent = portfolio.paths?.trades || "";
  $("#ocrNotice").textContent = portfolio.ocr?.message || "";

  $("#holdingRows").innerHTML = (portfolio.holdings || [])
    .map((item) => {
      const news = (item.news_hits || []).slice(0, 2).map((newsItem) => `<button class="link-button" data-news-id="${escapeHtml(newsItem.id || "")}">${escapeHtml(newsItem.title || "")}</button>`).join("");
      return `
        <tr>
          <td><strong>${escapeHtml(item.name || item.symbol)}</strong><br><span class="muted">${escapeHtml(item.symbol)} · ${escapeHtml(item.provider || "")}</span></td>
          <td>${assetLabel(item.asset_type)}</td>
          <td class="num">${fmtMoney(item.quantity, 2)}</td>
          <td class="num">${fmtMoney(item.cost_value)}<br><span class="muted">${item.cost_price ? `均价 ${fmtMoney(item.cost_price, 3)}` : "金额口径"}</span></td>
          <td class="num">${fmtMoney(item.current_price, 3)}<br><span class="${pnlClass(item.change_pct)}">${item.change_pct === null || item.change_pct === undefined ? statusLabel(item.data_status) : `${fmt(item.change_pct)}%`}</span></td>
          <td class="num">${fmtMoney(item.market_value)}<br><span class="muted">${statusLabel(item.data_status)}</span></td>
          <td class="num ${pnlClass(item.unrealized_pnl)}">${fmtMoney(item.unrealized_pnl)}<br><span>${item.unrealized_pct === null || item.unrealized_pct === undefined ? "-" : `${fmt(item.unrealized_pct)}%`}</span></td>
          <td class="num">${fmt(item.allocation_pct)}%</td>
          <td class="news-cell">${news || `<span class="muted">近 7 天无命中</span>`}</td>
          <td><button class="icon-button small danger" data-portfolio-action="remove-holding" data-symbol="${escapeHtml(item.symbol)}" data-provider="${escapeHtml(item.provider || "")}" title="删除持仓">×</button></td>
        </tr>`;
    })
    .join("") || `<tr><td colspan="10" class="empty">暂无持仓，先录入或导入截图文本。</td></tr>`;

  $("#portfolioSuggestions").innerHTML = (portfolio.suggestions || [])
    .map((item) => `
      <div class="suggestion-item ${escapeHtml(item.level || "info")}">
        <strong>${escapeHtml(item.title)}</strong>
        <p>${escapeHtml(item.detail)}</p>
      </div>`)
    .join("") || `<div class="empty">暂无建议</div>`;

  $("#tradeRows").innerHTML = (portfolio.trades || [])
    .map((trade) => `
      <tr>
        <td>${escapeHtml(trade.trade_date || "")}</td>
        <td><strong>${escapeHtml(trade.name || trade.symbol)}</strong><br><span class="muted">${escapeHtml(trade.symbol || "")}</span></td>
        <td>${sideLabel(trade.side)}</td>
        <td class="num">${fmtMoney(trade.quantity, 2)}</td>
        <td class="num">${fmtMoney(trade.price, 3)}</td>
        <td class="num">${fmtMoney(trade.amount)}</td>
        <td class="num ${pnlClass(trade.pnl)}">${trade.pnl === null || trade.pnl === undefined ? "-" : fmtMoney(trade.pnl)}</td>
        <td>${splitTagsClient(trade.tags).map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join(" ") || "-"}</td>
        <td>${escapeHtml(trade.reason || "")}</td>
      </tr>`)
    .join("") || `<tr><td colspan="9" class="empty">暂无交易记录</td></tr>`;

  const allocation = (portfolio.allocation || []).map((item) => `<div class="style-row"><span>${escapeHtml(assetLabel(item.name))}</span><strong>${fmt(item.pct)}%</strong></div>`).join("");
  const topTags = (style.top_tags || []).map((item) => `<span class="tag">${escapeHtml(item.name)} · ${item.count}</span>`).join(" ");
  $("#stylePanel").innerHTML = `
    <div class="style-row"><span>交易笔数</span><strong>${style.trade_count || 0}</strong></div>
    <div class="style-row"><span>买入 / 卖出</span><strong>${style.buy_count || 0} / ${style.sell_count || 0}</strong></div>
    <div class="style-row"><span>换手代理</span><strong>${style.turnover_proxy ?? "-"}</strong></div>
    <div class="style-section-title">资产分布</div>
    ${allocation || `<div class="empty">暂无分布</div>`}
    <div class="style-section-title">交易标签</div>
    <div class="mini-tags">${topTags || `<span class="muted">暂无标签</span>`}</div>
  `;
}

function renderFundAnalyst() {
  const data = state.fundAnalyst || {};
  const strong = data.strong_funds || {};
  const candidates = strong.candidates || [];
  const risks = data.portfolio_fund_risks || [];
  const running = data.state?.running;
  $("#faStrongCount").textContent = candidates.length;
  $("#faRiskCount").textContent = risks.length;
  $("#faStatus").textContent = running ? "运行中" : data.ok ? "可用" : "待运行";
  $("#faUpdated").textContent = data.run_at ? String(data.run_at).slice(5, 16).replace("T", " ") : "-";
  $("#fundCandidateRows").innerHTML = candidates.map((item) => {
    const plan = item.buy_plan || {};
    const small = plan.small_pullback || {};
    const healthy = plan.healthy_pullback || {};
    return `
      <tr>
        <td><strong>${escapeHtml(item.fund_name || item.fund_code)}</strong><br><span class="muted">${escapeHtml(item.fund_code || "")}</span></td>
        <td>${escapeHtml(item.rank_type || "-")}</td>
        <td class="num ${pnlClass(item.r1m)}">${fmt(item.r1m)}%</td>
        <td class="num ${pnlClass(item.r3m)}">${fmt(item.r3m)}%</td>
        <td class="num ${pnlClass(item.r6m)}">${fmt(item.r6m)}%</td>
        <td class="num">${fmt(item.rank_pct_1m)} / ${fmt(item.rank_pct_3m)} / ${fmt(item.rank_pct_6m)}</td>
        <td class="num negative">${fmt(item.maxdd_1m)} / ${fmt(item.maxdd_3m)} / ${fmt(item.maxdd_6m)}</td>
        <td class="num">${fmt(item.sharpe_1m)} / ${fmt(item.sharpe_3m)} / ${fmt(item.sharpe_6m)}</td>
        <td>${[item.above_ma10 ? "MA10" : "", item.above_ma20 ? "MA20" : "", item.above_ma60 ? "MA60" : ""].filter(Boolean).map((x) => `<span class="tag">${x}</span>`).join(" ") || "-"}</td>
        <td>${escapeHtml(item.purchase_status || "-")}<br><span class="muted">${item.purchase_executable ? "可执行" : "受限"}</span></td>
        <td class="num"><strong>${fmt(item.strong_fund_score)}</strong></td>
        <td class="news-cell">
          <div>${escapeHtml(plan.macro_action || "")}</div>
          ${small.target_nav_range ? `<div>小回调: ${small.target_nav_range.map((v) => fmt(v, 4)).join(" - ")}</div>` : ""}
          ${healthy.target_nav_range ? `<div>健康回撤: ${healthy.target_nav_range.map((v) => fmt(v, 4)).join(" - ")}</div>` : ""}
        </td>
      </tr>`;
  }).join("") || `<tr><td colspan="12" class="empty">暂无基金分析结果，点击刷新分析。</td></tr>`;

  $("#fundRiskList").innerHTML = risks.map((item) => `
    <div class="risk-item">
      <strong>${escapeHtml(item.fund_name || item.fund_code)}</strong>
      <span class="muted">${escapeHtml(item.fund_code || "")} · ${escapeHtml(item.latest_nav_date || "")}</span>
      ${item.error ? `<p>${escapeHtml(item.error)}</p>` : `
        <p>四维闸门: ${item.four_dimension_pass ? "通过" : "未通过"}；近期强度: ${item.recent_strength_pass ? "通过" : "未通过"}</p>
        <div class="mini-tags">${(item.four_dimension_reasons || []).slice(0, 4).map((x) => `<span class="tag">${escapeHtml(x)}</span>`).join("")}</div>
      `}
    </div>
  `).join("") || `<div class="empty">当前持仓里暂无可穿透的场外基金，录入基金代码后可生成风控摘要。</div>`;

  $("#fundAnalystMeta").innerHTML = `
    <div class="method-item">
      <strong>数据源</strong>
      <p>${escapeHtml(strong.source || "AKShare / 东方财富 / 天天基金")}</p>
    </div>
    <div class="method-item">
      <strong>方法边界</strong>
      <p>${escapeHtml(data.method_note || "用于研究筛选，不构成投资建议。")}</p>
    </div>
    <div class="method-item">
      <strong>结果文件</strong>
      <p>${escapeHtml(data.path || "")}</p>
    </div>
  `;
}

function fmtFlowMoney(value) {
  if (value === null || value === undefined || value === "") return "-";
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  const abs = Math.abs(number);
  if (abs >= 100000000) return `${(number / 100000000).toFixed(2)} 亿`;
  if (abs >= 10000) return `${(number / 10000).toFixed(2)} 万`;
  return number.toFixed(2);
}

function sourceStatusClass(item) {
  if (item.skipped) return "skipped";
  if (!item.ok) return "fail";
  if (item.degraded) return "warn";
  return "ok";
}

function sourceStatusLabel(item) {
  if (item.skipped) return "跳过";
  if (!item.ok) return "FAIL";
  if (item.degraded) return "备用";
  return "OK";
}

function flowClass(value) {
  const number = Number(value || 0);
  if (number > 0) return "positive";
  if (number < 0) return "negative";
  return "";
}

function signalClass(value) {
  return {
    资金确认: "good",
    资金试探: "watch",
    舆情资金背离: "warn",
    弱势流出: "risk",
    风险复核: "risk",
    缺资金数据: "neutral",
    中性观察: "neutral",
  }[value] || "neutral";
}

function renderFlowList(selector, items, limit = 8) {
  $(selector).innerHTML = (items || []).slice(0, limit).map((item) => `
    <div class="flow-item">
      <div>
        <strong>${escapeHtml(item.name || item.code || "-")}</strong>
        <span>${escapeHtml(item.category || item.source || "")}${item.change_pct === null || item.change_pct === undefined ? "" : ` · 涨跌 ${fmt(item.change_pct)}%`}</span>
      </div>
      <b class="${flowClass(item.main_net_inflow)}">${fmtFlowMoney(item.main_net_inflow)}</b>
    </div>
  `).join("") || `<div class="empty">暂无资金流数据</div>`;
}

function renderAfterClose() {
  const data = state.afterClose || {};
  const metrics = data.metrics || {};
  const running = data.state?.running;
  $("#acFlowFound").textContent = `${metrics.flow_found || 0}/${metrics.watch_count || 0}`;
  $("#acConfirmed").textContent = metrics.confirmed || 0;
  $("#acPortfolioFlowFound").textContent = `${metrics.portfolio_flow_found || 0}/${metrics.portfolio_count || 0}`;
  $("#acDiverged").textContent = metrics.diverged || 0;
  $("#acUpdated").textContent = running
    ? "运行中"
    : data.run_at
      ? String(data.run_at).slice(5, 16).replace("T", " ")
      : "待运行";
  $("#afterCloseMethod").textContent = `${data.method_note || "资金流只作研究辅助。"}${data.fallback_used ? " 当前显示上一次可用缓存。" : ""}`;

  $("#afterCloseConclusions").innerHTML = (data.conclusions || [])
    .map((item) => `
      <div class="suggestion-item ${escapeHtml(item.level || "info")}">
        <strong>${escapeHtml(item.title)}</strong>
        <p>${escapeHtml(item.detail)}</p>
      </div>`)
    .join("") || `<div class="empty">暂无盘后结论</div>`;

  $("#afterCloseSources").innerHTML = (data.source_statuses || [])
    .map((item) => `
      <div class="source-status ${sourceStatusClass(item)}">
        <div>
          <strong>${escapeHtml(item.name)}</strong>
          <p>${escapeHtml(item.detail || "")}${item.used_key ? ` · ${escapeHtml(item.used_key)}` : ""}</p>
        </div>
        <span>${sourceStatusLabel(item)} · ${item.count || 0}</span>
      </div>`)
    .join("") || `<div class="empty">尚未运行资金流任务</div>`;

  $("#afterClosePortfolioRows").innerHTML = (data.portfolio_flows || [])
    .map((item) => `
      <tr>
        <td><strong>${escapeHtml(item.name || item.symbol)}</strong><br><span class="muted">${escapeHtml(item.symbol || item.code || "")} · ${escapeHtml(statusLabel(item.data_status))}</span></td>
        <td class="num">${fmtMoney(item.market_value)}</td>
        <td class="num ${pnlClass(item.unrealized_pnl)}">${fmtMoney(item.unrealized_pnl)}<br><span>${item.unrealized_pct === null || item.unrealized_pct === undefined ? "-" : `${fmt(item.unrealized_pct)}%`}</span></td>
        <td class="num">${fmt(item.allocation_pct)}%</td>
        <td class="num ${pnlClass(item.change_pct)}">${fmt(item.change_pct)}%</td>
        <td class="num ${flowClass(item.main_net_inflow)}">${fmtFlowMoney(item.main_net_inflow)}</td>
        <td><span class="muted">${escapeHtml(item.flow_source || "未命中")}${item.rank ? ` #${item.rank}` : ""}</span></td>
        <td><span class="action-chip ${signalClass(item.signal)}">${escapeHtml(item.signal || "-")}</span></td>
        <td class="news-cell">${escapeHtml(item.signal_reason || "")}</td>
      </tr>`)
    .join("") || `<tr><td colspan="9" class="empty">暂无股票持仓盘后资金流。请先在“持仓”录入股票，或刷新盘后分析。</td></tr>`;

  $("#afterCloseWatchRows").innerHTML = (data.watchlist_flows || [])
    .map((item) => `
      <tr>
        <td><strong>${escapeHtml(item.name || item.symbol)}</strong><br><span class="muted">${escapeHtml(item.symbol || item.code || "")}</span></td>
        <td>${escapeHtml(item.source === "portfolio" ? "持仓" : "候选池")}</td>
        <td class="num">${fmt(item.total_score)}</td>
        <td class="num">${fmt(item.heat_score, 0)}</td>
        <td class="num ${pnlClass(item.change_pct)}">${fmt(item.change_pct)}%</td>
        <td class="num ${flowClass(item.main_net_inflow)}">${fmtFlowMoney(item.main_net_inflow)}</td>
        <td class="num ${flowClass(item.main_net_pct)}">${item.main_net_pct === null || item.main_net_pct === undefined || item.main_net_pct === "" ? "-" : `${fmt(item.main_net_pct)}%`}</td>
        <td><span class="muted">${escapeHtml(item.flow_source || "未命中")}${item.rank ? ` #${item.rank}` : ""}</span></td>
        <td><span class="action-chip ${signalClass(item.signal)}">${escapeHtml(item.signal || "-")}</span></td>
        <td class="news-cell">${escapeHtml(item.signal_reason || "")}</td>
      </tr>`)
    .join("") || `<tr><td colspan="10" class="empty">暂无候选/持仓资金流交叉结果，点击刷新盘后分析。</td></tr>`;

  renderFlowList("#sectorInflowList", data.sector_inflows || [], 8);
  renderFlowList("#sectorOutflowList", data.sector_outflows || [], 8);
  renderFlowList("#stockFlowRank", data.stock_flow_rank || [], 10);
}

async function triggerAfterClose() {
  const notice = $("#afterCloseNotice");
  notice.hidden = false;
  notice.className = "notice";
  notice.textContent = "盘后资金流分析刷新中";
  await fetchJson("/api/after-close/update", { method: "POST" });
  for (let i = 0; i < 100; i += 1) {
    state.afterClose = await fetchJson("/api/after-close");
    renderAfterClose();
    if (!state.afterClose.state?.running) {
      const failed = state.afterClose.metrics?.source_failures || 0;
      notice.className = `notice ${state.afterClose.state?.returncode === 0 ? "good" : "bad"}`;
      notice.textContent = state.afterClose.state?.returncode === 0
        ? `盘后分析已刷新${failed ? `，${failed} 个来源失败，已保留可用数据` : ""}`
        : `盘后分析失败: ${state.afterClose.state?.stderr || ""}`;
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 1500));
  }
  notice.className = "notice bad";
  notice.textContent = "盘后分析仍在运行，请稍后刷新页面查看。";
}

async function triggerFundAnalyst() {
  const notice = $("#fundAnalystNotice");
  notice.hidden = false;
  notice.className = "notice";
  notice.textContent = "基金分析刷新中";
  await fetchJson("/api/fund-analyst/update", { method: "POST" });
  for (let i = 0; i < 120; i += 1) {
    state.fundAnalyst = await fetchJson("/api/fund-analyst");
    renderFundAnalyst();
    if (!state.fundAnalyst.state?.running) {
      notice.className = `notice ${state.fundAnalyst.state?.returncode === 0 ? "good" : "bad"}`;
      notice.textContent = state.fundAnalyst.state?.returncode === 0 ? "基金分析已刷新" : `基金分析失败: ${state.fundAnalyst.state?.stderr || ""}`;
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 1500));
  }
  notice.className = "notice bad";
  notice.textContent = "基金分析仍在运行，请稍后刷新页面查看。";
}

function splitTagsClient(value) {
  return String(value || "").split(/[;,，；\s]+/).filter(Boolean);
}

function allNewsItems() {
  return state.summary?.recent_news_items || state.summary?.news_items || [];
}

function findNewsById(id) {
  return allNewsItems().find((item) => String(item.id || "") === String(id || ""));
}

function showNewsDetail(item) {
  if (!item) return;
  const dialog = $("#newsDialog");
  const points = (item.key_points || []).map((point) => `<li>${escapeHtml(point)}</li>`).join("");
  const tags = (item.themes || []).map((theme) => `<span class="tag">${escapeHtml(theme)}</span>`).join(" ");
  const symbols = (item.symbols || []).map((symbol) => `<span class="tag">${escapeHtml(symbol)}</span>`).join(" ");
  $("#newsDialogBody").innerHTML = `
    <p class="eyebrow">${escapeHtml(item.source_label || item.source || "")} · ${escapeHtml(item.published_at || item.fetched_at || "")}</p>
    <h3>${escapeHtml(item.title || "")}</h3>
    ${points ? `<ul class="key-points detail-points">${points}</ul>` : ""}
    <p>${escapeHtml(item.summary || "")}</p>
    <div class="mini-tags">${tags}${symbols}</div>
    <div class="detail-meta">
      <span>情绪分 ${fmt(item.sentiment_score)}</span>
      <span>风险关键词 ${item.risk_count || 0}</span>
      <span>抓取 ${escapeHtml(item.fetched_at || "")}</span>
    </div>
    ${item.url ? `<a class="text-button detail-link" href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">打开原文</a>` : ""}
  `;
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.setAttribute("open", "open");
}

function setView(view) {
  state.view = view;
  $$(".view").forEach((item) => item.classList.toggle("active", item.id === view));
  $$(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === view));
  $("#pageTitle").textContent = document.querySelector(`[data-view="${view}"] span`).textContent;
  if (view === "reports" && !state.reports) {
    loadReports();
  }
}

async function loadReports() {
  try {
    state.reports = await fetchJson("/api/report");
    renderReports();
  } catch (error) {
    $("#reportContent").innerHTML = `<p class="negative">报告加载失败: ${escapeHtml(error.message)}</p>`;
  }
}

async function triggerUpdate(mode) {
  $("#runStatus").textContent = `${mode === "offline" ? "离线" : "联网"}更新中`;
  await fetchJson(`/api/update?mode=${mode}`, { method: "POST" });
  pollUpdate();
}

function maybeAutoUpdate() {
  if (state.autoUpdateAttempted) return;
  const auto = state.summary?.auto_update || {};
  if (!auto.enabled || !auto.should_update) return;
  const todayKey = new Date().toISOString().slice(0, 10);
  const key = `finance-dashboard-auto-update-${todayKey}`;
  if (localStorage.getItem(key)) return;
  state.autoUpdateAttempted = true;
  localStorage.setItem(key, "1");
  triggerUpdate("online").catch((error) => {
    $("#runStatus").textContent = `自动更新失败: ${error.message}`;
  });
}

function maybeAutoAfterClose() {
  const data = state.afterClose || {};
  if (data.state?.running) return;
  const todayKey = new Date().toISOString().slice(0, 10);
  if (data.trade_date === todayKey && data.run_at) return;
  const key = `finance-dashboard-after-close-${todayKey}`;
  if (localStorage.getItem(key)) return;
  localStorage.setItem(key, "1");
  triggerAfterClose().catch((error) => {
    const notice = $("#afterCloseNotice");
    if (!notice) return;
    notice.hidden = false;
    notice.className = "notice bad";
    notice.textContent = `盘后分析自动刷新失败: ${error.message}`;
  });
}

async function pollUpdate() {
  for (let i = 0; i < 80; i += 1) {
    const data = await fetchJson("/api/update-state");
    const status = data.state;
    $("#runStatus").textContent = status.running
      ? `${status.mode === "offline" ? "离线" : "联网"}更新中`
      : `上次 ${status.returncode === 0 ? "成功" : "失败"} ${status.finished_at || ""}`;
    if (!status.running) {
      await loadAll();
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 1500));
  }
}

function bindEvents() {
  $$(".nav-item").forEach((button) => button.addEventListener("click", () => setView(button.dataset.view)));
  $$("[data-view-jump]").forEach((button) => button.addEventListener("click", () => setView(button.dataset.viewJump)));
  $("#searchInput").addEventListener("input", renderCandidates);
  $("#assetFilter").addEventListener("change", renderCandidates);
  $("#themeFilter").addEventListener("change", renderCandidates);
  $("#showAttempt").addEventListener("click", () => {
    state.showAttempt = !state.showAttempt;
    renderReports();
  });
  $("#runOnline").addEventListener("click", () => triggerUpdate("online"));
  $("#runOffline").addEventListener("click", () => triggerUpdate("offline"));
  $("#runFundAnalyst").addEventListener("click", () => triggerFundAnalyst().catch((error) => {
    const notice = $("#fundAnalystNotice");
    notice.hidden = false;
    notice.className = "notice bad";
    notice.textContent = `基金分析刷新失败: ${error.message}`;
  }));
  $("#runAfterClose").addEventListener("click", () => triggerAfterClose().catch((error) => {
    const notice = $("#afterCloseNotice");
    notice.hidden = false;
    notice.className = "notice bad";
    notice.textContent = `盘后分析刷新失败: ${error.message}`;
  }));
  $("#themeToggle").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("finance-dashboard-theme", next);
  });
  $("#config").addEventListener("click", (event) => {
    const button = event.target.closest("[data-config-action]");
    if (!button) return;
    const action = button.dataset.configAction;
    const index = Number(button.dataset.index);
    if (action === "toggle-source") {
      handleConfigAction("toggle-source", { index, enabled: button.dataset.enabled === "true" }, "数据源开关已保存");
    } else if (action === "toggle-watch") {
      handleConfigAction("toggle-watch", { index, enabled: button.dataset.enabled === "true" }, "观察标的开关已保存");
    } else if (action === "remove-source") {
      if (window.confirm("删除这个数据源？")) handleConfigAction("remove-source", { index }, "数据源已删除");
    } else if (action === "remove-watch") {
      if (window.confirm("删除这个观察标的？")) handleConfigAction("remove-watch", { index }, "观察标的已删除");
    } else if (action === "add-source-template") {
      handleConfigAction("add-source-template", { template_id: button.dataset.templateId }, "标准数据源已添加");
    }
  });
  $("#sourceForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const payload = readForm(form);
    await handleConfigAction("add-source", payload, "自定义数据源已添加");
    if (!$("#configNotice").classList.contains("bad")) form.reset();
  });
  $("#watchForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const payload = readForm(form);
    await handleConfigAction("add-watch", payload, "观察标的已添加");
    if (!$("#configNotice").classList.contains("bad")) {
      form.reset();
      syncProviderOptions();
    }
  });
  $("#providerSelect").addEventListener("change", updateProviderHint);
  $("#sentiment").addEventListener("click", (event) => {
    const card = event.target.closest("[data-news-index]");
    if (!card) return;
    showNewsDetail(allNewsItems()[Number(card.dataset.newsIndex)]);
  });
  $("#overview").addEventListener("click", (event) => {
    const card = event.target.closest("[data-news-index]");
    if (!card) return;
    showNewsDetail((state.summary?.news_items || [])[Number(card.dataset.newsIndex)]);
  });
  $("#portfolio").addEventListener("click", async (event) => {
    const newsButton = event.target.closest("[data-news-id]");
    if (newsButton) {
      showNewsDetail(findNewsById(newsButton.dataset.newsId));
      return;
    }
    const actionButton = event.target.closest("[data-portfolio-action]");
    if (!actionButton) return;
    if (actionButton.dataset.portfolioAction === "remove-holding") {
      if (!window.confirm("删除这个持仓？")) return;
      try {
        const result = await fetchJson("/api/portfolio/remove-holding", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ symbol: actionButton.dataset.symbol, provider: actionButton.dataset.provider }),
        });
        state.portfolio = result.portfolio;
        renderPortfolio();
        showPortfolioNotice("持仓已删除");
      } catch (error) {
        showPortfolioNotice(`删除失败: ${error.message}`, "bad");
      }
    }
  });
  $("#holdingForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    try {
      const result = await fetchJson("/api/portfolio/add-holding", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(readForm(form)),
      });
      state.portfolio = result.portfolio;
      renderPortfolio();
      showPortfolioNotice("持仓已保存");
      form.reset();
    } catch (error) {
      showPortfolioNotice(`保存失败: ${error.message}`, "bad");
    }
  });
  $("#tradeForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    try {
      const result = await fetchJson("/api/portfolio/add-trade", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(readForm(form)),
      });
      state.portfolio = result.portfolio;
      renderPortfolio();
      showPortfolioNotice("交易已记录");
      form.reset();
    } catch (error) {
      showPortfolioNotice(`记录失败: ${error.message}`, "bad");
    }
  });
  $("#importForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    try {
      const result = await fetchJson("/api/portfolio/import-text", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(readForm(form)),
      });
      state.portfolio = result.portfolio;
      renderPortfolio();
      showPortfolioNotice(`已导入 ${result.imported || 0} 条持仓`);
      form.reset();
    } catch (error) {
      showPortfolioNotice(`导入失败: ${error.message}`, "bad");
    }
  });
  $("#screenshotForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const file = $("#screenshotInput").files?.[0];
    if (!file) {
      showPortfolioNotice("请选择一张截图", "bad");
      return;
    }
    try {
      const dataUrl = await readFileAsDataUrl(file);
      const result = await fetchJson("/api/portfolio/upload-screenshot", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: file.name, data_url: dataUrl }),
      });
      showPortfolioNotice(`截图已保存: ${result.path}`);
      $("#screenshotInput").value = "";
    } catch (error) {
      showPortfolioNotice(`截图保存失败: ${error.message}`, "bad");
    }
  });
  $("#closeNewsDialog").addEventListener("click", () => $("#newsDialog").close());
  $("#newsDialog").addEventListener("click", (event) => {
    if (event.target.id === "newsDialog") $("#newsDialog").close();
  });
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error || new Error("read file failed"));
    reader.readAsDataURL(file);
  });
}

document.documentElement.dataset.theme = localStorage.getItem("finance-dashboard-theme") || "light";
bindEvents();
loadAll().catch((error) => {
  document.body.innerHTML = `<pre style="padding:24px;color:#c24132">加载失败: ${escapeHtml(error.message)}</pre>`;
});
