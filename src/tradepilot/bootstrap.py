"""Application component assembly and optional extension discovery."""

from __future__ import annotations

import logging
from importlib.metadata import entry_points

from tradepilot.core.components import (
    ComponentCatalog,
    DataSourcePlugin,
    ExtensionRegistry,
    StrategyPlugin,
)
from tradepilot.data_sources.tencent.plugin import TencentDataSourcePlugin
from tradepilot.strategies.double_ma.plugin import DoubleMaSignalStrategyPlugin

LOGGER = logging.getLogger(__name__)
DATA_SOURCE_ENTRY_POINT = "tradepilot.data_sources"
STRATEGY_ENTRY_POINT = "tradepilot.strategies"


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
