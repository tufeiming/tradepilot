"""Built-in components and discovery of optional external extensions."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from importlib.metadata import entry_points

from tradepilot.components import (
    ComponentCatalog,
    DataSourcePlugin,
    ExtensionRegistry,
    StrategyPlugin,
    SymbolDiagnostic,
)
from tradepilot.config import AppConfig, ConfigError
from tradepilot.gateway import TencentGateway
from tradepilot.strategy import DoubleMaSignalStrategy
from tradepilot.tencent import TencentClient, is_trading_session

LOGGER = logging.getLogger(__name__)
DATA_SOURCE_ENTRY_POINT = "tradepilot.data_sources"
STRATEGY_ENTRY_POINT = "tradepilot.strategies"


@dataclass(frozen=True, slots=True)
class TencentSettings:
    poll_interval_seconds: float
    stale_after_seconds: float


class TencentDataSourcePlugin(DataSourcePlugin):
    """Built-in POC provider backed by Tencent's unofficial public endpoints."""

    name = "tencent"
    display_name = "Tencent POC"
    gateway_class = TencentGateway

    def validate(self, config: AppConfig) -> None:
        self._settings(config)

    def connection_settings(self, config: AppConfig) -> dict[str, object]:
        settings = self._settings(config)
        return {
            "symbols": list(config.monitor.symbols),
            "poll_interval_seconds": settings.poll_interval_seconds,
            "stale_after_seconds": settings.stale_after_seconds,
        }

    def diagnose(self, config: AppConfig, now: datetime) -> list[SymbolDiagnostic]:
        settings = self._settings(config)
        client = TencentClient()
        quote_error: str | None = None
        try:
            quotes = {item.vt_symbol: item for item in client.fetch_quotes(config.monitor.symbols)}
        except Exception as exc:
            quotes = {}
            quote_error = str(exc)

        results: list[SymbolDiagnostic] = []
        for vt_symbol in config.monitor.symbols:
            quote = quotes.get(vt_symbol)
            current_quote_error = quote_error
            price = None
            quote_time = None
            stale = False
            if quote is None and current_quote_error is None:
                current_quote_error = "no quote returned"
            elif quote is not None:
                price = quote.last_price
                quote_time = quote.timestamp
                age = (now - quote.timestamp).total_seconds()
                stale = is_trading_session(now) and (
                    quote.timestamp.date() != now.date() or age > settings.stale_after_seconds
                )

            history_bars = None
            history_error = None
            try:
                history_bars = len(client.fetch_history(vt_symbol, now=now))
            except Exception as exc:
                history_error = str(exc)

            results.append(
                SymbolDiagnostic(
                    vt_symbol=vt_symbol,
                    price=price,
                    quote_time=quote_time,
                    quote_stale=stale,
                    history_bars=history_bars,
                    quote_error=current_quote_error,
                    history_error=history_error,
                )
            )
        return results

    def _settings(self, config: AppConfig) -> TencentSettings:
        values = config.data_source.settings
        _reject_unknown(
            values,
            {"poll_interval_seconds", "stale_after_seconds"},
            "data_source",
        )
        poll_interval = _number(
            values.get("poll_interval_seconds", 3.0),
            "data_source.poll_interval_seconds",
        )
        stale_after = _number(
            values.get("stale_after_seconds", 30.0),
            "data_source.stale_after_seconds",
        )
        if not 1 <= poll_interval <= 60:
            raise ConfigError("data_source.poll_interval_seconds must be between 1 and 60")
        if stale_after <= poll_interval:
            raise ConfigError(
                "data_source.stale_after_seconds must be greater than poll_interval_seconds"
            )
        return TencentSettings(poll_interval, stale_after)


@dataclass(frozen=True, slots=True)
class DoubleMaSettings:
    fast_window: int
    slow_window: int


class DoubleMaSignalStrategyPlugin(StrategyPlugin):
    """Built-in one-minute MA crossover notification strategy."""

    name = "double_ma_signal"
    display_name = "一分钟双均线信号"
    strategy_class = DoubleMaSignalStrategy

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
            "data_source": data_source.display_name,
        }

    def configuration_summary(self, config: AppConfig) -> str:
        settings = self._settings(config)
        return f"MA{settings.fast_window}/MA{settings.slow_window}，1分钟"

    def _settings(self, config: AppConfig) -> DoubleMaSettings:
        values = config.strategy.settings
        _reject_unknown(values, {"fast_window", "slow_window"}, "strategy")
        fast_window = _integer(values.get("fast_window", 10), "strategy.fast_window")
        slow_window = _integer(values.get("slow_window", 20), "strategy.slow_window")
        if fast_window < 2 or fast_window >= slow_window:
            raise ConfigError("strategy windows must satisfy 2 <= fast_window < slow_window")
        return DoubleMaSettings(fast_window, slow_window)


def build_default_catalog(*, discover: bool = True) -> ComponentCatalog:
    """Create built-ins and optionally discover extensions from installed packages."""
    data_sources: ExtensionRegistry[DataSourcePlugin] = ExtensionRegistry("data source")
    data_sources.register(TencentDataSourcePlugin())
    strategies: ExtensionRegistry[StrategyPlugin] = ExtensionRegistry("strategy")
    strategies.register(DoubleMaSignalStrategyPlugin())
    if discover:
        _discover(data_sources, DATA_SOURCE_ENTRY_POINT, DataSourcePlugin)
        _discover(strategies, STRATEGY_ENTRY_POINT, StrategyPlugin)
    return ComponentCatalog(data_sources=data_sources, strategies=strategies)


def _discover[ExtensionT: DataSourcePlugin | StrategyPlugin](
    registry: ExtensionRegistry[ExtensionT],
    group: str,
    expected_type: type[ExtensionT],
) -> None:
    for entry_point in entry_points(group=group):
        try:
            loaded = entry_point.load()
            extension = loaded() if isinstance(loaded, type) else loaded
            if not isinstance(extension, expected_type):
                raise TypeError(f"expected {expected_type.__name__}")
            registry.register(extension)
        except Exception as exc:
            LOGGER.warning("Ignoring invalid extension %s: %s", entry_point.name, exc)


def _reject_unknown(
    values: Mapping[str, object],
    allowed: set[str],
    section: str,
) -> None:
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ConfigError(f"unknown {section} setting(s): {', '.join(unknown)}")


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(f"{name} must be a number")
    return float(value)


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{name} must be an integer")
    return value
