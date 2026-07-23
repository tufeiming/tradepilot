from datetime import datetime

import pytest

from tradepilot.data_sources.tencent.client import (
    SHANGHAI_TZ,
    TencentDataError,
    from_tencent_symbol,
    is_trading_session,
    parse_history_response,
    parse_quote_response,
    to_tencent_symbol,
)


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
