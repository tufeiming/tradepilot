"""Signal-only adaptation of vnpy_ctastrategy's DoubleMaStrategy."""

from __future__ import annotations

import numpy as np
from vnpy.event import Event
from vnpy.trader.constant import Interval
from vnpy_ctastrategy import (
    ArrayManager,
    BarData,
    BarGenerator,
    CtaTemplate,
    OrderData,
    StopOrder,
    TickData,
    TradeData,
)

from tradepilot.events import (
    EVENT_TRADEPILOT_SIGNAL,
    SignalDirection,
    SignalEvent,
)


class DoubleMaSignalStrategy(CtaTemplate):
    """One-minute double-MA crosses that publish signals and never place orders."""

    author = "TradePilot"

    fast_window: int = 10
    slow_window: int = 20
    history_size: int = 100

    fast_ma0: float = 0.0
    fast_ma1: float = 0.0
    slow_ma0: float = 0.0
    slow_ma1: float = 0.0
    history_count: int = 0
    history_ready: bool = False

    parameters = ["fast_window", "slow_window", "history_size"]
    variables: list[str] = []

    def on_init(self) -> None:
        self.write_log("双均线信号策略初始化")
        self.bg = BarGenerator(self.on_bar)
        self.am = ArrayManager(size=self.history_size)
        self.load_bar(10, interval=Interval.MINUTE)
        self.history_ready = self.am.inited

    def on_start(self) -> None:
        self.write_log("双均线信号策略启动（仅通知，禁止下单）")
        self.put_event()

    def on_stop(self) -> None:
        self.write_log("双均线信号策略停止")
        self.put_event()

    def on_tick(self, tick: TickData) -> None:
        self.bg.update_tick(tick)

    def on_bar(self, bar: BarData) -> None:
        am = self.am
        am.update_bar(bar)
        self.history_count = min(am.count, self.history_size)
        self.history_ready = am.inited
        if not am.inited:
            return

        fast_ma: np.ndarray = am.sma(self.fast_window, array=True)
        slow_ma: np.ndarray = am.sma(self.slow_window, array=True)
        self.fast_ma0 = float(fast_ma[-1])
        self.fast_ma1 = float(fast_ma[-2])
        self.slow_ma0 = float(slow_ma[-1])
        self.slow_ma1 = float(slow_ma[-2])

        cross_over = self.fast_ma0 > self.slow_ma0 and self.fast_ma1 < self.slow_ma1
        cross_below = self.fast_ma0 < self.slow_ma0 and self.fast_ma1 > self.slow_ma1

        if self.trading and (cross_over or cross_below):
            direction = SignalDirection.BUY if cross_over else SignalDirection.SELL
            signal = SignalEvent(
                strategy_name=self.strategy_name,
                vt_symbol=self.vt_symbol,
                direction=direction,
                bar_time=bar.datetime,
                price=bar.close_price,
                fast_ma=self.fast_ma0,
                slow_ma=self.slow_ma0,
            )
            self.cta_engine.event_engine.put(Event(EVENT_TRADEPILOT_SIGNAL, signal))

        self.put_event()

    def flush_bar(self) -> None:
        bg = getattr(self, "bg", None)
        if bg:
            bg.generate()

    def on_order(self, order: OrderData) -> None:
        return

    def on_trade(self, trade: TradeData) -> None:
        return

    def on_stop_order(self, stop_order: StopOrder) -> None:
        return
