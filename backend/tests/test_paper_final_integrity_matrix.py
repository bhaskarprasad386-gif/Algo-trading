from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Order, Position, TradingAccount, User


def _seed_paper_db(tmp_path, name="paper-final-matrix.db"):
    engine = create_engine(
        f"sqlite:///{tmp_path / name}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    user = User(
        email=f"{name}@example.com",
        hashed_password="",
        full_name="Final Integrity Matrix",
        is_active=True,
    )
    db.add(user)
    db.flush()
    account = TradingAccount(
        user_id=user.id,
        mode="PAPER",
        virtual_balance=1000.0,
        initial_virtual_balance=1000.0,
        initial_balance_source="BOOTSTRAP",
        realized_pnl=0.0,
        is_active=True,
    )
    db.add(account)
    db.commit()
    return engine, db, user, account


def test_repair_precondition_distinguishes_nullable_numeric_state(tmp_path):
    from app.execution.paper_routes import (
        PaperOrderRequest,
        _paper_repair_precondition,
        paper_order,
    )

    engine, db, user, account = _seed_paper_db(tmp_path, "nullable-fingerprint.db")
    try:
        paper_order(
            PaperOrderRequest(
                symbol="NULLABLE",
                transaction_type="BUY",
                price=100.0,
                quantity=1,
                fill_id="NULLABLE-1",
            ),
            user_id=user.id,
            db=db,
        )
        order = (
            db.query(Order)
            .filter(Order.user_id == user.id, Order.fill_id == "NULLABLE-1")
            .one()
        )
        position = (
            db.query(Position)
            .filter(Position.user_id == user.id, Position.symbol == "NULLABLE")
            .one()
        )

        baseline = _paper_repair_precondition(
            db, user.id, account, [order], [position]
        )["state_hash"]

        order.trigger_price = 0.0
        db.commit()
        assert _paper_repair_precondition(
            db, user.id, account, [order], [position]
        )["state_hash"] != baseline

        order.trigger_price = None
        position.stop_loss = 0.0
        db.commit()
        assert _paper_repair_precondition(
            db, user.id, account, [order], [position]
        )["state_hash"] != baseline

        position.stop_loss = None
        position.target = 0.0
        db.commit()
        assert _paper_repair_precondition(
            db, user.id, account, [order], [position]
        )["state_hash"] != baseline
    finally:
        db.close()
        engine.dispose()


def test_reconcile_combined_account_audit_position_corruption_blocks_repair(tmp_path):
    from app.execution.paper_routes import (
        PaperOrderRequest,
        _reconcile_paper_ledger,
        _validate_paper_state,
        paper_order,
    )

    engine, db, user, account = _seed_paper_db(tmp_path, "combined-integrity-barrier.db")
    try:
        paper_order(
            PaperOrderRequest(
                symbol="COMBINED",
                transaction_type="BUY",
                price=100.0,
                quantity=1,
                fill_id="COMBINED-1",
            ),
            user_id=user.id,
            db=db,
        )
        order = (
            db.query(Order)
            .filter(Order.user_id == user.id, Order.fill_id == "COMBINED-1")
            .one()
        )
        position = (
            db.query(Position)
            .filter(Position.user_id == user.id, Position.symbol == "COMBINED")
            .one()
        )

        order.previous_audit_hash = "0" * 64
        position.last_price = -1.0
        account.realized_pnl = 5.0
        db.commit()

        payload = _reconcile_paper_ledger(db, user.id)
        assert payload["status"] == "MISMATCH"
        assert payload["repairability"] == "BLOCKED"
        assert payload["repairability_reason"] == "ledger_or_baseline_integrity_failure"
        assert "AUDIT_INTEGRITY" in payload["mismatch_categories"]
        assert "POSITION_STATE" in payload["mismatch_categories"]
        assert "ACCOUNTING_STATE" in payload["mismatch_categories"]
        assert payload["repair_plan"]["apply"] is False

        try:
            _validate_paper_state(db, user.id)
            raise AssertionError("combined corruption must fail closed")
        except RuntimeError:
            pass
    finally:
        db.close()
        engine.dispose()
