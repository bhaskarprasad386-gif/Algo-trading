from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.backtesting.cash_future_universe_pipeline import _request_has_materialized_rows
from app.backtesting.session_gap_planner import SessionWindow
from app.core.database import Base
from app.models.cash_future_history import CashFutureHistory


def _row(timestamp: datetime) -> CashFutureHistory:
    return CashFutureHistory(
        symbol="ABC",
        contract_month="2026-10",
        timestamp=timestamp,
        cash_price=100.0,
        future_price=105.0,
        gap=5.0,
        gap_pct=5.0,
        lot_size=125,
        margin_required=5000.0,
    )


def _request(start: datetime, end: datetime):
    return type(
        "Request",
        (),
        {
            "start_ns": int(start.timestamp() * 1_000_000_000),
            "end_ns": int(end.timestamp() * 1_000_000_000),
        },
    )()


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def test_materialized_range_rejects_rows_only_inside_requested_range():
    engine = _db()
    start = datetime(2026, 10, 1, 9, 15)
    end = start + timedelta(minutes=10)

    with Session(engine) as db:
        db.add(_row(start + timedelta(minutes=5)))
        db.commit()
        assert _request_has_materialized_rows(
            db, symbol="ABC", contract_month="2026-10", request=_request(start, end)
        ) is False


def test_materialized_range_accepts_rows_spanning_requested_endpoints():
    engine = _db()
    start = datetime(2026, 10, 1, 9, 15)
    end = start + timedelta(minutes=10)

    with Session(engine) as db:
        db.add_all((_row(start), _row(end)))
        db.commit()
        assert _request_has_materialized_rows(
            db, symbol="ABC", contract_month="2026-10", request=_request(start, end)
        ) is True


def test_session_reconciliation_rejects_genuine_interior_one_minute_gap():
    engine = _db()
    start = datetime(2026, 10, 1, 9, 15)
    end = start + timedelta(minutes=3)
    sessions = (SessionWindow(int(start.timestamp() * 1e9), int(end.timestamp() * 1e9)),)

    with Session(engine) as db:
        db.add_all((_row(start), _row(start + timedelta(minutes=1)), _row(end)))
        db.commit()
        assert _request_has_materialized_rows(
            db,
            symbol="ABC",
            contract_month="2026-10",
            request=_request(start, end),
            sessions=sessions,
        ) is False


def test_session_reconciliation_does_not_bridge_friday_to_monday():
    engine = _db()
    friday_start = datetime(2026, 10, 2, 15, 29)
    friday_end = datetime(2026, 10, 2, 15, 30)
    monday_start = datetime(2026, 10, 5, 9, 15)
    sessions = (
        SessionWindow(int(friday_start.timestamp() * 1e9), int(friday_end.timestamp() * 1e9)),
        SessionWindow(int(monday_start.timestamp() * 1e9), int(monday_start.timestamp() * 1e9)),
    )

    with Session(engine) as db:
        db.add_all((_row(friday_start), _row(friday_end), _row(monday_start)))
        db.commit()
        assert _request_has_materialized_rows(
            db,
            symbol="ABC",
            contract_month="2026-10",
            request=_request(friday_start, monday_start),
            sessions=sessions,
        ) is True


def test_session_reconciliation_accepts_fully_covered_session():
    engine = _db()
    start = datetime(2026, 10, 1, 9, 15)
    end = start + timedelta(minutes=3)
    sessions = (SessionWindow(int(start.timestamp() * 1e9), int(end.timestamp() * 1e9)),)

    with Session(engine) as db:
        db.add_all(tuple(_row(start + timedelta(minutes=i)) for i in range(4)))
        db.commit()
        assert _request_has_materialized_rows(
            db,
            symbol="ABC",
            contract_month="2026-10",
            request=_request(start, end),
            sessions=sessions,
        ) is True
