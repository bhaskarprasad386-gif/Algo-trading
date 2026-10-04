from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.database import Base
from app.models import User, Position, TradingAccount, Order
from app.execution.paper_routes import PaperOrderRequest, paper_order, _reconcile_paper_ledger, _validate_paper_state

def test_paper_position_scope_flip_is_detected_and_blocks_repair(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'position-scope.db'}", connect_args={"check_same_thread": False, "timeout": 10})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        user = User(email="position-scope@example.com", hashed_password="", full_name="Position Scope", is_active=True)
        db.add(user)
        db.flush()
        db.add(TradingAccount(user_id=user.id, mode="PAPER", virtual_balance=1000.0,
                               initial_virtual_balance=1000.0, initial_balance_source="BOOTSTRAP",
                               realized_pnl=0.0, is_active=True))
        db.commit()
        paper_order(PaperOrderRequest(symbol="SCOPE", transaction_type="BUY", price=10.0, quantity=1, fill_id="SCOPE-1"),
                    user_id=user.id, db=db)
        position = db.query(Position).filter(Position.user_id == user.id, Position.symbol == "SCOPE").one()
        position.is_paper = False
        db.commit()

        result = _reconcile_paper_ledger(db, user.id)
        assert result["status"] == "MISMATCH"
        assert "missing_position:SCOPE" in result["mismatches"]
        assert result["repairability"] == "BLOCKED"
        assert result["repair_plan"]["apply"] is False

        # A mutation must not continue from a scope-corrupted paper ledger.
        try:
            _validate_paper_state(db, user.id)
            raise AssertionError("scope-corrupted paper ledger must fail closed")
        except RuntimeError:
            pass
    finally:
        db.close()
        engine.dispose()
