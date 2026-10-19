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


def test_index_token_resolution_matches_angel_display_symbol_by_name():
    master = InstrumentMaster()
    master.instruments = [
        {
            "exch_seg": "NSE",
            "symbol": "Nifty 50",
            "name": "NIFTY",
            "token": "99926000",
            "instrumenttype": "AMXIDX",
        }
    ]
    master._loaded = True
    assert master.resolve_index_token("NIFTY") == "99926000"


def test_index_token_resolution_rejects_legacy_single_entry():
    master = InstrumentMaster()
    master.instruments = [
        {"exch_seg": "NSE", "symbol": "NIFTY", "token": "25", "instrumenttype": "AMXIDX"},
    ]
    master._loaded = True
    try:
        master.resolve_index_token("NIFTY")
    except LookupError as exc:
        assert "expected exactly one index instrument" in str(exc)
    else:
        raise AssertionError("expected legacy index token to be rejected")


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
    # Keep the declared-index lookup deterministic and offline; _tokens()
    # may refresh the instrument master when its cache is not authoritative.
    master.download = lambda force=False: master.instruments
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


def test_underlying_feed_retains_unresolved_target_and_recovers_token():
    master = InstrumentMaster()
    master.instruments = []
    master._loaded = True
    master.download = lambda force=False: master.instruments
    tracker = LiveSyntheticAtmTracker(strikes_by_symbol={"NIFTY": (100.0, 105.0)})
    feed = LiveSyntheticUnderlyingFeed(
        ("NIFTY",),
        tracker=tracker,
        instrument_master=master,
        index_symbols=frozenset({"NIFTY"}),
    )

    assert feed._tokens() == {"NIFTY": ""}
    assert feed.symbols == ("NIFTY",)
    assert feed.snapshot()["state"] == "TOKEN_RESOLUTION_FAILED"

    master.instruments = [
        {"exch_seg": "NSE", "symbol": "NIFTY", "token": "99926000"},
    ]
    assert feed._tokens() == {"NIFTY": "99926000"}
    assert [d.symbol for d in feed._descriptors()] == ["NIFTY"]


def test_run_forever_starts_common_feed_without_descriptor_token_attribute(monkeypatch):
    from app.market_data import live_synthetic_underlying as module

    holder = {}
    original_descriptor = module.CommonStrategyMarketFeed.descriptor

    class FakeFeed:
        descriptor = staticmethod(original_descriptor)

        def __init__(self, consumer, *, auth):
            self.started = []

        def start(self, descriptors, callback):
            self.started.append((tuple(descriptors), callback))
            holder["feed"].stop_event.set()

        def stop(self):
            pass

    monkeypatch.setattr(module, "CommonStrategyMarketFeed", FakeFeed)
    monkeypatch.setattr(module, "TOKEN_REFRESH_SECONDS", 0.0)

    master = InstrumentMaster()
    master.instruments = [
        {"exch_seg": "NSE", "instrumenttype": "EQ", "symbol": "ABC", "token": "123"}
    ]
    master._loaded = True
    master.download = lambda force=False: master.instruments
    tracker = LiveSyntheticAtmTracker(strikes_by_symbol={"ABC": (100.0, 105.0)})
    feed = LiveSyntheticUnderlyingFeed(("ABC",), tracker=tracker, instrument_master=master)
    holder["feed"] = feed

    feed.run_forever()

    # A descriptor exposes its broker token through key, not descriptor.token.
    assert feed._feed is None

def test_underlying_feed_maps_shared_canonical_symbol_back_to_configured_underlying():
    from time import time_ns

    tracker = LiveSyntheticAtmTracker(strikes_by_symbol={"RELIANCE": (100.0, 105.0)})
    observed = []
    feed = LiveSyntheticUnderlyingFeed(
        ("RELIANCE",),
        tracker=tracker,
        concrete_tokens={"RELIANCE": "2885"},
        on_price=lambda symbol, price, timestamp: observed.append((symbol, price, timestamp)),
    )

    # The cash-future consumer may have registered this shared NSE token as
    # RELIANCE-EQ first, so the normalized record's display symbol is not the
    # underlying key that the synthetic ATM tracker expects.
    descriptor = feed._descriptors()[0]
    timestamp_ns = time_ns()
    record = MarketDataRecord(
        instrument=InstrumentKey("NSE", "NSE", "2885"),
        symbol="RELIANCE-EQ",
        instrument_type=InstrumentType.EQUITY,
        timestamp_ns=timestamp_ns,
        ltp=104.6,
    )

    feed._on_record(record)

    assert descriptor.symbol == "RELIANCE"
    assert tracker.atm("RELIANCE") == 105.0
    assert observed == [("RELIANCE", 104.6, timestamp_ns)]

    health = feed.snapshot()
    assert health["symbols"]["RELIANCE"]["token_resolved"] is True
    assert health["symbols"]["RELIANCE"]["token"] == "2885"
    assert health["symbols"]["RELIANCE"]["last_price"] == 104.6
    assert health["symbols"]["RELIANCE"]["last_tick_age_seconds"] is not None
    assert health["state"] == "LIVE"
    assert health["fresh_symbol_count"] == 1
    assert health["stale_after_seconds"] == 10.0



def test_underlying_feed_reports_connected_but_silent(monkeypatch):
    from time import time_ns
    tracker = LiveSyntheticAtmTracker(strikes_by_symbol={"NIFTY": (100.0, 105.0)})
    feed = LiveSyntheticUnderlyingFeed(
        ("NIFTY",), tracker=tracker, concrete_tokens={"NIFTY": "99926000"}
    )

    class ConnectedSilentFeed:
        def snapshot(self):
            return {"connected": True, "transport_thread_alive": True}

    feed._feed = ConnectedSilentFeed()
    feed._last_resolved_tokens = {"NIFTY": "99926000"}
    feed._last_token_resolution_at_ns = time_ns()

    assert feed.snapshot()["state"] == "CONNECTED_NO_TICKS"


def test_underlying_feed_reports_stale_after_last_tick_expires(monkeypatch):
    from time import time_ns
    from app.market_data.contracts import InstrumentKey, InstrumentType, MarketDataRecord

    tracker = LiveSyntheticAtmTracker(strikes_by_symbol={"NIFTY": (100.0, 105.0)})
    feed = LiveSyntheticUnderlyingFeed(
        ("NIFTY",), tracker=tracker, concrete_tokens={"NIFTY": "99926000"}
    )
    now_ns = time_ns()
    feed._last_resolved_tokens = {"NIFTY": "99926000"}
    feed._last_token_resolution_at_ns = now_ns - 20_000_000_000
    feed._last_tick_by_symbol = {
        "NIFTY": {
            "price": 104.0,
            "source_timestamp_ns": now_ns - 20_000_000_000,
            "received_at_ns": now_ns - 20_000_000_000,
        }
    }

    class DisconnectedFeed:
        def snapshot(self):
            return {"connected": False, "transport_thread_alive": False}

    feed._feed = DisconnectedFeed()
    assert feed.snapshot()["state"] == "STALE"
    assert feed.snapshot()["symbols"]["NIFTY"]["last_tick_age_seconds"] >= 20.0



def test_underlying_feed_reports_reconnecting_when_transport_is_down_without_ticks():
    from time import time_ns

    tracker = LiveSyntheticAtmTracker(strikes_by_symbol={"NIFTY": (100.0, 105.0)})
    feed = LiveSyntheticUnderlyingFeed(
        ("NIFTY",), tracker=tracker, concrete_tokens={"NIFTY": "99926000"}
    )

    class DisconnectedFeed:
        def snapshot(self):
            return {
                "connected": False,
                "disconnected_groups": ["3:0"],
                "transport_close_timed_out_groups": [],
            }

    feed._feed = DisconnectedFeed()
    feed._last_resolved_tokens = {"NIFTY": "99926000"}
    feed._last_token_resolution_at_ns = time_ns()

    health = feed.snapshot()
    assert health["state"] == "RECONNECTING"
    assert health["state_reason"] == "required_transport_disconnected"


def test_underlying_feed_reports_failed_when_transport_close_is_stuck():
    from time import time_ns

    tracker = LiveSyntheticAtmTracker(strikes_by_symbol={"NIFTY": (100.0, 105.0)})
    feed = LiveSyntheticUnderlyingFeed(
        ("NIFTY",), tracker=tracker, concrete_tokens={"NIFTY": "99926000"}
    )

    class StuckTransportFeed:
        def snapshot(self):
            return {
                "connected": False,
                "disconnected_groups": ["3:0"],
                "transport_close_timed_out_groups": ["3:0"],
            }

    feed._feed = StuckTransportFeed()
    feed._last_resolved_tokens = {"NIFTY": "99926000"}
    feed._last_token_resolution_at_ns = time_ns()

    health = feed.snapshot()
    assert health["state"] == "FAILED"
    assert health["state_reason"] == "transport_close_timed_out"
