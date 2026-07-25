"""Read-only VeighNa gateway backed by Tencent's unofficial public feed."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Sequence
from datetime import datetime
from datetime import time as daytime

from vnpy.event import Event, EventEngine
from vnpy.trader.constant import Exchange, Interval, Product
from vnpy.trader.gateway import BaseGateway
from vnpy.trader.object import (
    BarData,
    CancelRequest,
    ContractData,
    HistoryRequest,
    OrderRequest,
    SubscribeRequest,
    TickData,
)

from tradepilot.core.config import ConfigError, parse_vt_symbol
from tradepilot.core.events import (
    EVENT_TRADEPILOT_FEED,
    FeedStatus,
    FeedStatusEvent,
)
from tradepilot.data_sources.tencent.client import (
    SHANGHAI_TZ,
    TencentClient,
    TencentError,
    is_trading_session,
)

LOGGER = logging.getLogger(__name__)


class TradingDisabledError(RuntimeError):
    """Raised for every order attempt through the POC gateway."""


class TencentGateway(BaseGateway):
    """Market-data-only gateway. It intentionally cannot place an order."""

    default_name = "TENCENT"
    default_setting = {
        "symbols": "515080.SSE",
        "poll_interval_seconds": 3.0,
        "stale_after_seconds": 30.0,
    }
    exchanges = [Exchange.SSE, Exchange.SZSE]

    def __init__(self, event_engine: EventEngine, gateway_name: str) -> None:
        super().__init__(event_engine, gateway_name)
        self.client = TencentClient()
        self._configured_symbols: set[str] = set()
        self._subscribed: set[str] = set()
        self._subscription_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._poll_interval = 3.0
        self._stale_after = 30.0
        self._last_fresh_monotonic: float | None = None
        self._outage_reported = False
        self._no_data_date = None
        self._session_date = None
        self._was_in_session = False

    def connect(self, setting: dict) -> None:
        symbols = _normalize_symbols(setting.get("symbols", []))
        self._poll_interval = float(setting.get("poll_interval_seconds", 3.0))
        self._stale_after = float(setting.get("stale_after_seconds", 30.0))
        self._configured_symbols = set(symbols)

        for vt_symbol in symbols:
            symbol, exchange_name = parse_vt_symbol(vt_symbol)
            exchange = Exchange[exchange_name]
            is_etf = symbol.startswith(("15", "51", "56", "58"))
            contract = ContractData(
                gateway_name=self.gateway_name,
                symbol=symbol,
                exchange=exchange,
                name=symbol,
                product=Product.ETF if is_etf else Product.EQUITY,
                size=100,
                pricetick=0.001 if is_etf else 0.01,
                min_volume=100,
                net_position=True,
                history_data=True,
            )
            self.on_contract(contract)

        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="tradepilot-tencent",
            daemon=True,
        )
        self._thread.start()
        self.write_log(f"腾讯POC行情已连接，配置 {len(symbols)} 个标的")
        self._emit_status(FeedStatus.CONNECTED, f"腾讯POC行情已连接：{len(symbols)} 个标的")

    def close(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)

    def subscribe(self, req: SubscribeRequest) -> None:
        if req.vt_symbol not in self._configured_symbols:
            self.write_log(f"拒绝订阅未配置标的：{req.vt_symbol}")
            return
        with self._subscription_lock:
            self._subscribed.add(req.vt_symbol)
        self.write_log(f"订阅行情：{req.vt_symbol}")

    def send_order(self, req: OrderRequest) -> str:
        raise TradingDisabledError(
            f"Tencent POC gateway is read-only; rejected order for {req.vt_symbol}"
        )

    def cancel_order(self, req: CancelRequest) -> None:
        raise TradingDisabledError("Tencent POC gateway has no orders to cancel")

    def query_account(self) -> None:
        return

    def query_position(self) -> None:
        return

    def query_history(self, req: HistoryRequest) -> list[BarData]:
        now = datetime.now(SHANGHAI_TZ)
        try:
            if req.interval is Interval.MINUTE:
                snapshots = self.client.fetch_history(
                    req.vt_symbol,
                    now=now,
                    start=req.start,
                    end=req.end,
                )
                return [
                    BarData(
                        gateway_name=self.gateway_name,
                        symbol=item.symbol,
                        exchange=req.exchange,
                        datetime=item.timestamp,
                        interval=Interval.MINUTE,
                        volume=item.volume,
                        turnover=item.turnover,
                        open_price=item.close_price,
                        high_price=item.close_price,
                        low_price=item.close_price,
                        close_price=item.close_price,
                    )
                    for item in snapshots
                ]

            if req.interval is Interval.DAILY:
                daily_snapshots = self.client.fetch_daily_history(
                    req.vt_symbol,
                    now=now,
                    start=req.start,
                    end=req.end,
                )
                return [
                    BarData(
                        gateway_name=self.gateway_name,
                        symbol=item.symbol,
                        exchange=req.exchange,
                        datetime=item.timestamp,
                        interval=Interval.DAILY,
                        volume=item.volume,
                        turnover=item.turnover,
                        open_price=item.open_price,
                        high_price=item.high_price,
                        low_price=item.low_price,
                        close_price=item.close_price,
                    )
                    for item in daily_snapshots
                ]
        except TencentError as exc:
            self.write_log(f"加载腾讯{req.interval.value}历史失败 {req.vt_symbol}：{exc}")
            return []

        self.write_log(f"腾讯POC历史仅支持1分钟和日线周期：{req.vt_symbol}")
        return []

    def _run(self) -> None:
        while not self._stop.is_set():
            now = datetime.now(SHANGHAI_TZ)
            in_session = is_trading_session(now)
            self._handle_session_transition(now, in_session)
            if not in_session:
                self._stop.wait(min(self._poll_interval, 5.0))
                continue

            symbols = self._subscription_snapshot()
            if symbols:
                self._poll_once(symbols, now)
                self._check_staleness(now)
            self._stop.wait(self._poll_interval)

    def _subscription_snapshot(self) -> tuple[str, ...]:
        with self._subscription_lock:
            return tuple(sorted(self._subscribed))

    def _poll_once(self, symbols: tuple[str, ...], now: datetime) -> None:
        try:
            snapshots = self.client.fetch_quotes(symbols)
        except TencentError as exc:
            LOGGER.warning("Tencent quote poll failed: %s", exc)
            return

        fresh = 0
        for snapshot in snapshots:
            age = (now - snapshot.timestamp).total_seconds()
            if snapshot.timestamp.date() != now.date() or age > self._stale_after or age < -5:
                continue
            exchange = Exchange[snapshot.exchange]
            tick = TickData(
                gateway_name=self.gateway_name,
                symbol=snapshot.symbol,
                exchange=exchange,
                datetime=snapshot.timestamp,
                name=snapshot.name,
                volume=snapshot.volume,
                turnover=snapshot.turnover,
                last_price=snapshot.last_price,
                open_price=snapshot.open_price,
                high_price=snapshot.high_price,
                low_price=snapshot.low_price,
                pre_close=snapshot.pre_close,
                bid_price_1=snapshot.bid_price_1,
                bid_volume_1=snapshot.bid_volume_1,
                ask_price_1=snapshot.ask_price_1,
                ask_volume_1=snapshot.ask_volume_1,
                localtime=now,
            )
            self.on_tick(tick)
            fresh += 1

        if fresh:
            self._last_fresh_monotonic = time.monotonic()
            if self._outage_reported:
                self._outage_reported = False
                self._emit_status(FeedStatus.RECOVERED, "腾讯实时行情已恢复")

    def _check_staleness(self, now: datetime) -> None:
        if self._last_fresh_monotonic is not None:
            stale_for = time.monotonic() - self._last_fresh_monotonic
            if stale_for > self._stale_after and not self._outage_reported:
                self._outage_reported = True
                self._emit_status(
                    FeedStatus.OUTAGE,
                    f"交易时段已 {int(stale_for)} 秒没有新鲜腾讯行情",
                )
            return

        if now.time() >= daytime(9, 35) and self._no_data_date != now.date():
            self._no_data_date = now.date()
            self._emit_status(
                FeedStatus.NO_DATA,
                "今日尚未收到新鲜行情，可能休市或行情源不可用",
            )

    def _handle_session_transition(self, now: datetime, in_session: bool) -> None:
        if self._session_date != now.date():
            self._session_date = now.date()
            self._last_fresh_monotonic = None
            self._outage_reported = False
            self._was_in_session = False

        if self._was_in_session and not in_session:
            self._emit_status(FeedStatus.SESSION_CLOSED, "交易时段结束，已刷新最后一分钟K线")
        self._was_in_session = in_session

    def _emit_status(self, status: FeedStatus, message: str) -> None:
        event = FeedStatusEvent(
            status=status,
            message=message,
            occurred_at=datetime.now(SHANGHAI_TZ),
        )
        self.event_engine.put(Event(EVENT_TRADEPILOT_FEED, event))


def _normalize_symbols(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        candidates: Sequence[object] = value.split(",")
    elif isinstance(value, Sequence):
        candidates = value
    else:
        raise ConfigError("Tencent gateway symbols must be a sequence")

    result: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, str):
            raise ConfigError("Tencent gateway symbols must be strings")
        symbol, exchange = parse_vt_symbol(candidate.strip())
        result.append(f"{symbol}.{exchange}")
    if not result:
        raise ConfigError("Tencent gateway requires at least one symbol")
    return tuple(dict.fromkeys(result))
