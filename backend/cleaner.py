"""PCを安全にクリーン保つためのスキャン・クリーニング実行エンジン。

すべて通常権限（UAC不要）で動作し、使用中（ロック中）のファイルや
シンボリックリンク／ジャンクション・保護期間内のファイルを安全にスキップする。
"""

from __future__ import annotations

from collections.abc import Callable
import ctypes
from datetime import datetime
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any

from backend.events import EventBroker
from backend.storage import AppStorage
from config import DEFAULT_CLEANER_TASKS, LOGS_DIR

logger = logging.getLogger("daily_updater.cleaner")

LogCallback = Callable[[str, str, str, str], None]

# ANSI エスケープシーケンス除去
_ANSI_ESCAPE_RE = re.compile(
    r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x07\x1B]*(?:\x07|\x1B\\))"
)

# `uv cache prune` の出力例: "Removed 1234 files (456.7MiB)"
_UV_REMOVED_RE = re.compile(
    r"Removed\s+([\d,]+)\s+files?\s*\(\s*([\d.]+)\s*([KMGT]?i?B)\s*\)",
    re.IGNORECASE,
)

# Windows SHEmptyRecycleBinW フラグ
_SHERB_NOCONFIRMATION = 0x00000001
_SHERB_NOPROGRESSUI = 0x00000002
_SHERB_NOSOUND = 0x00000004


def _is_admin() -> bool:
    """現在のプロセスが Windows 管理者権限で実行されているかを判定する。"""
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _kill_process_tree(pid: int) -> None:
    """子プロセスを含めてプロセスツリー全体を停止する。"""
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


def _decode_and_clean_line(raw: bytes) -> str:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp932", errors="replace")
    return _ANSI_ESCAPE_RE.sub("", text).rstrip("\r\n")


def _parse_uv_prune_output(text: str) -> tuple[int, int]:
    """`uv cache prune` の出力から (解放バイト数, 削除ファイル数) を抽出する。"""
    match = _UV_REMOVED_RE.search(text or "")
    if not match:
        return 0, 0
    file_count = int(match.group(1).replace(",", ""))
    value = float(match.group(2))
    unit = match.group(3).lower()
    multipliers = {
        "b": 1,
        "kb": 1000,
        "kib": 1024,
        "mb": 1000**2,
        "mib": 1024**2,
        "gb": 1000**3,
        "gib": 1024**3,
        "tb": 1000**4,
        "tib": 1024**4,
    }
    freed_bytes = int(value * multipliers.get(unit, 1))
    return max(0, freed_bytes), max(0, file_count)


class _SHQUERYRBINFO(ctypes.Structure):
    """Win32 SHQUERYRBINFO 構造体。"""

    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("i64Size", ctypes.c_longlong),
        ("i64NumItems", ctypes.c_longlong),
    ]


def format_bytes(num_bytes: int) -> str:
    """バイト数を人間が読みやすい単位表記へ変換する。"""
    value = float(max(0, num_bytes))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{int(num_bytes)} B"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _is_reparse_or_symlink(path: Path) -> bool:
    """シンボリックリンクまたは Windows ジャンクションかを安全に判定する。"""
    try:
        if path.is_symlink():
            return True
    except OSError:
        return True
    isjunction = getattr(os.path, "isjunction", None)
    if isjunction is not None:
        try:
            if isjunction(path):
                return True
        except OSError:
            return True
    return False


def _is_safe_cleanup_root(path: Path) -> bool:
    """誤ってシステムルートやホームディレクトリ直下を削除対象にしないための安全ガード。"""
    try:
        resolved = path.resolve()
    except OSError:
        return False

    # ドライブ直下 (C:\ など) は禁止
    if resolved == resolved.parent:
        return False

    # ホームディレクトリ直下は禁止
    try:
        if resolved == Path.home().resolve():
            return False
    except OSError:
        pass

    lower = str(resolved).lower().rstrip("\\/")
    forbidden_exact = {
        "c:\\windows",
        "c:\\program files",
        "c:\\program files (x86)",
        "c:\\programdata",
        "c:\\users",
    }
    if lower in forbidden_exact:
        return False

    return True


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    """重複するパスを正規化して除外する。"""
    unique: list[Path] = []
    seen: set[str] = set()
    for p in paths:
        try:
            key = str(p.resolve()).lower()
        except OSError:
            key = str(p).lower()
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def _resolve_user_temp_dirs() -> list[Path]:
    candidates: list[Path] = []
    for env_key in ("TEMP", "TMP"):
        val = os.environ.get(env_key)
        if val:
            candidates.append(Path(val))
    if not candidates:
        candidates.append(Path(tempfile.gettempdir()))
    return _dedupe_paths(candidates)


def _resolve_crash_dump_dirs() -> list[Path]:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return []
    base = Path(local_app_data)
    return _dedupe_paths(
        [
            base / "CrashDumps",
            base / "Microsoft" / "Windows" / "WER" / "ReportArchive",
            base / "Microsoft" / "Windows" / "WER" / "ReportQueue",
        ]
    )


def _resolve_pip_cache_dirs() -> list[Path]:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return []
    return [Path(local_app_data) / "pip" / "Cache"]


def _resolve_uv_cache_dirs() -> list[Path]:
    env_cache = os.environ.get("UV_CACHE_DIR")
    if env_cache:
        return [Path(env_cache)]
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return []
    return [Path(local_app_data) / "uv" / "cache"]


def _resolve_npm_cache_dirs() -> list[Path]:
    dirs: list[Path] = []
    env_cache = os.environ.get("npm_config_cache")
    if env_cache:
        dirs.append(Path(env_cache))
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        dirs.append(Path(local_app_data) / "npm-cache")
    app_data = os.environ.get("APPDATA")
    if app_data:
        dirs.append(Path(app_data) / "npm-cache")
    return _dedupe_paths(dirs)


def _query_recycle_bin() -> tuple[int, int]:
    """Windows ごみ箱の合計バイト数と項目数を取得する。"""
    if os.name != "nt":
        return 0, 0
    try:
        info = _SHQUERYRBINFO()
        info.cbSize = ctypes.sizeof(_SHQUERYRBINFO)
        hr = ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info))
        if hr == 0:
            return max(0, int(info.i64Size)), max(0, int(info.i64NumItems))
    except Exception as exc:
        logger.debug("ごみ箱の容量取得に失敗しました: %s", exc)
    return 0, 0


def _empty_recycle_bin() -> tuple[bool, str]:
    """Windows ごみ箱を空にする（確認ダイアログ・音なし）。"""
    if os.name != "nt":
        return False, "Windows 環境でのみ利用できます。"
    size_before, items_before = _query_recycle_bin()
    if items_before == 0 and size_before == 0:
        return True, "ごみ箱はすでに空です。"
    try:
        flags = _SHERB_NOCONFIRMATION | _SHERB_NOPROGRESSUI | _SHERB_NOSOUND
        hr = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, flags)
        # 0 (S_OK) 以外はエラー（すでに空の場合の E_UNEXPECTED = -2147418113 は許容）
        if hr in (0, -2147418113, 0x8000FFFF):
            return True, "ごみ箱を空にしました。"
        return False, f"ごみ箱のクリアに失敗しました (HRESULT: {hr:#x})。"
    except Exception as exc:
        return False, f"ごみ箱のクリア中にエラーが発生しました: {exc}"


def _scan_directory_tree(
    roots: list[Path],
    min_age_hours: float = 0.0,
    file_filter: Callable[[Path], bool] | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, int]:
    """指定ディレクトリ配下の削除対象ファイル数と合計サイズを安全に集計する。"""
    now = time.time()
    cutoff = now - (max(0.0, min_age_hours) * 3600.0) if min_age_hours > 0 else None

    total_bytes = 0
    file_count = 0
    skipped_recent = 0

    for root in roots:
        if cancel_event and cancel_event.is_set():
            break
        if not root.exists() or not root.is_dir():
            continue
        if _is_reparse_or_symlink(root) or not _is_safe_cleanup_root(root):
            continue

        stack: list[Path] = [root]
        while stack:
            if cancel_event and cancel_event.is_set():
                break
            current = stack.pop()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        if cancel_event and cancel_event.is_set():
                            break
                        entry_path = Path(entry.path)
                        if _is_reparse_or_symlink(entry_path):
                            continue
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(entry_path)
                            elif entry.is_file(follow_symlinks=False):
                                if file_filter is not None and not file_filter(entry_path):
                                    continue
                                st = entry.stat(follow_symlinks=False)
                                if cutoff is not None and st.st_mtime > cutoff:
                                    skipped_recent += 1
                                    continue
                                total_bytes += st.st_size
                                file_count += 1
                        except OSError:
                            continue
            except OSError:
                continue

    return {
        "reclaimable_bytes": total_bytes,
        "file_count": file_count,
        "skipped_recent": skipped_recent,
    }


def _clean_directory_tree(
    roots: list[Path],
    min_age_hours: float = 0.0,
    file_filter: Callable[[Path], bool] | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, int]:
    """指定ディレクトリ配下の対象ファイルと空サブディレクトリを安全に削除する。"""
    now = time.time()
    cutoff = now - (max(0.0, min_age_hours) * 3600.0) if min_age_hours > 0 else None

    freed_bytes = 0
    deleted_files = 0
    deleted_dirs = 0
    skipped_locked = 0
    skipped_recent = 0

    def _walk_and_clean(directory: Path, is_root: bool) -> bool:
        """後行順（ボトムアップ）で再帰的に削除する。ディレクトリが空なら True を返す。"""
        nonlocal freed_bytes, deleted_files, deleted_dirs, skipped_locked, skipped_recent

        if cancel_event and cancel_event.is_set():
            return False

        has_remaining = False
        try:
            with os.scandir(directory) as it:
                entries = list(it)
        except OSError:
            return False

        for entry in entries:
            if cancel_event and cancel_event.is_set():
                has_remaining = True
                break
            entry_path = Path(entry.path)

            # シンボリックリンク・ジャンクションには絶対に踏み込まない
            if _is_reparse_or_symlink(entry_path):
                has_remaining = True
                continue

            try:
                is_dir = entry.is_dir(follow_symlinks=False)
                is_file = entry.is_file(follow_symlinks=False)
            except OSError:
                has_remaining = True
                continue

            if is_dir:
                # 内部ファイル削除でディレクトリの mtime が現在時刻に更新されるため、再帰前に取得しておく
                orig_dir_mtime: float | None = None
                if cutoff is not None:
                    try:
                        orig_dir_mtime = entry.stat(follow_symlinks=False).st_mtime
                    except OSError:
                        orig_dir_mtime = None

                child_empty = _walk_and_clean(entry_path, is_root=False)
                if child_empty:
                    try:
                        if cutoff is not None and orig_dir_mtime is not None and orig_dir_mtime > cutoff:
                            has_remaining = True
                            continue
                        entry_path.rmdir()
                        deleted_dirs += 1
                    except OSError:
                        has_remaining = True
                else:
                    has_remaining = True
            elif is_file:
                if file_filter is not None and not file_filter(entry_path):
                    has_remaining = True
                    continue
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    skipped_locked += 1
                    has_remaining = True
                    continue

                if cutoff is not None and st.st_mtime > cutoff:
                    skipped_recent += 1
                    has_remaining = True
                    continue

                size = st.st_size
                try:
                    entry_path.unlink()
                    freed_bytes += size
                    deleted_files += 1
                except (PermissionError, OSError):
                    skipped_locked += 1
                    has_remaining = True
            else:
                has_remaining = True

        return not has_remaining and not is_root

    for root in roots:
        if cancel_event and cancel_event.is_set():
            break
        if not root.exists() or not root.is_dir():
            continue
        if _is_reparse_or_symlink(root) or not _is_safe_cleanup_root(root):
            logger.warning("安全ガードにより対象ディレクトリをスキップしました: %s", root)
            continue
        _walk_and_clean(root, is_root=True)

    return {
        "freed_bytes": freed_bytes,
        "deleted_files": deleted_files,
        "deleted_dirs": deleted_dirs,
        "skipped_locked": skipped_locked,
        "skipped_recent": skipped_recent,
    }


def _old_log_filter(path: Path) -> bool:
    """現行の updater.log を保護し、古いログ・ローテートログのみ対象とするフィルタ。"""
    if path.name.lower() == "updater.log":
        return False
    return True


class PCCleaner:
    """PCクリーニングのスキャン・実行・進捗配信を管理するクラス。"""

    def __init__(
        self,
        storage: AppStorage,
        broker: EventBroker,
        log_callback: LogCallback | None = None,
        target_overrides: dict[str, list[Path]] | None = None,
    ) -> None:
        self._storage = storage
        self._broker = broker
        self._log_callback = log_callback
        self._target_overrides = target_overrides or {}
        self._lock = threading.RLock()

        self._cancel_event = threading.Event()
        self._active_proc: subprocess.Popen[bytes] | None = None

        # ランタイム状態（スキャン結果・実行中ステータス）
        self._task_runtime: dict[str, dict[str, Any]] = {}
        self._state: dict[str, Any] = {
            "active": False,
            "mode": None,  # "scan" | "clean" | None
            "total": 0,
            "completed": 0,
            "succeeded": 0,
            "failed": 0,
            "skipped": 0,
            "total_reclaimable_bytes": 0,
            "last_freed_bytes": 0,
            "last_scan_at": None,
            "last_clean_at": None,
        }

    def _emit_log(self, task_id: str, task_name: str, line: str, level: str = "output") -> None:
        if self._log_callback is not None:
            self._log_callback(f"cleaner:{task_id}", f"掃除:{task_name}", line, level)

    def _is_task_available(self, task_id: str) -> tuple[bool, str]:
        """タスクが現在の環境で実行可能かを確認する。"""
        if task_id == "dns-flush":
            if os.name != "nt" or not shutil.which("ipconfig"):
                return False, "ipconfig が見つかりません"
            if not _is_admin():
                return False, "管理者権限が必要"
            return True, ""
        if task_id == "uv-cache-prune":
            if shutil.which("uv"):
                return True, ""
            return False, "uv が未導入です"
        if task_id == "npm-cache":
            if shutil.which("npm"):
                return True, ""
            return False, "npm が未導入です"
        if task_id == "recycle-bin":
            if os.name == "nt":
                return True, ""
            return False, "Windows 環境専用です"
        return True, ""

    def _resolve_task_paths(self, task_id: str) -> list[Path]:
        """タスクIDに対応する対象ディレクトリ一覧を解決する。"""
        if task_id in self._target_overrides:
            return list(self._target_overrides[task_id])
        if task_id == "user-temp":
            return _resolve_user_temp_dirs()
        if task_id == "crash-dumps":
            return _resolve_crash_dump_dirs()
        if task_id == "app-old-logs":
            return [LOGS_DIR]
        if task_id == "pip-cache":
            return _resolve_pip_cache_dirs()
        if task_id == "uv-cache-prune":
            return _resolve_uv_cache_dirs()
        if task_id == "npm-cache":
            return _resolve_npm_cache_dirs()
        return []

    def get_snapshot(self) -> dict[str, Any]:
        """フロントエンド描画用の最新クリーニング状態スナップショットを返す。"""
        persisted = self._storage.get_cleaner_states()
        settings = self._storage.get_settings()
        temp_age = int(settings.get("cleaner_temp_min_age_hours", 24))

        with self._lock:
            tasks: list[dict[str, Any]] = []
            total_reclaimable = 0

            for definition in DEFAULT_CLEANER_TASKS:
                tid = definition["id"]
                saved = persisted.get(tid, {})
                rt = self._task_runtime.get(tid, {})
                available, unavailable_reason = self._is_task_available(tid)

                reclaimable = rt.get("scan_bytes")
                if isinstance(reclaimable, int) and available:
                    total_reclaimable += reclaimable

                entry = {
                    **definition,
                    "enabled": bool(saved.get("enabled", definition["default_enabled"])),
                    "available": available,
                    "unavailable_reason": unavailable_reason,
                    "status": rt.get("status") or saved.get("last_status") or "idle",
                    "runtime_message": rt.get("message", ""),
                    "scan_bytes": reclaimable,
                    "scan_count": rt.get("scan_count"),
                    "scan_skipped_recent": rt.get("scan_skipped_recent", 0),
                    "scanned_at": rt.get("scanned_at"),
                    "last_run_at": saved.get("last_run_at"),
                    "last_status": saved.get("last_status"),
                    "last_duration": saved.get("last_duration"),
                    "last_freed_bytes": saved.get("last_freed_bytes", 0),
                    "last_deleted_count": saved.get("last_deleted_count", 0),
                    "last_skipped_count": saved.get("last_skipped_count", 0),
                    "last_summary": saved.get("last_summary", ""),
                }
                if tid == "user-temp":
                    entry["min_age_hours"] = temp_age
                tasks.append(entry)

            state_copy = dict(self._state)
            state_copy["total_reclaimable_bytes"] = total_reclaimable
            return {
                "tasks": tasks,
                "state": state_copy,
            }

    def _publish_snapshot(self) -> None:
        self._broker.publish("cleaner_snapshot", self.get_snapshot())

    def set_task_enabled(self, task_id: str, enabled: bool) -> dict[str, Any]:
        """クリーニング項目の有効・無効を切り替えて SSE 通知する。"""
        updated = self._storage.update_cleaner_task(task_id, enabled)
        self._publish_snapshot()
        return updated

    def cancel(self) -> bool:
        """実行中のスキャンまたはクリーニングを中止する。"""
        with self._lock:
            if not self._state["active"]:
                return False
            self._cancel_event.set()
            proc = self._active_proc
        if proc is not None and proc.poll() is None:
            _kill_process_tree(proc.pid)
        return True

    def start_scan(self, task_ids: list[str] | None = None) -> dict[str, Any]:
        """削除可能容量の事前スキャンをバックグラウンドで開始する。"""
        with self._lock:
            if self._state["active"]:
                return {"started": False, "message": "すでにクリーニングまたはスキャンを実行中です。"}

            valid_defs = [
                d for d in DEFAULT_CLEANER_TASKS if task_ids is None or d["id"] in task_ids
            ]
            if not valid_defs:
                return {"started": False, "message": "スキャン対象の項目がありません。"}

            self._cancel_event.clear()
            self._state.update(
                {
                    "active": True,
                    "mode": "scan",
                    "total": len(valid_defs),
                    "completed": 0,
                    "succeeded": 0,
                    "failed": 0,
                    "skipped": 0,
                }
            )
            for d in valid_defs:
                rt = self._task_runtime.setdefault(d["id"], {})
                rt["status"] = "scanning"
                rt["message"] = "スキャン待機中..."

        self._publish_snapshot()
        threading.Thread(
            target=self._run_scan_worker,
            args=(valid_defs,),
            daemon=True,
            name="pc-cleaner-scan",
        ).start()
        return {"started": True, "count": len(valid_defs)}

    def start_clean(self, mode: str = "enabled", task_ids: list[str] | None = None) -> dict[str, Any]:
        """クリーニングをバックグラウンドで開始する。"""
        persisted = self._storage.get_cleaner_states()
        with self._lock:
            if self._state["active"]:
                return {"started": False, "message": "すでにクリーニングまたはスキャンを実行中です。"}

            if mode == "selected" and task_ids:
                target_set = set(task_ids)
                selected_defs = [d for d in DEFAULT_CLEANER_TASKS if d["id"] in target_set]
            else:
                selected_defs = [
                    d
                    for d in DEFAULT_CLEANER_TASKS
                    if persisted.get(d["id"], {}).get("enabled", d["default_enabled"])
                    and self._is_task_available(d["id"])[0]
                ]

            if not selected_defs:
                return {"started": False, "message": "実行対象のクリーニング項目がありません。"}

            self._cancel_event.clear()
            self._state.update(
                {
                    "active": True,
                    "mode": "clean",
                    "total": len(selected_defs),
                    "completed": 0,
                    "succeeded": 0,
                    "failed": 0,
                    "skipped": 0,
                    "last_freed_bytes": 0,
                }
            )
            for d in selected_defs:
                rt = self._task_runtime.setdefault(d["id"], {})
                rt["status"] = "queued"
                rt["message"] = "実行待機中..."

        self._publish_snapshot()
        threading.Thread(
            target=self._run_clean_worker,
            args=(selected_defs,),
            daemon=True,
            name="pc-cleaner-run",
        ).start()
        return {
            "started": True,
            "task_ids": [d["id"] for d in selected_defs],
        }

    def _run_scan_worker(self, tasks: list[dict[str, Any]]) -> None:
        """全項目の削除可能サイズを安全に試算するワーカースレッド。"""
        settings = self._storage.get_settings()
        temp_age = float(settings.get("cleaner_temp_min_age_hours", 24))
        total_bytes = 0
        total_files = 0

        for task in tasks:
            tid = task["id"]
            if self._cancel_event.is_set():
                with self._lock:
                    self._task_runtime.setdefault(tid, {})["status"] = "idle"
                    self._task_runtime[tid]["message"] = "スキャン中止"
                continue

            available, reason = self._is_task_available(tid)
            if not available:
                with self._lock:
                    self._task_runtime[tid] = {
                        **self._task_runtime.get(tid, {}),
                        "status": "unavailable",
                        "message": reason,
                        "scan_bytes": 0,
                        "scan_count": 0,
                        "scanned_at": _now_iso(),
                    }
                    self._state["completed"] += 1
                    self._state["skipped"] += 1
                self._publish_snapshot()
                continue

            with self._lock:
                self._task_runtime.setdefault(tid, {})["status"] = "scanning"
                self._task_runtime[tid]["message"] = "容量を計測中..."
            self._publish_snapshot()

            scan_bytes: int | None = 0
            scan_count: int | None = 0
            skipped_recent = 0

            try:
                if tid == "recycle-bin":
                    scan_bytes, scan_count = _query_recycle_bin()
                elif tid == "dns-flush":
                    scan_bytes, scan_count = 0, 0
                elif tid == "uv-cache-prune":
                    # uv キャッシュは数十万ファイル規模になりやすく未使用分のみ整理されるため、
                    # Python 側の全件走査を行わず実行時に uv 本体の結果を集計する。
                    scan_bytes, scan_count = None, None
                else:
                    paths = self._resolve_task_paths(tid)
                    min_age = 0.0
                    file_filter: Callable[[Path], bool] | None = None
                    if tid == "user-temp":
                        min_age = temp_age
                    elif tid == "app-old-logs":
                        min_age = 24.0 * 7.0
                        file_filter = _old_log_filter
                    res = _scan_directory_tree(
                        paths,
                        min_age_hours=min_age,
                        file_filter=file_filter,
                        cancel_event=self._cancel_event,
                    )
                    scan_bytes = res["reclaimable_bytes"]
                    scan_count = res["file_count"]
                    skipped_recent = res["skipped_recent"]

                if isinstance(scan_bytes, int):
                    total_bytes += scan_bytes
                if isinstance(scan_count, int):
                    total_files += scan_count

                with self._lock:
                    self._task_runtime[tid] = {
                        **self._task_runtime.get(tid, {}),
                        "status": "idle",
                        "message": "",
                        "scan_bytes": scan_bytes,
                        "scan_count": scan_count,
                        "scan_skipped_recent": skipped_recent,
                        "scanned_at": _now_iso(),
                    }
                    self._state["completed"] += 1
                    self._state["succeeded"] += 1
            except Exception as exc:
                logger.exception("スキャン中にエラーが発生しました (%s): %s", tid, exc)
                with self._lock:
                    self._task_runtime[tid] = {
                        **self._task_runtime.get(tid, {}),
                        "status": "failed",
                        "message": f"スキャン失敗: {exc}",
                    }
                    self._state["completed"] += 1
                    self._state["failed"] += 1

            self._publish_snapshot()

        with self._lock:
            self._state["active"] = False
            self._state["mode"] = None
            self._state["last_scan_at"] = _now_iso()
        self._publish_snapshot()

        if not self._cancel_event.is_set():
            self._emit_log(
                "summary",
                "スキャン",
                f"容量スキャン完了: 削除候補 {format_bytes(total_bytes)} ({total_files}件)",
                "info",
            )

    def _run_command_task(
        self,
        cmd: list[str],
        task_id: str,
        task_name: str,
        timeout: int = 180,
    ) -> tuple[int, str]:
        """外部クリーニングコマンドをサブプロセス実行し、出力をリアルタイム配信する。"""
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=env,
            bufsize=0,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        with self._lock:
            self._active_proc = proc

        timed_out = threading.Event()

        def _on_timeout() -> None:
            timed_out.set()
            if proc.poll() is None:
                self._emit_log(
                    task_id,
                    task_name,
                    f"タイムアウト ({timeout}秒) に達したためプロセスを停止します。",
                    "error",
                )
                _kill_process_tree(proc.pid)

        timer = threading.Timer(timeout, _on_timeout)
        timer.daemon = True
        timer.start()

        lines: list[str] = []
        try:
            if proc.stdout is not None:
                buf = bytearray()
                while True:
                    chunk = proc.stdout.read(256)
                    if not chunk:
                        break
                    buf.extend(chunk)
                    while True:
                        nl_idx = buf.find(b"\n")
                        cr_idx = buf.find(b"\r")
                        if nl_idx == -1 and cr_idx == -1:
                            break
                        split_idx = nl_idx if (nl_idx != -1 and (cr_idx == -1 or nl_idx < cr_idx)) else cr_idx
                        raw_line = bytes(buf[:split_idx])
                        advance = split_idx + 1
                        if split_idx == cr_idx and advance < len(buf) and buf[advance] == 10:
                            advance += 1
                        del buf[:advance]

                        cleaned = _decode_and_clean_line(raw_line).strip()
                        if cleaned:
                            lines.append(cleaned)
                            self._emit_log(task_id, task_name, cleaned, "output")

                if buf:
                    cleaned = _decode_and_clean_line(bytes(buf)).strip()
                    if cleaned:
                        lines.append(cleaned)
                        self._emit_log(task_id, task_name, cleaned, "output")

            rc = proc.wait()
            if timed_out.is_set():
                return 124, "\n".join(lines) or f"タイムアウト ({timeout}秒) に達しました。"
            return rc, "\n".join(lines)
        finally:
            timer.cancel()
            if proc.stdout is not None:
                try:
                    proc.stdout.close()
                except OSError:
                    pass
            with self._lock:
                if self._active_proc is proc:
                    self._active_proc = None

    def _run_clean_worker(self, tasks: list[dict[str, Any]]) -> None:
        """選択されたクリーニング項目を順次安全に実行するワーカースレッド。"""
        settings = self._storage.get_settings()
        temp_age = float(settings.get("cleaner_temp_min_age_hours", 24))
        total_freed = 0
        total_deleted = 0

        self._emit_log("summary", "PCクリーニング", f"{len(tasks)}項目のクリーニングを開始します...", "info")

        for task in tasks:
            tid = task["id"]
            name = task["name"]

            if self._cancel_event.is_set():
                with self._lock:
                    self._task_runtime.setdefault(tid, {})["status"] = "cancelled"
                    self._task_runtime[tid]["message"] = "中止されました"
                self._publish_snapshot()
                continue

            available, reason = self._is_task_available(tid)
            if not available:
                with self._lock:
                    self._task_runtime[tid] = {
                        **self._task_runtime.get(tid, {}),
                        "status": "unavailable",
                        "message": reason,
                    }
                    self._state["completed"] += 1
                    self._state["skipped"] += 1
                self._emit_log(tid, name, f"スキップ: {reason}", "warn")
                self._publish_snapshot()
                continue

            with self._lock:
                self._task_runtime.setdefault(tid, {})["status"] = "running"
                self._task_runtime[tid]["message"] = "クリーニング中..."
            self._publish_snapshot()

            start_mono = time.monotonic()
            freed_bytes = 0
            deleted_count = 0
            skipped_count = 0
            status = "success"
            summary = ""

            try:
                if tid == "user-temp":
                    paths = self._resolve_task_paths(tid)
                    self._emit_log(
                        tid,
                        name,
                        f"対象を掃除中 (保護期間: {int(temp_age)}時間以内は保持)...",
                        "info",
                    )
                    res = _clean_directory_tree(
                        paths,
                        min_age_hours=temp_age,
                        cancel_event=self._cancel_event,
                    )
                    freed_bytes = res["freed_bytes"]
                    deleted_count = res["deleted_files"]
                    skipped_count = res["skipped_locked"] + res["skipped_recent"]
                    parts = [f"{format_bytes(freed_bytes)} 解放 ({deleted_count}件削除)"]
                    if res["skipped_locked"]:
                        parts.append(f"使用中 {res['skipped_locked']}件スキップ")
                    if res["skipped_recent"]:
                        parts.append(f"保護期間内 {res['skipped_recent']}件保持")
                    summary = " / ".join(parts)

                elif tid in ("crash-dumps", "pip-cache", "app-old-logs"):
                    paths = self._resolve_task_paths(tid)
                    min_age = 24.0 * 7.0 if tid == "app-old-logs" else 0.0
                    file_filter = _old_log_filter if tid == "app-old-logs" else None
                    res = _clean_directory_tree(
                        paths,
                        min_age_hours=min_age,
                        file_filter=file_filter,
                        cancel_event=self._cancel_event,
                    )
                    freed_bytes = res["freed_bytes"]
                    deleted_count = res["deleted_files"]
                    skipped_count = res["skipped_locked"] + res["skipped_recent"]
                    if deleted_count == 0 and freed_bytes == 0:
                        summary = "削除対象のファイルはありませんでした"
                    else:
                        summary = f"{format_bytes(freed_bytes)} 解放 ({deleted_count}件削除)"
                        if res["skipped_locked"]:
                            summary += f" / 使用中 {res['skipped_locked']}件スキップ"

                elif tid == "dns-flush":
                    self._emit_log(tid, name, "$ ipconfig /flushdns", "info")
                    rc, _ = self._run_command_task(["ipconfig", "/flushdns"], tid, name, timeout=30)
                    if rc == 0:
                        summary = "DNS リゾルバーキャッシュをフラッシュしました"
                    else:
                        status = "failed"
                        summary = f"DNS フラッシュ失敗 (終了コード: {rc})"

                elif tid == "uv-cache-prune":
                    uv_timeout = max(900, int(settings.get("timeout_seconds", 300)))
                    self._emit_log(tid, name, "$ uv cache prune --no-progress", "info")
                    rc, out = self._run_command_task(
                        ["uv", "cache", "prune", "--no-progress"],
                        tid,
                        name,
                        timeout=uv_timeout,
                    )
                    if rc == 0:
                        freed_bytes, deleted_count = _parse_uv_prune_output(out)
                        if freed_bytes > 0 or deleted_count > 0:
                            summary = f"{format_bytes(freed_bytes)} 解放 ({deleted_count}件削除)"
                        else:
                            summary = "未使用キャッシュはありませんでした"
                    else:
                        status = "failed"
                        summary = (
                            f"タイムアウト ({uv_timeout}秒)"
                            if rc == 124
                            else f"uv cache prune 失敗 (終了コード: {rc})"
                        )

                elif tid == "npm-cache":
                    paths = self._resolve_task_paths(tid)
                    before = _scan_directory_tree(paths, cancel_event=self._cancel_event)
                    npm_exe = shutil.which("npm") or "npm"
                    self._emit_log(tid, name, "$ npm cache clean --force", "info")
                    rc, _ = self._run_command_task(
                        [npm_exe, "cache", "clean", "--force"],
                        tid,
                        name,
                        timeout=300,
                    )
                    if rc == 0:
                        after = _scan_directory_tree(paths, cancel_event=self._cancel_event)
                        freed_bytes = max(0, before["reclaimable_bytes"] - after["reclaimable_bytes"])
                        deleted_count = max(0, before["file_count"] - after["file_count"])
                        summary = (
                            f"{format_bytes(freed_bytes)} 解放"
                            if freed_bytes > 0
                            else "npm キャッシュはクリーンな状態です"
                        )
                    else:
                        status = "failed"
                        summary = f"npm cache clean 失敗 (終了コード: {rc})"

                elif tid == "recycle-bin":
                    size_before, items_before = _query_recycle_bin()
                    ok, msg = _empty_recycle_bin()
                    if ok:
                        size_after, items_after = _query_recycle_bin()
                        freed_bytes = max(0, size_before - size_after)
                        deleted_count = max(0, items_before - items_after)
                        summary = (
                            f"{format_bytes(freed_bytes)} 解放 ({deleted_count}件削除)"
                            if items_before > 0
                            else msg
                        )
                    else:
                        status = "failed"
                        summary = msg

            except Exception as exc:
                logger.exception("クリーニング中にエラーが発生しました (%s): %s", tid, exc)
                status = "failed"
                summary = f"エラー: {exc}"

            if self._cancel_event.is_set():
                status = "cancelled"
                summary = "ユーザーにより中止されました"

            duration = time.monotonic() - start_mono

            if status == "success":
                total_freed += freed_bytes
                total_deleted += deleted_count
                self._emit_log(tid, name, f"完了: {summary} ({duration:.1f}秒)", "success")
            elif status == "cancelled":
                self._emit_log(tid, name, f"中止 ({duration:.1f}秒)", "warn")
            else:
                self._emit_log(tid, name, f"失敗: {summary} ({duration:.1f}秒)", "error")

            self._storage.record_cleaner_result(
                task_id=tid,
                status=status,
                duration=duration,
                freed_bytes=freed_bytes,
                deleted_count=deleted_count,
                skipped_count=skipped_count,
                summary=summary,
            )

            # クリーニング後の残量を最新化（大規模な uv キャッシュは走査しない）
            remaining_bytes: int | None = None
            remaining_count: int | None = None
            if status == "success" and tid not in ("dns-flush", "recycle-bin", "uv-cache-prune"):
                paths = self._resolve_task_paths(tid)
                min_age = temp_age if tid == "user-temp" else (24.0 * 7.0 if tid == "app-old-logs" else 0.0)
                file_filter = _old_log_filter if tid == "app-old-logs" else None
                after_scan = _scan_directory_tree(paths, min_age_hours=min_age, file_filter=file_filter)
                remaining_bytes = after_scan["reclaimable_bytes"]
                remaining_count = after_scan["file_count"]
            elif status == "success" and tid == "recycle-bin":
                remaining_bytes, remaining_count = _query_recycle_bin()
            elif status == "success" and tid == "dns-flush":
                remaining_bytes, remaining_count = 0, 0

            with self._lock:
                self._task_runtime[tid] = {
                    **self._task_runtime.get(tid, {}),
                    "status": status,
                    "message": summary,
                    "scan_bytes": remaining_bytes if status == "success" else self._task_runtime.get(tid, {}).get("scan_bytes"),
                    "scan_count": remaining_count if status == "success" else self._task_runtime.get(tid, {}).get("scan_count"),
                    "scanned_at": _now_iso() if status == "success" else self._task_runtime.get(tid, {}).get("scanned_at"),
                }
                self._state["completed"] += 1
                self._state["last_freed_bytes"] = total_freed
                if status == "success":
                    self._state["succeeded"] += 1
                elif status == "failed":
                    self._state["failed"] += 1
                else:
                    self._state["skipped"] += 1

            self._publish_snapshot()

        with self._lock:
            self._state["active"] = False
            self._state["mode"] = None
            self._state["last_clean_at"] = _now_iso()
            self._state["last_freed_bytes"] = total_freed
        self._publish_snapshot()

        self._emit_log(
            "summary",
            "PCクリーニング",
            f"クリーニング完了: 合計 {format_bytes(total_freed)} を解放 ({total_deleted}件削除)",
            "success",
        )
