"""Extension contracts and built-in TradePilot component registrations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from vnpy.trader.gateway import BaseGateway
from vnpy_ctastrategy import CtaTemplate

from tradepilot.core.config import AppConfig, ConfigError

MANAGED_STRATEGY_PREFIX = "tradepilot_"


@dataclass(frozen=True, slots=True)
class SymbolDiagnostic:
    """Provider-neutral market-data health result for one configured symbol."""

    vt_symbol: str
    price: float | None = None
    quote_time: datetime | None = None
    quote_stale: bool = False
    history_bars: int | None = None
    quote_error: str | None = None
    history_error: str | None = None


class DataSourcePlugin(ABC):
    """Object-oriented adapter between TradePilot and one VeighNa gateway."""

    name: str
    display_name: str
    gateway_class: type[BaseGateway]

    @abstractmethod
    def validate(self, config: AppConfig) -> None:
        """Validate provider-specific settings before any engine is created."""

    @abstractmethod
    def connection_settings(self, config: AppConfig) -> dict[str, object]:
        """Build settings passed to the selected VeighNa gateway."""

    @abstractmethod
    def diagnose(self, config: AppConfig, now: datetime) -> list[SymbolDiagnostic]:
        """Perform read-only quote and history checks for doctor."""


class StrategyPlugin(ABC):
    """Factory and configuration boundary for one CTA signal strategy."""

    name: str
    display_name: str
    strategy_class: type[CtaTemplate]
    managed_prefix: str = MANAGED_STRATEGY_PREFIX

    @abstractmethod
    def validate(self, config: AppConfig) -> None:
        """Validate strategy-specific settings and warm-up requirements."""

    @abstractmethod
    def instance_name(self, vt_symbol: str) -> str:
        """Return the stable CTA instance name for one symbol."""

    @abstractmethod
    def engine_settings(
        self,
        config: AppConfig,
        data_source: DataSourcePlugin,
    ) -> dict[str, object]:
        """Build settings passed to CtaEngine.add_strategy/edit_strategy."""

    @abstractmethod
    def configuration_summary(self, config: AppConfig) -> str:
        """Return a concise human-readable strategy description."""

    def readiness(self, strategy: CtaTemplate) -> tuple[bool, int]:
        """Read the common warm-up state; custom plugins may override it."""
        return (
            bool(getattr(strategy, "history_ready", False)),
            int(getattr(strategy, "history_count", 0)),
        )

    def on_session_closed(self, strategy: CtaTemplate) -> None:
        """Finalize the last completed bar when supported by the strategy."""
        flush = getattr(strategy, "flush_bar", None)
        if callable(flush):
            flush()


class ExtensionRegistry[ExtensionT: DataSourcePlugin | StrategyPlugin]:
    """Explicit registry that rejects duplicate and unknown component names."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._extensions: dict[str, ExtensionT] = {}

    def register(self, extension: ExtensionT) -> None:
        name = extension.name.strip().lower()
        if name in self._extensions:
            raise ValueError(f"duplicate {self.kind} extension: {name}")
        self._extensions[name] = extension

    def get(self, name: str) -> ExtensionT:
        normalized = name.strip().lower()
        try:
            return self._extensions[normalized]
        except KeyError as exc:
            available = ", ".join(sorted(self._extensions)) or "none"
            raise ConfigError(
                f"unknown {self.kind} {name!r}; available values: {available}"
            ) from exc

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._extensions))


@dataclass(slots=True)
class ComponentCatalog:
    """Injectable collection of all data-source and strategy extensions."""

    data_sources: ExtensionRegistry[DataSourcePlugin]
    strategies: ExtensionRegistry[StrategyPlugin]

    def validate(self, config: AppConfig) -> None:
        data_source = self.data_sources.get(config.data_source.name)
        strategy = self.strategies.get(config.strategy.name)
        if not issubclass(data_source.gateway_class, BaseGateway):
            raise ConfigError(f"{data_source.name} gateway_class must inherit BaseGateway")
        if not issubclass(strategy.strategy_class, CtaTemplate):
            raise ConfigError(f"{strategy.name} strategy_class must inherit CtaTemplate")
        data_source.validate(config)
        strategy.validate(config)

        instance_names = [strategy.instance_name(symbol) for symbol in config.monitor.symbols]
        if any(not name.startswith(strategy.managed_prefix) for name in instance_names):
            raise ConfigError(
                f"{strategy.name} instance names must start with {strategy.managed_prefix!r}"
            )
        if len(instance_names) != len(set(instance_names)):
            raise ConfigError(f"{strategy.name} generated duplicate instance names")
