from dataclasses import replace
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.cash_future_history import CashFutureHistory
from app.scanner.cash_future_history import CashFutureHistoryPoint
from app.scanner.cash_future_history_store import read_history, save_history_point


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_history_persists_and_reads_quote_liquidity_fields(db_session):
    point = CashFutureHistoryPoint(
        timestamp=datetime(2026, 9, 2, 10, 0),
        symbol="ABC",
        contract_month="CURRENT",
        cash_price=100.0,
        future_price=108.0,
        gap=8.0,
        gap_pct=8.0,
        lot_size=100,
        margin_required=25000.0,
        volume=5000,
        oi=20000,
        cash_bid=99.9,
        cash_ask=100.1,
        future_bid=107.9,
        future_ask=108.1,
        charges=50.0,
        funding_cost=10.0,
        net_profit=740.0,
        roi_pct=2.4,
    )

    saved = save_history_point(db_session, point)
    rows = read_history(db_session, "ABC", "CURRENT")

    assert saved.id is not None
    assert len(rows) == 1
    loaded = rows[0]
    assert loaded.volume == 5000
    assert loaded.oi == 20000
    assert loaded.cash_bid == 99.9
    assert loaded.cash_ask == 100.1
    assert loaded.future_bid == 107.9
    assert loaded.future_ask == 108.1


def test_history_upsert_keeps_one_row_for_same_identity(db_session):
    point = CashFutureHistoryPoint(
        timestamp=datetime(2026, 9, 2, 10, 0),
        symbol="ABC",
        contract_month="CURRENT",
        cash_price=100.0,
        future_price=108.0,
        gap=8.0,
        gap_pct=8.0,
        lot_size=100,
        margin_required=25000.0,
        volume=5000,
        oi=20000,
        cash_bid=99.9,
        cash_ask=100.1,
        future_bid=107.9,
        future_ask=108.1,
    )
    save_history_point(db_session, point)
    save_history_point(db_session, replace(point, future_price=109.0, gap=9.0))

    rows = read_history(db_session, "ABC", "CURRENT")
    assert len(rows) == 1
    assert rows[0].future_price == 109.0
    assert rows[0].gap == 9.0
    assert db_session.query(CashFutureHistory).count() == 1
