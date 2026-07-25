import time
from datetime import datetime, timedelta

from vnpy.event import EventEngine
from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.object import BarData, TickData
from vnpy_ctastrategy.base import EngineType

from tradepilot.data_sources.tencent.client import SHANGHAI_TZ, parse_quote_response
from tradepilot.notifications.feishu import FeishuClient, NotificationService, NotificationStore
from tradepilot.strategies.double_ma.strategy import DoubleMaSignalStrategy


class FakeResponse:
    def raise_for_status(self):
        return

    def json(self):
        return {"code": 0}


class FakeCtaEngine:
    def __init__(self, event_engine, history):
        self.event_engine = event_engine
        self.history = history

    def load_bar(self, vt_symbol, days, interval, callback, use_database):
        return self.history

    def load_timeframe_bars(self, vt_symbol, timeframe, count):
        assert timeframe == "15m"
        return self.history[-count:]

    def get_engine_type(self):
        return EngineType.LIVE

    def write_log(self, msg, strategy=None):
        return

    def put_strategy_event(self, strategy):
        return


def tencent_quote(price: float, moment: datetime, volume: int) -> bytes:
    fields = [""] * 38
    fields[1] = "中证红利ETF招商"
    fields[2] = "515080"
    fields[3] = str(price)
    fields[4] = "3"
    fields[5] = str(price)
    fields[6] = str(volume)
    fields[30] = moment.strftime("%Y%m%d%H%M%S")
    fields[33] = str(price)
    fields[34] = str(price)
    fields[35] = f"{price}/{volume}/{price * volume}"
    return f'v_sh515080="{"~".join(fields)}";'.encode("gb18030")


def historical_bar(index: int, close: float) -> BarData:
    return BarData(
        gateway_name="TENCENT",
        symbol="515080",
        exchange=Exchange.SSE,
        datetime=datetime(2026, 7, 22, 14, 0, tzinfo=SHANGHAI_TZ) + timedelta(minutes=15 * index),
        interval=Interval.MINUTE,
        open_price=close,
        high_price=close,
        low_price=close,
        close_price=close,
    )


def test_fake_tencent_tick_to_ma_cross_to_feishu(tmp_path):
    delivered = []

    def sender(url, **kwargs):
        delivered.append(kwargs["json"]["content"]["text"])
        return FakeResponse()

    event_engine = EventEngine(interval=0.01)
    event_engine.start()
    notifier = NotificationService(
        event_engine,
        NotificationStore(tmp_path / "notifications.json"),
        FeishuClient(
            "https://open.feishu.cn/open-apis/bot/v2/hook/test",
            sender=sender,
        ),
    )
    notifier.start()
    history = [historical_bar(index, close) for index, close in enumerate([7, 6, 5, 4, 3])]
    strategy = DoubleMaSignalStrategy(
        FakeCtaEngine(event_engine, history),
        "tradepilot_515080_sse",
        "515080.SSE",
        {
            "fast_window": 2,
            "slow_window": 3,
            "history_size": 5,
            "bar_window_minutes": 15,
            "data_source": "Tencent POC",
        },
    )

    try:
        strategy.on_init()
        strategy.trading = True
        start = datetime(2026, 7, 23, 9, 30, 5, tzinfo=SHANGHAI_TZ)
        window_prices = [4, 6, 6, 4]
        for minute in range(61):
            price = window_prices[min(minute // 15, 3)]
            moment = start + timedelta(minutes=minute)
            quote = parse_quote_response(tencent_quote(price, moment, (minute + 1) * 100))[0]
            strategy.on_tick(
                TickData(
                    gateway_name="TENCENT",
                    symbol=quote.symbol,
                    exchange=Exchange[quote.exchange],
                    datetime=quote.timestamp,
                    name=quote.name,
                    volume=quote.volume,
                    turnover=quote.turnover,
                    last_price=quote.last_price,
                    high_price=quote.high_price,
                    low_price=quote.low_price,
                )
            )

        deadline = time.monotonic() + 2
        while len(delivered) < 1 and time.monotonic() < deadline:
            time.sleep(0.01)

        assert len(delivered) == 1
        assert sum("[BUY] 515080.SSE" in message for message in delivered) == 1
        assert all("[SELL]" not in message for message in delivered)
    finally:
        notifier.stop()
        event_engine.stop()
