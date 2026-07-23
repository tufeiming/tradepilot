import inspect
from datetime import datetime, timedelta

from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.object import BarData, TickData

from tradepilot.events import EVENT_TRADEPILOT_SIGNAL, SignalDirection
from tradepilot.strategy import DoubleMaSignalStrategy
from tradepilot.tencent import SHANGHAI_TZ


class CaptureEventEngine:
    def __init__(self):
        self.events = []

    def put(self, event):
        self.events.append(event)


class FakeCtaEngine:
    def __init__(self, history):
        self.history = history
        self.event_engine = CaptureEventEngine()

    def load_bar(self, vt_symbol, days, interval, callback, use_database):
        return self.history

    def write_log(self, msg, strategy=None):
        return

    def put_strategy_event(self, strategy):
        return


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


def test_tick_to_bar_pipeline_emits_one_buy_and_one_sell():
    strategy, engine = build_strategy()
    strategy.trading = True
    for minute, price in [(0, 4), (1, 6), (2, 6), (3, 4), (4, 2)]:
        strategy.on_tick(make_tick(minute, price))

    signals = [
        event.data for event in engine.event_engine.events if event.type == EVENT_TRADEPILOT_SIGNAL
    ]
    assert [signal.direction for signal in signals] == [
        SignalDirection.BUY,
        SignalDirection.SELL,
    ]
    assert {signal.source for signal in signals} == {"Test Feed"}


def test_stop_does_not_finalize_an_incomplete_minute():
    strategy, engine = build_strategy()
    strategy.trading = True
    strategy.on_tick(make_tick(0, 6))

    strategy.on_stop()

    assert engine.event_engine.events == []


def test_strategy_source_contains_no_order_calls():
    source = inspect.getsource(DoubleMaSignalStrategy)
    for forbidden in ("self.buy(", "self.sell(", "self.short(", "self.cover(", "send_order("):
        assert forbidden not in source
