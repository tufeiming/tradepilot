import json
import time
from datetime import datetime

import requests
from vnpy.event import Event, EventEngine

from tradepilot.core.events import (
    EVENT_TRADEPILOT_SIGNAL,
    SignalDirection,
    SignalEvent,
)
from tradepilot.data_sources.tencent.client import SHANGHAI_TZ
from tradepilot.notifications.feishu import (
    FeishuClient,
    NotificationService,
    NotificationStore,
    make_feishu_signature,
)


class FakeResponse:
    def __init__(self, payload=None):
        self.payload = payload or {"code": 0}

    def raise_for_status(self):
        return

    def json(self):
        return self.payload


def test_feishu_client_builds_signed_payload():
    calls = []

    def sender(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse()

    client = FeishuClient(
        "https://open.feishu.cn/open-apis/bot/v2/hook/id",
        "secret",
        sender=sender,
        clock=lambda: 1_700_000_000,
    )
    client.send("hello")
    payload = calls[0][1]["json"]
    assert payload["content"]["text"] == "hello"
    assert payload["timestamp"] == "1700000000"
    assert payload["sign"] == make_feishu_signature(1_700_000_000, "secret")


def test_notification_store_persists_and_deduplicates(tmp_path):
    path = tmp_path / "notifications.json"
    store = NotificationStore(path)
    assert store.enqueue("same", "message") is True
    assert store.enqueue("same", "message") is False
    assert NotificationStore(path).pending_count() == 1
    store.mark_delivered("same")
    reloaded = NotificationStore(path)
    assert reloaded.pending_count() == 0
    assert reloaded.enqueue("same", "message") is False


def test_service_delivers_signal_once(tmp_path):
    calls = []

    def sender(url, **kwargs):
        calls.append(kwargs["json"])
        return FakeResponse()

    engine = EventEngine(interval=0.01)
    engine.start()
    store = NotificationStore(tmp_path / "notifications.json")
    service = NotificationService(
        engine,
        store,
        FeishuClient(
            "https://open.feishu.cn/open-apis/bot/v2/hook/id",
            sender=sender,
        ),
    )
    service.start()
    signal = SignalEvent(
        strategy_name="tradepilot_515080_sse",
        vt_symbol="515080.SSE",
        direction=SignalDirection.BUY,
        bar_time=datetime(2026, 7, 23, 10, 0, tzinfo=SHANGHAI_TZ),
        price=1.55,
        fast_ma=1.54,
        slow_ma=1.53,
    )
    engine.put(Event(EVENT_TRADEPILOT_SIGNAL, signal))
    engine.put(Event(EVENT_TRADEPILOT_SIGNAL, signal))

    deadline = time.monotonic() + 2
    while len(calls) < 1 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert len(calls) == 1
    assert "[BUY] 515080.SSE" in calls[0]["content"]["text"]
    service.stop()
    engine.stop()


def test_failed_delivery_remains_in_outbox(tmp_path):
    def sender(url, **kwargs):
        raise requests.Timeout("offline")

    engine = EventEngine(interval=60)
    store_path = tmp_path / "notifications.json"
    service = NotificationService(
        engine,
        NotificationStore(store_path),
        FeishuClient(
            "https://open.feishu.cn/open-apis/bot/v2/hook/id",
            sender=sender,
        ),
    )
    service.start()
    service.enqueue("pending", "important")
    deadline = time.monotonic() + 2
    attempts = 0
    while not attempts and time.monotonic() < deadline:
        time.sleep(0.02)
        attempts = json.loads(store_path.read_text(encoding="utf-8"))["pending"][0]["attempts"]
    service.stop()
    assert attempts == 1
    assert NotificationStore(store_path).pending_count() == 1
