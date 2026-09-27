from app.market_data.instruments import InstrumentMaster
from app.market_data.live_synthetic_atm import LiveSyntheticAtmTracker
from app.market_data.live_synthetic_underlying import LiveSyntheticUnderlyingFeed


def test_underlying_feed_resolves_real_nse_tokens():
    master = InstrumentMaster()
    master.instruments = [
        {"exch_seg": "NSE", "instrumenttype": "EQ", "symbol": "NIFTY", "token": "999"}
    ]
    master._loaded = True
    tracker = LiveSyntheticAtmTracker(strikes_by_symbol={"NIFTY": (100.0, 105.0)})
    feed = LiveSyntheticUnderlyingFeed(
        ("NIFTY",), tracker=tracker, instrument_master=master
    )
    assert feed._tokens() == {"NIFTY": "999"}


def test_underlying_feed_normalizes_smartapi_price_and_timestamp():
    tracker = LiveSyntheticAtmTracker(strikes_by_symbol={"NIFTY": (100.0, 105.0)})
    feed = LiveSyntheticUnderlyingFeed(("NIFTY",), tracker=tracker)
    assert feed._price({"last_traded_price": "10500"}) == 105.0
    assert feed._timestamp_ns({"exchange_timestamp": "1700000000"}) == 1_700_000_000_000_000_000
