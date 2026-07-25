"""TradePilot extensions to VeighNa's live CTA engine."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from vnpy.trader.object import BarData
from vnpy_ctastrategy import CtaEngine, CtaStrategyApp

from tradepilot.core.history import HistoricalBarService

MARKET_TZ = ZoneInfo("Asia/Shanghai")


class TradePilotCtaEngine(CtaEngine):
    """CTA engine with provider-neutral warm-up for project timeframes."""

    history_service: HistoricalBarService | None = None

    def load_timeframe_bars(
        self,
        vt_symbol: str,
        timeframe: str,
        count: int,
    ) -> list[BarData]:
        service = self.history_service
        if service is None:
            raise RuntimeError("selected data source does not provide timeframe history")
        if timeframe not in service.supported_timeframes:
            raise RuntimeError(
                f"selected data source does not provide {timeframe} history; "
                f"supported: {', '.join(service.supported_timeframes) or 'none'}"
            )
        return service.ensure_recent(
            vt_symbol,
            timeframe,
            count,
            datetime.now(MARKET_TZ),
        )


class TradePilotCtaStrategyApp(CtaStrategyApp):
    """VeighNa CTA app using TradePilot's warm-up engine."""

    engine_class = TradePilotCtaEngine


def configure_live_history_service(service: HistoricalBarService | None) -> None:
    """Configure the service before MainEngine constructs the CTA engine."""
    TradePilotCtaEngine.history_service = service
