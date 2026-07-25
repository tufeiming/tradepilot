"""Tencent native 15-minute history behind TradePilot's provider interface."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.object import BarData

from tradepilot.core.history import HistoricalBarService, HistoricalBarStore
from tradepilot.core.timeframes import BarTimeframe
from tradepilot.data_sources.tencent.client import SHANGHAI_TZ, TencentClient, TencentError


class TencentHistoricalBarService(HistoricalBarService):
    """Download and cache Tencent's rolling six-month m15 dataset."""

    gateway_name = "TENCENT_15M"

    def __init__(self, runtime_dir: Path, client: TencentClient | None = None) -> None:
        self.client = client or TencentClient()
        self.store = HistoricalBarStore(runtime_dir / "tradepilot_history.db")

    @property
    def supported_timeframes(self) -> tuple[str, ...]:
        return (BarTimeframe.MINUTE_15.value,)

    def download(
        self,
        vt_symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[BarData]:
        self._require_15m(timeframe)
        start_local = _as_shanghai(start)
        end_local = _as_shanghai(end)
        snapshots = self.client.fetch_15m_history(
            vt_symbol,
            now=datetime.now(SHANGHAI_TZ),
            start=start_local,
            end=end_local,
        )
        exchange = Exchange[vt_symbol.split(".")[1]]
        bars = [
            BarData(
                gateway_name=self.gateway_name,
                symbol=item.symbol,
                exchange=exchange,
                datetime=item.timestamp,
                # VeighNa has no 15m enum. The project store's timeframe key is authoritative.
                interval=Interval.MINUTE,
                open_price=item.open_price,
                high_price=item.high_price,
                low_price=item.low_price,
                close_price=item.close_price,
                volume=item.volume,
                turnover=item.turnover,
            )
            for item in snapshots
        ]
        self.store.save(timeframe, bars)
        return bars

    def load(
        self,
        vt_symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[BarData]:
        self._require_15m(timeframe)
        return self.store.load(
            vt_symbol,
            timeframe,
            _as_shanghai(start),
            _as_shanghai(end),
        )

    def ensure_recent(
        self,
        vt_symbol: str,
        timeframe: str,
        count: int,
        now: datetime,
    ) -> list[BarData]:
        self._require_15m(timeframe)
        now_local = _as_shanghai(now)
        start = now_local - timedelta(days=210)
        try:
            self.download(vt_symbol, timeframe, start, now_local)
        except TencentError:
            cached = self.load(vt_symbol, timeframe, start, now_local)
            if len(cached) < count:
                raise
        return self.load(vt_symbol, timeframe, start, now_local)[-count:]

    def _require_15m(self, timeframe: str) -> None:
        if timeframe not in self.supported_timeframes:
            raise ValueError(f"Tencent historical service does not support {timeframe!r}")


def _as_shanghai(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=SHANGHAI_TZ)
    return value.astimezone(SHANGHAI_TZ)
