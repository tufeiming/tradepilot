import inspect
from datetime import datetime, timedelta

from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.object import BarData, TickData
from vnpy_ctastrategy.base import EngineType

from tradepilot.core.events import EVENT_TRADEPILOT_SIGNAL, SignalDirection
from tradepilot.data_sources.tencent.client import SHANGHAI_TZ
from tradepilot.strategies.bars import AShareMinuteWindowAggregator
from tradepilot.strategies.double_ma.strategy import DoubleMaSignalStrategy


class CaptureEventEngine:
    def __init__(self):
        self.events = []

    def put(self, event):
        self.events.append(event)


class FakeCtaEngine:
    def __init__(self, history):
        self.history = history
        self.event_engine = CaptureEventEngine()
        self.timeframe_request = None

    def load_bar(self, vt_symbol, days, interval, callback, use_database):
        return self.history

    def write_log(self, msg, strategy=None):
        return

    def put_strategy_event(self, strategy):
        return

    def get_engine_type(self):
        return EngineType.LIVE

    def load_timeframe_bars(self, vt_symbol, timeframe, count):
        self.timeframe_request = (vt_symbol, timeframe, count)
        return self.history[-count:]


def make_bar(index, close):
    return BarData(
        gateway_name="TEST",
        symbol="515080",
        exchange=Exchange.SSE,
        datetime=datetime(2026, 7, 22, 14, 0, tzinfo=SHANGHAI_TZ) + timedelta(minutes=index),
        interval=Interval.MINUTE,
        open_price=close,
        high_price=close,
        low_price=close,
        close_price=close,
    )


def make_tick(minute, price):
    return TickData(
        gateway_name="TEST",
        symbol="515080",
        exchange=Exchange.SSE,
        datetime=datetime(2026, 7, 23, 10, minute, 5, tzinfo=SHANGHAI_TZ),
        last_price=price,
        volume=minute * 100,
        turnover=minute * 100 * price,
        high_price=price,
        low_price=price,
    )


def make_session_bar(timestamp, close):
    return BarData(
        gateway_name="TEST",
        symbol="515080",
        exchange=Exchange.SSE,
        datetime=timestamp,
        interval=Interval.MINUTE,
        open_price=close,
        high_price=close,
        low_price=close,
        close_price=close,
        volume=1,
        turnover=close,
    )


def build_strategy():
    history = [make_bar(index, close) for index, close in enumerate([7, 6, 5, 4, 3])]
    engine = FakeCtaEngine(history)
    strategy = DoubleMaSignalStrategy(
        engine,
        "tradepilot_515080_sse",
        "515080.SSE",
        {
            "fast_window": 2,
            "slow_window": 3,
            "history_size": 5,
            "bar_window_minutes": 1,
            "data_source": "Test Feed",
        },
    )
    strategy.on_init()
    return strategy, engine


def build_15m_strategy():
    history = [
        make_session_bar(
            datetime(2026, 7, 22, 9, 30, tzinfo=SHANGHAI_TZ) + timedelta(minutes=15 * index),
            close,
        )
        for index, close in enumerate([7, 6, 5, 4, 3])
    ]
    engine = FakeCtaEngine(history)
    strategy = DoubleMaSignalStrategy(
        engine,
        "tradepilot_515080_sse",
        "515080.SSE",
        {
            "fast_window": 2,
            "slow_window": 3,
            "history_size": 5,
            "bar_window_minutes": 15,
            "data_source": "Test Feed",
        },
    )
    strategy.on_init()
    return strategy, engine


def test_initialization_warms_up_without_emitting_signal():
    strategy, engine = build_strategy()
    assert strategy.history_ready is True
    assert strategy.history_count == 5
    assert engine.event_engine.events == []


def test_tick_to_bar_pipeline_emits_only_golden_cross():
    strategy, engine = build_strategy()
    strategy.trading = True
    for minute, price in [(0, 4), (1, 6), (2, 6), (3, 4), (4, 2)]:
        strategy.on_tick(make_tick(minute, price))

    signals = [
        event.data for event in engine.event_engine.events if event.type == EVENT_TRADEPILOT_SIGNAL
    ]
    assert [signal.direction for signal in signals] == [SignalDirection.BUY]
    assert {signal.source for signal in signals} == {"Test Feed"}


def test_stop_does_not_finalize_an_incomplete_minute():
    strategy, engine = build_strategy()
    strategy.trading = True
    strategy.on_tick(make_tick(0, 6))

    strategy.on_stop()

    assert engine.event_engine.events == []


def test_15m_live_pipeline_emits_only_golden_cross_on_complete_aligned_windows():
    strategy, engine = build_15m_strategy()
    assert engine.timeframe_request == ("515080.SSE", "15m", 5)
    strategy.trading = True
    start = datetime(2026, 7, 23, 9, 30, tzinfo=SHANGHAI_TZ)
    for window, close in enumerate([4, 6, 6, 4]):
        for minute in range(15):
            strategy.on_bar(
                make_session_bar(start + timedelta(minutes=window * 15 + minute), close)
            )

    signals = [
        event.data for event in engine.event_engine.events if event.type == EVENT_TRADEPILOT_SIGNAL
    ]
    assert [signal.direction for signal in signals] == [SignalDirection.BUY]
    assert [signal.bar_time.strftime("%H:%M") for signal in signals] == ["09:45"]


def test_incomplete_15m_window_does_not_reach_strategy():
    strategy, engine = build_15m_strategy()
    strategy.trading = True
    start = datetime(2026, 7, 23, 9, 30, tzinfo=SHANGHAI_TZ)
    for minute in range(14):
        strategy.on_bar(make_session_bar(start + timedelta(minutes=minute), 10))

    assert engine.event_engine.events == []


def test_15m_aggregator_respects_lunch_and_close_boundaries():
    completed = []
    aggregator = AShareMinuteWindowAggregator(15, completed.append)
    morning = datetime(2026, 7, 23, 11, 15, tzinfo=SHANGHAI_TZ)
    afternoon = datetime(2026, 7, 23, 13, 0, tzinfo=SHANGHAI_TZ)
    for minute in range(15):
        aggregator.update_bar(make_session_bar(morning + timedelta(minutes=minute), 1))
    aggregator.update_bar(make_session_bar(morning + timedelta(minutes=15), 99))
    for minute in range(15):
        aggregator.update_bar(make_session_bar(afternoon + timedelta(minutes=minute), 2))
    aggregator.update_bar(make_session_bar(datetime(2026, 7, 23, 15, 0, tzinfo=SHANGHAI_TZ), 99))

    assert [bar.datetime.strftime("%H:%M") for bar in completed] == ["11:15", "13:00"]
    assert [bar.close_price for bar in completed] == [1, 2]


def test_strategy_source_contains_no_order_calls():
    source = inspect.getsource(DoubleMaSignalStrategy)
    for forbidden in ("self.buy(", "self.sell(", "self.short(", "self.cover(", "send_order("):
        assert forbidden not in source
