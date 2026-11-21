"""HTTP/REST API および静的ファイル・SSE 配信を行う Web サーバーモジュール。"""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import mimetypes
from pathlib import Path
import queue
from typing import Any
from urllib.parse import parse_qs, urlparse

from backend.cleaner import PCCleaner
from backend.events import EventBroker
from backend.runner import UpdateRunner
from backend.storage import AppStorage
from config import COMMAND_TEMPLATES, FRONTEND_DIR

logger = logging.getLogger("daily_updater.server")


class UpdaterHTTPServer(ThreadingHTTPServer):
    """依存オブジェクト（ストレージ・ランナー・クリーナー・イベントブローカー）を保持するサーバー。"""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        storage: AppStorage,
        runner: UpdateRunner,
        broker: EventBroker,
        cleaner: PCCleaner | None = None,
    ) -> None:
        self.storage = storage
        self.runner = runner
        self.broker = broker
        self.cleaner = cleaner or PCCleaner(
            storage=storage,
            broker=broker,
            log_callback=runner.append_external_log,
        )
        super().__init__(server_address, UpdaterRequestHandler)


class UpdaterRequestHandler(BaseHTTPRequestHandler):
    """REST API、SSE ストリーム、およびフロントエンド静的ファイルを処理するハンドラ。"""

    server: UpdaterHTTPServer

    def log_message(self, format: str, *args: Any) -> None:
        """標準エラーへの冗長なアクセスログを抑制し、デバッグログへ流す。"""
        logger.debug("%s - %s", self.address_string(), format % args)

    def _send_json(self, data: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> None:
        self._send_json({"error": message}, status=status)

    def _read_json_body(self) -> dict[str, Any]:
        length_str = self.headers.get("Content-Length", "0")
        try:
            length = int(length_str)
        except ValueError as exc:
            raise ValueError("不正な Content-Length です。") from exc
        if length <= 0:
            return {}
        if length > 2 * 1024 * 1024:
            raise ValueError("リクエストサイズが大きすぎます。")
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON の解析に失敗しました: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("リクエストボディは JSON オブジェクトである必要があります。")
        return parsed

    def _serve_static(self, rel_path: str) -> None:
        """フロントエンドの静的ファイルを安全に配信する。"""
        clean_path = rel_path.lstrip("/")
        if not clean_path:
            clean_path = "index.html"

        base = FRONTEND_DIR.resolve()
        target = (base / clean_path).resolve()
        try:
            target.relative_to(base)
        except ValueError:
            self._send_error_json("不正なパスです。", HTTPStatus.FORBIDDEN)
            return

        if not target.exists() or not target.is_file():
            self._send_error_json("ファイルが見つかりません。", HTTPStatus.NOT_FOUND)
            return

        content_type, _ = mimetypes.guess_type(str(target))
        if target.suffix == ".js":
            content_type = "application/javascript; charset=utf-8"
        elif target.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif target.suffix == ".html":
            content_type = "text/html; charset=utf-8"
        elif target.suffix == ".ico":
            content_type = "image/x-icon"
        elif target.suffix == ".png":
            content_type = "image/png"
        elif not content_type:
            content_type = "application/octet-stream"

        data = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _handle_sse(self) -> None:
        """Server-Sent Events ストリームを確立し、リアルタイムイベントを配信する。"""
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        client_queue = self.server.broker.subscribe()
        try:
            # 接続直後に初期接続イベントを送信
            self.wfile.write(b"event: connected\ndata: {\"ok\": true}\n\n")
            self.wfile.flush()

            while True:
                try:
                    msg = client_queue.get(timeout=15.0)
                    self.wfile.write(msg.encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    # 接続維持用ハートビート
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            self.server.broker.unsubscribe(client_queue)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/events":
            self._handle_sse()
            return

        if path == "/api/bootstrap":
            self._send_json(
                {
                    "apps": self.server.storage.list_apps(),
                    "settings": self.server.storage.get_settings(),
                    "templates": COMMAND_TEMPLATES,
                    "runtime": self.server.runner.get_runtime_snapshot(),
                    "cleaner": self.server.cleaner.get_snapshot(),
                }
            )
            return

        if path == "/api/cleaner":
            self._send_json(self.server.cleaner.get_snapshot())
            return

        if path == "/api/apps":
            self._send_json({"apps": self.server.storage.list_apps()})
            return

        if path == "/api/settings":
            self._send_json({"settings": self.server.storage.get_settings()})
            return

        if path == "/api/export":
            bundle = self.server.storage.export_bundle()
            body = json.dumps(bundle, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header(
                "Content-Disposition",
                'attachment; filename="daily-updater-backup.json"',
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path.startswith("/api/"):
            self._send_error_json("API エンドポイントが見つかりません。", HTTPStatus.NOT_FOUND)
            return

        self._serve_static(path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        try:
            body = self._read_json_body()
        except ValueError as exc:
            self._send_error_json(str(exc), HTTPStatus.BAD_REQUEST)
            return

        try:
            if path == "/api/apps":
                created = self.server.storage.add_app(body)
                self.server.broker.publish("apps_changed", {"apps": self.server.storage.list_apps()})
                self._send_json({"app": created}, HTTPStatus.CREATED)
                return

            if path == "/api/apps/reorder":
                ordered_ids = body.get("ordered_ids", [])
                if not isinstance(ordered_ids, list):
                    raise ValueError("ordered_ids はリストである必要があります。")
                apps = self.server.storage.reorder_apps([str(x) for x in ordered_ids])
                self.server.broker.publish("apps_changed", {"apps": apps})
                self._send_json({"apps": apps})
                return

            if path == "/api/apps/import":
                apps_list = body.get("apps")
                if apps_list is None and isinstance(body.get("data"), dict):
                    apps_list = body["data"].get("apps")
                replace = bool(body.get("replace", False))
                apps = self.server.storage.import_apps(apps_list, replace=replace)
                self.server.broker.publish("apps_changed", {"apps": apps})
                self._send_json({"apps": apps})
                return

            if path == "/api/run":
                mode = str(body.get("mode", "selected"))
                max_workers = body.get("max_workers")
                if max_workers is not None:
                    max_workers = int(max_workers)

                if mode == "all":
                    app_ids = [
                        app["id"]
                        for app in self.server.storage.list_apps()
                        if app.get("enabled", True)
                    ]
                elif mode == "failed":
                    runtime = self.server.runner.get_runtime_snapshot()["statuses"]
                    app_ids = []
                    for app in self.server.storage.list_apps():
                        rt_status = runtime.get(app["id"], {}).get("status")
                        last_status = rt_status or app.get("last_status")
                        if last_status == "failed":
                            app_ids.append(app["id"])
                else:
                    raw_ids = body.get("app_ids", [])
                    if not isinstance(raw_ids, list):
                        raise ValueError("app_ids はリストである必要があります。")
                    app_ids = [str(x) for x in raw_ids]

                result = self.server.runner.start_run(
                    app_ids,
                    max_workers=max_workers,
                    trigger_mode=mode,
                )
                self._send_json(result)
                return

            if path == "/api/cleaner/scan":
                raw_ids = body.get("task_ids")
                task_ids = [str(x) for x in raw_ids] if isinstance(raw_ids, list) else None
                result = self.server.cleaner.start_scan(task_ids=task_ids)
                self._send_json(result)
                return

            if path == "/api/cleaner/run":
                mode = str(body.get("mode", "enabled"))
                raw_ids = body.get("task_ids")
                task_ids = [str(x) for x in raw_ids] if isinstance(raw_ids, list) else None
                result = self.server.cleaner.start_clean(mode=mode, task_ids=task_ids)
                self._send_json(result)
                return

            if path == "/api/cleaner/cancel":
                cancelled = self.server.cleaner.cancel()
                self._send_json({"cancelled": cancelled})
                return

            if path == "/api/cancel":
                app_id = body.get("app_id")
                if app_id:
                    cancelled = self.server.runner.cancel_app(str(app_id))
                    self._send_json({"cancelled": cancelled, "app_id": str(app_id)})
                else:
                    cancelled_ids = self.server.runner.cancel_all()
                    self._send_json({"cancelled_ids": cancelled_ids})
                return

            if path == "/api/logs/clear":
                app_id = body.get("app_id")
                self.server.runner.clear_logs(str(app_id) if app_id else None)
                self._send_json({"ok": True})
                return

            self._send_error_json("API エンドポイントが見つかりません。", HTTPStatus.NOT_FOUND)
        except KeyError as exc:
            self._send_error_json(str(exc), HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self._send_error_json(str(exc), HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            logger.exception("POST %s の処理中にエラーが発生しました: %s", path, exc)
            self._send_error_json("サーバー内部エラーが発生しました。", HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_PUT(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        try:
            body = self._read_json_body()
        except ValueError as exc:
            self._send_error_json(str(exc), HTTPStatus.BAD_REQUEST)
            return

        try:
            if path == "/api/settings":
                updated = self.server.storage.update_settings(body)
                self.server.broker.publish("settings_changed", {"settings": updated})
                self.server.broker.publish("cleaner_snapshot", self.server.cleaner.get_snapshot())
                self._send_json({"settings": updated})
                return

            if path.startswith("/api/cleaner/tasks/"):
                task_id = path[len("/api/cleaner/tasks/") :].strip("/")
                if not task_id:
                    raise ValueError("クリーニング項目IDが指定されていません。")
                if "enabled" not in body:
                    raise ValueError("enabled フィールドを指定してください。")
                updated = self.server.cleaner.set_task_enabled(task_id, bool(body["enabled"]))
                self._send_json({"task_id": task_id, "state": updated})
                return

            if path.startswith("/api/apps/"):
                app_id = path[len("/api/apps/") :].strip("/")
                if not app_id:
                    raise ValueError("アプリIDが指定されていません。")
                updated = self.server.storage.update_app(app_id, body)
                self.server.broker.publish("apps_changed", {"apps": self.server.storage.list_apps()})
                self._send_json({"app": updated})
                return

            self._send_error_json("API エンドポイントが見つかりません。", HTTPStatus.NOT_FOUND)
        except KeyError as exc:
            self._send_error_json(str(exc), HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self._send_error_json(str(exc), HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            logger.exception("PUT %s の処理中にエラーが発生しました: %s", path, exc)
            self._send_error_json("サーバー内部エラーが発生しました。", HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        _ = parse_qs(parsed.query)

        try:
            if path.startswith("/api/apps/"):
                app_id = path[len("/api/apps/") :].strip("/")
                if not app_id:
                    raise ValueError("アプリIDが指定されていません。")
                self.server.runner.cancel_app(app_id)
                removed = self.server.storage.delete_app(app_id)
                self.server.broker.publish("apps_changed", {"apps": self.server.storage.list_apps()})
                self._send_json({"removed": removed})
                return

            self._send_error_json("API エンドポイントが見つかりません。", HTTPStatus.NOT_FOUND)
        except KeyError as exc:
            self._send_error_json(str(exc), HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self._send_error_json(str(exc), HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            logger.exception("DELETE %s の処理中にエラーが発生しました: %s", path, exc)
            self._send_error_json("サーバー内部エラーが発生しました。", HTTPStatus.INTERNAL_SERVER_ERROR)
