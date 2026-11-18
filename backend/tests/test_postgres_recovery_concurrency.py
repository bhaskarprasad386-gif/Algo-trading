from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import os
import uuid

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.execution.durable_recovery_journal import DurableRecoveryJournal
from app.models.execution_recovery_journal import (
    RecoveryBudgetRecord, RecoveryInstrumentLeaseRecord,
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
        ).order_by(RecoveryInstrumentLeaseRecord.symbol)).scalars().all()
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
