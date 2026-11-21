"""Daily CLI Updater の自動テスト（ストレージ・並列ランナー・HTTP API）。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen

from backend.cleaner import (
    PCCleaner,
    _clean_directory_tree,
    _is_safe_cleanup_root,
    _parse_uv_prune_output,
    _scan_directory_tree,
)
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

    def test_cleaner_age_filter_and_safety_guards(self) -> None:
        """一時ファイルの保護期間フィルタ・空ディレクトリ削除・安全ガードを検証する。"""
        self.assertFalse(_is_safe_cleanup_root(Path("C:\\")))
        self.assertFalse(_is_safe_cleanup_root(Path("C:\\Windows")))
        self.assertFalse(_is_safe_cleanup_root(Path.home()))

        root = Path(self._tmp.name) / "fake_temp"
        old_dir = root / "old_sub"
        old_dir.mkdir(parents=True)

        old_file = old_dir / "stale.tmp"
        old_file.write_bytes(b"a" * 2048)
        recent_file = root / "recent.tmp"
        recent_file.write_bytes(b"b" * 1024)

        # 古いファイルとディレクトリの更新時刻を 48 時間前に設定
        old_ts = time.time() - (48 * 3600)
        os.utime(old_file, (old_ts, old_ts))
        os.utime(old_dir, (old_ts, old_ts))

        scan = _scan_directory_tree([root], min_age_hours=24)
        self.assertEqual(scan["file_count"], 1)
        self.assertEqual(scan["reclaimable_bytes"], 2048)
        self.assertEqual(scan["skipped_recent"], 1)

        clean = _clean_directory_tree([root], min_age_hours=24)
        self.assertEqual(clean["deleted_files"], 1)
        self.assertEqual(clean["deleted_dirs"], 1)
        self.assertEqual(clean["freed_bytes"], 2048)
        self.assertEqual(clean["skipped_recent"], 1)
        self.assertFalse(old_file.exists())
        self.assertFalse(old_dir.exists())
        self.assertTrue(recent_file.exists())
        self.assertTrue(root.exists())

    def test_cleaner_skips_locked_files_gracefully(self) -> None:
        """使用中（PermissionError）のファイルをエラーにせず安全にスキップすることを確認する。"""
        root = Path(self._tmp.name) / "locked_temp"
        root.mkdir()
        target_ok = root / "ok.tmp"
        target_ok.write_bytes(b"12345")
        target_locked = root / "locked.tmp"
        target_locked.write_bytes(b"999999")

        orig_unlink = Path.unlink

        def _fake_unlink(path_self: Path, *args: object, **kwargs: object) -> None:
            if path_self.name == "locked.tmp":
                raise PermissionError("file in use")
            orig_unlink(path_self, *args, **kwargs)

        with patch.object(Path, "unlink", _fake_unlink):
            res = _clean_directory_tree([root], min_age_hours=0)

        self.assertEqual(res["deleted_files"], 1)
        self.assertEqual(res["freed_bytes"], 5)
        self.assertEqual(res["skipped_locked"], 1)
        self.assertFalse(target_ok.exists())
        self.assertTrue(target_locked.exists())

    def test_cleaner_http_api_scan_toggle_and_run(self) -> None:
        """PCクリーニング REST API (スキャン・ON/OFF切替・個別実行) が動作することを確認する。"""
        fake_temp = Path(self._tmp.name) / "api_temp"
        fake_logs = Path(self._tmp.name) / "api_logs"
        fake_temp.mkdir()
        fake_logs.mkdir()

        old_tmp = fake_temp / "old_cache.dat"
        old_tmp.write_bytes(b"x" * 4096)
        old_ts = time.time() - (72 * 3600)
        os.utime(old_tmp, (old_ts, old_ts))

        active_log = fake_logs / "updater.log"
        active_log.write_text("active", encoding="utf-8")
        old_log = fake_logs / "updater.log.1"
        old_log.write_text("old-rotated-log", encoding="utf-8")
        old_log_ts = time.time() - (10 * 24 * 3600)
        os.utime(old_log, (old_log_ts, old_log_ts))
        os.utime(active_log, (old_log_ts, old_log_ts))

        cleaner = PCCleaner(
            storage=self.storage,
            broker=self.broker,
            log_callback=self.runner.append_external_log,
            target_overrides={
                "user-temp": [fake_temp],
                "app-old-logs": [fake_logs],
            },
        )
        server = UpdaterHTTPServer(("127.0.0.1", 0), self.storage, self.runner, self.broker, cleaner=cleaner)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urlopen(f"http://127.0.0.1:{port}/api/bootstrap") as resp:
                boot = json.loads(resp.read().decode("utf-8"))
                self.assertIn("cleaner", boot)
                self.assertGreaterEqual(len(boot["cleaner"]["tasks"]), 6)

            # トグル更新テスト
            toggle_req = Request(
                f"http://127.0.0.1:{port}/api/cleaner/tasks/pip-cache",
                data=json.dumps({"enabled": True}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="PUT",
            )
            with urlopen(toggle_req) as resp:
                toggled = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(toggled["state"]["enabled"])

            # スキャン実行（テスト対象2項目のみ）
            scan_req = Request(
                f"http://127.0.0.1:{port}/api/cleaner/scan",
                data=json.dumps({"task_ids": ["user-temp", "app-old-logs"]}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(scan_req) as resp:
                self.assertTrue(json.loads(resp.read().decode("utf-8"))["started"])

            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                snap = cleaner.get_snapshot()
                if not snap["state"]["active"]:
                    break
                time.sleep(0.05)

            tasks_by_id = {t["id"]: t for t in cleaner.get_snapshot()["tasks"]}
            self.assertEqual(tasks_by_id["user-temp"]["scan_bytes"], 4096)
            self.assertEqual(tasks_by_id["app-old-logs"]["scan_count"], 1)

            # クリーニング実行（テスト対象2項目のみ）
            run_req = Request(
                f"http://127.0.0.1:{port}/api/cleaner/run",
                data=json.dumps({"mode": "selected", "task_ids": ["user-temp", "app-old-logs"]}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(run_req) as resp:
                self.assertTrue(json.loads(resp.read().decode("utf-8"))["started"])

            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                snap = cleaner.get_snapshot()
                if not snap["state"]["active"]:
                    break
                time.sleep(0.05)

            self.assertFalse(old_tmp.exists())
            self.assertFalse(old_log.exists())
            self.assertTrue(active_log.exists())
            self.assertGreaterEqual(cleaner.get_snapshot()["state"]["last_freed_bytes"], 4096)
        finally:
            cleaner.cancel()
            server.shutdown()
            server.server_close()

    def test_uv_prune_output_parser_and_dns_admin_guard(self) -> None:
        """uv cache prune 出力パーサと、非管理者時の DNS フラッシュ除外を確認する。"""
        freed, files = _parse_uv_prune_output("Pruning cache at: C:\\uv\\cache\nRemoved 1,234 files (256.5MiB)")
        self.assertEqual(files, 1234)
        self.assertEqual(freed, int(256.5 * 1024 * 1024))

        freed_gb, files_gb = _parse_uv_prune_output("Removed 10 files (1.5GiB)")
        self.assertEqual(files_gb, 10)
        self.assertEqual(freed_gb, int(1.5 * (1024**3)))

        self.assertEqual(_parse_uv_prune_output("No unused entries found"), (0, 0))

        cleaner = PCCleaner(storage=self.storage, broker=self.broker)
        with patch("backend.cleaner._is_admin", return_value=False):
            snap = cleaner.get_snapshot()
            dns_task = next(t for t in snap["tasks"] if t["id"] == "dns-flush")
            self.assertFalse(dns_task["available"])
            self.assertIn("管理者権限", dns_task["unavailable_reason"])


if __name__ == "__main__":
    unittest.main()
