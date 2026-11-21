/**
 * バックエンド REST API および SSE 通信を担当するモジュール。
 */

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || `HTTP エラー (${response.status})`);
  }
  return data;
}

export const api = {
  bootstrap() {
    return requestJson("/api/bootstrap");
  },

  addApp(payload) {
    return requestJson("/api/apps", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  updateApp(appId, payload) {
    return requestJson(`/api/apps/${encodeURIComponent(appId)}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  },

  deleteApp(appId) {
    return requestJson(`/api/apps/${encodeURIComponent(appId)}`, {
      method: "DELETE",
    });
  },

  reorderApps(orderedIds) {
    return requestJson("/api/apps/reorder", {
      method: "POST",
      body: JSON.stringify({ ordered_ids: orderedIds }),
    });
  },

  importApps(apps, replace = false) {
    return requestJson("/api/apps/import", {
      method: "POST",
      body: JSON.stringify({ apps, replace }),
    });
  },

  updateSettings(payload) {
    return requestJson("/api/settings", {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  },

  runUpdates({ mode = "selected", appIds = [], maxWorkers = null }) {
    return requestJson("/api/run", {
      method: "POST",
      body: JSON.stringify({
        mode,
        app_ids: appIds,
        max_workers: maxWorkers,
      }),
    });
  },

  cancelUpdate(appId = null) {
    return requestJson("/api/cancel", {
      method: "POST",
      body: JSON.stringify(appId ? { app_id: appId } : {}),
    });
  },

  clearLogs(appId = null) {
    return requestJson("/api/logs/clear", {
      method: "POST",
      body: JSON.stringify(appId ? { app_id: appId } : {}),
    });
  },

  getCleaner() {
    return requestJson("/api/cleaner");
  },

  toggleCleanerTask(taskId, enabled) {
    return requestJson(`/api/cleaner/tasks/${encodeURIComponent(taskId)}`, {
      method: "PUT",
      body: JSON.stringify({ enabled }),
    });
  },

  scanCleaner(taskIds = null) {
    return requestJson("/api/cleaner/scan", {
      method: "POST",
      body: JSON.stringify(taskIds ? { task_ids: taskIds } : {}),
    });
  },

  runCleaner({ mode = "enabled", taskIds = null } = {}) {
    return requestJson("/api/cleaner/run", {
      method: "POST",
      body: JSON.stringify({
        mode,
        ...(taskIds ? { task_ids: taskIds } : {}),
      }),
    });
  },

  cancelCleaner() {
    return requestJson("/api/cleaner/cancel", {
      method: "POST",
      body: JSON.stringify({}),
    });
  },

  /**
   * SSE ストリームへ接続する。
   */
  connectEvents(handlers) {
    const source = new EventSource("/api/events");

    source.onopen = () => {
      handlers.onConnectionChange?.("online");
    };

    source.onerror = () => {
      handlers.onConnectionChange?.("offline");
    };

    const eventNames = [
      "connected",
      "app_status",
      "app_log",
      "batch_status",
      "apps_changed",
      "settings_changed",
      "logs_cleared",
      "cleaner_snapshot",
    ];

    for (const eventName of eventNames) {
      source.addEventListener(eventName, (event) => {
        try {
          const payload = JSON.parse(event.data);
          handlers.onEvent?.(eventName, payload);
        } catch (err) {
          console.error("SSE イベントの解析に失敗しました:", eventName, err);
        }
      });
    }

    return source;
  },
};
