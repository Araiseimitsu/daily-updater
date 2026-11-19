"""CLIアップデートコマンドの並列実行エンジンとプロセス管理モジュール。"""

from __future__ import annotations

import base64
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from typing import Any

from backend.events import EventBroker
from backend.storage import AppStorage

logger = logging.getLogger("daily_updater.runner")

# ANSI エスケープシーケンス（カラー・カーソル制御・OSC等）を除去する正規表現
ANSI_ESCAPE_RE = re.compile(
    r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x07\x1B]*(?:\x07|\x1B\\))"
)

MAX_APP_LOG_LINES = 500
MAX_GLOBAL_LOG_LINES = 1000


def _timestamp_short() -> str:
    """ログ表示用の時刻文字列 (HH:MM:SS) を返す。"""
    return datetime.now().strftime("%H:%M:%S")


def _clean_line(raw_bytes: bytes) -> str:
    """プロセス出力の1行を安全にデコードし、ANSI制御文字を除去する。"""
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = raw_bytes.decode("cp932", errors="replace")
    text = ANSI_ESCAPE_RE.sub("", text)
    return text.rstrip("\r\n")


def _kill_process_tree(pid: int) -> None:
    """Windows環境で子プロセスを含めてプロセスツリー全体を停止する。"""
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return
        except Exception as exc:
            logger.debug("taskkill 呼び出しに失敗しました (PID=%d): %s", pid, exc)
    try:
        os.kill(pid, 9)
    except OSError:
        pass


class UpdateRunner:
    """複数アプリのアップデートを並列実行し、状態とログをリアルタイム配信する。"""

    def __init__(self, storage: AppStorage, broker: EventBroker) -> None:
        self._storage = storage
        self._broker = broker
        self._lock = threading.RLock()

        # 利用可能な実行シェルをキャッシュ検出
        self._pwsh_path: str | None = shutil.which("pwsh")
        self._powershell_path: str | None = shutil.which("powershell")
        self._cmd_path: str = shutil.which("cmd") or "cmd.exe"

        # アプリごとの最新ランタイム状態とログバッファ
        self._runtime_status: dict[str, dict[str, Any]] = {}
        self._app_logs: dict[str, deque[dict[str, str]]] = {}
        self._global_logs: deque[dict[str, str]] = deque(maxlen=MAX_GLOBAL_LOG_LINES)

        # アクティブなプロセスおよびキャンセル管理
        self._active_processes: dict[str, subprocess.Popen[bytes]] = {}
        self._cancel_flags: set[str] = set()
        self._timeout_flags: set[str] = set()
        self._queued_or_running: set[str] = set()

        # 直近のバッチ進捗状態
        self._batch_state: dict[str, Any] = {
            "active": False,
            "total": 0,
            "completed": 0,
            "succeeded": 0,
            "failed": 0,
            "cancelled": 0,
            "running": 0,
            "queued": 0,
            "started_at": None,
            "finished_at": None,
            "target_ids": [],
        }

    def get_available_shells(self) -> dict[str, Any]:
        """環境で利用可能なシェル情報を返す。"""
        resolved_auto = self._resolve_shell("auto")
        return {
            "resolved_auto": resolved_auto,
            "pwsh": bool(self._pwsh_path),
            "powershell": bool(self._powershell_path),
            "cmd": True,
        }

    def _resolve_shell(self, requested: str) -> str:
        """設定と利用可能シェルから実際に使用するシェル種別を決定する。"""
        choice = requested
        if choice == "auto":
            default_shell = self._storage.get_settings().get("default_shell", "auto")
            choice = default_shell

        if choice == "pwsh" and self._pwsh_path:
            return "pwsh"
        if choice == "powershell" and self._powershell_path:
            return "powershell"
        if choice == "cmd":
            return "cmd"

        # auto または指定シェル不在時のフォールバック
        if self._pwsh_path:
            return "pwsh"
        if self._powershell_path:
            return "powershell"
        return "cmd"

    def _build_command(self, command: str, shell_kind: str) -> list[str]:
        """シェルに応じた安全な実行コマンド配列を構築する。"""
        if shell_kind in ("pwsh", "powershell"):
            exe = self._pwsh_path if shell_kind == "pwsh" else self._powershell_path
            if not exe:
                exe = "powershell.exe"
            # プロファイルを読み込んでユーザー関数 (例: agent) を利用可能にし、
            # UTF-8 出力と正確な終了コード伝播を保証する。
            script = (
                "$ErrorActionPreference = 'Continue'\n"
                "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n"
                "$OutputEncoding = [System.Text.Encoding]::UTF8\n"
                "$global:LASTEXITCODE = 0\n"
                "& {\n"
                f"{command}\n"
                "}\n"
                "if ($null -ne $LASTEXITCODE -and $LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n"
                "if (-not $?) { exit 1 }\n"
                "exit 0\n"
            )
            encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
            return [exe, "-NoLogo", "-EncodedCommand", encoded]

        return [self._cmd_path, "/d", "/c", f"chcp 65001 >nul && {command}"]

    def get_runtime_snapshot(self) -> dict[str, Any]:
        """現在のランタイム状態・バッチ進捗・ログのスナップショットを返す。"""
        with self._lock:
            return {
                "statuses": dict(self._runtime_status),
                "batch": dict(self._batch_state),
                "global_logs": list(self._global_logs),
                "app_logs": {k: list(v) for k, v in self._app_logs.items()},
                "shells": self.get_available_shells(),
            }

    def clear_logs(self, app_id: str | None = None) -> None:
        """ログバッファをクリアする。app_id 省略時は全ログをクリアする。"""
        with self._lock:
            if app_id:
                self._app_logs.pop(app_id, None)
            else:
                self._app_logs.clear()
                self._global_logs.clear()
        self._broker.publish("logs_cleared", {"app_id": app_id})

    def start_run(
        self,
        app_ids: list[str],
        max_workers: int | None = None,
    ) -> dict[str, Any]:
        """指定されたアプリ群のアップデートをバックグラウンドで並列開始する。"""
        unique_ids: list[str] = []
        seen: set[str] = set()
        for aid in app_ids:
            if aid not in seen:
                seen.add(aid)
                unique_ids.append(aid)

        apps_to_run: list[dict[str, Any]] = []
        skipped_ids: list[str] = []

        with self._lock:
            for aid in unique_ids:
                app = self._storage.get_app(aid)
                if app is None:
                    continue
                if aid in self._queued_or_running:
                    skipped_ids.append(aid)
                    continue
                apps_to_run.append(app)

            if not apps_to_run:
                return {
                    "started": [],
                    "skipped": skipped_ids,
                    "message": "実行可能な対象アプリがありません（すでに実行中か未登録です）。",
                }

            settings = self._storage.get_settings()
            workers = max_workers if max_workers is not None else int(settings.get("max_workers", 4))
            workers = max(1, min(12, workers))
            timeout_seconds = int(settings.get("timeout_seconds", 300))

            started_ids = [app["id"] for app in apps_to_run]
            for app in apps_to_run:
                aid = app["id"]
                self._queued_or_running.add(aid)
                self._cancel_flags.discard(aid)
                self._timeout_flags.discard(aid)
                self._app_logs[aid] = deque(maxlen=MAX_APP_LOG_LINES)
                self._set_app_status_locked(
                    aid,
                    status="queued",
                    message="実行待機中...",
                    started_at=None,
                    duration=None,
                    exit_code=None,
                )

            # バッチ進捗の更新（新規開始または追加合流）
            if not self._batch_state["active"]:
                self._batch_state = {
                    "active": True,
                    "total": len(started_ids),
                    "completed": 0,
                    "succeeded": 0,
                    "failed": 0,
                    "cancelled": 0,
                    "running": 0,
                    "queued": len(started_ids),
                    "started_at": datetime.now().isoformat(timespec="seconds"),
                    "finished_at": None,
                    "target_ids": list(started_ids),
                }
            else:
                self._batch_state["total"] += len(started_ids)
                self._batch_state["queued"] += len(started_ids)
                self._batch_state["target_ids"].extend(started_ids)

            self._publish_batch_locked()

        coordinator = threading.Thread(
            target=self._execute_batch,
            args=(apps_to_run, workers, timeout_seconds),
            daemon=True,
            name="updater-batch-coordinator",
        )
        coordinator.start()

        logger.info("アップデートを開始しました: %d件 (並列数=%d)", len(started_ids), workers)
        return {
            "started": started_ids,
            "skipped": skipped_ids,
            "max_workers": workers,
        }

    def cancel_app(self, app_id: str) -> bool:
        """指定アプリの実行または待機をキャンセルする。"""
        with self._lock:
            if app_id not in self._queued_or_running:
                return False
            self._cancel_flags.add(app_id)
            proc = self._active_processes.get(app_id)

        if proc is not None and proc.poll() is None:
            self._append_log(app_id, "", "ユーザー操作により中止を要求しました...", level="warn")
            _kill_process_tree(proc.pid)
        return True

    def cancel_all(self) -> list[str]:
        """待機中・実行中の全アプリをキャンセルする。"""
        with self._lock:
            targets = list(self._queued_or_running)
        cancelled: list[str] = []
        for aid in targets:
            if self.cancel_app(aid):
                cancelled.append(aid)
        return cancelled

    def _execute_batch(
        self,
        apps: list[dict[str, Any]],
        max_workers: int,
        timeout_seconds: int,
    ) -> None:
        """ThreadPoolExecutor を用いて対象アプリを並列実行する。"""
        effective_workers = max(1, min(max_workers, len(apps)))
        futures: list[Future[None]] = []
        with ThreadPoolExecutor(max_workers=effective_workers, thread_name_prefix="app-worker") as executor:
            for app in apps:
                futures.append(executor.submit(self._run_single_app, app, timeout_seconds))
            for fut in futures:
                try:
                    fut.result()
                except Exception as exc:
                    logger.exception("ワーカースレッドで予期しないエラーが発生しました: %s", exc)

        with self._lock:
            if not self._queued_or_running and self._batch_state["active"]:
                self._batch_state["active"] = False
                self._batch_state["running"] = 0
                self._batch_state["queued"] = 0
                self._batch_state["finished_at"] = datetime.now().isoformat(timespec="seconds")
                self._publish_batch_locked()

    def _run_single_app(self, app: dict[str, Any], timeout_seconds: int) -> None:
        """単一アプリのアップデートコマンドを実行し、出力を逐次配信する。"""
        app_id = app["id"]
        app_name = app["name"]
        command = app["command"]
        shell_kind = self._resolve_shell(app.get("shell", "auto"))

        # キュー待機中にキャンセルされた場合
        with self._lock:
            if app_id in self._cancel_flags:
                self._finish_app_locked(
                    app_id=app_id,
                    status="cancelled",
                    exit_code=None,
                    duration=0.0,
                    summary="実行前にキャンセルされました。",
                    was_running=False,
                )
                return

            self._batch_state["queued"] = max(0, self._batch_state["queued"] - 1)
            self._batch_state["running"] += 1
            start_monotonic = time.monotonic()
            started_iso = datetime.now().isoformat(timespec="seconds")
            self._set_app_status_locked(
                app_id,
                status="running",
                message=f"実行中 ({shell_kind})...",
                started_at=started_iso,
                duration=0.0,
                exit_code=None,
            )
            self._publish_batch_locked()

        self._append_log(app_id, app_name, f"$ {command}  [shell: {shell_kind}]", level="info")

        cmd_args = self._build_command(command, shell_kind)
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"

        last_meaningful_line = ""
        exit_code: int | None = None
        proc: subprocess.Popen[bytes] | None = None
        timer: threading.Timer | None = None

        try:
            proc = subprocess.Popen(
                cmd_args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=env,
                bufsize=0,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

            with self._lock:
                self._active_processes[app_id] = proc

            def _on_timeout() -> None:
                with self._lock:
                    self._timeout_flags.add(app_id)
                if proc is not None and proc.poll() is None:
                    self._append_log(
                        app_id,
                        app_name,
                        f"タイムアウト ({timeout_seconds}秒) に達したためプロセスを停止します。",
                        level="error",
                    )
                    _kill_process_tree(proc.pid)

            timer = threading.Timer(timeout_seconds, _on_timeout)
            timer.daemon = True
            timer.start()

            if proc.stdout is not None:
                buffer = bytearray()
                while True:
                    chunk = proc.stdout.read(256)
                    if not chunk:
                        break
                    buffer.extend(chunk)
                    while True:
                        nl_idx = buffer.find(b"\n")
                        cr_idx = buffer.find(b"\r")
                        if nl_idx == -1 and cr_idx == -1:
                            break
                        if nl_idx != -1 and (cr_idx == -1 or nl_idx < cr_idx):
                            split_idx = nl_idx
                        else:
                            split_idx = cr_idx
                        raw_line = bytes(buffer[:split_idx])
                        # \r\n を連続で消費する
                        advance = split_idx + 1
                        if (
                            split_idx == cr_idx
                            and advance < len(buffer)
                            and buffer[advance] == 10
                        ):
                            advance += 1
                        del buffer[:advance]

                        cleaned = _clean_line(raw_line)
                        if cleaned.strip():
                            last_meaningful_line = cleaned.strip()
                            self._append_log(app_id, app_name, cleaned, level="output")

                if buffer:
                    cleaned = _clean_line(bytes(buffer))
                    if cleaned.strip():
                        last_meaningful_line = cleaned.strip()
                        self._append_log(app_id, app_name, cleaned, level="output")

            exit_code = proc.wait()
        except FileNotFoundError as exc:
            err_msg = f"実行シェルまたはコマンドが見つかりません: {exc}"
            last_meaningful_line = err_msg
            self._append_log(app_id, app_name, err_msg, level="error")
            exit_code = 127
        except Exception as exc:
            err_msg = f"実行中にエラーが発生しました: {exc}"
            last_meaningful_line = err_msg
            self._append_log(app_id, app_name, err_msg, level="error")
            exit_code = 1
        finally:
            if timer is not None:
                timer.cancel()
            if proc is not None and proc.stdout is not None:
                try:
                    proc.stdout.close()
                except OSError:
                    pass
            with self._lock:
                self._active_processes.pop(app_id, None)

        duration = time.monotonic() - start_monotonic

        with self._lock:
            is_cancelled = app_id in self._cancel_flags
            is_timeout = app_id in self._timeout_flags

        if is_cancelled:
            status = "cancelled"
            summary = "ユーザーにより中止されました。"
            self._append_log(app_id, app_name, f"中止 ({duration:.1f}秒)", level="warn")
        elif is_timeout:
            status = "failed"
            summary = f"タイムアウトしました ({timeout_seconds}秒)。"
            self._append_log(app_id, app_name, summary, level="error")
        elif exit_code == 0:
            status = "success"
            summary = last_meaningful_line or "正常に完了しました。"
            self._append_log(
                app_id,
                app_name,
                f"完了しました (終了コード: 0 / {duration:.1f}秒)",
                level="success",
            )
        else:
            status = "failed"
            summary = last_meaningful_line or f"終了コード {exit_code} で失敗しました。"
            self._append_log(
                app_id,
                app_name,
                f"失敗しました (終了コード: {exit_code} / {duration:.1f}秒)",
                level="error",
            )

        with self._lock:
            self._finish_app_locked(
                app_id=app_id,
                status=status,
                exit_code=exit_code,
                duration=duration,
                summary=summary,
                was_running=True,
            )

    def _finish_app_locked(
        self,
        app_id: str,
        status: str,
        exit_code: int | None,
        duration: float,
        summary: str,
        was_running: bool,
    ) -> None:
        """単一アプリの完了状態を集計し、永続化およびSSE通知を行う（ロック保持前提）。"""
        self._queued_or_running.discard(app_id)
        self._cancel_flags.discard(app_id)
        self._timeout_flags.discard(app_id)

        if was_running:
            self._batch_state["running"] = max(0, self._batch_state["running"] - 1)
        else:
            self._batch_state["queued"] = max(0, self._batch_state["queued"] - 1)

        self._batch_state["completed"] += 1
        if status == "success":
            self._batch_state["succeeded"] += 1
        elif status == "cancelled":
            self._batch_state["cancelled"] += 1
        else:
            self._batch_state["failed"] += 1

        updated_app = self._storage.record_run_result(
            app_id=app_id,
            status=status,
            exit_code=exit_code,
            duration=duration,
            summary=summary,
        )

        self._set_app_status_locked(
            app_id,
            status=status,
            message=summary,
            started_at=self._runtime_status.get(app_id, {}).get("started_at"),
            duration=round(duration, 2),
            exit_code=exit_code,
            last_run_at=updated_app["last_run_at"] if updated_app else None,
        )
        self._publish_batch_locked()

    def _set_app_status_locked(
        self,
        app_id: str,
        status: str,
        message: str,
        started_at: str | None,
        duration: float | None,
        exit_code: int | None,
        last_run_at: str | None = None,
    ) -> None:
        """アプリ状態辞書を更新して SSE で配信する（ロック保持前提）。"""
        entry = {
            "app_id": app_id,
            "status": status,
            "message": message,
            "started_at": started_at,
            "duration": duration,
            "exit_code": exit_code,
            "last_run_at": last_run_at,
        }
        self._runtime_status[app_id] = entry
        self._broker.publish("app_status", entry)

    def _publish_batch_locked(self) -> None:
        """バッチ進捗を SSE で配信する（ロック保持前提）。"""
        self._broker.publish("batch_status", dict(self._batch_state))

    def _append_log(
        self,
        app_id: str,
        app_name: str,
        line: str,
        level: str = "output",
    ) -> None:
        """アプリ別ログと全体ログに追記し、SSE でリアルタイム配信する。"""
        if not app_name:
            app = self._storage.get_app(app_id)
            app_name = app["name"] if app else app_id

        entry = {
            "timestamp": _timestamp_short(),
            "app_id": app_id,
            "app_name": app_name,
            "level": level,
            "line": line,
        }
        with self._lock:
            buf = self._app_logs.setdefault(app_id, deque(maxlen=MAX_APP_LOG_LINES))
            buf.append(entry)
            self._global_logs.append(entry)
        self._broker.publish("app_log", entry)
