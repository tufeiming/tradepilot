"""Shared timeframe-aware double-MA lifecycle for live and research strategies."""

from __future__ import annotations

from abc import abstractmethod

import numpy as np
from vnpy.trader.constant import Interval
from vnpy_ctastrategy import ArrayManager, BarData, BarGenerator, CtaTemplate, TickData
from vnpy_ctastrategy.base import EngineType

from tradepilot.core.events import SignalDirection
from tradepilot.strategies.bars import AShareMinuteWindowAggregator


class DoubleMaStrategyBase(CtaTemplate):
    """Calculate strict MA crosses without deciding how a signal is executed."""

    fast_window: int = 10
    slow_window: int = 60
    history_size: int = 100
    bar_window_minutes: int = 15

    fast_ma0: float = 0.0
    fast_ma1: float = 0.0
    slow_ma0: float = 0.0
    slow_ma1: float = 0.0
    history_count: int = 0
    history_ready: bool = False

    parameters = ["fast_window", "slow_window", "history_size", "bar_window_minutes"]
    variables = [
        "fast_ma0",
        "fast_ma1",
        "slow_ma0",
        "slow_ma1",
        "history_count",
        "history_ready",
    ]

    def on_init(self) -> None:
        self.write_log("双均线策略初始化")
        self.bg = BarGenerator(self.on_bar)
        self.window_aggregator = AShareMinuteWindowAggregator(
            self.bar_window_minutes,
            self.on_signal_bar,
        )
        self.am = ArrayManager(size=self.history_size)
        if self.get_engine_type() is EngineType.BACKTESTING:
            history_interval = self._history_interval()
            history_days = self.history_size * 2 if history_interval is Interval.DAILY else 100
            self.load_bar(
                history_days,
                interval=history_interval,
                callback=self.on_signal_bar,
            )
        elif self.bar_window_minutes == 1:
            self.load_bar(10, interval=Interval.MINUTE, callback=self.on_signal_bar)
        else:
            loader = getattr(self.cta_engine, "load_timeframe_bars", None)
            if not callable(loader):
                raise RuntimeError("CTA engine does not support project timeframe history")
            timeframe = f"{self.bar_window_minutes}m"
            for bar in loader(self.vt_symbol, timeframe, self.history_size):
                self.on_signal_bar(bar)
        self.history_ready = self.am.inited

    def on_stop(self) -> None:
        self.write_log("双均线策略停止")
        self.put_event()

    def on_tick(self, tick: TickData) -> None:
        self.bg.update_tick(tick)

    def on_bar(self, bar: BarData) -> None:
        if self.get_engine_type() is EngineType.BACKTESTING or self.bar_window_minutes == 1:
            self.on_signal_bar(bar)
        else:
            self.window_aggregator.update_bar(bar)

    def on_signal_bar(self, bar: BarData) -> None:
        """Update indicators from one completed strategy-timeframe bar."""
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
            self.on_cross(direction, bar)

        self.put_event()

    def flush_bar(self) -> None:
        bg = getattr(self, "bg", None)
        if bg:
            bg.generate()

    def _history_interval(self) -> Interval:
        get_engine_type = getattr(self.cta_engine, "get_engine_type", None)
        interval = getattr(self.cta_engine, "interval", None)
        if (
            callable(get_engine_type)
            and get_engine_type() is EngineType.BACKTESTING
            and isinstance(interval, Interval)
        ):
            return interval
        return Interval.MINUTE

    @abstractmethod
    def on_cross(self, direction: SignalDirection, bar: BarData) -> None:
        """Handle one strict cross in a mode-specific way."""
