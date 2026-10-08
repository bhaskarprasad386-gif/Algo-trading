from app.market_data.contracts import InstrumentKey, InstrumentType, MarketDataRecord
from app.market_data.ingestion import BoundedMarketDataIngestor
from app.market_data.normalizer import AngelOneTickNormalizer
from app.market_data.registry import InstrumentDescriptor


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


def test_angel_one_best5_depth_normalizes_quotes_and_quantities():
    record = AngelOneTickNormalizer().normalize(
        descriptor(),
        {
            "token": "101",
            "last_traded_price": 2500000,
            "best_5_buy_data": [{"price": 2499900, "quantity": 10}],
            "best_5_sell_data": [{"price": 2500100, "quantity": 12}],
        },
    )
    assert record.ltp == 25000.0
    assert record.bid == 24999.0
    assert record.ask == 25001.0
    assert record.bid_qty == 10.0
    assert record.ask_qty == 12.0


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
