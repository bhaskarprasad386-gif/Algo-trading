import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.brokers.real_owner_state import account_fingerprint, claim_authenticated_account, disable_owner_trading, reconcile_owner_state_on_startup
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


def test_concurrent_first_claim_cannot_replace_persisted_owner(db, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    real_commit = db.commit

    def simulate_concurrent_winner():
        # Simulate another request winning the unique owner_key insert after
        # this request observed an empty table but before its commit.
        db.rollback()
        db.add(
            BoxSpreadRealOwnerState(
                owner_key="box-spread-real-owner",
                broker_name="angel_one",
                account_fingerprint=account_fingerprint("angel_one", "CLIENT123"),
                connected=False,
                real_trading_enabled=False,
                kill_switch=True,
            )
        )
        real_commit()
        raise IntegrityError("insert", {}, RuntimeError("unique owner_key conflict"))

    monkeypatch.setattr(db, "commit", simulate_concurrent_winner)

    with pytest.raises(HTTPException) as raised:
        claim_authenticated_account(db, "angel_one", "CLIENT999")

    assert raised.value.status_code == 409
    db.expire_all()
    persisted = db.query(BoxSpreadRealOwnerState).one()
    assert persisted.account_fingerprint == account_fingerprint("angel_one", "CLIENT123")
    assert persisted.real_trading_enabled is False
    assert persisted.kill_switch is True


def test_connection_lifecycle_persists_state_and_never_arms_trading(db):
    from app.brokers.real_owner_state import set_owner_connection_state

    state = claim_authenticated_account(db, "angel_one", "CLIENT123")
    state.real_trading_enabled = True
    state.kill_switch = False
    db.commit()

    connected = set_owner_connection_state(db, True)
    assert connected.connected is True
    assert connected.real_trading_enabled is False
    assert connected.kill_switch is True

    disconnected = set_owner_connection_state(db, False)
    assert disconnected.connected is False
    assert disconnected.real_trading_enabled is False
    assert disconnected.kill_switch is True


def test_connection_lifecycle_fails_closed_without_owner_row(db):
    from app.brokers.real_owner_state import set_owner_connection_state

    with pytest.raises(HTTPException) as raised:
        set_owner_connection_state(db, True)
    assert raised.value.status_code == 503


def test_startup_reconciliation_clears_stale_connection_and_disarms(db):
    state = claim_authenticated_account(db, "angel_one", "CLIENT123")
    state.connected = True
    state.real_trading_enabled = True
    state.kill_switch = False
    db.commit()

    assert reconcile_owner_state_on_startup(db) is True

    db.expire_all()
    persisted = db.query(BoxSpreadRealOwnerState).one()
    assert persisted.id == state.id
    assert persisted.connected is False
    assert persisted.real_trading_enabled is False
    assert persisted.kill_switch is True


def test_startup_reconciliation_does_not_create_unclaimed_owner(db):
    assert reconcile_owner_state_on_startup(db) is False
    assert db.query(BoxSpreadRealOwnerState).count() == 0



def test_durable_kill_switch_disables_trading_without_changing_connection(db):
    state = claim_authenticated_account(db, "angel_one", "CLIENT123")
    state.connected = True
    state.real_trading_enabled = True
    state.kill_switch = False
    db.commit()

    disabled = disable_owner_trading(db)

    assert disabled.id == state.id
    assert disabled.connected is True
    assert disabled.real_trading_enabled is False
    assert disabled.kill_switch is True


def test_durable_kill_switch_fails_closed_without_owner_row(db):
    with pytest.raises(HTTPException) as raised:
        disable_owner_trading(db)
    assert raised.value.status_code == 503
