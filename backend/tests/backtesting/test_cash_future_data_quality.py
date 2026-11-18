from app.backtesting.cash_future_data_quality import audit_cash_future_records
from app.backtesting.historical_catalog import HistoricalRecord


def _record(timestamp_ns: int, **payload: object) -> HistoricalRecord:
    return HistoricalRecord("test", "NSE:TEST", "1minute", timestamp_ns, payload)


def test_clean_cash_future_records_pass_quality_gate() -> None:
    report = audit_cash_future_records([_record(1, open=100, high=105, low=99, close=103, bid=102, ask=103, bid_qty=10, ask_qty=20)])
    assert report.clean
    assert report.records_checked == 1
    assert report.issue_count == 0
    assert report.require_clean() is report


def test_quality_gate_rejects_invalid_quotes_and_negative_depth() -> None:
    report = audit_cash_future_records([_record(1, open=100, high=105, low=99, close=103, bid=104, ask=103, cash_bid_qty=-1, future_ask_qty=-2)])
    assert not report.clean
    assert report.crossed_quotes == 1
    assert report.negative_depth == 2
    assert report.issue_count == 3

    try:
        report.require_clean()
    except ValueError as exc:
        assert "data-quality gate failed" in str(exc)
    else:
        raise AssertionError("expected data-quality gate failure")


def test_quality_gate_detects_duplicate_identity() -> None:
    record = _record(1, open=100, high=105, low=99, close=103)
    report = audit_cash_future_records([record, record])
    assert report.duplicate_timestamps == 1
    assert report.issue_count == 1
    assert not report.clean
