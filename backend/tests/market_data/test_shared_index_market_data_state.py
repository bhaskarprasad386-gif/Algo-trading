from time import time_ns

from app.market_data.shared_index_market_data_state import SharedIndexMarketDataState
from app.market_data.contracts import InstrumentKey, InstrumentType, MarketDataRecord



def _record(token, ts=None):
    return MarketDataRecord(
        instrument=InstrumentKey("NSE", "NSE", token), symbol=token,
        instrument_type=InstrumentType.INDEX, timestamp_ns=ts or time_ns(),
        ltp=25000.0,
    )


def test_state_accepts_fresh_ticks_and_reports_freshness():
    state = SharedIndexMarketDataState(max_age_seconds=5)
    assert state.on_record(_record("spot1"))
    snap = state.snapshot()
    assert snap["instrument_count"] == 1
    assert snap["fresh_count"] == 1
    assert snap["feed_fresh"] is True



def test_state_rejects_out_of_order_ticks():
    state = SharedIndexMarketDataState()
    now = time_ns()
    assert state.on_record(_record("spot2", now))
    assert not state.on_record(_record("spot2", now - 100))
    assert state.snapshot()["out_of_order_ticks_dropped"] == 1



def test_state_rejects_stale_tick():
    state = SharedIndexMarketDataState(max_age_seconds=1)
    assert not state.on_record(_record("spot3", time_ns() - 3_000_000_000))
    assert state.snapshot()["stale_ticks_dropped"] == 1
