"""Adapters that register TradePilot research strategies in CtaBacktester."""

from __future__ import annotations

from datetime import datetime, timedelta

from vnpy.trader.object import BarData
from vnpy_ctabacktester import CtaBacktesterApp
from vnpy_ctabacktester.engine import BacktesterEngine
from vnpy_ctastrategy import CtaTemplate
from vnpy_ctastrategy.backtesting import BacktestingEngine

from tradepilot.core.history import HistoricalBarService
from tradepilot.core.timeframes import BarTimeframe


class TradePilotBacktestingEngine(BacktestingEngine):
    """Official simulation engine with explicit project-timeframe loading."""

    def __init__(self, history_service: HistoricalBarService | None) -> None:
        super().__init__()
        self.history_service = history_service
        self.project_timeframe: str | None = None

    def set_parameters(self, vt_symbol: str, interval, *args, **kwargs) -> None:
        self.project_timeframe = str(interval)
        native_interval = "1m" if interval == BarTimeframe.MINUTE_15.value else interval
        super().set_parameters(vt_symbol, native_interval, *args, **kwargs)

    def load_data(self) -> None:
        if self.project_timeframe != BarTimeframe.MINUTE_15.value:
            super().load_data()
            return
        self.output("开始加载15分钟历史数据")
        self.history_data.clear()
        if not self.end or self.start >= self.end:
            self.output("起始日期必须小于结束日期")
            return
        service = self._require_history_service()
        self.history_data = service.load(
            self.vt_symbol,
            BarTimeframe.MINUTE_15.value,
            self.start,
            self.end,
        )
        self.output(f"15分钟历史数据加载完成，数据量：{len(self.history_data)}")

    def load_bar(
        self,
        vt_symbol: str,
        days: int,
        interval,
        callback,
        use_database: bool,
    ) -> list[BarData]:
        if self.project_timeframe != BarTimeframe.MINUTE_15.value:
            return super().load_bar(vt_symbol, days, interval, callback, use_database)
        service = self._require_history_service()
        init_end = self.start - timedelta(microseconds=1)
        init_start = self.start - timedelta(days=days)
        return service.load(
            vt_symbol,
            BarTimeframe.MINUTE_15.value,
            init_start,
            init_end,
        )

    def _require_history_service(self) -> HistoricalBarService:
        service = self.history_service
        if service is None or BarTimeframe.MINUTE_15.value not in service.supported_timeframes:
            raise RuntimeError("selected data source does not support 15m historical bars")
        return service


class TradePilotBacktesterEngine(BacktesterEngine):
    """Official backtester engine with additional package-based strategy classes."""

    extra_strategy_classes: tuple[type[CtaTemplate], ...] = ()
    history_service: HistoricalBarService | None = None

    def init_engine(self) -> None:
        super().init_engine()
        engine = TradePilotBacktestingEngine(self.history_service)
        engine.output = self.write_log
        self.backtesting_engine = engine

    def load_strategy_class(self) -> None:
        super().load_strategy_class()
        for strategy_class in self.extra_strategy_classes:
            existing = self.classes.get(strategy_class.__name__)
            if existing is not None and existing is not strategy_class:
                raise RuntimeError(
                    f"duplicate backtest strategy class name: {strategy_class.__name__}"
                )
            self.classes[strategy_class.__name__] = strategy_class

    def run_downloading(
        self,
        vt_symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> None:
        if interval != BarTimeframe.MINUTE_15.value:
            super().run_downloading(vt_symbol, interval, start, end)
            return

        self.write_log(f"{vt_symbol}-15m开始下载历史数据")
        try:
            service = self.history_service
            if service is None or interval not in service.supported_timeframes:
                raise RuntimeError("selected data source does not support 15m history")
            bars = service.download(vt_symbol, interval, start, end)
            if not bars:
                self.write_log(f"数据下载失败，无法获取{vt_symbol}的15分钟历史数据")
            else:
                self.write_log(
                    f"{vt_symbol}-15m历史数据下载完成，数据量：{len(bars)}，"
                    f"范围：{bars[0].datetime:%Y-%m-%d %H:%M} 至 "
                    f"{bars[-1].datetime:%Y-%m-%d %H:%M}"
                )
        except Exception as exc:
            self.write_log(f"15分钟数据下载失败：{exc}")
        finally:
            self.thread = None

    def start_optimization(self, class_name, vt_symbol, interval, *args, **kwargs) -> bool:
        if interval == BarTimeframe.MINUTE_15.value:
            self.write_log("15分钟自定义数据暂不支持参数优化，请先使用单次回测")
            return False
        return super().start_optimization(class_name, vt_symbol, interval, *args, **kwargs)


class TradePilotBacktesterApp(CtaBacktesterApp):
    """CtaBacktester app using TradePilot's strategy-aware engine."""

    engine_class = TradePilotBacktesterEngine


def configure_backtest_strategies(
    strategy_classes: tuple[type[CtaTemplate], ...],
) -> None:
    """Configure classes before MainEngine constructs the singleton GUI engine."""
    names = [strategy_class.__name__ for strategy_class in strategy_classes]
    if len(names) != len(set(names)):
        raise ValueError("backtest strategy class names must be unique")
    TradePilotBacktesterEngine.extra_strategy_classes = strategy_classes


def configure_backtest_history(service: HistoricalBarService | None) -> None:
    """Configure project-timeframe storage before constructing the GUI engine."""
    TradePilotBacktesterEngine.history_service = service
