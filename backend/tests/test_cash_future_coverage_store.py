from datetime import datetime

from app.scanner.cash_future_coverage_store import build_persisted_cash_future_coverage, iter_persisted_cash_future_points
from app.scanner.cash_future_history import CashFutureHistoryPoint
from app.scanner.cash_future_history_store import save_history_points


def make_point(ts: int, contract: str = "2026-09"):
    return CashFutureHistoryPoint(
        timestamp=datetime.fromtimestamp(ts),
        symbol="ABC",
        contract_month=contract,
        cash_price=100.0,
        future_price=102.0,
        gap=2.0,
        gap_pct=2.0,
        lot_size=10,
        margin_required=1000.0,
    )


def test_persisted_coverage_streams_and_keeps_contracts_separate(db_session):
    save_history_points(db_session, [make_point(1), make_point(2), make_point(1, "2026-10")])

    points = list(iter_persisted_cash_future_points(db_session, page_size=1))
    assert [(p.contract_month, p.timestamp) for p in points] == [
        ("2026-09", datetime.fromtimestamp(1)),
        ("2026-09", datetime.fromtimestamp(2)),
        ("2026-10", datetime.fromtimestamp(1)),
    ]

    report = build_persisted_cash_future_coverage(db_session)
    assert report.status == "READY"
    assert [(c.contract_month, c.count) for c in report.contracts] == [
        ("2026-09", 2),
        ("2026-10", 1),
    ]


def test_persisted_coverage_detects_authoritative_missing_timestamp(db_session):
    save_history_points(db_session, [make_point(1), make_point(3)])
    expected = (datetime.fromtimestamp(1), datetime.fromtimestamp(2), datetime.fromtimestamp(3))

    report = build_persisted_cash_future_coverage(db_session, expected_timestamps=expected)
    assert report.status == "INCOMPLETE"
    assert report.contracts[0].missing_timestamps == (datetime.fromtimestamp(2),)
