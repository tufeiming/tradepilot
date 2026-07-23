"""Reliable Feishu delivery with a small persistent outbox."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

import requests
from vnpy.event import Event, EventEngine

from tradepilot.core.events import (
    EVENT_TRADEPILOT_FEED,
    EVENT_TRADEPILOT_SIGNAL,
    FeedStatusEvent,
    SignalEvent,
)

LOGGER = logging.getLogger(__name__)
MAX_DELIVERED_IDS = 2_000


class ResponseLike(Protocol):
    def raise_for_status(self) -> None: ...

    def json(self) -> dict[str, Any]: ...


class Sender(Protocol):
    def __call__(
        self,
        url: str,
        *,
        json: dict[str, Any],
        timeout: tuple[float, float],
    ) -> ResponseLike: ...


class FeishuDeliveryError(RuntimeError):
    """Raised when Feishu rejects or cannot receive a message."""


def make_feishu_signature(timestamp: int, secret: str) -> str:
    """Create the signature expected by a signed Feishu custom bot."""
    string_to_sign = f"{timestamp}\n{secret}".encode()
    digest = hmac.new(string_to_sign, digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


class FeishuClient:
    """Synchronous Feishu webhook client used by the outbox worker."""

    def __init__(
        self,
        webhook_url: str,
        secret: str | None = None,
        *,
        sender: Sender | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.webhook_url = webhook_url
        self.secret = secret
        self._sender: Sender = sender or requests.Session().post
        self._clock = clock

    def send(self, text: str) -> None:
        payload: dict[str, Any] = {
            "msg_type": "text",
            "content": {"text": text},
        }
        if self.secret:
            timestamp = int(self._clock())
            payload["timestamp"] = str(timestamp)
            payload["sign"] = make_feishu_signature(timestamp, self.secret)

        try:
            response = self._sender(
                self.webhook_url,
                json=payload,
                timeout=(5.0, 10.0),
            )
            response.raise_for_status()
            result = response.json()
        except (requests.RequestException, ValueError, OSError) as exc:
            raise FeishuDeliveryError(f"Feishu request failed: {exc}") from exc

        code = result.get("code", result.get("StatusCode", 0))
        if code not in (0, "0", None):
            message = result.get("msg", result.get("StatusMessage", "unknown error"))
            raise FeishuDeliveryError(f"Feishu rejected message ({code}): {message}")


@dataclass(slots=True)
class PendingNotification:
    notification_id: str
    text: str
    attempts: int = 0
    next_attempt_at: float = 0.0


class NotificationStore:
    """Thread-safe JSON persistence for delivered IDs and pending messages."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._delivered: list[str] = []
        self._delivered_set: set[str] = set()
        self._pending: dict[str, PendingNotification] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            delivered = [str(value) for value in raw.get("delivered", [])]
            pending = [PendingNotification(**value) for value in raw.get("pending", [])]
        except (OSError, ValueError, TypeError) as exc:
            LOGGER.error("Ignoring corrupt notification state %s: %s", self.path, exc)
            return

        self._delivered = delivered[-MAX_DELIVERED_IDS:]
        self._delivered_set = set(self._delivered)
        self._pending = {
            item.notification_id: item
            for item in pending
            if item.notification_id not in self._delivered_set
        }

    def enqueue(self, notification_id: str, text: str) -> bool:
        with self._lock:
            if notification_id in self._delivered_set or notification_id in self._pending:
                return False
            self._pending[notification_id] = PendingNotification(notification_id, text)
            self._save()
            return True

    def due(self, now: float) -> list[PendingNotification]:
        with self._lock:
            return [
                PendingNotification(**asdict(item))
                for item in self._pending.values()
                if item.next_attempt_at <= now
            ]

    def mark_delivered(self, notification_id: str) -> None:
        with self._lock:
            self._pending.pop(notification_id, None)
            if notification_id not in self._delivered_set:
                self._delivered.append(notification_id)
                self._delivered_set.add(notification_id)
            if len(self._delivered) > MAX_DELIVERED_IDS:
                removed = self._delivered[:-MAX_DELIVERED_IDS]
                self._delivered = self._delivered[-MAX_DELIVERED_IDS:]
                self._delivered_set.difference_update(removed)
            self._save()

    def mark_failed(self, notification_id: str, now: float) -> None:
        with self._lock:
            item = self._pending.get(notification_id)
            if not item:
                return
            item.attempts += 1
            item.next_attempt_at = now + min(2 ** min(item.attempts, 8), 300)
            self._save()

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "delivered": self._delivered,
            "pending": [asdict(item) for item in self._pending.values()],
        }
        temporary = self.path.with_suffix(f"{self.path.suffix}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)


class NotificationService:
    """Event adapter and asynchronous persistent delivery worker."""

    def __init__(
        self,
        event_engine: EventEngine,
        store: NotificationStore,
        client: FeishuClient | None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.event_engine = event_engine
        self.store = store
        self.client = client
        self._clock = clock
        self._wake = threading.Event()
        self._active = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._active:
            return
        self.event_engine.register(EVENT_TRADEPILOT_SIGNAL, self._on_signal)
        self.event_engine.register(EVENT_TRADEPILOT_FEED, self._on_feed)
        self._active = True
        self._thread = threading.Thread(
            target=self._run,
            name="tradepilot-feishu",
            daemon=True,
        )
        self._thread.start()

    def enqueue(self, notification_id: str, text: str) -> bool:
        LOGGER.info("Notification %s: %s", notification_id, text.replace("\n", " | "))
        queued = self.store.enqueue(notification_id, text)
        if not queued:
            return False
        if self.client is None:
            self.store.mark_delivered(notification_id)
        else:
            self._wake.set()
        return True

    def stop(self, timeout: float = 5.0) -> None:
        if not self._active:
            return
        self._active = False
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        self.event_engine.unregister(EVENT_TRADEPILOT_SIGNAL, self._on_signal)
        self.event_engine.unregister(EVENT_TRADEPILOT_FEED, self._on_feed)

    def flush(self, timeout: float = 5.0) -> bool:
        """Wait briefly for currently deliverable notifications to leave the outbox."""
        deadline = time.monotonic() + timeout
        self._wake.set()
        while self.store.pending_count() and time.monotonic() < deadline:
            time.sleep(0.05)
        return self.store.pending_count() == 0

    def _run(self) -> None:
        while self._active:
            due = self.store.due(self._clock())
            if not due:
                self._wake.wait(1.0)
                self._wake.clear()
                continue

            for item in due:
                if not self._active or self.client is None:
                    break
                try:
                    self.client.send(item.text)
                except FeishuDeliveryError as exc:
                    LOGGER.error("Notification delivery failed: %s", exc)
                    self.store.mark_failed(item.notification_id, self._clock())
                else:
                    self.store.mark_delivered(item.notification_id)

    def _on_signal(self, event: Event) -> None:
        signal: SignalEvent = event.data
        text = (
            f"[{signal.direction.value}] {signal.vt_symbol}\n"
            f"时间: {signal.bar_time:%Y-%m-%d %H:%M}\n"
            f"价格: {signal.price:.4f}\n"
            f"MA快线: {signal.fast_ma:.4f}\n"
            f"MA慢线: {signal.slow_ma:.4f}\n"
            f"数据源: {signal.source}"
        )
        self.enqueue(f"signal|{signal.dedup_key}", text)

    def _on_feed(self, event: Event) -> None:
        status: FeedStatusEvent = event.data
        notification_id = (
            f"feed|{status.status.value}|{status.occurred_at.strftime('%Y%m%d%H%M%S')}"
        )
        text = (
            f"[行情{status.status.value}]\n{status.message}\n{status.occurred_at:%Y-%m-%d %H:%M:%S}"
        )
        self.enqueue(notification_id, text)
