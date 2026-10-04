from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.database import Base
from app.models import User, Position, TradingAccount, Order
from app.execution.paper_routes import PaperOrderRequest, paper_order, _paper_repair_precondition

def test_repair_precondition_preserves_raw_identity_metadata(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'raw-identity.db'}", connect_args={"check_same_thread": False, "timeout": 10})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        user = User(email="raw-identity@example.com", hashed_password="", full_name="Raw Identity", is_active=True)
        db.add(user)
        db.flush()
        db.add(TradingAccount(user_id=user.id, mode="PAPER", virtual_balance=1000.0,
                               initial_virtual_balance=1000.0, initial_balance_source="BOOTSTRAP",
                               realized_pnl=0.0, is_active=True))
        db.commit()
        paper_order(PaperOrderRequest(symbol="RAW", transaction_type="BUY", price=10.0, quantity=1, fill_id="RAW-1"),
                    user_id=user.id, db=db)
        position = db.query(Position).filter(Position.user_id == user.id, Position.symbol == "RAW").one()
        order = db.query(Order).filter_by(user_id=user.id).one()

        baseline = _paper_repair_precondition(db, user.id)["state_hash"]
        order.symbol = " RAW "
        assert _paper_repair_precondition(db, user.id)["state_hash"] != baseline
        order.symbol = "RAW"
        position.symbol = " RAW "
        assert _paper_repair_precondition(db, user.id)["state_hash"] != baseline
        position.symbol = "RAW"
        order.transaction_type = " buy "
        assert _paper_repair_precondition(db, user.id)["state_hash"] != baseline
    finally:
        db.close()
        engine.dispose()


def test_repair_precondition_distinguishes_null_and_empty_string_metadata(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'nullable-strings.db'}", connect_args={"check_same_thread": False, "timeout": 10})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        user = User(email="nullable-strings@example.com", hashed_password="", full_name="Nullable Strings", is_active=True)
        db.add(user)
        db.flush()
        db.add(TradingAccount(user_id=user.id, mode="PAPER", virtual_balance=1000.0,
                               initial_virtual_balance=1000.0, initial_balance_source="BOOTSTRAP",
                               realized_pnl=0.0, is_active=True))
        db.commit()
        paper_order(PaperOrderRequest(symbol="NULLABLE", transaction_type="BUY", price=10.0, quantity=1, fill_id="NULLABLE-1"), user_id=user.id, db=db)
        account = db.query(TradingAccount).filter_by(user_id=user.id).one()
        baseline = _paper_repair_precondition(db, user.id, account, db.query(Order).filter_by(user_id=user.id).all(), db.query(Position).filter_by(user_id=user.id, is_paper=True).all())["state_hash"]
        account.initial_balance_source = None
        null_hash = _paper_repair_precondition(db, user.id, account, db.query(Order).filter_by(user_id=user.id).all(), db.query(Position).filter_by(user_id=user.id, is_paper=True).all())["state_hash"]
        assert null_hash != baseline
        account.initial_balance_source = ""
        empty_hash = _paper_repair_precondition(db, user.id, account, db.query(Order).filter_by(user_id=user.id).all(), db.query(Position).filter_by(user_id=user.id, is_paper=True).all())["state_hash"]
        assert empty_hash != null_hash
    finally:
        db.close()
        engine.dispose()


def test_repair_precondition_raw_nullable_order_metadata_is_distinct(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'raw-nullable-order-meta.db'}", connect_args={"check_same_thread": False, "timeout": 10})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        user = User(email="raw-nullable@example.com", hashed_password="", full_name="Raw Nullable", is_active=True)
        db.add(user)
        db.flush()
        account = TradingAccount(user_id=user.id, mode="PAPER", virtual_balance=1000.0,
                                 initial_virtual_balance=1000.0, initial_balance_source="BOOTSTRAP",
                                 realized_pnl=0.0, is_active=True)
        db.add(account)
        db.commit()
        paper_order(PaperOrderRequest(symbol="NULLMETA", transaction_type="BUY", price=10.0, quantity=1, fill_id="NULLMETA-1"), user_id=user.id, db=db)
        order = db.query(Order).filter_by(user_id=user.id).one()
        positions = db.query(Position).filter_by(user_id=user.id, is_paper=True).all()
        baseline = _paper_repair_precondition(db, user.id, account, [order], positions)["state_hash"]
        canonical = {
            "broker_order_id": None, "token": None, "exchange": None, "order_type": "MARKET",
            "product_type": "INTRADAY", "time_in_force": "DAY", "message": None,
        }
        for field, original in canonical.items():
            setattr(order, field, "")
            empty_hash = _paper_repair_precondition(db, user.id, account, [order], positions)["state_hash"]
            setattr(order, field, None)
            null_hash = _paper_repair_precondition(db, user.id, account, [order], positions)["state_hash"]
            assert empty_hash != null_hash
            assert empty_hash != baseline
            setattr(order, field, original)
    finally:
        db.close()
        engine.dispose()


def test_repair_precondition_order_input_order_is_deterministic(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'order-ordering.db'}", connect_args={"check_same_thread": False, "timeout": 10})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        user = User(email="ordering@example.com", hashed_password="", full_name="Ordering", is_active=True)
        db.add(user)
        db.flush()
        account = TradingAccount(user_id=user.id, mode="PAPER", virtual_balance=1000.0,
                                 initial_virtual_balance=1000.0, initial_balance_source="BOOTSTRAP",
                                 realized_pnl=0.0, is_active=True)
        db.add(account)
        db.commit()
        paper_order(PaperOrderRequest(symbol="ORD-A", transaction_type="BUY", price=10.0, quantity=1, fill_id="ORD-A-1"), user_id=user.id, db=db)
        paper_order(PaperOrderRequest(symbol="ORD-B", transaction_type="BUY", price=20.0, quantity=1, fill_id="ORD-B-1"), user_id=user.id, db=db)
        orders = db.query(Order).filter_by(user_id=user.id).order_by(Order.id.asc()).all()
        positions = db.query(Position).filter_by(user_id=user.id, is_paper=True).order_by(Position.id.asc()).all()
        forward = _paper_repair_precondition(db, user.id, account, orders, positions)
        reverse = _paper_repair_precondition(db, user.id, account, list(reversed(orders)), list(reversed(positions)))
        assert reverse["state_hash"] == forward["state_hash"]
        assert reverse["audit_head"] == forward["audit_head"]
    finally:
        db.close()
        engine.dispose()
