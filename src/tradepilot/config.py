"""Application configuration loaded from TOML and environment variables."""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

VT_SYMBOL_PATTERN = re.compile(r"^(?P<symbol>\d{6})\.(?P<exchange>SSE|SZSE)$")
SSE_PREFIXES = ("5", "6")
SZSE_PREFIXES = ("0", "1", "3")


class ConfigError(ValueError):
    """Raised when the local configuration is unsafe or invalid."""


@dataclass(frozen=True, slots=True)
class MonitorConfig:
    symbols: tuple[str, ...]
    minimum_history_bars: int = 100


@dataclass(frozen=True, slots=True)
class ComponentConfig:
    """Named extension and its implementation-specific settings."""

    name: str
    settings: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class FeishuConfig:
    enabled: bool
    webhook_url: str | None
    secret: str | None


@dataclass(frozen=True, slots=True)
class AppConfig:
    monitor: MonitorConfig
    data_source: ComponentConfig
    strategy: ComponentConfig
    feishu: FeishuConfig
    config_path: Path

    @property
    def runtime_dir(self) -> Path:
        return self.config_path.parent / ".vntrader"


def parse_vt_symbol(vt_symbol: str) -> tuple[str, str]:
    """Validate and split a VeighNa A-share symbol."""
    match = VT_SYMBOL_PATTERN.fullmatch(vt_symbol.strip().upper())
    if not match:
        raise ConfigError(f"invalid symbol {vt_symbol!r}; expected a value such as 515080.SSE")

    symbol = match.group("symbol")
    exchange = match.group("exchange")
    expected_prefixes = SSE_PREFIXES if exchange == "SSE" else SZSE_PREFIXES
    if not symbol.startswith(expected_prefixes):
        raise ConfigError(f"symbol {symbol} does not belong to exchange {exchange}")
    return symbol, exchange


def load_config(
    path: str | Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> AppConfig:
    """Load core configuration; selected plugins validate their own settings."""
    config_path = Path(path).expanduser().resolve()
    if not config_path.is_file():
        raise ConfigError(f"configuration file not found: {config_path}")

    try:
        with config_path.open("rb") as file:
            raw = tomllib.load(file)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {config_path}: {exc}") from exc

    monitor_raw = _table(raw, "monitor")
    data_source_raw = _table(raw, "data_source")
    strategy_raw = _table(raw, "strategy")
    feishu_raw = _table(raw, "feishu")
    _reject_unknown(monitor_raw, {"symbols", "minimum_history_bars"}, "monitor")

    symbols_value = monitor_raw.get("symbols", [])
    if not isinstance(symbols_value, list) or not symbols_value:
        raise ConfigError("monitor.symbols must be a non-empty list")

    symbols: list[str] = []
    for value in symbols_value:
        if not isinstance(value, str):
            raise ConfigError("every monitor.symbols value must be a string")
        symbol, exchange = parse_vt_symbol(value)
        symbols.append(f"{symbol}.{exchange}")
    if len(symbols) != len(set(symbols)):
        raise ConfigError("monitor.symbols must not contain duplicates")

    minimum_history = _as_int(
        monitor_raw.get("minimum_history_bars", 100),
        "monitor.minimum_history_bars",
    )
    if minimum_history < 1:
        raise ConfigError("monitor.minimum_history_bars must be positive")

    data_source = _component(data_source_raw, "data_source", "tencent")
    strategy = _component(strategy_raw, "strategy", "double_ma_signal")

    env = os.environ if environ is None else environ
    enabled = feishu_raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError("feishu.enabled must be true or false")
    configured_webhook = feishu_raw.get("webhook_url")
    if configured_webhook is not None and not isinstance(configured_webhook, str):
        raise ConfigError("feishu.webhook_url must be a string")
    webhook_url = (env.get("TRADEPILOT_FEISHU_WEBHOOK_URL") or configured_webhook or "").strip()
    webhook_url = webhook_url or None
    secret = env.get("TRADEPILOT_FEISHU_SECRET") or None
    if enabled and not webhook_url:
        raise ConfigError(
            "feishu.webhook_url or TRADEPILOT_FEISHU_WEBHOOK_URL is required "
            "when feishu.enabled is true"
        )
    if webhook_url and not webhook_url.startswith("https://open.feishu.cn/"):
        raise ConfigError("TRADEPILOT_FEISHU_WEBHOOK_URL must use open.feishu.cn HTTPS")

    return AppConfig(
        monitor=MonitorConfig(
            symbols=tuple(symbols),
            minimum_history_bars=minimum_history,
        ),
        data_source=data_source,
        strategy=strategy,
        feishu=FeishuConfig(enabled=enabled, webhook_url=webhook_url, secret=secret),
        config_path=config_path,
    )


def _table(raw: Mapping[str, object], name: str) -> Mapping[str, object]:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a TOML table")
    return value


def _component(raw: Mapping[str, object], section: str, default: str) -> ComponentConfig:
    value = raw.get("name", default)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{section}.name must be a non-empty string")
    name = value.strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise ConfigError(f"{section}.name must use lowercase letters, numbers, and underscores")
    return ComponentConfig(
        name=name,
        settings={key: item for key, item in raw.items() if key != "name"},
    )


def _reject_unknown(
    values: Mapping[str, object],
    allowed: set[str],
    section: str,
) -> None:
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ConfigError(f"unknown {section} setting(s): {', '.join(unknown)}")


def _as_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{name} must be an integer")
    return value
