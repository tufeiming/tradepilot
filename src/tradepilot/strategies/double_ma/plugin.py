"""TradePilot component adapter for the baseline double-MA strategy."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from tradepilot.core.components import DataSourcePlugin, StrategyPlugin
from tradepilot.core.config import AppConfig, ConfigError
from tradepilot.strategies.double_ma.backtest import DoubleMaLongBacktestStrategy
from tradepilot.strategies.double_ma.strategy import DoubleMaSignalStrategy


@dataclass(frozen=True, slots=True)
class DoubleMaSettings:
    fast_window: int
    slow_window: int
    bar_window_minutes: int


class DoubleMaSignalStrategyPlugin(StrategyPlugin):
    """Built-in configurable-timeframe MA crossover notification strategy."""

    name = "double_ma_signal"
    display_name = "15分钟双均线信号"
    strategy_class = DoubleMaSignalStrategy
    backtest_strategy_class = DoubleMaLongBacktestStrategy

    def validate(self, config: AppConfig) -> None:
        settings = self._settings(config)
        minimum = config.monitor.minimum_history_bars
        if minimum < max(100, settings.slow_window + 2):
            raise ConfigError(
                "monitor.minimum_history_bars must be at least max(100, strategy.slow_window + 2)"
            )

    def instance_name(self, vt_symbol: str) -> str:
        symbol, exchange = vt_symbol.split(".")
        return f"{self.managed_prefix}{symbol}_{exchange.lower()}"

    def engine_settings(
        self,
        config: AppConfig,
        data_source: DataSourcePlugin,
    ) -> dict[str, object]:
        settings = self._settings(config)
        return {
            "fast_window": settings.fast_window,
            "slow_window": settings.slow_window,
            "history_size": config.monitor.minimum_history_bars,
            "bar_window_minutes": settings.bar_window_minutes,
            "data_source": data_source.display_name,
        }

    def configuration_summary(self, config: AppConfig) -> str:
        settings = self._settings(config)
        return (
            f"MA{settings.fast_window}/MA{settings.slow_window}，{settings.bar_window_minutes}分钟"
        )

    def get_backtest_strategy_class(self, config: AppConfig) -> type[DoubleMaLongBacktestStrategy]:
        settings = self._settings(config)
        strategy_class = DoubleMaLongBacktestStrategy
        strategy_class.fast_window = settings.fast_window
        strategy_class.slow_window = settings.slow_window
        strategy_class.history_size = config.monitor.minimum_history_bars
        strategy_class.bar_window_minutes = settings.bar_window_minutes
        return strategy_class

    def _settings(self, config: AppConfig) -> DoubleMaSettings:
        values = config.strategy.settings
        _reject_unknown(
            values,
            {"fast_window", "slow_window", "bar_window_minutes"},
            "strategy",
        )
        fast_window = _integer(values.get("fast_window", 10), "strategy.fast_window")
        slow_window = _integer(values.get("slow_window", 60), "strategy.slow_window")
        bar_window_minutes = _integer(
            values.get("bar_window_minutes", 15),
            "strategy.bar_window_minutes",
        )
        if fast_window < 2 or fast_window >= slow_window:
            raise ConfigError("strategy windows must satisfy 2 <= fast_window < slow_window")
        if bar_window_minutes != 15:
            raise ConfigError("strategy.bar_window_minutes currently must be 15")
        return DoubleMaSettings(fast_window, slow_window, bar_window_minutes)


def _reject_unknown(
    values: Mapping[str, object],
    allowed: set[str],
    section: str,
) -> None:
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ConfigError(f"unknown {section} setting(s): {', '.join(unknown)}")


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{name} must be an integer")
    return value
