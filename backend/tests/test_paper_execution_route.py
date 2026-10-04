from fastapi.testclient import TestClient
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import User

from app.main import app
from app.core.database import SessionLocal
from app.models import Order, Position, TradingAccount


def _client_and_headers():
    db = SessionLocal()
    try:
        db.query(Position).delete()
        db.query(Order).delete()
        accounts = db.query(TradingAccount).all()
        for account in accounts:
            account.virtual_balance = 10_000_000.0
            account.realized_pnl = 0.0
            account.is_active = True
            account.mode = "PAPER"
        db.commit()
    finally:
        db.close()
    return TestClient(app), {}

def test_paper_entry_route_registered():
    paths = app.openapi().get("paths", {})
    assert "/api/v1/execution/paper/entry" in paths


def test_paper_entry_requires_no_authentication():
    client, _ = _client_and_headers()
    response = client.post(
        "/api/v1/execution/paper/entry",
        json={"price": 100.0, "quantity": 2, "stop_loss_pct": 0.05, "target_pct": 0.10},
    )
    assert response.status_code == 200


def test_paper_entry_persists_position_and_order_and_updates_balance():
    client, headers = _client_and_headers()
    starting_balance = 10_000_000.0

    response = client.post(
        "/api/v1/execution/paper/entry",
        headers=headers,
        json={"price": 100.0, "quantity": 2, "stop_loss_pct": 0.05, "target_pct": 0.10},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["mode"] == "paper"
    assert data["fill"] == {"price": 100.0, "quantity": 2.0}
    assert data["entry_price"] == 100.0
    assert data["stop_loss"] == 95.0
    assert data["target"] == 110.0
    assert data["virtual_balance"] == starting_balance - 200.0
    assert data["order"]["transaction_type"] == "BUY"

    position_response = client.get("/api/v1/execution/paper/position", headers=headers)
    assert position_response.status_code == 200
    position = position_response.json()["position"]
    assert position["symbol"] == "PAPER"
    assert position["quantity"] == 2.0
    assert position["entry_price"] == 100.0

    orders_response = client.get("/api/v1/execution/paper/orders", headers=headers)
    assert orders_response.status_code == 200
    orders = orders_response.json()["orders"]
    assert orders[-1]["transaction_type"] == "BUY"
    assert orders[-1]["price"] == 100.0
    assert orders[-1]["quantity"] == 2.0

    exit_response = client.post(
        "/api/v1/execution/paper/exit",
        headers=headers,
        json={"price": 105.0},
    )
    assert exit_response.status_code == 200
    exit_data = exit_response.json()
    assert exit_data["pnl"] == 10.0
    assert exit_data["realized_pnl"] == 10.0
    assert exit_data["virtual_balance"] == starting_balance + 10.0
    assert exit_data["order"]["transaction_type"] == "SELL"

    flat_response = client.get("/api/v1/execution/paper/position", headers=headers)
    assert flat_response.status_code == 200
    assert flat_response.json() == {"status": "flat", "position": None, "mark_to_market": None}

    orders_after_exit = client.get("/api/v1/execution/paper/orders", headers=headers)
    assert orders_after_exit.status_code == 200
    assert orders_after_exit.json()["orders"][-1]["transaction_type"] == "SELL"


def test_paper_entry_rejects_non_positive_values_for_authenticated_user():
    client, headers = _client_and_headers()
    response = client.post(
        "/api/v1/execution/paper/entry",
        headers=headers,
        json={"price": 0, "quantity": 1},
    )
    assert response.status_code == 422


def test_cash_future_scanner_bridge_creates_symbol_specific_paper_position():
    client, headers = _client_and_headers()
    response = client.post(
        "/api/v1/execution/paper/from-scanner",
        headers=headers,
        json={
            "symbol": "RELIANCE",
            "cash_price": 2500.0,
            "quantity": 2,
            "stop_loss_pct": 0.02,
            "target_pct": 0.04,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "cash-future-scanner"
    assert data["scanner_entry_price"] == 2500.0
    assert data["order"]["symbol"] == "RELIANCE"
    assert data["position"]["symbol"] == "RELIANCE"
    assert data["position"]["quantity"] == 2.0


def test_cash_future_scanner_bridge_requires_no_authentication():
    client, _ = _client_and_headers()
    response = client.post(
        "/api/v1/execution/paper/from-scanner",
        json={"symbol": "RELIANCE", "cash_price": 2500.0, "quantity": 2},
    )
    assert response.status_code == 200


def test_paper_short_reversal_deducts_cost_of_remaining_long():
    client, headers = _client_and_headers()
    starting_balance = 10_000_000.0

    short_response = client.post(
        "/api/v1/execution/paper/order",
        headers=headers,
        json={"symbol": "REVERSAL", "transaction_type": "SELL", "price": 100.0, "quantity": 5},
    )
    assert short_response.status_code == 200
    assert short_response.json()["virtual_balance"] == starting_balance - 500.0

    reversal_response = client.post(
        "/api/v1/execution/paper/order",
        headers=headers,
        json={"symbol": "REVERSAL", "transaction_type": "BUY", "price": 90.0, "quantity": 8},
    )
    assert reversal_response.status_code == 200
    data = reversal_response.json()

    # Cover 5 shorts: release 500 margin and realize +50 P&L.
    # Open the remaining 3-long reversal at 90: deduct 270 from cash.
    assert data["realized_pnl"] == 50.0
    assert data["virtual_balance"] == starting_balance - 220.0
    assert data["position"]["symbol"] == "REVERSAL"
    assert data["position"]["quantity"] == 3.0
    assert data["position"]["entry_price"] == 90.0


def test_paper_api_fails_closed_when_multiple_active_paper_accounts_exist():
    from app.execution.paper_routes import current_user_id
    from fastapi import HTTPException

    db = SessionLocal()
    try:
        db.query(TradingAccount).delete()
        db.commit()
        db.add_all([
            TradingAccount(
                user_id=101,
                mode="PAPER",
                virtual_balance=10_000_000.0,
                realized_pnl=0.0,
                is_active=True,
            ),
            TradingAccount(
                user_id=202,
                mode="PAPER",
                virtual_balance=10_000_000.0,
                realized_pnl=0.0,
                is_active=True,
            ),
        ])
        db.commit()

        try:
            current_user_id(db)
            assert False, "ambiguous paper identity must fail closed"
        except HTTPException as exc:
            assert exc.status_code == 409
            assert exc.detail == "multiple active paper trading accounts require authenticated user context"
    finally:
        db.query(TradingAccount).delete()
        db.commit()
        db.close()


def test_paper_identity_bootstrap_converges_under_two_first_request_race(tmp_path):
    from app.execution.paper_routes import current_user_id
    import threading

    engine = create_engine(
        f"sqlite:///{tmp_path / 'paper-bootstrap.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    seed = Session()
    try:
        seed.add(
            User(
                email="system@local.algo-trading",
                hashed_password="",
                full_name="Algo Trading System",
                is_active=True,
            )
        )
        seed.commit()
    finally:
        seed.close()

    barrier = threading.Barrier(2)

    def first_request():
        db = Session()
        try:
            barrier.wait(timeout=5)
            return current_user_id(db)
        finally:
            db.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: first_request(), range(2)))

        assert results[0] == results[1]

        verify = Session()
        try:
            accounts = (
                verify.query(TradingAccount)
                .filter(
                    TradingAccount.mode == "PAPER",
                    TradingAccount.is_active.is_(True),
                )
                .all()
            )
            assert len(accounts) == 1
            assert accounts[0].user_id == results[0]
        finally:
            verify.close()
    finally:
        engine.dispose()


def test_same_user_concurrent_paper_orders_serialize_balance_and_position(tmp_path):
    from app.execution.paper_routes import PaperOrderRequest, paper_order
    from fastapi import HTTPException
    import threading

    engine = create_engine(
        f"sqlite:///{tmp_path / 'paper-order-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    seed = Session()
    try:
        user = User(
            email="paper-race@example.com",
            hashed_password="",
            full_name="Paper Race",
            is_active=True,
        )
        seed.add(user)
        seed.flush()
        seed.add(
            TradingAccount(
                user_id=user.id,
                mode="PAPER",
                virtual_balance=1_000.0,
                realized_pnl=0.0,
                is_active=True,
            )
        )
        seed.commit()
        user_id = user.id
    finally:
        seed.close()

    barrier = threading.Barrier(2)

    def submit():
        db = Session()
        try:
            barrier.wait(timeout=5)
            try:
                result = paper_order(
                    PaperOrderRequest(
                        symbol="RACE",
                        transaction_type="BUY",
                        price=100.0,
                        quantity=5,
                    ),
                    user_id=user_id,
                    db=db,
                )
                return ("ok", result)
            except HTTPException as exc:
                db.rollback()
                return ("http", exc.status_code, exc.detail)
        finally:
            db.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: submit(), range(2)))

        assert sum(1 for result in results if result[0] == "ok") == 1
        rejected = [result for result in results if result[0] == "http"]
        assert len(rejected) == 1
        assert rejected[0][1] == 409

        verify = Session()
        try:
            account = verify.query(TradingAccount).filter(
                TradingAccount.user_id == user_id
            ).one()
            positions = verify.query(Position).filter(
                Position.user_id == user_id,
                Position.symbol == "RACE",
                Position.quantity != 0,
            ).all()
            orders = verify.query(Order).filter(
                Order.user_id == user_id,
                Order.symbol == "RACE",
            ).all()
            assert account.virtual_balance == 500.0
            assert len(positions) == 1
            assert positions[0].quantity == 5
            assert len(orders) == 1
        finally:
            verify.close()
    finally:
        engine.dispose()


def test_paper_long_to_short_reversal_preserves_realized_pnl_and_short_margin():
    client, headers = _client_and_headers()
    starting_balance = 10_000_000.0

    opened = client.post(
        "/api/v1/execution/paper/order",
        headers=headers,
        json={"symbol": "LONGREV", "transaction_type": "BUY", "price": 100.0, "quantity": 5},
    )
    assert opened.status_code == 200

    reversed_response = client.post(
        "/api/v1/execution/paper/order",
        headers=headers,
        json={"symbol": "LONGREV", "transaction_type": "SELL", "price": 120.0, "quantity": 8},
    )
    assert reversed_response.status_code == 200
    data = reversed_response.json()

    # Close 5-long: +100 realized and +600 sale proceeds from the
    # previously paid 500 cost. Open the remaining 3-short by reserving
    # 360 at the reversal price, leaving 9,999,840 cash.
    assert data["realized_pnl"] == 100.0
    assert data["virtual_balance"] == starting_balance - 160.0
    assert data["position"]["quantity"] == -3.0
    assert data["position"]["entry_price"] == 120.0

    covered = client.post(
        "/api/v1/execution/paper/order",
        headers=headers,
        json={"symbol": "LONGREV", "transaction_type": "BUY", "price": 110.0, "quantity": 3},
    )
    assert covered.status_code == 200
    covered_data = covered.json()

    # Release the 360 short margin and realize another +30.
    assert covered_data["realized_pnl"] == 130.0
    assert covered_data["virtual_balance"] == starting_balance + 130.0
    assert covered_data["position"] is None

def test_paper_partial_short_cover_preserves_margin_pnl_and_remaining_short():
    client, headers = _client_and_headers()
    starting_balance = 10_000_000.0

    opened = client.post(
        "/api/v1/execution/paper/order",
        headers=headers,
        json={"symbol": "PARTIAL", "transaction_type": "SELL", "price": 100.0, "quantity": 5},
    )
    assert opened.status_code == 200

    covered = client.post(
        "/api/v1/execution/paper/order",
        headers=headers,
        json={"symbol": "PARTIAL", "transaction_type": "BUY", "price": 90.0, "quantity": 2},
    )
    assert covered.status_code == 200
    data = covered.json()

    # Release 2/5 of the original 500 margin and realize +20.
    assert data["realized_pnl"] == 20.0
    assert data["virtual_balance"] == starting_balance - 280.0
    assert data["position"]["quantity"] == -3.0
    assert data["position"]["entry_price"] == 100.0


def test_paper_reversal_and_exit_race_converges_to_one_terminal_transition(tmp_path):
    from app.execution.paper_routes import PaperOrderRequest, PaperExitRequest, paper_order, paper_exit

    engine = create_engine(
        f"sqlite:///{tmp_path / 'paper-reversal-exit-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    seed = Session()
    try:
        user = User(
            email="reversal-exit-race@example.com",
            hashed_password="",
            full_name="Reversal Exit Race",
            is_active=True,
        )
        seed.add(user)
        seed.flush()
        seed.add(
            TradingAccount(
                user_id=user.id,
                mode="PAPER",
                virtual_balance=500.0,
                realized_pnl=0.0,
                is_active=True,
            )
        )
        seed.add(
            Position(
                user_id=user.id,
                symbol="RACE",
                quantity=-5,
                average_price=100.0,
            )
        )
        seed.commit()
        user_id = user.id
    finally:
        seed.close()

    barrier = threading.Barrier(2)

    def reversal():
        db = Session()
        try:
            barrier.wait(timeout=5)
            try:
                return ("order", paper_order(
                    PaperOrderRequest(
                        symbol="RACE",
                        transaction_type="BUY",
                        price=90.0,
                        quantity=5,
                    ),
                    user_id=user_id,
                    db=db,
                ))
            except HTTPException as exc:
                db.rollback()
                return ("http", exc.status_code, exc.detail)
        finally:
            db.close()

    def exit_position():
        db = Session()
        try:
            barrier.wait(timeout=5)
            try:
                return ("exit", paper_exit(
                    PaperExitRequest(symbol="RACE", price=90.0),
                    user_id=user_id,
                    db=db,
                ))
            except HTTPException as exc:
                db.rollback()
                return ("http", exc.status_code, exc.detail)
        finally:
            db.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(reversal)
            second = pool.submit(exit_position)
            results = [first.result(), second.result()]

        successful = [result for result in results if result[0] in {"order", "exit"}]
        assert len(successful) == 1

        verify = Session()
        try:
            account = verify.query(TradingAccount).filter(
                TradingAccount.user_id == user_id
            ).one()
            positions = verify.query(Position).filter(
                Position.user_id == user_id,
                Position.symbol == "RACE",
                Position.quantity != 0,
            ).all()
            orders = verify.query(Order).filter(
                Order.user_id == user_id,
                Order.symbol == "RACE",
            ).all()
            assert len(orders) == 1
            assert len(positions) == 0
            # Both operations close the 5-short at 90: +50 realized, and
            # release the original 500 short margin.
            assert account.virtual_balance == 550.0
            assert account.realized_pnl == 50.0
        finally:
            verify.close()
    finally:
        engine.dispose()
