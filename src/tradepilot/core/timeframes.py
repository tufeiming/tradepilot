"""Project-level bar timeframes not represented by VeighNa's Interval enum."""

from __future__ import annotations

from enum import StrEnum


class BarTimeframe(StrEnum):
    """Stable identifiers used at TradePilot's data-source boundary."""

    MINUTE_1 = "1m"
    MINUTE_15 = "15m"
    DAILY = "d"


DEFAULT_SIGNAL_TIMEFRAME = BarTimeframe.MINUTE_15
