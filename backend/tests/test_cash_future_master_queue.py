from datetime import date, datetime, timezone
from app.backtesting.cash_future_master_queue import build_master_backed_cash_future_queue
from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord

def test_master_backed_queue_resolves_cash_and_future():
    catalog = ContractMasterCatalog()
    catalog.upsert_snapshot(date(2026, 9, 1), [ContractRecord("NFO", "ABC26OCT", "999", date(2026, 10, 29), "STOCK_FUTURE", "ABC", 125)])
    rows = [{"exch_seg": "NSE", "symbol": "ABC-EQ", "name": "ABC", "token": "123", "instrumenttype": ""}]
    queue = build_master_backed_cash_future_queue(master_rows=rows, catalog=catalog, exchange="NFO", underlying="ABC", start=datetime(2026, 9, 10, 9, 15, tzinfo=timezone.utc), end=datetime(2026, 9, 10, 15, 30, tzinfo=timezone.utc), mode="CURRENT")
    assert queue.spot.instrument == "NSE:123:ABC-EQ"
    assert [item.request.instrument for item in queue.futures] == ["NFO:999:ABC26OCT"]
    catalog.close()
