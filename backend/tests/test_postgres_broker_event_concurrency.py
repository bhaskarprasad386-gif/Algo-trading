"""PostgreSQL concurrency regressions for durable broker callback/fill ingestion."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import os
import uuid

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.execution.broker_events import BrokerOrderEvent, BrokerOrderEventStatus
from app.execution.broker_event_persistence import (
    BrokerEventConflict,
    record_broker_order_event,
    record_broker_order_event_with_fill,
)
from app.execution.contracts import (
    ExecutionIntent, ExecutionLeg, ExecutionMode, IntentStatus, OrderSide,
)
from app.execution.persistence import create_intent, record_broker_order, transition_intent
from app.models.broker_order_event import BrokerOrderEventRecord
from app.models.execution_intent import (
    ExecutionFillRecord, ExecutionIntentRecord, ExecutionLegRecord,
)


@pytest.fixture()
def postgres_factory():
    url = os.getenv("IV_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("IV_TEST_POSTGRES_URL is not configured")
    admin = create_engine(url, pool_pre_ping=True)
    schema = f"broker_event_race_{uuid.uuid4().hex}"
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        url, connect_args={"options": f"-csearch_path={schema}"},
        pool_pre_ping=True, pool_size=12, max_overflow=4,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()
    with admin.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    admin.dispose()


def _seed_working_order(factory):
    with factory() as db, db.begin():
        intent = ExecutionIntent(
            "intent-callback-race", "acct-callback-race", "key-callback-race",
            "corr-callback-race", ExecutionMode.PAPER, created_at_ns=100,
        )
        legs = (
            ExecutionLeg("leg-ce", "NFO:CE", OrderSide.BUY, 50, limit_price=100),
            ExecutionLeg("leg-pe", "NFO:PE", OrderSide.BUY, 50, limit_price=90),
        )
        saved, _ = create_intent(db, intent, legs)
        version = saved.version
        for target in (
            IntentStatus.VALIDATING, IntentStatus.RISK_CHECK, IntentStatus.APPROVED,
            IntentStatus.RESERVED, IntentStatus.SUBMITTING,
        ):
            saved = transition_intent(
                db, account_id="acct-callback-race",
                intent_id="intent-callback-race",
                expected_version=version, target=target,
            )
            version = saved.version
        record_broker_order(
            db, account_id="acct-callback-race",
            intent_id="intent-callback-race", leg_id="leg-ce",
            broker="fyers", broker_order_id="order-callback-race",
            symbol="NFO:CE", side="BUY", quantity=50, accepted_at_ns=200,
        )


def _fill_event(*, event_id="evt-callback-race", fill_id="trade-callback-race",
                quantity=10, cumulative=10, price=101.25, status=None):
    return BrokerOrderEvent(
        broker="fyers", account_id="acct-callback-race", event_id=event_id,
        broker_order_id="order-callback-race",
        status=status or BrokerOrderEventStatus.PARTIALLY_FILLED,
        cumulative_filled_quantity=cumulative, fill_id=fill_id,
        fill_quantity=quantity, average_fill_price=price, occurred_at_ns=300,
    )


def test_postgres_concurrent_exact_callback_replay_applies_one_fill(postgres_factory):
    _seed_working_order(postgres_factory)
    event = _fill_event()

    def ingest(_):
        with postgres_factory() as db:
            return record_broker_order_event_with_fill(db, event)

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(ingest, range(12)))

    assert sum(result["fill_created"] for result in results) == 1
    assert all(not result["fill_deferred"] for result in results)
    with postgres_factory() as db:
        assert db.query(BrokerOrderEventRecord).count() == 1
        assert db.query(ExecutionFillRecord).count() == 1
        leg = db.scalar(select(ExecutionLegRecord).where(
            ExecutionLegRecord.leg_id == "leg-ce",
        ))
        assert leg.filled_quantity == 10


def test_postgres_distinct_callbacks_with_same_fill_identity_apply_once(postgres_factory):
    _seed_working_order(postgres_factory)

    def ingest(index):
        event = _fill_event(event_id=f"evt-callback-alias-{index}")
        with postgres_factory() as db:
            return record_broker_order_event_with_fill(db, event)

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(ingest, range(12)))

    assert sum(result["fill_created"] for result in results) == 1
    assert sum(result["event_created"] for result in results) == 12
    with postgres_factory() as db:
        assert db.query(BrokerOrderEventRecord).count() == 12
        assert db.query(ExecutionFillRecord).count() == 1
        leg = db.scalar(select(ExecutionLegRecord).where(
            ExecutionLegRecord.leg_id == "leg-ce",
        ))
        assert leg.filled_quantity == 10


def test_postgres_concurrent_event_identity_payload_conflict_fails_closed(postgres_factory):
    def ingest(index):
        event = BrokerOrderEvent(
            broker="fyers", account_id="acct-callback-race",
            event_id="evt-conflicting-identity",
            broker_order_id="order-not-registered",
            status=(BrokerOrderEventStatus.OPEN if index % 2 else BrokerOrderEventStatus.REJECTED),
            cumulative_filled_quantity=0, occurred_at_ns=500 + index,
        )
        with postgres_factory() as db:
            try:
                return record_broker_order_event(db, event)["created"]
            except BrokerEventConflict:
                return "conflict"

    with ThreadPoolExecutor(max_workers=12) as pool:
        outcomes = list(pool.map(ingest, range(12)))

    assert outcomes.count(True) == 1
    assert outcomes.count("conflict") == 11
    with postgres_factory() as db:
        rows = db.scalars(select(BrokerOrderEventRecord)).all()
        assert len(rows) == 1
        assert rows[0].event_id == "evt-conflicting-identity"
        assert db.query(ExecutionFillRecord).count() == 0


def test_postgres_concurrent_fill_identity_payload_conflict_fails_closed(postgres_factory):
    _seed_working_order(postgres_factory)

    def ingest(index):
        event = _fill_event(
            event_id=f"evt-fill-conflict-{index}",
            price=101.25 if index % 2 == 0 else 101.50,
        )
        with postgres_factory() as db:
            try:
                return record_broker_order_event_with_fill(db, event)
            except BrokerEventConflict:
                return "conflict"

    with ThreadPoolExecutor(max_workers=12) as pool:
        outcomes = list(pool.map(ingest, range(12)))

    # Six workers carry each of the two price variants. Exactly one variant
    # wins the immutable fill identity; identical evidence is idempotent,
    # while only the opposite-price variant must fail closed.
    assert sum(isinstance(item, dict) and item["fill_created"] for item in outcomes) == 1
    conflict_count = outcomes.count("conflict")
    assert conflict_count in (5, 6)
    accepted = [item for item in outcomes if isinstance(item, dict)]
    assert len(accepted) + conflict_count == 12
    assert all(item["event_created"] for item in accepted)
    assert all(not item["fill_deferred"] for item in accepted)
    assert sum(item["fill_created"] for item in accepted) == 1
    with postgres_factory() as db:
        assert db.query(BrokerOrderEventRecord).count() == len(accepted)
        assert db.query(ExecutionFillRecord).count() == 1
        fill = db.scalar(select(ExecutionFillRecord))
        assert Decimal(str(fill.fill_price)) in {
            Decimal("101.25"), Decimal("101.50"),
        }

def test_postgres_concurrent_out_of_order_callbacks_reconcile_after_gap_closes(postgres_factory):
    _seed_working_order(postgres_factory)
    earlier = _fill_event(
        event_id="evt-concurrent-earlier", fill_id="trade-concurrent-earlier",
        quantity=10, cumulative=10, price=101.10,
    )
    later = _fill_event(
        event_id="evt-concurrent-later", fill_id="trade-concurrent-later",
        quantity=10, cumulative=20, price=101.20,
    )

    def ingest(event):
        with postgres_factory() as db:
            return record_broker_order_event_with_fill(db, event)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(ingest, (later, earlier)))

    # Either callback may win the initial row lock. If the later cumulative
    # event arrived first it must remain durable/deferred, never be guessed.

    with postgres_factory() as db:
        assert db.query(BrokerOrderEventRecord).count() == 2
        assert db.query(ExecutionFillRecord).count() in (1, 2)

    # Once the missing predecessor is known, explicitly retry the immutable
    # later event. Exact replay is idempotent if it was already applied.
    with postgres_factory() as db:
        replay = record_broker_order_event_with_fill(db, later)
    assert replay["fill_deferred"] is False

    with postgres_factory() as db:
        assert db.query(BrokerOrderEventRecord).count() == 2
        assert db.query(ExecutionFillRecord).count() == 2
        leg = db.scalar(select(ExecutionLegRecord).where(
            ExecutionLegRecord.leg_id == "leg-ce",
        ))
        assert leg.filled_quantity == 20
