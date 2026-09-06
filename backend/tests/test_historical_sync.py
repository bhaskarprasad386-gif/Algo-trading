from app.backtesting.historical_sync import build_chunked_plan


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
