from datetime import datetime

import pytest
from vnpy.event import EventEngine
from vnpy.trader.constant import Direction, Exchange, Interval, OrderType
from vnpy.trader.object import HistoryRequest, OrderRequest

from tradepilot.core.events import FeedStatus
from tradepilot.data_sources.tencent.client import (
    SHANGHAI_TZ,
    DailySnapshot,
    MinuteSnapshot,
    QuoteSnapshot,
)
from tradepilot.data_sources.tencent.gateway import (
    TencentGateway,
    TradingDisabledError,
    _normalize_symbols,
)


def test_normalize_multiple_symbols():
    assert _normalize_symbols("515080.SSE, 159915.SZSE") == (
        "515080.SSE",
        "159915.SZSE",
    )


def test_gateway_publishes_contract_with_quote_name():
    gateway = TencentGateway(EventEngine(), "TENCENT")
    gateway.client.fetch_quotes = lambda symbols: [
        QuoteSnapshot(
            symbol="600011",
            exchange="SSE",
            name="华能国际",
            timestamp=datetime(2026, 7, 24, 15, 0, tzinfo=SHANGHAI_TZ),
            last_price=7.1,
            volume=1,
            turnover=7.1,
            open_price=7.0,
            high_price=7.2,
            low_price=6.9,
            pre_close=7.0,
            bid_price_1=7.09,
            bid_volume_1=100,
            ask_price_1=7.1,
            ask_volume_1=100,
        )
    ]
    contracts = []
    statuses = []
    gateway.on_contract = contracts.append
    gateway._emit_status = lambda status, message: statuses.append((status, message))

    try:
        gateway.connect(
            {
                "symbols": ["600011.SSE"],
                "poll_interval_seconds": 3,
                "stale_after_seconds": 30,
            }
        )
    finally:
        gateway.close()

    assert len(contracts) == 1
    assert contracts[0].name == "华能国际"
    assert statuses == [
        (
            FeedStatus.CONNECTED,
            "腾讯POC行情已连接：1 个标的\n监听标的：\n- 华能国际（600011.SSE）",
        )
    ]


def test_gateway_rejects_every_order():
    gateway = TencentGateway(EventEngine(), "TENCENT")
    request = OrderRequest(
        symbol="515080",
        exchange=Exchange.SSE,
        direction=Direction.LONG,
        type=OrderType.LIMIT,
        volume=100,
        price=1.55,
    )
    with pytest.raises(TradingDisabledError, match="read-only"):
        gateway.send_order(request)


def test_gateway_converts_history_to_vnpy_bars():
    gateway = TencentGateway(EventEngine(), "TENCENT")
    gateway.client.fetch_history = lambda *args, **kwargs: [
        MinuteSnapshot(
            symbol="515080",
            exchange="SSE",
            timestamp=datetime(2026, 7, 23, 9, 30, tzinfo=SHANGHAI_TZ),
            close_price=1.55,
            volume=100,
            turnover=155,
        )
    ]
    request = HistoryRequest(
        symbol="515080",
        exchange=Exchange.SSE,
        start=datetime(2026, 7, 22, tzinfo=SHANGHAI_TZ),
        interval=Interval.MINUTE,
    )
    bars = gateway.query_history(request)
    assert len(bars) == 1
    assert bars[0].vt_symbol == "515080.SSE"
    assert bars[0].close_price == pytest.approx(1.55)


def test_gateway_converts_daily_history_to_vnpy_bars():
    gateway = TencentGateway(EventEngine(), "TENCENT")
    gateway.client.fetch_daily_history = lambda *args, **kwargs: [
        DailySnapshot(
            symbol="515080",
            exchange="SSE",
            timestamp=datetime(2026, 7, 22, tzinfo=SHANGHAI_TZ),
            open_price=1.50,
            high_price=1.56,
            low_price=1.49,
            close_price=1.55,
            volume=1000,
        )
    ]
    request = HistoryRequest(
        symbol="515080",
        exchange=Exchange.SSE,
        start=datetime(2020, 1, 1, tzinfo=SHANGHAI_TZ),
        interval=Interval.DAILY,
    )

    bars = gateway.query_history(request)

    assert len(bars) == 1
    assert bars[0].interval is Interval.DAILY
    assert bars[0].open_price == pytest.approx(1.50)
    assert bars[0].high_price == pytest.approx(1.56)
    assert bars[0].low_price == pytest.approx(1.49)
    assert bars[0].close_price == pytest.approx(1.55)
