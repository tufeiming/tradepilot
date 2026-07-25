"""Adapters that register TradePilot research strategies in CtaBacktester."""

from __future__ import annotations

from vnpy_ctabacktester import CtaBacktesterApp
from vnpy_ctabacktester.engine import BacktesterEngine
from vnpy_ctastrategy import CtaTemplate


class TradePilotBacktesterEngine(BacktesterEngine):
    """Official backtester engine with additional package-based strategy classes."""

    extra_strategy_classes: tuple[type[CtaTemplate], ...] = ()

    def load_strategy_class(self) -> None:
        super().load_strategy_class()
        for strategy_class in self.extra_strategy_classes:
            existing = self.classes.get(strategy_class.__name__)
            if existing is not None and existing is not strategy_class:
                raise RuntimeError(
                    f"duplicate backtest strategy class name: {strategy_class.__name__}"
                )
            self.classes[strategy_class.__name__] = strategy_class


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
