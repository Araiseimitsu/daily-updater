"""アプリケーション共通の設定・定数・初期プリセット定義モジュール。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

BASE_DIR: Path = Path(__file__).resolve().parent
DATA_DIR: Path = BASE_DIR / "data"
LOGS_DIR: Path = BASE_DIR / "logs"
FRONTEND_DIR: Path = BASE_DIR / "frontend"

APPS_FILE: Path = DATA_DIR / "apps.json"
SETTINGS_FILE: Path = DATA_DIR / "settings.json"
CLEANER_FILE: Path = DATA_DIR / "cleaner.json"

DEFAULT_HOST: str = "127.0.0.1"
DEFAULT_PORT: int = 8765

ALLOWED_SHELLS: tuple[str, ...] = ("auto", "pwsh", "powershell", "cmd")

DEFAULT_SETTINGS: dict[str, Any] = {
    "max_workers": 4,
    "default_shell": "auto",
    "timeout_seconds": 300,
    "cleaner_temp_min_age_hours": 24,
    "clean_after_update_all": False,
}

# PCクリーニング標準タスク定義（すべて通常権限で安全に実行可能）
DEFAULT_CLEANER_TASKS: list[dict[str, Any]] = [
    {
        "id": "user-temp",
        "name": "ユーザー一時ファイル",
        "category": "一時ファイル",
        "target_display": "%TEMP%",
        "description": "使用中（ロック中）のファイルや保護期間内の新しいファイルは自動でスキップします。",
        "kind": "directory",
        "safety": "safe",
        "default_enabled": True,
        "supports_age_filter": True,
    },
    {
        "id": "crash-dumps",
        "name": "クラッシュダンプ・エラー報告",
        "category": "システム",
        "target_display": "%LOCALAPPDATA%\\CrashDumps / WER",
        "description": "アプリ異常終了時に残る診断ダンプと Windows エラー報告アーカイブを削除します。",
        "kind": "directory",
        "safety": "safe",
        "default_enabled": True,
        "supports_age_filter": False,
    },
    {
        "id": "dns-flush",
        "name": "DNS キャッシュのフラッシュ",
        "category": "システム",
        "target_display": "ipconfig /flushdns",
        "description": "古い名前解決キャッシュをクリアします（Windows の仕様により管理者権限での起動時のみ利用可能）。",
        "kind": "command",
        "safety": "optional",
        "default_enabled": False,
        "supports_age_filter": False,
    },
    {
        "id": "uv-cache-prune",
        "name": "uv 未使用キャッシュ整理",
        "category": "開発キャッシュ",
        "target_display": "uv cache prune",
        "description": "uv で参照されなくなったキャッシュのみを安全に整理します（大規模キャッシュ時は数分かかることがあります）。",
        "kind": "command",
        "safety": "safe",
        "default_enabled": True,
        "supports_age_filter": False,
    },
    {
        "id": "app-old-logs",
        "name": "アプリの古いログ整理",
        "category": "ログ",
        "target_display": "logs\\ (7日以上前の古いログ)",
        "description": "本アプリのローテート済み古いログを削除します（現行の updater.log は保持）。",
        "kind": "directory",
        "safety": "safe",
        "default_enabled": True,
        "supports_age_filter": False,
    },
    {
        "id": "pip-cache",
        "name": "pip ダウンロードキャッシュ",
        "category": "開発キャッシュ",
        "target_display": "%LOCALAPPDATA%\\pip\\Cache",
        "description": "pip のパッケージキャッシュを削除します（次回インストール時に再取得されます）。",
        "kind": "directory",
        "safety": "optional",
        "default_enabled": False,
        "supports_age_filter": False,
    },
    {
        "id": "npm-cache",
        "name": "npm キャッシュ",
        "category": "開発キャッシュ",
        "target_display": "npm cache clean --force",
        "description": "npm のダウンロードキャッシュを空にします（次回インストール時に再取得されます）。",
        "kind": "command",
        "safety": "optional",
        "default_enabled": False,
        "supports_age_filter": False,
    },
    {
        "id": "recycle-bin",
        "name": "ごみ箱を空にする",
        "category": "ごみ箱",
        "target_display": "Windows ごみ箱",
        "description": "ごみ箱内のファイルを完全に削除します（復元不可・誤削除防止のため既定OFF）。",
        "kind": "recycle_bin",
        "safety": "caution",
        "default_enabled": False,
        "supports_age_filter": False,
    },
]

# 初期登録アプリ（毎朝のアップデート対象としてプリセット）
DEFAULT_APPS: list[dict[str, Any]] = [
    {
        "id": "claude-code",
        "name": "Claude Code",
        "command": "claude update",
        "category": "AI CLI",
        "description": "Anthropic Claude Code CLI のアップデート",
        "shell": "auto",
        "enabled": True,
    },
    {
        "id": "codex-cli",
        "name": "Codex CLI",
        "command": "codex update",
        "category": "AI CLI",
        "description": "OpenAI Codex CLI のアップデート",
        "shell": "auto",
        "enabled": True,
    },
    {
        "id": "antigravity-agy",
        "name": "Antigravity (agy)",
        "command": "agy update",
        "category": "AI CLI",
        "description": "Antigravity CLI (agy) のアップデート",
        "shell": "auto",
        "enabled": True,
    },
    {
        "id": "cursor-agent",
        "name": "Cursor Agent",
        "command": "agent update",
        "category": "AI CLI",
        "description": "Cursor Agent (PowerShell関数経由) のアップデート",
        "shell": "auto",
        "enabled": True,
    },
    {
        "id": "opencode-cli",
        "name": "OpenCode CLI",
        "command": "npm install -g @opencode/cli@latest",
        "category": "npm Global",
        "description": "OpenCode CLI を npm グローバル最新版へ更新",
        "shell": "auto",
        "enabled": True,
    },
    {
        "id": "grok-cli",
        "name": "Grok CLI",
        "command": "grok update",
        "category": "AI CLI",
        "description": "Grok CLI のアップデート",
        "shell": "auto",
        "enabled": True,
    },
]

# 新規アプリ登録時の入力補助テンプレート
COMMAND_TEMPLATES: list[dict[str, str]] = [
    {
        "label": "CLI update",
        "command": "<tool-name> update",
        "category": "AI CLI",
        "description": "標準的な CLI のセルフアップデートコマンド",
    },
    {
        "label": "npm グローバル",
        "command": "npm install -g <package-name>@latest",
        "category": "npm Global",
        "description": "npm グローバルパッケージを最新版へ更新",
    },
    {
        "label": "pip パッケージ",
        "command": "py -3.14 -m pip install -U <package-name>",
        "category": "Python",
        "description": "Python パッケージを最新版へ更新",
    },
    {
        "label": "winget アプリ",
        "command": "winget upgrade --id <Package.Id> --silent --accept-package-agreements --accept-source-agreements",
        "category": "Windows",
        "description": "winget で管理されているアプリの更新",
    },
    {
        "label": "uv tool",
        "command": "uv tool upgrade <tool-name>",
        "category": "Python",
        "description": "uv でインストールした CLI ツールの更新",
    },
]
