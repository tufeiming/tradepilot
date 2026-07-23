from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

import tradepilot.bootstrap as bootstrap
from tradepilot.bootstrap import build_default_catalog
from tradepilot.core.components import (
    ComponentCatalog,
    DataSourcePlugin,
    ExtensionRegistry,
    StrategyPlugin,
    SymbolDiagnostic,
)
from tradepilot.core.config import (
    AppConfig,
    ComponentConfig,
    ConfigError,
    FeishuConfig,
    MonitorConfig,
)
from tradepilot.data_sources.tencent.gateway import TencentGateway
from tradepilot.strategies.double_ma.strategy import DoubleMaSignalStrategy


def make_config(
    *,
    data_source: str = "tencent",
    strategy: str = "double_ma_signal",
) -> AppConfig:
    return AppConfig(
        monitor=MonitorConfig(symbols=("515080.SSE",), minimum_history_bars=100),
        data_source=ComponentConfig(
            name=data_source,
            settings={"poll_interval_seconds": 3, "stale_after_seconds": 30}
            if data_source == "tencent"
            else {},
        ),
        strategy=ComponentConfig(
            name=strategy,
            settings={"fast_window": 10, "slow_window": 20}
            if strategy == "double_ma_signal"
            else {},
        ),
        feishu=FeishuConfig(enabled=False, webhook_url=None, secret=None),
        config_path=Path("config.toml"),
    )


def test_default_catalog_builds_selected_component_settings():
    config = make_config()
    catalog = build_default_catalog(discover=False)
    catalog.validate(config)
    data_source = catalog.data_sources.get("tencent")
    strategy = catalog.strategies.get("double_ma_signal")

    assert data_source.connection_settings(config) == {
        "symbols": ["515080.SSE"],
        "poll_interval_seconds": 3.0,
        "stale_after_seconds": 30.0,
    }
    assert strategy.instance_name("515080.SSE") == "tradepilot_515080_sse"
    assert strategy.engine_settings(config, data_source) == {
        "fast_window": 10,
        "slow_window": 20,
        "history_size": 100,
        "data_source": "Tencent POC",
    }


def test_catalog_rejects_unknown_selected_component():
    catalog = build_default_catalog(discover=False)
    with pytest.raises(ConfigError, match="available values: tencent"):
        catalog.validate(replace(make_config(), data_source=ComponentConfig("missing", {})))


def test_selected_plugins_reject_invalid_or_unknown_settings():
    catalog = build_default_catalog(discover=False)
    invalid_source = replace(
        make_config(),
        data_source=ComponentConfig(
            "tencent",
            {"poll_interval_seconds": 0, "stale_after_seconds": 30},
        ),
    )
    with pytest.raises(ConfigError, match="poll_interval_seconds"):
        catalog.validate(invalid_source)

    invalid_strategy = replace(
        make_config(),
        strategy=ComponentConfig(
            "double_ma_signal",
            {"fast_window": 10, "slow_window": 20, "magic_filter": True},
        ),
    )
    with pytest.raises(ConfigError, match="magic_filter"):
        catalog.validate(invalid_strategy)


class DummyDataSource(DataSourcePlugin):
    name = "dummy"
    display_name = "Dummy Feed"
    gateway_class = TencentGateway

    def __init__(self):
        self.validated = False

    def validate(self, config: AppConfig) -> None:
        self.validated = True

    def connection_settings(self, config: AppConfig) -> dict[str, object]:
        return {"symbols": list(config.monitor.symbols)}

    def diagnose(self, config: AppConfig, now: datetime) -> list[SymbolDiagnostic]:
        return []


class DummyStrategy(StrategyPlugin):
    name = "dummy"
    display_name = "Dummy Strategy"
    strategy_class = DoubleMaSignalStrategy

    def __init__(self):
        self.validated = False

    def validate(self, config: AppConfig) -> None:
        self.validated = True

    def instance_name(self, vt_symbol: str) -> str:
        return f"tradepilot_dummy_{vt_symbol.replace('.', '_').lower()}"

    def engine_settings(
        self,
        config: AppConfig,
        data_source: DataSourcePlugin,
    ) -> dict[str, object]:
        return {"data_source": data_source.display_name}

    def configuration_summary(self, config: AppConfig) -> str:
        return "Dummy"


class OutOfScopeStrategy(DummyStrategy):
    name = "out_of_scope"

    def instance_name(self, vt_symbol: str) -> str:
        return "foreign_strategy"


def test_catalog_accepts_injected_replacement_components():
    data_source = DummyDataSource()
    strategy = DummyStrategy()
    data_sources: ExtensionRegistry[DataSourcePlugin] = ExtensionRegistry("data source")
    strategies: ExtensionRegistry[StrategyPlugin] = ExtensionRegistry("strategy")
    data_sources.register(data_source)
    strategies.register(strategy)
    catalog = ComponentCatalog(data_sources, strategies)

    catalog.validate(make_config(data_source="dummy", strategy="dummy"))

    assert data_source.validated is True
    assert strategy.validated is True


def test_catalog_rejects_strategy_instances_outside_managed_prefix():
    data_sources: ExtensionRegistry[DataSourcePlugin] = ExtensionRegistry("data source")
    strategies: ExtensionRegistry[StrategyPlugin] = ExtensionRegistry("strategy")
    data_sources.register(DummyDataSource())
    strategies.register(OutOfScopeStrategy())
    catalog = ComponentCatalog(data_sources, strategies)

    with pytest.raises(ConfigError, match="must start with"):
        catalog.validate(make_config(data_source="dummy", strategy="out_of_scope"))


class FakeEntryPoint:
    def __init__(self, name, extension):
        self.name = name
        self.extension = extension

    def load(self):
        return self.extension


def test_installed_entry_points_are_discovered(monkeypatch):
    def fake_entry_points(*, group):
        if group == bootstrap.DATA_SOURCE_ENTRY_POINT:
            return [FakeEntryPoint("dummy", DummyDataSource)]
        if group == bootstrap.STRATEGY_ENTRY_POINT:
            return [FakeEntryPoint("dummy", DummyStrategy)]
        return []

    monkeypatch.setattr(bootstrap, "entry_points", fake_entry_points)
    catalog = build_default_catalog()

    assert catalog.data_sources.names() == ("dummy", "tencent")
    assert catalog.strategies.names() == ("double_ma_signal", "dummy")


def test_registry_rejects_duplicate_names():
    registry: ExtensionRegistry[DataSourcePlugin] = ExtensionRegistry("data source")
    registry.register(DummyDataSource())
    with pytest.raises(ValueError, match="duplicate"):
        registry.register(DummyDataSource())
