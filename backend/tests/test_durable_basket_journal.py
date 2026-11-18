from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.execution.durable_basket_journal import DurableBasketJournal
from app.execution.multi_leg_coordinator import (
    BasketReconciliation, BrokerOrderAck, ReconciledLeg,
)
from app.models.basket_execution_journal import (
    BasketExecutionJournalEventRecord, BasketExecutionJournalRecord,
)


@pytest.fixture()
def journal():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield DurableBasketJournal(factory), factory
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_claim_is_account_scoped_and_duplicate_claim_is_blocked(journal):
    durable, factory = journal
    assert asyncio.run(durable.claim_submission(
        account_id="acct-a", intent_id="intent-1",
        strategy="BOX_SPREAD", snapshot_id="snap-1",
    )) is True
    assert asyncio.run(durable.claim_submission(
        account_id="acct-a", intent_id="intent-1",
        strategy="BOX_SPREAD", snapshot_id="snap-1",
    )) is False
    # Same intent text under another account is a separate owner scope.
    assert asyncio.run(durable.claim_submission(
        account_id="acct-b", intent_id="intent-1",
        strategy="BOX_SPREAD", snapshot_id="snap-1",
    )) is True
    with factory() as db:
        rows = db.execute(select(BasketExecutionJournalRecord)).scalars().all()
        assert len(rows) == 2
        assert {row.account_id for row in rows} == {"acct-a", "acct-b"}


def test_submission_ack_and_full_reconciliation_are_durable(journal):
    durable, factory = journal
    async def run():
        assert await durable.claim_submission(
            account_id="acct-a", intent_id="intent-2",
            strategy="BOX_SPREAD", snapshot_id="snap-2",
        )
        client_ids = ("client-1", "client-2", "client-3", "client-4")
        await durable.record_submission_started(
            account_id="acct-a", intent_id="intent-2",
            client_order_ids=client_ids,
            expected_quantities={client_id: 50 for client_id in client_ids},
        )
        for index, client_id in enumerate(client_ids, start=1):
            await durable.record_order_ack(
                account_id="acct-a", intent_id="intent-2",
                ack=BrokerOrderAck(
                    client_order_id=client_id,
                    broker_order_id=f"broker-{index}",
                    status="OPEN", accepted=True,
                ),
            )
        reconciliation = BasketReconciliation(
            authoritative=True,
            orders_complete=True,
            trades_complete=True,
            positions_reconciled=True,
            legs=tuple(
                ReconciledLeg(
                    client_order_id=client_id,
                    broker_order_id=f"broker-{index}",
                    filled_quantity=50,
                    order_terminal=True,
                )
                for index, client_id in enumerate(client_ids, start=1)
            ),
        )
        await durable.complete_filled_basket(
            account_id="acct-a", intent_id="intent-2",
            reconciliation=reconciliation,
        )
    asyncio.run(run())
    with factory() as db:
        row = db.execute(select(BasketExecutionJournalRecord).where(
            BasketExecutionJournalRecord.account_id == "acct-a",
            BasketExecutionJournalRecord.intent_id == "intent-2",
        )).scalar_one()
        assert row.state == "FILLED"
        assert json.loads(row.client_order_ids_json) == [
            "client-1", "client-2", "client-3", "client-4",
        ]
        assert json.loads(row.reconciliation_json)["positions_reconciled"] is True
        events = db.execute(select(BasketExecutionJournalEventRecord).where(
            BasketExecutionJournalEventRecord.journal_id == row.id,
        ).order_by(BasketExecutionJournalEventRecord.sequence)).scalars().all()
        assert [event.event_type for event in events].count("BROKER_ORDER_ACK") == 4
        assert events[-1].event_type == "BASKET_FILLED_RECONCILED"


def test_incomplete_or_zero_fill_reconciliation_cannot_mark_filled(journal):
    durable, _ = journal
    async def run():
        await durable.claim_submission(
            account_id="acct-a", intent_id="intent-3",
            strategy="BOX_SPREAD", snapshot_id="snap-3",
        )
        await durable.record_submission_started(
            account_id="acct-a", intent_id="intent-3",
            client_order_ids=("c1", "c2"),
            expected_quantities={"c1": 50, "c2": 50},
        )
        with pytest.raises(ValueError, match="authoritative complete"):
            await durable.complete_filled_basket(
                account_id="acct-a", intent_id="intent-3",
                reconciliation=BasketReconciliation(
                    authoritative=True, orders_complete=True,
                    trades_complete=False, positions_reconciled=True,
                    legs=(),
                ),
            )
        with pytest.raises(ValueError, match="exact target quantity"):
            await durable.complete_filled_basket(
                account_id="acct-a", intent_id="intent-3",
                reconciliation=BasketReconciliation(
                    authoritative=True, orders_complete=True,
                    trades_complete=True, positions_reconciled=True,
                    legs=(
                        ReconciledLeg("c1", "o1", 50, True),
                        ReconciledLeg("c2", "o2", 0, True),
                    ),
                ),
            )
    asyncio.run(run())


def test_acknowledgement_must_belong_to_claimed_basket(journal):
    durable, _ = journal
    async def run():
        await durable.claim_submission(
            account_id="acct-a", intent_id="intent-4",
            strategy="BOX_SPREAD", snapshot_id="snap-4",
        )
        await durable.record_submission_started(
            account_id="acct-a", intent_id="intent-4",
            client_order_ids=("allowed-client", "other-client"),
            expected_quantities={"allowed-client": 25, "other-client": 25},
        )
        with pytest.raises(ValueError, match="not part of this basket"):
            await durable.record_order_ack(
                account_id="acct-a", intent_id="intent-4",
                ack=BrokerOrderAck(
                    client_order_id="foreign-client",
                    broker_order_id="broker-x", status="OPEN", accepted=True,
                ),
            )
    asyncio.run(run())


def test_submission_start_is_one_way_and_cannot_replace_client_ids(journal):
    durable = journal[0]
    async def run():
        await durable.claim_submission(
            account_id="acct-a", intent_id="intent-5",
            strategy="BOX_SPREAD", snapshot_id="snap-5",
        )
        await durable.record_submission_started(
            account_id="acct-a", intent_id="intent-5",
            client_order_ids=("c1", "c2"),
        )
        with pytest.raises(ValueError, match="fresh durable claim"):
            await durable.record_submission_started(
                account_id="acct-a", intent_id="intent-5",
                client_order_ids=("replacement-1", "replacement-2"),
                expected_quantities={"replacement-1": 25, "replacement-2": 25},
            )
    asyncio.run(run())
