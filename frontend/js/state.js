/**
 * フロントエンドのアプリ状態を集中管理するモジュール。
 */

const MAX_LOG_ITEMS = 1000;

export const state = {
  activeTab: "updates",
  apps: [],
  settings: {
    max_workers: 4,
    default_shell: "auto",
    timeout_seconds: 300,
    cleaner_temp_min_age_hours: 24,
    clean_after_update_all: false,
  },
  templates: [],
  shells: {
    resolved_auto: "pwsh",
    pwsh: true,
    powershell: true,
    cmd: true,
  },
  statuses: {},
  batch: {
    active: false,
    total: 0,
    completed: 0,
    succeeded: 0,
    failed: 0,
    cancelled: 0,
    running: 0,
    queued: 0,
  },
  cleanerTasks: [],
  cleanerState: {
    active: false,
    mode: null,
    total: 0,
    completed: 0,
    succeeded: 0,
    failed: 0,
    skipped: 0,
    total_reclaimable_bytes: 0,
    last_freed_bytes: 0,
    last_scan_at: null,
    last_clean_at: null,
  },
  cleanerAutoScanned: false,
  globalLogs: [],
  appLogs: {},
  selectedIds: new Set(),
  focusedLogAppId: null,
  searchQuery: "",
  categoryFilter: "all",
  pendingDeleteAppId: null,
};

export function hydrateState(bootstrap) {
  state.apps = bootstrap.apps || [];
  state.settings = { ...state.settings, ...(bootstrap.settings || {}) };
  state.templates = bootstrap.templates || [];

  const runtime = bootstrap.runtime || {};
  state.statuses = runtime.statuses || {};
  state.batch = runtime.batch || state.batch;
  state.globalLogs = runtime.global_logs || [];
  state.appLogs = runtime.app_logs || {};
  state.shells = runtime.shells || state.shells;

  if (bootstrap.cleaner) {
    setCleanerSnapshot(bootstrap.cleaner);
  }

  pruneSelection();
}

export function setCleanerSnapshot(snapshot) {
  if (!snapshot) {
    return;
  }
  state.cleanerTasks = snapshot.tasks || [];
  state.cleanerState = { ...state.cleanerState, ...(snapshot.state || {}) };
  if (state.cleanerState.last_scan_at) {
    state.cleanerAutoScanned = true;
  }
}

export function setApps(apps) {
  state.apps = apps || [];
  pruneSelection();
}

export function pruneSelection() {
  const validIds = new Set(state.apps.map((app) => app.id));
  for (const id of Array.from(state.selectedIds)) {
    if (!validIds.has(id)) {
      state.selectedIds.delete(id);
    }
  }
  if (
    state.focusedLogAppId &&
    !state.focusedLogAppId.startsWith("cleaner:") &&
    !validIds.has(state.focusedLogAppId)
  ) {
    state.focusedLogAppId = null;
  }
}

export function getEffectiveStatus(app) {
  const rt = state.statuses[app.id];
  if (rt && rt.status) {
    return rt.status;
  }
  return app.last_status || "idle";
}

export function getFilteredApps() {
  const query = state.searchQuery.trim().toLowerCase();
  const category = state.categoryFilter;

  return state.apps.filter((app) => {
    if (category !== "all" && app.category !== category) {
      return false;
    }
    if (!query) {
      return true;
    }
    const haystack = [app.name, app.command, app.category, app.description]
      .join(" ")
      .toLowerCase();
    return haystack.includes(query);
  });
}

export function pushLog(entry) {
  state.globalLogs.push(entry);
  if (state.globalLogs.length > MAX_LOG_ITEMS) {
    state.globalLogs.shift();
  }

  if (!state.appLogs[entry.app_id]) {
    state.appLogs[entry.app_id] = [];
  }
  const buf = state.appLogs[entry.app_id];
  buf.push(entry);
  if (buf.length > MAX_LOG_ITEMS) {
    buf.shift();
  }
}

export function clearStateLogs(appId = null) {
  if (appId) {
    state.appLogs[appId] = [];
  } else {
    state.globalLogs = [];
    state.appLogs = {};
  }
}
