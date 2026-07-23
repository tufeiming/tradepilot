from datetime import datetime

import pytest
from vnpy.event import EventEngine
from vnpy.trader.constant import Direction, Exchange, Interval, OrderType
from vnpy.trader.object import HistoryRequest, OrderRequest

from tradepilot.gateway import (
    TencentGateway,
    TradingDisabledError,
    _normalize_symbols,
)
from tradepilot.tencent import SHANGHAI_TZ, MinuteSnapshot


def test_normalize_multiple_symbols():
    assert _normalize_symbols("515080.SSE, 159915.SZSE") == (
        "515080.SSE",
        "159915.SZSE",
    )


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
