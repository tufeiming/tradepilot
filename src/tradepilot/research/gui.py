"""Launch VeighNa's official CTA backtesting GUI for TradePilot research."""

from __future__ import annotations

from vnpy.event import EventEngine
from vnpy.trader.engine import MainEngine
from vnpy.trader.gateway import BaseGateway
from vnpy.trader.object import CancelRequest, OrderRequest
from vnpy.trader.ui import MainWindow, create_qapp
from vnpy_ctabacktester import APP_NAME
from vnpy_ctabacktester.ui import BacktesterManager

from tradepilot.core.components import ComponentCatalog
from tradepilot.core.config import AppConfig
from tradepilot.core.timeframes import BarTimeframe
from tradepilot.research.backtester import (
    TradePilotBacktesterApp,
    configure_backtest_history,
    configure_backtest_strategies,
)


class ResearchTradingDisabledError(RuntimeError):
    """Raised when any order reaches the historical research process."""


class TradePilotBacktesterManager(BacktesterManager):
    """Official backtesting widget with TradePilot's explicit 15m choice."""

    default_symbol = "515080.SSE"

    def init_ui(self) -> None:
        super().init_ui()
        timeframe = BarTimeframe.MINUTE_15.value
        if self.interval_combo.findText(timeframe) < 0:
            self.interval_combo.insertItem(1, timeframe)
        self.symbol_line.setText(self.default_symbol)
        self.interval_combo.setCurrentText(timeframe)


def make_read_only_gateway(gateway_class: type[BaseGateway]) -> type[BaseGateway]:
    """Wrap a market-data gateway so the research GUI can never place real orders."""

    class ReadOnlyResearchGateway(gateway_class):
        default_name = f"{gateway_class.default_name}_RESEARCH"

        def send_order(self, req: OrderRequest) -> str:
            raise ResearchTradingDisabledError(
                f"research GUI rejected real order for {req.vt_symbol}"
            )

        def cancel_order(self, req: CancelRequest) -> None:
            raise ResearchTradingDisabledError("research GUI has no real orders to cancel")

    ReadOnlyResearchGateway.__name__ = f"ReadOnly{gateway_class.__name__}"
    return ReadOnlyResearchGateway


def run_backtest_gui(config: AppConfig, catalog: ComponentCatalog) -> int:
    """Open the official GUI with the selected data source and research strategies."""
    data_source = catalog.data_sources.get(config.data_source.name)
    strategy_plugin = catalog.strategies.get(config.strategy.name)
    research_class = strategy_plugin.get_backtest_strategy_class(config)
    if research_class is None:
        raise ValueError(f"strategy {strategy_plugin.name!r} does not support backtesting")
    configure_backtest_strategies((research_class,))
    history_service = data_source.create_history_service(config, config.runtime_dir)
    configure_backtest_history(history_service)
    research_gateway_class = make_read_only_gateway(data_source.gateway_class)

    qapp = create_qapp()
    event_engine = EventEngine()
    main_engine = MainEngine(event_engine)
    try:
        main_engine.add_gateway(research_gateway_class)
        main_engine.add_app(TradePilotBacktesterApp)
        main_engine.connect(
            data_source.connection_settings(config),
            research_gateway_class.default_name,
        )

        main_window = MainWindow(main_engine, event_engine)
        main_window.setWindowTitle("TradePilot 策略研究 - VeighNa CTA回测")
        main_window.showMaximized()
        TradePilotBacktesterManager.default_symbol = config.monitor.symbols[0]
        main_window.open_widget(TradePilotBacktesterManager, APP_NAME)
        return qapp.exec()
    finally:
        main_engine.close()
