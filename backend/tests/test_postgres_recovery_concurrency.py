from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import os
import uuid

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.execution.durable_recovery_journal import DurableRecoveryJournal
from app.execution.emergency_recovery import RecoveryState
from app.models.execution_recovery_journal import (
    RecoveryBudgetRecord, RecoveryInstrumentLeaseRecord,
    RecoveryControlRecord, RecoveryJournalEventRecord,
)


@pytest.fixture()
def postgres_journal():
    url = os.getenv("IV_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("IV_TEST_POSTGRES_URL is not configured")
    admin = create_engine(url, pool_pre_ping=True)
    schema = f"recovery_race_{uuid.uuid4().hex}"
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        url, connect_args={"options": f"-csearch_path={schema}"}, pool_pre_ping=True,
        pool_size=12, max_overflow=4,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield DurableRecoveryJournal(factory), factory
    engine.dispose()
    with admin.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    admin.dispose()


def test_postgres_concurrent_recovery_claims_never_exceed_approved_attempts(postgres_journal):
    journal, factory = postgres_journal
    journal.configure_recovery_budget(
        account_id="acct-race", intent_id="intent-race", symbol="NFO:OPT",
        max_attempts=3, max_notional=Decimal("50000"), approved=True,
    )

    def claim(_):
        try:
            return journal.claim_recovery_attempt(
                account_id="acct-race", intent_id="intent-race", symbol="NFO:OPT",
            )
        except RuntimeError:
            return None

    with ThreadPoolExecutor(max_workers=12) as pool:
        outcomes = list(pool.map(claim, range(12)))

    accepted = [value for value in outcomes if value is not None]
    assert sorted(accepted) == [1, 2, 3]
    with factory() as db:
        budget = db.execute(select(RecoveryBudgetRecord).where(
            RecoveryBudgetRecord.account_id == "acct-race",
            RecoveryBudgetRecord.intent_id == "intent-race",
            RecoveryBudgetRecord.symbol == "NFO:OPT",
        )).scalar_one()
        assert budget.attempts_used == 3


def test_postgres_concurrent_instrument_lease_has_exactly_one_owner(postgres_journal):
    journal, factory = postgres_journal

    def acquire(intent_id):
        return journal.acquire_instrument_leases(
            account_id="acct-race", intent_id=intent_id,
            symbols=("NFO:CE", "NFO:PE"),
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(acquire, [f"intent-{i}" for i in range(8)]))

    assert sum(outcomes) == 1
    with factory() as db:
        leases = db.execute(select(RecoveryInstrumentLeaseRecord).where(
            RecoveryInstrumentLeaseRecord.account_id == "acct-race",
        )).scalars().all()
        assert len(leases) == 2
        assert len({row.intent_id for row in leases}) == 1


def test_postgres_conflicting_basket_lease_rolls_back_uncontested_symbols(postgres_journal):
    journal, factory = postgres_journal
    assert journal.acquire_instrument_leases(
        account_id="acct-race", intent_id="owner", symbols=("NFO:PE",),
    )
    assert not journal.acquire_instrument_leases(
        account_id="acct-race", intent_id="contender", symbols=("NFO:CE", "NFO:PE"),
    )
    with factory() as db:
        leases = db.execute(select(RecoveryInstrumentLeaseRecord).where(
            RecoveryInstrumentLeaseRecord.account_id == "acct-race",
        )).scalars().all()
        assert [(row.intent_id, row.symbol) for row in leases] == [("owner", "NFO:PE")]


def test_postgres_concurrent_attempt_claims_across_rebuilt_journals_respect_budget(postgres_journal):
    journal, factory = postgres_journal
    journal.configure_recovery_budget(
        account_id="acct-restart-race", intent_id="intent-restart-race",
        symbol="NFO:OPT", max_attempts=2,
        max_notional=Decimal("25000"), approved=True,
    )

    def claim(_):
        # Each worker gets a distinct adapter instance, mirroring separate
        # coordinator processes reconstructed against the same database.
        restarted_journal = DurableRecoveryJournal(factory)
        try:
            return restarted_journal.claim_recovery_attempt(
                account_id="acct-restart-race",
                intent_id="intent-restart-race",
                symbol="NFO:OPT",
            )
        except RuntimeError:
            return None

    with ThreadPoolExecutor(max_workers=12) as pool:
        outcomes = list(pool.map(claim, range(12)))

    assert sorted(value for value in outcomes if value is not None) == [1, 2]
    with factory() as db:
        budget = db.execute(select(RecoveryBudgetRecord).where(
            RecoveryBudgetRecord.account_id == "acct-restart-race",
            RecoveryBudgetRecord.intent_id == "intent-restart-race",
            RecoveryBudgetRecord.symbol == "NFO:OPT",
        )).scalar_one()
        assert budget.attempts_used == 2


def test_postgres_concurrent_notional_approvals_across_rebuilt_journals_respect_cap(postgres_journal):
    journal, factory = postgres_journal
    account_id = "acct-notional-race"
    intent_id = "intent-notional-race"
    symbol = "NFO:OPT"
    journal.configure_recovery_budget(
        account_id=account_id, intent_id=intent_id, symbol=symbol,
        max_attempts=12, max_notional=Decimal("250"), approved=True,
    )
    for _ in range(12):
        assert journal.claim_recovery_attempt(
            account_id=account_id, intent_id=intent_id, symbol=symbol,
        ) >= 1

    def approve(_):
        # Rebuild the adapter per worker to exercise independent coordinators.
        restarted_journal = DurableRecoveryJournal(factory)
        return restarted_journal.approve_emergency_close(
            account_id=account_id, intent_id=intent_id, symbol=symbol,
            side="SELL", quantity=1, limit_price=Decimal("100"),
            notional=Decimal("100"),
        )

    with ThreadPoolExecutor(max_workers=12) as pool:
        outcomes = list(pool.map(approve, range(12)))

    assert sum(outcomes) == 2
    with factory() as db:
        budget = db.execute(select(RecoveryBudgetRecord).where(
            RecoveryBudgetRecord.account_id == account_id,
            RecoveryBudgetRecord.intent_id == intent_id,
            RecoveryBudgetRecord.symbol == symbol,
        )).scalar_one()
        assert Decimal(budget.notional_used) == Decimal("200")
        assert Decimal(budget.notional_used) <= Decimal(budget.max_notional)


def test_postgres_concurrent_recovery_events_keep_unique_contiguous_sequence(postgres_journal):
    journal, factory = postgres_journal
    account_id = "acct-event-race"
    intent_id = "intent-event-race"
    # Create and commit the control row before racing appenders.
    journal.append_event(
        account_id=account_id, intent_id=intent_id,
        event="RECOVERY_TEST_STARTED", details={"source": "concurrency-test"},
    )

    def append(index):
        DurableRecoveryJournal(factory).append_event(
            account_id=account_id, intent_id=intent_id,
            event="CONCURRENT_RECOVERY_TEST_EVENT", details={"worker": index},
        )

    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(append, range(12)))

    with factory() as db:
        control = db.execute(select(RecoveryControlRecord).where(
            RecoveryControlRecord.account_id == account_id,
            RecoveryControlRecord.intent_id == intent_id,
        )).scalar_one()
        events = db.execute(select(RecoveryJournalEventRecord).where(
            RecoveryJournalEventRecord.control_id == control.id,
        ).order_by(RecoveryJournalEventRecord.sequence)).scalars().all()
        assert len(events) == 13
        assert [event.sequence for event in events] == list(range(13))
        assert sum(event.event == "CONCURRENT_RECOVERY_TEST_EVENT" for event in events) == 12



def test_postgres_concurrent_state_writers_cannot_reopen_verified_flat(postgres_journal):
    journal, factory = postgres_journal
    account_id = "acct-flat-race"
    intent_id = "intent-flat-race"
    # Seed the row so the race exercises row-level serialization, not
    # simultaneous first-time control creation.
    journal.set_state(
        account_id=account_id, intent_id=intent_id,
        state=RecoveryState.RECOVERING, reason="recovery in progress",
    )

    def transition(index):
        state = RecoveryState.FLAT if index == 0 else (
            RecoveryState.MANUAL_REVIEW if index % 2 else RecoveryState.RECOVERING
        )
        try:
            DurableRecoveryJournal(factory).set_state(
                account_id=account_id, intent_id=intent_id,
                state=state, reason=f"concurrent writer {index}",
            )
            return "accepted"
        except ValueError as exc:
            assert "terminal" in str(exc)
            return "rejected_after_flat"

    with ThreadPoolExecutor(max_workers=12) as pool:
        outcomes = list(pool.map(transition, range(12)))

    assert outcomes.count("accepted") >= 1
    with factory() as db:
        control = db.execute(select(RecoveryControlRecord).where(
            RecoveryControlRecord.account_id == account_id,
            RecoveryControlRecord.intent_id == intent_id,
        )).scalar_one()
        assert control.state == RecoveryState.FLAT.value
        assert control.risk_reserved is True
        events = db.execute(select(RecoveryJournalEventRecord).where(
            RecoveryJournalEventRecord.control_id == control.id,
        ).order_by(RecoveryJournalEventRecord.sequence)).scalars().all()
        sequences = [event.sequence for event in events]
        assert sequences == list(range(len(sequences)))
        assert sum(
            event.event == "RECOVERY_STATE_CHANGED"
            and '"state":"FLAT_AFTER_UNWIND"' in event.details_json
            for event in events
        ) == 1
        # Once FLAT commits, every later non-FLAT transition must be rejected.
        flat_index = next(
            index for index, event in enumerate(events)
            if event.event == "RECOVERY_STATE_CHANGED"
            and '"state":"FLAT_AFTER_UNWIND"' in event.details_json
        )
        assert all(
            '"state":"FLAT_AFTER_UNWIND"' in event.details_json
            or event.event != "RECOVERY_STATE_CHANGED"
            for event in events[flat_index + 1:]
        )



def test_postgres_concurrent_verified_flat_release_is_idempotent(postgres_journal):
    journal, factory = postgres_journal
    account_id = "acct-release-race"
    intent_id = "intent-release-race"
    assert journal.acquire_instrument_leases(
        account_id=account_id, intent_id=intent_id,
        symbols=("NFO:CE", "NFO:PE"),
    )
    journal.set_state(
        account_id=account_id, intent_id=intent_id,
        state=RecoveryState.FLAT, reason="broker positions reconciled flat",
    )

    def release(_):
        DurableRecoveryJournal(factory).release_risk_reservation(
            account_id=account_id, intent_id=intent_id,
        )

    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(release, range(12)))

    with factory() as db:
        control = db.execute(select(RecoveryControlRecord).where(
            RecoveryControlRecord.account_id == account_id,
            RecoveryControlRecord.intent_id == intent_id,
        )).scalar_one()
        leases = db.execute(select(RecoveryInstrumentLeaseRecord).where(
            RecoveryInstrumentLeaseRecord.account_id == account_id,
            RecoveryInstrumentLeaseRecord.intent_id == intent_id,
        )).scalars().all()
        events = db.execute(select(RecoveryJournalEventRecord).where(
            RecoveryJournalEventRecord.control_id == control.id,
        ).order_by(RecoveryJournalEventRecord.sequence)).scalars().all()
        assert control.state == RecoveryState.FLAT.value
        assert control.risk_reserved is False
        assert leases == []
        assert [event.sequence for event in events] == list(range(len(events)))
        assert sum(event.event == "RISK_RESERVATION_RELEASED" for event in events) == 1
