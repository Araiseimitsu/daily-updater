/**
 * DOM 描画・UI 更新を担当するモジュール（ミニマル・リストビュー構成）。
 * XSS 防止のため、すべてのユーザー入力値は textContent 経由で描画する。
 */

import { getEffectiveStatus, getFilteredApps, state } from "./state.js";

const STATUS_LABELS = {
  idle: "",
  queued: "待機",
  scanning: "スキャン中",
  running: "実行中",
  success: "完了",
  failed: "失敗",
  cancelled: "中止",
  unavailable: "未導入",
};

const SAFETY_LABELS = {
  safe: "毎日推奨",
  optional: "任意",
  caution: "注意",
};

export function formatBytes(numBytes) {
  const bytes = Math.max(0, Number(numBytes) || 0);
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  for (let i = 0; i < units.length; i += 1) {
    if (value < 1024 || i === units.length - 1) {
      return i === 0 ? `${Math.round(value)} B` : `${value.toFixed(1)} ${units[i]}`;
    }
    value /= 1024;
  }
  return `${Math.round(bytes)} B`;
}

function formatDateTime(isoString) {
  if (!isoString) {
    return "未実行";
  }
  const dt = new Date(isoString);
  if (Number.isNaN(dt.getTime())) {
    return isoString;
  }
  const mm = String(dt.getMonth() + 1).padStart(2, "0");
  const dd = String(dt.getDate()).padStart(2, "0");
  const hh = String(dt.getHours()).padStart(2, "0");
  const mi = String(dt.getMinutes()).padStart(2, "0");
  return `${mm}/${dd} ${hh}:${mi}`;
}

function el(tag, className = "", text = null) {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (text !== null) {
    node.textContent = text;
  }
  return node;
}

export function setConnectionBadge(status) {
  const badge = document.getElementById("connectionBadge");
  const text = document.getElementById("connectionText");
  badge.classList.remove("is-connecting", "is-online", "is-offline");

  if (status === "online") {
    badge.classList.add("is-online");
    text.textContent = "オンライン";
  } else if (status === "offline") {
    badge.classList.add("is-offline");
    text.textContent = "再接続中";
  } else {
    badge.classList.add("is-connecting");
    text.textContent = "接続中";
  }
}

export function renderHeaderAndControls() {
  const enabledCount = state.apps.filter((app) => app.enabled).length;
  const selectedCount = state.selectedIds.size;
  const failedCount = state.apps.filter((app) => getEffectiveStatus(app) === "failed").length;
  const isBusy = Boolean(state.batch.active);

  document.getElementById("enabledCountBadge").textContent = String(enabledCount);
  document.getElementById("selectedCountBadge").textContent = String(selectedCount);
  document.getElementById("failedCountBadge").textContent = String(failedCount);

  document.getElementById("runAllBtn").disabled = enabledCount === 0;
  document.getElementById("runSelectedBtn").disabled = selectedCount === 0;
  document.getElementById("retryFailedBtn").classList.toggle("is-hidden", failedCount === 0);
  document.getElementById("cancelAllBtn").classList.toggle("is-hidden", !isBusy);

  const workersSelect = document.getElementById("quickWorkersSelect");
  const workersValue = String(state.settings.max_workers || 4);
  if (Array.from(workersSelect.options).some((opt) => opt.value === workersValue)) {
    workersSelect.value = workersValue;
  }

  document.getElementById("shellInfoTag").textContent = state.shells.resolved_auto || "pwsh";
}

export function renderCategoryFilter() {
  const select = document.getElementById("categoryFilter");
  const current = state.categoryFilter;
  const categories = Array.from(new Set(state.apps.map((app) => app.category))).sort((a, b) =>
    a.localeCompare(b, "ja")
  );

  select.replaceChildren();
  const allOpt = el("option", "", "すべて");
  allOpt.value = "all";
  select.appendChild(allOpt);

  for (const cat of categories) {
    const opt = el("option", "", cat);
    opt.value = cat;
    select.appendChild(opt);
  }

  if (current === "all" || categories.includes(current)) {
    select.value = current;
  } else {
    state.categoryFilter = "all";
    select.value = "all";
  }
}

export function renderActiveTab() {
  const isCleaner = state.activeTab === "cleaner";
  const tabUpdatesBtn = document.getElementById("tabUpdatesBtn");
  const tabCleanerBtn = document.getElementById("tabCleanerBtn");

  tabUpdatesBtn.classList.toggle("is-active", !isCleaner);
  tabUpdatesBtn.setAttribute("aria-selected", String(!isCleaner));
  tabCleanerBtn.classList.toggle("is-active", isCleaner);
  tabCleanerBtn.setAttribute("aria-selected", String(isCleaner));

  document.getElementById("updaterToolbar").classList.toggle("is-hidden", isCleaner);
  document.getElementById("appsPanel").classList.toggle("is-hidden", isCleaner);
  document.getElementById("cleanerToolbar").classList.toggle("is-hidden", !isCleaner);
  document.getElementById("cleanerPanel").classList.toggle("is-hidden", !isCleaner);

  // 進捗バーは現在アクティブなタブのものだけ表示
  renderProgressPanel();
  renderCleanerProgress();
}

export function renderProgressPanel() {
  const section = document.getElementById("progressSection");
  const batch = state.batch;

  if (state.activeTab !== "updates" || !batch || batch.total === 0) {
    section.classList.add("is-hidden");
    return;
  }

  section.classList.remove("is-hidden");
  const percent = batch.total > 0 ? Math.round((batch.completed / batch.total) * 100) : 0;

  const headline = document.getElementById("progressHeadline");
  if (batch.active) {
    headline.textContent = "並列アップデート実行中";
  } else if (batch.failed > 0) {
    headline.textContent = `完了（${batch.failed}件エラー）`;
  } else if (batch.cancelled > 0) {
    headline.textContent = "中止しました";
  } else {
    headline.textContent = "アップデート完了";
  }

  document.getElementById("progressRatio").textContent = `${batch.completed}/${batch.total} (${percent}%)`;
  document.getElementById("statRunning").textContent = String(batch.running || 0);
  document.getElementById("statQueued").textContent = String(batch.queued || 0);
  document.getElementById("statSuccess").textContent = String(batch.succeeded || 0);
  document.getElementById("statFailed").textContent = String(batch.failed || 0);

  const track = document.getElementById("progressBarTrack");
  track.setAttribute("aria-valuenow", String(percent));
  document.getElementById("progressBarFill").style.width = `${percent}%`;
}

function buildAppRow(app, index, totalVisible) {
  const rt = state.statuses[app.id] || {};
  const status = getEffectiveStatus(app);
  const isRunning = status === "running" || status === "queued";
  const isSelected = state.selectedIds.has(app.id);

  const row = el("article", "app-row");
  row.dataset.appId = app.id;
  if (!app.enabled) {
    row.classList.add("is-disabled");
  }
  if (isSelected) {
    row.classList.add("is-selected");
  }

  // 左端: チェックボックス + ステータス極小ドット
  const leading = el("div", "row-leading");
  const checkbox = el("input", "row-checkbox");
  checkbox.type = "checkbox";
  checkbox.checked = isSelected;
  checkbox.dataset.action = "select";
  checkbox.dataset.appId = app.id;
  checkbox.setAttribute("aria-label", `${app.name} を選択`);

  const dot = el("span", `state-dot is-${status}`);
  dot.title = STATUS_LABELS[status] || "未実行";
  leading.append(checkbox, dot);

  // 中央: 名前・カテゴリ・状態・コマンド・履歴
  const main = el("div", "row-main");

  const headline = el("div", "row-headline");
  headline.appendChild(el("span", "row-name", app.name));
  headline.appendChild(el("span", "row-category", app.category));
  if (STATUS_LABELS[status]) {
    headline.appendChild(el("span", `row-status-text is-${status}`, STATUS_LABELS[status]));
  }

  const cmdEl = el("div", "row-command", app.command);
  cmdEl.title = app.command;

  const submeta = el("div", "row-submeta");
  const lastRunAt = rt.last_run_at || app.last_run_at;
  const duration = rt.duration ?? app.last_duration;
  const timeText =
    typeof duration === "number"
      ? `${formatDateTime(lastRunAt)} · ${duration.toFixed(1)}s`
      : formatDateTime(lastRunAt);
  submeta.appendChild(el("span", "meta-mono", timeText));

  const summaryText = rt.message || app.last_summary || "";
  if (summaryText) {
    const summaryEl = el("span", "row-summary", summaryText);
    summaryEl.title = summaryText;
    submeta.appendChild(summaryEl);
  }

  main.append(headline, cmdEl, submeta);

  // 右端: 一括対象トグル + 個別実行 + ホバーユーティリティ
  const trailing = el("div", "row-trailing");

  const toggleLabel = el("label", "toggle-label");
  toggleLabel.title = app.enabled ? "一括更新の対象（クリックで除外）" : "一括更新から除外中（クリックで対象へ）";
  const toggleInput = el("input", "toggle-input");
  toggleInput.type = "checkbox";
  toggleInput.checked = Boolean(app.enabled);
  toggleInput.dataset.action = "toggle-enabled";
  toggleInput.dataset.appId = app.id;
  const toggleSlider = el("span", "toggle-slider");
  toggleLabel.append(toggleInput, toggleSlider);

  const runBtn = el(
    "button",
    isRunning ? "btn btn-xs btn-danger-subtle" : "btn btn-xs btn-secondary",
    isRunning ? "中止" : "更新"
  );
  runBtn.type = "button";
  runBtn.dataset.action = isRunning ? "cancel-one" : "run-one";
  runBtn.dataset.appId = app.id;

  const utility = el("div", "row-utility");

  const logBtn = el("button", "btn btn-xs btn-text", "ログ");
  logBtn.type = "button";
  logBtn.dataset.action = "focus-log";
  logBtn.dataset.appId = app.id;

  const editBtn = el("button", "btn btn-xs btn-text", "編集");
  editBtn.type = "button";
  editBtn.dataset.action = "edit";
  editBtn.dataset.appId = app.id;

  const upBtn = el("button", "btn btn-xs btn-text", "↑");
  upBtn.type = "button";
  upBtn.title = "上へ";
  upBtn.dataset.action = "move-up";
  upBtn.dataset.appId = app.id;
  upBtn.disabled = index === 0;

  const downBtn = el("button", "btn btn-xs btn-text", "↓");
  downBtn.type = "button";
  downBtn.title = "下へ";
  downBtn.dataset.action = "move-down";
  downBtn.dataset.appId = app.id;
  downBtn.disabled = index === totalVisible - 1;

  const delBtn = el("button", "btn btn-xs btn-text", "解除");
  delBtn.type = "button";
  delBtn.dataset.action = "delete";
  delBtn.dataset.appId = app.id;

  utility.append(logBtn, editBtn, upBtn, downBtn, delBtn);
  trailing.append(toggleLabel, runBtn, utility);

  row.append(leading, main, trailing);
  return row;
}

export function renderCleanerControls() {
  const enabledCount = state.cleanerTasks.filter((task) => task.enabled && task.available).length;
  const isBusy = Boolean(state.cleanerState.active);

  document.getElementById("cleanerEnabledCountBadge").textContent = String(enabledCount);
  document.getElementById("runCleanAllBtn").disabled = isBusy || enabledCount === 0;
  document.getElementById("scanCleanBtn").disabled = isBusy;
  document.getElementById("cancelCleanBtn").classList.toggle("is-hidden", !isBusy);

  const tempAgeSelect = document.getElementById("quickTempAgeSelect");
  const tempAgeVal = String(state.settings.cleaner_temp_min_age_hours ?? 24);
  if (Array.from(tempAgeSelect.options).some((opt) => opt.value === tempAgeVal)) {
    tempAgeSelect.value = tempAgeVal;
  }

  // タブ上の小さなバッジ（削除可能サイズ or 実行中）
  const tabBadge = document.getElementById("tabCleanerBadge");
  const reclaimable = state.cleanerState.total_reclaimable_bytes || 0;
  if (isBusy) {
    tabBadge.textContent = state.cleanerState.mode === "scan" ? "..." : "実行中";
    tabBadge.classList.remove("is-hidden");
  } else if (reclaimable > 0) {
    tabBadge.textContent = formatBytes(reclaimable);
    tabBadge.classList.remove("is-hidden");
  } else {
    tabBadge.classList.add("is-hidden");
  }

  // ツールバー右のサマリーピル
  const summaryPill = document.getElementById("cleanerSummaryBadge");
  summaryPill.classList.remove("has-reclaimable", "is-freed");
  if (isBusy) {
    summaryPill.textContent = state.cleanerState.mode === "scan" ? "スキャン中..." : "クリーニング中...";
  } else if (state.cleanerState.last_clean_at && state.cleanerState.last_freed_bytes > 0) {
    summaryPill.textContent = `直近解放: ${formatBytes(state.cleanerState.last_freed_bytes)} · 残候補: ${formatBytes(reclaimable)}`;
    summaryPill.classList.add("is-freed");
  } else if (state.cleanerState.last_scan_at) {
    summaryPill.textContent = reclaimable > 0 ? `削除候補: ${formatBytes(reclaimable)}` : "クリーンな状態です";
    if (reclaimable > 0) {
      summaryPill.classList.add("has-reclaimable");
    }
  } else {
    summaryPill.textContent = "未スキャン";
  }
}

export function renderCleanerProgress() {
  const section = document.getElementById("cleanerProgressSection");
  const cs = state.cleanerState;

  if (state.activeTab !== "cleaner" || !cs || (!cs.active && cs.total === 0)) {
    section.classList.add("is-hidden");
    return;
  }

  section.classList.remove("is-hidden");
  const percent = cs.total > 0 ? Math.round((cs.completed / cs.total) * 100) : 0;

  const headline = document.getElementById("cleanerProgressHeadline");
  const metric = document.getElementById("cleanerStatMetric");
  if (cs.active) {
    headline.textContent = cs.mode === "scan" ? "容量スキャン実行中" : "PCクリーニング実行中";
  } else if (cs.failed > 0) {
    headline.textContent = `完了（${cs.failed}件エラー）`;
  } else if (cs.last_clean_at && (!cs.last_scan_at || cs.last_clean_at >= cs.last_scan_at)) {
    headline.textContent = "PCクリーニング完了";
  } else {
    headline.textContent = "容量スキャン完了";
  }

  if (cs.mode === "clean" || (cs.last_clean_at && (!cs.last_scan_at || cs.last_clean_at >= cs.last_scan_at))) {
    metric.textContent = `解放: ${formatBytes(cs.last_freed_bytes || 0)}`;
  } else {
    metric.textContent = `削除候補: ${formatBytes(cs.total_reclaimable_bytes || 0)}`;
  }

  document.getElementById("cleanerProgressRatio").textContent = `${cs.completed}/${cs.total} (${percent}%)`;
  document.getElementById("cleanerStatSuccess").textContent = String(cs.succeeded || 0);
  document.getElementById("cleanerStatSkipped").textContent = String(cs.skipped || 0);
  document.getElementById("cleanerStatFailed").textContent = String(cs.failed || 0);

  document.getElementById("cleanerProgressBarTrack").setAttribute("aria-valuenow", String(percent));
  document.getElementById("cleanerProgressBarFill").style.width = `${percent}%`;
}

function buildCleanerRow(task) {
  const isBusy = Boolean(state.cleanerState.active);
  const status = task.available ? task.status || "idle" : "unavailable";
  const isDisabled = !task.enabled || !task.available;

  const row = el("article", "app-row cleaner-row");
  row.dataset.taskId = task.id;
  if (isDisabled) {
    row.classList.add("is-disabled");
  }

  // 左端: ステータスドット
  const leading = el("div", "row-leading");
  const dot = el("span", `state-dot is-${status}`);
  dot.title = STATUS_LABELS[status] || "待機";
  leading.appendChild(dot);

  // 中央: 項目名・カテゴリ・安全度・対象・説明・最終結果
  const main = el("div", "row-main");

  const headline = el("div", "row-headline");
  headline.appendChild(el("span", "row-name", task.name));
  headline.appendChild(el("span", "row-category", task.category));

  if (!task.available) {
    headline.appendChild(el("span", "safety-badge is-unavailable", task.unavailable_reason || "未導入"));
  } else if (SAFETY_LABELS[task.safety]) {
    headline.appendChild(el("span", `safety-badge is-${task.safety}`, SAFETY_LABELS[task.safety]));
  }

  if (STATUS_LABELS[status] && status !== "unavailable") {
    headline.appendChild(el("span", `row-status-text is-${status}`, STATUS_LABELS[status]));
  }

  let targetText = task.target_display || "";
  if (task.id === "user-temp") {
    const age = Number(task.min_age_hours ?? state.settings.cleaner_temp_min_age_hours ?? 24);
    targetText += age > 0 ? `  (${age}時間以内は保護)` : "  (保護期間なし)";
  }
  const targetEl = el("div", "row-command", targetText);
  targetEl.title = targetText;

  const descEl = el("div", "row-desc", task.description || "");
  descEl.title = task.description || "";

  const submeta = el("div", "row-submeta");
  const timeText =
    typeof task.last_duration === "number"
      ? `${formatDateTime(task.last_run_at)} · ${task.last_duration.toFixed(1)}s`
      : formatDateTime(task.last_run_at);
  submeta.appendChild(el("span", "meta-mono", timeText));

  const summaryText = task.runtime_message || task.last_summary || "";
  if (summaryText) {
    const summaryEl = el("span", "row-summary", summaryText);
    summaryEl.title = summaryText;
    submeta.appendChild(summaryEl);
  }

  main.append(headline, targetEl, descEl, submeta);

  // 右端: スキャン容量ピル + トグル + 個別実行 + ログ
  const trailing = el("div", "row-trailing");

  const sizePill = el("span", "size-pill");
  if (!task.available) {
    sizePill.textContent = "—";
  } else if (task.id === "dns-flush") {
    sizePill.textContent = "メモリ";
  } else if (task.scan_bytes !== null && task.scan_bytes !== undefined) {
    if (task.scan_bytes > 0 || (task.scan_count || 0) > 0) {
      sizePill.textContent = `${formatBytes(task.scan_bytes)} · ${task.scan_count || 0}件`;
      sizePill.classList.add("has-data");
    } else {
      sizePill.textContent = "空きなし";
    }
  } else {
    sizePill.textContent = "未スキャン";
  }

  const toggleLabel = el("label", "toggle-label");
  toggleLabel.title = task.enabled ? "一括クリーニング対象（クリックで除外）" : "一括対象から除外中（クリックで対象へ）";
  const toggleInput = el("input", "toggle-input");
  toggleInput.type = "checkbox";
  toggleInput.checked = Boolean(task.enabled);
  toggleInput.disabled = !task.available;
  toggleInput.dataset.cleanerAction = "toggle-cleaner";
  toggleInput.dataset.taskId = task.id;
  const toggleSlider = el("span", "toggle-slider");
  toggleLabel.append(toggleInput, toggleSlider);

  const runBtn = el("button", "btn btn-xs btn-secondary", "実行");
  runBtn.type = "button";
  runBtn.disabled = isBusy || !task.available;
  runBtn.dataset.cleanerAction = "run-cleaner-one";
  runBtn.dataset.taskId = task.id;

  const logBtn = el("button", "btn btn-xs btn-text", "ログ");
  logBtn.type = "button";
  logBtn.dataset.cleanerAction = "focus-cleaner-log";
  logBtn.dataset.taskId = task.id;

  trailing.append(sizePill, toggleLabel, runBtn, logBtn);
  row.append(leading, main, trailing);
  return row;
}

export function renderCleanerList() {
  const container = document.getElementById("cleanerContainer");
  container.replaceChildren();
  const frag = document.createDocumentFragment();
  for (const task of state.cleanerTasks) {
    frag.appendChild(buildCleanerRow(task));
  }
  container.appendChild(frag);
}

export function renderAppsGrid() {
  const container = document.getElementById("appsContainer");
  const emptyState = document.getElementById("emptyState");
  const visibleApps = getFilteredApps();

  container.replaceChildren();
  if (visibleApps.length === 0) {
    emptyState.classList.remove("is-hidden");
    return;
  }

  emptyState.classList.add("is-hidden");
  const frag = document.createDocumentFragment();
  visibleApps.forEach((app, idx) => {
    frag.appendChild(buildAppRow(app, idx, visibleApps.length));
  });
  container.appendChild(frag);
}

function buildLogLineNode(entry, showAppName) {
  const row = el("div", `log-line level-${entry.level || "output"}`);
  row.appendChild(el("span", "log-time", entry.timestamp));
  if (showAppName) {
    row.appendChild(el("span", "log-app", entry.app_name));
  }
  row.appendChild(el("span", "log-text", entry.line));
  return row;
}

export function renderConsole() {
  const output = document.getElementById("consoleOutput");
  const scopeBadge = document.getElementById("consoleScopeBadge");
  const focusedId = state.focusedLogAppId;

  let logs;
  let showAppName = true;
  if (focusedId) {
    if (focusedId.startsWith("cleaner:")) {
      const tid = focusedId.slice("cleaner:".length);
      const task = state.cleanerTasks.find((item) => item.id === tid);
      scopeBadge.textContent = task ? `掃除:${task.name}` : "掃除";
    } else {
      const app = state.apps.find((item) => item.id === focusedId);
      scopeBadge.textContent = app ? app.name : "個別";
    }
    logs = state.appLogs[focusedId] || [];
    showAppName = false;
  } else {
    scopeBadge.textContent = "全体";
    logs = state.globalLogs;
    showAppName = true;
  }

  output.replaceChildren();
  if (logs.length === 0) {
    output.appendChild(el("div", "console-empty", "実行ログがここに表示されます"));
    return;
  }

  const frag = document.createDocumentFragment();
  for (const entry of logs) {
    frag.appendChild(buildLogLineNode(entry, showAppName));
  }
  output.appendChild(frag);
  scrollConsoleIfNeeded();
}

export function appendConsoleLog(entry) {
  if (state.focusedLogAppId && state.focusedLogAppId !== entry.app_id) {
    return;
  }
  const output = document.getElementById("consoleOutput");
  const emptyNode = output.querySelector(".console-empty");
  if (emptyNode) {
    emptyNode.remove();
  }
  const showAppName = !state.focusedLogAppId;
  output.appendChild(buildLogLineNode(entry, showAppName));
  scrollConsoleIfNeeded();
}

function scrollConsoleIfNeeded() {
  const autoScroll = document.getElementById("autoScrollCheckbox").checked;
  if (autoScroll) {
    const output = document.getElementById("consoleOutput");
    output.scrollTop = output.scrollHeight;
  }
}

export function renderTemplates() {
  const wrap = document.getElementById("templateButtons");
  wrap.replaceChildren();
  for (const tpl of state.templates) {
    const btn = el("button", "template-chip", tpl.label);
    btn.type = "button";
    btn.title = tpl.description;
    btn.dataset.command = tpl.command;
    btn.dataset.category = tpl.category;
    btn.dataset.description = tpl.description;
    wrap.appendChild(btn);
  }
}

export function openAppModal(app = null) {
  const modal = document.getElementById("appModal");
  const title = document.getElementById("appModalTitle");

  document.getElementById("appIdField").value = app ? app.id : "";
  document.getElementById("appNameInput").value = app ? app.name : "";
  document.getElementById("appCommandInput").value = app ? app.command : "";
  document.getElementById("appCategoryInput").value = app ? app.category : "AI CLI";
  document.getElementById("appShellSelect").value = app ? app.shell || "auto" : "auto";
  document.getElementById("appEnabledInput").checked = app ? Boolean(app.enabled) : true;
  document.getElementById("appDescriptionInput").value = app ? app.description || "" : "";

  title.textContent = app ? `${app.name} を編集` : "アプリを新規登録";
  modal.classList.remove("is-hidden");
  document.getElementById("appNameInput").focus();
}

export function closeAppModal() {
  document.getElementById("appModal").classList.add("is-hidden");
}

export function openSettingsModal() {
  document.getElementById("settingsMaxWorkers").value = String(state.settings.max_workers || 4);
  document.getElementById("settingsTimeout").value = String(state.settings.timeout_seconds || 300);
  document.getElementById("settingsDefaultShell").value = state.settings.default_shell || "auto";
  document.getElementById("settingsTempMinAge").value = String(state.settings.cleaner_temp_min_age_hours ?? 24);
  document.getElementById("settingsCleanAfterUpdate").checked = Boolean(state.settings.clean_after_update_all);
  document.getElementById("settingsModal").classList.remove("is-hidden");
}

export function closeSettingsModal() {
  document.getElementById("settingsModal").classList.add("is-hidden");
}

export function openConfirmDeleteModal(app) {
  state.pendingDeleteAppId = app.id;
  document.getElementById("confirmMessage").textContent = `「${app.name}」を更新リストから解除しますか？`;
  document.getElementById("confirmModal").classList.remove("is-hidden");
}

export function closeConfirmDeleteModal() {
  state.pendingDeleteAppId = null;
  document.getElementById("confirmModal").classList.add("is-hidden");
}

export function showToast(message, type = "info") {
  const container = document.getElementById("toastContainer");
  const toast = el("div", `toast is-${type}`, message);
  container.appendChild(toast);
  window.setTimeout(() => {
    toast.remove();
  }, 2800);
}

export function renderAll() {
  renderActiveTab();
  renderCategoryFilter();
  renderHeaderAndControls();
  renderProgressPanel();
  renderAppsGrid();
  renderCleanerControls();
  renderCleanerProgress();
  renderCleanerList();
  renderConsole();
}
