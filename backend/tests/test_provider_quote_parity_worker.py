from datetime import date
from decimal import Decimal
from time import monotonic, sleep

from app.market_data.provider_contracts import CanonicalContractKey
from app.market_data.provider_quote_parity_store import SQLiteQuoteParityStore
from app.market_data.provider_quote_parity_worker import (
    BoundedQuoteParityWorker, QuoteParityEnvelope,
)
from app.market_data.provider_quote_quality import ProviderQuoteObservation

EXCHANGE_NOW = 1_800_000_000_000_000_000


def _contract():
    return CanonicalContractKey(
        exchange="NFO", segment="OPT", underlying="NIFTY",
        instrument_type="OPTION", expiry=date(2026, 11, 19),
        strike=Decimal("22000"), option_type="CE",
    )


def _envelope(*, received=100, left_generation=1, right_generation=1,
              exchange_left=EXCHANGE_NOW - 1_000_000_000,
              exchange_right=EXCHANGE_NOW - 900_000_000):
    def quote(provider, generation, timestamp, bid):
        return ProviderQuoteObservation(
            provider=provider, contract=_contract(), generation=generation,
            authenticated=True, connection_current=True,
            exchange_timestamp_ns=timestamp, received_monotonic_ns=received,
            bid=bid, ask="100.10", bid_size=20, ask_size=25,
            depth_complete=True,
        )
    return QuoteParityEnvelope(
        left=quote("angel_one", left_generation, exchange_left, "100.00"),
        right=quote("shoonya", right_generation, exchange_right, "100.05"),
        received_monotonic_ns=received,
        observed_at_utc_ns=EXCHANGE_NOW + received,
        now_exchange_timestamp_ns=EXCHANGE_NOW,
        max_age_ns=5_000_000_000,
    )


def _store(tmp_path, name="worker.sqlite3"):
    return SQLiteQuoteParityStore(tmp_path / name)


def _wait_for(predicate, timeout=2.0):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if predicate():
            return True
        sleep(0.01)
    return predicate()


def test_worker_persists_submitted_pair_off_submit_path(tmp_path):
    store = _store(tmp_path)
    worker = BoundedQuoteParityWorker(store, capacity=4)
    worker.activate_generation("angel_one", 1)
    worker.activate_generation("shoonya", 1)
    worker.start()
    assert worker.submit(_envelope())
    assert _wait_for(lambda: worker.snapshot()["persisted"] == 1)
    snapshot = worker.snapshot()
    assert snapshot["processed"] == 1
    assert snapshot["queue_depth"] == 0
    assert worker.stop(drain=True)
    rows = store.latest(contract_key=store._contract_key(
        __import__("app.market_data.provider_quote_parity", fromlist=["analyze_quote_parity"]).analyze_quote_parity(
            _envelope().left, _envelope().right,
            now_monotonic_ns=100, now_exchange_timestamp_ns=EXCHANGE_NOW,
            max_age_ns=5_000_000_000,
        )
    ))
    assert len(rows) == 1
    store.close()


def test_worker_rejects_stale_generation_before_queueing(tmp_path):
    store = _store(tmp_path)
    worker = BoundedQuoteParityWorker(store)
    worker.activate_generation("angel_one", 2)
    worker.activate_generation("shoonya", 1)
    assert worker.submit(_envelope(left_generation=1)) is False
    assert worker.snapshot()["stale_generation"] == 1
    worker.stop(drain=False)
    store.close()


def test_worker_rejects_duplicate_exchange_timestamp_pair(tmp_path):
    store = _store(tmp_path)
    worker = BoundedQuoteParityWorker(store)
    assert worker.submit(_envelope(received=100))
    assert worker.submit(_envelope(received=101)) is False
    assert worker.snapshot()["duplicates"] == 1
    worker.stop(drain=False)
    store.close()


def test_worker_rejects_out_of_order_receive_time(tmp_path):
    store = _store(tmp_path)
    worker = BoundedQuoteParityWorker(store)
    assert worker.submit(_envelope(received=200))
    assert worker.submit(_envelope(received=199)) is False
    assert worker.snapshot()["out_of_order"] == 1
    worker.stop(drain=False)
    store.close()


def test_queue_overflow_latches_integrity_fault_and_stops_accepting(tmp_path):
    store = _store(tmp_path)
    worker = BoundedQuoteParityWorker(store, capacity=1)
    assert worker.submit(_envelope(received=100))
    assert worker.submit(_envelope(
        received=101,
        exchange_left=EXCHANGE_NOW - 800_000_000,
        exchange_right=EXCHANGE_NOW - 700_000_000,
    )) is False
    snapshot = worker.snapshot()
    assert snapshot["integrity_fault"] is True
    assert snapshot["fault_reason"] == "queue_overflow"
    assert snapshot["queue_overflow"] == 1
    assert worker.submit(_envelope(received=102)) is False
    worker.stop(drain=False)
    assert worker.snapshot()["discarded_on_shutdown"] == 1
    store.close()


def test_dedupe_memory_is_bounded(tmp_path):
    store = _store(tmp_path)
    worker = BoundedQuoteParityWorker(store, capacity=1)
    for index in range(1100):
        assert worker.submit(_envelope(
            received=1000 + index,
            exchange_left=EXCHANGE_NOW - 1_000_000_000 - index,
            exchange_right=EXCHANGE_NOW - 900_000_000 - index,
        ))
        # Drain the single pending item synchronously by using a started worker
        # is intentionally avoided; bounded dedupe itself is the assertion.
        if worker.snapshot()["queue_depth"] == 1:
            worker._queue.get_nowait()
            worker._queue.task_done()
    snapshot = worker.snapshot()
    assert snapshot["dedupe_entries"] <= snapshot["dedupe_capacity"]
    worker.stop(drain=False)
    store.close()


def test_envelope_rejects_different_contract_identity():
    left = _envelope()
    different = CanonicalContractKey(
        exchange="NFO", segment="OPT", underlying="NIFTY",
        instrument_type="OPTION", expiry=date(2026, 11, 19),
        strike=Decimal("22100"), option_type="CE",
    )
    bad_right = ProviderQuoteObservation(
        provider="shoonya", contract=different, generation=1,
        authenticated=True, connection_current=True,
        exchange_timestamp_ns=EXCHANGE_NOW, received_monotonic_ns=100,
        bid="100", ask="100.10", bid_size=10, ask_size=10,
        depth_complete=True,
    )
    import pytest
    with pytest.raises(ValueError, match="same canonical"):
        QuoteParityEnvelope(
            left=left.left, right=bad_right,
            received_monotonic_ns=100, observed_at_utc_ns=EXCHANGE_NOW,
            now_exchange_timestamp_ns=EXCHANGE_NOW, max_age_ns=1_000_000_000,
        )
