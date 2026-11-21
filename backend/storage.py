"""アプリ一覧および設定のJSON永続化モジュール（スレッドセーフ）。"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import threading
from typing import Any
import uuid

from config import (
    ALLOWED_SHELLS,
    APPS_FILE,
    CLEANER_FILE,
    DATA_DIR,
    DEFAULT_APPS,
    DEFAULT_CLEANER_TASKS,
    DEFAULT_SETTINGS,
    SETTINGS_FILE,
)

logger = logging.getLogger("daily_updater.storage")


def _now_iso() -> str:
    """現在時刻をローカルタイムゾーンの ISO 文字列で返す。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _atomic_write_json(path: Path, data: Any) -> None:
    """一時ファイル経由で JSON をアトミックに書き込み、データ破損を防ぐ。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def _normalize_app(raw: dict[str, Any], order_index: int) -> dict[str, Any]:
    """アプリ定義の欠損フィールドを補完し、正規化した辞書を返す。"""
    shell = str(raw.get("shell", "auto")).strip().lower()
    if shell not in ALLOWED_SHELLS:
        shell = "auto"

    return {
        "id": str(raw.get("id") or uuid.uuid4().hex[:12]),
        "name": str(raw.get("name", "")).strip(),
        "command": str(raw.get("command", "")).strip(),
        "category": str(raw.get("category", "その他")).strip() or "その他",
        "description": str(raw.get("description", "")).strip(),
        "shell": shell,
        "enabled": bool(raw.get("enabled", True)),
        "order": int(raw.get("order", order_index)),
        "created_at": str(raw.get("created_at") or _now_iso()),
        "last_run_at": raw.get("last_run_at"),
        "last_status": raw.get("last_status"),
        "last_exit_code": raw.get("last_exit_code"),
        "last_duration": raw.get("last_duration"),
        "last_summary": raw.get("last_summary", ""),
    }


def _default_cleaner_states() -> dict[str, dict[str, Any]]:
    """標準クリーニングタスクの初期永続状態を生成する。"""
    return {
        item["id"]: {
            "enabled": bool(item.get("default_enabled", False)),
            "last_run_at": None,
            "last_status": None,
            "last_duration": None,
            "last_freed_bytes": 0,
            "last_deleted_count": 0,
            "last_skipped_count": 0,
            "last_summary": "",
        }
        for item in DEFAULT_CLEANER_TASKS
    }


def _normalize_cleaner_states(raw: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """読み込み済みのクリーニング状態を標準タスク一覧に合わせて正規化する。"""
    defaults = _default_cleaner_states()
    if not isinstance(raw, dict):
        return defaults

    for task_id, base in defaults.items():
        saved = raw.get(task_id)
        if not isinstance(saved, dict):
            continue
        if "enabled" in saved:
            base["enabled"] = bool(saved["enabled"])
        base["last_run_at"] = saved.get("last_run_at")
        base["last_status"] = saved.get("last_status")
        base["last_duration"] = saved.get("last_duration")
        try:
            base["last_freed_bytes"] = max(0, int(saved.get("last_freed_bytes") or 0))
        except (TypeError, ValueError):
            base["last_freed_bytes"] = 0
        try:
            base["last_deleted_count"] = max(0, int(saved.get("last_deleted_count") or 0))
        except (TypeError, ValueError):
            base["last_deleted_count"] = 0
        try:
            base["last_skipped_count"] = max(0, int(saved.get("last_skipped_count") or 0))
        except (TypeError, ValueError):
            base["last_skipped_count"] = 0
        base["last_summary"] = str(saved.get("last_summary") or "")
    return defaults


class AppStorage:
    """登録アプリ情報・設定・クリーニング状態を JSON ファイルで永続管理するクラス。"""

    def __init__(
        self,
        apps_file: Path = APPS_FILE,
        settings_file: Path = SETTINGS_FILE,
        cleaner_file: Path | None = None,
    ) -> None:
        self._apps_file = apps_file
        self._settings_file = settings_file
        if cleaner_file is not None:
            self._cleaner_file = cleaner_file
        elif apps_file == APPS_FILE:
            self._cleaner_file = CLEANER_FILE
        else:
            self._cleaner_file = apps_file.parent / "cleaner.json"
        self._lock = threading.RLock()
        self._apps: list[dict[str, Any]] = []
        self._settings: dict[str, Any] = deepcopy(DEFAULT_SETTINGS)
        self._cleaner_states: dict[str, dict[str, Any]] = _default_cleaner_states()

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._load_all()

    def _load_all(self) -> None:
        """起動時に JSON ファイルからデータをロードする。未作成なら初期データを生成する。"""
        with self._lock:
            if self._apps_file.exists():
                try:
                    raw_apps = json.loads(self._apps_file.read_text(encoding="utf-8"))
                    if isinstance(raw_apps, list):
                        self._apps = [
                            _normalize_app(item, idx)
                            for idx, item in enumerate(raw_apps)
                            if isinstance(item, dict)
                        ]
                    else:
                        raise ValueError("apps.json のルート要素がリストではありません。")
                except Exception as exc:
                    logger.error("apps.json の読み込みに失敗したため初期データを使用します: %s", exc)
                    self._apps = [_normalize_app(item, idx) for idx, item in enumerate(DEFAULT_APPS)]
            else:
                self._apps = [_normalize_app(item, idx) for idx, item in enumerate(DEFAULT_APPS)]
                self._save_apps_locked()
                logger.info("初期アプリリストを生成しました (%d件)", len(self._apps))

            self._sort_apps_locked()

            if self._settings_file.exists():
                try:
                    raw_settings = json.loads(self._settings_file.read_text(encoding="utf-8"))
                    if isinstance(raw_settings, dict):
                        self._settings = self._validate_settings(raw_settings)
                except Exception as exc:
                    logger.error("settings.json の読み込みに失敗したためデフォルト設定を使用します: %s", exc)
                    self._settings = deepcopy(DEFAULT_SETTINGS)
            else:
                self._save_settings_locked()

            if self._cleaner_file.exists():
                try:
                    raw_cleaner = json.loads(self._cleaner_file.read_text(encoding="utf-8"))
                    self._cleaner_states = _normalize_cleaner_states(raw_cleaner)
                except Exception as exc:
                    logger.error("cleaner.json の読み込みに失敗したため初期状態を使用します: %s", exc)
                    self._cleaner_states = _default_cleaner_states()
            else:
                self._save_cleaner_locked()

    def _sort_apps_locked(self) -> None:
        """order 順に並び替え、連番を振り直す。"""
        self._apps.sort(key=lambda item: (item.get("order", 0), item.get("created_at", "")))
        for idx, item in enumerate(self._apps):
            item["order"] = idx

    def _save_apps_locked(self) -> None:
        _atomic_write_json(self._apps_file, self._apps)

    def _save_settings_locked(self) -> None:
        _atomic_write_json(self._settings_file, self._settings)

    def _save_cleaner_locked(self) -> None:
        _atomic_write_json(self._cleaner_file, self._cleaner_states)

    def _validate_settings(self, raw: dict[str, Any]) -> dict[str, Any]:
        """設定値の妥当性を検証して安全な範囲に収める。"""
        merged = deepcopy(self._settings)
        if "max_workers" in raw:
            try:
                workers = int(raw["max_workers"])
                merged["max_workers"] = max(1, min(12, workers))
            except (TypeError, ValueError):
                pass
        if "default_shell" in raw:
            shell = str(raw["default_shell"]).strip().lower()
            if shell in ALLOWED_SHELLS:
                merged["default_shell"] = shell
        if "timeout_seconds" in raw:
            try:
                timeout = int(raw["timeout_seconds"])
                merged["timeout_seconds"] = max(30, min(3600, timeout))
            except (TypeError, ValueError):
                pass
        if "cleaner_temp_min_age_hours" in raw:
            try:
                hours = int(raw["cleaner_temp_min_age_hours"])
                merged["cleaner_temp_min_age_hours"] = max(0, min(720, hours))
            except (TypeError, ValueError):
                pass
        if "clean_after_update_all" in raw:
            merged["clean_after_update_all"] = bool(raw["clean_after_update_all"])
        return merged

    def list_apps(self) -> list[dict[str, Any]]:
        """登録済みアプリの一覧を返す。"""
        with self._lock:
            return deepcopy(self._apps)

    def get_app(self, app_id: str) -> dict[str, Any] | None:
        """ID 指定でアプリを1件取得する。"""
        with self._lock:
            for item in self._apps:
                if item["id"] == app_id:
                    return deepcopy(item)
            return None

    def add_app(self, payload: dict[str, Any]) -> dict[str, Any]:
        """新規アプリを登録する。"""
        name = str(payload.get("name", "")).strip()
        command = str(payload.get("command", "")).strip()
        if not name:
            raise ValueError("アプリ名を入力してください。")
        if not command:
            raise ValueError("アップデートコマンドを入力してください。")

        with self._lock:
            new_app = _normalize_app(
                {
                    "id": uuid.uuid4().hex[:12],
                    "name": name,
                    "command": command,
                    "category": payload.get("category", "その他"),
                    "description": payload.get("description", ""),
                    "shell": payload.get("shell", "auto"),
                    "enabled": payload.get("enabled", True),
                    "order": len(self._apps),
                    "created_at": _now_iso(),
                },
                len(self._apps),
            )
            self._apps.append(new_app)
            self._sort_apps_locked()
            self._save_apps_locked()
            logger.info("アプリを新規登録しました: %s (%s)", new_app["name"], new_app["command"])
            return deepcopy(new_app)

    def update_app(self, app_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """既存アプリの情報を部分または全体更新する。"""
        with self._lock:
            target = next((item for item in self._apps if item["id"] == app_id), None)
            if target is None:
                raise KeyError(f"指定されたアプリが見つかりません: {app_id}")

            if "name" in payload:
                name = str(payload["name"]).strip()
                if not name:
                    raise ValueError("アプリ名は空にできません。")
                target["name"] = name
            if "command" in payload:
                command = str(payload["command"]).strip()
                if not command:
                    raise ValueError("アップデートコマンドは空にできません。")
                target["command"] = command
            if "category" in payload:
                target["category"] = str(payload["category"]).strip() or "その他"
            if "description" in payload:
                target["description"] = str(payload["description"]).strip()
            if "shell" in payload:
                shell = str(payload["shell"]).strip().lower()
                if shell not in ALLOWED_SHELLS:
                    raise ValueError(f"未対応のシェル指定です: {shell}")
                target["shell"] = shell
            if "enabled" in payload:
                target["enabled"] = bool(payload["enabled"])

            self._save_apps_locked()
            logger.info("アプリ設定を更新しました: %s", target["name"])
            return deepcopy(target)

    def delete_app(self, app_id: str) -> dict[str, Any]:
        """指定アプリを登録解除（削除）する。"""
        with self._lock:
            for idx, item in enumerate(self._apps):
                if item["id"] == app_id:
                    removed = self._apps.pop(idx)
                    self._sort_apps_locked()
                    self._save_apps_locked()
                    logger.info("アプリを登録解除しました: %s", removed["name"])
                    return deepcopy(removed)
            raise KeyError(f"指定されたアプリが見つかりません: {app_id}")

    def reorder_apps(self, ordered_ids: list[str]) -> list[dict[str, Any]]:
        """指定された ID 順序でアプリリストを並び替える。"""
        with self._lock:
            id_to_index = {app_id: idx for idx, app_id in enumerate(ordered_ids)}
            fallback_base = len(ordered_ids)
            for idx, item in enumerate(self._apps):
                item["order"] = id_to_index.get(item["id"], fallback_base + idx)
            self._sort_apps_locked()
            self._save_apps_locked()
            return deepcopy(self._apps)

    def record_run_result(
        self,
        app_id: str,
        status: str,
        exit_code: int | None,
        duration: float,
        summary: str,
    ) -> dict[str, Any] | None:
        """アプリの最新実行結果を永続化する。"""
        with self._lock:
            target = next((item for item in self._apps if item["id"] == app_id), None)
            if target is None:
                return None
            target["last_run_at"] = _now_iso()
            target["last_status"] = status
            target["last_exit_code"] = exit_code
            target["last_duration"] = round(duration, 2)
            target["last_summary"] = summary[:240]
            self._save_apps_locked()
            return deepcopy(target)

    def get_settings(self) -> dict[str, Any]:
        """全体設定を取得する。"""
        with self._lock:
            return deepcopy(self._settings)

    def update_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        """全体設定を更新する。"""
        with self._lock:
            self._settings = self._validate_settings(payload)
            self._save_settings_locked()
            logger.info("全体設定を更新しました: %s", self._settings)
            return deepcopy(self._settings)

    def get_cleaner_states(self) -> dict[str, dict[str, Any]]:
        """クリーニングタスクごとの永続状態を返す。"""
        with self._lock:
            return deepcopy(self._cleaner_states)

    def update_cleaner_task(self, task_id: str, enabled: bool) -> dict[str, Any]:
        """指定クリーニングタスクの有効・無効を切り替えて永続化する。"""
        with self._lock:
            if task_id not in self._cleaner_states:
                raise KeyError(f"指定されたクリーニング項目が見つかりません: {task_id}")
            self._cleaner_states[task_id]["enabled"] = bool(enabled)
            self._save_cleaner_locked()
            logger.info("クリーニング項目の有効状態を変更しました: %s -> %s", task_id, enabled)
            return deepcopy(self._cleaner_states[task_id])

    def record_cleaner_result(
        self,
        task_id: str,
        status: str,
        duration: float,
        freed_bytes: int,
        deleted_count: int,
        skipped_count: int,
        summary: str,
    ) -> dict[str, Any] | None:
        """クリーニングタスクの最新実行結果を永続化する。"""
        with self._lock:
            target = self._cleaner_states.get(task_id)
            if target is None:
                return None
            target["last_run_at"] = _now_iso()
            target["last_status"] = status
            target["last_duration"] = round(duration, 2)
            target["last_freed_bytes"] = max(0, int(freed_bytes))
            target["last_deleted_count"] = max(0, int(deleted_count))
            target["last_skipped_count"] = max(0, int(skipped_count))
            target["last_summary"] = summary[:240]
            self._save_cleaner_locked()
            return deepcopy(target)

    def export_bundle(self) -> dict[str, Any]:
        """バックアップ用にアプリ一覧・設定・クリーニング設定をまとめてエクスポートする。"""
        with self._lock:
            return {
                "exported_at": _now_iso(),
                "settings": deepcopy(self._settings),
                "apps": deepcopy(self._apps),
                "cleaner_states": deepcopy(self._cleaner_states),
            }

    def import_apps(self, apps_data: list[dict[str, Any]], replace: bool = False) -> list[dict[str, Any]]:
        """JSON からアプリリストをインポートする（追加または置換）。"""
        if not isinstance(apps_data, list):
            raise ValueError("インポートデータの apps はリスト形式である必要があります。")

        validated: list[dict[str, Any]] = []
        for idx, raw in enumerate(apps_data):
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name", "")).strip()
            command = str(raw.get("command", "")).strip()
            if not name or not command:
                continue
            validated.append(_normalize_app(raw, idx))

        if not validated:
            raise ValueError("有効なアプリ定義が見つかりませんでした。")

        with self._lock:
            if replace:
                self._apps = validated
            else:
                existing_ids = {item["id"] for item in self._apps}
                for item in validated:
                    if item["id"] in existing_ids:
                        item["id"] = uuid.uuid4().hex[:12]
                    item["order"] = len(self._apps)
                    self._apps.append(item)
                    existing_ids.add(item["id"])
            self._sort_apps_locked()
            self._save_apps_locked()
            logger.info("アプリをインポートしました (%d件, replace=%s)", len(validated), replace)
            return deepcopy(self._apps)
