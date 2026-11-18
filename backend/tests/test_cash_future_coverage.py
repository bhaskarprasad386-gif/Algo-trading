from datetime import datetime, timedelta

from app.scanner.cash_future_coverage import build_cash_future_coverage_report
from app.scanner.cash_future_history import CashFutureHistoryPoint


def point(ts, month="SEP"):
    return CashFutureHistoryPoint(
        timestamp=ts,
        symbol="ABC",
        contract_month=month,
        cash_price=100.0,
        future_price=105.0,
        gap=5.0,
        gap_pct=5.0,
        lot_size=100,
        margin_required=10000.0,
    )


def test_coverage_is_complete_per_contract_when_expected_timestamps_exist():
    start = datetime(2026, 9, 1, 10, 0)
    expected = [start + timedelta(minutes=i) for i in range(3)]
    report = build_cash_future_coverage_report(
        [point(expected[2]), point(expected[0]), point(expected[1], month="OCT")],
        expected_timestamps=expected,
    )
    assert report.status == "INCOMPLETE"
    assert [(item.contract_month, item.count, item.expected_count) for item in report.contracts] == [
        ("OCT", 1, 3),
        ("SEP", 2, 3),
    ]
    sep = next(item for item in report.contracts if item.contract_month == "SEP")
    assert sep.missing_timestamps == (expected[1],)


def test_coverage_ready_when_every_contract_has_expected_history():
    start = datetime(2026, 9, 1, 10, 0)
    expected = [start + timedelta(minutes=i) for i in range(2)]
    report = build_cash_future_coverage_report(
        [point(expected[1], "SEP"), point(expected[0], "SEP"), point(expected[0], "OCT"), point(expected[1], "OCT")],
        expected_timestamps=expected,
    )
    assert report.status == "READY"
    assert report.complete is True
    assert all(item.missing_timestamps == () for item in report.contracts)


def test_coverage_without_expected_timestamps_does_not_assume_overnight_cadence():
    first = datetime(2026, 9, 1, 15, 29)
    last = datetime(2026, 9, 2, 9, 15)
    report = build_cash_future_coverage_report([point(first), point(last)])
    assert report.status == "READY"
    assert report.contracts[0].first_timestamp == first
    assert report.contracts[0].last_timestamp == last
    assert report.contracts[0].expected_count is None
