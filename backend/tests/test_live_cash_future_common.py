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
    assert len(descriptors) == 4
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
