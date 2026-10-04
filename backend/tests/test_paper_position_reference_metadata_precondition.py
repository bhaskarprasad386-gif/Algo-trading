from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.database import Base
from app.models import User, Position, TradingAccount
from app.execution.paper_routes import PaperOrderRequest, paper_order, _paper_repair_precondition

def test_paper_position_token_exchange_changes_invalidate_repair_precondition(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'position-ref-meta.db'}", connect_args={"check_same_thread": False, "timeout": 10})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        user = User(email="position-ref@example.com", hashed_password="", full_name="Position Ref", is_active=True)
        db.add(user)
        db.flush()
        db.add(TradingAccount(user_id=user.id, mode="PAPER", virtual_balance=1000.0,
                               initial_virtual_balance=1000.0, initial_balance_source="BOOTSTRAP",
                               realized_pnl=0.0, is_active=True))
        db.commit()
        paper_order(PaperOrderRequest(symbol="REF", transaction_type="BUY", price=10.0, quantity=1, fill_id="REF-1"),
                    user_id=user.id, db=db)
        position = db.query(Position).filter(Position.user_id == user.id, Position.symbol == "REF").one()
        baseline = _paper_repair_precondition(db, user.id)["state_hash"]

        position.token = "TOKEN-CHANGED"
        changed_token = _paper_repair_precondition(db, user.id)["state_hash"]
        assert changed_token != baseline

        position.token = None
        position.exchange = "NSE"
        changed_exchange = _paper_repair_precondition(db, user.id)["state_hash"]
        assert changed_exchange != baseline
    finally:
        db.close()
        engine.dispose()
