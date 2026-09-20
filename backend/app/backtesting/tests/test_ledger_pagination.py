import pytest

from app.backtesting.ledger import BacktestLedger, LedgerRecord


def _seed() -> BacktestLedger:
    ledger = BacktestLedger()
    ledger.start_run("page-run", "strategy", "1", 1000)
    ledger.append_batch(
        LedgerRecord("page-run", "EQUITY", index, {"value": index})
        for index in range(5)
    )
    ledger.append(LedgerRecord("page-run", "TRADE", 10, {"value": 10}))
    return ledger


def test_record_page_uses_durable_id_cursor_and_preserves_record_order():
    ledger = _seed()

    first = ledger.record_page("page-run", "EQUITY", limit=2)
    assert [record.payload["value"] for record in first.records] == [0, 1]
    assert first.next_cursor is not None

    second = ledger.record_page(
        "page-run", "EQUITY", limit=2, after_id=first.next_cursor
    )
    assert [record.payload["value"] for record in second.records] == [2, 3]
    assert second.next_cursor is not None

    third = ledger.record_page(
        "page-run", "EQUITY", limit=2, after_id=second.next_cursor
    )
    assert [record.payload["value"] for record in third.records] == [4]
    assert third.next_cursor is None
    ledger.close()


def test_record_page_filters_record_type_without_crossing_cursor():
    ledger = _seed()

    page = ledger.record_page("page-run", "TRADE", limit=10)
    assert [record.payload["value"] for record in page.records] == [10]
    assert page.next_cursor is None
    ledger.close()


def test_record_page_validates_limit_and_cursor():
    ledger = _seed()

    with pytest.raises(ValueError, match="limit must be positive"):
        ledger.record_page("page-run", limit=0)

    with pytest.raises(ValueError, match="after_id must be non-negative"):
        ledger.record_page("page-run", after_id=-1)

    ledger.close()
