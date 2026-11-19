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

DEFAULT_HOST: str = "127.0.0.1"
DEFAULT_PORT: int = 8765

ALLOWED_SHELLS: tuple[str, ...] = ("auto", "pwsh", "powershell", "cmd")

DEFAULT_SETTINGS: dict[str, Any] = {
    "max_workers": 4,
    "default_shell": "auto",
    "timeout_seconds": 300,
}

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
