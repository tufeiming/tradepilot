import os
from datetime import datetime, timedelta
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pandas as pd
import pytest
from vnpy.trader.constant import Direction, Exchange, Interval, Offset
from vnpy.trader.object import BarData
from vnpy.trader.ui import create_qapp
from vnpy_ctabacktester.engine import BacktesterEngine
from vnpy_ctabacktester.ui.widget import BacktesterChart
from vnpy_ctastrategy.backtesting import BacktestingEngine
from vnpy_ctastrategy.base import EngineType

from tradepilot.data_sources.tencent.client import SHANGHAI_TZ
from tradepilot.data_sources.tencent.gateway import TencentGateway
from tradepilot.research.backtester import (
    TradePilotBacktesterEngine,
    configure_backtest_strategies,
)
from tradepilot.research.gui import ResearchTradingDisabledError, make_read_only_gateway
from tradepilot.strategies.double_ma.backtest import DoubleMaLongBacktestStrategy


def make_bar(timestamp: datetime, close: float) -> BarData:
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
    )


class InMemoryBacktestingEngine(BacktestingEngine):
    def __init__(self, warmup: list[BarData]) -> None:
        super().__init__()
        self.warmup = warmup
        self.requested_history: tuple[int, Interval] | None = None

    def load_bar(self, vt_symbol, days, interval, callback, use_database):
        self.requested_history = (days, interval)
        return self.warmup


def run_cross_backtest(*, t_plus_one: bool) -> list:
    warmup_start = datetime(2026, 7, 21, 14, 0, tzinfo=SHANGHAI_TZ)
    warmup = [
        make_bar(warmup_start + timedelta(minutes=index), close)
        for index, close in enumerate([7, 6, 5, 4, 3])
    ]
    replay_values = [
        (datetime(2026, 7, 22, 10, 0, tzinfo=SHANGHAI_TZ), 4),
        (datetime(2026, 7, 22, 10, 1, tzinfo=SHANGHAI_TZ), 6),
        (datetime(2026, 7, 22, 10, 2, tzinfo=SHANGHAI_TZ), 6),
        (datetime(2026, 7, 22, 10, 3, tzinfo=SHANGHAI_TZ), 4),
        (datetime(2026, 7, 22, 10, 4, tzinfo=SHANGHAI_TZ), 4),
        (datetime(2026, 7, 23, 9, 30, tzinfo=SHANGHAI_TZ), 4),
        (datetime(2026, 7, 23, 9, 31, tzinfo=SHANGHAI_TZ), 4),
    ]
    replay = [make_bar(timestamp, close) for timestamp, close in replay_values]

    engine = InMemoryBacktestingEngine(warmup)
    engine.set_parameters(
        vt_symbol="515080.SSE",
        interval=Interval.MINUTE,
        start=replay[0].datetime,
        end=replay[-1].datetime,
        rate=0,
        slippage=0,
        size=100,
        pricetick=0.001,
        capital=100_000,
    )
    engine.add_strategy(
        DoubleMaLongBacktestStrategy,
        {
            "fast_window": 2,
            "slow_window": 3,
            "history_size": 5,
            "fixed_size": 1,
            "t_plus_one": t_plus_one,
        },
    )
    engine.history_data = replay
    engine.run_backtesting()
    return engine.get_all_trades()


def test_official_engine_simulates_long_only_cross_trades_with_t_plus_one():
    trades = run_cross_backtest(t_plus_one=True)

    assert [(trade.direction, trade.offset) for trade in trades] == [
        (Direction.LONG, Offset.OPEN),
        (Direction.SHORT, Offset.CLOSE),
    ]
    assert trades[0].datetime.date() == datetime(2026, 7, 22).date()
    assert trades[1].datetime.date() == datetime(2026, 7, 23).date()


def test_t_plus_one_can_be_disabled_for_research_comparison():
    trades = run_cross_backtest(t_plus_one=False)

    assert len(trades) == 2
    assert trades[1].datetime.date() == datetime(2026, 7, 22).date()


def test_backtest_warmup_uses_selected_daily_interval():
    engine = InMemoryBacktestingEngine([])
    engine.set_parameters(
        vt_symbol="515080.SSE",
        interval=Interval.DAILY,
        start=datetime(2020, 1, 1, tzinfo=SHANGHAI_TZ),
        end=datetime(2026, 7, 24, tzinfo=SHANGHAI_TZ),
        rate=0,
        slippage=0,
        size=100,
        pricetick=0.001,
        capital=100_000,
    )
    engine.add_strategy(DoubleMaLongBacktestStrategy, {})

    engine.strategy.on_init()

    assert engine.requested_history == (200, Interval.DAILY)


class LiveEngineStub:
    def get_engine_type(self):
        return EngineType.LIVE


def test_backtest_strategy_refuses_to_start_on_live_engine():
    strategy = DoubleMaLongBacktestStrategy(
        LiveEngineStub(),
        "unsafe",
        "515080.SSE",
        {},
    )

    with pytest.raises(RuntimeError, match="BACKTESTING"):
        strategy.on_start()


def test_tradepilot_backtester_registers_package_strategy(monkeypatch):
    monkeypatch.setattr(BacktesterEngine, "load_strategy_class", lambda self: None)
    configure_backtest_strategies((DoubleMaLongBacktestStrategy,))
    engine = object.__new__(TradePilotBacktesterEngine)
    engine.classes = {}

    engine.load_strategy_class()

    assert engine.classes == {"DoubleMaLongBacktestStrategy": DoubleMaLongBacktestStrategy}


def test_research_strategy_names_must_be_unique():
    with pytest.raises(ValueError, match="unique"):
        configure_backtest_strategies((DoubleMaLongBacktestStrategy, DoubleMaLongBacktestStrategy))


def test_research_gateway_rejects_real_orders_before_provider():
    gateway_class = make_read_only_gateway(TencentGateway)
    gateway = gateway_class(None, gateway_class.default_name)

    with pytest.raises(ResearchTradingDisabledError, match="rejected real order"):
        gateway.send_order(SimpleNamespace(vt_symbol="515080.SSE"))


def test_official_backtester_chart_accepts_date_indexed_pandas_series():
    qapp = create_qapp()
    chart = BacktesterChart()
    result = pd.DataFrame(
        {
            "balance": [100_000.0, 100_100.0],
            "drawdown": [0.0, 0.0],
            "net_pnl": [0.0, 100.0],
        },
        index=[datetime(2026, 7, 23).date(), datetime(2026, 7, 24).date()],
    )

    chart.set_data(result)

    assert chart.balance_curve.getData()[1].tolist() == [100_000.0, 100_100.0]
    chart.close()
    qapp.processEvents()
