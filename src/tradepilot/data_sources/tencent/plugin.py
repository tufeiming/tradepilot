"""TradePilot component adapter for the Tencent POC data source."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from tradepilot.core.components import DataSourcePlugin, SymbolDiagnostic
from tradepilot.core.config import AppConfig, ConfigError
from tradepilot.data_sources.tencent.client import TencentClient, is_trading_session
from tradepilot.data_sources.tencent.gateway import TencentGateway


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
