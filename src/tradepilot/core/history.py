"""Provider-neutral historical bars and collision-free project storage."""

from __future__ import annotations

import sqlite3
import threading
from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.object import BarData

from tradepilot.core.timeframes import BarTimeframe


class HistoricalBarService(ABC):
    """Extension point used by live warm-up and graphical backtesting."""

    @property
    @abstractmethod
    def supported_timeframes(self) -> tuple[str, ...]:
        """Return project timeframe identifiers supported by the provider."""

    @abstractmethod
    def download(
        self,
        vt_symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[BarData]:
        """Fetch remote bars, persist them, and return the fetched range."""

    @abstractmethod
    def load(
        self,
        vt_symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[BarData]:
        """Load already persisted bars without network access."""

    @abstractmethod
    def ensure_recent(
        self,
        vt_symbol: str,
        timeframe: str,
        count: int,
        now: datetime,
    ) -> list[BarData]:
        """Refresh and return the newest completed bars for live warm-up."""


class HistoricalBarStore:
    """Small SQLite store keyed by an explicit timeframe string.

    VeighNa 4.4 has no 15-minute Interval value. Keeping the timeframe in this
    project-owned table prevents native 15-minute bars from colliding with 1m
    rows in VeighNa's standard database.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._initialize()

    def save(self, timeframe: str, bars: Sequence[BarData]) -> int:
        if not bars:
            return 0
        rows = [
            (
                bar.symbol,
                bar.exchange.value,
                timeframe,
                bar.datetime.isoformat(timespec="seconds"),
                bar.gateway_name,
                bar.open_price,
                bar.high_price,
                bar.low_price,
                bar.close_price,
                bar.volume,
                bar.turnover,
                bar.open_interest,
            )
            for bar in bars
        ]
        with self._lock, self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO historical_bars (
                    symbol, exchange, timeframe, datetime, gateway_name,
                    open_price, high_price, low_price, close_price,
                    volume, turnover, open_interest
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, exchange, timeframe, datetime) DO UPDATE SET
                    gateway_name=excluded.gateway_name,
                    open_price=excluded.open_price,
                    high_price=excluded.high_price,
                    low_price=excluded.low_price,
                    close_price=excluded.close_price,
                    volume=excluded.volume,
                    turnover=excluded.turnover,
                    open_interest=excluded.open_interest
                """,
                rows,
            )
        return len(rows)

    def load(
        self,
        vt_symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[BarData]:
        symbol, exchange_name = vt_symbol.split(".")
        start_value = start.isoformat(timespec="seconds")
        end_value = end.isoformat(timespec="seconds")
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT datetime, gateway_name, open_price, high_price, low_price,
                       close_price, volume, turnover, open_interest
                FROM historical_bars
                WHERE symbol=? AND exchange=? AND timeframe=?
                  AND datetime>=? AND datetime<=?
                ORDER BY datetime
                """,
                (symbol, Exchange[exchange_name].value, timeframe, start_value, end_value),
            ).fetchall()

        interval = Interval.DAILY if timeframe == BarTimeframe.DAILY.value else Interval.MINUTE
        exchange = Exchange[exchange_name]
        return [
            BarData(
                gateway_name=row[1],
                symbol=symbol,
                exchange=exchange,
                datetime=datetime.fromisoformat(row[0]),
                interval=interval,
                open_price=row[2],
                high_price=row[3],
                low_price=row[4],
                close_price=row[5],
                volume=row[6],
                turnover=row[7],
                open_interest=row[8],
            )
            for row in rows
        ]

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS historical_bars (
                    symbol TEXT NOT NULL,
                    exchange TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    datetime TEXT NOT NULL,
                    gateway_name TEXT NOT NULL,
                    open_price REAL NOT NULL,
                    high_price REAL NOT NULL,
                    low_price REAL NOT NULL,
                    close_price REAL NOT NULL,
                    volume REAL NOT NULL,
                    turnover REAL NOT NULL,
                    open_interest REAL NOT NULL,
                    PRIMARY KEY (symbol, exchange, timeframe, datetime)
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)
