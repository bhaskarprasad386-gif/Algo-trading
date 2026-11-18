from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.execution.durable_recovery_journal import DurableRecoveryJournal
from app.execution.emergency_recovery import RecoveryState
from app.models.execution_recovery_journal import (
    RecoveryBudgetRecord, RecoveryControlRecord, RecoveryInstrumentLeaseRecord,
    RecoveryJournalEventRecord, RecoveryKillSwitchRecord,
)


@pytest.fixture()
def journal():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield DurableRecoveryJournal(factory), factory
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_kill_switch_is_durable_and_latched_per_account_strategy(journal):
    durable, factory = journal
    durable.latch_entry_kill_switch(
        account_id="acct-a", strategy="BOX_SPREAD", reason="partial basket",
    )
    durable.latch_entry_kill_switch(
        account_id="acct-a", strategy="BOX_SPREAD", reason="still recovering",
    )
    assert durable.is_entry_kill_switch_latched(account_id="acct-a", strategy="BOX_SPREAD")
    assert not durable.is_entry_kill_switch_latched(account_id="acct-b", strategy="BOX_SPREAD")
    with factory() as db:
        rows = db.execute(select(RecoveryKillSwitchRecord)).scalars().all()
        assert len(rows) == 1
        assert rows[0].latched is True
        assert rows[0].reason == "still recovering"


def test_instrument_leases_are_atomic_and_exclusive(journal):
    durable, factory = journal
    assert durable.acquire_instrument_leases(
        account_id="acct-a", intent_id="intent-1", symbols=("OPT:CE", "OPT:PE"),
    )
    assert not durable.acquire_instrument_leases(
        account_id="acct-a", intent_id="intent-2", symbols=("OPT:CE", "OPT:PE"),
    )
    assert durable.assert_exclusive_instrument_lease(
        account_id="acct-a", intent_id="intent-1", symbols=("OPT:CE", "OPT:PE"),
    )
    assert not durable.assert_exclusive_instrument_lease(
        account_id="acct-a", intent_id="intent-1", symbols=("OPT:CE",),
    )
    with factory() as db:
        rows = db.execute(select(RecoveryInstrumentLeaseRecord)).scalars().all()
        assert len(rows) == 2
        assert {x.intent_id for x in rows} == {"intent-1"}


def test_partial_lease_acquisition_rolls_back_all_symbols(journal):
    durable, factory = journal
    assert durable.acquire_instrument_leases(
        account_id="acct-a", intent_id="owner", symbols=("OPT:PE",),
    )
    assert not durable.acquire_instrument_leases(
        account_id="acct-a", intent_id="contender", symbols=("OPT:CE", "OPT:PE"),
    )
    with factory() as db:
        rows = db.execute(select(RecoveryInstrumentLeaseRecord)).scalars().all()
        assert [(x.intent_id, x.symbol) for x in rows] == [("owner", "OPT:PE")]


def test_recovery_budget_requires_explicit_approval_and_bounds_attempts(journal):
    durable, factory = journal
    durable.configure_recovery_budget(
        account_id="acct-a", intent_id="intent-1", symbol="OPT:CE",
        max_attempts=2, max_notional=Decimal("10000"), approved=False,
    )
    with pytest.raises(RuntimeError, match="explicit approved"):
        durable.claim_recovery_attempt(
            account_id="acct-a", intent_id="intent-1", symbol="OPT:CE",
        )
    durable.configure_recovery_budget(
        account_id="acct-a", intent_id="intent-2", symbol="OPT:CE",
        max_attempts=2, max_notional=Decimal("10000"), approved=True,
    )
    assert durable.claim_recovery_attempt(
        account_id="acct-a", intent_id="intent-2", symbol="OPT:CE",
    ) == 1
    assert durable.claim_recovery_attempt(
        account_id="acct-a", intent_id="intent-2", symbol="OPT:CE",
    ) == 2
    with pytest.raises(RuntimeError, match="exhausted"):
        durable.claim_recovery_attempt(
            account_id="acct-a", intent_id="intent-2", symbol="OPT:CE",
        )
    with factory() as db:
        row = db.execute(select(RecoveryBudgetRecord).where(
            RecoveryBudgetRecord.intent_id == "intent-2",
        )).scalar_one()
        assert row.attempts_used == 2


def test_emergency_close_budget_checks_exact_notional_and_cumulative_cap(journal):
    durable, _ = journal
    durable.configure_recovery_budget(
        account_id="acct-a", intent_id="intent-1", symbol="OPT:CE",
        max_attempts=2, max_notional=Decimal("1000"), approved=True,
    )
    durable.claim_recovery_attempt(account_id="acct-a", intent_id="intent-1", symbol="OPT:CE")
    assert not durable.approve_emergency_close(
        account_id="acct-a", intent_id="intent-1", symbol="OPT:CE",
        side="SELL", quantity=10, limit_price=Decimal("10"),
        notional=Decimal("99"),
    )
    assert durable.approve_emergency_close(
        account_id="acct-a", intent_id="intent-1", symbol="OPT:CE",
        side="SELL", quantity=10, limit_price=Decimal("10"),
        notional=Decimal("100"),
    )
    assert not durable.approve_emergency_close(
        account_id="acct-a", intent_id="intent-1", symbol="OPT:CE",
        side="SELL", quantity=100, limit_price=Decimal("10"),
        notional=Decimal("1000"),
    )


def test_risk_reservation_and_leases_release_only_after_verified_flat(journal):
    durable, factory = journal
    assert durable.acquire_instrument_leases(
        account_id="acct-a", intent_id="intent-1", symbols=("OPT:CE",),
    )
    durable.freeze_intent(account_id="acct-a", intent_id="intent-1", reason="partial fill")
    with pytest.raises(ValueError, match="broker-confirmed FLAT"):
        durable.release_risk_reservation(account_id="acct-a", intent_id="intent-1")
    durable.set_state(
        account_id="acct-a", intent_id="intent-1",
        state=RecoveryState.FLAT, reason="broker positions reconciled flat",
    )
    durable.release_risk_reservation(account_id="acct-a", intent_id="intent-1")
    assert not durable.assert_exclusive_instrument_lease(
        account_id="acct-a", intent_id="intent-1", symbols=("OPT:CE",),
    )
    with factory() as db:
        row = db.execute(select(RecoveryControlRecord).where(
            RecoveryControlRecord.intent_id == "intent-1",
        )).scalar_one()
        assert row.frozen is True
        assert row.risk_reserved is False
        events = db.execute(select(RecoveryJournalEventRecord).where(
            RecoveryJournalEventRecord.control_id == row.id,
        ).order_by(RecoveryJournalEventRecord.sequence)).scalars().all()
        assert [x.sequence for x in events] == list(range(len(events)))
        assert events[-1].event == "RISK_RESERVATION_RELEASED"


def test_verified_flat_is_terminal_and_does_not_clear_kill_switch(journal):
    durable, _ = journal
    durable.set_state(
        account_id="acct-a", intent_id="intent-1",
        state=RecoveryState.FLAT, reason="broker confirmed flat",
    )
    with pytest.raises(ValueError, match="terminal"):
        durable.set_state(
            account_id="acct-a", intent_id="intent-1",
            state=RecoveryState.RECOVERING, reason="cannot reopen",
        )
    durable.latch_entry_kill_switch(
        account_id="acct-a", strategy="BOX_SPREAD", reason="recovery event",
    )
    assert durable.is_entry_kill_switch_latched(account_id="acct-a", strategy="BOX_SPREAD")
