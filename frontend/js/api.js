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
