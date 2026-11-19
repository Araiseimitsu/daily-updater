"""Daily CLI Updater エントリーポイント。

起動コマンド例:
    py -3.14 main.py
    py -3.14 main.py --port 8765 --debug
    py -3.14 main.py --no-browser
"""

from __future__ import annotations

import argparse
import socket
import threading
import time
import webbrowser

from backend.events import EventBroker
from backend.logger import setup_logger
from backend.runner import UpdateRunner
from backend.server import UpdaterHTTPServer
from backend.storage import AppStorage
from config import DEFAULT_HOST, DEFAULT_PORT


def _is_port_in_use(host: str, port: int) -> bool:
    """指定ポートがすでに待ち受け中かどうかを確認する。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def _open_browser_later(url: str, delay: float = 0.6) -> None:
    """サーバー起動直後に既定のブラウザでダッシュボードを開く。"""
    time.sleep(delay)
    webbrowser.open(url)


def parse_args() -> argparse.Namespace:
    """コマンドライン引数を解析する。"""
    parser = argparse.ArgumentParser(
        description="毎朝のCLIアプリを一括・個別アップデートできるローカルWebアプリ"
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"バインドするホスト名 (既定: {DEFAULT_HOST})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"待ち受けポート番号 (既定: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="起動時にブラウザを自動で開かない",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="デバッグモードを有効にし詳細ログを出力する",
    )
    return parser.parse_args()


def main() -> None:
    """アプリケーションを初期化して HTTP サーバーを起動する。"""
    args = parse_args()
    logger = setup_logger(debug=args.debug)
    url = f"http://{args.host}:{args.port}"

    # すでに同ポートで起動済みならブラウザだけ開いて終了する（二重起動防止）
    if _is_port_in_use(args.host, args.port):
        logger.info("すでにサーバーが起動しています: %s", url)
        if not args.no_browser:
            webbrowser.open(url)
        return

    storage = AppStorage()
    broker = EventBroker()
    runner = UpdateRunner(storage=storage, broker=broker)
    server = UpdaterHTTPServer((args.host, args.port), storage, runner, broker)

    logger.info("Daily CLI Updater を起動しました: %s", url)
    logger.info("終了するには Ctrl+C を押してください。")

    if not args.no_browser:
        threading.Thread(
            target=_open_browser_later,
            args=(url,),
            daemon=True,
        ).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("終了シグナルを受信しました。実行中の処理を停止します...")
        runner.cancel_all()
    finally:
        server.server_close()
        logger.info("サーバーを正常終了しました。")


if __name__ == "__main__":
    main()
