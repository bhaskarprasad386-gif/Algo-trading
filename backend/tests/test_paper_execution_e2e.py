from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.execution.paper_routes import PaperOrderRequest, PaperExitRequest, paper_exit, paper_order, paper_position
from app.models import TradingAccount, User


def _session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_paper_buy_hold_exit_persists_balance_and_realized_pnl():
    db = _session()
    user = User(username="paper-e2e")
    db.add(user)
    db.flush()
    account = TradingAccount(user_id=user.id, mode="PAPER", virtual_balance=1_000_000.0, realized_pnl=0.0)
    db.add(account)
    db.commit()

    entry = paper_order(
        PaperOrderRequest(symbol="TEST", transaction_type="BUY", price=100.0, quantity=10.0),
        user_id=user.id,
        db=db,
    )
    assert entry["status"] == "success"
    assert entry["position"]["symbol"] == "TEST"
    assert entry["virtual_balance"] == 999_000.0
    assert entry["realized_pnl"] == 0.0

    position = paper_position(user_id=user.id, db=db)
    assert position["status"] == "active"
    assert position["position"]["quantity"] == 10.0
    assert position["position"]["entry_price"] == 100.0

    exit_result = paper_exit(PaperExitRequest(price=110.0), user_id=user.id, db=db)
    assert exit_result["status"] == "closed"
    assert exit_result["quantity"] == 10.0
    assert exit_result["pnl"] == 100.0
    assert exit_result["virtual_balance"] == 1_000_100.0
    assert exit_result["realized_pnl"] == 100.0

    flat = paper_position(user_id=user.id, db=db)
    assert flat["status"] == "flat"
    assert flat["position"] is None
