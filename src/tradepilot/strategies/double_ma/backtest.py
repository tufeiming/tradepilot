"""Backtesting-only long strategy for VeighNa's official CTA backtester."""

from __future__ import annotations

from datetime import date

from vnpy.trader.constant import Direction, Offset
from vnpy_ctastrategy import BarData, OrderData, StopOrder, TradeData
from vnpy_ctastrategy.base import EngineType

from tradepilot.core.events import SignalDirection
from tradepilot.strategies.double_ma.base import DoubleMaStrategyBase


class DoubleMaLongBacktestStrategy(DoubleMaStrategyBase):
    """Turn the baseline signals into simulated long-only orders during backtesting."""

    author = "TradePilot"

    fixed_size: int = 1
    t_plus_one: bool = True
    entry_date: date | None = None
    pending_exit: bool = False

    parameters = [*DoubleMaStrategyBase.parameters, "fixed_size", "t_plus_one"]
    variables = [*DoubleMaStrategyBase.variables, "entry_date", "pending_exit"]

    def on_start(self) -> None:
        if self.get_engine_type() is not EngineType.BACKTESTING:
            raise RuntimeError("DoubleMaLongBacktestStrategy can only run in BACKTESTING mode")
        if self.fixed_size <= 0:
            raise ValueError("fixed_size must be positive")
        self.write_log("双均线多头模拟策略启动（仅回测）")
        self.put_event()

    def on_bar(self, bar: BarData) -> None:
        self.cancel_all()
        if self.pending_exit and self.pos > 0 and self._can_exit(bar):
            self.sell(bar.close_price, abs(self.pos))
        super().on_bar(bar)

    def on_cross(self, direction: SignalDirection, bar: BarData) -> None:
        if self.get_engine_type() is not EngineType.BACKTESTING:
            raise RuntimeError("simulated orders are forbidden outside BACKTESTING mode")

        if direction is SignalDirection.BUY:
            if self.pos == 0:
                self.buy(bar.close_price, self.fixed_size)
            return

        if self.pos <= 0:
            return
        if self._can_exit(bar):
            self.sell(bar.close_price, abs(self.pos))
        else:
            self.pending_exit = True

    def on_trade(self, trade: TradeData) -> None:
        if trade.direction is Direction.LONG and trade.offset is Offset.OPEN:
            self.entry_date = trade.datetime.date() if trade.datetime else None
        elif trade.direction is Direction.SHORT and trade.offset is Offset.CLOSE:
            self.entry_date = None
            self.pending_exit = False
        self.put_event()

    def on_order(self, order: OrderData) -> None:
        return

    def on_stop_order(self, stop_order: StopOrder) -> None:
        return

    def _can_exit(self, bar: BarData) -> bool:
        if not self.t_plus_one or self.entry_date is None:
            return True
        return bar.datetime.date() > self.entry_date
