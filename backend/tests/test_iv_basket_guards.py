from datetime import date
import hashlib

import pytest
from sqlalchemy import create_engine, delete, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.iv_basket_guards import install_iv_basket_immutability_guards
from app.models.iv_constituent_basket_version import IVConstituentBasketVersion
from app.scanner.iv_skew_flip.weight_import import parse_official_weight_basket
from app.scanner.iv_skew_flip.weight_store import persist_validated_basket_version


CSV = b"symbol,weight\\nAAA,60\\nBBB,40\\n"


@pytest.fixture
def guarded_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    IVConstituentBasketVersion.__table__.create(engine)
    install_iv_basket_immutability_guards(engine)
    # Startup may run repeatedly; trigger installation must be idempotent.
    install_iv_basket_immutability_guards(engine)
    yield engine
    engine.dispose()


def _insert_version(session):
    basket = parse_official_weight_basket(
        CSV,
        filename="weights.csv",
        underlying="NIFTY",
        effective_date="2026-11-01",
        source_name="Official weights",
        source_url="https://www.niftyindices.com/reports/monthly",
        expected_symbols=("AAA", "BBB"),
        as_of=date(2026, 11, 8),
    )
    return persist_validated_basket_version(
        session,
        basket,
        {"AAA": "101", "BBB": "202"},
        roster_source_name="Official roster",
        roster_source_url="https://www.niftyindices.com/indices/equity/broad-based-indices",
        roster_sha256=hashlib.sha256(b"roster").hexdigest(),
    )


def test_database_trigger_blocks_bulk_update_that_bypasses_orm_events(guarded_db):
    with Session(guarded_db) as session:
        record = _insert_version(session)
        with pytest.raises(DBAPIError, match="append-only"):
            session.execute(
                update(IVConstituentBasketVersion)
                .where(IVConstituentBasketVersion.id == record.id)
                .values(status="REVOKED")
            )
        session.rollback()
        stored = session.scalar(
            select(IVConstituentBasketVersion).where(IVConstituentBasketVersion.id == record.id)
        )
        assert stored.status == "VALID"


def test_database_trigger_blocks_bulk_delete_that_bypasses_orm_events(guarded_db):
    with Session(guarded_db) as session:
        record = _insert_version(session)
        with pytest.raises(DBAPIError, match="append-only"):
            session.execute(
                delete(IVConstituentBasketVersion).where(
                    IVConstituentBasketVersion.id == record.id
                )
            )
        session.rollback()
        assert session.scalar(select(IVConstituentBasketVersion.id)) == record.id
