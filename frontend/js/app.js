/**
 * アプリケーション全体のイベント制御・初期化モジュール。
 */

import { api } from "./api.js";
import { clearStateLogs, getFilteredApps, hydrateState, pushLog, setApps, state } from "./state.js";
import {
  appendConsoleLog,
  closeAppModal,
  closeConfirmDeleteModal,
  closeSettingsModal,
  openAppModal,
  openConfirmDeleteModal,
  openSettingsModal,
  renderAll,
  renderAppsGrid,
  renderCategoryFilter,
  renderConsole,
  renderHeaderAndControls,
  renderProgressPanel,
  renderTemplates,
  setConnectionBadge,
  showToast,
} from "./ui.js";

const THEME_STORAGE_KEY = "daily-updater-theme";

function initTheme() {
  const saved = window.localStorage.getItem(THEME_STORAGE_KEY) || "dark";
  document.documentElement.setAttribute("data-theme", saved);
}

function toggleTheme() {
  const current = document.documentElement.getAttribute("data-theme") || "dark";
  const next = current === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  window.localStorage.setItem(THEME_STORAGE_KEY, next);
}

async function handleRun(mode, appIds = []) {
  try {
    const maxWorkers = Number(document.getElementById("quickWorkersSelect").value) || state.settings.max_workers;
    const res = await api.runUpdates({ mode, appIds, maxWorkers });
    if (res.started && res.started.length > 0) {
      showToast(`${res.started.length}件のアップデートを開始しました（並列数: ${res.max_workers}）`, "success");
    } else {
      showToast(res.message || "実行対象のアプリがありません。", "error");
    }
  } catch (err) {
    showToast(err.message, "error");
  }
}

async function handleCancel(appId = null) {
  try {
    await api.cancelUpdate(appId);
    showToast(appId ? "アプリのアップデートを中止しています..." : "すべてのアップデートを中止しています...");
  } catch (err) {
    showToast(err.message, "error");
  }
}

async function handleMoveApp(appId, direction) {
  const ids = state.apps.map((app) => app.id);
  const idx = ids.indexOf(appId);
  if (idx === -1) {
    return;
  }
  const targetIdx = direction === "up" ? idx - 1 : idx + 1;
  if (targetIdx < 0 || targetIdx >= ids.length) {
    return;
  }
  [ids[idx], ids[targetIdx]] = [ids[targetIdx], ids[idx]];
  try {
    const res = await api.reorderApps(ids);
    setApps(res.apps);
    renderAppsGrid();
  } catch (err) {
    showToast(err.message, "error");
  }
}

async function handleCardClick(event) {
  const target = event.target.closest("[data-action]");
  if (!target) {
    return;
  }

  const action = target.dataset.action;
  const appId = target.dataset.appId;
  if (!appId) {
    return;
  }

  const app = state.apps.find((item) => item.id === appId);
  if (!app) {
    return;
  }

  if (action === "run-one") {
    state.focusedLogAppId = appId;
    renderConsole();
    await handleRun("selected", [appId]);
    return;
  }

  if (action === "cancel-one") {
    await handleCancel(appId);
    return;
  }

  if (action === "focus-log") {
    state.focusedLogAppId = appId;
    renderConsole();
    return;
  }

  if (action === "edit") {
    openAppModal(app);
    return;
  }

  if (action === "delete") {
    openConfirmDeleteModal(app);
    return;
  }

  if (action === "move-up") {
    await handleMoveApp(appId, "up");
    return;
  }

  if (action === "move-down") {
    await handleMoveApp(appId, "down");
  }
}

async function handleCardChange(event) {
  const target = event.target;
  const action = target.dataset?.action;
  const appId = target.dataset?.appId;
  if (!action || !appId) {
    return;
  }

  if (action === "select") {
    if (target.checked) {
      state.selectedIds.add(appId);
    } else {
      state.selectedIds.delete(appId);
    }
    renderHeaderAndControls();
    renderAppsGrid();
    return;
  }

  if (action === "toggle-enabled") {
    try {
      await api.updateApp(appId, { enabled: target.checked });
      const app = state.apps.find((item) => item.id === appId);
      if (app) {
        app.enabled = target.checked;
      }
      renderHeaderAndControls();
      renderAppsGrid();
      showToast(target.checked ? "一括更新の対象に含めました" : "一括更新の対象から除外しました");
    } catch (err) {
      target.checked = !target.checked;
      showToast(err.message, "error");
    }
  }
}

async function handleAppFormSubmit(event) {
  event.preventDefault();
  const appId = document.getElementById("appIdField").value.trim();
  const payload = {
    name: document.getElementById("appNameInput").value.trim(),
    command: document.getElementById("appCommandInput").value.trim(),
    category: document.getElementById("appCategoryInput").value.trim() || "その他",
    shell: document.getElementById("appShellSelect").value,
    enabled: document.getElementById("appEnabledInput").checked,
    description: document.getElementById("appDescriptionInput").value.trim(),
  };

  try {
    if (appId) {
      await api.updateApp(appId, payload);
      showToast(`「${payload.name}」を更新しました`, "success");
    } else {
      await api.addApp(payload);
      showToast(`「${payload.name}」を登録しました`, "success");
    }
    closeAppModal();
  } catch (err) {
    showToast(err.message, "error");
  }
}

async function handleSettingsSubmit(event) {
  event.preventDefault();
  const payload = {
    max_workers: Number(document.getElementById("settingsMaxWorkers").value),
    timeout_seconds: Number(document.getElementById("settingsTimeout").value),
    default_shell: document.getElementById("settingsDefaultShell").value,
  };

  try {
    const res = await api.updateSettings(payload);
    state.settings = res.settings;
    renderHeaderAndControls();
    closeSettingsModal();
    showToast("設定を保存しました", "success");
  } catch (err) {
    showToast(err.message, "error");
  }
}

async function handleConfirmDelete() {
  const appId = state.pendingDeleteAppId;
  if (!appId) {
    return;
  }
  try {
    const res = await api.deleteApp(appId);
    closeConfirmDeleteModal();
    showToast(`「${res.removed.name}」を登録解除しました`);
  } catch (err) {
    showToast(err.message, "error");
  }
}

async function handleImportFile(event) {
  const file = event.target.files?.[0];
  if (!file) {
    return;
  }
  try {
    const text = await file.text();
    const parsed = JSON.parse(text);
    const apps = Array.isArray(parsed) ? parsed : parsed.apps;
    if (!Array.isArray(apps)) {
      throw new Error("アプリ一覧 (apps 配列) が見つかりません。");
    }
    const res = await api.importApps(apps, false);
    setApps(res.apps);
    renderAll();
    showToast("JSON からアプリをインポートしました", "success");
  } catch (err) {
    showToast(`インポート失敗: ${err.message}`, "error");
  } finally {
    event.target.value = "";
  }
}

function handleSseEvent(eventName, payload) {
  if (eventName === "app_status") {
    state.statuses[payload.app_id] = payload;
    const app = state.apps.find((item) => item.id === payload.app_id);
    if (app && payload.last_run_at) {
      app.last_run_at = payload.last_run_at;
      app.last_status = payload.status;
      app.last_duration = payload.duration;
      app.last_summary = payload.message;
    }
    renderHeaderAndControls();
    renderAppsGrid();
    return;
  }

  if (eventName === "app_log") {
    pushLog(payload);
    appendConsoleLog(payload);
    return;
  }

  if (eventName === "batch_status") {
    state.batch = payload;
    renderHeaderAndControls();
    renderProgressPanel();
    return;
  }

  if (eventName === "apps_changed") {
    setApps(payload.apps);
    renderCategoryFilter();
    renderHeaderAndControls();
    renderAppsGrid();
    return;
  }

  if (eventName === "settings_changed") {
    state.settings = payload.settings;
    renderHeaderAndControls();
    return;
  }

  if (eventName === "logs_cleared") {
    clearStateLogs(payload.app_id || null);
    renderConsole();
  }
}

function bindStaticListeners() {
  document.getElementById("themeToggleBtn").addEventListener("click", toggleTheme);
  document.getElementById("openAddModalBtn").addEventListener("click", () => openAppModal(null));
  document.getElementById("openSettingsBtn").addEventListener("click", openSettingsModal);

  document.getElementById("runAllBtn").addEventListener("click", () => {
    state.focusedLogAppId = null;
    renderConsole();
    handleRun("all");
  });

  document.getElementById("runSelectedBtn").addEventListener("click", () => {
    const ids = Array.from(state.selectedIds);
    if (ids.length === 1) {
      state.focusedLogAppId = ids[0];
    } else {
      state.focusedLogAppId = null;
    }
    renderConsole();
    handleRun("selected", ids);
  });

  document.getElementById("retryFailedBtn").addEventListener("click", () => {
    state.focusedLogAppId = null;
    renderConsole();
    handleRun("failed");
  });

  document.getElementById("cancelAllBtn").addEventListener("click", () => handleCancel(null));

  document.getElementById("quickWorkersSelect").addEventListener("change", async (event) => {
    const workers = Number(event.target.value);
    try {
      const res = await api.updateSettings({ max_workers: workers });
      state.settings = res.settings;
      showToast(`並列実行数を ${workers} に変更しました`);
    } catch (err) {
      showToast(err.message, "error");
    }
  });

  document.getElementById("searchInput").addEventListener("input", (event) => {
    state.searchQuery = event.target.value;
    renderAppsGrid();
  });

  document.getElementById("categoryFilter").addEventListener("change", (event) => {
    state.categoryFilter = event.target.value;
    renderAppsGrid();
  });

  document.getElementById("selectAllBtn").addEventListener("click", () => {
    for (const app of getFilteredApps()) {
      state.selectedIds.add(app.id);
    }
    renderHeaderAndControls();
    renderAppsGrid();
  });

  document.getElementById("clearSelectionBtn").addEventListener("click", () => {
    state.selectedIds.clear();
    renderHeaderAndControls();
    renderAppsGrid();
  });

  const appsContainer = document.getElementById("appsContainer");
  appsContainer.addEventListener("click", handleCardClick);
  appsContainer.addEventListener("change", handleCardChange);

  // コンソール操作
  document.getElementById("showAllLogsBtn").addEventListener("click", () => {
    state.focusedLogAppId = null;
    renderConsole();
  });

  document.getElementById("copyLogsBtn").addEventListener("click", async () => {
    const logs = state.focusedLogAppId
      ? state.appLogs[state.focusedLogAppId] || []
      : state.globalLogs;
    const text = logs
      .map((entry) => `[${entry.timestamp}] [${entry.app_name}] ${entry.line}`)
      .join("\n");
    try {
      await navigator.clipboard.writeText(text);
      showToast("ログをクリップボードにコピーしました", "success");
    } catch {
      showToast("クリップボードへのコピーに失敗しました", "error");
    }
  });

  document.getElementById("clearLogsBtn").addEventListener("click", async () => {
    try {
      await api.clearLogs(state.focusedLogAppId);
    } catch (err) {
      showToast(err.message, "error");
    }
  });

  // テンプレートボタン
  document.getElementById("templateButtons").addEventListener("click", (event) => {
    const btn = event.target.closest(".template-chip");
    if (!btn) {
      return;
    }
    document.getElementById("appCommandInput").value = btn.dataset.command || "";
    document.getElementById("appCategoryInput").value = btn.dataset.category || "その他";
    if (!document.getElementById("appDescriptionInput").value) {
      document.getElementById("appDescriptionInput").value = btn.dataset.description || "";
    }
    document.getElementById("appCommandInput").focus();
  });

  // モーダル制御
  document.getElementById("appForm").addEventListener("submit", handleAppFormSubmit);
  document.getElementById("closeAppModalBtn").addEventListener("click", closeAppModal);
  document.getElementById("cancelAppModalBtn").addEventListener("click", closeAppModal);

  document.getElementById("settingsForm").addEventListener("submit", handleSettingsSubmit);
  document.getElementById("closeSettingsModalBtn").addEventListener("click", closeSettingsModal);
  document.getElementById("cancelSettingsModalBtn").addEventListener("click", closeSettingsModal);

  document.getElementById("exportJsonBtn").addEventListener("click", () => {
    window.location.href = "/api/export";
  });
  document.getElementById("importJsonBtn").addEventListener("click", () => {
    document.getElementById("importFileInput").click();
  });
  document.getElementById("importFileInput").addEventListener("change", handleImportFile);

  document.getElementById("closeConfirmModalBtn").addEventListener("click", closeConfirmDeleteModal);
  document.getElementById("cancelConfirmBtn").addEventListener("click", closeConfirmDeleteModal);
  document.getElementById("acceptConfirmBtn").addEventListener("click", handleConfirmDelete);

  // ESC キーでモーダルを閉じる
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeAppModal();
      closeSettingsModal();
      closeConfirmDeleteModal();
    }
  });
}

async function init() {
  initTheme();
  bindStaticListeners();

  try {
    const bootstrap = await api.bootstrap();
    hydrateState(bootstrap);
    renderTemplates();
    renderAll();

    api.connectEvents({
      onConnectionChange: setConnectionBadge,
      onEvent: handleSseEvent,
    });
  } catch (err) {
    setConnectionBadge("offline");
    showToast(`初期データの読み込みに失敗しました: ${err.message}`, "error");
  }
}

document.addEventListener("DOMContentLoaded", init);
