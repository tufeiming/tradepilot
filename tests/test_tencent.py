import json
from datetime import datetime, timedelta

import pytest

import tradepilot.data_sources.tencent.client as tencent_client
from tradepilot.data_sources.tencent.client import (
    SHANGHAI_TZ,
    TencentClient,
    TencentDataError,
    from_tencent_symbol,
    is_trading_session,
    parse_15m_history_response,
    parse_daily_history_response,
    parse_history_response,
    parse_quote_response,
    to_tencent_symbol,
)


def m15_payload(rows):
    return {"code": 0, "data": {"sh515080": {"m15": rows}}}


def m15_row(timestamp, close="1.1"):
    return [timestamp, "1.0", close, "1.2", "0.9", "10", {}, "20"]


def quote_line(*, price="1.555", timestamp="20260723101447") -> str:
    fields = [""] * 38
    fields[0] = "1"
    fields[1] = "中证红利ETF招商"
    fields[2] = "515080"
    fields[3] = price
    fields[4] = "1.540"
    fields[5] = "1.532"
    fields[6] = "2491028"
    fields[9] = "1.554"
    fields[10] = "6540"
    fields[19] = "1.555"
    fields[20] = "1411"
    fields[30] = timestamp
    fields[33] = "1.556"
    fields[34] = "1.531"
    fields[35] = "1.555/2491028/385768163"
    return f'v_sh515080="{"~".join(fields)}";'


def test_symbol_mapping():
    assert to_tencent_symbol("515080.SSE") == "sh515080"
    assert to_tencent_symbol("159915.SZSE") == "sz159915"
    assert from_tencent_symbol("sh600519") == ("600519", "SSE")


def test_parse_15m_normalizes_bar_end_and_deduplicates():
    bars = parse_15m_history_response(
        m15_payload(
            [
                m15_row("202607240945", "1.05"),
                m15_row("202607241000", "1.10"),
                m15_row("202607241000", "1.11"),
                m15_row("202607241015", "1.12"),
            ]
        ),
        "515080.SSE",
        now=datetime(2026, 7, 24, 10, 7, tzinfo=SHANGHAI_TZ),
    )

    assert [bar.timestamp.strftime("%H:%M") for bar in bars] == ["09:30", "09:45"]
    assert bars[-1].close_price == pytest.approx(1.11)


def test_parse_15m_has_16_aligned_bars_per_normal_trading_day():
    morning = [datetime(2026, 7, 24, 9, 45) + timedelta(minutes=15 * index) for index in range(8)]
    afternoon = [
        datetime(2026, 7, 24, 13, 15) + timedelta(minutes=15 * index) for index in range(8)
    ]
    rows = [m15_row(value.strftime("%Y%m%d%H%M")) for value in [*morning, *afternoon]]

    bars = parse_15m_history_response(
        m15_payload(rows),
        "515080.SSE",
        now=datetime(2026, 7, 24, 15, 1, tzinfo=SHANGHAI_TZ),
    )

    assert len(bars) == 16
    assert bars[0].timestamp.strftime("%H:%M") == "09:30"
    assert bars[7].timestamp.strftime("%H:%M") == "11:15"
    assert bars[8].timestamp.strftime("%H:%M") == "13:00"
    assert bars[-1].timestamp.strftime("%H:%M") == "14:45"


def test_15m_history_pages_backwards(monkeypatch):
    requested_params = []
    monkeypatch.setattr(tencent_client, "INTRADAY_HISTORY_PAGE_SIZE", 2)

    class Response:
        def __init__(self, rows):
            self.content = json.dumps(m15_payload(rows)).encode()

        def raise_for_status(self):
            return None

    def fake_get(url, *, params, headers, timeout):
        requested_params.append(params["param"])
        if len(requested_params) == 1:
            return Response([m15_row("202607241445"), m15_row("202607241500")])
        return Response([m15_row("202607241415"), m15_row("202607241430")])

    bars = TencentClient(get=fake_get, retries=1).fetch_15m_history(
        "515080.SSE",
        now=datetime(2026, 7, 24, 16, 0, tzinfo=SHANGHAI_TZ),
        start=datetime(2026, 7, 24, 14, 0),
        end=datetime(2026, 7, 24, 15, 0),
    )

    assert [bar.timestamp.strftime("%H:%M") for bar in bars] == [
        "14:00",
        "14:15",
        "14:30",
        "14:45",
    ]
    assert len(requested_params) == 2
    assert "202607241444" in requested_params[1]


def test_parse_gb18030_quote():
    snapshots = parse_quote_response(quote_line().encode("gb18030"))
    quote = snapshots[0]
    assert quote.vt_symbol == "515080.SSE"
    assert quote.name == "中证红利ETF招商"
    assert quote.last_price == pytest.approx(1.555)
    assert quote.turnover == pytest.approx(385768163)
    assert quote.timestamp.tzinfo == SHANGHAI_TZ


def test_zero_price_quote_is_ignored():
    assert parse_quote_response(quote_line(price="0")) == []


def test_malformed_quote_is_rejected():
    with pytest.raises(TencentDataError):
        parse_quote_response('v_sh515080="too~short";')


def test_parse_history_filters_breaks_and_incomplete_minute():
    payload = {
        "data": {
            "sh515080": {
                "data": [
                    {
                        "date": "20260722",
                        "data": ["0930 1.50 10 100", "1500 1.60 20 200"],
                    },
                    {
                        "date": "20260723",
                        "data": [
                            "0929 1.51 1 10",
                            "0930 1.52 10 100",
                            "0931 1.53 15 160",
                            "1131 1.54 20 220",
                            "1200 1.55 25 280",
                            "1300 1.56 30 350",
                            "1400 1.57 40 450",
                        ],
                    },
                ]
            }
        }
    }
    now = datetime(2026, 7, 23, 14, 0, 30, tzinfo=SHANGHAI_TZ)
    bars = parse_history_response(payload, "515080.SSE", now=now)
    assert [bar.timestamp.strftime("%Y%m%d%H%M") for bar in bars] == [
        "202607220930",
        "202607221500",
        "202607230930",
        "202607230931",
        "202607231300",
    ]
    assert bars[3].volume == pytest.approx(5)
    assert bars[3].turnover == pytest.approx(60)


def test_history_range_keeps_cumulative_delta_and_accepts_naive_times():
    payload = {
        "data": {
            "sh515080": {
                "data": [
                    {
                        "date": "20260723",
                        "data": [
                            "0930 1.50 100 1000",
                            "0931 1.51 110 1200",
                            "0932 1.52 125 1500",
                        ],
                    }
                ]
            }
        }
    }

    bars = parse_history_response(
        payload,
        "515080.SSE",
        now=datetime(2026, 7, 23, 10, 0),
        start=datetime(2026, 7, 23, 9, 31),
        end=datetime(2026, 7, 23, 9, 31),
    )

    assert len(bars) == 1
    assert bars[0].volume == pytest.approx(10)
    assert bars[0].turnover == pytest.approx(200)
    assert bars[0].timestamp.tzinfo == SHANGHAI_TZ


def daily_payload(rows):
    return {"code": 0, "data": {"sh515080": {"qfqday": rows}}}


def test_parse_daily_history_uses_adjusted_ohlcv_and_skips_incomplete_day():
    bars = parse_daily_history_response(
        daily_payload(
            [
                ["2026-07-22", "1.50", "1.55", "1.56", "1.49", "1000"],
                ["2026-07-23", "1.55", "1.54", "1.57", "1.53", "900"],
            ]
        ),
        "515080.SSE",
        now=datetime(2026, 7, 23, 14, 30, tzinfo=SHANGHAI_TZ),
    )

    assert len(bars) == 1
    assert bars[0].timestamp.strftime("%Y-%m-%d") == "2026-07-22"
    assert bars[0].open_price == pytest.approx(1.50)
    assert bars[0].high_price == pytest.approx(1.56)
    assert bars[0].low_price == pytest.approx(1.49)
    assert bars[0].close_price == pytest.approx(1.55)
    assert bars[0].volume == pytest.approx(1000)


def test_daily_history_pages_backwards_to_requested_start():
    requested_params = []

    class Response:
        def __init__(self, rows):
            self.content = json.dumps(daily_payload(rows)).encode()

        def raise_for_status(self):
            return None

    def fake_get(url, *, params, headers, timeout):
        requested_params.append(params["param"])
        if ",2026-01-03," in params["param"]:
            return Response(
                [
                    ["2026-01-02", "1", "1.1", "1.2", "0.9", "10"],
                    ["2026-01-03", "1.1", "1.2", "1.3", "1", "20"],
                ]
            )
        return Response([["2026-01-01", "0.9", "1", "1.1", "0.8", "30"]])

    bars = TencentClient(get=fake_get, retries=1).fetch_daily_history(
        "515080.SSE",
        now=datetime(2026, 1, 4, tzinfo=SHANGHAI_TZ),
        start=datetime(2026, 1, 1),
        end=datetime(2026, 1, 3),
    )

    assert [bar.timestamp.strftime("%Y-%m-%d") for bar in bars] == [
        "2026-01-01",
        "2026-01-02",
        "2026-01-03",
    ]
    assert len(requested_params) == 2


@pytest.mark.parametrize(
    "moment,expected",
    [
        (datetime(2026, 7, 23, 9, 30, tzinfo=SHANGHAI_TZ), True),
        (datetime(2026, 7, 23, 11, 31, tzinfo=SHANGHAI_TZ), False),
        (datetime(2026, 7, 23, 12, 0, tzinfo=SHANGHAI_TZ), False),
        (datetime(2026, 7, 23, 15, 0, tzinfo=SHANGHAI_TZ), True),
        (datetime(2026, 7, 23, 9, 30), True),
        (datetime(2026, 7, 25, 10, 0, tzinfo=SHANGHAI_TZ), False),
    ],
)
def test_trading_session(moment, expected):
    assert is_trading_session(moment) is expected
