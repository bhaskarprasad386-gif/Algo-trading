from app.market_data.contracts import InstrumentKey, InstrumentType, MarketDataRecord
from app.market_data.ingestion import BoundedMarketDataIngestor
from app.market_data.normalizer import AngelOneTickNormalizer
from app.market_data.registry import InstrumentDescriptor
from app.market_data.live_cash_future_common import LiveCashFutureCommonRunner


def descriptor(token="101", exchange="NSE", segment="EQ", instrument_type="equity"):
    key = InstrumentKey(exchange, segment, token)
    return InstrumentDescriptor(
        key=key,
        symbol="ABC",
        instrument_type=instrument_type,
        exchange=exchange,
        segment=segment,
        lot_size=1,
        tick_size=0.05,
    )


def test_angel_one_payload_normalizes_price_units_and_metadata():
    record = AngelOneTickNormalizer().normalize(
        descriptor(),
        {
            "token": "101",
            "exchange_timestamp": 1720000000123,
            "last_traded_price": 12345,
            "open_price_of_the_day": 12000,
            "high_price_of_the_day": 12500,
            "low_price_of_the_day": 11900,
            "closed_price": 12100,
            "volume_trade_for_the_day": 77,
            "open_interest": 88,
            "best_buy_data": [{"price": 12340, "quantity": 10}],
            "best_sell_data": [{"price": 12350, "quantity": 12}],
        },
    )
    assert isinstance(record, MarketDataRecord)
    assert record.instrument_type is InstrumentType.EQUITY
    assert record.timestamp_ns == 1720000000123 * 1_000_000
    assert record.ltp == 123.45
    assert record.bid == 123.40
    assert record.ask == 123.50
    assert record.bid_qty == 10.0
    assert record.ask_qty == 12.0
    assert record.volume == 77
    assert record.oi == 88
    assert record.payload["token"] == "101"


def test_normalized_callback_uses_exact_exchange_group():
    from app.market_data.common_websocket import CommonWebSocketManager, SocketGroup

    class FakeSocket:
        instances = []
        def __init__(self):
            self.connect_calls = []
            self.closed = False
            FakeSocket.instances.append(self)
        def connect(self, **kwargs):
            self.connect_calls.append(kwargs)
        def subscribe(self, tokens, mode=None):
            pass
        def subscribe_groups(self, groups, mode=None):
            pass
        def unsubscribe_groups(self, groups):
            pass
        def close(self):
            self.closed = True

    registry = __import__("app.market_data.registry", fromlist=["InstrumentRegistry"]).InstrumentRegistry()
    nse = descriptor("101", "NSE", "EQ")
    nfo = descriptor("101", "NFO", "DERIVATIVES", "future")
    registry.register_many([nse, nfo])
    manager = CommonWebSocketManager(registry, socket_factory=FakeSocket)
    seen = []
    manager.register_normalized_callback("nse", lambda record: seen.append(record))
    manager.register_normalized_callback("nfo", lambda record: seen.append(record))
    manager.subscribe("nse", [nse.key])
    manager.subscribe("nfo", [nfo.key])

    manager._on_data(SocketGroup(1, 0), {"token": "101", "exchange_type": 1, "last_traded_price": 10000})
    assert len(seen) == 1
    assert seen[0].instrument.exchange == "NSE"
    assert seen[0].ltp == 100.0
    manager.close()


class FakeCatalog:
    def __init__(self):
        self.batches = []
    def ingest_if_absent_batch(self, records, *, ingested_at_ns=0):
        batch = list(records)
        self.batches.append(batch)
        return len(batch)


def test_bounded_ingestor_batches_and_flushes_without_unbounded_memory():
    catalog = FakeCatalog()
    ingestor = BoundedMarketDataIngestor(
        catalog, max_queue=2, batch_size=2, flush_seconds=0.05
    )
    for token in ("1", "2", "3"):
        ingestor.submit(
            MarketDataRecord(
                instrument=InstrumentKey("NSE", "EQ", token),
                symbol=f"S{token}",
                instrument_type=InstrumentType.EQUITY,
                timestamp_ns=int(token),
                ltp=100.0,
            )
        )
    ingestor.close(timeout=2)
    assert sum(len(batch) for batch in catalog.batches) == 3
    assert all(r.source == "angelone-live-1s" for batch in catalog.batches for r in batch)
    assert ingestor.snapshot()["queue_capacity"] == 2


def test_non_finite_depth_does_not_drop_other_valid_market_fields():
    record = AngelOneTickNormalizer().normalize(
        descriptor(),
        {
            "token": "101",
            "last_traded_price": 12345,
            "volume_trade_for_the_day": 77,
            "open_interest": 88,
            "best_buy_data": [{"price": float("nan"), "quantity": float("inf")}],
            "best_sell_data": [{"price": "not-a-price", "quantity": -1}],
        },
    )
    assert record.ltp == 123.45
    assert record.bid is None
    assert record.ask is None
    assert record.bid_qty is None
    assert record.ask_qty is None
    assert record.volume == 77
    assert record.oi == 88


def test_crossed_depth_does_not_discard_valid_ltp_and_oi():
    record = AngelOneTickNormalizer().normalize(
        descriptor(),
        {
            "token": "101",
            "last_traded_price": 12345,
            "open_interest": 88,
            "best_buy_data": [{"price": 12400, "quantity": 10}],
            "best_sell_data": [{"price": 12300, "quantity": 12}],
        },
    )
    assert record.ltp == 123.45
    assert record.bid is None
    assert record.ask is None
    assert record.oi == 88


def test_price_scale_rejects_non_finite_values():
    for value in (float("nan"), float("inf"), 0, -1):
        try:
            AngelOneTickNormalizer(price_scale=value)
        except ValueError as exc:
            assert "finite and positive" in str(exc)
        else:
            raise AssertionError(f"invalid price scale accepted: {value}")


def test_depth_skips_empty_levels_and_uses_first_valid_price():
    record = AngelOneTickNormalizer().normalize(
        descriptor(),
        {
            "token": "101",
            "last_traded_price": 12345,
            "best_5_buy_data": [
                {"flag": 0, "no of orders": 0, "price": 0, "quantity": 0},
                {"flag": 1, "no of orders": 2, "price": 10100, "quantity": 7},
            ],
            "best_5_sell_data": [
                {"flag": 0, "no of orders": 0, "price": 0, "quantity": 0},
                {"flag": 1, "no of orders": 3, "price": 10150, "quantity": 9},
            ],
        },
    )
    assert record.bid == 101.0
    assert record.ask == 101.5
    assert record.bid_qty == 7.0
    assert record.ask_qty == 9.0


def test_depth_keeps_quote_missing_when_every_level_is_empty():
    record = AngelOneTickNormalizer().normalize(
        descriptor(),
        {
            "token": "101",
            "last_traded_price": 12345,
            "best_5_buy_data": [{"price": 0, "quantity": 0}, {"price": None, "quantity": 0}],
            "best_5_sell_data": [{"price": 0, "quantity": 0}],
        },
    )
    assert record.ltp == 123.45
    assert record.bid is None
    assert record.ask is None



def test_cash_future_quote_side_telemetry_counts_missing_sides_without_raw_frames():
    runner = LiveCashFutureCommonRunner(
        "unused.sqlite", instrument_master=object(), manager=object(), scanner=object()
    )
    record = AngelOneTickNormalizer().normalize(
        descriptor(),
        {
            "token": "101",
            "last_traded_price": 12345,
            "best_5_buy_data": [{"price": 12300, "quantity": 4}],
            "best_5_sell_data": [{"price": 0, "quantity": 0}],
        },
    )
    runner._record_quote_side_quality(record, {"leg": "CASH"})
    runner._record_quote_side_quality(record, {"leg": "FUTURE"})
    stats = runner.snapshot()["quote_side_counts"]
    assert stats == {
        "cash_bid_missing": 0,
        "cash_ask_missing": 1,
        "future_bid_missing": 0,
        "future_ask_missing": 1,
    }
    source_stats = runner.snapshot()["quote_side_source_counts"]
    assert source_stats["cash_ask_source_missing"] == 1
    assert source_stats["future_ask_source_missing"] == 1
    assert source_stats["cash_bid_source_missing"] == 0
    assert source_stats["cash_bid_normalization_missing"] == 0
    assert not hasattr(runner, "_raw_payloads")


def test_cash_future_quote_side_telemetry_identifies_normalization_gap():
    runner = LiveCashFutureCommonRunner(
        "unused.sqlite", instrument_master=object(), manager=object(), scanner=object()
    )
    record = AngelOneTickNormalizer().normalize(
        descriptor(),
        {
            "token": "101",
            "last_traded_price": 12345,
            "best_5_buy_data": [{"price": 12500, "quantity": 4}],
            "best_5_sell_data": [{"price": 12300, "quantity": 5}],
        },
    )
    assert record.bid is None and record.ask is None  # crossed book is rejected
    runner._record_quote_side_quality(record, {"leg": "CASH"})
    stats = runner.snapshot()["quote_side_source_counts"]
    assert stats["cash_bid_normalization_missing"] == 1
    assert stats["cash_ask_normalization_missing"] == 1
    assert stats["cash_bid_source_missing"] == 0
    assert stats["cash_ask_source_missing"] == 0


def test_cash_future_source_timestamp_requires_original_provider_timestamp():
    has_timestamp = LiveCashFutureCommonRunner._has_provider_timestamp

    assert has_timestamp({"exchange_timestamp": 1720000000123}) is True
    assert has_timestamp({"exchange_timestamp_ns": 1720000000123000000}) is True
    assert has_timestamp({"timestamp": "1720000000123"}) is True
    assert has_timestamp({}) is False
    assert has_timestamp({"exchange_timestamp": None, "timestamp": "invalid"}) is False
    assert has_timestamp({"exchange_timestamp": float("nan")}) is False

    runner = LiveCashFutureCommonRunner(
        "unused.sqlite", instrument_master=object(), manager=object(), scanner=object()
    )
    meta = {"leg": "CASH", "underlying": "ABC", "contract_month": "CASH"}
    record = AngelOneTickNormalizer().normalize(
        descriptor(),
        {"token": "101", "last_traded_price": 12345},
    )
    missing = runner._record_payload(record, meta)
    assert missing["timestamp_ns"] > 0  # canonical fallback remains available
    assert missing["source_timestamp_ns"] == 0  # but is not mislabeled as exchange time

    raw_timestamp = 1720000000123
    record_with_timestamp = AngelOneTickNormalizer().normalize(
        descriptor(),
        {"token": "101", "exchange_timestamp": raw_timestamp, "last_traded_price": 12345},
    )
    present = runner._record_payload(record_with_timestamp, meta)
    assert present["source_timestamp_ns"] == raw_timestamp * 1_000_000



def test_normalizer_converts_epoch_seconds_without_marking_them_stale():
    seconds = 1_780_000_000
    record = AngelOneTickNormalizer().normalize(
        descriptor(),
        {"token": "101", "timestamp": seconds, "last_traded_price": 12345},
    )
    assert record.timestamp_ns == seconds * 1_000_000_000


def test_normalizer_converts_epoch_microseconds_and_preserves_explicit_nanoseconds():
    micros = 1_780_000_000_123_456
    micro_record = AngelOneTickNormalizer().normalize(
        descriptor(),
        {"token": "101", "feed_time": micros, "last_traded_price": 12345},
    )
    explicit_ns = 1_780_000_000_123_456_789
    ns_record = AngelOneTickNormalizer().normalize(
        descriptor(),
        {"token": "101", "timestamp_ns": explicit_ns, "last_traded_price": 12345},
    )
    assert micro_record.timestamp_ns == micros * 1_000
    assert ns_record.timestamp_ns == explicit_ns
