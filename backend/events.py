"""Server-Sent Events (SSE) 用のスレッドセーフなイベントブローカー。"""

from __future__ import annotations

import json
import queue
import threading
from typing import Any


class EventBroker:
    """接続中の全フロントエンドクライアントへリアルタイムイベントを配信する。"""

    def __init__(self, max_queue_size: int = 500) -> None:
        self._lock = threading.Lock()
        self._subscribers: set[queue.Queue[str]] = set()
        self._max_queue_size = max_queue_size

    def subscribe(self) -> queue.Queue[str]:
        """新規クライアント用のイベントキューを作成して登録する。"""
        client_queue: queue.Queue[str] = queue.Queue(maxsize=self._max_queue_size)
        with self._lock:
            self._subscribers.add(client_queue)
        return client_queue

    def unsubscribe(self, client_queue: queue.Queue[str]) -> None:
        """切断したクライアントのイベントキューを登録解除する。"""
        with self._lock:
            self._subscribers.discard(client_queue)

    def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        """全購読クライアントへ SSE フォーマット済みメッセージを配信する。"""
        data_json = json.dumps(payload, ensure_ascii=False)
        message = f"event: {event_type}\ndata: {data_json}\n\n"

        with self._lock:
            subscribers = list(self._subscribers)

        for client_queue in subscribers:
            try:
                client_queue.put_nowait(message)
            except queue.Full:
                # 遅延クライアントで詰まらないよう最も古いメッセージを1件破棄して再投入
                try:
                    client_queue.get_nowait()
                    client_queue.put_nowait(message)
                except (queue.Empty, queue.Full):
                    pass
