import threading

from app.market_data.cash_future_opportunity import CashFutureOpportunityScanner
from app.market_data.common_websocket import CommonWebSocketManager
from app.market_data.contracts import InstrumentKey, InstrumentType, MarketDataRecord
from app.market_data.registry import InstrumentDescriptor


def record(key, symbol, kind, ts, bid, ask, lot=None, underlying=None, expiry=None):
    return MarketDataRecord(
        instrument=key, symbol=symbol, instrument_type=kind, timestamp_ns=ts,
        ltp=(bid + ask) / 2, bid=bid, ask=ask, lot_size=lot,
        underlying=underlying, expiry=expiry,
    )


def test_common_manager_receives_normalized_cash_future_pair():
    manager = CommonWebSocketManager(socket_factory=lambda: object())
    cash_key = InstrumentKey("NSE", "NSE", "1")
    future_key = InstrumentKey("NFO", "NFO", "2")
    manager.registry.register_many((
        InstrumentDescriptor(cash_key, "ABC-EQ", "equity", "NSE", "NSE"),
        InstrumentDescriptor(future_key, "ABC-FUT", "future", "NFO", "NFO", expiry="2026-10-29", lot_size=50),
    ))
    scanner = CashFutureOpportunityScanner(minimum_gap_points=2, minimum_gross_profit=50)
    assert scanner.update(record(cash_key, "ABC-EQ", InstrumentType.EQUITY, 1, 99, 100, underlying="ABC"), contract_month="CASH") is None
    result = scanner.update(record(future_key, "ABC-FUT", InstrumentType.FUTURE, 1, 103, 104, lot=50, underlying="ABC", expiry="2026-10-29"), contract_month="CURRENT")
    assert result is not None and result.signal.qualifies
    assert result.signal.gap_points == 3
    assert result.signal.gross_profit == 150


def test_runner_contract_metadata_keeps_current_and_near_separate():
    class Master:
        def download(self):
            return [
                {"exch_seg":"NFO","instrumenttype":"FUTSTK","name":"ABC","token":"10","symbol":"ABC26OCTFUT","expiry":"29OCT2026","lotsize":"50"},
                {"exch_seg":"NFO","instrumenttype":"FUTSTK","name":"ABC","token":"11","symbol":"ABC26NOVFUT","expiry":"26NOV2026","lotsize":"50"},
                {"exch_seg":"NSE","instrumenttype":"EQ","name":"ABC","token":"1","symbol":"ABC-EQ"},
            ]
        def resolve_cash_instrument(self, symbol, exchange):
            return {"token":"1","symbol":"ABC-EQ"}
    from app.market_data.live_cash_future_common import LiveCashFutureCommonRunner
    runner = LiveCashFutureCommonRunner("ignored", instrument_master=Master(), manager=CommonWebSocketManager(socket_factory=lambda: object()))
    descriptors = runner._build_descriptors()
    assert len(descriptors) == 3
    assert len({descriptor.key for descriptor in descriptors}) == len(descriptors)
    months = {meta["contract_month"] for meta in runner._metadata.values() if meta["leg"] == "FUTURE"}
    assert months == {"CURRENT", "NEAR"}


def test_runner_stop_is_idempotent_under_concurrent_shutdown():
    from types import SimpleNamespace
    from app.market_data.live_cash_future_common import LiveCashFutureCommonRunner

    class Manager:
        def __init__(self):
            self.clear_calls = 0
            self.lock = threading.Lock()

        def clear_consumer(self, consumer):
            with self.lock:
                self.clear_calls += 1

    class Ingestor:
        def __init__(self):
            self.close_calls = 0
            self.lock = threading.Lock()

        def close(self):
            with self.lock:
                self.close_calls += 1

    class Repository:
        def __init__(self):
            self.close_calls = 0
            self.lock = threading.Lock()

        def close(self):
            with self.lock:
                self.close_calls += 1

    manager = Manager()
    ingestor = Ingestor()
    repository = Repository()
    runner = LiveCashFutureCommonRunner(
        "ignored",
        manager=manager,
    )
    runner._ingestor = ingestor
    runner._repository = repository

    threads = [threading.Thread(target=runner.stop) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert runner.stop_event.is_set()
    assert manager.clear_calls == 1
    assert ingestor.close_calls == 1
    assert repository.close_calls == 1
    assert runner._ingestor is None
    assert runner._repository is None


def test_runner_persistence_coalesces_multiple_ticks_within_one_second(monkeypatch):
    from app.market_data.live_cash_future_common import LiveCashFutureCommonRunner
    from app.core.config import settings
    monkeypatch.setattr(settings, "LIVE_MARKET_DATA_PERSISTENCE_ENABLED", True)

    class Ingestor:
        def __init__(self):
            self.records = []
        def submit_historical(self, record):
            self.records.append(record)
        def close(self):
            pass

    runner = LiveCashFutureCommonRunner(
        "ignored",
        manager=CommonWebSocketManager(socket_factory=lambda: object()),
    )
    ingestor = Ingestor()
    runner._ingestor = ingestor
    key = InstrumentKey("NSE", "NSE", "1")
    runner._metadata[key] = {"leg": "CASH", "underlying": "ABC", "contract_month": "CASH"}

    runner._on_record(record(key, "ABC-EQ", InstrumentType.EQUITY, 1_100_000_000, 99, 100, underlying="ABC"))
    runner._on_record(record(key, "ABC-EQ", InstrumentType.EQUITY, 1_500_000_000, 99.5, 100.5, underlying="ABC"))
    assert ingestor.records == []

    runner._on_record(record(key, "ABC-EQ", InstrumentType.EQUITY, 2_100_000_000, 100, 101, underlying="ABC"))
    assert len(ingestor.records) == 1
    assert ingestor.records[0].timestamp_ns == 1_500_000_000

    runner.stop()
    assert len(ingestor.records) == 2
    assert ingestor.records[1].timestamp_ns == 2_100_000_000


def test_runner_reuses_compatible_shared_cash_descriptor_without_conflict():
    from app.market_data.live_cash_future_common import LiveCashFutureCommonRunner
    manager = CommonWebSocketManager(socket_factory=lambda: object())
    key = InstrumentKey("NSE", "NSE", "25")
    manager.registry.register_many((
        InstrumentDescriptor(key, "CANONICAL-EQ", "equity", "NSE", "NSE"),
    ))
    runner = LiveCashFutureCommonRunner("ignored", manager=manager)
    runner._register_descriptors((
        InstrumentDescriptor(key, "ALIAS-EQ", "equity", "NSE", "NSE"),
    ))
    assert manager.registry.get(key).symbol == "CANONICAL-EQ"


def test_runner_skips_incompatible_shared_descriptor_instead_of_crashing():
    from app.market_data.live_cash_future_common import LiveCashFutureCommonRunner
    manager = CommonWebSocketManager(socket_factory=lambda: object())
    key = InstrumentKey("NSE", "NSE", "25")
    manager.registry.register_many((InstrumentDescriptor(key, "NIFTY-INDEX", "index", "NSE", "NSE"),))
    runner = LiveCashFutureCommonRunner("ignored", manager=manager)
    assert runner._register_descriptors((InstrumentDescriptor(key, "ABC-EQ", "equity", "NSE", "NSE"),)) == set()


def test_runner_maps_underlying_metadata_before_pairing_cash_and_future():
    from app.market_data.live_cash_future_common import LiveCashFutureCommonRunner

    runner = LiveCashFutureCommonRunner(
        "ignored",
        manager=CommonWebSocketManager(socket_factory=lambda: object()),
        scanner=CashFutureOpportunityScanner(minimum_gap_points=0, minimum_gross_profit=0),
    )
    cash_key = InstrumentKey("NSE", "NSE", "1")
    future_key = InstrumentKey("NFO", "NFO", "2")
    runner._metadata[cash_key] = {
        "leg": "CASH", "underlying": "ABC", "contract_month": "CASH",
    }
    runner._metadata[future_key] = {
        "leg": "FUTURE", "underlying": "ABC", "contract_month": "CURRENT",
    }

    # Broker-normalized records intentionally omit underlying, and the future's
    # tradingsymbol differs from the cash symbol. Runner metadata must join them.
    runner._on_record(record(cash_key, "ABC-EQ", InstrumentType.EQUITY, 1_000_000_000, 99, 100))
    runner._on_record(record(future_key, "ABC26OCTFUT", InstrumentType.FUTURE, 1_000_000_000, 103, 104, lot=50))

    assert runner.last_result is not None
    assert runner.last_result.signal.symbol == "ABC"
    assert runner.last_result.cash.underlying == "ABC"
    assert runner.last_result.future.underlying == "ABC"
    runner.stop()


def test_cash_future_scanner_does_not_replace_newer_leg_with_out_of_order_tick():
    scanner = CashFutureOpportunityScanner(minimum_gap_points=0, minimum_gross_profit=0)
    cash_key = InstrumentKey("NSE", "NSE", "1")
    future_key = InstrumentKey("NFO", "NFO", "2")
    cash = record(cash_key, "ABC-EQ", InstrumentType.EQUITY, 2_000_000_000, 99, 100, underlying="ABC")
    future = record(future_key, "ABC26OCTFUT", InstrumentType.FUTURE, 2_000_000_000, 103, 104, lot=50, underlying="ABC")
    assert scanner.update(cash, contract_month="CASH") is None
    assert scanner.update(future, contract_month="CURRENT") is not None

    delayed = record(cash_key, "ABC-EQ", InstrumentType.EQUITY, 1_000_000_000, 90, 91, underlying="ABC")
    assert scanner.update(delayed, contract_month="CASH") is None
    assert scanner._latest["ABC"]["CASH"].timestamp_ns == 2_000_000_000
    assert scanner.out_of_order_records == 1



def test_quote_diagnostics_distinguish_missing_depth_from_normalization_failure():
    from app.market_data.live_cash_future_common import LiveCashFutureCommonRunner

    runner = LiveCashFutureCommonRunner(
        "ignored", manager=CommonWebSocketManager(socket_factory=lambda: object())
    )
    key = InstrumentKey("NSE", "NSE", "1")
    runner._metadata[key] = {
        "leg": "CASH", "underlying": "ABC", "contract_month": "CASH",
    }
    record_with_raw_depth = MarketDataRecord(
        instrument=key,
        symbol="ABC-EQ",
        instrument_type=InstrumentType.EQUITY,
        timestamp_ns=1_000_000_000,
        ltp=100.0,
        bid=None,
        ask=100.1,
        underlying="ABC",
        payload={"best_5_buy_data": [{"price": 9990, "quantity": 10}]},
    )

    runner._on_record(record_with_raw_depth)
    diagnostics = runner.snapshot()["quote_diagnostics"]
    assert diagnostics["records"] == 1
    assert diagnostics["cash_records"] == 1
    assert diagnostics["missing_bid"] == 1
    assert diagnostics["missing_ask"] == 0
    assert diagnostics["buy_depth_present"] == 1
    assert diagnostics["buy_depth_present_bid_missing"] == 1
    assert diagnostics["sell_depth_present"] == 0
    runner.stop()


def test_quote_diagnostics_count_missing_depth_without_retaining_payload():
    from app.market_data.live_cash_future_common import LiveCashFutureCommonRunner

    runner = LiveCashFutureCommonRunner(
        "ignored", manager=CommonWebSocketManager(socket_factory=lambda: object())
    )
    key = InstrumentKey("NFO", "NFO", "2")
    runner._metadata[key] = {
        "leg": "FUTURE", "underlying": "ABC", "contract_month": "CURRENT",
    }
    tick = MarketDataRecord(
        instrument=key,
        symbol="ABC26OCTFUT",
        instrument_type=InstrumentType.FUTURE,
        timestamp_ns=1_000_000_000,
        ltp=103.0,
        bid=None,
        ask=None,
        lot_size=50,
        underlying="ABC",
        payload={},
    )

    runner._on_record(tick)
    diagnostics = runner.snapshot()["quote_diagnostics"]
    assert diagnostics["future_records"] == 1
    assert diagnostics["missing_both_sides"] == 1
    assert diagnostics["buy_depth_present"] == 0
    assert diagnostics["sell_depth_present"] == 0
    assert "payload" not in diagnostics
    runner.stop()



def test_runner_counts_timestamp_provenance_without_retaining_raw_payload():
    from app.market_data.live_cash_future_common import LiveCashFutureCommonRunner

    runner = LiveCashFutureCommonRunner(
        "ignored", manager=CommonWebSocketManager(socket_factory=lambda: object())
    )
    key = InstrumentKey("NSE", "NSE", "1")
    runner._metadata[key] = {
        "leg": "CASH", "underlying": "ABC", "contract_month": "CASH",
    }
    for index, provenance in enumerate(("broker_ms", "fallback_missing", "fallback_invalid", "unknown"), 1):
        tick = MarketDataRecord(
            instrument=key,
            symbol="ABC-EQ",
            instrument_type=InstrumentType.EQUITY,
            timestamp_ns=index * 1_000_000_000,
            ltp=100.0,
            bid=99.9,
            ask=100.0,
            underlying="ABC",
            payload={"_timestamp_provenance": provenance},
        )
        runner._on_record(tick)

    snapshot = runner.snapshot()
    assert snapshot["timestamp_diagnostics"] == {
        "broker_ns": 0,
        "broker_ms": 1,
        "fallback_missing": 1,
        "fallback_invalid": 1,
        "invalid": 0,
        "unknown": 1,
    }
    assert "payload" not in snapshot["timestamp_diagnostics"]
    runner.stop()



def test_cash_future_payload_uses_ingress_timestamp_not_callback_time():
    from app.market_data.live_cash_future_common import LiveCashFutureCommonRunner

    runner = LiveCashFutureCommonRunner(
        "ignored", manager=CommonWebSocketManager(socket_factory=lambda: object())
    )
    key = InstrumentKey("NSE", "NSE", "1")
    tick = MarketDataRecord(
        instrument=key,
        symbol="ABC-EQ",
        instrument_type=InstrumentType.EQUITY,
        timestamp_ns=1_760_000_000_000_000_000,
        ltp=100.0,
        bid=99.9,
        ask=100.0,
        payload={"_received_at_ns": 1_760_000_000_123_456_789},
    )
    payload = runner._record_payload(
        tick,
        {"leg": "CASH", "underlying": "ABC", "contract_month": "CASH"},
    )
    assert payload["received_at_ns"] == 1_760_000_000_123_456_789
    runner.stop()



def test_quote_diagnostics_distinguish_present_depth_from_usable_ask_price():
    from app.market_data.live_cash_future_common import LiveCashFutureCommonRunner

    runner = LiveCashFutureCommonRunner(
        "ignored", manager=CommonWebSocketManager(socket_factory=lambda: object())
    )
    key = InstrumentKey("NFO", "NFO", "777")
    meta = {"leg": "FUTURE", "underlying": "ABC", "contract_month": "CURRENT"}
    malformed = MarketDataRecord(
        instrument=key,
        symbol="ABC-FUT",
        instrument_type=InstrumentType.FUTURE,
        timestamp_ns=1_760_000_000_000_000_000,
        ltp=100.0,
        bid=99.0,
        ask=None,
        payload={
            "best_5_buy_data": [{"price": "9900", "quantity": 10}],
            "best_5_sell_data": [{"price": "bad"}, {"price": 0}, None],
        },
    )
    valid = MarketDataRecord(
        instrument=key,
        symbol="ABC-FUT",
        instrument_type=InstrumentType.FUTURE,
        timestamp_ns=1_760_000_000_100_000_000,
        ltp=100.0,
        bid=99.0,
        ask=101.0,
        payload={"best_5_sell_data": [{"price": "10100", "quantity": 10}]},
    )

    runner._record_quote_diagnostics(malformed, meta)
    runner._record_quote_diagnostics(valid, meta)
    diagnostics = runner.snapshot()["quote_diagnostics"]
    assert diagnostics["sell_depth_present"] == 2
    assert diagnostics["sell_depth_usable_price"] == 1
    assert diagnostics["sell_depth_present_without_usable_price"] == 1
    assert diagnostics["sell_depth_levels_examined"] >= 4
    assert "best_5_sell_data" not in diagnostics
    runner.stop()
