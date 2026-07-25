"""A-share session-aware bar aggregation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from datetime import time as daytime

from vnpy.trader.constant import Interval
from vnpy.trader.object import BarData


class AShareMinuteWindowAggregator:
    """Aggregate only complete aligned minute windows across split A-share sessions."""

    def __init__(self, window: int, callback: Callable[[BarData], None]) -> None:
        if window < 1 or 120 % window:
            raise ValueError("A-share minute window must be a positive divisor of 120")
        self.window = window
        self.callback = callback
        self._window_start: datetime | None = None
        self._bars: dict[datetime, BarData] = {}

    def update_bar(self, bar: BarData) -> None:
        timestamp = bar.datetime.replace(second=0, microsecond=0)
        window_start = self._aligned_window_start(timestamp)
        if window_start is None:
            return
        if window_start != self._window_start:
            self._window_start = window_start
            self._bars = {}
        self._bars[timestamp] = bar

        expected_end = window_start + timedelta(minutes=self.window - 1)
        if timestamp != expected_end:
            return
        expected = [window_start + timedelta(minutes=index) for index in range(self.window)]
        if any(value not in self._bars for value in expected):
            self._bars = {}
            return

        source = [self._bars[value] for value in expected]
        first = source[0]
        result = BarData(
            gateway_name=first.gateway_name,
            symbol=first.symbol,
            exchange=first.exchange,
            datetime=window_start,
            interval=Interval.MINUTE,
            open_price=first.open_price,
            high_price=max(item.high_price for item in source),
            low_price=min(item.low_price for item in source),
            close_price=source[-1].close_price,
            volume=sum(item.volume for item in source),
            turnover=sum(item.turnover for item in source),
            open_interest=source[-1].open_interest,
        )
        self._bars = {}
        self.callback(result)

    def _aligned_window_start(self, timestamp: datetime) -> datetime | None:
        value = timestamp.time().replace(tzinfo=None)
        if daytime(9, 30) <= value < daytime(11, 30):
            session_start = timestamp.replace(hour=9, minute=30, second=0, microsecond=0)
        elif daytime(13, 0) <= value < daytime(15, 0):
            session_start = timestamp.replace(hour=13, minute=0, second=0, microsecond=0)
        else:
            return None
        elapsed = int((timestamp - session_start).total_seconds() // 60)
        return session_start + timedelta(minutes=(elapsed // self.window) * self.window)
