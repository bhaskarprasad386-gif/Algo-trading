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

def test_records_snapshot_exposes_quote_fields_and_one_second_oi_delta():
    state = SharedIndexMarketDataState(max_age_seconds=5)
    now = time_ns()
    option = MarketDataRecord(
        instrument=InstrumentKey("NFO", "NFO", "opt1"), symbol="NIFTYCE",
        instrument_type=InstrumentType.OPTION, timestamp_ns=now - 1_300_000_000,
        ltp=125.5, bid=124.9, ask=126.1, bid_qty=20, ask_qty=30,
        volume=800, oi=1000, underlying="NIFTY", expiry="2026-11-26",
        strike=25000, option_type=__import__("app.market_data.contracts", fromlist=["OptionType"]).OptionType.CALL,
        lot_size=25,
    )
    assert state.on_record(option)
    later = MarketDataRecord(
        instrument=option.instrument, symbol=option.symbol,
        instrument_type=option.instrument_type, timestamp_ns=time_ns() - 100_000_000,
        ltp=126.0, bid=125.5, ask=126.5, bid_qty=25, ask_qty=40,
        volume=820, oi=1060, underlying="NIFTY", expiry="2026-11-26",
        strike=25000, option_type=option.option_type, lot_size=25,
    )
    # Timestamp stays within freshness and monotonic acceptance.
    assert state.on_record(later)
    rows = state.records_snapshot()
    assert len(rows) == 1
    row = rows[0]
    assert row["ltp"] == 126.0 and row["bid"] == 125.5 and row["ask"] == 126.5
    assert row["bid_qty"] == 25 and row["ask_qty"] == 40
    assert row["volume"] == 820 and row["oi"] == 1060
    assert row["oi_delta_1s"] == 60
    assert row["volume_delta_1s"] == 20
    assert row["fresh"] is True


def test_oi_delta_is_not_reported_for_subsecond_sample_window():
    state = SharedIndexMarketDataState(max_age_seconds=5)
    now = time_ns()
    option = MarketDataRecord(
        instrument=InstrumentKey("NFO", "NFO", "opt-short"), symbol="NIFTYCE",
        instrument_type=InstrumentType.OPTION, timestamp_ns=now - 300_000_000,
        ltp=125.5, oi=1000, volume=1200, underlying="NIFTY", expiry="2026-11-26",
        strike=25000, option_type=__import__("app.market_data.contracts", fromlist=["OptionType"]).OptionType.CALL,
        lot_size=25,
    )
    assert state.on_record(option)
    later = MarketDataRecord(
        instrument=option.instrument, symbol=option.symbol,
        instrument_type=option.instrument_type, timestamp_ns=time_ns() - 100_000_000,
        ltp=126.0, oi=1060, volume=1220, underlying="NIFTY", expiry=option.expiry,
        strike=option.strike, option_type=option.option_type, lot_size=25,
    )
    assert state.on_record(later)
    row = state.records_snapshot()[0]
    assert row["oi_delta_1s"] is None
    assert row["volume_delta_1s"] is None



def test_stale_record_does_not_expose_oi_or_volume_delta():
    state = SharedIndexMarketDataState(max_age_seconds=5)
    now = time_ns()
    option = MarketDataRecord(
        instrument=InstrumentKey("NFO", "NFO", "opt-stale"), symbol="NIFTYCE",
        instrument_type=InstrumentType.OPTION, timestamp_ns=now - 1_500_000_000,
        ltp=125.5, oi=1000, volume=1200, underlying="NIFTY", expiry="2026-11-26",
        strike=25000, option_type=__import__("app.market_data.contracts", fromlist=["OptionType"]).OptionType.CALL,
        lot_size=25,
    )
    assert state.on_record(option)
    state.max_age_ns = 1_000_000_000  # tighter threshold after acceptance
    row = state.records_snapshot()[0]
    assert row["fresh"] is False
    assert row["oi_delta_1s"] is None
    assert row["volume_delta_1s"] is None



def test_volume_reset_clears_old_session_baseline():
    state = SharedIndexMarketDataState(max_age_seconds=5)
    now = time_ns()
    inst = InstrumentKey("NFO", "NFO", "opt-reset")
    def tick(ts, volume, oi):
        return MarketDataRecord(
            instrument=inst, symbol="NIFTYCE", instrument_type=InstrumentType.OPTION,
            timestamp_ns=ts, ltp=125.5, volume=volume, oi=oi, underlying="NIFTY",
            expiry="2026-11-26", strike=25000,
            option_type=__import__("app.market_data.contracts", fromlist=["OptionType"]).OptionType.CALL,
            lot_size=25,
        )
    assert state.on_record(tick(now - 1_300_000_000, 5000, 1000))
    assert state.on_record(tick(time_ns() - 100_000_000, 20, 1010))
    row = state.records_snapshot()[0]
    assert row["volume_delta_1s"] is None
    assert row["oi_delta_1s"] == 10
