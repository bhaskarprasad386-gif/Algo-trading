from datetime import datetime
from zoneinfo import ZoneInfo

from app.market_data.live_cash_future_stream import (
    LiveCashFutureOneSecondCollector,
    _ltp,
    _timestamp_ns,
)

IST = ZoneInfo("Asia/Kolkata")


def test_live_timestamp_and_ltp_normalization():
    assert _timestamp_ns({"exchange_timestamp": 1762234836588}) == 1762234836588000000
    assert _ltp({"last_traded_price": 7051240}) == 70512.40


def test_live_market_session():
    assert LiveCashFutureOneSecondCollector.market_open(datetime(2026, 9, 28, 10, 0, tzinfo=IST))
    assert not LiveCashFutureOneSecondCollector.market_open(datetime(2026, 9, 27, 10, 0, tzinfo=IST))
    assert not LiveCashFutureOneSecondCollector.market_open(datetime(2026, 9, 28, 16, 0, tzinfo=IST))
