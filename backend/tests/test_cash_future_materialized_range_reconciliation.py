from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.backtesting.cash_future_universe_pipeline import _request_has_materialized_rows
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


def test_materialized_range_rejects_rows_only_inside_requested_range():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    start = datetime(2026, 10, 1, 9, 15)
    end = start + timedelta(minutes=10)

    with Session(engine) as db:
        db.add(_row(start + timedelta(minutes=5)))
        db.commit()
        request = type("Request", (), {"start_ns": int(start.timestamp() * 1_000_000_000), "end_ns": int(end.timestamp() * 1_000_000_000)})()
        assert _request_has_materialized_rows(
            db, symbol="ABC", contract_month="2026-10", request=request
        ) is False


def test_materialized_range_accepts_rows_spanning_requested_endpoints():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    start = datetime(2026, 10, 1, 9, 15)
    end = start + timedelta(minutes=10)

    with Session(engine) as db:
        db.add_all((_row(start), _row(end)))
        db.commit()
        request = type("Request", (), {"start_ns": int(start.timestamp() * 1_000_000_000), "end_ns": int(end.timestamp() * 1_000_000_000)})()
        assert _request_has_materialized_rows(
            db, symbol="ABC", contract_month="2026-10", request=request
        ) is True
