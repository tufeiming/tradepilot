"""Pure parsers and HTTP client for Tencent's unofficial quote endpoints."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as daytime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import requests

from tradepilot.core.config import parse_vt_symbol

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
QUOTE_URL = "https://qt.gtimg.cn/q="
HISTORY_URL = "https://web.ifzq.gtimg.cn/appstock/app/day/query"
DAILY_HISTORY_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
DAILY_HISTORY_PAGE_SIZE = 640
INTRADAY_HISTORY_URL = "https://ifzq.gtimg.cn/appstock/app/kline/mkline"
INTRADAY_HISTORY_PAGE_SIZE = 320
USER_AGENT = "Mozilla/5.0 (TradePilot/0.1; Tencent POC feed)"
QUOTE_PATTERN = re.compile(r'v_(?P<symbol>(?:sh|sz)\d{6})="(?P<data>.*)";?')


class TencentError(RuntimeError):
    """Base Tencent POC feed error."""


class TencentNetworkError(TencentError):
    """Tencent could not be reached after retries."""


class TencentDataError(TencentError):
    """Tencent returned malformed or unusable data."""


class HttpResponse(Protocol):
    content: bytes

    def raise_for_status(self) -> None: ...


class HttpGet(Protocol):
    def __call__(
        self,
        url: str,
        *,
        params: dict[str, str] | None,
        headers: dict[str, str],
        timeout: tuple[float, float],
    ) -> HttpResponse: ...


@dataclass(frozen=True, slots=True)
class QuoteSnapshot:
    symbol: str
    exchange: str
    name: str
    timestamp: datetime
    last_price: float
    volume: float
    turnover: float
    open_price: float
    high_price: float
    low_price: float
    pre_close: float
    bid_price_1: float
    bid_volume_1: float
    ask_price_1: float
    ask_volume_1: float

    @property
    def vt_symbol(self) -> str:
        return f"{self.symbol}.{self.exchange}"


@dataclass(frozen=True, slots=True)
class MinuteSnapshot:
    symbol: str
    exchange: str
    timestamp: datetime
    close_price: float
    volume: float
    turnover: float


@dataclass(frozen=True, slots=True)
class DailySnapshot:
    symbol: str
    exchange: str
    timestamp: datetime
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: float
    turnover: float = 0.0


@dataclass(frozen=True, slots=True)
class FifteenMinuteSnapshot:
    """A completed native Tencent 15-minute bar, timestamped at bar start."""

    symbol: str
    exchange: str
    timestamp: datetime
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: float
    turnover: float = 0.0


def to_tencent_symbol(vt_symbol: str) -> str:
    symbol, exchange = parse_vt_symbol(vt_symbol)
    return f"{'sh' if exchange == 'SSE' else 'sz'}{symbol}"


def from_tencent_symbol(value: str) -> tuple[str, str]:
    if not re.fullmatch(r"(?:sh|sz)\d{6}", value):
        raise TencentDataError(f"invalid Tencent symbol: {value!r}")
    return value[2:], "SSE" if value.startswith("sh") else "SZSE"


def is_trading_session(moment: datetime) -> bool:
    local = _as_shanghai(moment)
    if local.weekday() >= 5:
        return False
    value = local.time().replace(tzinfo=None)
    return daytime(9, 30) <= value < daytime(11, 31) or daytime(13, 0) <= value < daytime(15, 1)


def parse_quote_response(raw: bytes | str) -> list[QuoteSnapshot]:
    text = raw.decode("gb18030") if isinstance(raw, bytes) else raw
    snapshots: list[QuoteSnapshot] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        match = QUOTE_PATTERN.fullmatch(line)
        if not match:
            raise TencentDataError(f"malformed Tencent quote line: {line[:80]!r}")
        fields = match.group("data").split("~")
        if len(fields) < 38:
            raise TencentDataError("Tencent quote has too few fields")

        symbol, exchange = from_tencent_symbol(match.group("symbol"))
        if fields[2] and fields[2] != symbol:
            raise TencentDataError("Tencent quote symbol does not match response key")
        timestamp = _parse_quote_timestamp(fields[30])
        last_price = _float(fields[3])
        if last_price <= 0:
            continue

        turnover = 0.0
        summary = fields[35].split("/")
        if len(summary) >= 3:
            turnover = _float(summary[2])

        snapshots.append(
            QuoteSnapshot(
                symbol=symbol,
                exchange=exchange,
                name=fields[1],
                timestamp=timestamp,
                last_price=last_price,
                volume=_float(fields[6]),
                turnover=turnover,
                open_price=_float(fields[5]),
                high_price=_float(fields[33]),
                low_price=_float(fields[34]),
                pre_close=_float(fields[4]),
                bid_price_1=_float(fields[9]),
                bid_volume_1=_float(fields[10]),
                ask_price_1=_float(fields[19]),
                ask_volume_1=_float(fields[20]),
            )
        )
    return snapshots


def parse_history_response(
    raw: bytes | str | dict[str, Any],
    vt_symbol: str,
    *,
    now: datetime,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[MinuteSnapshot]:
    payload = _load_json(raw)
    tx_symbol = to_tencent_symbol(vt_symbol)
    symbol, exchange = parse_vt_symbol(vt_symbol)
    days = payload.get("data", {}).get(tx_symbol, {}).get("data", [])
    if not isinstance(days, list):
        raise TencentDataError("Tencent minute history has an invalid data node")

    current_minute = _as_shanghai(now).replace(second=0, microsecond=0)
    start_local = _as_shanghai(start) if start else None
    end_local = _as_shanghai(end) if end else None
    result: dict[datetime, MinuteSnapshot] = {}

    for day in days:
        date_value = str(day.get("date", ""))
        rows = day.get("data", [])
        if not re.fullmatch(r"\d{8}", date_value) or not isinstance(rows, list):
            continue

        previous_volume = 0.0
        previous_turnover = 0.0
        for row in rows:
            fields = str(row).split()
            if len(fields) < 2 or not re.fullmatch(r"\d{4}", fields[0]):
                continue
            timestamp = datetime.strptime(f"{date_value}{fields[0]}", "%Y%m%d%H%M").replace(
                tzinfo=SHANGHAI_TZ
            )
            cumulative_volume = _float(fields[2]) if len(fields) > 2 else previous_volume
            cumulative_turnover = _float(fields[3]) if len(fields) > 3 else previous_turnover
            volume = max(cumulative_volume - previous_volume, 0.0)
            turnover = max(cumulative_turnover - previous_turnover, 0.0)
            previous_volume = max(cumulative_volume, previous_volume)
            previous_turnover = max(cumulative_turnover, previous_turnover)

            if not is_trading_session(timestamp) or timestamp >= current_minute:
                continue
            if start_local and timestamp < start_local:
                continue
            if end_local and timestamp > end_local:
                continue

            close_price = _float(fields[1])
            if close_price <= 0:
                continue
            result[timestamp] = MinuteSnapshot(
                symbol=symbol,
                exchange=exchange,
                timestamp=timestamp,
                close_price=close_price,
                volume=volume,
                turnover=turnover,
            )

    snapshots = sorted(result.values(), key=lambda item: item.timestamp)
    if not snapshots:
        raise TencentDataError(f"Tencent returned no completed minute data for {vt_symbol}")
    return snapshots


def parse_daily_history_response(
    raw: bytes | str | dict[str, Any],
    vt_symbol: str,
    *,
    now: datetime,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[DailySnapshot]:
    """Parse completed forward-adjusted daily bars returned by Tencent."""
    payload = _load_json(raw)
    tx_symbol = to_tencent_symbol(vt_symbol)
    symbol, exchange = parse_vt_symbol(vt_symbol)
    node = payload.get("data", {}).get(tx_symbol, {})
    rows = node.get("qfqday") or node.get("day") or []
    if not isinstance(rows, list):
        raise TencentDataError("Tencent daily history has an invalid data node")

    now_local = _as_shanghai(now)
    start_date = _local_date(start) if start else None
    end_date = _local_date(end) if end else None
    result: dict[date, DailySnapshot] = {}

    for row in rows:
        if not isinstance(row, list | tuple) or len(row) < 6:
            continue
        try:
            trading_date = datetime.strptime(str(row[0]), "%Y-%m-%d").date()
            open_price = _float(row[1])
            close_price = _float(row[2])
            high_price = _float(row[3])
            low_price = _float(row[4])
            volume = _float(row[5])
        except (TencentDataError, ValueError):
            continue

        if trading_date == now_local.date() and now_local.time() < daytime(15, 1):
            continue
        if start_date and trading_date < start_date:
            continue
        if end_date and trading_date > end_date:
            continue
        if min(open_price, high_price, low_price, close_price) <= 0 or volume < 0:
            continue
        if high_price < max(open_price, low_price, close_price):
            continue
        if low_price > min(open_price, high_price, close_price):
            continue

        timestamp = datetime.combine(trading_date, daytime(), tzinfo=SHANGHAI_TZ)
        result[trading_date] = DailySnapshot(
            symbol=symbol,
            exchange=exchange,
            timestamp=timestamp,
            open_price=open_price,
            high_price=high_price,
            low_price=low_price,
            close_price=close_price,
            volume=volume,
        )

    snapshots = [result[key] for key in sorted(result)]
    if not snapshots:
        raise TencentDataError(f"Tencent returned no completed daily data for {vt_symbol}")
    return snapshots


def parse_15m_history_response(
    raw: bytes | str | dict[str, Any],
    vt_symbol: str,
    *,
    now: datetime,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[FifteenMinuteSnapshot]:
    """Parse Tencent m15 bars and normalize end timestamps to bar starts."""
    payload = _load_json(raw)
    tx_symbol = to_tencent_symbol(vt_symbol)
    symbol, exchange = parse_vt_symbol(vt_symbol)
    rows = payload.get("data", {}).get(tx_symbol, {}).get("m15", [])
    if not isinstance(rows, list):
        raise TencentDataError("Tencent 15m history has an invalid data node")

    now_local = _as_shanghai(now)
    start_local = _as_shanghai(start) if start else None
    end_local = _as_shanghai(end) if end else None
    result: dict[datetime, FifteenMinuteSnapshot] = {}

    for row in rows:
        if not isinstance(row, list | tuple) or len(row) < 6:
            continue
        try:
            bar_end = datetime.strptime(str(row[0]), "%Y%m%d%H%M").replace(tzinfo=SHANGHAI_TZ)
            open_price = _float(row[1])
            close_price = _float(row[2])
            high_price = _float(row[3])
            low_price = _float(row[4])
            volume = _float(row[5])
            turnover = _float(row[7]) if len(row) > 7 else 0.0
        except (TencentDataError, ValueError):
            continue

        if not _is_15m_bar_end(bar_end) or bar_end > now_local:
            continue
        bar_start = bar_end - timedelta(minutes=15)
        if start_local and bar_start < start_local:
            continue
        if end_local and bar_start > end_local:
            continue
        if min(open_price, high_price, low_price, close_price) <= 0 or volume < 0:
            continue
        if high_price < max(open_price, low_price, close_price):
            continue
        if low_price > min(open_price, high_price, close_price):
            continue

        result[bar_start] = FifteenMinuteSnapshot(
            symbol=symbol,
            exchange=exchange,
            timestamp=bar_start,
            open_price=open_price,
            high_price=high_price,
            low_price=low_price,
            close_price=close_price,
            volume=volume,
            turnover=turnover,
        )

    snapshots = [result[key] for key in sorted(result)]
    if not snapshots:
        raise TencentDataError(f"Tencent returned no completed 15m data for {vt_symbol}")
    return snapshots


class TencentClient:
    """Retrying client for quotes and Tencent's supported history endpoints."""

    def __init__(
        self,
        *,
        get: HttpGet | None = None,
        retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._get: HttpGet = get or requests.Session().get
        self.retries = retries
        self._sleep = sleep

    def fetch_quotes(self, vt_symbols: tuple[str, ...] | list[str]) -> list[QuoteSnapshot]:
        if not vt_symbols:
            return []
        query = ",".join(to_tencent_symbol(value) for value in vt_symbols)
        response = self._request(f"{QUOTE_URL}{query}")
        snapshots = parse_quote_response(response.content)
        expected = set(vt_symbols)
        return [item for item in snapshots if item.vt_symbol in expected]

    def fetch_history(
        self,
        vt_symbol: str,
        *,
        now: datetime,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[MinuteSnapshot]:
        response = self._request(
            HISTORY_URL,
            params={"code": to_tencent_symbol(vt_symbol)},
        )
        return parse_history_response(
            response.content,
            vt_symbol,
            now=now,
            start=start,
            end=end,
        )

    def fetch_daily_history(
        self,
        vt_symbol: str,
        *,
        now: datetime,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[DailySnapshot]:
        """Fetch forward-adjusted daily bars, paging backwards to the requested start."""
        now_local = _as_shanghai(now)
        start_date = _local_date(start) if start else date(1990, 1, 1)
        end_date = min(_local_date(end) if end else now_local.date(), now_local.date())
        if start_date > end_date:
            return []

        tx_symbol = to_tencent_symbol(vt_symbol)
        cursor = end_date
        result: dict[date, DailySnapshot] = {}

        for _ in range(100):
            response = self._request(
                DAILY_HISTORY_URL,
                params={
                    "param": (
                        f"{tx_symbol},day,{start_date:%Y-%m-%d},"
                        f"{cursor:%Y-%m-%d},{DAILY_HISTORY_PAGE_SIZE},qfq"
                    )
                },
            )
            try:
                page = parse_daily_history_response(
                    response.content,
                    vt_symbol,
                    now=now_local,
                    start=start,
                    end=end,
                )
            except TencentDataError as exc:
                no_data = str(exc) == (f"Tencent returned no completed daily data for {vt_symbol}")
                if result and no_data:
                    break
                raise

            for snapshot in page:
                result[snapshot.timestamp.date()] = snapshot

            earliest = page[0].timestamp.date()
            if earliest <= start_date:
                break
            next_cursor = earliest - timedelta(days=1)
            if next_cursor >= cursor:
                raise TencentDataError("Tencent daily history pagination did not advance")
            cursor = next_cursor
        else:
            raise TencentDataError("Tencent daily history exceeded pagination limit")

        return [result[key] for key in sorted(result)]

    def fetch_15m_history(
        self,
        vt_symbol: str,
        *,
        now: datetime,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[FifteenMinuteSnapshot]:
        """Fetch Tencent's rolling native 15-minute history backwards by page."""
        now_local = _as_shanghai(now)
        start_local = _as_shanghai(start) if start else now_local - timedelta(days=210)
        end_local = min(_as_shanghai(end) if end else now_local, now_local)
        if start_local > end_local:
            return []

        tx_symbol = to_tencent_symbol(vt_symbol)
        cursor = end_local
        result: dict[datetime, FifteenMinuteSnapshot] = {}

        for _ in range(20):
            response = self._request(
                INTRADAY_HISTORY_URL,
                params={
                    "param": (f"{tx_symbol},m15,{cursor:%Y%m%d%H%M},{INTRADAY_HISTORY_PAGE_SIZE}")
                },
            )
            try:
                page = parse_15m_history_response(
                    response.content,
                    vt_symbol,
                    now=now_local,
                )
            except TencentDataError as exc:
                no_data = str(exc) == f"Tencent returned no completed 15m data for {vt_symbol}"
                if result and no_data:
                    break
                raise

            for snapshot in page:
                if start_local <= snapshot.timestamp <= end_local:
                    result[snapshot.timestamp] = snapshot

            earliest = page[0].timestamp
            if earliest <= start_local or len(page) < INTRADAY_HISTORY_PAGE_SIZE:
                break
            next_cursor = earliest + timedelta(minutes=14)
            if next_cursor >= cursor:
                raise TencentDataError("Tencent 15m history pagination did not advance")
            cursor = next_cursor
        else:
            raise TencentDataError("Tencent 15m history exceeded pagination limit")

        snapshots = [result[key] for key in sorted(result)]
        if not snapshots:
            raise TencentDataError(f"Tencent returned no completed 15m data for {vt_symbol}")
        return snapshots

    def _request(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
    ) -> HttpResponse:
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                response = self._get(
                    url,
                    params=params,
                    headers={"User-Agent": USER_AGENT},
                    timeout=(5.0, 10.0),
                )
                response.raise_for_status()
                return response
            except (requests.RequestException, OSError) as exc:
                last_error = exc
                if attempt + 1 < self.retries:
                    self._sleep(1.2 * (attempt + 1))
        raise TencentNetworkError(
            f"Tencent request failed after {self.retries} attempts: {last_error}"
        )


def _load_json(raw: bytes | str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TencentDataError(f"Tencent returned invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise TencentDataError("Tencent returned a non-object JSON value")
    return value


def _is_15m_bar_end(moment: datetime) -> bool:
    if moment.weekday() >= 5:
        return False
    value = moment.time().replace(tzinfo=None)
    morning = daytime(9, 45) <= value <= daytime(11, 30)
    afternoon = daytime(13, 15) <= value <= daytime(15, 0)
    return (morning or afternoon) and value.minute % 15 == 0


def _parse_quote_timestamp(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=SHANGHAI_TZ)
    except ValueError as exc:
        raise TencentDataError(f"invalid Tencent quote timestamp: {value!r}") from exc


def _as_shanghai(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=SHANGHAI_TZ)
    return value.astimezone(SHANGHAI_TZ)


def _local_date(value: datetime) -> date:
    return _as_shanghai(value).date()


def _float(value: str | int | float) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError) as exc:
        raise TencentDataError(f"invalid Tencent numeric value: {value!r}") from exc
