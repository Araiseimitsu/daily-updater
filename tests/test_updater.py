"""Daily CLI Updater の自動テスト（ストレージ・並列ランナー・HTTP API）。"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.request import Request, urlopen

from backend.events import EventBroker
from backend.runner import UpdateRunner
from backend.server import UpdaterHTTPServer
from backend.storage import AppStorage


class TestDailyUpdater(unittest.TestCase):
    """ストレージ・並列実行・REST API の動作を検証するテストスイート。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        tmp_dir = Path(self._tmp.name)
        self.storage = AppStorage(
            apps_file=tmp_dir / "apps.json",
            settings_file=tmp_dir / "settings.json",
        )
        self.broker = EventBroker()
        self.runner = UpdateRunner(storage=self.storage, broker=self.broker)

    def tearDown(self) -> None:
        self.runner.cancel_all()
        self._tmp.cleanup()

    def test_default_apps_initialized(self) -> None:
        """初期状態で6つのプリセットアプリが登録されていることを確認する。"""
        apps = self.storage.list_apps()
        self.assertEqual(len(apps), 6)
        commands = [app["command"] for app in apps]
        self.assertIn("claude update", commands)
        self.assertIn("codex update", commands)
        self.assertIn("agy update", commands)
        self.assertIn("agent update", commands)
        self.assertIn("npm install -g @opencode/cli@latest", commands)
        self.assertIn("grok update", commands)

    def test_storage_crud_and_reorder(self) -> None:
        """アプリの登録・更新・並び替え・削除が正しく動作することを確認する。"""
        created = self.storage.add_app(
            {
                "name": "テストツール",
                "command": "Write-Output 'ok'",
                "category": "Test",
                "enabled": True,
            }
        )
        self.assertEqual(created["name"], "テストツール")

        updated = self.storage.update_app(created["id"], {"enabled": False, "name": "更新後ツール"})
        self.assertFalse(updated["enabled"])
        self.assertEqual(updated["name"], "更新後ツール")

        apps_before = self.storage.list_apps()
        reversed_ids = [app["id"] for app in reversed(apps_before)]
        reordered = self.storage.reorder_apps(reversed_ids)
        self.assertEqual(reordered[0]["id"], reversed_ids[0])

        removed = self.storage.delete_app(created["id"])
        self.assertEqual(removed["id"], created["id"])
        self.assertIsNone(self.storage.get_app(created["id"]))

    def test_parallel_runner_success_and_failure(self) -> None:
        """複数コマンドの並列実行と成功・失敗ステータス判定を確認する。"""
        ok1 = self.storage.add_app({"name": "成功1", "command": "Write-Output 'hello-1'", "shell": "auto"})
        ok2 = self.storage.add_app({"name": "成功2", "command": "Write-Output 'hello-2'", "shell": "auto"})
        ng1 = self.storage.add_app({"name": "失敗1", "command": "exit 3", "shell": "auto"})

        res = self.runner.start_run([ok1["id"], ok2["id"], ng1["id"]], max_workers=3)
        self.assertEqual(len(res["started"]), 3)

        deadline = time.monotonic() + 25.0
        while time.monotonic() < deadline:
            snap = self.runner.get_runtime_snapshot()
            if not snap["batch"]["active"] and snap["batch"]["completed"] == 3:
                break
            time.sleep(0.2)

        snap = self.runner.get_runtime_snapshot()
        self.assertFalse(snap["batch"]["active"])
        self.assertEqual(snap["batch"]["succeeded"], 2)
        self.assertEqual(snap["batch"]["failed"], 1)
        self.assertEqual(snap["statuses"][ok1["id"]]["status"], "success")
        self.assertEqual(snap["statuses"][ok2["id"]]["status"], "success")
        self.assertEqual(snap["statuses"][ng1["id"]]["status"], "failed")
        self.assertEqual(snap["statuses"][ng1["id"]]["exit_code"], 3)

    def test_powershell_profile_function_resolution(self) -> None:
        """PowerShell プロファイルに定義された関数 (agent) がランナー経由で認識されることを確認する。"""
        probe = self.storage.add_app(
            {
                "name": "関数解決チェック",
                "command": "(Get-Command agent -ErrorAction Stop).CommandType",
                "shell": "auto",
            }
        )
        self.runner.start_run([probe["id"]], max_workers=1)
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            snap = self.runner.get_runtime_snapshot()
            if not snap["batch"]["active"] and snap["batch"]["completed"] == 1:
                break
            time.sleep(0.2)

        snap = self.runner.get_runtime_snapshot()
        self.assertEqual(snap["statuses"][probe["id"]]["status"], "success")
        self.assertIn("Function", snap["statuses"][probe["id"]]["message"])

    def test_http_api_bootstrap_and_crud(self) -> None:
        """HTTP サーバー経由の API と静的ファイル配信を確認する。"""
        server = UpdaterHTTPServer(("127.0.0.1", 0), self.storage, self.runner, self.broker)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urlopen(f"http://127.0.0.1:{port}/") as resp:
                self.assertEqual(resp.status, 200)
                html = resp.read().decode("utf-8")
                self.assertIn("Daily CLI Updater", html)

            with urlopen(f"http://127.0.0.1:{port}/api/bootstrap") as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(len(data["apps"]), 6)

            req = Request(
                f"http://127.0.0.1:{port}/api/apps",
                data=json.dumps({"name": "API登録", "command": "Write-Output 'api'"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req) as resp:
                self.assertEqual(resp.status, 201)
                created = json.loads(resp.read().decode("utf-8"))["app"]
                self.assertEqual(created["name"], "API登録")
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
