from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.execution.durable_basket_journal import DurableBasketJournal
from app.execution.unified_restart_recovery import UnifiedRestartRecoveryInventory
from app.models.basket_execution_journal import (
    BasketExecutionJournalEventRecord,
    BasketExecutionJournalRecord,
)
from app.models.execution_recovery_journal import (
    RecoveryControlRecord,
    RecoveryJournalEventRecord,
    RecoveryKillSwitchRecord,
)


@pytest.fixture()
def recovery_store():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


def _create_claim(factory, *, intent_id: str, state: str = "CLAIMED") -> None:
    with factory() as db, db.begin():
        db.add(BasketExecutionJournalRecord(
            account_id="acct-crash", intent_id=intent_id, strategy="BOX_SPREAD",
            snapshot_id="snapshot-crash", state=state,
            client_order_ids_json="[]", reason="", version=0,
            created_at_ns=1, updated_at_ns=2,
        ))


def test_crash_after_broker_acceptance_before_ack_journal_never_resubmits(recovery_store):
    """A broker-accepted order with a lost local ack is unresolved, never replayed."""
    factory = recovery_store
    intent_id = "accepted-ack-lost"
    client_ids = ("client-a", "client-b", "client-c", "client-d")

    async def persist_pre_submit_boundary():
        journal = DurableBasketJournal(factory)
        assert await journal.claim_submission(
            account_id="acct-crash", intent_id=intent_id,
            strategy="BOX_SPREAD", snapshot_id="snapshot-crash",
        )
        await journal.record_submission_started(
            account_id="acct-crash", intent_id=intent_id,
            client_order_ids=client_ids,
            expected_quantities={client_id: 50 for client_id in client_ids},
        )

    asyncio.run(persist_pre_submit_boundary())

    # Simulate the external broker having accepted one request while the
    # process died before its acknowledgement reached the durable journal.
    broker = {"accepted_client_ids": {"client-a"}, "submit_calls": 1}
    broker_calls_before_restart = broker["submit_calls"]

    # Construct a fresh service instance, as a restarted process would.
    restarted = UnifiedRestartRecoveryInventory(factory)
    candidates = restarted.list_unresolved(account_id="acct-crash")
    assert len(candidates) == 1
    assert candidates[0].basket_state == "SUBMITTING"
    assert candidates[0].requires_broker_reconciliation

    restarted.freeze_for_reconciliation(
        account_id="acct-crash", intent_id=intent_id,
        reason="restart: broker acceptance may have preceded lost acknowledgement",
    )

    # Startup inventory/freeze has no broker submission path. It must not
    # retry any client ID merely because the ack is absent from local storage.
    assert broker["submit_calls"] == broker_calls_before_restart
    assert broker["accepted_client_ids"] == {"client-a"}

    with factory() as db:
        basket = db.execute(select(BasketExecutionJournalRecord).where(
            BasketExecutionJournalRecord.intent_id == intent_id,
        )).scalar_one()
        control = db.execute(select(RecoveryControlRecord).where(
            RecoveryControlRecord.intent_id == intent_id,
        )).scalar_one()
        kill = db.execute(select(RecoveryKillSwitchRecord).where(
            RecoveryKillSwitchRecord.account_id == "acct-crash",
            RecoveryKillSwitchRecord.strategy == "BOX_SPREAD",
        )).scalar_one()
        events = db.execute(select(RecoveryJournalEventRecord).where(
            RecoveryJournalEventRecord.control_id == control.id,
        )).scalars().all()
        assert basket.state == "FROZEN"
        assert control.frozen and control.risk_reserved
        assert kill.latched
        assert json.loads(events[-1].details_json)["broker_action_taken"] is False


@pytest.mark.parametrize(
    ("intent_id", "initial_state", "expected_client_ids"),
    [
        ("crash-after-claim", "CLAIMED", ()),
        ("crash-after-submit-start", "SUBMITTING", ("c1", "c2", "c3", "c4")),
    ],
)
def test_restart_freezes_each_pre_ack_crash_boundary(
    recovery_store, intent_id, initial_state, expected_client_ids,
):
    factory = recovery_store
    with factory() as db, db.begin():
        db.add(BasketExecutionJournalRecord(
            account_id="acct-crash", intent_id=intent_id, strategy="BOX_SPREAD",
            snapshot_id="snapshot-crash", state=initial_state,
            client_order_ids_json=json.dumps(expected_client_ids), reason="",
            version=0, created_at_ns=1, updated_at_ns=2,
        ))
        if initial_state == "SUBMITTING":
            db.add(BasketExecutionJournalEventRecord(
                journal_id=1, sequence=0, event_type="SUBMISSION_STARTED",
                payload_json=json.dumps({
                    "client_order_ids": list(expected_client_ids),
                    "expected_quantities": {key: 50 for key in expected_client_ids},
                }),
                occurred_at_ns=2,
            ))

    restarted = UnifiedRestartRecoveryInventory(factory)
    candidate = restarted.list_unresolved(account_id="acct-crash")[0]
    assert candidate.basket_state == initial_state
    restarted.freeze_for_reconciliation(
        account_id="acct-crash", intent_id=intent_id,
        reason=f"restart at {initial_state} boundary",
    )
    frozen = restarted.list_unresolved(account_id="acct-crash")[0]
    assert frozen.basket_state == "FROZEN"
    assert frozen.risk_reserved and frozen.frozen
    assert frozen.requires_broker_reconciliation
