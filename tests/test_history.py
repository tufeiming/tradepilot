from datetime import datetime

from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.object import BarData

from tradepilot.core.history import HistoricalBarStore
from tradepilot.data_sources.tencent.client import SHANGHAI_TZ


def make_bar(close: float) -> BarData:
    return BarData(
        gateway_name="TEST",
        symbol="515080",
        exchange=Exchange.SSE,
        datetime=datetime(2026, 7, 24, 9, 30, tzinfo=SHANGHAI_TZ),
        interval=Interval.MINUTE,
        open_price=close,
        high_price=close,
        low_price=close,
        close_price=close,
    )


def test_explicit_timeframe_key_prevents_15m_and_1m_collision(tmp_path):
    store = HistoricalBarStore(tmp_path / "history.db")
    store.save("1m", [make_bar(1.0)])
    store.save("15m", [make_bar(1.5)])
    start = datetime(2026, 7, 24, 9, 0, tzinfo=SHANGHAI_TZ)
    end = datetime(2026, 7, 24, 10, 0, tzinfo=SHANGHAI_TZ)

    minute = store.load("515080.SSE", "1m", start, end)
    fifteen = store.load("515080.SSE", "15m", start, end)

    assert len(minute) == len(fifteen) == 1
    assert minute[0].close_price == 1.0
    assert fifteen[0].close_price == 1.5


def test_saving_same_15m_bar_updates_in_place(tmp_path):
    store = HistoricalBarStore(tmp_path / "history.db")
    store.save("15m", [make_bar(1.0)])
    store.save("15m", [make_bar(1.6)])
    start = datetime(2026, 7, 24, 9, 0, tzinfo=SHANGHAI_TZ)
    end = datetime(2026, 7, 24, 10, 0, tzinfo=SHANGHAI_TZ)

    bars = store.load("515080.SSE", "15m", start, end)

    assert len(bars) == 1
    assert bars[0].close_price == 1.6
