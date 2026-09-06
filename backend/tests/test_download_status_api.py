from app.backtesting.download_status_api import download_status_payload
from app.backtesting.historical_download_status import (
    DownloadChunkStatus,
    HistoricalDownloadStatusStore,
)


def test_download_status_payload_is_json_safe():
    store = HistoricalDownloadStatusStore()
    store.create_job(
        job_id="api-1", mode="BOTH", timeframe="1m", spot_instrument="NSE:SPOT",
        exchange="NFO", underlying="ABC", start_ns=0, end_ns=60,
    )
    store.upsert_chunk(
        DownloadChunkStatus(
            job_id="api-1", sequence=0, instrument="NSE:SPOT", start_ns=0, end_ns=60,
            status="COMPLETE", attempts=1,
        )
    )
    payload = download_status_payload(store, "api-1")
    assert payload["job"]["status"] == "QUEUED"
    assert payload["chunks"][0]["status"] == "COMPLETE"
    store.close()


def test_download_status_payload_missing_job_fails_closed():
    store = HistoricalDownloadStatusStore()
    try:
        download_status_payload(store, "missing")
    except KeyError as exc:
        assert exc.args == ("missing",)
    else:
        raise AssertionError("missing job must raise KeyError")
    store.close()
