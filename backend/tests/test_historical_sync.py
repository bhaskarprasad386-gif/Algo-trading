from app.backtesting.historical_catalog import HistoricalCatalog
from app.backtesting.historical_ingest import HistoricalFetchRequest
from app.backtesting.historical_sync import build_chunked_plan, build_expected_event_plan


def test_chunked_plan_covers_range_without_overlap():
    plan = build_chunked_plan(
        source="angelone",
        instrument="NSE:3045:SBIN",
        timeframe="1m",
        start_ns=0,
        end_ns=9,
        chunk_ns=4,
    )
    assert [(r.start_ns, r.end_ns) for r in plan.requests] == [(0, 3), (4, 7), (8, 9)]


def test_chunked_plan_rejects_invalid_chunk():
    try:
        build_chunked_plan(
            source="angelone",
            instrument="NSE:3045",
            timeframe="1m",
            start_ns=0,
            end_ns=1,
            chunk_ns=0,
        )
    except ValueError as exc:
        assert "chunk_ns" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_expected_event_plan_repairs_only_authoritative_missing_events(tmp_path):
    catalog = HistoricalCatalog(tmp_path / "history.db")
    catalog.ingest(
        HistoricalFetchRequest("provider", "NSE:1:TEST", "tick", 10, 10),
        [(10, {"price": 100.0})],
    )

    plan = build_expected_event_plan(
        catalog,
        source="provider",
        instrument="NSE:1:TEST",
        timeframe="tick",
        expected_timestamps=[10, 20, 25, 40],
        max_request_ns=10,
    )

    assert [(r.start_ns, r.end_ns) for r in plan.requests] == [(20, 25), (40, 40)]
    assert all(isinstance(request, HistoricalFetchRequest) for request in plan.requests)


def test_expected_event_plan_does_not_invent_cadence_gaps(tmp_path):
    catalog = HistoricalCatalog(tmp_path / "history.db")

    plan = build_expected_event_plan(
        catalog,
        source="provider",
        instrument="NSE:1:TEST",
        timeframe="tick",
        expected_timestamps=[1, 100, 1000],
        max_request_ns=10,
    )

    assert [(r.start_ns, r.end_ns) for r in plan.requests] == [(1, 1), (100, 100), (1000, 1000)]
