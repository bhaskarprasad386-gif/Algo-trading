from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.execution.paper_routes import (
    PaperExitRequest,
    PaperOrderRequest,
    ScannerPaperEntryRequest,
    paper_exit,
    paper_from_scanner,
    paper_order,
    paper_position,
)
from app.models import TradingAccount, User


def _session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _paper_user(db):
    user = User(username="paper-e2e")
    db.add(user)
    db.flush()
    account = TradingAccount(user_id=user.id, mode="PAPER", virtual_balance=1_000_000.0, realized_pnl=0.0)
    db.add(account)
    db.commit()
    return user


def test_paper_buy_hold_exit_persists_balance_and_realized_pnl():
    db = _session()
    user = _paper_user(db)

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


def test_cash_future_scanner_opportunity_enters_paper_and_preserves_metadata():
    db = _session()
    user = _paper_user(db)

    result = paper_from_scanner(
        ScannerPaperEntryRequest(
            symbol="ABC",
            cash_price=100.0,
            quantity=10.0,
            future_price=106.0,
            gap=6.0,
            net_profit=42.5,
            executable=True,
        ),
        user_id=user.id,
        db=db,
    )

    assert result["status"] == "success"
    assert result["mode"] == "paper"
    assert result["source"] == "cash-future-scanner"
    assert result["scanner_entry_price"] == 100.0
    assert result["scanner_future_price"] == 106.0
    assert result["scanner_gap"] == 6.0
    assert result["scanner_net_profit"] == 42.5
    assert result["order"]["transaction_type"] == "BUY"
    assert result["order"]["price"] == 100.0
    assert result["position"]["symbol"] == "ABC"
    assert result["virtual_balance"] == 999_000.0

    position = paper_position(user_id=user.id, db=db)
    assert position["status"] == "active"
    assert position["position"]["symbol"] == "ABC"
    assert position["position"]["entry_price"] == 100.0
