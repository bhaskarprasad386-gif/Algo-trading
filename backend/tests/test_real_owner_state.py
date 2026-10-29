import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.brokers.real_owner_state import account_fingerprint, claim_authenticated_account
from app.core.database import Base
from app.models import BoxSpreadRealOwnerState

TEST_SECRET = "test-secret-key-that-is-long-enough-for-owner-state"


@pytest.fixture
def db(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "SECRET_KEY", TEST_SECRET)
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def test_account_fingerprint_is_stable_and_keyed(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "SECRET_KEY", TEST_SECRET)
    first = account_fingerprint("angel_one", "CLIENT123")
    assert first == account_fingerprint(" ANGEL_ONE ", " CLIENT123 ")
    assert first != account_fingerprint("angel_one", "CLIENT124")
    assert "CLIENT123" not in first


def test_owner_binding_persists_only_fingerprint_and_starts_fail_closed(db):
    state = claim_authenticated_account(db, "angel_one", "CLIENT123")

    assert state.owner_key == "box-spread-real-owner"
    assert state.broker_name == "angel_one"
    assert state.account_fingerprint == account_fingerprint("angel_one", "CLIENT123")
    assert state.account_fingerprint != "CLIENT123"
    assert state.connected is False
    assert state.real_trading_enabled is False
    assert state.kill_switch is True


def test_same_authenticated_account_can_reconnect_without_arming(db):
    first = claim_authenticated_account(db, "angel_one", "CLIENT123")
    first.real_trading_enabled = True
    first.kill_switch = False
    db.commit()

    second = claim_authenticated_account(db, "angel_one", "CLIENT123")

    assert second.id == first.id
    assert second.real_trading_enabled is False
    assert second.kill_switch is True
    assert second.connected is False


def test_different_account_is_rejected_without_overwriting_owner_binding(db):
    original = claim_authenticated_account(db, "angel_one", "CLIENT123")

    with pytest.raises(HTTPException) as raised:
        claim_authenticated_account(db, "angel_one", "CLIENT999")

    assert raised.value.status_code == 409
    db.expire_all()
    persisted = db.query(BoxSpreadRealOwnerState).one()
    assert persisted.id == original.id
    assert persisted.account_fingerprint == account_fingerprint("angel_one", "CLIENT123")


def test_blank_account_identity_fails_closed(db):
    with pytest.raises(HTTPException) as raised:
        claim_authenticated_account(db, "angel_one", "  ")
    assert raised.value.status_code == 503


def test_fingerprint_fails_closed_with_weak_signing_key(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "SECRET_KEY", "weak")
    with pytest.raises(HTTPException) as raised:
        account_fingerprint("angel_one", "CLIENT123")
    assert raised.value.status_code == 503
