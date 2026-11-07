from datetime import date
import hashlib

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.models.iv_constituent_basket_version import IVConstituentBasketVersion
from app.scanner.iv_skew_flip.weight_import import parse_official_weight_basket
from app.scanner.iv_skew_flip.weight_store import persist_validated_basket_version


CSV = b"symbol,weight\nAAA,60\nBBB,40\n"


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    IVConstituentBasketVersion.__table__.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def basket():
    return parse_official_weight_basket(
        CSV,
        filename="weights.csv",
        underlying="NIFTY",
        effective_date="2026-11-01",
        source_name="Official index weights",
        source_url="https://www.niftyindices.com/reports/monthly",
        expected_symbols=("AAA", "BBB"),
        as_of=date(2026, 11, 8),
    )


def provenance():
    return {
        "roster_source_name": "Official constituent roster",
        "roster_source_url": "https://www.niftyindices.com/indices/equity/broad-based-indices",
        "roster_sha256": hashlib.sha256(b"authoritative roster").hexdigest(),
    }


def test_persists_immutable_valid_version_with_complete_provenance(db):
    record = persist_validated_basket_version(
        db,
        basket(),
        {"AAA": "101", "BBB": "202"},
        created_by_user_id=17,
        **provenance(),
    )
    assert record.id is not None
    assert record.underlying == "NIFTY"
    assert record.status == "VALID"
    assert record.created_by_user_id == 17
    assert record.source_sha256 == hashlib.sha256(CSV).hexdigest()
    assert record.roster_sha256 == provenance()["roster_sha256"]
    assert '"symbol":"AAA"' in record.components_json
    assert '"BBB":"202"' in record.token_mapping_json


def test_rejects_incomplete_or_duplicate_token_mapping_without_writing(db):
    with pytest.raises(ValueError, match="missing NSE cash"):
        persist_validated_basket_version(
            db, basket(), {"AAA": "101"}, **provenance()
        )
    with pytest.raises(ValueError, match="duplicate NSE cash"):
        persist_validated_basket_version(
            db, basket(), {"AAA": "101", "BBB": "101"}, **provenance()
        )
    assert db.scalars(select(IVConstituentBasketVersion)).all() == []


def test_requires_verified_roster_provenance(db):
    args = {**provenance(), "roster_source_url": "http://example.com/roster.csv"}
    with pytest.raises(ValueError, match="HTTPS"):
        persist_validated_basket_version(
            db, basket(), {"AAA": "101", "BBB": "202"}, **args
        )
    with pytest.raises(ValueError, match="SHA-256"):
        persist_validated_basket_version(
            db,
            basket(),
            {"AAA": "101", "BBB": "202"},
            **{**provenance(), "roster_sha256": "not-a-hash"},
        )
    assert db.scalars(select(IVConstituentBasketVersion)).all() == []


def test_duplicate_source_import_is_idempotently_rejected(db):
    args = (basket(), {"AAA": "101", "BBB": "202"})
    persist_validated_basket_version(db, *args, **provenance())
    with pytest.raises(ValueError, match="already imported"):
        persist_validated_basket_version(db, *args, **provenance())
    rows = db.scalars(select(IVConstituentBasketVersion)).all()
    assert len(rows) == 1
