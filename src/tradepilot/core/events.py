"""TradePilot event payloads shared by the gateway, strategy, and notifier."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

EVENT_TRADEPILOT_SIGNAL = "eTradePilotSignal"
EVENT_TRADEPILOT_FEED = "eTradePilotFeed"


class SignalDirection(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class FeedStatus(StrEnum):
    CONNECTED = "CONNECTED"
    OUTAGE = "OUTAGE"
    RECOVERED = "RECOVERED"
    NO_DATA = "NO_DATA"
    SESSION_CLOSED = "SESSION_CLOSED"


@dataclass(frozen=True, slots=True)
class SignalEvent:
    strategy_name: str
    vt_symbol: str
    direction: SignalDirection
    bar_time: datetime
    price: float
    fast_ma: float
    slow_ma: float
    source: str = "Tencent POC"

    @property
    def dedup_key(self) -> str:
        return "|".join(
            (
                self.strategy_name,
                self.vt_symbol,
                self.direction.value,
                self.bar_time.isoformat(),
            )
        )


@dataclass(frozen=True, slots=True)
class FeedStatusEvent:
    status: FeedStatus
    message: str
    occurred_at: datetime
