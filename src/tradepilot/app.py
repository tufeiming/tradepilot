"""Headless VeighNa application lifecycle for TradePilot."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future
from datetime import datetime

from vnpy.event import Event, EventEngine
from vnpy.trader.engine import MainEngine
from vnpy_ctastrategy import CtaEngine, CtaStrategyApp

from tradepilot.config import AppConfig
from tradepilot.events import EVENT_TRADEPILOT_FEED, FeedStatus, FeedStatusEvent
from tradepilot.gateway import TencentGateway
from tradepilot.notifier import FeishuClient, NotificationService, NotificationStore
from tradepilot.strategy import DoubleMaSignalStrategy
from tradepilot.tencent import SHANGHAI_TZ

LOGGER = logging.getLogger(__name__)
STRATEGY_PREFIX = "tradepilot_"


class TradePilotApp:
    """Owns engines, strategies, notification delivery, and graceful shutdown."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.stop_event = threading.Event()
        self.event_engine = EventEngine()
        self.main_engine = MainEngine(self.event_engine)
        try:
            self.main_engine.add_gateway(TencentGateway)
            self.cta_engine: CtaEngine = self.main_engine.add_app(CtaStrategyApp)
        except Exception:
            self.main_engine.close()
            raise
        self.cta_engine.classes[DoubleMaSignalStrategy.__name__] = DoubleMaSignalStrategy

        client = None
        if config.feishu.enabled and config.feishu.webhook_url:
            client = FeishuClient(config.feishu.webhook_url, config.feishu.secret)
        store = NotificationStore(config.runtime_dir / "tradepilot_notifications.json")
        self.notifier = NotificationService(self.event_engine, store, client)
        self.event_engine.register(EVENT_TRADEPILOT_FEED, self._on_feed_control)
        self._managed_names: set[str] = set()
        self._started = False
        self._closed = False

    def start(self) -> int:
        self.notifier.start()
        self.main_engine.connect(
            {
                "symbols": list(self.config.monitor.symbols),
                "poll_interval_seconds": self.config.monitor.poll_interval_seconds,
                "stale_after_seconds": self.config.monitor.stale_after_seconds,
            },
            TencentGateway.default_name,
        )
        self._wait_for_contracts()
        self.cta_engine.init_engine()
        self._reconcile_strategies()

        ready = 0
        for strategy_name in sorted(self._managed_names):
            future: Future = self.cta_engine.init_strategy(strategy_name)
            try:
                future.result(timeout=30)
            except Exception as exc:
                LOGGER.exception("Strategy initialization failed: %s", strategy_name)
                self.notifier.enqueue(
                    f"strategy-init-error|{strategy_name}|{datetime.now(SHANGHAI_TZ):%Y%m%d}",
                    f"[策略初始化失败] {strategy_name}\n{exc}",
                )
                continue

            strategy = self.cta_engine.strategies[strategy_name]
            if not getattr(strategy, "history_ready", False):
                count = getattr(strategy, "history_count", 0)
                self.notifier.enqueue(
                    f"strategy-not-ready|{strategy_name}|{datetime.now(SHANGHAI_TZ):%Y%m%d}",
                    f"[策略未就绪] {strategy_name}\n历史分钟线 {count}/"
                    f"{self.config.monitor.minimum_history_bars}，本次不启动",
                )
                continue

            self.cta_engine.start_strategy(strategy_name)
            ready += 1
            self.notifier.enqueue(
                f"strategy-ready|{strategy_name}|{datetime.now(SHANGHAI_TZ):%Y%m%d}",
                f"[策略就绪] {strategy_name}\n"
                f"MA{self.config.strategy.fast_window}/MA{self.config.strategy.slow_window}\n"
                "运行模式：仅通知，委托已禁用",
            )

        self._started = True
        self.notifier.enqueue(
            f"app-start|{datetime.now(SHANGHAI_TZ):%Y%m%d%H%M%S}",
            f"[TradePilot启动]\n配置 {len(self._managed_names)} 个标的，"
            f"成功启动 {ready} 个策略\n数据源：Tencent POC",
        )
        return ready

    def run_forever(self) -> None:
        self.stop_event.wait()

    def request_stop(self) -> None:
        self.stop_event.set()

    def close(self) -> None:
        if self._closed:
            return
        occurred_at = datetime.now(SHANGHAI_TZ)
        if self._started:
            self.notifier.enqueue(
                f"app-stop|{occurred_at:%Y%m%d%H%M%S}",
                f"[TradePilot正常关闭]\n{occurred_at:%Y-%m-%d %H:%M:%S}",
            )
            self.notifier.flush(timeout=5.0)
        self.main_engine.close()
        self.event_engine.unregister(EVENT_TRADEPILOT_FEED, self._on_feed_control)
        self.notifier.stop()
        self._started = False
        self._closed = True

    def _reconcile_strategies(self) -> None:
        expected = {
            strategy_name(vt_symbol): vt_symbol for vt_symbol in self.config.monitor.symbols
        }
        managed_existing = {
            name for name in self.cta_engine.strategies if name.startswith(STRATEGY_PREFIX)
        }

        for stale_name in sorted(managed_existing - expected.keys()):
            self.cta_engine.remove_strategy(stale_name)

        setting = {
            "fast_window": self.config.strategy.fast_window,
            "slow_window": self.config.strategy.slow_window,
            "history_size": self.config.monitor.minimum_history_bars,
        }
        for name, vt_symbol in expected.items():
            if name in self.cta_engine.strategies:
                self.cta_engine.edit_strategy(name, setting)
            else:
                self.cta_engine.add_strategy(
                    DoubleMaSignalStrategy.__name__,
                    name,
                    vt_symbol,
                    setting,
                )
        self._managed_names = set(expected)

    def _wait_for_contracts(self, timeout: float = 5.0) -> None:
        """Wait until asynchronous contract events reach the main engine."""
        deadline = time.monotonic() + timeout
        missing = set(self.config.monitor.symbols)
        while missing and time.monotonic() < deadline:
            missing = {
                vt_symbol
                for vt_symbol in missing
                if self.main_engine.get_contract(vt_symbol) is None
            }
            if missing:
                time.sleep(0.01)
        if missing:
            raise RuntimeError(
                "Tencent gateway did not publish contracts: " + ", ".join(sorted(missing))
            )

    def _on_feed_control(self, event: Event) -> None:
        status: FeedStatusEvent = event.data
        if status.status is not FeedStatus.SESSION_CLOSED:
            return
        for name in self._managed_names:
            strategy = self.cta_engine.strategies.get(name)
            if strategy and strategy.trading:
                strategy.flush_bar()


def strategy_name(vt_symbol: str) -> str:
    symbol, exchange = vt_symbol.split(".")
    return f"{STRATEGY_PREFIX}{symbol}_{exchange.lower()}"
