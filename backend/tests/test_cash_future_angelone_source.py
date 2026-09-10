from datetime import date, datetime

from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord
from app.backtesting.historical_catalog import HistoricalRecord
from app.scanner.cash_future_angelone_source import AngelOneCashFutureHistoricalSource


class FakeHistoricalSource:
    def __init__(self):
        self.requests = []

    def fetch(self, request):
        self.requests.append(request)
        if request.instrument.startswith("NSE:"):
            rows = [("2026-09-10T09:15:00+05:30", 100, 101, 99, 100, 1000)]
        else:
            rows = [("2026-09-10T09:15:00+05:30", 110, 111, 109, 110, 500)]
        for row in rows:
            timestamp = int(datetime.fromisoformat(row[0]).timestamp() * 1_000_000_000)
            yield HistoricalRecord(
                source="angelone", instrument=request.instrument, timeframe="1m",
                timestamp_ns=timestamp,
                payload={"close": row[4], "volume": row[5]},
            )


def test_fetch_resolves_cash_and_exact_future_and_synchronizes():
    catalog = ContractMasterCatalog()
    catalog.upsert_snapshot(
        date(2026, 9, 7),
        [ContractRecord("NFO", "ABC26OCT", "999", date(2026, 10, 29), "STOCK_FUTURE", "ABC", 125)],
    )
    source = FakeHistoricalSource()
    adapter = AngelOneCashFutureHistoricalSource(
        master_rows=[{"exch_seg": "NSE", "symbol": "ABC-EQ", "name": "ABC", "token": "123", "instrumenttype": ""}],
        contract_catalog=catalog,
        historical_source=source,
    )

    points = list(adapter.fetch(
        symbol="ABC", contract_month="2026-10",
        start=datetime.fromisoformat("2026-09-10T09:15:00+05:30"),
        end=datetime.fromisoformat("2026-09-10T15:30:00+05:30"),
    ))

    assert len(points) == 1
    assert points[0].cash_price == 100
    assert points[0].future_price == 110
    assert points[0].gap == 10
    assert points[0].lot_size == 125
    assert points[0].expiry_date == date(2026, 10, 29)
    assert points[0].margin_required == 0.0
    assert {request.instrument for request in source.requests} == {"NSE:123:ABC-EQ", "NFO:999:ABC26OCT"}
    catalog.close()
