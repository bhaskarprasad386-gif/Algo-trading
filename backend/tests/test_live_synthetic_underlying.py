from app.market_data.instruments import InstrumentMaster
from app.market_data.live_synthetic_atm import LiveSyntheticAtmTracker
from app.market_data.live_synthetic_underlying import LiveSyntheticUnderlyingFeed


def test_underlying_feed_resolves_real_nse_tokens():
    master = InstrumentMaster()
    master.instruments = [
        {"exch_seg": "NSE", "instrumenttype": "EQ", "symbol": "NIFTY", "token": "999"}
    ]
    master._loaded = True
    # Keep this token-resolution regression deterministic and offline; the
    # test fixture must not be replaced by the process-wide Angel master cache.
    master.download = lambda force=False: master.instruments
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


def test_underlying_feed_accepts_authoritative_index_token_without_cash_lookup():
    tracker = LiveSyntheticAtmTracker(strikes_by_symbol={"NIFTY": (100.0, 105.0)})
    feed = LiveSyntheticUnderlyingFeed(
        ("NIFTY",),
        tracker=tracker,
        concrete_tokens={"NIFTY": "26000"},
    )
    assert feed._tokens() == {"NIFTY": "26000"}

def test_index_token_resolution_uses_exact_master_entry():
    master = InstrumentMaster()
    master.instruments = [
        {"exch_seg": "NSE", "symbol": "NIFTY", "token": "99926000"},
    ]
    master._loaded = True
    assert master.resolve_index_token("NIFTY") == "99926000"


def test_index_token_resolution_prefers_migrated_angel_index_entry():
    master = InstrumentMaster()
    master.instruments = [
        {"exch_seg": "NSE", "symbol": "NIFTY", "token": "26000", "instrumenttype": "AMXIDX"},
        {"exch_seg": "NSE", "symbol": "NIFTY", "token": "99926000", "instrumenttype": "AMXIDX"},
    ]
    master._loaded = True
    assert master.resolve_index_token("NIFTY") == "99926000"


def test_index_token_resolution_still_rejects_multiple_migrated_entries():
    master = InstrumentMaster()
    master.instruments = [
        {"exch_seg": "BSE", "symbol": "BANKEX", "token": "99919012", "instrumenttype": "AMXIDX"},
        {"exch_seg": "BSE", "symbol": "BANKEX", "token": "99919099", "instrumenttype": "AMXIDX"},
    ]
    master._loaded = True
    try:
        master.resolve_index_token("BANKEX", "BSE")
    except LookupError as exc:
        assert "expected exactly one index instrument" in str(exc)
    else:
        raise AssertionError("expected LookupError")

def test_underlying_feed_resolves_index_from_master_when_declared():
    master = InstrumentMaster()
    master.instruments = [
        {"exch_seg": "NSE", "symbol": "NIFTY", "token": "99926000"},
    ]
    master._loaded = True
    tracker = LiveSyntheticAtmTracker(strikes_by_symbol={"NIFTY": (100.0, 105.0)})
    feed = LiveSyntheticUnderlyingFeed(
        ("NIFTY",), tracker=tracker, instrument_master=master, index_symbols=frozenset({"NIFTY"})
    )
    assert feed._tokens() == {"NIFTY": "99926000"}


def test_underlying_feed_routes_bse_indices_to_bfo_exchange_type():
    tracker = LiveSyntheticAtmTracker(strikes_by_symbol={"SENSEX": (80000.0, 80100.0), "NIFTY": (100.0, 105.0)})
    feed = LiveSyntheticUnderlyingFeed(
        ("NIFTY", "SENSEX", "BANKEX"),
        tracker=tracker,
        concrete_tokens={"NIFTY": "999", "SENSEX": "1", "BANKEX": "2"},
        index_symbols=frozenset({"NIFTY", "SENSEX", "BANKEX"}),
    )
    groups = feed._subscription_groups(feed._tokens())
    assert groups[1] == ["999"]
    assert groups[4] == ["1", "2"]


def test_underlying_feed_keeps_bse_exchange_routing_only_for_bse_indices():
    tracker = LiveSyntheticAtmTracker(strikes_by_symbol={"SENSEX": (80000.0,)})
    feed = LiveSyntheticUnderlyingFeed(
        ("SENSEX",), tracker=tracker,
        concrete_tokens={"SENSEX": "1"},
        index_symbols=frozenset({"SENSEX"}),
    )
    assert feed._exchange_type("SENSEX") == 4
    assert feed._exchange_type("NIFTY") == 1


def test_filter_resolvable_index_symbols_skips_unresolvable_index():
    from app.market_data.live_synthetic_underlying import filter_resolvable_index_symbols
    master = InstrumentMaster()
    master.instruments = [
        {"exch_seg": "NSE", "symbol": "NIFTY", "token": "99926000"},
    ]
    master._loaded = True
    assert filter_resolvable_index_symbols(master, ("NIFTY", "NIFTYFPI")) == ("NIFTY",)


def test_underlying_feed_skips_unresolvable_symbols_without_killing_feed():
    master = InstrumentMaster()
    master.instruments = [{"exch_seg":"NSE","symbol":"NIFTY","token":"99926000"}]
    master._loaded = True
    master.download = lambda force=False: master.instruments
    tracker = LiveSyntheticAtmTracker(strikes_by_symbol={"NIFTY": (100.0, 105.0)})
    feed = LiveSyntheticUnderlyingFeed(("NIFTY","COPPER"), tracker=tracker, instrument_master=master, index_symbols=frozenset({"NIFTY"}), commodity_symbols=frozenset({"COPPER"}))
    assert feed._tokens() == {"NIFTY":"99926000"}
    assert [d.symbol for d in feed._descriptors()] == ["NIFTY"]
