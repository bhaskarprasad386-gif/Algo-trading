from app.backtesting.angelone_historical import AngelOneHistoricalSource
from app.backtesting.historical_catalog import HistoricalCatalog
from app.backtesting.historical_ingest import HistoricalFetchRequest, HistoricalIngestionService


class FakeClient:
    def __init__(self):
        self.requests = []

    def getCandleData(self, params):
        self.requests.append(params)
        return {
            "status": True,
            "data": [
                ["2026-01-05 09:15", 100, 101, 99, 100.5, 1000, 250],
                ["2026-01-05 09:16", 100.5, 102, 100, 101.5, 1200, 275],
            ],
        }


class FakeAuth:
    def __init__(self, client):
        self.client = client

    def get_client(self):
        return self.client


class FakeLimiter:
    def __init__(self):
        self.calls = 0

    def acquire(self):
        self.calls += 1


def test_angelone_source_streams_real_response_shape_into_sqlite_catalog():
    client = FakeClient()
    limiter = FakeLimiter()
    source = AngelOneHistoricalSource(
        auth=FakeAuth(client),
        limiter=limiter,
        chunk_days=30,
    )
    catalog = HistoricalCatalog()
    ingestion = HistoricalIngestionService(catalog)

    start_ns = 1767604500 * 1_000_000_000
    end_ns = start_ns + 60 * 1_000_000_000
    request = HistoricalFetchRequest(
        source="angelone",
        instrument="NSE:3045:SBIN-EQ",
        timeframe="1m",
        start_ns=start_ns,
        end_ns=end_ns,
    )

    result = ingestion.sync_streaming(source, request, batch_size=1)
    rows = catalog.records(
        source="angelone",
        instrument="NSE:3045:SBIN-EQ",
        timeframe="1m",
    )

    assert result.fetched == 2
    assert result.inserted == 2
    assert len(rows) == 2
    assert rows[0].payload["open"] == 100.0
    assert rows[1].payload["open_interest"] == 275.0
    assert limiter.calls == 1
    assert len(client.requests) == 1
    assert client.requests[0]["exchange"] == "NSE"
    assert client.requests[0]["symboltoken"] == "3045"
    assert client.requests[0]["interval"] == "ONE_MINUTE"

    catalog.close()
