import inspect
from fastapi.testclient import TestClient
from fastapi import HTTPException
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


def test_concurrent_same_fill_id_mutates_cash_and_pnl_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.core.database import Base
    from app.models.account import TradingAccount
    from app.models.order import Order
    from app.models.user import User
    from app.execution.paper_routes import PaperOrderRequest, paper_order

    engine = create_engine(
        "sqlite:///" + str(tmp_path / "same-fill-race.db"),
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    seed = Session()
    try:
        user = User(email="same-fill-race@example.com", hashed_password="", full_name="Same Fill Race", is_active=True)
        seed.add(user)
        seed.flush()
        seed.add(TradingAccount(user_id=user.id, mode="PAPER", virtual_balance=1000.0, realized_pnl=0.0, is_active=True))
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
                return ("ok", paper_order(
                    PaperOrderRequest(symbol="RACE", transaction_type="BUY", price=100.0, quantity=5, fill_id="BROKER-RACE-1"),
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
            first = pool.submit(submit)
            second = pool.submit(submit)
            results = [first.result(), second.result()]

        successful = [item for item in results if item[0] == "ok"]
        assert len(successful) == 2
        assert sum(1 for item in successful if item[1].get("idempotent") is True) == 1

        verify = Session()
        try:
            account = verify.query(TradingAccount).filter(TradingAccount.user_id == user_id).one()
            orders = verify.query(Order).filter(Order.user_id == user_id, Order.fill_id == "BROKER-RACE-1").all()
            assert len(orders) == 1
            assert account.virtual_balance == 500.0
            assert account.realized_pnl == 0.0
        finally:
            verify.close()
    finally:
        engine.dispose()


def test_paper_exit_fill_id_remains_idempotent_after_position_is_closed():
    client, headers = _client_and_headers()
    first_entry = client.post(
        "/api/v1/execution/paper/order",
        headers=headers,
        json={"symbol":"EXITRETRY","transaction_type":"BUY","price":100.0,"quantity":5},
    )
    assert first_entry.status_code == 200

    first_exit = client.post(
        "/api/v1/execution/paper/exit",
        headers=headers,
        json={"symbol":"EXITRETRY","price":120.0,"fill_id":"EXIT-FILL-1"},
    )
    assert first_exit.status_code == 200
    first_data = first_exit.json()
    assert first_data["pnl"] == 100.0
    balance_after_exit = first_data["virtual_balance"]
    realized_after_exit = first_data["realized_pnl"]

    retry = client.post(
        "/api/v1/execution/paper/exit",
        headers=headers,
        json={"symbol":"EXITRETRY","price":120.0,"fill_id":"EXIT-FILL-1"},
    )
    assert retry.status_code == 200
    retry_data = retry.json()
    assert retry_data["idempotent"] is True
    assert retry_data["pnl"] == 100.0
    assert retry_data["virtual_balance"] == balance_after_exit
    assert retry_data["realized_pnl"] == realized_after_exit


def test_paper_fill_id_is_durable_across_client_retry_and_rejects_conflict():
    client, headers = _client_and_headers()
    starting_balance = 10_000_000.0

    first = client.post(
        "/api/v1/execution/paper/order",
        headers=headers,
        json={"symbol":"DURABLE","transaction_type":"BUY","price":100.0,"quantity":5,"fill_id":"BROKER-FILL-1"},
    )
    assert first.status_code == 200
    assert first.json()["virtual_balance"] == starting_balance - 500.0
    first_order_id = first.json()["order"]["id"]

    # A fresh HTTP client/session simulates a process restart/replay boundary.
    restarted_client = TestClient(app)
    retry = restarted_client.post(
        "/api/v1/execution/paper/order",
        headers=headers,
        json={"symbol":"DURABLE","transaction_type":"BUY","price":100.0,"quantity":5,"fill_id":"BROKER-FILL-1"},
    )
    assert retry.status_code == 200
    retry_data = retry.json()
    assert retry_data["idempotent"] is True
    assert retry_data["order"]["id"] == first_order_id
    assert retry_data["virtual_balance"] == starting_balance - 500.0

    orders = restarted_client.get("/api/v1/execution/paper/orders", headers=headers).json()["orders"]
    assert [item["id"] for item in orders].count(first_order_id) == 1

    conflict = restarted_client.post(
        "/api/v1/execution/paper/order",
        headers=headers,
        json={"symbol":"DURABLE","transaction_type":"BUY","price":101.0,"quantity":5,"fill_id":"BROKER-FILL-1"},
    )
    assert conflict.status_code == 409

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
    # Open the remaining 3-long reversal at 90: deduct 270 from cash.    assert data["realized_pnl"] == 50.0    assert data["virtual_balance"] == starting_balance - 220.0    assert data["position"]["symbol"] == "REVERSAL"    assert data["position"]["quantity"] == 3.0
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
    assert data["virtual_balance"] == starting_balance - 260.0
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

def test_paper_mixed_reversal_chain_preserves_cash_and_realized_pnl():
    client, headers = _client_and_headers()
    starting_balance = 10_000.0

    # Build a 10-long at 100: cash 9,000.
    opened = client.post(
        "/api/v1/execution/paper/order",
        headers=headers,
        json={"symbol": "CHAIN", "transaction_type": "BUY", "price": 100.0, "quantity": 10},
    )
    assert opened.status_code == 200
    assert opened.json()["virtual_balance"] == 9_000.0

    # Partial long close: sell 4 at 120 -> +480 cash, +80 realized.
    partial = client.post(
        "/api/v1/execution/paper/order",
        headers=headers,
        json={"symbol": "CHAIN", "transaction_type": "SELL", "price": 120.0, "quantity": 4},
    )
    assert partial.status_code == 200
    data = partial.json()
    assert data["virtual_balance"] == 9_480.0
    assert data["realized_pnl"] == 80.0
    assert data["position"]["quantity"] == 6.0
    assert data["position"]["entry_price"] == 100.0

    # Sell 8 at 110: close the remaining 6-long (+60), open 2-short,
    # and reserve 220 of short margin. Sale proceeds are never double-counted
    # with realized P&L.
    reverse_short = client.post(
        "/api/v1/execution/paper/order",
        headers=headers,
        json={"symbol": "CHAIN", "transaction_type": "SELL", "price": 110.0, "quantity": 8},
    )
    assert reverse_short.status_code == 200
    data = reverse_short.json()
    assert data["virtual_balance"] == 9_920.0
    assert data["realized_pnl"] == 140.0
    assert data["position"]["quantity"] == -2.0
    assert data["position"]["entry_price"] == 110.0

    # Cover one short at 100: release 110 margin and realize +10.
    partial_cover = client.post(
        "/api/v1/execution/paper/order",
        headers=headers,
        json={"symbol": "CHAIN", "transaction_type": "BUY", "price": 100.0, "quantity": 1},
    )
    assert partial_cover.status_code == 200
    data = partial_cover.json()
    assert data["virtual_balance"] == 10_040.0
    assert data["realized_pnl"] == 150.0
    assert data["position"]["quantity"] == -1.0
    assert data["position"]["entry_price"] == 110.0

    # Buy 3 at 100: cover the last short (+10) and open 2-long at 100.
    reverse_long = client.post(
        "/api/v1/execution/paper/order",
        headers=headers,
        json={"symbol": "CHAIN", "transaction_type": "BUY", "price": 100.0, "quantity": 3},
    )
    assert reverse_long.status_code == 200
    data = reverse_long.json()
    assert data["virtual_balance"] == 9_960.0
    assert data["realized_pnl"] == 160.0
    assert data["position"]["quantity"] == 2.0    assert data["position"]["entry_price"] == 100.0
    # Final long close at 90: receive 180 and realize -20.
    closed = client.post(        "/api/v1/execution/paper/exit",
        headers=headers,        json={"symbol": "CHAIN", "price": 90.0},
    )
    assert closed.status_code == 200
    data = closed.json()
    assert data["virtual_balance"] == starting_balance + 140.0
    assert data["realized_pnl"] == 140.0
    assert data["position"] is None

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


def test_paper_multi_symbol_portfolio_accounting_isolated_across_reversal_and_exit():
    client, headers = _client_and_headers()
    starting_balance = 10_000.0

    # Two independent symbols consume independent capital/positions.
    a = client.post(
        "/api/v1/execution/paper/order",
        headers=headers,
        json={"symbol": "ALPHA", "transaction_type": "BUY", "price": 100.0, "quantity": 10},
    )
    b = client.post(
        "/api/v1/execution/paper/order",
        headers=headers,
        json={"symbol": "BETA", "transaction_type": "SELL", "price": 50.0, "quantity": 4},
    )
    assert a.status_code == 200
    assert b.status_code == 200

    # Cash: 10,000 - 1,000 - 200 = 8,800.
    assert b.json()["virtual_balance"] == 8_800.0
    assert b.json()["realized_pnl"] == 0.0

    # Reverse only ALPHA: close 10-long at 120 (+200) and open 2-short
    # at 120, reserving 240 margin. BETA must remain untouched.
    reversal = client.post(
        "/api/v1/execution/paper/order",
        headers=headers,
        json={"symbol": "ALPHA", "transaction_type": "SELL", "price": 120.0, "quantity": 12},
    )
    assert reversal.status_code == 200
    data = reversal.json()
    assert data["realized_pnl"] == 200.0
    assert data["virtual_balance"] == 9_760.0
    assert data["position"]["symbol"] == "ALPHA"
    assert data["position"]["quantity"] == -2.0
    assert data["position"]["entry_price"] == 120.0

    beta = client.get(
        "/api/v1/execution/paper/position",
        headers=headers,
        params={"symbol": "BETA"},
    )
    assert beta.status_code == 200
    beta_data = beta.json()
    assert beta_data["position"]["symbol"] == "BETA"
    assert beta_data["position"]["quantity"] == -4.0
    assert beta_data["position"]["entry_price"] == 50.0

    # Exit only BETA at 40: +40 realized. ALPHA short and its margin remain.
    beta_exit = client.post(
        "/api/v1/execution/paper/exit",
        headers=headers,
        json={"symbol": "BETA", "price": 40.0},
    )
    assert beta_exit.status_code == 200
    data = beta_exit.json()
    assert data["pnl"] == 40.0
    assert data["realized_pnl"] == 240.0
    assert data["virtual_balance"] == 9_960.0
    assert data["position"] is None

    alpha = client.get(
        "/api/v1/execution/paper/position",
        headers=headers,
        params={"symbol": "ALPHA"},
    )
    assert alpha.status_code == 200
    alpha_data = alpha.json()
    assert alpha_data["position"]["quantity"] == -2.0
    assert alpha_data["position"]["entry_price"] == 120.0

    # Cover ALPHA: release 240 margin and realize another +40.
    alpha_exit = client.post(
        "/api/v1/execution/paper/exit",
        headers=headers,
        json={"symbol": "ALPHA", "price": 100.0},
    )
    assert alpha_exit.status_code == 200
    data = alpha_exit.json()
    assert data["pnl"] == 40.0
    assert data["realized_pnl"] == 280.0
    assert data["virtual_balance"] == starting_balance + 280.0
    assert data["position"] is None

def test_paper_multi_symbol_concurrent_mutations_preserve_each_position(tmp_path):
    from app.execution.paper_routes import PaperOrderRequest, paper_order

    engine = create_engine(
        f"sqlite:///{tmp_path / 'paper-multi-symbol-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    seed = Session()
    try:
        user = User(
            email="multi-symbol-race@example.com",
            hashed_password="",
            full_name="Multi Symbol Race",
            is_active=True,
        )
        seed.add(user)
        seed.flush()
        seed.add(
            TradingAccount(
                user_id=user.id,
                mode="PAPER",
                virtual_balance=10_000.0,
                realized_pnl=0.0,
                is_active=True,
            )
        )
        seed.commit()
        user_id = user.id
    finally:
        seed.close()

    barrier = threading.Barrier(2)

    def submit(symbol, price, quantity):
        db = Session()
        try:
            barrier.wait(timeout=5)
            try:
                return paper_order(
                    PaperOrderRequest(
                        symbol=symbol,
                        transaction_type="BUY",
                        price=price,
                        quantity=quantity,
                    ),
                    user_id=user_id,
                    db=db,
                )
            except HTTPException as exc:
                db.rollback()
                return {"status": "http", "code": exc.status_code, "detail": exc.detail}
        finally:
            db.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            alpha_future = pool.submit(submit, "ALPHA", 100.0, 10)
            beta_future = pool.submit(submit, "BETA", 200.0, 20)
            alpha = alpha_future.result()
            beta = beta_future.result()

        assert alpha["status"] == "success"
        assert beta["status"] == "success"

        verify = Session()
        try:
            account = verify.query(TradingAccount).filter(
                TradingAccount.user_id == user_id
            ).one()
            positions = verify.query(Position).filter(
                Position.user_id == user_id,
                Position.quantity != 0,
            ).order_by(Position.symbol.asc()).all()
            orders = verify.query(Order).filter(
                Order.user_id == user_id,
            ).all()

            assert account.virtual_balance == 5_000.0
            assert account.realized_pnl == 0.0
            assert [(p.symbol, p.quantity, p.average_price) for p in positions] == [
                ("ALPHA", 10, 100.0),
                ("BETA", 20, 200.0),
            ]
            assert len(orders) == 2
        finally:
            verify.close()
    finally:
        engine.dispose()


def test_paper_multi_symbol_concurrent_exit_and_reversal_preserve_accounting(tmp_path):
    from app.execution.paper_routes import PaperOrderRequest, PaperExitRequest, paper_order, paper_exit

    engine = create_engine(
        f"sqlite:///{tmp_path / 'paper-multi-symbol-close-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    seed = Session()
    try:
        user = User(
            email="multi-symbol-close-race@example.com",
            hashed_password="",
            full_name="Multi Symbol Close Race",
            is_active=True,
        )
        seed.add(user)
        seed.flush()
        seed.add(
            TradingAccount(
                user_id=user.id,
                mode="PAPER",
                virtual_balance=10_000.0,
                realized_pnl=0.0,
                is_active=True,
            )
        )
        seed.add_all(
            [
                Position(user_id=user.id, symbol="ALPHA", quantity=10, average_price=100.0),
                Position(user_id=user.id, symbol="BETA", quantity=-5, average_price=200.0),
            ]
        )
        # Reserve the two open positions: 1,000 long cost + 1,000 short margin.
        seed.query(TradingAccount).filter(TradingAccount.user_id == user.id).one().virtual_balance = 8_000.0
        seed.commit()
        user_id = user.id
    finally:
        seed.close()

    barrier = threading.Barrier(2)

    def close_alpha():
        db = Session()
        try:
            barrier.wait(timeout=5)
            try:
                return ("alpha", paper_exit(
                    PaperExitRequest(symbol="ALPHA", price=120.0),
                    user_id=user_id,
                    db=db,
                ))
            except HTTPException as exc:
                db.rollback()
                return ("http", exc.status_code, exc.detail)
        finally:
            db.close()

    def reverse_beta():
        db = Session()
        try:
            barrier.wait(timeout=5)
            try:
                return ("beta", paper_order(
                    PaperOrderRequest(
                        symbol="BETA",
                        transaction_type="BUY",
                        price=180.0,
                        quantity=8,
                    ),
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
            first = pool.submit(close_alpha)
            second = pool.submit(reverse_beta)            results = [first.result(), second.result()]

        successful = [result for result in results if result[0] in {"alpha", "beta"}]        assert len(successful) == 2

        verify = Session()        try:
            account = verify.query(TradingAccount).filter(
                TradingAccount.user_id == user_id            ).one()
            positions = verify.query(Position).filter(
                Position.user_id == user_id,
                Position.quantity != 0,
            ).order_by(Position.symbol.asc()).all()
            orders = verify.query(Order).filter(Order.user_id == user_id).order_by(Order.id.asc()).all()

            # ALPHA closes for +200. BETA covers 5-short for +100 and
            # reverses the remaining 3 into a long at 180, costing 540.
            # Final cash = 8,000 + 1,200 + 1,000 + 100 - 540 = 9,760.
            assert account.virtual_balance == 9_760.0
            assert account.realized_pnl == 300.0
            assert [(p.symbol, p.quantity, p.average_price) for p in positions] == [
                ("BETA", 3, 180.0),
            ]
            assert len(orders) == 2
        finally:
            verify.close()
    finally:
        engine.dispose()


def test_paper_multi_symbol_partial_fills_and_reversals_preserve_accounting():
    client, headers = _client_and_headers()
    starting_balance = 10_000.0

    # ALPHA long and BETA short coexist.
    alpha = client.post(
        "/api/v1/execution/paper/order",
        headers=headers,
        json={"symbol": "ALPHA", "transaction_type": "BUY", "price": 100.0, "quantity": 10},
    )
    beta = client.post(
        "/api/v1/execution/paper/order",
        headers=headers,
        json={"symbol": "BETA", "transaction_type": "SELL", "price": 200.0, "quantity": 6},
    )
    assert alpha.status_code == 200
    assert beta.status_code == 200
    assert beta.json()["virtual_balance"] == 7_800.0

    # Partial ALPHA close: +80 realized, 6-long remains at 100.
    alpha_partial = client.post(
        "/api/v1/execution/paper/order",
        headers=headers,
        json={"symbol": "ALPHA", "transaction_type": "SELL", "price": 120.0, "quantity": 4},
    )
    assert alpha_partial.status_code == 200
    data = alpha_partial.json()
    assert data["virtual_balance"] == 8_280.0
    assert data["realized_pnl"] == 80.0
    assert data["position"]["quantity"] == 6.0
    assert data["position"]["entry_price"] == 100.0

    # Partial BETA cover: release 400 short margin and realize +40.
    beta_partial = client.post(
        "/api/v1/execution/paper/order",
        headers=headers,
        json={"symbol": "BETA", "transaction_type": "BUY", "price": 180.0, "quantity": 2},
    )
    assert beta_partial.status_code == 200
    data = beta_partial.json()
    assert data["virtual_balance"] == 8_720.0
    assert data["realized_pnl"] == 120.0
    assert data["position"]["quantity"] == -4.0
    assert data["position"]["entry_price"] == 200.0

    # ALPHA now reverses: close 6-long (+60), open 2-short and reserve 220.
    alpha_reverse = client.post(
        "/api/v1/execution/paper/order",
        headers=headers,
        json={"symbol": "ALPHA", "transaction_type": "SELL", "price": 110.0, "quantity": 8},
    )
    assert alpha_reverse.status_code == 200
    data = alpha_reverse.json()
    assert data["virtual_balance"] == 9_380.0
    assert data["realized_pnl"] == 180.0
    assert data["position"]["quantity"] == -2.0
    assert data["position"]["entry_price"] == 110.0

    # Finish BETA short: release 800 margin and realize +200.
    beta_exit = client.post(
        "/api/v1/execution/paper/exit",
        headers=headers,
        json={"symbol": "BETA", "price": 150.0},
    )
    assert beta_exit.status_code == 200
    data = beta_exit.json()
    assert data["virtual_balance"] == 10_380.0
    assert data["realized_pnl"] == 380.0
    assert data["position"] is None

    # Finish ALPHA short: release 220 margin and realize +20.
    alpha_exit = client.post(
        "/api/v1/execution/paper/exit",
        headers=headers,
        json={"symbol": "ALPHA", "price": 100.0},
    )
    assert alpha_exit.status_code == 200
    data = alpha_exit.json()
    assert data["pnl"] == 20.0
    assert data["virtual_balance"] == starting_balance + 420.0
    assert data["realized_pnl"] == 420.0
    assert data["position"] is None


def test_executed_fill_idempotency_prevents_duplicate_realized_pnl():
    from app.execution.fill_accounting import ExecutedFill, FillAccountingState, apply_executed_fill

    state = FillAccountingState(quantity=10, average_price=100.0, realized_pnl=0.0)

    first = apply_executed_fill(
        state,
        ExecutedFill(side="SELL", price=120.0, quantity=4, fill_id="FILL-1"),
    )
    assert first.quantity == 6
    assert first.average_price == 100.0
    assert first.realized_pnl == 80.0
    assert first.applied_fill_ids == ("FILL-1",)

    # Broker retry of the exact same execution must be a no-op.
    retry = apply_executed_fill(
        first,
        ExecutedFill(side="SELL", price=120.0, quantity=4, fill_id="FILL-1"),
    )
    assert retry == first

    # A genuinely different fill still applies normally.
    second = apply_executed_fill(
        retry,
        ExecutedFill(side="SELL", price=110.0, quantity=2, fill_id="FILL-2"),
    )
    assert second.quantity == 4
    assert second.average_price == 100.0
    assert second.realized_pnl == 100.0
    assert second.applied_fill_ids == ("FILL-1", "FILL-2")


def test_executed_fill_idempotency_also_holds_across_reversal():
    from app.execution.fill_accounting import ExecutedFill, FillAccountingState, apply_executed_fill

    state = FillAccountingState(quantity=-5, average_price=200.0, realized_pnl=0.0)

    first = apply_executed_fill(
        state,
        ExecutedFill(side="BUY", price=180.0, quantity=8, fill_id="REV-1"),
    )
    assert first.quantity == 3
    assert first.average_price == 180.0
    assert first.realized_pnl == 100.0

    retry = apply_executed_fill(
        first,
        ExecutedFill(side="BUY", price=180.0, quantity=8, fill_id="REV-1"),
    )
    assert retry == first

    # Different fill ID can close the remaining long and realize its own P&L.
    second = apply_executed_fill(
        retry,
        ExecutedFill(side="SELL", price=190.0, quantity=3, fill_id="REV-2"),
    )
    assert second.quantity == 0
    assert second.average_price == 0.0
    assert second.realized_pnl == 130.0
    assert second.applied_fill_ids == ("REV-1", "REV-2")


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


def test_paper_order_persists_filled_execution_fields_for_partial_lifecycle():
    client, headers = _client_and_headers()
    starting_balance = 10_000_000.0

    opened = client.post(
        "/api/v1/execution/paper/order",
        headers=headers,
        json={"symbol": "LIFECYCLE", "transaction_type": "BUY", "price": 100.0, "quantity": 10},    )
    assert opened.status_code == 200

    partial = client.post(        "/api/v1/execution/paper/order",
        headers=headers,
        json={"symbol": "LIFECYCLE", "transaction_type": "SELL", "price": 120.0, "quantity": 4},
    )    assert partial.status_code == 200

    closed = client.post(
        "/api/v1/execution/paper/exit",        headers=headers,
        json={"symbol": "LIFECYCLE", "price": 110.0},
    )
    assert closed.status_code == 200
    assert closed.json()["pnl"] == 60.0
    assert closed.json()["realized_pnl"] == 140.0
    assert closed.json()["virtual_balance"] == starting_balance + 140.0

    db = SessionLocal()
    try:
        orders = (
            db.query(Order)
            .filter(Order.symbol == "LIFECYCLE")
            .order_by(Order.id.asc())
            .all()
        )
        assert [(o.transaction_type, o.quantity, o.filled_quantity, o.price, o.average_price, o.average_fill_price, o.status, o.is_paper) for o in orders] == [
            ("BUY", 10, 10, 100.0, 100.0, 100.0, "FILLED", True),
            ("SELL", 4, 4, 120.0, 120.0, 120.0, "FILLED", True),
            ("SELL", 6, 6, 110.0, 110.0, 110.0, "FILLED", True),
        ]
    finally:
        db.close()


def test_paper_reversal_and_terminal_retry_keep_order_fill_fields_consistent():
    client, headers = _client_and_headers()

    opened = client.post(
        "/api/v1/execution/paper/order",
        headers=headers,
        json={"symbol": "REV-FIELDS", "transaction_type": "BUY", "price": 100.0, "quantity": 10},
    )
    assert opened.status_code == 200

    reversal = client.post(
        "/api/v1/execution/paper/order",
        headers=headers,
        json={
            "symbol": "REV-FIELDS",
            "transaction_type": "SELL",
            "price": 120.0,
            "quantity": 12,
            "fill_id": "REV-FIELDS-SELL-1",
        },
    )
    assert reversal.status_code == 200
    assert reversal.json()["realized_pnl"] == 200.0
    assert reversal.json()["position"]["quantity"] == -2.0
    assert reversal.json()["position"]["entry_price"] == 120.0

    terminal = client.post(
        "/api/v1/execution/paper/exit",
        headers=headers,
        json={"symbol": "REV-FIELDS", "price": 100.0, "fill_id": "REV-FIELDS-EXIT-1"},
    )
    assert terminal.status_code == 200
    assert terminal.json()["pnl"] == 40.0
    assert terminal.json()["realized_pnl"] == 240.0

    retry = client.post(
        "/api/v1/execution/paper/exit",
        headers=headers,
        json={"symbol": "REV-FIELDS", "price": 100.0, "fill_id": "REV-FIELDS-EXIT-1"},
    )
    assert retry.status_code == 200
    assert retry.json()["idempotent"] is True
    assert retry.json()["realized_pnl"] == 240.0

    db = SessionLocal()
    try:
        orders = (
            db.query(Order)
            .filter(Order.symbol == "REV-FIELDS")
            .order_by(Order.id.asc())
            .all()
        )
        assert len(orders) == 3
        assert all(order.status == "FILLED" for order in orders)
        assert all(order.is_paper is True for order in orders)
        assert [(o.quantity, o.filled_quantity, o.average_price, o.average_fill_price) for o in orders] == [
            (10, 10, 100.0, 100.0),
            (12, 12, 120.0, 120.0),
            (2, 2, 100.0, 100.0),
        ]
        assert orders[1].pnl == 200.0
        assert orders[2].pnl == 40.0
    finally:
        db.close()


def test_paper_execution_rejects_and_rolls_back_duplicate_active_position_invariant():
    _, headers = _client_and_headers()
    client = TestClient(app, raise_server_exceptions=False)
    db = SessionLocal()
    try:
        account = db.query(TradingAccount).order_by(TradingAccount.id.asc()).first()
        db.add_all([
            Position(user_id=account.user_id, symbol="CORRUPT", quantity=5, average_price=100.0, is_paper=True),
            Position(user_id=account.user_id, symbol="CORRUPT", quantity=3, average_price=110.0, is_paper=True),
        ])
        starting_balance = account.virtual_balance
        db.commit()
    finally:
        db.close()

    response = client.post(
        "/api/v1/execution/paper/order",
        headers=headers,
        json={"symbol": "CORRUPT", "transaction_type": "SELL", "price": 120.0, "quantity": 1},
    )
    assert response.status_code == 500

    verify = SessionLocal()
    try:
        account = verify.query(TradingAccount).order_by(TradingAccount.id.asc()).first()
        positions = verify.query(Position).filter(
            Position.user_id == account.user_id,
            Position.symbol == "CORRUPT",
        ).order_by(Position.id.asc()).all()
        orders = verify.query(Order).filter(
            Order.user_id == account.user_id,
            Order.symbol == "CORRUPT",
        ).all()
        assert account.virtual_balance == starting_balance
        assert account.realized_pnl == 0.0
        assert [(p.quantity, p.average_price) for p in positions] == [(5, 100.0), (3, 110.0)]
        assert orders == []
    finally:
        verify.close()


def test_paper_execution_rejects_and_rolls_back_corrupt_existing_order_invariant():
    _, headers = _client_and_headers()
    client = TestClient(app, raise_server_exceptions=False)
    db = SessionLocal()
    try:
        account = db.query(TradingAccount).order_by(TradingAccount.id.asc()).first()
        corrupt = Order(
            user_id=account.user_id,
            order_id=f"CORRUPT-{account.user_id}",
            symbol="ORDER-CORRUPT",
            transaction_type="BUY",
            quantity=5,
            price=100.0,
            average_price=100.0,
            filled_quantity=4,
            average_fill_price=100.0,
            status="FILLED",
            is_paper=True,
            pnl=0.0,
        )
        db.add(corrupt)
        starting_balance = account.virtual_balance
        db.commit()
    finally:
        db.close()

    response = client.post(
        "/api/v1/execution/paper/order",
        headers=headers,
        json={"symbol": "ORDER-CORRUPT", "transaction_type": "SELL", "price": 120.0, "quantity": 1},
    )
    assert response.status_code == 500

    verify = SessionLocal()
    try:
        account = verify.query(TradingAccount).order_by(TradingAccount.id.asc()).first()
        corrupt = verify.query(Order).filter(Order.order_id == f"CORRUPT-{account.user_id}").one()
        orders = verify.query(Order).filter(
            Order.user_id == account.user_id,
            Order.symbol == "ORDER-CORRUPT",
        ).all()
        positions = verify.query(Position).filter(
            Position.user_id == account.user_id,
            Position.symbol == "ORDER-CORRUPT",
        ).all()
        assert account.virtual_balance == starting_balance
        assert account.realized_pnl == 0.0
        assert corrupt.filled_quantity == 4
        assert len(orders) == 1
        assert positions == []
    finally:
        verify.close()


def test_paper_ledger_reconciliation_rebuilds_realized_pnl_position_and_balance():
    client, headers = _client_and_headers()
    for payload in [
        {"symbol": "RECON", "transaction_type": "BUY", "price": 100.0, "quantity": 10},
        {"symbol": "RECON", "transaction_type": "SELL", "price": 120.0, "quantity": 4},
        {"symbol": "RECON", "transaction_type": "SELL", "price": 110.0, "quantity": 8},
    ]:
        response = client.post("/api/v1/execution/paper/order", headers=headers, json=payload)
        assert response.status_code == 200

    response = client.get("/api/v1/execution/paper/reconcile", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "OK"
    assert data["reconstructed_realized_pnl"] == 180.0
    assert data["stored_realized_pnl"] == 180.0
    assert data["reconstructed_open_exposure"] == 220.0
    assert data["reconstructed_virtual_balance"] == 9_999_920.0
    assert data["stored_virtual_balance"] == 9_999_920.0
    assert data["repairability"] == "NONE"
    assert data["baseline_status"] == "BOOTSTRAP"
    assert data["repair_plan"]["apply"] is False
    assert data["mismatches"] == []


def test_paper_ledger_reconciliation_detects_state_mismatch_without_changing_it():
    client, headers = _client_and_headers()
    opened = client.post(
        "/api/v1/execution/paper/order",
        headers=headers,
        json={"symbol": "RECON-CHECK", "transaction_type": "BUY", "price": 100.0, "quantity": 5},
    )
    assert opened.status_code == 200

    db = SessionLocal()
    try:
        account = db.query(TradingAccount).order_by(TradingAccount.id.asc()).first()
        position = db.query(Position).filter(
            Position.user_id == account.user_id,
            Position.symbol == "RECON-CHECK",
        ).one()
        account.virtual_balance += 123.0
        position.average_price = 101.0
        db.commit()
    finally:
        db.close()

    response = client.get("/api/v1/execution/paper/reconcile", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "MISMATCH"
    assert "position_mismatch:RECON-CHECK" in data["mismatches"]
    assert "virtual_balance_mismatch" in data["mismatches"]
    assert data["repairability"] == "SAFE_DRY_RUN"
    assert data["repair_plan"]["apply"] is False

    verify = SessionLocal()
    try:
        account = verify.query(TradingAccount).order_by(TradingAccount.id.asc()).first()
        position = verify.query(Position).filter(
            Position.user_id == account.user_id,
            Position.symbol == "RECON-CHECK",
        ).one()
        assert account.virtual_balance == 9_999_623.0
        assert position.average_price == 101.0
    finally:
        verify.close()


def test_paper_ledger_reconciliation_blocks_repair_plan_for_corrupt_order():
    client, headers = _client_and_headers()
    db = SessionLocal()
    try:
        account = db.query(TradingAccount).order_by(TradingAccount.id.asc()).first()
        db.add(
            Order(
                user_id=account.user_id,
                order_id=f"BAD-RECON-{account.user_id}",
                symbol="BAD-RECON",
                transaction_type="BUY",
                quantity=5,
                price=100.0,
                average_price=100.0,
                filled_quantity=4,
                average_fill_price=100.0,
                status="FILLED",
                is_paper=True,
                pnl=0.0,
            )
        )
        db.commit()
    finally:
        db.close()

    response = client.get("/api/v1/execution/paper/reconcile", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "MISMATCH"
    assert data["repairability"] == "BLOCKED"
    assert data["repair_plan"]["apply"] is False
    assert any(item.startswith("invalid_order:") for item in data["mismatches"])

def test_paper_ledger_reconciliation_fingerprint_chain_is_valid_and_tamper_detected():
    client, headers = _client_and_headers()
    for payload in [
        {"symbol": "HASH", "transaction_type": "BUY", "price": 100.0, "quantity": 5},        {"symbol": "HASH", "transaction_type": "SELL", "price": 120.0, "quantity": 2},
    ]:
        response = client.post("/api/v1/execution/paper/order", headers=headers, json=payload)
        assert response.status_code == 200
    db = SessionLocal()
    try:
        orders = db.query(Order).filter(Order.symbol == "HASH").order_by(Order.id.asc()).all()
        assert len(orders) == 2
        assert all(len(order.audit_hash) == 64 for order in orders)        assert orders[0].previous_audit_hash is None
        assert orders[1].previous_audit_hash == orders[0].audit_hash
        orders[0].price = 101.0
        db.commit()
    finally:
        db.close()

    response = client.get("/api/v1/execution/paper/reconcile", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "MISMATCH"
    assert data["repairability"] == "BLOCKED"
    assert data["repair_plan"]["apply"] is False
    assert any(item.startswith("audit_hash_mismatch:") for item in data["mismatches"])


def test_paper_ledger_reconciliation_blocks_legacy_unfingerprinted_orders():
    client, headers = _client_and_headers()
    db = SessionLocal()
    try:
        account = db.query(TradingAccount).order_by(TradingAccount.id.asc()).first()
        db.add(
            Order(
                user_id=account.user_id,
                order_id=f"LEGACY-HASH-{account.user_id}",
                symbol="LEGACY-HASH",
                transaction_type="BUY",
                quantity=1,
                price=100.0,
                average_price=100.0,
                filled_quantity=1,
                average_fill_price=100.0,
                status="FILLED",
                is_paper=True,
                pnl=0.0,
                fill_id=None,
            )
        )
        db.commit()
    finally:
        db.close()

    response = client.get("/api/v1/execution/paper/reconcile", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["repairability"] == "BLOCKED"
    assert data["baseline_status"] == "LEGACY_UNFINGERPRINTED"
    assert any(item.startswith("audit_chain_mismatch:") for item in data["mismatches"])


def test_paper_concurrent_same_fill_id_preserves_single_audit_chain_entry(tmp_path):
    from app.execution.paper_routes import PaperOrderRequest, paper_order
    engine = create_engine(f"sqlite:///{tmp_path / 'audit-race.db'}", connect_args={"check_same_thread": False, "timeout": 10})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    seed = Session()
    try:
        user = User(email="audit-race@example.com", hashed_password="", full_name="Audit Race", is_active=True)
        seed.add(user); seed.flush()
        seed.add(TradingAccount(user_id=user.id, mode="PAPER", virtual_balance=1000.0, realized_pnl=0.0, is_active=True))
        seed.commit(); user_id = user.id
    finally: seed.close()
    barrier = threading.Barrier(2)
    def submit():
        db = Session()
        try:
            barrier.wait(timeout=5)
            try:
                return paper_order(PaperOrderRequest(symbol="AUDIT-RACE", transaction_type="BUY", price=100.0, quantity=5, fill_id="AUDIT-RACE-1"), user_id=user_id, db=db)
            except HTTPException as exc:
                db.rollback(); return {"status_code": exc.status_code}
        finally: db.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(submit) for _ in range(2)]
        results = [f.result() for f in futures]
    db = Session()
    try:
        orders = db.query(Order).filter(Order.user_id == user_id).order_by(Order.id.asc()).all()
        account = db.query(TradingAccount).filter(TradingAccount.user_id == user_id).one()
        assert len(orders) == 1
        assert sum(1 for result in results if result.get("idempotent") is True) == 1
        assert account.virtual_balance == 500.0
        assert len(orders[0].audit_hash) == 64
        assert orders[0].previous_audit_hash is None
    finally: db.close(); engine.dispose()


def test_paper_restart_style_retry_keeps_audit_hash_stable(tmp_path):
    from app.execution.paper_routes import PaperOrderRequest, paper_order
    engine = create_engine(f"sqlite:///{tmp_path / 'audit-restart.db'}", connect_args={"check_same_thread": False, "timeout": 10})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    seed = Session()
    try:
        user = User(email="audit-restart@example.com", hashed_password="", full_name="Audit Restart", is_active=True)
        seed.add(user); seed.flush()
        seed.add(TradingAccount(user_id=user.id, mode="PAPER", virtual_balance=1000.0, realized_pnl=0.0, is_active=True))
        seed.commit(); user_id = user.id
    finally: seed.close()
    first_db = Session()
    try:
        first = paper_order(PaperOrderRequest(symbol="RESTART", transaction_type="BUY", price=100.0, quantity=5, fill_id="RESTART-FILL-1"), user_id=user_id, db=first_db)
    finally: first_db.close()
    second_db = Session()
    try:
        retry = paper_order(PaperOrderRequest(symbol="RESTART", transaction_type="BUY", price=100.0, quantity=5, fill_id="RESTART-FILL-1"), user_id=user_id, db=second_db)
        assert retry["idempotent"] is True
    finally: second_db.close()
    verify = Session()
    try:
        orders = verify.query(Order).filter(Order.user_id == user_id).order_by(Order.id.asc()).all()
        assert len(orders) == 1
        assert orders[0].order_id == first["id"]
        assert len(orders[0].audit_hash) == 64
        assert orders[0].previous_audit_hash is None
    finally: verify.close(); engine.dispose()


def test_paper_end_to_end_multi_symbol_reversal_duplicate_and_audit_reconciliation(tmp_path):
    from app.execution.paper_routes import (
        PaperOrderRequest, PaperExitRequest, paper_order, paper_exit,
        _reconcile_paper_ledger,
    )
    engine = create_engine(
        f"sqlite:///{tmp_path / 'paper-e2e-audit.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    seed = Session()
    try:
        user = User(email="paper-e2e-audit@example.com", hashed_password="", full_name="Paper E2E Audit", is_active=True)
        seed.add(user)
        seed.flush()
        seed.add(TradingAccount(
            user_id=user.id, mode="PAPER", virtual_balance=10_000.0,
            initial_virtual_balance=10_000.0, initial_balance_source="BOOTSTRAP",
            realized_pnl=0.0, is_active=True,
        ))
        seed.commit()
        user_id = user.id
    finally:
        seed.close()

    def order(symbol, side, price, quantity, fill_id):
        db = Session()
        try:
            return paper_order(
                PaperOrderRequest(symbol=symbol, transaction_type=side, price=price, quantity=quantity, fill_id=fill_id),
                user_id=user_id, db=db,
            )
        finally:
            db.close()

    def exit_trade(symbol, price, fill_id):
        db = Session()
        try:
            return paper_exit(
                PaperExitRequest(symbol=symbol, price=price, fill_id=fill_id),
                user_id=user_id, db=db,
            )
        finally:
            db.close()

    try:
        order("ALPHA", "BUY", 100.0, 10, "E2E-A1")
        order("BETA", "SELL", 200.0, 6, "E2E-B1")
        order("ALPHA", "SELL", 120.0, 4, "E2E-A2")
        order("BETA", "BUY", 180.0, 2, "E2E-B2")
        order("ALPHA", "SELL", 110.0, 8, "E2E-A3")
        exit_trade("BETA", 150.0, "E2E-B3")
        exit_trade("ALPHA", 100.0, "E2E-A4")
        retry = order("ALPHA", "SELL", 100.0, 2, "E2E-A4")
        assert retry["idempotent"] is True

        verify = Session()
        try:
            account = verify.query(TradingAccount).filter(TradingAccount.user_id == user_id).one()
            positions = verify.query(Position).filter(
                Position.user_id == user_id, Position.is_paper.is_(True), Position.quantity != 0
            ).all()
            orders = verify.query(Order).filter(
                Order.user_id == user_id, Order.is_paper.is_(True)
            ).order_by(Order.id.asc()).all()
            assert account.virtual_balance == 10_400.0
            assert account.realized_pnl == 400.0
            assert positions == []
            assert len(orders) == 7
            assert all(len(item.audit_hash) == 64 for item in orders)
            for previous, current in zip(orders, orders[1:]):
                assert current.previous_audit_hash == previous.audit_hash
        finally:
            verify.close()

        reconcile = Session()
        try:
            data = _reconcile_paper_ledger(reconcile, user_id)
            assert data["status"] == "OK"
            assert data["repairability"] == "NONE"
            assert data["reconstructed_realized_pnl"] == 400.0
            assert data["stored_realized_pnl"] == 400.0
            assert data["reconstructed_virtual_balance"] == 10_400.0
            assert data["stored_virtual_balance"] == 10_400.0
            assert data["mismatches"] == []
        finally:
            reconcile.close()
    finally:
        engine.dispose()


def test_paper_reconcile_migrated_inferred_baseline_never_becomes_bootstrap(tmp_path):
    from app.execution.paper_routes import PaperOrderRequest, paper_order, _reconcile_paper_ledger

    engine = create_engine(
        f"sqlite:///{tmp_path / 'migrated-baseline.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    seed = Session()
    try:
        user = User(email="migrated-baseline@example.com", hashed_password="", full_name="Migrated Baseline", is_active=True)
        seed.add(user)
        seed.flush()
        seed.add(TradingAccount(
            user_id=user.id, mode="PAPER", virtual_balance=900.0,
            initial_virtual_balance=1000.0, initial_balance_source="MIGRATED_INFERRED",
            realized_pnl=0.0, is_active=True,
        ))
        seed.commit()
        user_id = user.id
    finally:
        seed.close()

    db = Session()
    try:
        paper_order(
            PaperOrderRequest(symbol="MIGRATED", transaction_type="BUY", price=100.0, quantity=1, fill_id="MIGRATED-1"),
            user_id=user_id, db=db,
        )
    finally:
        db.close()

    verify = Session()
    try:
        data = _reconcile_paper_ledger(verify, user_id)
        assert data["status"] == "OK"
        assert data["baseline_status"] == "MIGRATED_INFERRED"
        assert data["repairability"] == "BLOCKED"
        assert data["mismatches"] == []
        assert data["repair_plan"]["apply"] is False
    finally:
        verify.close()
        engine.dispose()


def test_paper_reconcile_mixed_legacy_and_fingerprinted_orders_is_blocked(tmp_path):
    from app.execution.paper_routes import PaperOrderRequest, paper_order, _reconcile_paper_ledger

    engine = create_engine(
        f"sqlite:///{tmp_path / 'mixed-legacy.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    seed = Session()
    try:
        user = User(email="mixed-legacy@example.com", hashed_password="", full_name="Mixed Legacy", is_active=True)
        seed.add(user)
        seed.flush()
        seed.add(TradingAccount(
            user_id=user.id, mode="PAPER", virtual_balance=1000.0,
            initial_virtual_balance=1000.0, initial_balance_source="BOOTSTRAP",
            realized_pnl=0.0, is_active=True,
        ))
        seed.commit()
        user_id = user.id
        from app.models import Order
        seed.add(Order(
            user_id=user_id, symbol="LEGACY", transaction_type="BUY", quantity=1,
            price=100.0, average_price=100.0, filled_quantity=1,
            average_fill_price=100.0, status="FILLED", is_paper=True, pnl=0.0,
            fill_id=None, audit_hash=None, previous_audit_hash=None,
        ))
        seed.commit()    finally:
        seed.close()

    db = Session()
    try:
        result = paper_order(            PaperOrderRequest(symbol="NEW", transaction_type="BUY", price=100.0, quantity=1, fill_id="NEW-1"),
            user_id=user_id, db=db,
        )
        assert result["order"]["fill_id"] == "NEW-1"
    finally:
        db.close()
    verify = Session()
    try:
        data = _reconcile_paper_ledger(verify, user_id)
        assert data["status"] == "MISMATCH"
        assert data["baseline_status"] == "LEGACY_UNFINGERPRINTED"        assert data["repairability"] == "BLOCKED"
        assert any(item.startswith("audit_chain_mismatch:") for item in data["mismatches"])
    finally:
        verify.close()
        engine.dispose()


def test_paper_long_short_long_reversal_chain_survives_restart_and_reconciles(tmp_path):
    from app.execution.paper_routes import PaperOrderRequest, paper_order, _reconcile_paper_ledger

    engine = create_engine(
        f"sqlite:///{tmp_path / 'long-short-long.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    seed = Session()
    try:
        user = User(email="reversal-chain@example.com", hashed_password="", full_name="Reversal Chain", is_active=True)
        seed.add(user)
        seed.flush()
        seed.add(TradingAccount(
            user_id=user.id, mode="PAPER", virtual_balance=10_000.0,
            initial_virtual_balance=10_000.0, initial_balance_source="BOOTSTRAP",
            realized_pnl=0.0, is_active=True,
        ))
        seed.commit()
        user_id = user.id
    finally:
        seed.close()

    fills = [
        ("BUY", 100.0, 10, "R-L1"),
        ("SELL", 120.0, 15, "R-S1"),
        ("BUY", 110.0, 2, "R-B1"),
        ("BUY", 90.0, 5, "R-B2"),
        ("SELL", 100.0, 2, "R-S2"),
    ]
    for side, price, quantity, fill_id in fills:
        db = Session()
        try:
            result = paper_order(
                PaperOrderRequest(symbol="REVERSAL", transaction_type=side, price=price, quantity=quantity, fill_id=fill_id),
                user_id=user_id, db=db,
            )
            assert result["status"] == "success"
        finally:
            db.close()

    verify = Session()
    try:
        account = verify.query(TradingAccount).filter(TradingAccount.user_id == user_id).one()
        orders = verify.query(Order).filter(Order.user_id == user_id).order_by(Order.id.asc()).all()
        assert account.virtual_balance == 10_330.0
        assert account.realized_pnl == 330.0
        assert not [p for p in verify.query(Position).filter(Position.user_id == user_id).all() if p.quantity != 0]
        assert len(orders) == 5
        assert all(len(item.audit_hash) == 64 for item in orders)
        assert all(
            current.previous_audit_hash == previous.audit_hash
            for previous, current in zip(orders, orders[1:])
        )
    finally:
        verify.close()

    reconcile = Session()
    try:
        data = _reconcile_paper_ledger(reconcile, user_id)
        assert data["status"] == "OK"
        assert data["repairability"] == "NONE"
        assert data["reconstructed_realized_pnl"] == 330.0
        assert data["reconstructed_virtual_balance"] == 10_330.0
        assert data["mismatches"] == []
    finally:
        reconcile.close()
        engine.dispose()


def test_paper_entry_corrupt_state_rolls_back_all_mutations(tmp_path):
    from app.execution.paper_routes import PaperEntryRequest, paper_entry

    engine = create_engine(f"sqlite:///{tmp_path / 'entry-corrupt.db'}", connect_args={"check_same_thread": False, "timeout": 10})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    seed = Session()
    try:
        user = User(email="entry-corrupt@example.com", hashed_password="", full_name="Entry Corrupt", is_active=True)
        seed.add(user)
        seed.flush()
        seed.add(TradingAccount(user_id=user.id, mode="PAPER", virtual_balance=1000.0, initial_virtual_balance=1000.0, initial_balance_source="BOOTSTRAP", realized_pnl=0.0, is_active=True))
        seed.add(Order(user_id=user.id, symbol="BAD", transaction_type="BUY", quantity=1, filled_quantity=1, price=0.0, average_price=0.0, average_fill_price=0.0, status="FILLED", is_paper=True, pnl=0.0))
        seed.commit()
        user_id = user.id
    finally:
        seed.close()

    db = Session()
    try:
        try:
            paper_entry(PaperEntryRequest(symbol="NEW", price=100.0, quantity=2, fill_id="ENTRY-ROLLBACK"), user_id=user_id, db=db)
            assert False, "corrupt paper state must fail closed"
        except RuntimeError as exc:
            assert "paper order invariant" in str(exc)
            db.rollback()
    finally:
        db.close()

    verify = Session()
    try:
        account = verify.query(TradingAccount).filter(TradingAccount.user_id == user_id).one()
        assert account.virtual_balance == 1000.0
        assert account.realized_pnl == 0.0
        assert verify.query(Position).filter(Position.user_id == user_id).count() == 0
        assert verify.query(Order).filter(Order.user_id == user_id).count() == 1
        assert verify.query(Order).filter(Order.user_id == user_id, Order.fill_id == "ENTRY-ROLLBACK").count() == 0
    finally:
        verify.close()
        engine.dispose()


def test_paper_order_corrupt_state_rolls_back_reversal_mutation(tmp_path):
    from app.execution.paper_routes import PaperOrderRequest, paper_order

    engine = create_engine(f"sqlite:///{tmp_path / 'order-corrupt.db'}", connect_args={"check_same_thread": False, "timeout": 10})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    seed = Session()
    try:
        user = User(email="order-corrupt@example.com", hashed_password="", full_name="Order Corrupt", is_active=True)
        seed.add(user)
        seed.flush()
        seed.add(TradingAccount(user_id=user.id, mode="PAPER", virtual_balance=1000.0, initial_virtual_balance=1000.0, initial_balance_source="BOOTSTRAP", realized_pnl=0.0, is_active=True))
        seed.add(Position(user_id=user.id, symbol="REV", quantity=-5, average_price=100.0, is_paper=True, is_open=True))
        seed.add(Order(user_id=user.id, symbol="BAD", transaction_type="BUY", quantity=1, filled_quantity=1, price=0.0, average_price=0.0, average_fill_price=0.0, status="FILLED", is_paper=True, pnl=0.0))
        seed.commit()
        user_id = user.id
    finally:
        seed.close()

    db = Session()
    try:
        try:
            paper_order(PaperOrderRequest(symbol="REV", transaction_type="BUY", price=90.0, quantity=8, fill_id="REV-ROLLBACK"), user_id=user_id, db=db)
            assert False, "corrupt paper state must fail closed"
        except RuntimeError as exc:
            assert "paper order invariant" in str(exc)
            db.rollback()
    finally:
        db.close()

    verify = Session()
    try:
        account = verify.query(TradingAccount).filter(TradingAccount.user_id == user_id).one()
        position = verify.query(Position).filter(Position.user_id == user_id, Position.symbol == "REV").one()
        assert account.virtual_balance == 1000.0
        assert account.realized_pnl == 0.0
        assert position.quantity == -5
        assert position.average_price == 100.0
        assert verify.query(Order).filter(Order.user_id == user_id, Order.fill_id == "REV-ROLLBACK").count() == 0
    finally:
        verify.close()
        engine.dispose()


def test_paper_exit_corrupt_state_rolls_back_close_mutation(tmp_path):
    from app.execution.paper_routes import PaperOrderRequest, PaperExitRequest, paper_order, paper_exit

    engine = create_engine(f"sqlite:///{tmp_path / 'exit-corrupt.db'}", connect_args={"check_same_thread": False, "timeout": 10})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    seed = Session()
    try:
        user = User(email="exit-corrupt@example.com", hashed_password="", full_name="Exit Corrupt", is_active=True)
        seed.add(user)
        seed.flush()
        seed.add(TradingAccount(user_id=user.id, mode="PAPER", virtual_balance=900.0, initial_virtual_balance=1000.0, initial_balance_source="BOOTSTRAP", realized_pnl=0.0, is_active=True))
        seed.add(Position(user_id=user.id, symbol="EXIT", quantity=1, average_price=100.0, is_paper=True, is_open=True))
        seed.add(Order(user_id=user.id, symbol="BAD", transaction_type="BUY", quantity=1, filled_quantity=1, price=0.0, average_price=0.0, average_fill_price=0.0, status="FILLED", is_paper=True, pnl=0.0))
        seed.commit()
        user_id = user.id
    finally:
        seed.close()

    db = Session()
    try:
        try:
            paper_exit(PaperExitRequest(symbol="EXIT", price=120.0, fill_id="EXIT-ROLLBACK"), user_id=user_id, db=db)
            assert False, "corrupt paper state must fail closed"
        except RuntimeError as exc:
            assert "paper order invariant" in str(exc)
            db.rollback()
    finally:
        db.close()

    verify = Session()
    try:
        account = verify.query(TradingAccount).filter(TradingAccount.user_id == user_id).one()
        position = verify.query(Position).filter(Position.user_id == user_id, Position.symbol == "EXIT").one()
        assert account.virtual_balance == 900.0
        assert account.realized_pnl == 0.0
        assert position.quantity == 1
        assert position.average_price == 100.0
        assert verify.query(Order).filter(Order.user_id == user_id, Order.fill_id == "EXIT-ROLLBACK").count() == 0
    finally:
        verify.close()
        engine.dispose()

def test_paper_audit_chain_tamper_matrix_is_blocked(tmp_path):
    from app.execution.paper_routes import PaperOrderRequest, _reconcile_paper_ledger, paper_order

    variants = (
        ("price", "audit_hash_mismatch:"),
        ("quantity", "invalid_order:"),
        ("pnl", "audit_hash_mismatch:"),
        ("fill_id", "duplicate_fill_id:"),
        ("previous_hash", "audit_chain_mismatch:"),
        ("audit_hash", "audit_hash_mismatch:"),
        ("sequence", "audit_chain_mismatch:"),
    )

    for name, expected_prefix in variants:
        engine = create_engine(
            f"sqlite:///{tmp_path / f'audit-tamper-{name}.db'}",
            connect_args={"check_same_thread": False, "timeout": 10},
        )
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        seed = Session()
        try:
            user = User(
                email=f"audit-tamper-{name}@example.com",
                hashed_password="",
                full_name=f"Audit Tamper {name}",
                is_active=True,
            )
            seed.add(user)
            seed.flush()
            seed.add(
                TradingAccount(
                    user_id=user.id,
                    mode="PAPER",
                    virtual_balance=1000.0,
                    initial_virtual_balance=1000.0,
                    initial_balance_source="BOOTSTRAP",
                    realized_pnl=0.0,
                    is_active=True,
                )
            )
            seed.commit()
            user_id = user.id
        finally:
            seed.close()

        first_db = Session()
        try:
            paper_order(
                PaperOrderRequest(
                    symbol="TAMPER",
                    transaction_type="BUY",
                    price=100.0,
                    quantity=5,
                    fill_id="TAMPER-1",
                ),
                user_id=user_id,
                db=first_db,
            )
        finally:
            first_db.close()

        second_db = Session()
        try:
            paper_order(
                PaperOrderRequest(
                    symbol="TAMPER",
                    transaction_type="SELL",
                    price=120.0,
                    quantity=2,
                    fill_id="TAMPER-2",
                ),
                user_id=user_id,
                db=second_db,
            )        finally:
            second_db.close()

        tamper_db = Session()
        try:
            orders = (
                tamper_db.query(Order)                .filter(Order.user_id == user_id, Order.is_paper.is_(True))
                .order_by(Order.id.asc())
                .all()
            )
            assert len(orders) == 2
            first, second = orders
            if name == "price":
                first.price = 101.0
                first.average_fill_price = 101.0
            elif name == "quantity":
                second.quantity = 1
            elif name == "pnl":
                second.pnl = 41.0            elif name == "fill_id":
                second.fill_id = first.fill_id
            elif name == "previous_hash":
                second.previous_audit_hash = "f" * 64
            elif name == "audit_hash":
                second.audit_hash = "0" * 64
            elif name == "sequence":
                first.id = 100000
                second.id = 99999

            tamper_db.commit()
        finally:
            tamper_db.close()

        reconcile_db = Session()
        try:
            data = _reconcile_paper_ledger(reconcile_db, user_id)
            assert data["status"] == "MISMATCH"
            assert data["repairability"] == "BLOCKED"
            assert data["repair_plan"]["apply"] is False
            assert any(item.startswith(expected_prefix) for item in data["mismatches"])
        finally:
            reconcile_db.close()
            engine.dispose()

def test_paper_reconcile_cross_component_consistency_matrix(tmp_path):
    from app.execution.paper_routes import PaperOrderRequest, _reconcile_paper_ledger, paper_order

    tamper_cases = (
        ("cash", lambda account, position: setattr(account, "virtual_balance", 9999.0), "virtual_balance_mismatch"),
        ("realized", lambda account, position: setattr(account, "realized_pnl", 9999.0), "realized_pnl_mismatch"),
        ("position_qty", lambda account, position: setattr(position, "quantity", 99), "position_mismatch:PORT"),
        ("position_avg", lambda account, position: setattr(position, "average_price", 999.0), "position_mismatch:PORT"),
        ("position_open", lambda account, position: setattr(position, "is_open", False), "orphan_position:PORT"),
    )

    for name, tamper, expected in tamper_cases:
        engine = create_engine(
            f"sqlite:///{tmp_path / f'consistency-{name}.db'}",
            connect_args={"check_same_thread": False, "timeout": 10},
        )
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        seed = Session()
        try:
            user = User(
                email=f"consistency-{name}@example.com",
                hashed_password="",
                full_name=f"Consistency {name}",
                is_active=True,
            )
            seed.add(user)
            seed.flush()
            seed.add(
                TradingAccount(
                    user_id=user.id,
                    mode="PAPER",
                    virtual_balance=1000.0,
                    initial_virtual_balance=1000.0,
                    initial_balance_source="BOOTSTRAP",
                    realized_pnl=0.0,
                    is_active=True,
                )
            )
            seed.commit()
            user_id = user.id
        finally:
            seed.close()

        db = Session()
        try:
            paper_order(
                PaperOrderRequest(
                    symbol="PORT",
                    transaction_type="BUY",
                    price=100.0,
                    quantity=5,
                    fill_id="PORT-1",
                ),
                user_id=user_id,
                db=db,
            )
        finally:
            db.close()

        corrupt = Session()
        try:
            account = corrupt.query(TradingAccount).filter(TradingAccount.user_id == user_id).one()
            position = corrupt.query(Position).filter(
                Position.user_id == user_id,
                Position.symbol == "PORT",
                Position.is_paper.is_(True),
            ).one()
            tamper(account, position)
            corrupt.commit()
        finally:
            corrupt.close()

        verify = Session()
        try:
            data = _reconcile_paper_ledger(verify, user_id)
            assert data["status"] == "MISMATCH"
            assert expected in data["mismatches"]
            assert data["repairability"] == "SAFE_DRY_RUN"
            assert data["repair_plan"]["apply"] is False
            assert data["orders"] == 1
            assert data["reconstructed_realized_pnl"] == 0.0
            assert data["reconstructed_virtual_balance"] == 500.0
            assert data["repair_plan"]["positions"]["PORT"]["quantity"] == 5
            assert data["repair_plan"]["positions"]["PORT"]["average_price"] == 100.0
        finally:
            verify.close()
            engine.dispose()

def test_paper_reconcile_safe_dry_run_plan_is_deterministic_and_read_only(tmp_path):
    from app.execution.paper_routes import PaperOrderRequest, _reconcile_paper_ledger, paper_order

    engine = create_engine(
        f"sqlite:///{tmp_path / 'repair-plan-safe.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    seed = Session()
    try:
        user = User(
            email="repair-safe@example.com",
            hashed_password="",
            full_name="Repair Safe",
            is_active=True,
        )
        seed.add(user)
        seed.flush()
        seed.add(
            TradingAccount(
                user_id=user.id,
                mode="PAPER",
                virtual_balance=1000.0,
                initial_virtual_balance=1000.0,
                initial_balance_source="BOOTSTRAP",
                realized_pnl=0.0,
                is_active=True,
            )
        )
        seed.commit()
        user_id = user.id
    finally:
        seed.close()

    db = Session()
    try:
        paper_order(
            PaperOrderRequest(
                symbol="SAFE",
                transaction_type="BUY",
                price=100.0,
                quantity=5,
                fill_id="SAFE-1",
            ),
            user_id=user_id,
            db=db,
        )
    finally:
        db.close()

    corrupt = Session()
    try:
        account = corrupt.query(TradingAccount).filter(TradingAccount.user_id == user_id).one()
        account.virtual_balance = 777.0
        corrupt.commit()
    finally:
        corrupt.close()

    before = Session()
    try:
        before_account = before.query(TradingAccount).filter(TradingAccount.user_id == user_id).one()
        before_orders = before.query(Order).filter(Order.user_id == user_id, Order.is_paper.is_(True)).count()
        before_positions = before.query(Position).filter(Position.user_id == user_id, Position.is_paper.is_(True)).count()
        before_values = (before_account.virtual_balance, before_account.realized_pnl, before_orders, before_positions)
    finally:
        before.close()

    first = Session()
    second = Session()
    try:
        plan1 = _reconcile_paper_ledger(first, user_id)
        plan2 = _reconcile_paper_ledger(second, user_id)
        assert plan1 == plan2
        assert plan1["status"] == "MISMATCH"
        assert plan1["repairability"] == "SAFE_DRY_RUN"
        assert plan1["repair_plan"]["apply"] is False
        assert plan1["repair_plan"]["reason"] == "read_only_dry_run"
        precondition = plan1["repair_plan"]["precondition"]
        assert precondition["algorithm"] == "SHA256"
        assert precondition["order_count"] == 1
        assert precondition["position_count"] == 1
        assert len(precondition["state_hash"]) == 64
        assert precondition["audit_head"]
        assert plan1["repair_plan"]["proposed_virtual_balance"] == 500.0
        assert plan1["repair_plan"]["proposed_realized_pnl"] == 0.0
        assert plan1["repair_plan"]["positions"]["SAFE"] == {
            "quantity": 5,
            "average_price": 100.0,
        }
    finally:
        first.close()
        second.close()

    after = Session()
    try:
        after_account = after.query(TradingAccount).filter(TradingAccount.user_id == user_id).one()
        after_orders = after.query(Order).filter(Order.user_id == user_id, Order.is_paper.is_(True)).count()
        after_positions = after.query(Position).filter(Position.user_id == user_id, Position.is_paper.is_(True)).count()
        assert (after_account.virtual_balance, after_account.realized_pnl, after_orders, after_positions) == before_values
    finally:
        after.close()

    changed = Session()
    try:
        changed_account = changed.query(TradingAccount).filter(TradingAccount.user_id == user_id).one()
        changed_account.virtual_balance = 776.0
        changed.commit()
        changed_result = _reconcile_paper_ledger(changed, user_id)
        assert changed_result["repair_plan"]["precondition"]["state_hash"] != precondition["state_hash"]
    finally:
        changed.close()
        engine.dispose()


def test_paper_reconcile_blocked_baseline_never_exposes_applicable_repair(tmp_path):
    from app.execution.paper_routes import PaperOrderRequest, _reconcile_paper_ledger, paper_order

    engine = create_engine(
        f"sqlite:///{tmp_path / 'repair-plan-blocked.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    seed = Session()
    try:
        user = User(
            email="repair-blocked@example.com",
            hashed_password="",
            full_name="Repair Blocked",
            is_active=True,
        )
        seed.add(user)
        seed.flush()
        seed.add(
            TradingAccount(
                user_id=user.id,
                mode="PAPER",
                virtual_balance=1000.0,
                initial_virtual_balance=1000.0,
                initial_balance_source="MIGRATED_INFERRED",
                realized_pnl=0.0,
                is_active=True,
            )
        )
        seed.commit()
        user_id = user.id
    finally:
        seed.close()

    db = Session()
    try:
        paper_order(
            PaperOrderRequest(
                symbol="BLOCK",
                transaction_type="BUY",
                price=100.0,
                quantity=2,
                fill_id="BLOCK-1",
            ),
            user_id=user_id,
            db=db,
        )    finally:
        db.close()

    corrupt = Session()
    try:
        account = corrupt.query(TradingAccount).filter(TradingAccount.user_id == user_id).one()
        account.virtual_balance = 999.0
        corrupt.commit()    finally:
        corrupt.close()

    verify = Session()
    try:
        result = _reconcile_paper_ledger(verify, user_id)
        assert result["status"] == "MISMATCH"
        assert result["baseline_status"] == "MIGRATED_INFERRED"        assert result["repairability"] == "BLOCKED"
        assert result["repair_plan"]["apply"] is False
        assert result["repair_plan"]["reason"] == "read_only_dry_run"
        assert result["repair_plan"]["proposed_virtual_balance"] == 800.0
        assert result["repair_plan"]["positions"]["BLOCK"] == {
            "quantity": 2,
            "average_price": 100.0,
        }    finally:
        verify.close()
        engine.dispose()

def test_paper_reconcile_precondition_invalidates_every_core_mutation(tmp_path):
    from app.execution.paper_routes import PaperOrderRequest, _paper_repair_precondition, _reconcile_paper_ledger, paper_order

    mutations = ("account", "order_price", "order_pnl", "order_hash", "position_qty", "position_open")
    for mutation in mutations:
        engine = create_engine(
            f"sqlite:///{tmp_path / f'precondition-{mutation}.db'}",
            connect_args={"check_same_thread": False, "timeout": 10},
        )
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        seed = Session()
        try:
            user = User(email=f"pre-{mutation}@example.com", hashed_password="", full_name="Precondition", is_active=True)
            seed.add(user)
            seed.flush()
            seed.add(TradingAccount(
                user_id=user.id, mode="PAPER", virtual_balance=1000.0,
                initial_virtual_balance=1000.0, initial_balance_source="BOOTSTRAP",
                realized_pnl=0.0, is_active=True,
            ))
            seed.commit()
            user_id = user.id
        finally:
            seed.close()

        db = Session()
        try:
            paper_order(PaperOrderRequest(symbol="MATRIX", transaction_type="BUY", price=100.0, quantity=5, fill_id="MATRIX-1"), user_id=user_id, db=db)
        finally:
            db.close()

        base = Session()
        try:
            account = base.query(TradingAccount).filter(TradingAccount.user_id == user_id).one()
            orders = base.query(Order).filter(Order.user_id == user_id, Order.is_paper.is_(True)).order_by(Order.id.asc()).all()
            positions = base.query(Position).filter(Position.user_id == user_id, Position.is_paper.is_(True)).order_by(Position.id.asc()).all()
            before_hash = _paper_repair_precondition(base, user_id, account, orders, positions)["state_hash"]
        finally:
            base.close()

        mutate = Session()
        try:
            account = mutate.query(TradingAccount).filter(TradingAccount.user_id == user_id).one()
            order = mutate.query(Order).filter(Order.user_id == user_id, Order.is_paper.is_(True)).one()
            position = mutate.query(Position).filter(Position.user_id == user_id, Position.is_paper.is_(True)).one()
            if mutation == "account":
                account.virtual_balance = 499.0
            elif mutation == "order_price":
                order.price = 101.0
            elif mutation == "order_pnl":
                order.pnl = 7.0
            elif mutation == "order_hash":
                order.audit_hash = "a" * 64
            elif mutation == "position_qty":
                position.quantity = 4
            elif mutation == "position_open":
                position.is_open = False
            mutate.commit()
            current = _paper_repair_precondition(
                mutate, user_id, account,
                mutate.query(Order).filter(Order.user_id == user_id, Order.is_paper.is_(True)).order_by(Order.id.asc()).all(),
                mutate.query(Position).filter(Position.user_id == user_id, Position.is_paper.is_(True)).order_by(Position.id.asc()).all(),
            )
            assert current["state_hash"] != before_hash, mutation
        finally:
            mutate.close()
            engine.dispose()

def test_paper_reconcile_precondition_invalidates_order_set_and_sequence_mutations(tmp_path):
    from app.execution.paper_routes import PaperOrderRequest, _paper_repair_precondition, paper_order

    mutations = ("delete_order", "insert_order", "order_id_resequence", "fill_id_mutation")
    for mutation in mutations:
        engine = create_engine(
            f"sqlite:///{tmp_path / f'precondition-order-{mutation}.db'}",
            connect_args={"check_same_thread": False, "timeout": 10},
        )
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        seed = Session()
        try:
            user = User(email=f"order-{mutation}@example.com", hashed_password="", full_name="Order Mutation", is_active=True)
            seed.add(user)
            seed.flush()
            seed.add(TradingAccount(
                user_id=user.id, mode="PAPER", virtual_balance=1000.0,
                initial_virtual_balance=1000.0, initial_balance_source="BOOTSTRAP",
                realized_pnl=0.0, is_active=True,
            ))
            seed.commit()
            user_id = user.id
        finally:
            seed.close()

        db = Session()
        try:
            paper_order(PaperOrderRequest(symbol="MATRIX", transaction_type="BUY", price=100.0, quantity=5, fill_id="MATRIX-1"), user_id=user_id, db=db)
            paper_order(PaperOrderRequest(symbol="SECOND", transaction_type="BUY", price=50.0, quantity=2, fill_id="SECOND-1"), user_id=user_id, db=db)
        finally:
            db.close()

        base = Session()
        try:
            account = base.query(TradingAccount).filter(TradingAccount.user_id == user_id).one()
            orders = base.query(Order).filter(Order.user_id == user_id, Order.is_paper.is_(True)).order_by(Order.id.asc()).all()
            positions = base.query(Position).filter(Position.user_id == user_id, Position.is_paper.is_(True)).order_by(Position.id.asc()).all()
            before_hash = _paper_repair_precondition(base, user_id, account, orders, positions)["state_hash"]
        finally:
            base.close()

        mutate = Session()
        try:
            account = mutate.query(TradingAccount).filter(TradingAccount.user_id == user_id).one()
            orders = mutate.query(Order).filter(Order.user_id == user_id, Order.is_paper.is_(True)).order_by(Order.id.asc()).all()
            positions = mutate.query(Position).filter(Position.user_id == user_id, Position.is_paper.is_(True)).order_by(Position.id.asc()).all()
            if mutation == "delete_order":
                mutate.delete(orders[1])
            elif mutation == "insert_order":
                extra = Order(
                    user_id=user_id, order_id="PAPER-MUTATION-INSERT", symbol="INJECTED",
                    transaction_type="BUY", order_type="MARKET", product_type="INTRADAY",
                    quantity=1, price=10.0, average_price=10.0, filled_quantity=1,
                    average_fill_price=10.0, time_in_force="DAY", pnl=0.0, status="FILLED",
                    is_paper=True, fill_id="INJECTED-1", audit_hash="b" * 64,
                    previous_audit_hash=orders[-1].audit_hash,
                )
                mutate.add(extra)
            elif mutation == "order_id_resequence":
                orders[0].id, orders[1].id = orders[1].id, orders[0].id
            elif mutation == "fill_id_mutation":
                orders[0].fill_id = "MATRIX-MUTATED"
            mutate.commit()
            current_orders = mutate.query(Order).filter(Order.user_id == user_id, Order.is_paper.is_(True)).order_by(Order.id.asc()).all()
            current_positions = mutate.query(Position).filter(Position.user_id == user_id, Position.is_paper.is_(True)).order_by(Position.id.asc()).all()
            current = _paper_repair_precondition(mutate, user_id, account, current_orders, current_positions)
            assert current["state_hash"] != before_hash, mutation
        finally:
            mutate.close()
            engine.dispose()


def test_paper_reconcile_precondition_captures_multi_symbol_reversal_ledger(tmp_path):
    from app.execution.paper_routes import PaperOrderRequest, PaperExitRequest, _paper_repair_precondition, _reconcile_paper_ledger, paper_order, paper_exit

    engine = create_engine(
        f"sqlite:///{tmp_path / 'precondition-reversal.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    seed = Session()
    try:
        user = User(email="reversal-pre@example.com", hashed_password="", full_name="Reversal Precondition", is_active=True)
        seed.add(user)
        seed.flush()
        seed.add(TradingAccount(
            user_id=user.id, mode="PAPER", virtual_balance=10000.0,
            initial_virtual_balance=10000.0, initial_balance_source="BOOTSTRAP",
            realized_pnl=0.0, is_active=True,
        ))
        seed.commit()
        user_id = user.id
    finally:
        seed.close()

    for request in (
        PaperOrderRequest(symbol="ALPHA", transaction_type="BUY", price=100.0, quantity=10, fill_id="A1"),
        PaperOrderRequest(symbol="ALPHA", transaction_type="SELL", price=120.0, quantity=15, fill_id="A2"),
        PaperOrderRequest(symbol="ALPHA", transaction_type="BUY", price=110.0, quantity=2, fill_id="A3"),
        PaperOrderRequest(symbol="BETA", transaction_type="SELL", price=200.0, quantity=6, fill_id="B1"),
        PaperOrderRequest(symbol="BETA", transaction_type="BUY", price=180.0, quantity=2, fill_id="B2"),
    ):
        db = Session()
        try:
            paper_order(request, user_id=user_id, db=db)
        finally:
            db.close()

    base = Session()
    try:
        account = base.query(TradingAccount).filter(TradingAccount.user_id == user_id).one()
        orders = base.query(Order).filter(Order.user_id == user_id, Order.is_paper.is_(True)).order_by(Order.id.asc()).all()
        positions = base.query(Position).filter(Position.user_id == user_id, Position.is_paper.is_(True)).order_by(Position.id.asc()).all()
        pre = _paper_repair_precondition(base, user_id, account, orders, positions)
        assert pre["order_count"] == 5
        assert pre["position_count"] == 2
        assert pre["audit_head"] == orders[-1].audit_hash
        assert len(pre["state_hash"]) == 64
    finally:
        base.close()

    mutate = Session()
    try:
        account = mutate.query(TradingAccount).filter(TradingAccount.user_id == user_id).one()
        order = mutate.query(Order).filter(Order.user_id == user_id, Order.is_paper.is_(True)).order_by(Order.id.asc()).first()
        order.pnl = float(order.pnl or 0.0) + 1.0
        mutate.commit()
        now = _reconcile_paper_ledger(mutate, user_id)
        assert now["repair_plan"]["precondition"]["state_hash"] != pre["state_hash"]
        assert now["repairability"] == "BLOCKED"
        assert now["repair_plan"]["apply"] is False
    finally:
        mutate.close()
        engine.dispose()

def test_paper_reconcile_repair_plan_complete_for_partial_reversal_multi_symbol(tmp_path):
    from app.execution.paper_routes import PaperOrderRequest, _reconcile_paper_ledger, paper_order

    engine = create_engine(
        f"sqlite:///{tmp_path / 'repair-plan-complete.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    seed = Session()
    try:
        user = User(email="complete-plan@example.com", hashed_password="", full_name="Complete Plan", is_active=True)
        seed.add(user)
        seed.flush()
        seed.add(TradingAccount(
            user_id=user.id, mode="PAPER", virtual_balance=10000.0,
            initial_virtual_balance=10000.0, initial_balance_source="BOOTSTRAP",
            realized_pnl=0.0, is_active=True,
        ))
        seed.commit()
        user_id = user.id
    finally:
        seed.close()

    requests = (
        PaperOrderRequest(symbol="ALPHA", transaction_type="BUY", price=100.0, quantity=10, fill_id="A1"),
        PaperOrderRequest(symbol="ALPHA", transaction_type="SELL", price=120.0, quantity=15, fill_id="A2"),
        PaperOrderRequest(symbol="ALPHA", transaction_type="BUY", price=110.0, quantity=2, fill_id="A3"),
        PaperOrderRequest(symbol="BETA", transaction_type="BUY", price=50.0, quantity=4, fill_id="B1"),
        PaperOrderRequest(symbol="BETA", transaction_type="SELL", price=60.0, quantity=1, fill_id="B2"),
    )
    for request in requests:
        db = Session()
        try:
            paper_order(request, user_id=user_id, db=db)
        finally:
            db.close()

    corrupt = Session()
    try:
        account = corrupt.query(TradingAccount).filter(TradingAccount.user_id == user_id).one()
        account.virtual_balance = 1.0
        account.realized_pnl = 999.0
        corrupt.commit()
    finally:
        corrupt.close()

    verify = Session()
    try:
        result = _reconcile_paper_ledger(verify, user_id)
        assert result["status"] == "MISMATCH"
        assert result["repairability"] == "SAFE_DRY_RUN"
        assert result["repair_plan"]["apply"] is False
        assert result["reconstructed_realized_pnl"] == 230.0
        assert result["reconstructed_virtual_balance"] == 9390.0
        assert result["repair_plan"]["proposed_realized_pnl"] == 230.0
        assert result["repair_plan"]["proposed_virtual_balance"] == 9390.0
        assert result["repair_plan"]["positions"] == {
            "ALPHA": {"quantity": -3, "average_price": 110.0},
            "BETA": {"quantity": 3, "average_price": 50.0},
        }
        assert "ALPHA" in result["repair_plan"]["positions"]
        assert "BETA" in result["repair_plan"]["positions"]
    finally:
        verify.close()
        engine.dispose()

def test_paper_reconcile_flat_symbol_excluded_from_repair_positions(tmp_path):
    from app.execution.paper_routes import PaperOrderRequest, PaperExitRequest, _reconcile_paper_ledger, paper_order, paper_exit

    engine = create_engine(
        f"sqlite:///{tmp_path / 'repair-plan-flat.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    seed = Session()
    try:
        user = User(email="flat-plan@example.com", hashed_password="", full_name="Flat Plan", is_active=True)
        seed.add(user)
        seed.flush()
        seed.add(TradingAccount(
            user_id=user.id, mode="PAPER", virtual_balance=1000.0,            initial_virtual_balance=1000.0, initial_balance_source="BOOTSTRAP",
            realized_pnl=0.0, is_active=True,
        ))
        seed.commit()
        user_id = user.id
    finally:
        seed.close()

    db = Session()    try:
        paper_order(PaperOrderRequest(symbol="FLAT", transaction_type="BUY", price=100.0, quantity=2, fill_id="F1"), user_id=user_id, db=db)
    finally:
        db.close()
    db = Session()
    try:
        paper_exit(PaperExitRequest(symbol="FLAT", price=110.0, fill_id="F2"), user_id=user_id, db=db)
    finally:
        db.close()

    corrupt = Session()
    try:
        account = corrupt.query(TradingAccount).filter(TradingAccount.user_id == user_id).one()
        account.virtual_balance = 999.0
        corrupt.commit()
    finally:
        corrupt.close()

    verify = Session()
    try:
        result = _reconcile_paper_ledger(verify, user_id)
        assert result["repairability"] == "SAFE_DRY_RUN"
        assert result["reconstructed_realized_pnl"] == 20.0
        assert result["reconstructed_virtual_balance"] == 1020.0
        assert result["repair_plan"]["positions"] == {}
    finally:
        verify.close()
        engine.dispose()

def test_paper_reconcile_audit_chain_and_repair_plan_agree_on_clean_ledger(tmp_path):
    from app.execution.paper_routes import PaperOrderRequest, _reconcile_paper_ledger, paper_order

    engine = create_engine(
        f"sqlite:///{tmp_path / 'audit-plan-agree.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    seed = Session()
    try:
        user = User(email="audit-agree@example.com", hashed_password="", full_name="Audit Agree", is_active=True)
        seed.add(user); seed.flush()
        seed.add(TradingAccount(user_id=user.id, mode="PAPER", virtual_balance=5000.0,
                                initial_virtual_balance=5000.0, initial_balance_source="BOOTSTRAP",
                                realized_pnl=0.0, is_active=True))
        seed.commit(); user_id = user.id
    finally:
        seed.close()

    for req in (
        PaperOrderRequest(symbol="A", transaction_type="BUY", price=100.0, quantity=5, fill_id="AA1"),
        PaperOrderRequest(symbol="B", transaction_type="SELL", price=200.0, quantity=2, fill_id="BB1"),
    ):
        db = Session()
        try: paper_order(req, user_id=user_id, db=db)
        finally: db.close()

    db = Session()
    try:
        result = _reconcile_paper_ledger(db, user_id)
        assert result["status"] == "OK"
        assert result["repairability"] == "NONE"
        assert result["repair_plan"]["apply"] is False
        assert result["repair_plan"]["proposed_virtual_balance"] == result["reconstructed_virtual_balance"]
        assert result["repair_plan"]["proposed_realized_pnl"] == result["reconstructed_realized_pnl"]
        assert result["repair_plan"]["positions"] == result["reconstructed_positions"]
        assert result["repair_plan"]["precondition"]["audit_head"]
    finally:
        db.close(); engine.dispose()




def test_paper_reconcile_response_contract_is_deterministic_for_clean_safe_and_blocked(tmp_path):
    from app.execution.paper_routes import PaperOrderRequest, _reconcile_paper_ledger, paper_order

    def seed_db(path, email):
        engine = create_engine(
            f"sqlite:///{path}",
            connect_args={"check_same_thread": False, "timeout": 10},
        )
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        db = Session()
        user = User(email=email, hashed_password="", full_name="Contract", is_active=True)
        db.add(user); db.flush()
        db.add(TradingAccount(
            user_id=user.id, mode="PAPER", virtual_balance=5000.0,
            initial_virtual_balance=5000.0, initial_balance_source="BOOTSTRAP",
            realized_pnl=0.0, is_active=True,
        ))
        db.commit()
        user_id = user.id
        db.close()
        return engine, Session, user_id

    def assert_contract(result):
        assert set(result) == {
            "status", "repairability", "repairability_reason", "mismatch_categories",
            "baseline_status", "user_id", "orders", "reconstructed_realized_pnl",
            "stored_realized_pnl", "reconstructed_virtual_balance", "stored_virtual_balance",
            "reconstructed_positions", "mismatches", "repair_plan",
        }
        assert result["status"] in {"OK", "MISMATCH"}
        assert result["repairability"] in {"NONE", "SAFE_DRY_RUN", "BLOCKED"}
        assert result["repairability_reason"] in {
            "no_mismatch", "account_or_position_state_only", "ledger_or_baseline_integrity_failure"
        }
        assert isinstance(result["mismatch_categories"], list)
        assert result["mismatch_categories"] == sorted(set(result["mismatch_categories"]))
        assert isinstance(result["mismatches"], list)
        assert isinstance(result["orders"], int) and result["orders"] >= 0
        assert isinstance(result["reconstructed_positions"], dict)
        assert isinstance(result["repair_plan"], dict)
        assert set(result["repair_plan"]) == {
            "apply", "reason", "precondition", "proposed_realized_pnl",
            "proposed_virtual_balance", "positions",
        }
        assert result["repair_plan"]["apply"] is False
        assert result["repair_plan"]["reason"] == "read_only_dry_run"
        assert result["repair_plan"]["positions"] == result["reconstructed_positions"]
        assert isinstance(result["repair_plan"]["precondition"], dict)
        assert set(result["repair_plan"]["precondition"]) == {
            "algorithm", "order_count", "position_count", "audit_head", "state_hash"
        }
        assert result["repair_plan"]["precondition"]["algorithm"] == "SHA256"
        assert isinstance(result["repair_plan"]["precondition"]["state_hash"], str)
        assert len(result["repair_plan"]["precondition"]["state_hash"]) == 64
        assert result["repair_plan"]["proposed_realized_pnl"] == result["reconstructed_realized_pnl"]
        assert result["repair_plan"]["proposed_virtual_balance"] == result["reconstructed_virtual_balance"]

    # CLEAN
    clean_engine, clean_session, clean_user = seed_db(tmp_path / "contract-clean.db", "contract-clean@example.com")
    try:
        db = clean_session()
        try:
            paper_order(PaperOrderRequest(symbol="CLEAN", transaction_type="BUY", price=100.0, quantity=2, fill_id="CC1"), user_id=clean_user, db=db)
        finally:
            db.close()
        db = clean_session()
        try:
            first = _reconcile_paper_ledger(db, clean_user)
            second = _reconcile_paper_ledger(db, clean_user)
            assert_contract(first)
            assert first == second
            assert first["status"] == "OK"
            assert first["repairability"] == "NONE"
        finally:
            db.close()
    finally:
        clean_engine.dispose()

    # SAFE_DRY_RUN: account-only mutation must not alter the reconstructed ledger.
    safe_engine, safe_session, safe_user = seed_db(tmp_path / "contract-safe.db", "contract-safe@example.com")
    try:
        db = safe_session()
        try:
            paper_order(PaperOrderRequest(symbol="SAFE", transaction_type="BUY", price=100.0, quantity=2, fill_id="SC1"), user_id=safe_user, db=db)
        finally:
            db.close()
        db = safe_session()
        try:
            db.query(TradingAccount).filter(TradingAccount.user_id == safe_user).one().virtual_balance += 1.0
            db.commit()
            result = _reconcile_paper_ledger(db, safe_user)
            assert_contract(result)
            assert result["status"] == "MISMATCH"
            assert result["repairability"] == "SAFE_DRY_RUN"
            assert result["mismatch_categories"] == ["ACCOUNTING_STATE"]
        finally:
            db.close()
    finally:
        safe_engine.dispose()

    # BLOCKED: order/audit corruption takes precedence over any proposed repair.
    blocked_engine, blocked_session, blocked_user = seed_db(tmp_path / "contract-blocked.db", "contract-blocked@example.com")
    try:
        db = blocked_session()
        try:
            paper_order(PaperOrderRequest(symbol="BLOCKED", transaction_type="BUY", price=100.0, quantity=2, fill_id="BC1"), user_id=blocked_user, db=db)
        finally:
            db.close()
        db = blocked_session()
        try:
            order = db.query(Order).filter(Order.user_id == blocked_user, Order.is_paper.is_(True)).one()
            order.price = 101.0
            db.commit()
            result = _reconcile_paper_ledger(db, blocked_user)
            assert_contract(result)
            assert result["status"] == "MISMATCH"
            assert result["repairability"] == "BLOCKED"
            assert "ORDER_INTEGRITY" in result["mismatch_categories"]
            assert "AUDIT_INTEGRITY" in result["mismatch_categories"]
            assert result["repair_plan"]["apply"] is False
        finally:
            db.close()
    finally:
        blocked_engine.dispose()




def test_paper_reconcile_http_ignores_client_user_id_override_and_uses_dependency_identity():
    from app.execution import paper_routes as routes

    db = SessionLocal()
    try:
        accounts = (
            db.query(TradingAccount)
            .filter(TradingAccount.is_active.is_(True), TradingAccount.mode == "PAPER")
            .order_by(TradingAccount.id.asc())
            .all()
        )
        if len(accounts) < 2:
            pytest.skip("requires two active paper accounts for cross-user HTTP authorization test")
        authorized_id = int(accounts[0].user_id)
        attempted_id = int(accounts[1].user_id)
    finally:
        db.close()

    client = TestClient(app)
    app.dependency_overrides[routes.current_user_id] = lambda: authorized_id
    try:
        response = client.get(
            "/api/v1/execution/paper/reconcile",
            params={"user_id": attempted_id},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == authorized_id
        assert data["user_id"] != attempted_id
        assert data["repair_plan"]["apply"] is False
    finally:
        app.dependency_overrides.pop(routes.current_user_id, None)


def test_paper_reconcile_http_dependency_identity_can_be_changed_without_client_selector():
    from app.execution import paper_routes as routes

    db = SessionLocal()
    try:
        accounts = (
            db.query(TradingAccount)
            .filter(TradingAccount.is_active.is_(True), TradingAccount.mode == "PAPER")
            .order_by(TradingAccount.id.asc())
            .all()
        )
        if len(accounts) < 2:
            pytest.skip("requires two active paper accounts for dependency identity switching")
        first_id = int(accounts[0].user_id)
        second_id = int(accounts[1].user_id)
    finally:
        db.close()

    client = TestClient(app)
    try:
        app.dependency_overrides[routes.current_user_id] = lambda: first_id
        first = client.get(
            "/api/v1/execution/paper/reconcile",
            params={"user_id": second_id},
        )
        assert first.status_code == 200
        assert first.json()["user_id"] == first_id

        app.dependency_overrides[routes.current_user_id] = lambda: second_id
        second = client.get(
            "/api/v1/execution/paper/reconcile",
            params={"user_id": first_id},
        )
        assert second.status_code == 200
        assert second.json()["user_id"] == second_id
        assert first.json()["user_id"] != second.json()["user_id"]
    finally:
        app.dependency_overrides.pop(routes.current_user_id, None)


def test_paper_reconcile_repair_plan_integrity_partial_fills_flat_reversals_and_multiple_symbols(tmp_path):
    """Repair plans stay deterministic/read-only across partial closes, reversals and symbols."""
    from app.core.database import get_db
    from app.execution import paper_routes as routes
    from app.execution.paper_routes import PaperOrderRequest, paper_order

    engine = create_engine(
        f"sqlite:///{tmp_path / 'repair-plan-integrity-matrix.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    TestSession = sessionmaker(bind=engine)

    seed = TestSession()
    try:
        user = User(
            email="repair-integrity-matrix@example.com",
            hashed_password="",
            full_name="Repair Integrity Matrix",
            is_active=True,
        )
        seed.add(user)
        seed.flush()
        seed.add(            TradingAccount(
                user_id=user.id,
                mode="PAPER",
                virtual_balance=10_000.0,
                initial_virtual_balance=10_000.0,
                initial_balance_source="BOOTSTRAP",
                realized_pnl=0.0,
                is_active=True,
            )
        )
        seed.commit()
        user_id = int(user.id)
    finally:
        seed.close()

    # ALPHA: long -> partial close -> flat.
    # BETA: short -> partial cover -> flat.
    # GAMMA: independent long remains open.
    sequence = (
        PaperOrderRequest(symbol="ALPHA", transaction_type="BUY", price=100.0, quantity=10, fill_id="ALPHA-1"),
        PaperOrderRequest(symbol="ALPHA", transaction_type="SELL", price=120.0, quantity=4, fill_id="ALPHA-2"),
        PaperOrderRequest(symbol="ALPHA", transaction_type="SELL", price=110.0, quantity=6, fill_id="ALPHA-3"),
        PaperOrderRequest(symbol="BETA", transaction_type="SELL", price=200.0, quantity=5, fill_id="BETA-1"),
        PaperOrderRequest(symbol="BETA", transaction_type="BUY", price=180.0, quantity=2, fill_id="BETA-2"),
        PaperOrderRequest(symbol="BETA", transaction_type="BUY", price=170.0, quantity=3, fill_id="BETA-3"),
        PaperOrderRequest(symbol="GAMMA", transaction_type="BUY", price=50.0, quantity=3, fill_id="GAMMA-1"),
    )

    for request in sequence:
        db = TestSession()
        try:
            result = paper_order(request, user_id=user_id, db=db)
            assert result["status"] == "success"
        finally:
            db.close()

    def reconcile():
        db = TestSession()
        try:
            return _reconcile_paper_ledger(db, user_id)
        finally:
            db.close()

    clean = reconcile()
    assert clean["status"] == "OK"
    assert clean["repairability"] == "NONE"
    assert clean["orders"] == 7
    assert clean["reconstructed_realized_pnl"] == 270.0
    assert clean["reconstructed_virtual_balance"] == 10_120.0
    assert clean["mismatches"] == []
    assert clean["repair_plan"]["apply"] is False
    assert clean["repair_plan"]["reason"] == "read_only_dry_run"
    assert clean["repair_plan"]["positions"] == {
        "GAMMA": {"quantity": 3, "average_price": 50.0},
    }
    clean_precondition = clean["repair_plan"]["precondition"]
    assert clean_precondition["order_count"] == 7
    assert clean_precondition["position_count"] == 1
    assert len(clean_precondition["state_hash"]) == 64
    assert clean_precondition["audit_head"]

    # A safe accounting-only mismatch must expose the deterministic repair
    # proposal while remaining strictly non-applicable.
    corrupt = TestSession()
    try:
        account = corrupt.query(TradingAccount).filter(
            TradingAccount.user_id == user_id
        ).one()
        account.virtual_balance += 7.25
        corrupt.commit()
    finally:
        corrupt.close()

    with ThreadPoolExecutor(max_workers=12) as pool:
        snapshots = list(pool.map(lambda _: reconcile(), range(12)))

    assert all(snapshot["status"] == "MISMATCH" for snapshot in snapshots)
    assert all(snapshot["repairability"] == "SAFE_DRY_RUN" for snapshot in snapshots)
    assert all(snapshot["repair_plan"]["apply"] is False for snapshot in snapshots)
    assert all(snapshot["repair_plan"]["reason"] == "read_only_dry_run" for snapshot in snapshots)
    assert all(snapshot["repair_plan"]["proposed_virtual_balance"] == 10_120.0 for snapshot in snapshots)
    assert all(snapshot["repair_plan"]["proposed_realized_pnl"] == 270.0 for snapshot in snapshots)
    assert all(snapshot["repair_plan"]["positions"] == clean["repair_plan"]["positions"] for snapshot in snapshots)
    assert all(snapshot["repair_plan"]["precondition"] == snapshots[0]["repair_plan"]["precondition"] for snapshot in snapshots)
    assert all(snapshot["mismatches"] == ["virtual_balance_mismatch"] for snapshot in snapshots)

    # Move the same mismatch onto a non-bootstrap baseline. This must hard
    # block any future repair/apply path even though the reconstructed ledger
    # itself is still internally coherent.
    blocked = TestSession()
    try:
        account = blocked.query(TradingAccount).filter(
            TradingAccount.user_id == user_id
        ).one()
        account.initial_balance_source = "MIGRATED_INFERRED"
        blocked.commit()
    finally:
        blocked.close()

    blocked_snapshots = [reconcile() for _ in range(4)]
    for snapshot in blocked_snapshots:
        assert snapshot["status"] == "MISMATCH"
        assert snapshot["repairability"] == "BLOCKED"
        assert snapshot["baseline_status"] == "MIGRATED_INFERRED"
        assert snapshot["repair_plan"]["apply"] is False
        assert snapshot["repair_plan"]["reason"] == "read_only_dry_run"
        assert snapshot["repair_plan"]["proposed_virtual_balance"] == 10_120.0
        assert snapshot["repair_plan"]["proposed_realized_pnl"] == 270.0
        assert snapshot["repair_plan"]["positions"] == clean["repair_plan"]["positions"]

    # Restore the canonical baseline and stored balance. The same ledger must
    # return to a clean, deterministic state with no repair side effects.
    restore = TestSession()
    try:
        account = restore.query(TradingAccount).filter(
            TradingAccount.user_id == user_id
        ).one()
        account.initial_balance_source = "BOOTSTRAP"
        account.virtual_balance = 10_120.0
        account.realized_pnl = 270.0
        restore.commit()
    finally:
        restore.close()

    final = reconcile()
    assert final == clean
    assert final["repair_plan"]["apply"] is False

    client = TestClient(app)
    def override_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[routes.current_user_id] = lambda: user_id
    try:
        responses = list(
            ThreadPoolExecutor(max_workers=8).map(
                lambda _: client.get("/api/v1/execution/paper/reconcile"),
                range(8),
            )
        )
        assert all(response.status_code == 200 for response in responses)
        payloads = [response.json() for response in responses]
        assert all(payload == payloads[0] for payload in payloads)
        assert payloads[0] == final
    finally:
        app.dependency_overrides.pop(routes.current_user_id, None)
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()\n

def test_paper_reconcile_http_failed_mutation_rollback_and_stale_precondition_boundary(tmp_path):
    """Concurrent readers never observe a failed mutation, and dry-run preconditions become stale after a commit."""
    from app.core.database import get_db
    from app.execution import paper_routes as routes
    from app.execution.paper_routes import PaperOrderRequest, _reconcile_paper_ledger, paper_order

    engine = create_engine(
        f"sqlite:///{tmp_path / 'http-rollback-precondition.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    TestSession = sessionmaker(bind=engine)

    seed = TestSession()
    try:
        user = User(
            email="http-rollback-precondition@example.com",
            hashed_password="",
            full_name="HTTP Rollback Precondition",
            is_active=True,
        )
        seed.add(user)
        seed.flush()
        seed.add(
            TradingAccount(
                user_id=user.id,
                mode="PAPER",
                virtual_balance=1_000.0,
                initial_virtual_balance=1_000.0,
                initial_balance_source="BOOTSTRAP",
                realized_pnl=0.0,
                is_active=True,
            )
        )
        seed.commit()
        user_id = int(user.id)
    finally:
        seed.close()

    # Establish a committed ledger snapshot that every reader must agree on.
    db = TestSession()
    try:
        created = paper_order(
            PaperOrderRequest(
                symbol="ROLLBACK",
                transaction_type="BUY",
                price=100.0,
                quantity=2,
                fill_id="RB-1",
            ),
            user_id=user_id,
            db=db,
        )
        assert created["status"] == "success"
    finally:
        db.close()

    def reconcile():
        db = TestSession()
        try:
            return _reconcile_paper_ledger(db, user_id)
        finally:
            db.close()

    baseline = reconcile()
    assert baseline["status"] == "OK"
    assert baseline["repairability"] == "NONE"
    assert baseline["orders"] == 1
    assert baseline["reconstructed_virtual_balance"] == 800.0
    assert baseline["repair_plan"]["apply"] is False

    # A separate writer may temporarily hold uncommitted state, but readers
    # must continue seeing the last committed snapshot and never dirty state.
    writer = TestSession()
    try:
        writer.connection().exec_driver_sql("BEGIN IMMEDIATE")
        account = writer.query(TradingAccount).filter(
            TradingAccount.user_id == user_id
        ).one()
        account.virtual_balance = 123.0
        writer.flush()

        with ThreadPoolExecutor(max_workers=12) as pool:
            snapshots = list(pool.map(lambda _: reconcile(), range(12)))

        assert all(snapshot == baseline for snapshot in snapshots)
        writer.rollback()
    finally:
        writer.close()

    assert reconcile() == baseline

    # The HTTP mutation path must also fail closed without leaking partial
    # account/order/position state to concurrent reconciliation readers.
    client = TestClient(app)

    def override_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[routes.current_user_id] = lambda: user_id
    try:
        def read_http(_):
            response = client.get("/api/v1/execution/paper/reconcile")
            assert response.status_code == 200
            return response.json()

        with ThreadPoolExecutor(max_workers=12) as pool:
            reader_future = [pool.submit(read_http, index) for index in range(12)]
            failed = client.post(
                "/api/v1/execution/paper/order",
                json={
                    "symbol": "SHOULD-ROLLBACK",
                    "transaction_type": "BUY",
                    "price": 1_000.0,
                    "quantity": 2,
                    "fill_id": "RB-FAILED",
                },
            )
            reader_payloads = [future.result() for future in reader_future]

        assert failed.status_code == 400
        assert failed.json()["detail"] == "Insufficient paper balance"
        assert all(payload == baseline for payload in reader_payloads)
    finally:
        app.dependency_overrides.pop(routes.current_user_id, None)
        app.dependency_overrides.pop(get_db, None)

    final = reconcile()
    assert final == baseline

    # A safe dry-run repair plan is intentionally read-only. Once a new
    # committed mutation occurs, its old precondition must no longer describe
    # the current durable state.
    corrupt = TestSession()
    try:
        account = corrupt.query(TradingAccount).filter(
            TradingAccount.user_id == user_id
        ).one()
        account.virtual_balance = 801.0
        corrupt.commit()
    finally:
        corrupt.close()

    stale_candidate = reconcile()
    assert stale_candidate["status"] == "MISMATCH"
    assert stale_candidate["repairability"] == "SAFE_DRY_RUN"
    assert stale_candidate["repair_plan"]["apply"] is False
    stale_precondition = stale_candidate["repair_plan"]["precondition"]
    assert stale_candidate["repair_plan"]["proposed_virtual_balance"] == 800.0

    db = TestSession()
    try:
        committed = paper_order(
            PaperOrderRequest(
                symbol="NEXT-EPOCH",
                transaction_type="BUY",
                price=50.0,
                quantity=2,
                fill_id="RB-2",
            ),
            user_id=user_id,
            db=db,
        )
        assert committed["status"] == "success"
    finally:
        db.close()

    current = reconcile()
    assert current["status"] == "MISMATCH"
    assert current["repairability"] == "SAFE_DRY_RUN"
    assert current["repair_plan"]["apply"] is False
    assert current["repair_plan"]["proposed_virtual_balance"] == 700.0
    assert current["repair_plan"]["precondition"]["order_count"] == 2
    assert current["repair_plan"]["precondition"]["state_hash"] != stale_precondition["state_hash"]
    assert current["repair_plan"]["precondition"]["audit_head"] != stale_precondition["audit_head"]
    assert current["repair_plan"]["positions"] == {
        "ROLLBACK": {"quantity": 2, "average_price": 100.0},
        "NEXT-EPOCH": {"quantity": 2, "average_price": 50.0},
    }

    # The previously captured dry-run plan is stale by construction and cannot
    # be treated as an apply token after the durable ledger changed.
    assert stale_precondition["order_count"] == 1
    assert current["repair_plan"]["reason"] == "read_only_dry_run"

    # Final concurrent readers must converge on one identical current snapshot.
    with ThreadPoolExecutor(max_workers=12) as pool:
        final_snapshots = list(pool.map(lambda _: reconcile(), range(12)))
    assert all(snapshot == current for snapshot in final_snapshots)

    verify = TestSession()
    try:
        assert verify.query(Order).filter(
            Order.user_id == user_id,
            Order.is_paper.is_(True),
        ).count() == 2
        assert verify.query(Position).filter(
            Position.user_id == user_id,
            Position.is_paper.is_(True),
            Position.is_open.is_(True),
        ).count() == 2
    finally:
        verify.close()
        engine.dispose()
\n

def test_paper_reconcile_http_concurrent_corruption_matrix_is_deterministic_and_read_only(tmp_path):
    """Concurrent HTTP reconciliation is deterministic across every corruption class."""
    from app.core.database import get_db
    from app.execution import paper_routes as routes
    from app.execution.paper_routes import PaperOrderRequest, paper_order

    engine = create_engine(
        f"sqlite:///{tmp_path / 'http-corruption-matrix.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    TestSession = sessionmaker(bind=engine)

    seed = TestSession()
    try:
        user = User(
            email="http-corruption-matrix@example.com",
            hashed_password="",
            full_name="HTTP Corruption Matrix",
            is_active=True,
        )
        seed.add(user)
        seed.flush()
        seed.add(
            TradingAccount(
                user_id=user.id,
                mode="PAPER",
                virtual_balance=1_000.0,
                initial_virtual_balance=1_000.0,
                initial_balance_source="BOOTSTRAP",
                realized_pnl=0.0,
                is_active=True,
            )
        )
        seed.commit()
        user_id = int(user.id)
    finally:
        seed.close()

    db = TestSession()
    try:
        created = paper_order(
            PaperOrderRequest(
                symbol="MATRIX",
                transaction_type="BUY",
                price=100.0,
                quantity=2,
                fill_id="MATRIX-1",
            ),
            user_id=user_id,
            db=db,
        )
        assert created["status"] == "success"
    finally:
        db.close()

    def http_snapshots():
        client = TestClient(app)

        def override_db():
            db = TestSession()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[routes.current_user_id] = lambda: user_id
        try:
            with ThreadPoolExecutor(max_workers=16) as pool:
                responses = list(
                    pool.map(
                        lambda _: client.get("/api/v1/execution/paper/reconcile"),
                        range(16),
                    )
                )
            assert all(response.status_code == 200 for response in responses)
            payloads = [response.json() for response in responses]
            assert all(payload == payloads[0] for payload in payloads)
            return payloads[0]
        finally:
            app.dependency_overrides.pop(routes.current_user_id, None)
            app.dependency_overrides.pop(get_db, None)

    def mutate_and_read(name):
        db = TestSession()
        try:
            order = db.query(Order).filter(
                Order.user_id == user_id,
                Order.is_paper.is_(True),
            ).one()
            position = db.query(Position).filter(
                Position.user_id == user_id,
                Position.is_paper.is_(True),
                Position.is_open.is_(True),
            ).one()
            account = db.query(TradingAccount).filter(
                TradingAccount.user_id == user_id,
                TradingAccount.mode == "PAPER",
            ).one()

            if name == "POSITION_STATE":
                position.quantity = 1
                db.commit()
            elif name == "ACCOUNTING_STATE":
                account.virtual_balance = 801.0
                db.commit()
            elif name == "ORDER_INTEGRITY":
                order.price = 101.0
                db.commit()
            elif name == "AUDIT_INTEGRITY":
                order.audit_hash = "0" * 64
                db.commit()
            else:
                raise AssertionError(name)
        finally:
            db.close()

        return http_snapshots()

    def restore():
        db = TestSession()
        try:
            order = db.query(Order).filter(
                Order.user_id == user_id,
                Order.is_paper.is_(True),
            ).one()
            position = db.query(Position).filter(
                Position.user_id == user_id,
                Position.is_paper.is_(True),
                Position.is_open.is_(True),
            ).one()
            account = db.query(TradingAccount).filter(
                TradingAccount.user_id == user_id,
                TradingAccount.mode == "PAPER",
            ).one()
            position.quantity = 2
            account.virtual_balance = 800.0
            order.price = 100.0
            order.audit_hash = routes._paper_audit_payload(
                user_id=user_id,
                symbol=order.symbol,
                side=str(order.transaction_type).upper(),
                quantity=int(order.filled_quantity or 0),
                price=100.0,
                pnl=float(order.pnl or 0.0),
                fill_id=order.fill_id,
                previous_hash=order.previous_audit_hash,
            )
            db.commit()
        finally:
            db.close()

    clean = http_snapshots()
    assert clean["status"] == "OK"
    assert clean["repairability"] == "NONE"
    assert clean["mismatch_categories"] == []
    assert clean["repair_plan"]["apply"] is False

    expected = {
        "POSITION_STATE": {
            "status": "MISMATCH",
            "repairability": "SAFE_DRY_RUN",
            "categories": ["POSITION_STATE"],
            "mismatches": ["position_mismatch:MATRIX"],
        },
        "ACCOUNTING_STATE": {
            "status": "MISMATCH",
            "repairability": "SAFE_DRY_RUN",
            "categories": ["ACCOUNTING_STATE"],
            "mismatches": ["virtual_balance_mismatch"],
        },
        "ORDER_INTEGRITY": {
            "status": "MISMATCH",
            "repairability": "BLOCKED",
            "categories": ["ORDER_INTEGRITY", "AUDIT_INTEGRITY"],
            "mismatches": ["audit_hash_mismatch:1"],
        },
        "AUDIT_INTEGRITY": {
            "status": "MISMATCH",
            "repairability": "BLOCKED",
            "categories": ["AUDIT_INTEGRITY"],
            "mismatches": ["audit_hash_mismatch:1"],
        },
    }

    for corruption, contract in expected.items():
        snapshot = mutate_and_read(corruption)
        assert snapshot["status"] == contract["status"]
        assert snapshot["repairability"] == contract["repairability"]
        assert snapshot["mismatch_categories"] == contract["categories"]
        assert snapshot["mismatches"] == contract["mismatches"]
        assert snapshot["repair_plan"]["apply"] is False
        assert snapshot["repair_plan"]["reason"] == "read_only_dry_run"
        assert snapshot["user_id"] == user_id
        assert snapshot["orders"] == 1
        assert snapshot["repair_plan"]["precondition"]["order_count"] == 1
        restore()

    final = http_snapshots()
    assert final == clean

    verify = TestSession()
    try:
        assert verify.query(Order).filter(
            Order.user_id == user_id,
            Order.is_paper.is_(True),
        ).count() == 1
        assert verify.query(Position).filter(
            Position.user_id == user_id,
            Position.is_paper.is_(True),
            Position.is_open.is_(True),
        ).count() == 1
        account = verify.query(TradingAccount).filter(
            TradingAccount.user_id == user_id,
            TradingAccount.mode == "PAPER",
        ).one()
        assert float(account.virtual_balance) == 800.0
        assert float(account.realized_pnl) == 0.0
    finally:
        verify.close()
        engine.dispose()
\n

def test_paper_reconcile_http_mixed_corruption_precedence_is_complete_and_deterministic(tmp_path):
    """Mixed corruption must report every category and always block repair."""
    from app.core.database import get_db
    from app.execution import paper_routes as routes
    from app.execution.paper_routes import PaperOrderRequest, paper_order

    engine = create_engine(
        f"sqlite:///{tmp_path / 'http-mixed-corruption.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    TestSession = sessionmaker(bind=engine)

    seed = TestSession()
    try:
        user = User(
            email="http-mixed-corruption@example.com",
            hashed_password="",
            full_name="HTTP Mixed Corruption",
            is_active=True,
        )
        seed.add(user)
        seed.flush()
        seed.add(
            TradingAccount(
                user_id=user.id,
                mode="PAPER",
                virtual_balance=1_000.0,
                initial_virtual_balance=1_000.0,
                initial_balance_source="BOOTSTRAP",
                realized_pnl=0.0,
                is_active=True,
            )
        )
        seed.commit()
        user_id = int(user.id)
    finally:
        seed.close()

    db = TestSession()
    try:
        created = paper_order(
            PaperOrderRequest(
                symbol="MIXED",
                transaction_type="BUY",
                price=100.0,
                quantity=2,
                fill_id="MIXED-1",
            ),
            user_id=user_id,
            db=db,
        )
        assert created["status"] == "success"
    finally:
        db.close()

    def http_reconcile():
        client = TestClient(app)

        def override_db():
            db = TestSession()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[routes.current_user_id] = lambda: user_id
        try:
            with ThreadPoolExecutor(max_workers=20) as pool:
                responses = list(
                    pool.map(
                        lambda _: client.get("/api/v1/execution/paper/reconcile"),
                        range(20),
                    )
                )
            assert all(response.status_code == 200 for response in responses)
            payloads = [response.json() for response in responses]
            assert all(payload == payloads[0] for payload in payloads)
            return payloads[0]
        finally:
            app.dependency_overrides.pop(routes.current_user_id, None)
            app.dependency_overrides.pop(get_db, None)

    clean = http_reconcile()
    assert clean["status"] == "OK"
    assert clean["repairability"] == "NONE"
    assert clean["mismatch_categories"] == []
    assert clean["mismatches"] == []

    corrupt = TestSession()
    try:
        order = corrupt.query(Order).filter(
            Order.user_id == user_id,
            Order.is_paper.is_(True),
        ).one()
        position = corrupt.query(Position).filter(
            Position.user_id == user_id,
            Position.is_paper.is_(True),
            Position.is_open.is_(True),
        ).one()
        account = corrupt.query(TradingAccount).filter(
            TradingAccount.user_id == user_id,
            TradingAccount.mode == "PAPER",
        ).one()

        # Deliberately introduce three independent corruption classes in one
        # durable state: audit integrity, position state, and accounting state.
        order.audit_hash = "f" * 64
        position.quantity = 1
        account.virtual_balance = 801.0
        corrupt.commit()
    finally:
        corrupt.close()

    expected_categories = [
        "ACCOUNTING_STATE",
        "AUDIT_INTEGRITY",
        "POSITION_STATE",
    ]
    expected_mismatches = [
        "audit_hash_mismatch:1",
        "position_mismatch:MIXED",
        "virtual_balance_mismatch",
    ]

    with ThreadPoolExecutor(max_workers=4) as pool:
        snapshots = list(pool.map(lambda _: http_reconcile(), range(4)))

    for snapshot in snapshots:
        assert snapshot["status"] == "MISMATCH"
        assert snapshot["repairability"] == "BLOCKED"
        assert snapshot["repairability_reason"] == "ledger_or_baseline_integrity_failure"
        assert snapshot["mismatch_categories"] == expected_categories
        assert snapshot["mismatches"] == expected_mismatches
        assert snapshot["orders"] == 1
        assert snapshot["user_id"] == user_id
        assert snapshot["repair_plan"]["apply"] is False
        assert snapshot["repair_plan"]["reason"] == "read_only_dry_run"
        assert snapshot["repair_plan"]["precondition"]["order_count"] == 1
        assert snapshot["repair_plan"]["positions"] == {
            "MIXED": {"quantity": 2, "average_price": 100.0},
        }
        assert snapshot["repair_plan"]["proposed_virtual_balance"] == 800.0
        assert snapshot["repair_plan"]["proposed_realized_pnl"] == 0.0

    # All concurrent reads must be byte-for-byte equivalent at the JSON object
    # level, not merely equivalent in status/category fields.
    assert all(snapshot == snapshots[0] for snapshot in snapshots)

    verify = TestSession()
    try:
        order = verify.query(Order).filter(
            Order.user_id == user_id,
            Order.is_paper.is_(True),
        ).one()
        position = verify.query(Position).filter(
            Position.user_id == user_id,
            Position.is_paper.is_(True),
            Position.is_open.is_(True),
        ).one()
        account = verify.query(TradingAccount).filter(
            TradingAccount.user_id == user_id,
            TradingAccount.mode == "PAPER",
        ).one()

        assert order.audit_hash == "f" * 64
        assert int(position.quantity) == 1
        assert float(account.virtual_balance) == 801.0
        assert verify.query(Order).filter(
            Order.user_id == user_id,
            Order.is_paper.is_(True),
        ).count() == 1
        assert verify.query(Position).filter(
            Position.user_id == user_id,
            Position.is_paper.is_(True),
            Position.is_open.is_(True),
        ).count() == 1
    finally:
        verify.close()

    # Re-running the same corruption without any mutation must reproduce the
    # exact same reconciliation result.
    repeated = http_reconcile()
    assert repeated == snapshots[0]

    engine.dispose()


def test_paper_reconcile_http_baseline_integrity_precedence_with_mixed_ledger_corruption(tmp_path):
    """A non-bootstrap baseline must always block repair, including mixed ledger corruption."""
    from app.core.database import get_db
    from app.execution import paper_routes as routes
    from app.execution.paper_routes import PaperOrderRequest, paper_order

    engine = create_engine(
        f"sqlite:///{tmp_path / 'http-baseline-precedence.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    TestSession = sessionmaker(bind=engine)

    seed = TestSession()
    try:
        user = User(
            email="http-baseline-precedence@example.com",
            hashed_password="",
            full_name="HTTP Baseline Precedence",
            is_active=True,
        )
        seed.add(user)
        seed.flush()
        seed.add(
            TradingAccount(
                user_id=user.id,
                mode="PAPER",
                virtual_balance=1_000.0,
                initial_virtual_balance=1_000.0,
                initial_balance_source="BOOTSTRAP",
                realized_pnl=0.0,
                is_active=True,
            )
        )
        seed.commit()
        user_id = int(user.id)
    finally:
        seed.close()

    db = TestSession()
    try:
        created = paper_order(
            PaperOrderRequest(
                symbol="BASELINE",
                transaction_type="BUY",
                price=100.0,
                quantity=2,
                fill_id="BASELINE-1",
            ),
            user_id=user_id,
            db=db,
        )
        assert created["status"] == "success"
    finally:
        db.close()

    def http_reconcile():
        client = TestClient(app)

        def override_db():
            db = TestSession()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[routes.current_user_id] = lambda: user_id
        try:
            with ThreadPoolExecutor(max_workers=20) as pool:
                responses = list(
                    pool.map(
                        lambda _: client.get("/api/v1/execution/paper/reconcile"),
                        range(20),
                    )
                )
            assert all(response.status_code == 200 for response in responses)
            payloads = [response.json() for response in responses]
            assert all(payload == payloads[0] for payload in payloads)
            return payloads[0]
        finally:
            app.dependency_overrides.pop(routes.current_user_id, None)
            app.dependency_overrides.pop(get_db, None)

    def apply_corruption(case):
        db = TestSession()
        try:
            order = db.query(Order).filter(
                Order.user_id == user_id,
                Order.is_paper.is_(True),
            ).one()
            position = db.query(Position).filter(
                Position.user_id == user_id,
                Position.is_paper.is_(True),
                Position.is_open.is_(True),
            ).one()
            account = db.query(TradingAccount).filter(
                TradingAccount.user_id == user_id,
                TradingAccount.mode == "PAPER",
            ).one()

            # Baseline corruption is intentionally independent of ledger
            # corruption: the order audit chain remains valid unless this case
            # explicitly adds AUDIT_INTEGRITY.
            account.initial_balance_source = "LEGACY_IMPORT"

            if case in {"BASELINE_ACCOUNTING_POSITION", "BASELINE_ALL"}:
                account.virtual_balance = 801.0
                position.quantity = 1
            if case == "BASELINE_ALL":
                order.audit_hash = "e" * 64

            db.commit()
        finally:
            db.close()

    def restore():
        db = TestSession()
        try:
            order = db.query(Order).filter(
                Order.user_id == user_id,
                Order.is_paper.is_(True),
            ).one()
            position = db.query(Position).filter(
                Position.user_id == user_id,
                Position.is_paper.is_(True),
                Position.is_open.is_(True),
            ).one()
            account = db.query(TradingAccount).filter(
                TradingAccount.user_id == user_id,
                TradingAccount.mode == "PAPER",
            ).one()

            position.quantity = 2
            account.virtual_balance = 800.0
            account.initial_balance_source = "BOOTSTRAP"
            order.audit_hash = routes._paper_audit_payload(
                user_id=user_id,
                symbol=order.symbol,
                side=str(order.transaction_type).upper(),
                quantity=int(order.filled_quantity or 0),
                price=float(order.average_fill_price or order.price),
                pnl=float(order.pnl or 0.0),
                fill_id=order.fill_id,
                previous_hash=order.previous_audit_hash,
            )
            db.commit()
        finally:
            db.close()

    clean = http_reconcile()
    assert clean["status"] == "OK"
    assert clean["baseline_status"] == "BOOTSTRAP"
    assert clean["repairability"] == "NONE"
    assert clean["mismatch_categories"] == []
    assert clean["mismatches"] == []

    expected = {
        "BASELINE_ONLY": {
            "status": "OK",
            "categories": ["BASELINE_INTEGRITY"],
            "mismatches": [],
            "baseline_status": "LEGACY_IMPORT",
        },
        "BASELINE_ACCOUNTING_POSITION": {
            "status": "MISMATCH",
            "categories": ["ACCOUNTING_STATE", "BASELINE_INTEGRITY", "POSITION_STATE"],
            "mismatches": [
                "position_mismatch:BASELINE",
                "virtual_balance_mismatch",
            ],
            "baseline_status": "LEGACY_IMPORT",
        },
        "BASELINE_ALL": {
            "status": "MISMATCH",
            "categories": [
                "ACCOUNTING_STATE",
                "AUDIT_INTEGRITY",
                "BASELINE_INTEGRITY",
                "POSITION_STATE",
            ],
            "mismatches": [
                "audit_hash_mismatch:1",
                "position_mismatch:BASELINE",
                "virtual_balance_mismatch",
            ],
            "baseline_status": "LEGACY_IMPORT",
        },
    }

    for case, contract in expected.items():
        if case == "BASELINE_ONLY":
            apply_corruption(case)
        else:
            apply_corruption(case)

        snapshots = [http_reconcile() for _ in range(2)]
        assert snapshots[0] == snapshots[1]

        snapshot = snapshots[0]
        assert snapshot["status"] == contract["status"]
        # Baseline integrity alone must not become SAFE_DRY_RUN. Adding
        # account/position corruption must not downgrade or mask the baseline
        # failure, and adding audit corruption must still report every class.
        assert snapshot["repairability"] == "BLOCKED"
        assert snapshot["repairability_reason"] == "ledger_or_baseline_integrity_failure"
        assert snapshot["baseline_status"] == contract["baseline_status"]
        assert snapshot["mismatch_categories"] == contract["categories"]
        assert snapshot["mismatches"] == contract["mismatches"]
        assert snapshot["user_id"] == user_id
        assert snapshot["orders"] == 1
        assert snapshot["repair_plan"]["apply"] is False
        assert snapshot["repair_plan"]["reason"] == "read_only_dry_run"
        assert snapshot["repair_plan"]["proposed_virtual_balance"] == 800.0
        assert snapshot["repair_plan"]["positions"] == {
            "BASELINE": {"quantity": 2, "average_price": 100.0},
        }

        verify = TestSession()
        try:
            account = verify.query(TradingAccount).filter(
                TradingAccount.user_id == user_id,
                TradingAccount.mode == "PAPER",
            ).one()
            position = verify.query(Position).filter(
                Position.user_id == user_id,
                Position.is_paper.is_(True),
                Position.is_open.is_(True),
            ).one()
            order = verify.query(Order).filter(
                Order.user_id == user_id,
                Order.is_paper.is_(True),
            ).one()
            assert account.initial_balance_source == "LEGACY_IMPORT"
            if case == "BASELINE_ONLY":
                assert float(account.virtual_balance) == 800.0
                assert int(position.quantity) == 2
                assert order.audit_hash == routes._paper_audit_payload(
                    user_id=user_id,
                    symbol=order.symbol,
                    side=str(order.transaction_type).upper(),
                    quantity=int(order.filled_quantity or 0),
                    price=float(order.average_fill_price or order.price),
                    pnl=float(order.pnl or 0.0),
                    fill_id=order.fill_id,
                    previous_hash=order.previous_audit_hash,
                )
            elif case == "BASELINE_ACCOUNTING_POSITION":
                assert float(account.virtual_balance) == 801.0
                assert int(position.quantity) == 1
            else:
                assert float(account.virtual_balance) == 801.0
                assert int(position.quantity) == 1
                assert order.audit_hash == "e" * 64
        finally:
            verify.close()

        restore()
        assert http_reconcile() == clean

    engine.dispose()


def test_paper_reconcile_http_baseline_transition_invalidates_stale_repair_precondition(tmp_path):
    """Baseline transitions must invalidate a previously safe dry-run precondition."""
    from app.core.database import get_db
    from app.execution import paper_routes as routes
    from app.execution.paper_routes import PaperOrderRequest, paper_order

    engine = create_engine(
        f"sqlite:///{tmp_path / 'http-baseline-transition.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    TestSession = sessionmaker(bind=engine)

    seed = TestSession()
    try:
        user = User(
            email="http-baseline-transition@example.com",
            hashed_password="",
            full_name="HTTP Baseline Transition",
            is_active=True,
        )
        seed.add(user)
        seed.flush()
        seed.add(
            TradingAccount(
                user_id=user.id,
                mode="PAPER",
                virtual_balance=1_000.0,
                initial_virtual_balance=1_000.0,
                initial_balance_source="BOOTSTRAP",
                realized_pnl=0.0,
                is_active=True,
            )
        )
        seed.commit()
        user_id = int(user.id)
    finally:
        seed.close()

    db = TestSession()
    try:
        result = paper_order(
            PaperOrderRequest(
                symbol="TRANSITION",
                transaction_type="BUY",
                price=100.0,
                quantity=2,
                fill_id="TRANSITION-1",
            ),
            user_id=user_id,
            db=db,
        )
        assert result["status"] == "success"
    finally:
        db.close()

    def http_snapshots():
        client = TestClient(app)

        def override_db():
            db = TestSession()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[routes.current_user_id] = lambda: user_id
        try:
            with ThreadPoolExecutor(max_workers=20) as pool:
                responses = list(
                    pool.map(
                        lambda _: client.get("/api/v1/execution/paper/reconcile"),
                        range(20),
                    )
                )
            assert all(response.status_code == 200 for response in responses)
            payloads = [response.json() for response in responses]
            assert all(payload == payloads[0] for payload in payloads)
            return payloads
        finally:
            app.dependency_overrides.pop(routes.current_user_id, None)
            app.dependency_overrides.pop(get_db, None)

    def mutate(**changes):
        db = TestSession()
        try:
            account = db.query(TradingAccount).filter(
                TradingAccount.user_id == user_id,
                TradingAccount.mode == "PAPER",
            ).one()
            for key, value in changes.items():
                setattr(account, key, value)
            db.commit()
        finally:
            db.close()

    def restore():
        db = TestSession()
        try:
            account = db.query(TradingAccount).filter(
                TradingAccount.user_id == user_id,
                TradingAccount.mode == "PAPER",
            ).one()
            account.virtual_balance = 801.0
            account.initial_balance_source = "BOOTSTRAP"
            db.commit()
        finally:
            db.close()

    # Epoch A: a normal accounting mismatch is safely dry-run repairable.
    mutate(virtual_balance=801.0)
    safe_payloads = http_snapshots()
    safe = safe_payloads[0]
    assert all(payload == safe for payload in safe_payloads)
    assert safe["status"] == "MISMATCH"
    assert safe["repairability"] == "SAFE_DRY_RUN"
    assert safe["repairability_reason"] == "account_or_position_state_only"
    assert safe["baseline_status"] == "BOOTSTRAP"
    assert safe["mismatch_categories"] == ["ACCOUNTING_STATE"]
    assert safe["mismatches"] == ["virtual_balance_mismatch"]
    assert safe["repair_plan"]["apply"] is False
    assert safe["repair_plan"]["reason"] == "read_only_dry_run"

    stale_precondition = safe["repair_plan"]["precondition"]
    assert stale_precondition["order_count"] == 1
    assert stale_precondition["position_count"] == 1
    assert stale_precondition["audit_head"]

    # Epoch B: changing only the baseline classification must invalidate the
    # old precondition and hard-block repair, even though the ledger mismatch
    # itself is unchanged.
    mutate(initial_balance_source="MIGRATED_INFERRED")
    blocked_payloads = http_snapshots()
    blocked = blocked_payloads[0]
    assert all(payload == blocked for payload in blocked_payloads)
    assert blocked["status"] == "MISMATCH"
    assert blocked["repairability"] == "BLOCKED"
    assert blocked["repairability_reason"] == "ledger_or_baseline_integrity_failure"
    assert blocked["baseline_status"] == "MIGRATED_INFERRED"
    assert blocked["mismatch_categories"] == [
        "ACCOUNTING_STATE",
        "BASELINE_INTEGRITY",
    ]
    assert blocked["mismatches"] == ["virtual_balance_mismatch"]
    assert blocked["repair_plan"]["apply"] is False
    assert blocked["repair_plan"]["reason"] == "read_only_dry_run"

    blocked_precondition = blocked["repair_plan"]["precondition"]
    assert blocked_precondition != stale_precondition
    assert blocked_precondition["state_hash"] != stale_precondition["state_hash"]
    assert blocked_precondition["audit_head"] == stale_precondition["audit_head"]
    assert blocked_precondition["order_count"] == stale_precondition["order_count"]
    assert blocked_precondition["position_count"] == stale_precondition["position_count"]

    # A stale SAFE_DRY_RUN precondition must never equal the current blocked
    # state. This is the exact equality a future apply endpoint must require.
    assert stale_precondition["state_hash"] != blocked_precondition["state_hash"]

    # Epoch C: restoring the canonical baseline returns to the same safe state
    # only after the stored state is explicitly restored; no reconciliation
    # call performs this restoration.
    restore()
    restored_payloads = http_snapshots()
    restored = restored_payloads[0]
    assert all(payload == restored for payload in restored_payloads)
    assert restored == safe

    verify = TestSession()
    try:
        account = verify.query(TradingAccount).filter(
            TradingAccount.user_id == user_id,
            TradingAccount.mode == "PAPER",
        ).one()
        assert account.initial_balance_source == "BOOTSTRAP"
        assert float(account.virtual_balance) == 801.0
        assert verify.query(Order).filter(
            Order.user_id == user_id,
            Order.is_paper.is_(True),
        ).count() == 1
        assert verify.query(Position).filter(
            Position.user_id == user_id,
            Position.is_paper.is_(True),
            Position.is_open.is_(True),
        ).count() == 1
    finally:
        verify.close()
        engine.dispose()


def test_paper_reconcile_http_repair_plan_audit_chain_consistency_matrix(tmp_path):
    """Audit tampering must stay deterministic, dry-run only, and never expose a repairable plan."""
    from app.execution import paper_routes as routes
    from app.core.database import get_db

    variants = ("pnl", "delete_order", "duplicate_fill", "previous_hash")

    for variant in variants:
        engine = create_engine(
            f"sqlite:///{tmp_path / f'repair-audit-http-{variant}.db'}",
            connect_args={"check_same_thread": False, "timeout": 10},
        )
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)

        seed = Session()
        try:
            user = User(
                email=f"repair-audit-http-{variant}@example.com",
                hashed_password="",
                full_name="Repair Audit HTTP",
                is_active=True,
            )
            seed.add(user)
            seed.flush()
            seed.add(
                TradingAccount(
                    user_id=user.id,
                    mode="PAPER",
                    virtual_balance=1000.0,
                    initial_virtual_balance=1000.0,
                    initial_balance_source="BOOTSTRAP",
                    realized_pnl=0.0,
                    is_active=True,
                )
            )
            seed.commit()
            user_id = user.id
        finally:
            seed.close()

        db = Session()
        try:
            from app.execution.paper_routes import PaperOrderRequest, paper_order

            paper_order(
                PaperOrderRequest(
                    symbol="AUDIT-MATRIX",
                    transaction_type="BUY",
                    price=100.0,
                    quantity=5,
                    fill_id="AUDIT-MATRIX-1",
                ),
                user_id=user_id,
                db=db,
            )
            paper_order(
                PaperOrderRequest(
                    symbol="AUDIT-MATRIX",
                    transaction_type="SELL",
                    price=120.0,
                    quantity=2,
                    fill_id="AUDIT-MATRIX-2",
                ),
                user_id=user_id,
                db=db,
            )
        finally:
            db.close()

        tamper = Session()
        try:
            orders = (
                tamper.query(Order)
                .filter(Order.user_id == user_id, Order.is_paper.is_(True))
                .order_by(Order.id.asc())
                .all()
            )
            assert len(orders) == 2
            first, second = orders

            if variant == "pnl":
                second.pnl = 41.0
            elif variant == "delete_order":
                tamper.delete(first)
            elif variant == "duplicate_fill":
                second.fill_id = first.fill_id
            elif variant == "previous_hash":
                second.previous_audit_hash = "f" * 64

            tamper.commit()
        finally:
            tamper.close()

        def db_override():
            session = Session()
            try:
                yield session
            finally:
                session.close()

        app.dependency_overrides[get_db] = db_override
        app.dependency_overrides[routes.current_user_id] = lambda: user_id
        client = TestClient(app)

        try:
            def read_reconcile():
                response = client.get("/api/v1/execution/paper/reconcile")
                assert response.status_code == 200
                return response.json()

            with ThreadPoolExecutor(max_workers=20) as pool:
                results = list(pool.map(lambda _: read_reconcile(), range(20)))

            assert all(result == results[0] for result in results)
            data = results[0]
            assert data["user_id"] == user_id
            assert data["status"] == "MISMATCH"
            assert data["repairability"] == "BLOCKED"
            assert data["repair_plan"]["apply"] is False
            assert data["repair_plan"]["reason"] == "read_only_dry_run"
            assert data["baseline_status"] == "BOOTSTRAP"

            if variant == "pnl":
                assert data["mismatches"] == ["audit_hash_mismatch:2"]
                assert data["mismatch_categories"] == ["AUDIT_INTEGRITY"]
                assert data["repair_plan"]["proposed_realized_pnl"] == 40.0
                assert data["repair_plan"]["proposed_virtual_balance"] == 740.0
                assert data["repair_plan"]["positions"]["AUDIT-MATRIX"] == {
                    "quantity": 3,
                    "average_price": 100.0,
                }
            elif variant == "delete_order":
                assert data["repairability"] == "BLOCKED"
                assert "audit_chain_mismatch:2" in data["mismatches"]
                assert "AUDIT_INTEGRITY" in data["mismatch_categories"]
            elif variant == "duplicate_fill":
                assert "duplicate_fill_id:AUDIT-MATRIX-1" in data["mismatches"]
                assert "AUDIT_INTEGRITY" in data["mismatch_categories"]
            elif variant == "previous_hash":
                assert data["mismatches"] == ["audit_chain_mismatch:2", "audit_hash_mismatch:2"]
                assert data["mismatch_categories"] == ["AUDIT_INTEGRITY"]

            assert data["repair_plan"]["precondition"]["algorithm"] == "SHA256"
            assert len(data["repair_plan"]["precondition"]["state_hash"]) == 64
            assert data["repair_plan"]["precondition"]["order_count"] in {1, 2}

            verify = Session()
            try:
                assert verify.query(Order).filter(
                    Order.user_id == user_id,
                    Order.is_paper.is_(True),
                ).count() in {1, 2}
                assert verify.query(Position).filter(
                    Position.user_id == user_id,
                    Position.is_paper.is_(True),
                ).count() == 1
            finally:
                verify.close()
        finally:
            app.dependency_overrides.pop(routes.current_user_id, None)
            app.dependency_overrides.pop(get_db, None)
            engine.dispose()


def test_paper_reconcile_http_concurrent_committed_epochs_invalidate_stale_precondition(tmp_path):
    """Every committed ledger epoch invalidates an older dry-run precondition."""
    from app.core.database import get_db
    from app.execution import paper_routes as routes
    from app.execution.paper_routes import PaperOrderRequest, _paper_audit_payload, paper_order

    engine = create_engine(
        f"sqlite:///{tmp_path / 'http-committed-epochs-precondition.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    seed = TestSession()
    try:
        user = User(
            email="http-committed-epochs@example.com",
            hashed_password="",
            full_name="HTTP Committed Epochs",
            is_active=True,
        )
        seed.add(user)
        seed.flush()
        seed.add(
            TradingAccount(
                user_id=user.id,
                mode="PAPER",
                virtual_balance=1000.0,
                initial_virtual_balance=1000.0,
                initial_balance_source="BOOTSTRAP",
                realized_pnl=0.0,
                is_active=True,
            )
        )
        seed.commit()
        user_id = int(user.id)
    finally:
        seed.close()

    db = TestSession()
    try:
        result = paper_order(
            PaperOrderRequest(
                symbol="EPOCH-BASE",
                transaction_type="BUY",
                price=100.0,
                quantity=2,
                fill_id="EPOCH-BASE-1",
            ),
            user_id=user_id,
            db=db,
        )
        assert result["status"] == "success"
    finally:
        db.close()

    def http_snapshot():
        client = TestClient(app)

        def override_db():
            db = TestSession()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[routes.current_user_id] = lambda: user_id
        try:
            with ThreadPoolExecutor(max_workers=20) as pool:
                responses = list(
                    pool.map(
                        lambda _: client.get("/api/v1/execution/paper/reconcile"),
                        range(20),
                    )
                )
            assert all(response.status_code == 200 for response in responses)
            payloads = [response.json() for response in responses]
            assert all(payload == payloads[0] for payload in payloads)
            return payloads[0]
        finally:
            app.dependency_overrides.pop(routes.current_user_id, None)
            app.dependency_overrides.pop(get_db, None)

    def mutate_account(**changes):
        db = TestSession()
        try:
            account = db.query(TradingAccount).filter(
                TradingAccount.user_id == user_id,
                TradingAccount.mode == "PAPER",
            ).one()
            for key, value in changes.items():
                setattr(account, key, value)
            db.commit()
        finally:
            db.close()

    def mutate_position(quantity):
        db = TestSession()
        try:
            position = db.query(Position).filter(
                Position.user_id == user_id,
                Position.is_paper.is_(True),
                Position.is_open.is_(True),
            ).one()
            position.quantity = quantity
            db.commit()
        finally:
            db.close()

    def restore_position():
        mutate_position(2)

    # Epoch 0: create a real committed accounting mismatch and capture the
    # only precondition that a future repair/apply operation could start from.
    mutate_account(virtual_balance=801.0)
    epoch0 = http_snapshot()
    assert epoch0["status"] == "MISMATCH"
    assert epoch0["repairability"] == "SAFE_DRY_RUN"
    assert epoch0["repairability_reason"] == "account_or_position_state_only"
    assert epoch0["mismatch_categories"] == ["ACCOUNTING_STATE"]
    assert epoch0["mismatches"] == ["virtual_balance_mismatch"]
    pre0 = epoch0["repair_plan"]["precondition"]

    # Epoch 1: committed position mutation. The old precondition is stale,
    # while the current corruption remains safely dry-run repairable.
    mutate_position(1)
    epoch1 = http_snapshot()
    assert epoch1["status"] == "MISMATCH"
    assert epoch1["repairability"] == "SAFE_DRY_RUN"
    assert epoch1["mismatch_categories"] == ["ACCOUNTING_STATE", "POSITION_STATE"]
    assert epoch1["repair_plan"]["precondition"] != pre0
    assert epoch1["repair_plan"]["precondition"]["state_hash"] != pre0["state_hash"]
    assert epoch1["repair_plan"]["precondition"]["order_count"] == pre0["order_count"]
    assert epoch1["repair_plan"]["precondition"]["audit_head"] == pre0["audit_head"]

    restore_position()

    # Epoch 2: another committed account mutation invalidates the previous
    # epoch even though order/position/audit metadata is unchanged.
    mutate_account(virtual_balance=802.0)
    epoch2 = http_snapshot()
    assert epoch2["repairability"] == "SAFE_DRY_RUN"
    pre2 = epoch2["repair_plan"]["precondition"]
    assert pre2 != pre0
    assert pre2["state_hash"] != pre0["state_hash"]
    assert pre2["order_count"] == pre0["order_count"]
    assert pre2["position_count"] == pre0["position_count"]
    assert pre2["audit_head"] == pre0["audit_head"]

    # Restore the original mismatch before introducing a genuine new-order
    # epoch, so the order-set/audit-head change is isolated.
    mutate_account(virtual_balance=801.0)

    db = TestSession()
    try:
        result = paper_order(
            PaperOrderRequest(
                symbol="EPOCH-NEW",
                transaction_type="BUY",
                price=50.0,
                quantity=1,
                fill_id="EPOCH-NEW-1",
            ),
            user_id=user_id,
            db=db,
        )
        assert result["status"] == "success"
    finally:
        db.close()

    epoch3 = http_snapshot()
    assert epoch3["status"] == "MISMATCH"
    assert epoch3["repairability"] == "SAFE_DRY_RUN"
    pre3 = epoch3["repair_plan"]["precondition"]
    assert pre3 != pre0
    assert pre3["state_hash"] != pre0["state_hash"]
    assert pre3["order_count"] == pre0["order_count"] + 1
    assert pre3["audit_head"] != pre0["audit_head"]
    assert pre3["position_count"] >= pre0["position_count"]
    assert epoch3["mismatches"] == ["virtual_balance_mismatch"]

    # Epoch 4: a committed baseline transition converts the same durable
    # mismatch into a hard BLOCKED state. The previous SAFE_DRY_RUN
    # precondition must never be reusable.
    mutate_account(initial_balance_source="MIGRATED_INFERRED")
    epoch4 = http_snapshot()
    assert epoch4["status"] == "MISMATCH"
    assert epoch4["repairability"] == "BLOCKED"
    assert epoch4["repairability_reason"] == "ledger_or_baseline_integrity_failure"
    assert epoch4["baseline_status"] == "MIGRATED_INFERRED"
    assert epoch4["mismatch_categories"] == [
        "ACCOUNTING_STATE",
        "BASELINE_INTEGRITY",
    ]
    pre4 = epoch4["repair_plan"]["precondition"]
    assert pre4 != pre3
    assert pre4["state_hash"] != pre3["state_hash"]
    assert pre4["order_count"] == pre3["order_count"]
    assert pre4["audit_head"] == pre3["audit_head"]
    assert pre4["position_count"] == pre3["position_count"]

    # Restore only through an explicit committed mutation. Reconciliation
    # itself never repairs or rewrites the ledger.
    restore = TestSession()
    try:
        account = restore.query(TradingAccount).filter(
            TradingAccount.user_id == user_id,
            TradingAccount.mode == "PAPER",
        ).one()
        account.initial_balance_source = "BOOTSTRAP"
        account.virtual_balance = 801.0
        restore.commit()
    finally:
        restore.close()

    # The extra order is intentionally retained: the final precondition must
    # reflect the latest committed ledger, never an obsolete epoch snapshot.
    final = http_snapshot()
    assert final["repairability"] == "SAFE_DRY_RUN"
    final_pre = final["repair_plan"]["precondition"]
    assert final_pre["order_count"] == pre3["order_count"]
    assert final_pre["audit_head"] == pre3["audit_head"]
    assert final_pre["state_hash"] != pre0["state_hash"]

    verify = TestSession()
    try:
        account = verify.query(TradingAccount).filter(
            TradingAccount.user_id == user_id,
            TradingAccount.mode == "PAPER",
        ).one()
        assert account.initial_balance_source == "BOOTSTRAP"
        assert float(account.virtual_balance) == 801.0
        assert verify.query(Order).filter(
            Order.user_id == user_id,
            Order.is_paper.is_(True),
        ).count() == 2
        assert verify.query(Position).filter(
            Position.user_id == user_id,
            Position.is_paper.is_(True),
            Position.is_open.is_(True),
        ).count() == 2
    finally:
        verify.close()
        engine.dispose()


def test_paper_reconcile_http_committed_new_order_audit_head_mutation_blocks_stale_precondition(tmp_path):
    """A committed new order followed by audit-head tampering must invalidate and block an old repair precondition."""
    from app.core.database import get_db
    from app.execution import paper_routes as routes
    from app.execution.paper_routes import PaperOrderRequest, _paper_audit_payload, paper_order

    engine = create_engine(
        f"sqlite:///{tmp_path / 'http-new-order-audit-head-precondition.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    seed = TestSession()
    try:
        user = User(
            email="http-new-order-audit-head@example.com",
            hashed_password="",
            full_name="HTTP New Order Audit Head",
            is_active=True,
        )
        seed.add(user)
        seed.flush()
        seed.add(
            TradingAccount(
                user_id=user.id,
                mode="PAPER",
                virtual_balance=1000.0,
                initial_virtual_balance=1000.0,
                initial_balance_source="BOOTSTRAP",
                realized_pnl=0.0,
                is_active=True,
            )
        )
        seed.commit()
        user_id = int(user.id)
    finally:
        seed.close()

    db = TestSession()
    try:
        result = paper_order(
            PaperOrderRequest(
                symbol="AUDIT-EPOCH-BASE",
                transaction_type="BUY",
                price=100.0,
                quantity=2,
                fill_id="AUDIT-EPOCH-BASE-1",
            ),
            user_id=user_id,
            db=db,
        )
        assert result["status"] == "success"
    finally:
        db.close()

    def http_snapshot():
        client = TestClient(app)

        def override_db():
            db = TestSession()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[routes.current_user_id] = lambda: user_id
        try:
            with ThreadPoolExecutor(max_workers=20) as pool:
                responses = list(
                    pool.map(
                        lambda _: client.get("/api/v1/execution/paper/reconcile"),
                        range(20),
                    )
                )
            assert all(response.status_code == 200 for response in responses)
            payloads = [response.json() for response in responses]
            assert all(payload == payloads[0] for payload in payloads)
            return payloads[0]
        finally:
            app.dependency_overrides.pop(routes.current_user_id, None)
            app.dependency_overrides.pop(get_db, None)

    def mutate_account(**changes):
        db = TestSession()
        try:
            account = db.query(TradingAccount).filter(
                TradingAccount.user_id == user_id,
                TradingAccount.mode == "PAPER",
            ).one()
            for key, value in changes.items():
                setattr(account, key, value)
            db.commit()
        finally:
            db.close()

    # Epoch 0: capture a SAFE_DRY_RUN precondition before the committed order.
    mutate_account(virtual_balance=801.0)
    safe0 = http_snapshot()
    assert safe0["status"] == "MISMATCH"
    assert safe0["repairability"] == "SAFE_DRY_RUN"
    assert safe0["mismatch_categories"] == ["ACCOUNTING_STATE"]
    pre0 = safe0["repair_plan"]["precondition"]

    # Epoch 1: commit a valid new order. This changes both the order set and
    # audit head, so the old precondition is immediately stale.
    db = TestSession()
    try:
        result = paper_order(
            PaperOrderRequest(
                symbol="AUDIT-EPOCH-NEW",
                transaction_type="BUY",
                price=50.0,
                quantity=1,
                fill_id="AUDIT-EPOCH-NEW-1",
            ),
            user_id=user_id,
            db=db,
        )
        assert result["status"] == "success"
    finally:
        db.close()

    safe1 = http_snapshot()
    assert safe1["status"] == "MISMATCH"
    assert safe1["repairability"] == "SAFE_DRY_RUN"
    pre1 = safe1["repair_plan"]["precondition"]
    assert pre1 != pre0
    assert pre1["state_hash"] != pre0["state_hash"]
    assert pre1["order_count"] == pre0["order_count"] + 1
    assert pre1["audit_head"] != pre0["audit_head"]
    assert safe1["mismatches"] == ["virtual_balance_mismatch"]

    # Epoch 2: commit only an audit-head corruption. This must hard-block
    # repair, and neither pre0 nor the immediately previous safe pre1 may
    # match the current state.
    tamper = TestSession()
    try:
        latest = tamper.query(Order).filter(
            Order.user_id == user_id,
            Order.is_paper.is_(True),
        ).order_by(Order.id.desc()).one()
        canonical_previous = latest.previous_audit_hash
        canonical_hash = _paper_audit_payload(
            user_id=user_id,
            symbol=latest.symbol,
            side=latest.side,
            quantity=int(latest.quantity),
            price=float(latest.price),
            pnl=float(latest.pnl or 0.0),
            fill_id=latest.fill_id,
            previous_hash=canonical_previous,
        )
        assert latest.audit_hash == canonical_hash
        latest.audit_hash = "f" * 64
        tamper.commit()
    finally:
        tamper.close()

    blocked = http_snapshot()
    assert blocked["status"] == "MISMATCH"
    assert blocked["repairability"] == "BLOCKED"
    assert blocked["repairability_reason"] == "ledger_or_baseline_integrity_failure"
    assert blocked["baseline_status"] == "BOOTSTRAP"
    assert blocked["mismatch_categories"] == ["AUDIT_INTEGRITY"]
    assert blocked["mismatches"] == [f"audit_hash_mismatch:{pre1['order_count']}"]
    assert blocked["repair_plan"]["apply"] is False
    assert blocked["repair_plan"]["reason"] == "read_only_dry_run"

    pre2 = blocked["repair_plan"]["precondition"]
    assert pre2 != pre1
    assert pre2 != pre0
    assert pre2["state_hash"] != pre1["state_hash"]
    assert pre2["audit_head"] == "f" * 64
    assert pre2["order_count"] == pre1["order_count"]

    # Epoch 3: explicitly restore the canonical audit hash. The old pre0 is
    # still stale because the valid new order remains committed; the current
    # state becomes safely dry-run repairable again.
    restore = TestSession()
    try:
        latest = restore.query(Order).filter(
            Order.user_id == user_id,
            Order.is_paper.is_(True),
        ).order_by(Order.id.desc()).one()
        latest.audit_hash = _paper_audit_payload(
            user_id=user_id,
            symbol=latest.symbol,
            side=latest.side,
            quantity=int(latest.quantity),
            price=float(latest.price),
            pnl=float(latest.pnl or 0.0),
            fill_id=latest.fill_id,
            previous_hash=latest.previous_audit_hash,
        )
        restore.commit()
    finally:
        restore.close()

    restored = http_snapshot()
    assert restored["status"] == "MISMATCH"
    assert restored["repairability"] == "SAFE_DRY_RUN"
    assert restored["mismatch_categories"] == ["ACCOUNTING_STATE"]
    restored_pre = restored["repair_plan"]["precondition"]
    assert restored_pre != pre0
    assert restored_pre["order_count"] == pre1["order_count"]
    assert restored_pre["audit_head"] == pre1["audit_head"]
    assert restored_pre["state_hash"] == pre1["state_hash"]

    verify = TestSession()
    try:
        account = verify.query(TradingAccount).filter(
            TradingAccount.user_id == user_id,
            TradingAccount.mode == "PAPER",
        ).one()
        orders = verify.query(Order).filter(
            Order.user_id == user_id,
            Order.is_paper.is_(True),
        ).order_by(Order.id.asc()).all()
        assert account.initial_balance_source == "BOOTSTRAP"
        assert float(account.virtual_balance) == 751.0
        assert len(orders) == 2
        assert all(order.audit_hash for order in orders)
        assert orders[-1].audit_hash == restored_pre["audit_head"]
        assert verify.query(Position).filter(
            Position.user_id == user_id,
            Position.is_paper.is_(True),
            Position.is_open.is_(True),
        ).count() == 2
    finally:
        verify.close()
        engine.dispose()


def test_paper_reconcile_http_multi_user_precondition_isolation_across_committed_epochs(tmp_path):
    """A repair precondition from one paper account must never become valid for another."""
    from app.core.database import get_db
    from app.execution import paper_routes as routes
    from app.execution.paper_routes import PaperOrderRequest, paper_order

    engine = create_engine(
        f"sqlite:///{tmp_path / 'http-multi-user-precondition-isolation.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    seed = TestSession()
    try:
        users = []
        for suffix in ("a", "b"):
            user = User(
                email=f"precondition-isolation-{suffix}@example.com",
                hashed_password="",
                full_name=f"Precondition Isolation {suffix}",
                is_active=True,
            )
            seed.add(user)
            seed.flush()
            seed.add(
                TradingAccount(
                    user_id=user.id,
                    mode="PAPER",
                    virtual_balance=1000.0,
                    initial_virtual_balance=1000.0,
                    initial_balance_source="BOOTSTRAP",
                    realized_pnl=0.0,
                    is_active=True,
                )
            )
            users.append(int(user.id))
        seed.commit()
        user_a, user_b = users
    finally:
        seed.close()

    for user_id, symbol, fill_id in (
        (user_a, "ISOLATE-A", "ISOLATE-A-1"),
        (user_b, "ISOLATE-B", "ISOLATE-B-1"),
    ):
        db = TestSession()
        try:
            result = paper_order(
                PaperOrderRequest(
                    symbol=symbol,
                    transaction_type="BUY",
                    price=100.0,
                    quantity=2,
                    fill_id=fill_id,
                ),
                user_id=user_id,
                db=db,
            )
            assert result["status"] == "success"
        finally:
            db.close()

    def snapshot_for(user_id):
        client = TestClient(app)

        def override_db():
            db = TestSession()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[routes.current_user_id] = lambda: user_id
        try:
            with ThreadPoolExecutor(max_workers=20) as pool:
                responses = list(
                    pool.map(
                        lambda _: client.get("/api/v1/execution/paper/reconcile"),
                        range(20),
                    )
                )
            assert all(response.status_code == 200 for response in responses)
            payloads = [response.json() for response in responses]
            assert all(payload == payloads[0] for payload in payloads)
            return payloads[0]
        finally:
            app.dependency_overrides.pop(routes.current_user_id, None)
            app.dependency_overrides.pop(get_db, None)

    def mutate_user(user_id, **changes):
        db = TestSession()
        try:
            account = db.query(TradingAccount).filter(
                TradingAccount.user_id == user_id,
                TradingAccount.mode == "PAPER",
            ).one()
            for key, value in changes.items():
                setattr(account, key, value)
            db.commit()
        finally:
            db.close()

    # Both users begin with equivalent ledger shape, but their identity is
    # part of the fingerprint and therefore their preconditions must differ.
    mutate_user(user_a, virtual_balance=801.0)
    mutate_user(user_b, virtual_balance=801.0)
    a0 = snapshot_for(user_a)
    b0 = snapshot_for(user_b)

    assert a0["user_id"] == user_a
    assert b0["user_id"] == user_b
    assert a0["status"] == b0["status"] == "MISMATCH"
    assert a0["repairability"] == b0["repairability"] == "SAFE_DRY_RUN"
    assert a0["mismatches"] == b0["mismatches"] == ["virtual_balance_mismatch"]

    pre_a0 = a0["repair_plan"]["precondition"]
    pre_b0 = b0["repair_plan"]["precondition"]
    assert pre_a0["order_count"] == pre_b0["order_count"] == 1
    assert pre_a0["position_count"] == pre_b0["position_count"] == 1
    assert pre_a0["audit_head"] != pre_b0["audit_head"]
    assert pre_a0["state_hash"] != pre_b0["state_hash"]

    # A committed mutation to user A must invalidate only A's precondition;
    # user B's independent precondition remains byte-for-byte stable.
    mutate_user(user_a, virtual_balance=802.0)
    a1 = snapshot_for(user_a)
    b1 = snapshot_for(user_b)

    pre_a1 = a1["repair_plan"]["precondition"]
    pre_b1 = b1["repair_plan"]["precondition"]
    assert pre_a1 != pre_a0
    assert pre_a1["state_hash"] != pre_a0["state_hash"]
    assert pre_b1 == pre_b0
    assert b1 == b0

    # A committed position mutation to A likewise changes only A's
    # fingerprint and repair proposal.
    db = TestSession()
    try:
        position = db.query(Position).filter(
            Position.user_id == user_a,
            Position.is_paper.is_(True),
            Position.is_open.is_(True),
        ).one()
        position.quantity = 1
        db.commit()
    finally:
        db.close()

    a2 = snapshot_for(user_a)
    b2 = snapshot_for(user_b)
    assert a2["repairability"] == "SAFE_DRY_RUN"
    assert a2["mismatch_categories"] == ["ACCOUNTING_STATE", "POSITION_STATE"]
    assert a2["repair_plan"]["precondition"]["state_hash"] != pre_a1["state_hash"]
    assert b2 == b0
    assert b2["repair_plan"]["precondition"] == pre_b0

    # A baseline transition on A must block only A. B must remain independently
    # safe and must not inherit A's blocked precondition.
    mutate_user(user_a, initial_balance_source="MIGRATED_INFERRED")
    a3 = snapshot_for(user_a)
    b3 = snapshot_for(user_b)

    assert a3["repairability"] == "BLOCKED"
    assert a3["baseline_status"] == "MIGRATED_INFERRED"
    assert "BASELINE_INTEGRITY" in a3["mismatch_categories"]
    assert a3["repair_plan"]["precondition"] != pre_a0
    assert b3 == b0
    assert b3["repairability"] == "SAFE_DRY_RUN"
    assert b3["baseline_status"] == "BOOTSTRAP"

    # Final database verification proves no cross-user order/position/account
    # mutation leaked across the isolation boundary.
    verify = TestSession()
    try:
        account_a = verify.query(TradingAccount).filter(
            TradingAccount.user_id == user_a,
            TradingAccount.mode == "PAPER",
        ).one()
        account_b = verify.query(TradingAccount).filter(
            TradingAccount.user_id == user_b,
            TradingAccount.mode == "PAPER",
        ).one()
        assert account_a.initial_balance_source == "MIGRATED_INFERRED"
        assert float(account_a.virtual_balance) == 802.0
        assert account_b.initial_balance_source == "BOOTSTRAP"
        assert float(account_b.virtual_balance) == 801.0

        assert verify.query(Order).filter(
            Order.user_id == user_a,
            Order.is_paper.is_(True),
        ).count() == 1
        assert verify.query(Order).filter(
            Order.user_id == user_b,
            Order.is_paper.is_(True),
        ).count() == 1
        assert verify.query(Position).filter(
            Position.user_id == user_a,
            Position.is_paper.is_(True),
            Position.is_open.is_(True),
        ).one().quantity == 1
        assert verify.query(Position).filter(
            Position.user_id == user_b,
            Position.is_paper.is_(True),
            Position.is_open.is_(True),
        ).one().quantity == 2
    finally:
        verify.close()
        engine.dispose()


def test_paper_order_http_concurrent_duplicate_fill_id_is_idempotent(tmp_path):
    """Concurrent retries of one fill must create exactly one paper order."""
    from app.core.database import get_db
    from app.execution import paper_routes as routes
    from app.execution.paper_routes import PaperOrderRequest, paper_order

    engine = create_engine(
        f"sqlite:///{tmp_path / 'http-duplicate-fill-idempotency.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    seed = TestSession()
    try:
        user = User(
            email="duplicate-fill-http@example.com",
            hashed_password="",
            full_name="Duplicate Fill HTTP",
            is_active=True,
        )
        seed.add(user)
        seed.flush()
        seed.add(
            TradingAccount(
                user_id=user.id,
                mode="PAPER",
                virtual_balance=1000.0,
                initial_virtual_balance=1000.0,
                initial_balance_source="BOOTSTRAP",
                realized_pnl=0.0,
                is_active=True,
            )
        )
        seed.commit()
        user_id = int(user.id)
    finally:
        seed.close()

    client = TestClient(app)

    def override_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[routes.current_user_id] = lambda: user_id
    try:
        def submit(_):
            return client.post(
                "/api/v1/execution/paper/order",
                json={
                    "symbol": "HTTP-IDEMPOTENT",
                    "transaction_type": "BUY",
                    "price": 100.0,
                    "quantity": 2,
                    "fill_id": "HTTP-IDEMPOTENT-1",
                },
            )

        with ThreadPoolExecutor(max_workers=12) as pool:
            responses = list(pool.map(submit, range(12)))

        assert all(response.status_code == 200 for response in responses)
        payloads = [response.json() for response in responses]
        assert sum(payload.get("idempotent") is True for payload in payloads) == 11
        assert sum("idempotent" not in payload for payload in payloads) == 1
        assert all(payload["status"] == "success" for payload in payloads)
    finally:
        app.dependency_overrides.pop(routes.current_user_id, None)
        app.dependency_overrides.pop(get_db, None)

    final_db = TestSession()
    try:
        account = final_db.query(TradingAccount).filter(
            TradingAccount.user_id == user_id,
            TradingAccount.mode == "PAPER",
        ).one()
        orders = final_db.query(Order).filter(
            Order.user_id == user_id,
            Order.is_paper.is_(True),
        ).all()
        positions = final_db.query(Position).filter(
            Position.user_id == user_id,
            Position.is_paper.is_(True),
            Position.is_open.is_(True),
        ).all()

        assert len(orders) == 1
        assert len(positions) == 1
        assert positions[0].quantity == 2
        assert float(account.virtual_balance) == 800.0
        assert float(account.realized_pnl or 0.0) == 0.0
        assert orders[0].fill_id == "HTTP-IDEMPOTENT-1"

        from app.execution.paper_routes import _reconcile_paper_ledger
        reconciliation = _reconcile_paper_ledger(final_db, user_id)
        assert reconciliation["status"] == "OK"
        assert reconciliation["mismatches"] == []
        assert reconciliation["repairability"] == "NONE"
    finally:
        final_db.close()
        engine.dispose()


def test_paper_order_http_concurrent_same_fill_id_conflicting_details_fail_closed(tmp_path):
    """A fill_id collision with different execution details must never execute twice."""
    from app.core.database import get_db
    from app.execution import paper_routes as routes

    engine = create_engine(
        f"sqlite:///{tmp_path / 'http-fill-id-conflict.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    seed = TestSession()
    try:
        user = User(
            email="fill-id-conflict-http@example.com",
            hashed_password="",
            full_name="Fill ID Conflict HTTP",
            is_active=True,
        )
        seed.add(user)
        seed.flush()
        seed.add(
            TradingAccount(
                user_id=user.id,
                mode="PAPER",
                virtual_balance=1000.0,
                initial_virtual_balance=1000.0,
                initial_balance_source="BOOTSTRAP",
                realized_pnl=0.0,
                is_active=True,
            )
        )
        seed.commit()
        user_id = int(user.id)
    finally:
        seed.close()

    client = TestClient(app)

    def override_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[routes.current_user_id] = lambda: user_id
    try:
        def submit(_):
            return client.post(
                "/api/v1/execution/paper/order",
                json={
                    "symbol": "HTTP-FILL-CONFLICT",
                    "transaction_type": "BUY",
                    "price": 100.0 if _ == 0 else 101.0,
                    "quantity": 2,
                    "fill_id": "HTTP-FILL-CONFLICT-1",
                },
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(pool.map(submit, range(8)))

        successful = [response for response in responses if response.status_code == 200]
        conflicts = [response for response in responses if response.status_code == 409]

        assert len(successful) == 1
        assert len(conflicts) == 7
        assert all(
            response.json()["detail"]
            == "fill_id already exists with different execution details"
            for response in conflicts
        )
    finally:
        app.dependency_overrides.pop(routes.current_user_id, None)
        app.dependency_overrides.pop(get_db, None)

    verify = TestSession()
    try:
        account = verify.query(TradingAccount).filter(
            TradingAccount.user_id == user_id,
            TradingAccount.mode == "PAPER",
        ).one()
        orders = verify.query(Order).filter(
            Order.user_id == user_id,
            Order.is_paper.is_(True),
        ).all()
        positions = verify.query(Position).filter(
            Position.user_id == user_id,
            Position.is_paper.is_(True),
            Position.is_open.is_(True),
        ).all()

        assert len(orders) == 1
        assert len(positions) == 1
        assert orders[0].fill_id == "HTTP-FILL-CONFLICT-1"
        assert float(orders[0].price) in {100.0, 101.0}
        assert positions[0].quantity == 2
        assert float(account.virtual_balance) == 800.0
        assert float(account.realized_pnl or 0.0) == 0.0

        from app.execution.paper_routes import _reconcile_paper_ledger
        reconciliation = _reconcile_paper_ledger(verify, user_id)
        assert reconciliation["status"] == "OK"
        assert reconciliation["mismatches"] == []
        assert reconciliation["repairability"] == "NONE"
    finally:
        verify.close()
        engine.dispose()



def test_paper_from_scanner_http_concurrent_duplicate_fill_id_preserves_idempotency_and_source(tmp_path):
    """Scanner retries must reuse the paper-order idempotency boundary without double allocation."""
    from app.core.database import get_db
    from app.execution import paper_routes as routes

    engine = create_engine(
        f"sqlite:///{tmp_path / 'http-scanner-duplicate-fill-id.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    seed = TestSession()
    try:
        user = User(
            email="scanner-duplicate-fill@example.com",
            hashed_password="",
            full_name="Scanner Duplicate Fill",
            is_active=True,
        )
        seed.add(user)
        seed.flush()
        seed.add(
            TradingAccount(
                user_id=user.id,
                mode="PAPER",
                virtual_balance=1000.0,
                initial_virtual_balance=1000.0,
                initial_balance_source="BOOTSTRAP",
                realized_pnl=0.0,
                is_active=True,
            )
        )
        seed.commit()
        user_id = int(user.id)
    finally:
        seed.close()

    client = TestClient(app)

    def override_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[routes.current_user_id] = lambda: user_id
    try:
        def submit(_):
            return client.post(
                "/api/v1/execution/paper/from-scanner",
                json={
                    "symbol": "SCANNER-IDEMPOTENT",
                    "cash_price": 100.0,
                    "quantity": 2,
                    "future_price": 101.0,
                    "gap": 1.0,
                    "net_profit": 1.0,
                    "executable": True,
                    "fill_id": "SCANNER-IDEMPOTENT-1",
                },
            )

        with ThreadPoolExecutor(max_workers=12) as pool:
            responses = list(pool.map(submit, range(12)))

        assert all(response.status_code == 200 for response in responses)
        payloads = [response.json() for response in responses]
        assert sum(payload.get("idempotent") is True for payload in payloads) == 11
        assert sum("idempotent" not in payload for payload in payloads) == 1
        assert all(payload["status"] == "success" for payload in payloads)
        assert all(payload["source"] == "cash-future-scanner" for payload in payloads)
        assert all(payload["scanner_entry_price"] == 100.0 for payload in payloads)
        assert all(payload["scanner_future_price"] == 101.0 for payload in payloads)
        assert all(payload["scanner_gap"] == 1.0 for payload in payloads)
        assert all(payload["scanner_net_profit"] == 1.0 for payload in payloads)
    finally:
        app.dependency_overrides.pop(routes.current_user_id, None)
        app.dependency_overrides.pop(get_db, None)

    verify = TestSession()
    try:
        account = verify.query(TradingAccount).filter(
            TradingAccount.user_id == user_id,
            TradingAccount.mode == "PAPER",
        ).one()
        orders = verify.query(Order).filter(
            Order.user_id == user_id,
            Order.is_paper.is_(True),
        ).all()
        positions = verify.query(Position).filter(
            Position.user_id == user_id,
            Position.is_paper.is_(True),
            Position.is_open.is_(True),
        ).all()

        assert len(orders) == 1
        assert len(positions) == 1
        assert orders[0].fill_id == "SCANNER-IDEMPOTENT-1"
        assert positions[0].quantity == 2
        assert float(account.virtual_balance) == 800.0
        assert float(account.realized_pnl or 0.0) == 0.0

        from app.execution.paper_routes import _reconcile_paper_ledger
        reconciliation = _reconcile_paper_ledger(verify, user_id)
        assert reconciliation["status"] == "OK"
        assert reconciliation["mismatches"] == []
        assert reconciliation["repairability"] == "NONE"
    finally:
        verify.close()
        engine.dispose()



def test_paper_exit_http_concurrent_duplicate_fill_id_closes_once_and_is_idempotent(tmp_path):
    """Concurrent retries of one exit fill must close the position exactly once."""
    from app.core.database import get_db
    from app.execution import paper_routes as routes

    engine = create_engine(
        f"sqlite:///{tmp_path / 'http-exit-duplicate-fill-id.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    seed = TestSession()
    try:
        user = User(email="exit-duplicate-fill@example.com", hashed_password="", full_name="Exit Duplicate Fill", is_active=True)
        seed.add(user)
        seed.flush()
        seed.add(TradingAccount(user_id=user.id, mode="PAPER", virtual_balance=800.0, initial_virtual_balance=1000.0, initial_balance_source="BOOTSTRAP", realized_pnl=0.0, is_active=True))
        seed.add(Position(user_id=user.id, symbol="EXIT-IDEMPOTENT", quantity=2, average_price=100.0, stop_loss=None, target=None, is_paper=True, is_open=True))
        seed.commit()
        user_id = int(user.id)
    finally:
        seed.close()

    client = TestClient(app)
    def override_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[routes.current_user_id] = lambda: user_id
    try:
        def submit(_):
            return client.post("/api/v1/execution/paper/exit", json={"symbol":"EXIT-IDEMPOTENT","price":110.0,"fill_id":"EXIT-IDEMPOTENT-1"})
        with ThreadPoolExecutor(max_workers=12) as pool:
            responses = list(pool.map(submit, range(12)))
        assert all(response.status_code == 200 for response in responses)
        payloads = [response.json() for response in responses]
        assert sum(payload.get("idempotent") is True for payload in payloads) == 11
        assert sum("idempotent" not in payload for payload in payloads) == 1
        assert all(payload["status"] == "closed" for payload in payloads)
        assert all(float(payload["pnl"]) == 20.0 for payload in payloads)
    finally:
        app.dependency_overrides.pop(routes.current_user_id, None)
        app.dependency_overrides.pop(get_db, None)

    verify = TestSession()
    try:
        account = verify.query(TradingAccount).filter(TradingAccount.user_id == user_id, TradingAccount.mode == "PAPER").one()
        orders = verify.query(Order).filter(Order.user_id == user_id, Order.is_paper.is_(True)).all()
        positions = verify.query(Position).filter(Position.user_id == user_id, Position.is_paper.is_(True), Position.is_open.is_(True)).all()
        assert len(orders) == 1
        assert orders[0].fill_id == "EXIT-IDEMPOTENT-1"
        assert orders[0].transaction_type == "SELL"
        assert float(orders[0].pnl) == 20.0
        assert positions == []
        assert float(account.virtual_balance) == 1020.0
        assert float(account.realized_pnl) == 20.0
        from app.execution.paper_routes import _reconcile_paper_ledger
        reconciliation = _reconcile_paper_ledger(verify, user_id)
        assert reconciliation["status"] == "OK"
        assert reconciliation["mismatches"] == []
        assert reconciliation["repairability"] == "NONE"
    finally:
        verify.close()
        engine.dispose()



def test_paper_exit_http_concurrent_same_fill_id_conflicting_details_fail_closed(tmp_path):
    """An exit fill_id reused with different prices must not create a second close."""
    from app.core.database import get_db
    from app.execution import paper_routes as routes

    engine = create_engine(
        f"sqlite:///{tmp_path / 'http-exit-fill-id-conflict.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    seed = TestSession()
    try:
        user = User(email="exit-fill-conflict@example.com", hashed_password="", full_name="Exit Fill Conflict", is_active=True)
        seed.add(user)
        seed.flush()
        seed.add(TradingAccount(user_id=user.id, mode="PAPER", virtual_balance=800.0, initial_virtual_balance=1000.0, initial_balance_source="BOOTSTRAP", realized_pnl=0.0, is_active=True))
        seed.add(Position(user_id=user.id, symbol="EXIT-FILL-CONFLICT", quantity=2, average_price=100.0, stop_loss=None, target=None, is_paper=True, is_open=True))
        seed.commit()
        user_id = int(user.id)
    finally:
        seed.close()

    client = TestClient(app)
    def override_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[routes.current_user_id] = lambda: user_id
    try:
        def submit(_):
            return client.post("/api/v1/execution/paper/exit", json={"symbol":"EXIT-FILL-CONFLICT","price":110.0 if _ == 0 else 111.0,"fill_id":"EXIT-FILL-CONFLICT-1"})
        with ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(pool.map(submit, range(8)))
        successful = [response for response in responses if response.status_code == 200]
        conflicts = [response for response in responses if response.status_code == 409]
        assert len(successful) == 1
        assert len(conflicts) == 7
        assert all(response.json()["detail"] == "fill_id already exists with different execution details" for response in conflicts)
    finally:
        app.dependency_overrides.pop(routes.current_user_id, None)
        app.dependency_overrides.pop(get_db, None)

    verify = TestSession()
    try:
        account = verify.query(TradingAccount).filter(TradingAccount.user_id == user_id, TradingAccount.mode == "PAPER").one()
        orders = verify.query(Order).filter(Order.user_id == user_id, Order.is_paper.is_(True)).all()
        positions = verify.query(Position).filter(Position.user_id == user_id, Position.is_paper.is_(True), Position.is_open.is_(True)).all()
        assert len(orders) == 1
        assert orders[0].fill_id == "EXIT-FILL-CONFLICT-1"
        assert float(orders[0].price) in {110.0, 111.0}
        assert orders[0].transaction_type == "SELL"
        assert float(orders[0].pnl) in {20.0, 22.0}
        assert positions == []
        assert float(account.virtual_balance) in {1020.0, 1022.0}
        assert float(account.realized_pnl) in {20.0, 22.0}
        from app.execution.paper_routes import _reconcile_paper_ledger
        reconciliation = _reconcile_paper_ledger(verify, user_id)
        assert reconciliation["status"] == "OK"
        assert reconciliation["mismatches"] == []
        assert reconciliation["repairability"] == "NONE"
    finally:
        verify.close()
        engine.dispose()


def test_paper_exit_http_concurrent_duplicate_fill_id_closes_short_once(tmp_path):
    """Concurrent short-cover exits must realize P&L and close the short exactly once."""
    from app.core.database import get_db
    from app.execution import paper_routes as routes
    from app.execution.paper_routes import _reconcile_paper_ledger

    engine = create_engine(
        f"sqlite:///{tmp_path / 'http-short-exit-idempotent.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    seed = TestSession()
    try:
        user = User(email="short-exit-idempotent@example.com", hashed_password="", full_name="Short Exit", is_active=True)
        seed.add(user)
        seed.flush()
        seed.add(TradingAccount(user_id=user.id, mode="PAPER", virtual_balance=800.0, initial_virtual_balance=1000.0, initial_balance_source="BOOTSTRAP", realized_pnl=0.0, is_active=True))
        seed.add(Position(user_id=user.id, symbol="SHORT-EXIT-IDEMPOTENT", quantity=-2, average_price=100.0, stop_loss=None, target=None, is_paper=True, is_open=True))
        seed.commit()
        user_id = int(user.id)
    finally:
        seed.close()

    client = TestClient(app)
    def override_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[routes.current_user_id] = lambda: user_id
    try:
        def submit(_):
            return client.post("/api/v1/execution/paper/exit", json={
                "symbol": "SHORT-EXIT-IDEMPOTENT",
                "price": 90.0,
                "fill_id": "SHORT-EXIT-IDEMPOTENT-1",
            })
        with ThreadPoolExecutor(max_workers=12) as pool:
            responses = list(pool.map(submit, range(12)))
        assert all(response.status_code == 200 for response in responses)
        payloads = [response.json() for response in responses]
        assert sum(not payload.get("idempotent", False) for payload in payloads) == 1
        assert sum(payload.get("idempotent", False) for payload in payloads) == 11
        assert all(payload["status"] == "closed" for payload in payloads)
        assert all(payload["pnl"] == 20.0 for payload in payloads)
    finally:
        app.dependency_overrides.pop(routes.current_user_id, None)
        app.dependency_overrides.pop(get_db, None)

    verify = TestSession()
    try:
        account = verify.query(TradingAccount).filter(TradingAccount.user_id == user_id, TradingAccount.mode == "PAPER").one()
        orders = verify.query(Order).filter(Order.user_id == user_id, Order.is_paper.is_(True)).all()
        positions = verify.query(Position).filter(Position.user_id == user_id, Position.is_paper.is_(True), Position.is_open.is_(True)).all()
        assert len(orders) == 1
        assert orders[0].fill_id == "SHORT-EXIT-IDEMPOTENT-1"
        assert orders[0].transaction_type == "BUY"
        assert float(orders[0].price) == 90.0
        assert float(orders[0].quantity) == 2.0
        assert float(orders[0].pnl) == 20.0
        assert positions == []
        assert float(account.virtual_balance) == 1020.0
        assert float(account.realized_pnl) == 20.0
        reconciliation = _reconcile_paper_ledger(verify, user_id)
        assert reconciliation["status"] == "OK"
        assert reconciliation["mismatches"] == []
        assert reconciliation["repairability"] == "NONE"
    finally:
        verify.close()
        engine.dispose()


def test_paper_http_concurrent_reversal_and_exit_close_short_once(tmp_path):
    """HTTP reversal and terminal exit racing for the same short must converge to one close."""
    from app.core.database import get_db
    from app.execution import paper_routes as routes
    from app.execution.paper_routes import _reconcile_paper_ledger

    engine = create_engine(
        f"sqlite:///{tmp_path / 'http-reversal-exit-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    seed = TestSession()
    try:
        user = User(email="http-reversal-exit-race@example.com", hashed_password="", full_name="HTTP Reversal Exit Race", is_active=True)
        seed.add(user)
        seed.flush()
        seed.add(TradingAccount(user_id=user.id, mode="PAPER", virtual_balance=500.0, initial_virtual_balance=1000.0, initial_balance_source="BOOTSTRAP", realized_pnl=0.0, is_active=True))
        seed.add(Position(user_id=user.id, symbol="HTTP-RACE", quantity=-5, average_price=100.0, stop_loss=None, target=None, is_paper=True, is_open=True))
        seed.commit()
        user_id = int(user.id)
    finally:
        seed.close()

    client = TestClient(app)
    def override_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[routes.current_user_id] = lambda: user_id
    barrier = threading.Barrier(2)
    try:
        def reversal():
            barrier.wait(timeout=5)
            return client.post("/api/v1/execution/paper/order", json={"symbol":"HTTP-RACE","transaction_type":"BUY","price":90.0,"quantity":5})
        def terminal_exit():
            barrier.wait(timeout=5)
            return client.post("/api/v1/execution/paper/exit", json={"symbol":"HTTP-RACE","price":90.0})
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda fn: fn(), (reversal, terminal_exit)))
        assert all(response.status_code == 200 for response in responses)
        payloads = [response.json() for response in responses]
        assert sum(payload.get("status") == "success" for payload in payloads) == 1
        assert sum(payload.get("status") == "flat" for payload in payloads) == 1
    finally:
        app.dependency_overrides.pop(routes.current_user_id, None)
        app.dependency_overrides.pop(get_db, None)

    verify = TestSession()
    try:
        account = verify.query(TradingAccount).filter(TradingAccount.user_id == user_id, TradingAccount.mode == "PAPER").one()
        orders = verify.query(Order).filter(Order.user_id == user_id, Order.symbol == "HTTP-RACE", Order.is_paper.is_(True)).all()
        positions = verify.query(Position).filter(Position.user_id == user_id, Position.symbol == "HTTP-RACE", Position.is_paper.is_(True), Position.is_open.is_(True)).all()
        assert len(orders) == 1
        assert orders[0].quantity == 5
        assert orders[0].price == 90.0
        assert orders[0].transaction_type == "BUY"
        assert orders[0].pnl == 50.0
        assert positions == []
        assert account.virtual_balance == 550.0
        assert account.realized_pnl == 50.0
        reconciliation = _reconcile_paper_ledger(verify, user_id)
        assert reconciliation["status"] == "OK"
        assert reconciliation["mismatches"] == []
        assert reconciliation["repairability"] == "NONE"
    finally:
        verify.close()
        engine.dispose()


def test_paper_http_concurrent_multi_symbol_partial_reversals_preserve_accounting():
    client, headers = _client_and_headers()
    starting_balance = 10_000.0

    alpha_open = client.post(
        "/api/v1/execution/paper/order",
        headers=headers,
        json={"symbol": "ALPHA", "transaction_type": "BUY", "price": 100.0, "quantity": 10},
    )
    beta_open = client.post(
        "/api/v1/execution/paper/order",
        headers=headers,
        json={"symbol": "BETA", "transaction_type": "SELL", "price": 200.0, "quantity": 6},
    )
    assert alpha_open.status_code == 200
    assert beta_open.status_code == 200
    assert beta_open.json()["virtual_balance"] == 7_800.0

    barrier = threading.Barrier(2)

    def reverse_alpha():
        barrier.wait(timeout=5)
        return client.post(
            "/api/v1/execution/paper/order",
            headers=headers,
            json={
                "symbol": "ALPHA",
                "transaction_type": "SELL",
                "price": 120.0,
                "quantity": 14,
            },
        )

    def reverse_beta():
        barrier.wait(timeout=5)
        return client.post(
            "/api/v1/execution/paper/order",
            headers=headers,
            json={
                "symbol": "BETA",
                "transaction_type": "BUY",
                "price": 180.0,
                "quantity": 10,
            },
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        alpha_future = pool.submit(reverse_alpha)
        beta_future = pool.submit(reverse_beta)
        alpha = alpha_future.result()
        beta = beta_future.result()

    assert alpha.status_code == 200
    assert beta.status_code == 200
    alpha_data = alpha.json()
    beta_data = beta.json()

    assert alpha_data["realized_pnl"] == 200.0
    assert alpha_data["position"]["symbol"] == "ALPHA"
    assert alpha_data["position"]["quantity"] == -4.0
    assert alpha_data["position"]["entry_price"] == 120.0

    assert beta_data["realized_pnl"] == 320.0
    assert beta_data["position"]["symbol"] == "BETA"
    assert beta_data["position"]["quantity"] == 4.0
    assert beta_data["position"]["entry_price"] == 180.0
    assert beta_data["virtual_balance"] == starting_balance - 880.0

    positions = client.get("/api/v1/execution/paper/positions", headers=headers)
    assert positions.status_code == 200
    rows = sorted(
        [
            (item["symbol"], item["quantity"], item["entry_price"])
            for item in positions.json()["positions"]
            if item["quantity"] != 0
        ]
    )
    assert rows == [
        ("ALPHA", -4.0, 120.0),
        ("BETA", 4.0, 180.0),
    ]

    orders = client.get("/api/v1/execution/paper/orders", headers=headers)
    assert orders.status_code == 200
    assert len(orders.json()["orders"]) == 4


def test_paper_http_concurrent_insufficient_balance_fails_closed_across_symbols():
    client, headers = _client_and_headers()
    db = SessionLocal()
    try:
        account = db.query(TradingAccount).filter(TradingAccount.mode == "PAPER").one()
        account.virtual_balance = 1_000.0
        account.realized_pnl = 0.0
        db.commit()
    finally:
        db.close()

    barrier = threading.Barrier(2)

    def submit(symbol):
        barrier.wait(timeout=5)
        return client.post(
            "/api/v1/execution/paper/order",
            headers=headers,
            json={
                "symbol": symbol,
                "transaction_type": "BUY",
                "price": 700.0,
                "quantity": 1,
            },
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        alpha_future = pool.submit(submit, "BALANCE_A")
        beta_future = pool.submit(submit, "BALANCE_B")
        alpha = alpha_future.result()
        beta = beta_future.result()

    responses = [alpha, beta]
    assert sum(response.status_code == 200 for response in responses) == 1
    rejected = [response for response in responses if response.status_code != 200]
    assert len(rejected) == 1
    assert rejected[0].status_code == 400
    assert rejected[0].json()["detail"] == "Insufficient paper balance"

    positions = client.get("/api/v1/execution/paper/positions", headers=headers)
    assert positions.status_code == 200
    open_positions = [
        item for item in positions.json()["positions"] if item["quantity"] != 0
    ]
    assert len(open_positions) == 1
    assert open_positions[0]["quantity"] == 1.0
    assert open_positions[0]["entry_price"] == 700.0

    orders = client.get("/api/v1/execution/paper/orders", headers=headers)
    assert orders.status_code == 200
    assert len(orders.json()["orders"]) == 1

    account_response = client.get(
        "/api/v1/execution/paper/account",
        headers=headers,
    )
    assert account_response.status_code == 200
    account_data = account_response.json()
    assert account_data["virtual_balance"] == 300.0
    assert account_data["realized_pnl"] == 0.0


def test_paper_http_concurrent_exits_across_symbols_preserve_shared_accounting():
    client, headers = _client_and_headers()
    starting_balance = 10_000.0

    alpha_open = client.post(
        "/api/v1/execution/paper/order",
        headers=headers,
        json={"symbol": "EXIT_A", "transaction_type": "BUY", "price": 100.0, "quantity": 5},
    )
    beta_open = client.post(
        "/api/v1/execution/paper/order",
        headers=headers,
        json={"symbol": "EXIT_B", "transaction_type": "SELL", "price": 200.0, "quantity": 4},
    )
    assert alpha_open.status_code == 200
    assert beta_open.status_code == 200
    assert beta_open.json()["virtual_balance"] == 8_700.0

    barrier = threading.Barrier(2)

    def exit_alpha():
        barrier.wait(timeout=5)
        return client.post(
            "/api/v1/execution/paper/exit",
            headers=headers,
            json={"symbol": "EXIT_A", "price": 130.0},
        )

    def exit_beta():
        barrier.wait(timeout=5)
        return client.post(
            "/api/v1/execution/paper/exit",
            headers=headers,
            json={"symbol": "EXIT_B", "price": 170.0},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        alpha_future = pool.submit(exit_alpha)
        beta_future = pool.submit(exit_beta)
        alpha = alpha_future.result()
        beta = beta_future.result()

    assert alpha.status_code == 200
    assert beta.status_code == 200
    alpha_data = alpha.json()
    beta_data = beta.json()

    assert alpha_data["status"] == "closed"
    assert alpha_data["pnl"] == 150.0
    assert beta_data["status"] == "closed"
    assert beta_data["pnl"] == 120.0
    assert beta_data["realized_pnl"] == 270.0
    assert beta_data["virtual_balance"] == starting_balance + 270.0

    positions = client.get("/api/v1/execution/paper/positions", headers=headers)
    assert positions.status_code == 200
    assert [
        item for item in positions.json()["positions"] if item["quantity"] != 0
    ] == []

    orders = client.get("/api/v1/execution/paper/orders", headers=headers)
    assert orders.status_code == 200
    assert len(orders.json()["orders"]) == 4


def test_paper_http_concurrent_multi_symbol_close_and_partial_reversal_preserve_accounting():
    client, headers = _client_and_headers()

    seed = SessionLocal()
    try:
        accounts = seed.query(TradingAccount).order_by(TradingAccount.id.asc()).all()
        assert accounts
        account = accounts[0]
        for other in accounts:
            other.is_active = other.id == account.id
        account.virtual_balance = 8_000.0
        account.realized_pnl = 0.0
        seed.add_all([
            Position(user_id=account.user_id, symbol="HTTP_ALPHA", quantity=10, average_price=100.0),
            Position(user_id=account.user_id, symbol="HTTP_BETA", quantity=-5, average_price=200.0),
        ])
        seed.commit()
    finally:
        seed.close()

    barrier = threading.Barrier(2)

    def close_alpha():
        barrier.wait(timeout=5)
        return client.post(
            "/api/v1/execution/paper/exit",
            headers=headers,
            json={"symbol": "HTTP_ALPHA", "price": 120.0},
        )

    def reverse_beta():
        barrier.wait(timeout=5)
        return client.post(
            "/api/v1/execution/paper/order",
            headers=headers,
            json={
                "symbol": "HTTP_BETA",
                "transaction_type": "BUY",
                "price": 180.0,
                "quantity": 8,
            },
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        alpha = pool.submit(close_alpha).result()
        beta = pool.submit(reverse_beta).result()

    assert alpha.status_code == 200
    assert beta.status_code == 200
    assert alpha.json()["status"] == "closed"
    assert beta.json()["status"] == "success"

    verify = SessionLocal()
    try:
        account = verify.query(TradingAccount).filter(
            TradingAccount.id == account.id
        ).one()
        positions = verify.query(Position).filter(
            Position.user_id == account.user_id,
            Position.quantity != 0,
        ).order_by(Position.symbol.asc()).all()
        orders = verify.query(Order).filter(
            Order.user_id == account.user_id
        ).order_by(Order.id.asc()).all()

        assert account.virtual_balance == 9_760.0
        assert account.realized_pnl == 300.0
        assert [(p.symbol, p.quantity, p.average_price) for p in positions] == [
            ("HTTP_BETA", 3, 180.0),
        ]
        assert len(orders) == 2
    finally:
        verify.close()
