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
    poll_interval_seconds: float = 3.0
    stale_after_seconds: float = 30.0
    minimum_history_bars: int = 100


@dataclass(frozen=True, slots=True)
class StrategyConfig:
    fast_window: int = 10
    slow_window: int = 20


@dataclass(frozen=True, slots=True)
class FeishuConfig:
    enabled: bool
    webhook_url: str | None
    secret: str | None


@dataclass(frozen=True, slots=True)
class AppConfig:
    monitor: MonitorConfig
    strategy: StrategyConfig
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
    """Load and validate the application's complete configuration."""
    config_path = Path(path).expanduser().resolve()
    if not config_path.is_file():
        raise ConfigError(f"configuration file not found: {config_path}")

    try:
        with config_path.open("rb") as file:
            raw = tomllib.load(file)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {config_path}: {exc}") from exc

    monitor_raw = raw.get("monitor", {})
    strategy_raw = raw.get("strategy", {})
    feishu_raw = raw.get("feishu", {})

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

    poll_interval = _as_float(
        monitor_raw.get("poll_interval_seconds", 3.0),
        "monitor.poll_interval_seconds",
    )
    stale_after = _as_float(
        monitor_raw.get("stale_after_seconds", 30.0),
        "monitor.stale_after_seconds",
    )
    minimum_history = _as_int(
        monitor_raw.get("minimum_history_bars", 100),
        "monitor.minimum_history_bars",
    )
    fast_window = _as_int(strategy_raw.get("fast_window", 10), "strategy.fast_window")
    slow_window = _as_int(strategy_raw.get("slow_window", 20), "strategy.slow_window")

    if not 1 <= poll_interval <= 60:
        raise ConfigError("monitor.poll_interval_seconds must be between 1 and 60")
    if stale_after <= poll_interval:
        raise ConfigError("monitor.stale_after_seconds must be greater than poll_interval_seconds")
    if fast_window < 2 or fast_window >= slow_window:
        raise ConfigError("strategy windows must satisfy 2 <= fast_window < slow_window")
    if minimum_history < max(100, slow_window + 2):
        raise ConfigError("monitor.minimum_history_bars must be at least max(100, slow_window + 2)")

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
            poll_interval_seconds=poll_interval,
            stale_after_seconds=stale_after,
            minimum_history_bars=minimum_history,
        ),
        strategy=StrategyConfig(fast_window=fast_window, slow_window=slow_window),
        feishu=FeishuConfig(enabled=enabled, webhook_url=webhook_url, secret=secret),
        config_path=config_path,
    )


def _as_float(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(f"{name} must be a number")
    return float(value)


def _as_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{name} must be an integer")
    return value
