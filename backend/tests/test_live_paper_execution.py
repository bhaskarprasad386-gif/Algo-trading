from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.execution.live_paper import LivePaperExecution
from app.models import TradingAccount


def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_market_order_uses_ask_and_persists_fill():
    db = session()
    account = TradingAccount(user_id=1)
    db.add(account)
    db.commit()
    service = LivePaperExecution()
    order = service.place(db, user_id=1, symbol="RELIANCE", side="BUY", quantity=10)
    fills = service.on_tick(db, user_id=1, tick={"symbol": "RELIANCE", "ltp": 100, "bid": 99, "ask": 101})
    assert fills[0]["price"] == 101
    assert order.status == "FILLED"
    assert order.filled_quantity == 10


def test_limit_order_waits_then_fills():
    db = session()
    db.add(TradingAccount(user_id=1))
    db.commit()
    service = LivePaperExecution()
    order = service.place(db, user_id=1, symbol="SBIN", side="BUY", quantity=5, order_type="LIMIT", price=100)
    assert service.on_tick(db, user_id=1, tick={"symbol": "SBIN", "ltp": 101, "bid": 100, "ask": 101}) == []
    fills = service.on_tick(db, user_id=1, tick={"symbol": "SBIN", "ltp": 99, "bid": 99, "ask": 100})
    assert fills[0]["price"] == 100
    assert order.status == "FILLED"


def test_cancel_is_idempotent():
    db = session()
    db.add(TradingAccount(user_id=1))
    db.commit()
    service = LivePaperExecution()
    order = service.place(db, user_id=1, symbol="TCS", side="BUY", quantity=1)
    service.cancel(db, user_id=1, order_id=order.order_id)
    assert service.cancel(db, user_id=1, order_id=order.order_id).status == "CANCELLED"
