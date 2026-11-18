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
    durable.acquire_instrument_leases(
        account_id="acct-a", intent_id="intent-1", symbols=("OPT:CE",),
    )
    durable.freeze_intent(account_id="acct-a", intent_id="intent-1", reason="partial fill")
    with pytest.raises(RuntimeError, match="explicit approved"):
        durable.claim_recovery_attempt(
            account_id="acct-a", intent_id="intent-1", symbol="OPT:CE",
        )
    durable.configure_recovery_budget(
        account_id="acct-a", intent_id="intent-2", symbol="OPT:CE",
        max_attempts=2, max_notional=Decimal("10000"), approved=True,
    )
    durable.acquire_instrument_leases(
        account_id="acct-a", intent_id="intent-2", symbols=("OPT:CE",),
    )
    durable.freeze_intent(account_id="acct-a", intent_id="intent-2", reason="partial fill")
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
    # A budget cannot even consume an attempt before recovery is frozen and leased.
    with pytest.raises(RuntimeError, match="frozen risk-reserved"):
        durable.claim_recovery_attempt(
            account_id="acct-a", intent_id="intent-1", symbol="OPT:CE",
        )
    assert not durable.approve_emergency_close(
        account_id="acct-a", intent_id="intent-1", symbol="OPT:CE",
        side="SELL", quantity=10, limit_price=Decimal("10"),
        notional=Decimal("100"),
    client_order_id="test-close",
    )
    # A lease alone is also insufficient until the intent is frozen for recovery.
    assert durable.acquire_instrument_leases(
        account_id="acct-a", intent_id="intent-1", symbols=("OPT:CE",),
    )
    assert not durable.approve_emergency_close(
        account_id="acct-a", intent_id="intent-1", symbol="OPT:CE",
        side="SELL", quantity=10, limit_price=Decimal("10"),
        notional=Decimal("100"),
    client_order_id="test-close",
    )
    durable.freeze_intent(
        account_id="acct-a", intent_id="intent-1", reason="confirmed partial basket",
    )
    durable.claim_recovery_attempt(
        account_id="acct-a", intent_id="intent-1", symbol="OPT:CE",
    )
    assert not durable.approve_emergency_close(
        account_id="acct-a", intent_id="intent-1", symbol="OPT:CE",
        side="SELL", quantity=10, limit_price=Decimal("10"),
        notional=Decimal("99"),
    client_order_id="test-close",
    )
    assert durable.approve_emergency_close(
        account_id="acct-a", intent_id="intent-1", symbol="OPT:CE",
        side="SELL", quantity=10, limit_price=Decimal("10"),
        notional=Decimal("100"),
    client_order_id="test-close",
    )
    # The same claimed attempt cannot authorize a duplicate close request.
    assert not durable.approve_emergency_close(
        account_id="acct-a", intent_id="intent-1", symbol="OPT:CE",
        side="SELL", quantity=10, limit_price=Decimal("10"),
        notional=Decimal("100"),
    client_order_id="test-close",
    )
    assert not durable.approve_emergency_close(
        account_id="acct-a", intent_id="intent-1", symbol="OPT:CE",
        side="SELL", quantity=100, limit_price=Decimal("10"),
        notional=Decimal("1000"),
    client_order_id="test-close",
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


def test_manual_review_state_never_authorizes_another_close(journal):
    durable, _ = journal
    durable.configure_recovery_budget(
        account_id="acct-a", intent_id="intent-manual", symbol="OPT:CE",
        max_attempts=2, max_notional=Decimal("1000"), approved=True,
    )
    assert durable.acquire_instrument_leases(
        account_id="acct-a", intent_id="intent-manual", symbols=("OPT:CE",),
    )
    durable.freeze_intent(
        account_id="acct-a", intent_id="intent-manual", reason="recovery ambiguity",
    )
    durable.set_state(
        account_id="acct-a", intent_id="intent-manual",
        state=RecoveryState.MANUAL_REVIEW, reason="broker snapshot became ambiguous",
    )
    with pytest.raises(ValueError, match="manual review cannot resume"):
        durable.set_state(
            account_id="acct-a", intent_id="intent-manual",
            state=RecoveryState.RECOVERING, reason="unsafe automatic retry",
        )
    with pytest.raises(RuntimeError, match="EMERGENCY_UNWIND"):
        durable.claim_recovery_attempt(
            account_id="acct-a", intent_id="intent-manual", symbol="OPT:CE",
        )
    assert not durable.approve_emergency_close(
        account_id="acct-a", intent_id="intent-manual", symbol="OPT:CE",
        side="SELL", quantity=1, limit_price=Decimal("10"),
        notional=Decimal("10"),
    client_order_id="test-close",
    )


def test_close_approval_budget_survives_journal_reconstruction(journal):
    durable, factory = journal
    durable.configure_recovery_budget(
        account_id="acct-restart", intent_id="intent-restart", symbol="OPT:CE",
        max_attempts=1, max_notional=Decimal("1000"), approved=True,
    )
    assert durable.acquire_instrument_leases(
        account_id="acct-restart", intent_id="intent-restart", symbols=("OPT:CE",),
    )
    durable.freeze_intent(
        account_id="acct-restart", intent_id="intent-restart", reason="partial fill",
    )
    durable.claim_recovery_attempt(
        account_id="acct-restart", intent_id="intent-restart", symbol="OPT:CE",
    )
    assert durable.approve_emergency_close(
        account_id="acct-restart", intent_id="intent-restart", symbol="OPT:CE",
        side="SELL", quantity=2, limit_price=Decimal("25"),
        notional=Decimal("50"),
    client_order_id="test-close",
    )

    # Reconstruct the persistence adapter as if the process had restarted.
    restarted = DurableRecoveryJournal(factory)
    assert not restarted.approve_emergency_close(
        account_id="acct-restart", intent_id="intent-restart", symbol="OPT:CE",
        side="SELL", quantity=2, limit_price=Decimal("25"),
        notional=Decimal("50"),
    client_order_id="test-close",
    )
    with factory() as db:
        budget = db.execute(select(RecoveryBudgetRecord).where(
            RecoveryBudgetRecord.account_id == "acct-restart",
            RecoveryBudgetRecord.intent_id == "intent-restart",
            RecoveryBudgetRecord.symbol == "OPT:CE",
        )).scalar_one()
        approvals = db.execute(select(RecoveryJournalEventRecord).where(
            RecoveryJournalEventRecord.control_id == db.execute(
                select(RecoveryControlRecord.id).where(
                    RecoveryControlRecord.account_id == "acct-restart",
                    RecoveryControlRecord.intent_id == "intent-restart",
                )
            ).scalar_one(),
            RecoveryJournalEventRecord.event == "EMERGENCY_CLOSE_BUDGET_APPROVED",
        )).scalars().all()
        assert budget.attempts_used == 1
        assert budget.notional_used == Decimal("50")
        assert len(approvals) == 1


def test_close_authorization_remains_pending_until_explicit_broker_reconciliation(journal):
    durable, factory = journal
    durable.configure_recovery_budget(
        account_id="acct-pending", intent_id="intent-pending", symbol="OPT:CE",
        max_attempts=1, max_notional=Decimal("1000"), approved=True,
    )
    durable.acquire_instrument_leases(
        account_id="acct-pending", intent_id="intent-pending", symbols=("OPT:CE",),
    )
    durable.freeze_intent(
        account_id="acct-pending", intent_id="intent-pending", reason="partial fill",
    )
    durable.claim_recovery_attempt(
        account_id="acct-pending", intent_id="intent-pending", symbol="OPT:CE",
    )
    assert durable.approve_emergency_close(
        account_id="acct-pending", intent_id="intent-pending", symbol="OPT:CE",
        side="SELL", quantity=1, limit_price=Decimal("10"), notional=Decimal("10"),
        client_order_id="REC-crash-window",
    )

    restarted = DurableRecoveryJournal(factory)
    assert restarted.pending_close_authorizations(
        account_id="acct-pending", intent_id="intent-pending",
    ) == ("REC-crash-window",)
    restarted.mark_close_authorization_reconciled(
        account_id="acct-pending", intent_id="intent-pending",
        client_order_id="REC-crash-window",
    )
    assert restarted.pending_close_authorizations(
        account_id="acct-pending", intent_id="intent-pending",
    ) == ()


def test_unknown_close_id_cannot_be_marked_reconciled_or_poison_future_authorization(journal):
    durable, _ = journal
    durable.freeze_intent(
        account_id="acct-unknown", intent_id="intent-unknown", reason="partial fill",
    )
    with pytest.raises(ValueError, match="previously approved"):
        durable.mark_close_authorization_reconciled(
            account_id="acct-unknown", intent_id="intent-unknown",
            client_order_id="REC-future-id",
        )
    durable.configure_recovery_budget(
        account_id="acct-unknown", intent_id="intent-unknown", symbol="OPT:CE",
        max_attempts=1, max_notional=Decimal("1000"), approved=True,
    )
    durable.acquire_instrument_leases(
        account_id="acct-unknown", intent_id="intent-unknown", symbols=("OPT:CE",),
    )
    durable.claim_recovery_attempt(
        account_id="acct-unknown", intent_id="intent-unknown", symbol="OPT:CE",
    )
    assert durable.approve_emergency_close(
        account_id="acct-unknown", intent_id="intent-unknown", symbol="OPT:CE",
        side="SELL", quantity=1, limit_price=Decimal("10"), notional=Decimal("10"),
        client_order_id="REC-future-id",
    )
    assert durable.pending_close_authorizations(
        account_id="acct-unknown", intent_id="intent-unknown",
    ) == ("REC-future-id",)
