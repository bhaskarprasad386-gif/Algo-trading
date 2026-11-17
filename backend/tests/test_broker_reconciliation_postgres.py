"""SQLite and PostgreSQL coverage for durable broker reconciliation evidence.

PostgreSQL cases use an isolated schema per test and the CI-provided
IV_TEST_POSTGRES_URL. No broker clients, credentials, network calls, or order
actions are involved.
"""
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.brokers.history import BrokerHistoryWindow
from app.core.database import Base
from app.execution.broker_reconciliation import BrokerOrderSnapshot, BrokerSnapshot
from app.execution.contracts import (
    ExecutionIntent,
    ExecutionLeg,
    ExecutionMode,
    IntentStatus,
    OrderSide,
)
from app.execution.persistence import create_intent, record_broker_order, transition_intent
from app.execution.reconciliation_service import (
    ReconciliationIdentityConflict,
    reconcile_persisted_history_window,
)
from app.models.broker_reconciliation import BrokerReconciliationRecord


@pytest.fixture(params=["sqlite", "postgres"])
def db(request):
    admin_engine = None
    schema = None
    if request.param == "sqlite":
        engine = create_engine("sqlite:///:memory:")
    else:
        url = os.getenv("IV_TEST_POSTGRES_URL")
        if not url:
            pytest.skip("IV_TEST_POSTGRES_URL is not configured")
        admin_engine = create_engine(url, pool_pre_ping=True)
        schema = f"broker_reconciliation_test_{uuid.uuid4().hex}"
        with admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(
            url,
            connect_args={"options": f"-csearch_path={schema}"},
            pool_pre_ping=True,
        )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
        if admin_engine is not None:
            with admin_engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            admin_engine.dispose()


def _persist_four_leg_intent(db, *, account_id="pg-acct-1", intent_id="pg-intent-1"):
    intent = ExecutionIntent(
        intent_id, account_id, f"{intent_id}-key", f"{intent_id}-corr",
        ExecutionMode.PAPER, created_at_ns=100,
    )
    legs = (
        ExecutionLeg("cb", "NFO:CE", OrderSide.BUY, 50, limit_price=100),
        ExecutionLeg("cs", "NFO:CE-HIGH", OrderSide.SELL, 50, limit_price=80),
        ExecutionLeg("pb", "NFO:PE", OrderSide.BUY, 50, limit_price=90),
        ExecutionLeg("ps", "NFO:PE-HIGH", OrderSide.SELL, 50, limit_price=70),
    )
    saved, _ = create_intent(db, intent, legs)
    version = saved.version
    for target in (
        IntentStatus.VALIDATING,
        IntentStatus.RISK_CHECK,
        IntentStatus.APPROVED,
        IntentStatus.RESERVED,
        IntentStatus.SUBMITTING,
    ):
        saved = transition_intent(
            db, account_id=account_id, intent_id=intent_id,
            expected_version=version, target=target,
        )
        version = saved.version
    for index, leg in enumerate(legs, start=1):
        record_broker_order(
            db, account_id=account_id, intent_id=intent_id,
            leg_id=leg.leg_id, broker="fyers",
            broker_order_id=f"{intent_id}-order-{index}",
            symbol=leg.instrument, side=leg.side.value,
            quantity=leg.quantity, accepted_at_ns=200 + index,
        )


def _history_window(*, account_id="pg-acct-1", intent_id="pg-intent-1",
                    provider_window="FYERS_CLOSED_DAY_REPORTS"):
    orders = (
        ("NFO:CE", "BUY"),
        ("NFO:CE-HIGH", "SELL"),
        ("NFO:PE", "BUY"),
        ("NFO:PE-HIGH", "SELL"),
    )
    snapshot = BrokerSnapshot(
        account_id=account_id,
        broker="FYERS",
        orders=tuple(
            BrokerOrderSnapshot(
                broker_order_id=f"{intent_id}-order-{index}",
                symbol=symbol,
                side=side,
                quantity=50,
                filled_quantity=0,
                raw_status="OPEN",
            )
            for index, (symbol, side) in enumerate(orders, start=1)
        ),
        trades=(),
        complete=True,
    )
    start = datetime(2026, 11, 16, tzinfo=timezone.utc)
    return BrokerHistoryWindow(
        snapshot=snapshot,
        window_start=start,
        window_end=start + timedelta(days=1),
        pages_fetched=3,
        has_more=False,
        provider_window=provider_window,
    )


def _reconcile(db, *, reconciliation_id="pg-run-1", window=None):
    return reconcile_persisted_history_window(
        db,
        history_window=window or _history_window(),
        reconciliation_id=reconciliation_id,
        intent_ids=("pg-intent-1",),
    )


def test_postgres_history_reconciliation_persists_complete_audit_payload(db):
    _persist_four_leg_intent(db)
    window = _history_window()

    result = _reconcile(db, window=window)
    stored = db.scalar(select(BrokerReconciliationRecord).where(
        BrokerReconciliationRecord.reconciliation_id == "pg-run-1"
    ))

    assert result["created"] is True
    assert result["report"].is_consistent is True
    assert stored is not None
    assert (stored.broker, stored.account_id) == ("fyers", "pg-acct-1")
    assert stored.snapshot_complete is True
    payload = json.loads(stored.report_json)
    assert payload["history_window"] == {
        "window_start": window.window_start.isoformat(),
        "window_end": window.window_end.isoformat(),
        "pages_fetched": 3,
        "has_more": False,
        "next_cursor": None,
        "provider_window": "FYERS_CLOSED_DAY_REPORTS",
        "complete": True,
    }
    assert payload["source_snapshot"]["account_id"] == "pg-acct-1"
    assert payload["source_snapshot"]["broker"] == "fyers"


def test_postgres_exact_reconciliation_replay_is_idempotent(db):
    _persist_four_leg_intent(db)
    first = _reconcile(db)
    replay = _reconcile(db)

    assert first["created"] is True
    assert replay["created"] is False
    assert replay["record_id"] == first["record_id"]
    assert db.query(BrokerReconciliationRecord).count() == 1


def test_postgres_same_identity_with_changed_provider_evidence_conflicts(db):
    _persist_four_leg_intent(db)
    _reconcile(db)

    changed = _history_window(provider_window="FYERS_CORRECTED_CLOSED_DAY_REPORTS")
    with pytest.raises(ReconciliationIdentityConflict):
        _reconcile(db, window=changed)

    assert db.query(BrokerReconciliationRecord).count() == 1


def test_postgres_reconciliation_cannot_cross_account_boundary(db):
    _persist_four_leg_intent(db, account_id="pg-acct-1")
    foreign_window = _history_window(account_id="pg-acct-2")

    with pytest.raises(LookupError, match="do not belong"):
        _reconcile(db, window=foreign_window)

    assert db.query(BrokerReconciliationRecord).count() == 0


def test_postgres_caller_rollback_removes_reconciliation_record(db):
    _persist_four_leg_intent(db)
    _reconcile(db)
    db.rollback()

    assert db.scalar(select(BrokerReconciliationRecord)) is None
