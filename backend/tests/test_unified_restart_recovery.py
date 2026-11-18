from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.execution.unified_restart_recovery import UnifiedRestartRecoveryInventory
from app.models.basket_execution_journal import (
    BasketExecutionJournalEventRecord, BasketExecutionJournalRecord,
)
from app.models.execution_recovery_journal import (
    RecoveryControlRecord, RecoveryJournalEventRecord, RecoveryKillSwitchRecord,
)
from app.models.execution_intent import ExecutionIntentRecord


@pytest.fixture()
def inventory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield UnifiedRestartRecoveryInventory(factory), factory
    Base.metadata.drop_all(engine)
    engine.dispose()


def _basket(db, *, account="acct-a", intent="intent-1", state="SUBMITTING"):
    row = BasketExecutionJournalRecord(
        account_id=account, intent_id=intent, strategy="BOX_SPREAD",
        snapshot_id="snapshot-1", state=state, client_order_ids_json='["c1","c2"]',
        reason="", version=0, created_at_ns=1, updated_at_ns=2,
    )
    db.add(row)
    db.flush()
    return row


def _execution(db, *, account="acct-a", intent="intent-1", status="UNKNOWN"):
    row = ExecutionIntentRecord(
        intent_id=intent, account_id=account, idempotency_key=f"key-{intent}",
        correlation_id=f"corr-{intent}", mode="PAPER", status=status,
        created_at_ns=1, updated_at_ns=3, version=0,
    )
    db.add(row)
    return row


def test_inventory_joins_basket_execution_and_recovery_by_account_intent(inventory):
    service, factory = inventory
    with factory() as db, db.begin():
        _basket(db)
        _execution(db)
        db.add(RecoveryControlRecord(
            account_id="acct-a", intent_id="intent-1", strategy="BOX_SPREAD",
            state="EMERGENCY_UNWIND", frozen=True, risk_reserved=True,
            reason="partial fill", updated_at_ns=4,
        ))
        _basket(db, account="acct-b", intent="intent-1")

    rows = service.list_unresolved(account_id="acct-a")
    assert len(rows) == 1
    row = rows[0]
    assert row.intent_id == "intent-1"
    assert row.basket_state == "SUBMITTING"
    assert row.execution_state == "UNKNOWN"
    assert row.recovery_state == "EMERGENCY_UNWIND"
    assert row.risk_reserved and row.frozen
    assert row.sources == ("basket", "execution", "recovery")
    assert row.requires_broker_reconciliation


def test_inventory_includes_orphaned_basket_claim_and_is_read_only(inventory):
    service, factory = inventory
    with factory() as db, db.begin():
        _basket(db, state="CLAIMED")

    before = service.list_unresolved(account_id="acct-a")
    after = service.list_unresolved(account_id="acct-a")
    assert len(before) == 1
    assert before[0] == after[0]
    assert before[0].sources == ("basket",)
    assert before[0].requires_broker_reconciliation
    with factory() as db:
        row = db.execute(select(BasketExecutionJournalRecord)).scalar_one()
        assert row.state == "CLAIMED"
        assert db.execute(select(RecoveryControlRecord)).scalars().all() == []


def test_freeze_atomically_latches_kill_switch_and_retains_risk(inventory):
    service, factory = inventory
    with factory() as db, db.begin():
        basket = _basket(db)

    service.freeze_for_reconciliation(
        account_id="acct-a", intent_id="intent-1", reason="process restarted during submission",
    )
    # Idempotent retry must not create a second basket freeze event.
    service.freeze_for_reconciliation(
        account_id="acct-a", intent_id="intent-1", reason="process restarted during submission",
    )

    with factory() as db:
        basket = db.execute(select(BasketExecutionJournalRecord)).scalar_one()
        control = db.execute(select(RecoveryControlRecord)).scalar_one()
        kill = db.execute(select(RecoveryKillSwitchRecord)).scalar_one()
        basket_events = db.execute(select(BasketExecutionJournalEventRecord)).scalars().all()
        recovery_events = db.execute(select(RecoveryJournalEventRecord)).scalars().all()
        assert basket.state == "FROZEN"
        assert basket.version == 1
        assert len(basket_events) == 1
        assert control.frozen is True and control.risk_reserved is True
        assert control.state == "EMERGENCY_UNWIND"
        assert kill.latched is True and kill.strategy == "BOX_SPREAD"
        assert len(recovery_events) == 2
        payload = json.loads(recovery_events[-1].details_json)
        assert payload["broker_action_taken"] is False


def test_freeze_requires_existing_intent_and_cannot_reopen_verified_flat(inventory):
    service, factory = inventory
    with pytest.raises(LookupError, match="no durable"):
        service.freeze_for_reconciliation(
            account_id="acct-a", intent_id="missing", reason="startup",
        )
    with factory() as db, db.begin():
        db.add(RecoveryControlRecord(
            account_id="acct-a", intent_id="flat", strategy="BOX_SPREAD",
            state="FLAT_AFTER_UNWIND", frozen=True, risk_reserved=False,
            reason="verified", updated_at_ns=1,
        ))
    with pytest.raises(ValueError, match="terminal"):
        service.freeze_for_reconciliation(
            account_id="acct-a", intent_id="flat", reason="late restart",
        )
