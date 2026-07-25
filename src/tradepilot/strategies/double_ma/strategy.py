"""Signal-only adaptation of vnpy_ctastrategy's DoubleMaStrategy."""

from __future__ import annotations

from vnpy.event import Event
from vnpy_ctastrategy import (
    BarData,
    OrderData,
    StopOrder,
    TradeData,
)

from tradepilot.core.events import (
    EVENT_TRADEPILOT_SIGNAL,
    SignalDirection,
    SignalEvent,
)
from tradepilot.strategies.double_ma.base import DoubleMaStrategyBase


class DoubleMaSignalStrategy(DoubleMaStrategyBase):
    """One-minute double-MA crosses that publish signals and never place orders."""

    author = "TradePilot"

    data_source: str = "unknown"
    parameters = [*DoubleMaStrategyBase.parameters, "data_source"]

    def on_start(self) -> None:
        self.write_log("双均线信号策略启动（仅通知，禁止下单）")
        self.put_event()

    def on_cross(self, direction: SignalDirection, bar: BarData) -> None:
        signal = SignalEvent(
            strategy_name=self.strategy_name,
            vt_symbol=self.vt_symbol,
            direction=direction,
            bar_time=bar.datetime,
            price=bar.close_price,
            fast_ma=self.fast_ma0,
            slow_ma=self.slow_ma0,
            source=self.data_source,
        )
        self.cta_engine.event_engine.put(Event(EVENT_TRADEPILOT_SIGNAL, signal))

    def on_order(self, order: OrderData) -> None:
        return

    def on_trade(self, trade: TradeData) -> None:
        return

    def on_stop_order(self, stop_order: StopOrder) -> None:
        return
