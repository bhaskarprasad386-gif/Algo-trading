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
    assert data["position"]["quantity"] == 2.0
    assert data["position"]["entry_price"] == 100.0

    # Final long close at 90: receive 180 and realize -20.
    closed = client.post(
        "/api/v1/execution/paper/exit",
        headers=headers,
        json={"symbol": "CHAIN", "price": 90.0},
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
            second = pool.submit(reverse_beta)
            results = [first.result(), second.result()]

        successful = [result for result in results if result[0] in {"alpha", "beta"}]
        assert len(successful) == 2

        verify = Session()
        try:
            account = verify.query(TradingAccount).filter(
                TradingAccount.user_id == user_id
            ).one()
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
        json={"symbol": "LIFECYCLE", "transaction_type": "BUY", "price": 100.0, "quantity": 10},
    )
    assert opened.status_code == 200

    partial = client.post(
        "/api/v1/execution/paper/order",
        headers=headers,
        json={"symbol": "LIFECYCLE", "transaction_type": "SELL", "price": 120.0, "quantity": 4},
    )
    assert partial.status_code == 200

    closed = client.post(
        "/api/v1/execution/paper/exit",
        headers=headers,
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
        {"symbol": "HASH", "transaction_type": "BUY", "price": 100.0, "quantity": 5},
        {"symbol": "HASH", "transaction_type": "SELL", "price": 120.0, "quantity": 2},
    ]:
        response = client.post("/api/v1/execution/paper/order", headers=headers, json=payload)
        assert response.status_code == 200

    db = SessionLocal()
    try:
        orders = db.query(Order).filter(Order.symbol == "HASH").order_by(Order.id.asc()).all()
        assert len(orders) == 2
        assert all(len(order.audit_hash) == 64 for order in orders)
        assert orders[0].previous_audit_hash is None
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
        seed.commit()
    finally:
        seed.close()

    db = Session()
    try:
        result = paper_order(
            PaperOrderRequest(symbol="NEW", transaction_type="BUY", price=100.0, quantity=1, fill_id="NEW-1"),
            user_id=user_id, db=db,
        )
        assert result["order"]["fill_id"] == "NEW-1"
    finally:
        db.close()

    verify = Session()
    try:
        data = _reconcile_paper_ledger(verify, user_id)
        assert data["status"] == "MISMATCH"
        assert data["baseline_status"] == "LEGACY_UNFINGERPRINTED"
        assert data["repairability"] == "BLOCKED"
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
            )
        finally:
            second_db.close()

        tamper_db = Session()
        try:
            orders = (
                tamper_db.query(Order)
                .filter(Order.user_id == user_id, Order.is_paper.is_(True))
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
                second.pnl = 41.0
            elif name == "fill_id":
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
        )
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
        assert result["status"] == "MISMATCH"
        assert result["baseline_status"] == "MIGRATED_INFERRED"
        assert result["repairability"] == "BLOCKED"
        assert result["repair_plan"]["apply"] is False
        assert result["repair_plan"]["reason"] == "read_only_dry_run"
        assert result["repair_plan"]["proposed_virtual_balance"] == 800.0
        assert result["repair_plan"]["positions"]["BLOCK"] == {
            "quantity": 2,
            "average_price": 100.0,
        }
    finally:
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
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    seed = Session()
    try:
        user = User(email="flat-plan@example.com", hashed_password="", full_name="Flat Plan", is_active=True)
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


def test_paper_reconcile_http_is_strictly_read_only_for_user_state():
    from app.execution import paper_routes as routes

    db = SessionLocal()
    try:
        accounts = (
            db.query(TradingAccount)
            .filter(TradingAccount.is_active.is_(True), TradingAccount.mode == "PAPER")
            .order_by(TradingAccount.id.asc())
            .all()
        )
        if not accounts:
            pytest.skip("requires an active paper account")
        user_id = int(accounts[0].user_id)

        def snapshot():
            account = db.query(TradingAccount).filter(TradingAccount.user_id == user_id).one()
            orders = (
                db.query(Order)
                .filter(Order.user_id == user_id, Order.is_paper.is_(True))
                .order_by(Order.id.asc())
                .all()
            )
            positions = (
                db.query(Position)
                .filter(Position.user_id == user_id, Position.is_paper.is_(True))
                .order_by(Position.id.asc())
                .all()
            )
            def row(obj):
                return tuple((column.name, getattr(obj, column.name)) for column in obj.__table__.columns)
            return {
                "account": row(account),
                "orders": [row(order) for order in orders],
                "positions": [row(position) for position in positions],
            }

        before = snapshot()
        client = TestClient(app)
        app.dependency_overrides[routes.current_user_id] = lambda: user_id
        try:
            response = client.get("/api/v1/execution/paper/reconcile")
        finally:
            app.dependency_overrides.pop(routes.current_user_id, None)
        assert response.status_code == 200
        payload = response.json()
        assert payload["user_id"] == user_id
        assert payload["repair_plan"]["apply"] is False

        db.expire_all()
        after = snapshot()
        assert before == after

        # The read-only response itself must not manufacture a repair mutation.
        assert payload["repair_plan"]["reason"] == "read_only_dry_run"
    finally:
        db.close()


def test_paper_reconcile_direct_read_only_preserves_state_hash_and_audit_head():
    from app.execution.paper_routes import _reconcile_paper_ledger

    db = SessionLocal()
    try:
        accounts = (
            db.query(TradingAccount)
            .filter(TradingAccount.is_active.is_(True), TradingAccount.mode == "PAPER")
            .order_by(TradingAccount.id.asc())
            .all()
        )
        if not accounts:
            pytest.skip("requires an active paper account")
        user_id = int(accounts[0].user_id)

        first = _reconcile_paper_ledger(db, user_id)
        db.expire_all()
        second = _reconcile_paper_ledger(db, user_id)

        assert first["repair_plan"]["apply"] is False
        assert second["repair_plan"]["apply"] is False
        assert first["repair_plan"]["precondition"] == second["repair_plan"]["precondition"]
        assert first["repair_plan"]["precondition"]["state_hash"] == second["repair_plan"]["precondition"]["state_hash"]
        assert first["repair_plan"]["precondition"]["audit_head"] == second["repair_plan"]["precondition"]["audit_head"]
        assert first == second
    finally:
        db.close()

def test_paper_reconcile_authorization_fails_closed_when_multiple_active_users_exist(tmp_path):
    from app.execution.paper_routes import current_user_id, _reconcile_paper_ledger

    engine = create_engine(
        f"sqlite:///{tmp_path / 'reconcile-auth-boundary.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        users = [
            User(email="auth-a@example.com", hashed_password="", full_name="Auth A", is_active=True),
            User(email="auth-b@example.com", hashed_password="", full_name="Auth B", is_active=True),
        ]
        db.add_all(users); db.flush()
        db.add_all([
            TradingAccount(user_id=users[0].id, mode="PAPER", virtual_balance=1000.0,
                           initial_virtual_balance=1000.0, initial_balance_source="BOOTSTRAP", is_active=True),
            TradingAccount(user_id=users[1].id, mode="PAPER", virtual_balance=2000.0,
                           initial_virtual_balance=2000.0, initial_balance_source="BOOTSTRAP", is_active=True),
        ])
        db.commit()
        with pytest.raises(HTTPException) as exc:
            current_user_id(db)
        assert exc.value.status_code == 409
        assert "authenticated user context" in str(exc.value.detail)

        # Reconciliation itself remains explicitly scoped: there is no fallback
        # to the first active account when identity is ambiguous.
        with pytest.raises(HTTPException):
            current_user_id(db)
    finally:
        db.close()
        engine.dispose()


def test_paper_reconcile_route_has_no_client_selectable_user_id(tmp_path):
    from app.execution.paper_routes import paper_reconcile

    signature = inspect.signature(paper_reconcile)
    assert "user_id" in signature.parameters
    parameter = signature.parameters["user_id"]
    assert parameter.default is not inspect.Parameter.empty
    assert "Depends" in repr(parameter.default)

    # The only route-level identity source is the dependency; callers cannot
    # provide a second user selector that competes with current_user_id.
    assert list(signature.parameters).count("user_id") == 1

def test_paper_reconcile_is_user_isolated_across_orders_positions_and_preconditions(tmp_path):
    from app.execution.paper_routes import PaperOrderRequest, _reconcile_paper_ledger, paper_order

    engine = create_engine(
        f"sqlite:///{tmp_path / 'reconcile-user-isolation.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    seed = Session()
    try:
        user_a = User(email="reconcile-a@example.com", hashed_password="", full_name="Reconcile A", is_active=True)
        user_b = User(email="reconcile-b@example.com", hashed_password="", full_name="Reconcile B", is_active=True)
        seed.add_all([user_a, user_b]); seed.flush()
        seed.add_all([
            TradingAccount(user_id=user_a.id, mode="PAPER", virtual_balance=10000.0, initial_virtual_balance=10000.0, initial_balance_source="BOOTSTRAP", realized_pnl=0.0, is_active=True),
            TradingAccount(user_id=user_b.id, mode="PAPER", virtual_balance=20000.0, initial_virtual_balance=20000.0, initial_balance_source="BOOTSTRAP", realized_pnl=0.0, is_active=True),
        ])
        seed.commit(); a_id, b_id = user_a.id, user_b.id
    finally:
        seed.close()

    for user_id, symbol, price, qty, fill_id in (
        (a_id, "ALPHA", 100.0, 5, "UA-1"),
        (b_id, "BETA", 500.0, 3, "UB-1"),
    ):
        db = Session()
        try:
            paper_order(PaperOrderRequest(symbol=symbol, transaction_type="BUY", price=price, quantity=qty, fill_id=fill_id), user_id=user_id, db=db)
        finally:
            db.close()

    db = Session()
    try:
        a = _reconcile_paper_ledger(db, a_id)
        b = _reconcile_paper_ledger(db, b_id)
        assert a["status"] == b["status"] == "OK"
        assert a["user_id"] == a_id and b["user_id"] == b_id
        assert a["orders"] == b["orders"] == 1
        assert a["reconstructed_virtual_balance"] == 9500.0
        assert b["reconstructed_virtual_balance"] == 18500.0
        assert a["reconstructed_positions"] == {"ALPHA": {"quantity": 5, "average_price": 100.0}}
        assert b["reconstructed_positions"] == {"BETA": {"quantity": 3, "average_price": 500.0}}
        assert a["repair_plan"]["positions"] == a["reconstructed_positions"]
        assert b["repair_plan"]["positions"] == b["reconstructed_positions"]
        assert a["repair_plan"]["precondition"]["audit_head"] != b["repair_plan"]["precondition"]["audit_head"]
        assert a["repair_plan"]["precondition"]["state_hash"] != b["repair_plan"]["precondition"]["state_hash"]
        assert "BETA" not in a["reconstructed_positions"]
        assert "ALPHA" not in b["reconstructed_positions"]
    finally:
        db.close(); engine.dispose()


def test_paper_reconcile_cross_user_tamper_does_not_change_other_users_result(tmp_path):
    from app.execution.paper_routes import PaperOrderRequest, _reconcile_paper_ledger, paper_order

    engine = create_engine(
        f"sqlite:///{tmp_path / 'reconcile-cross-user-tamper.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine); Session = sessionmaker(bind=engine)
    seed = Session()
    try:
        user_a = User(email="cross-a@example.com", hashed_password="", full_name="Cross A", is_active=True)
        user_b = User(email="cross-b@example.com", hashed_password="", full_name="Cross B", is_active=True)
        seed.add_all([user_a, user_b]); seed.flush()
        seed.add_all([
            TradingAccount(user_id=user_a.id, mode="PAPER", virtual_balance=10000.0, initial_virtual_balance=10000.0, initial_balance_source="BOOTSTRAP", realized_pnl=0.0, is_active=True),
            TradingAccount(user_id=user_b.id, mode="PAPER", virtual_balance=10000.0, initial_virtual_balance=10000.0, initial_balance_source="BOOTSTRAP", realized_pnl=0.0, is_active=True),
        ])
        seed.commit(); a_id, b_id = user_a.id, user_b.id
    finally:
        seed.close()

    for user_id, symbol, fill_id in ((a_id, "ALPHA", "CU-A"), (b_id, "BETA", "CU-B")):
        db = Session()
        try:
            paper_order(PaperOrderRequest(symbol=symbol, transaction_type="BUY", price=100.0, quantity=2, fill_id=fill_id), user_id=user_id, db=db)
        finally:
            db.close()

    db = Session()
    try:
        before_b = _reconcile_paper_ledger(db, b_id)
        order_a = db.query(Order).filter(Order.user_id == a_id, Order.is_paper.is_(True)).one()
        order_a.price = 999.0; db.commit()
        after_b = _reconcile_paper_ledger(db, b_id)
        after_a = _reconcile_paper_ledger(db, a_id)
        assert before_b == after_b
        assert after_b["status"] == "OK"
        assert after_b["reconstructed_positions"] == {"BETA": {"quantity": 2, "average_price": 100.0}}
        assert after_b["repair_plan"]["positions"] == after_b["reconstructed_positions"]
        assert after_a["status"] == "MISMATCH"
        assert after_a["repairability"] == "BLOCKED"
        assert "ALPHA" in after_a["reconstructed_positions"]
        assert "BETA" not in after_a["reconstructed_positions"]
    finally:
        db.close(); engine.dispose()

def test_paper_reconcile_reconstructed_positions_equal_repair_plan_for_reversal_and_flat_symbols(tmp_path):
    from app.execution.paper_routes import (
        PaperExitRequest, PaperOrderRequest, _reconcile_paper_ledger, paper_exit, paper_order
    )

    engine = create_engine(
        f"sqlite:///{tmp_path / 'reconstructed-positions-contract.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    seed = Session()
    try:
        user = User(email="reconstructed-contract@example.com", hashed_password="", full_name="Reconstructed Contract", is_active=True)
        seed.add(user); seed.flush()
        seed.add(TradingAccount(
            user_id=user.id, mode="PAPER", virtual_balance=10000.0,
            initial_virtual_balance=10000.0, initial_balance_source="BOOTSTRAP",
            realized_pnl=0.0, is_active=True,
        ))
        seed.commit(); user_id = user.id
    finally:
        seed.close()

    requests = (
        PaperOrderRequest(symbol="ALPHA", transaction_type="BUY", price=100.0, quantity=10, fill_id="RC-A1"),
        PaperOrderRequest(symbol="ALPHA", transaction_type="SELL", price=120.0, quantity=15, fill_id="RC-A2"),
        PaperOrderRequest(symbol="ALPHA", transaction_type="BUY", price=110.0, quantity=2, fill_id="RC-A3"),
        PaperOrderRequest(symbol="BETA", transaction_type="BUY", price=50.0, quantity=4, fill_id="RC-B1"),
        PaperOrderRequest(symbol="BETA", transaction_type="SELL", price=60.0, quantity=4, fill_id="RC-B2"),
        PaperOrderRequest(symbol="FLAT", transaction_type="BUY", price=25.0, quantity=2, fill_id="RC-F1"),
    )
    for req in requests:
        db = Session()
        try:
            paper_order(req, user_id=user_id, db=db)
        finally:
            db.close()

    db = Session()
    try:
        paper_exit(PaperExitRequest(symbol="FLAT", price=30.0, quantity=2, fill_id="RC-F2"), user_id=user_id, db=db)
    finally:
        db.close()

    db = Session()
    try:
        result = _reconcile_paper_ledger(db, user_id)
        assert result["status"] == "OK"
        assert result["repairability"] == "NONE"
        assert result["reconstructed_positions"] == {
            "ALPHA": {"quantity": -3, "average_price": 110.0},
        }
        assert result["repair_plan"]["positions"] == result["reconstructed_positions"]
        assert "BETA" not in result["reconstructed_positions"]
        assert "FLAT" not in result["reconstructed_positions"]
        assert result["repair_plan"]["apply"] is False
    finally:
        db.close()
        engine.dispose()


def test_paper_reconcile_corrupt_account_preserves_exact_reconstructed_position_contract(tmp_path):
    from app.execution.paper_routes import PaperOrderRequest, _reconcile_paper_ledger, paper_order

    engine = create_engine(
        f"sqlite:///{tmp_path / 'reconstructed-positions-safe-dry-run.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    seed = Session()
    try:
        user = User(email="reconstructed-safe@example.com", hashed_password="", full_name="Reconstructed Safe", is_active=True)
        seed.add(user); seed.flush()
        seed.add(TradingAccount(
            user_id=user.id, mode="PAPER", virtual_balance=5000.0,
            initial_virtual_balance=5000.0, initial_balance_source="BOOTSTRAP",
            realized_pnl=0.0, is_active=True,
        ))
        seed.commit(); user_id = user.id
    finally:
        seed.close()

    for req in (
        PaperOrderRequest(symbol="ONE", transaction_type="BUY", price=100.0, quantity=5, fill_id="RCS-1"),
        PaperOrderRequest(symbol="TWO", transaction_type="BUY", price=200.0, quantity=2, fill_id="RCS-2"),
    ):
        db = Session()
        try:
            paper_order(req, user_id=user_id, db=db)
        finally:
            db.close()

    db = Session()
    try:
        db.query(TradingAccount).filter(TradingAccount.user_id == user_id).one().virtual_balance += 7.0
        db.commit()
        result = _reconcile_paper_ledger(db, user_id)
        assert result["status"] == "MISMATCH"
        assert result["repairability"] == "SAFE_DRY_RUN"
        assert result["reconstructed_positions"] == {
            "ONE": {"quantity": 5, "average_price": 100.0},
            "TWO": {"quantity": 2, "average_price": 200.0},
        }
        assert result["repair_plan"]["positions"] == result["reconstructed_positions"]
        assert result["repair_plan"]["apply"] is False
    finally:
        db.close()
        engine.dispose()

def test_paper_reconcile_tampered_pnl_blocks_repair_and_never_proposes_apply(tmp_path):
    from app.execution.paper_routes import PaperOrderRequest, _reconcile_paper_ledger, paper_order

    engine = create_engine(
        f"sqlite:///{tmp_path / 'audit-pnl-blocked.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine); Session = sessionmaker(bind=engine)
    seed = Session()
    try:
        user = User(email="audit-pnl@example.com", hashed_password="", full_name="Audit PNL", is_active=True)
        seed.add(user); seed.flush()
        seed.add(TradingAccount(user_id=user.id, mode="PAPER", virtual_balance=5000.0,
                                initial_virtual_balance=5000.0, initial_balance_source="BOOTSTRAP",
                                realized_pnl=0.0, is_active=True))
        seed.commit(); user_id = user.id
    finally: seed.close()

    db = Session()
    try: paper_order(PaperOrderRequest(symbol="A", transaction_type="BUY", price=100.0, quantity=5, fill_id="AP1"), user_id=user_id, db=db)
    finally: db.close()

    corrupt = Session()
    try:
        order = corrupt.query(Order).filter(Order.user_id == user_id, Order.is_paper.is_(True)).one()
        order.pnl = 99.0
        corrupt.commit()
        result = _reconcile_paper_ledger(corrupt, user_id)
        assert result["status"] == "MISMATCH"
        assert result["repairability"] == "BLOCKED"
        assert result["repair_plan"]["apply"] is False
        assert any("audit" in str(item).lower() or "pnl" in str(item).lower() for item in result["mismatches"])
    finally:
        corrupt.close(); engine.dispose()


def test_paper_reconcile_duplicate_fill_and_broken_previous_hash_are_blocked(tmp_path):
    from app.execution.paper_routes import PaperOrderRequest, _reconcile_paper_ledger, paper_order

    for mutation in ("duplicate_fill", "previous_hash"):
        engine = create_engine(
            f"sqlite:///{tmp_path / f'audit-{mutation}.db'}",
            connect_args={"check_same_thread": False, "timeout": 10},
        )
        Base.metadata.create_all(engine); Session = sessionmaker(bind=engine)
        seed = Session()
        try:
            user = User(email=f"audit-{mutation}@example.com", hashed_password="", full_name="Audit Mutation", is_active=True)
            seed.add(user); seed.flush()
            seed.add(TradingAccount(user_id=user.id, mode="PAPER", virtual_balance=5000.0,
                                    initial_virtual_balance=5000.0, initial_balance_source="BOOTSTRAP",
                                    realized_pnl=0.0, is_active=True))
            seed.commit(); user_id = user.id
        finally: seed.close()

        db = Session()
        try:
            for req in (
                PaperOrderRequest(symbol="A", transaction_type="BUY", price=100.0, quantity=2, fill_id="D1"),
                PaperOrderRequest(symbol="B", transaction_type="BUY", price=50.0, quantity=2, fill_id="D2"),
            ):
                paper_order(req, user_id=user_id, db=db)
        finally: db.close()

        corrupt = Session()
        try:
            orders = corrupt.query(Order).filter(Order.user_id == user_id, Order.is_paper.is_(True)).order_by(Order.id.asc()).all()
            if mutation == "duplicate_fill":
                orders[1].fill_id = orders[0].fill_id
            else:
                orders[1].previous_audit_hash = "f" * 64
            corrupt.commit()
            result = _reconcile_paper_ledger(corrupt, user_id)
            assert result["status"] == "MISMATCH"
            assert result["repairability"] == "BLOCKED"
            assert result["repair_plan"]["apply"] is False
        finally:
            corrupt.close(); engine.dispose()

def test_paper_reconcile_full_corruption_contract_never_allows_safe_repair(tmp_path):
    from app.execution.paper_routes import PaperOrderRequest, _reconcile_paper_ledger, paper_order

    mutations = ("price", "quantity", "pnl", "fill_id", "previous_hash", "audit_hash",
                 "delete", "insert", "sequence")
    for mutation in mutations:
        engine = create_engine(
            f"sqlite:///{tmp_path / f'full-{mutation}.db'}",
            connect_args={"check_same_thread": False, "timeout": 10},
        )
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        seed = Session()
        try:
            user = User(email=f"full-{mutation}@example.com", hashed_password="", full_name="Full Matrix", is_active=True)
            seed.add(user); seed.flush()
            seed.add(TradingAccount(user_id=user.id, mode="PAPER", virtual_balance=5000.0,
                                    initial_virtual_balance=5000.0, initial_balance_source="BOOTSTRAP",
                                    realized_pnl=0.0, is_active=True))
            seed.commit(); user_id = user.id
        finally:
            seed.close()

        db = Session()
        try:
            paper_order(PaperOrderRequest(symbol="A", transaction_type="BUY", price=100.0, quantity=2, fill_id="M1"), user_id=user_id, db=db)
            paper_order(PaperOrderRequest(symbol="B", transaction_type="BUY", price=50.0, quantity=2, fill_id="M2"), user_id=user_id, db=db)
        finally:
            db.close()

        corrupt = Session()
        try:
            orders = corrupt.query(Order).filter(Order.user_id == user_id, Order.is_paper.is_(True)).order_by(Order.id.asc()).all()
            if mutation == "price":
                orders[0].price = 101.0
            elif mutation == "quantity":
                orders[0].quantity = 3
            elif mutation == "pnl":
                orders[0].pnl = 7.0
            elif mutation == "fill_id":
                orders[1].fill_id = orders[0].fill_id
            elif mutation == "previous_hash":
                orders[1].previous_audit_hash = "a" * 64
            elif mutation == "audit_hash":
                orders[0].audit_hash = "b" * 64
            elif mutation == "delete":
                corrupt.delete(orders[0])
            elif mutation == "insert":
                corrupt.add(Order(user_id=user_id, symbol="X", transaction_type="BUY", quantity=1,
                                  filled_quantity=1, price=10.0, average_fill_price=10.0,
                                  pnl=0.0, status="FILLED", is_paper=True, fill_id="NEW",
                                  audit_hash="c" * 64, previous_audit_hash=orders[-1].audit_hash))
            elif mutation == "sequence":
                orders[1].id, orders[0].id = orders[0].id, orders[1].id
            corrupt.commit()
            result = _reconcile_paper_ledger(corrupt, user_id)
            assert result["status"] == "MISMATCH", mutation
            assert result["repairability"] == "BLOCKED", mutation
            assert result["repair_plan"]["apply"] is False, mutation
        finally:
            corrupt.close()
            engine.dispose()

def test_paper_reconcile_repairability_precedence_account_vs_ledger_corruption(tmp_path):
    from app.execution.paper_routes import PaperOrderRequest, _reconcile_paper_ledger, paper_order

    def setup(name):
        engine = create_engine(f"sqlite:///{tmp_path / name}.db",
                               connect_args={"check_same_thread": False, "timeout": 10})
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        db = Session()
        user = User(email=f"{name}@example.com", hashed_password="", full_name="Precedence", is_active=True)
        db.add(user); db.flush()
        db.add(TradingAccount(user_id=user.id, mode="PAPER", virtual_balance=5000.0,
                              initial_virtual_balance=5000.0, initial_balance_source="BOOTSTRAP",
                              realized_pnl=0.0, is_active=True))
        db.commit(); uid=user.id; db.close()
        db = Session()
        paper_order(PaperOrderRequest(symbol="A", transaction_type="BUY", price=100.0, quantity=2, fill_id=f"{name}-1"), user_id=uid, db=db)
        db.close()
        return engine, Session, uid

    engine, Session, uid = setup("account-only")
    db = Session()
    try:
        account = db.query(TradingAccount).filter(TradingAccount.user_id == uid).one()
        account.virtual_balance = 4999.0
        db.commit()
        result = _reconcile_paper_ledger(db, uid)
        assert result["status"] == "MISMATCH"
        assert result["repairability"] == "SAFE_DRY_RUN"
        assert result["repair_plan"]["apply"] is False
        assert "virtual_balance_mismatch" in result["mismatches"]
    finally:
        db.close(); engine.dispose()

    engine, Session, uid = setup("ledger-only")
    db = Session()
    try:
        order = db.query(Order).filter(Order.user_id == uid, Order.is_paper.is_(True)).one()
        order.price = 101.0
        db.commit()
        result = _reconcile_paper_ledger(db, uid)
        assert result["status"] == "MISMATCH"
        assert result["repairability"] == "BLOCKED"
        assert result["repair_plan"]["apply"] is False
    finally:
        db.close(); engine.dispose()

    engine, Session, uid = setup("both")
    db = Session()
    try:
        account = db.query(TradingAccount).filter(TradingAccount.user_id == uid).one()
        order = db.query(Order).filter(Order.user_id == uid, Order.is_paper.is_(True)).one()
        account.virtual_balance = 4999.0
        order.price = 101.0
        db.commit()
        result = _reconcile_paper_ledger(db, uid)
        assert result["status"] == "MISMATCH"
        assert result["repairability"] == "BLOCKED"
        assert result["repair_plan"]["apply"] is False
        assert "virtual_balance_mismatch" in result["mismatches"]
    finally:
        db.close(); engine.dispose()


def test_paper_reconcile_mismatch_classification_is_deterministic_and_complete(tmp_path):
    from app.execution.paper_routes import PaperOrderRequest, _reconcile_paper_ledger, paper_order

    cases = (
        ("account", "ACCOUNTING_STATE", "SAFE_DRY_RUN"),
        ("position", "POSITION_STATE", "SAFE_DRY_RUN"),
        ("audit", "AUDIT_INTEGRITY", "BLOCKED"),
        ("order", "ORDER_INTEGRITY", "BLOCKED"),
        ("baseline", "BASELINE_INTEGRITY", "BLOCKED"),
    )
    for kind, expected_category, expected_repairability in cases:
        engine = create_engine(
            f"sqlite:///{tmp_path / f'classify-{kind}.db'}",
            connect_args={"check_same_thread": False, "timeout": 10},
        )
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        seed = Session()
        try:
            user = User(email=f"classify-{kind}@example.com", hashed_password="", full_name="Classification", is_active=True)
            seed.add(user)
            seed.flush()
            seed.add(TradingAccount(
                user_id=user.id, mode="PAPER", virtual_balance=5000.0,
                initial_virtual_balance=5000.0, initial_balance_source="BOOTSTRAP",
                realized_pnl=0.0, is_active=True,
            ))
            seed.commit()
            uid = user.id
        finally:
            seed.close()

        db = Session()
        try:
            paper_order(
                PaperOrderRequest(symbol="CLASSIFY", transaction_type="BUY",
                                  price=100.0, quantity=2, fill_id=f"{kind}-1"),
                user_id=uid, db=db,
            )
        finally:
            db.close()

        corrupt = Session()
        try:
            account = corrupt.query(TradingAccount).filter(TradingAccount.user_id == uid).one()
            order = corrupt.query(Order).filter(Order.user_id == uid, Order.is_paper.is_(True)).one()
            position = corrupt.query(Position).filter(
                Position.user_id == uid, Position.is_paper.is_(True)
            ).one()
            if kind == "account":
                account.virtual_balance += 1.0
            elif kind == "position":
                position.quantity += 1
            elif kind == "audit":
                order.audit_hash = "a" * 64
            elif kind == "order":
                order.quantity = 3
            else:
                account.initial_balance_source = "MIGRATED_INFERRED"
            corrupt.commit()

            result = _reconcile_paper_ledger(corrupt, uid)
            assert result["status"] == "MISMATCH", kind
            assert result["repairability"] == expected_repairability, kind
            assert result["repairability_reason"] == (
                "account_or_position_state_only"
                if expected_repairability == "SAFE_DRY_RUN"
                else "ledger_or_baseline_integrity_failure"
            ), kind
            assert expected_category in result["mismatch_categories"], kind
            assert result["repair_plan"]["apply"] is False, kind
        finally:
            corrupt.close()
            engine.dispose()


def test_paper_reconcile_mixed_corruption_categories_always_report_all_categories_and_block(tmp_path):
    from app.execution.paper_routes import PaperOrderRequest, _reconcile_paper_ledger, paper_order

    engine = create_engine(
        f"sqlite:///{tmp_path / 'classify-mixed.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    seed = Session()
    try:
        user = User(email="classify-mixed@example.com", hashed_password="", full_name="Mixed Classification", is_active=True)
        seed.add(user)
        seed.flush()
        seed.add(TradingAccount(
            user_id=user.id, mode="PAPER", virtual_balance=5000.0,
            initial_virtual_balance=5000.0, initial_balance_source="BOOTSTRAP",
            realized_pnl=0.0, is_active=True,
        ))
        seed.commit()
        uid = user.id
    finally:
        seed.close()

    db = Session()
    try:
        paper_order(PaperOrderRequest(
            symbol="MIX", transaction_type="BUY", price=100.0, quantity=2, fill_id="MX1"
        ), user_id=uid, db=db)
    finally:
        db.close()

    corrupt = Session()
    try:
        account = corrupt.query(TradingAccount).filter(TradingAccount.user_id == uid).one()
        order = corrupt.query(Order).filter(Order.user_id == uid, Order.is_paper.is_(True)).one()
        position = corrupt.query(Position).filter(
            Position.user_id == uid, Position.is_paper.is_(True)
        ).one()
        account.virtual_balance += 10.0
        order.pnl = 5.0
        position.quantity += 1
        corrupt.commit()

        result = _reconcile_paper_ledger(corrupt, uid)
        assert result["status"] == "MISMATCH"
        assert result["repairability"] == "BLOCKED"
        assert result["repairability_reason"] == "ledger_or_baseline_integrity_failure"
        assert result["mismatch_categories"] == [
            "ACCOUNTING_STATE",
            "AUDIT_INTEGRITY",
            "POSITION_STATE",
        ]
        assert result["repair_plan"]["apply"] is False
    finally:
        corrupt.close()
        engine.dispose()


def test_paper_reconcile_safe_dry_run_is_strictly_account_or_position_state_only(tmp_path):
    from app.execution.paper_routes import PaperOrderRequest, _reconcile_paper_ledger, paper_order

    engine = create_engine(f"sqlite:///{tmp_path / 'safe-boundary.db'}.db",
                           connect_args={"check_same_thread": False, "timeout": 10})
    Base.metadata.create_all(engine); Session = sessionmaker(bind=engine)
    db = Session()
    try:
        user = User(email="safe-boundary@example.com", hashed_password="", full_name="Safe Boundary", is_active=True)
        db.add(user); db.flush()
        db.add(TradingAccount(user_id=user.id, mode="PAPER", virtual_balance=5000.0,
                              initial_virtual_balance=5000.0, initial_balance_source="BOOTSTRAP",
                              realized_pnl=0.0, is_active=True))
        db.commit(); uid=user.id
    finally: db.close()

    db=Session()
    try:
        paper_order(PaperOrderRequest(symbol="PORT", transaction_type="BUY", price=100.0, quantity=5, fill_id="SB1"), user_id=uid, db=db)
    finally: db.close()

    for kind in ("cash", "realized", "position_qty", "position_avg", "position_open"):
        db=Session()
        try:
            if kind == "cash":
                db.query(TradingAccount).filter(TradingAccount.user_id == uid).one().virtual_balance += 1
            elif kind == "realized":
                db.query(TradingAccount).filter(TradingAccount.user_id == uid).one().realized_pnl += 1
            else:
                pos=db.query(Position).filter(Position.user_id == uid, Position.is_paper.is_(True)).one()
                if kind == "position_qty": pos.quantity += 1
                elif kind == "position_avg": pos.average_price += 1
                else: pos.is_open = False
            db.commit()
            result=_reconcile_paper_ledger(db,uid)
            assert result["status"]=="MISMATCH", kind
            assert result["repairability"]=="SAFE_DRY_RUN", kind
            assert result["repair_plan"]["apply"] is False
        finally:
            db.close()
    engine.dispose()


def test_paper_reconcile_sqlite_read_snapshot_is_repeatable_during_concurrent_write(tmp_path):
    from app.execution.paper_routes import PaperOrderRequest, _reconcile_paper_ledger, paper_order

    engine = create_engine(
        f"sqlite:///{tmp_path / 'reconcile-snapshot.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    Session = sessionmaker(bind=engine)
    seed = Session()
    try:
        user = User(
            email="snapshot@example.com",
            hashed_password="",
            full_name="Snapshot",
            is_active=True,
        )
        seed.add(user)
        seed.flush()
        seed.add(TradingAccount(
            user_id=user.id,
            mode="PAPER",
            virtual_balance=5000.0,
            initial_virtual_balance=5000.0,
            initial_balance_source="BOOTSTRAP",
            realized_pnl=0.0,
            is_active=True,
        ))
        seed.commit()
        uid = user.id
    finally:
        seed.close()

    writer = Session()
    reader = Session()
    try:
        paper_order(
            PaperOrderRequest(
                symbol="SNAP",
                transaction_type="BUY",
                price=100.0,
                quantity=2,
                fill_id="SNAP-1",
            ),
            user_id=uid,
            db=writer,
        )
        writer.commit()
        first = _reconcile_paper_ledger(reader, uid)

        writer.add(Order(
            user_id=uid,
            symbol="SNAP2",
            transaction_type="BUY",
            order_type="MARKET",
            product_type="INTRADAY",
            quantity=1,
            price=50.0,
            average_price=50.0,
            filled_quantity=1,
            average_fill_price=50.0,
            status="FILLED",
            is_paper=True,
            fill_id="SNAP-2",
            pnl=0.0,
            message="snapshot test",
        ))
        writer.commit()

        second = _reconcile_paper_ledger(reader, uid)
        assert second == first
        assert second["orders"] == 1
        assert "SNAP2" not in second["reconstructed_positions"]
    finally:
        reader.rollback()
        reader.close()
        writer.close()
        engine.dispose()

def test_paper_reconcile_http_concurrent_reader_mutation_stress_matrix(tmp_path):
    """Concurrent HTTP reconciliation never exposes a malformed or mutating response."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from app.core.database import get_db
    from app.execution import paper_routes as routes

    engine = create_engine(
        f"sqlite:///{tmp_path / 'http-reconcile-stress.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    TestSession = sessionmaker(bind=engine)

    seed = TestSession()
    try:
        user = User(
            email="http-reconcile-stress@example.com",
            hashed_password="",
            full_name="HTTP Reconcile Stress",
            is_active=True,
        )
        seed.add(user)
        seed.flush()
        seed.add(TradingAccount(
            user_id=user.id,
            mode="PAPER",
            virtual_balance=10_000_000.0,
            initial_virtual_balance=10_000_000.0,
            initial_balance_source="BOOTSTRAP",
            realized_pnl=0.0,
            is_active=True,
        ))
        seed.commit()
        user_id = int(user.id)
    finally:
        seed.close()

    def override_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    client = TestClient(app)
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[routes.current_user_id] = lambda: user_id
    symbols = [f"HTTP-STRESS-{index}" for index in range(4)]

    def reconcile():
        response = client.get("/api/v1/execution/paper/reconcile")
        return ("reconcile", response.status_code, response.json())

    def entry(index: int):
        response = client.post(
            "/api/v1/execution/paper/order",
            json={
                "symbol": symbols[index],
                "transaction_type": "BUY",
                "price": 10.0 + index,
                "quantity": 1,
                "fill_id": f"HTTP-STRESS-ENTRY-{index}",
            },
        )
        return ("entry", response.status_code, response.json())

    def exit_(index: int):
        response = client.post(
            "/api/v1/execution/paper/exit",
            json={
                "symbol": symbols[index],
                "price": 11.0 + index,
                "fill_id": f"HTTP-STRESS-EXIT-{index}",
            },
        )
        return ("exit", response.status_code, response.json())

    try:
        for index in range(2):
            kind, status, payload = entry(index)
            assert kind == "entry"
            assert status == 200
            assert payload["status"] == "success"

        operations = []
        with ThreadPoolExecutor(max_workers=12) as pool:
            futures = [pool.submit(reconcile) for _ in range(8)]
            futures.extend([
                pool.submit(entry, 2),
                pool.submit(exit_, 0),
                pool.submit(exit_, 1),
                pool.submit(reconcile),
            ])
            for future in as_completed(futures):
                operations.append(future.result())

        reconcile_results = [item for item in operations if item[0] == "reconcile"]
        assert len(reconcile_results) == 10
        for _, status, payload in reconcile_results:
            assert status == 200
            assert payload["status"] in {"OK", "MISMATCH"}
            assert isinstance(payload["orders"], int)
            assert isinstance(payload["reconstructed_positions"], dict)
            assert isinstance(payload["mismatches"], list)
            assert payload["repair_plan"]["apply"] is False
            assert payload["repair_plan"]["reason"] == "read_only_dry_run"
            assert payload["user_id"] == user_id

        mutation_results = [item for item in operations if item[0] in {"entry", "exit"}]
        assert len(mutation_results) == 3
        for kind, status, payload in mutation_results:
            assert status in {200, 409}
            assert isinstance(payload, dict)
            if kind == "entry" and status == 200:
                assert payload["status"] == "success"
            if kind == "exit" and status == 200:
                assert payload["status"] in {"closed", "flat"}

        final = client.get("/api/v1/execution/paper/reconcile")
        assert final.status_code == 200
        final_payload = final.json()
        assert final_payload["user_id"] == user_id
        assert final_payload["repair_plan"]["apply"] is False

        verify = TestSession()
        try:
            stress_orders = verify.query(Order).filter(
                Order.user_id == user_id,
                Order.is_paper.is_(True),
                Order.symbol.in_(symbols),
            ).all()
            assert len(stress_orders) >= 3
            assert all(order.status == "FILLED" for order in stress_orders)
            assert verify.query(Order).filter(
                Order.user_id == user_id,
                Order.is_paper.is_(False),
                Order.symbol.in_(symbols),
            ).count() == 0
        finally:
            verify.close()
    finally:
        app.dependency_overrides.pop(routes.current_user_id, None)
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()
\ndef test_paper_reconcile_http_concurrent_two_user_isolation_stress(tmp_path):
    """Concurrent reconciliation/mutations for two users cannot cross-contaminate ledgers."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from app.core.database import get_db
    from app.execution import paper_routes as routes

    engine = create_engine(
        f"sqlite:///{tmp_path / 'http-reconcile-two-user.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    TestSession = sessionmaker(bind=engine)

    seed = TestSession()
    try:
        users = []
        for suffix in ("a", "b"):
            user = User(
                email=f"http-isolation-{suffix}@example.com",
                hashed_password="",
                full_name=f"HTTP Isolation {suffix}",
                is_active=True,
            )
            seed.add(user)
            seed.flush()
            seed.add(TradingAccount(
                user_id=user.id,
                mode="PAPER",
                virtual_balance=100_000.0,
                initial_virtual_balance=100_000.0,
                initial_balance_source="BOOTSTRAP",
                realized_pnl=0.0,
                is_active=True,
            ))
            users.append(int(user.id))
        seed.commit()
    finally:
        seed.close()

    def override_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    client = TestClient(app)
    app.dependency_overrides[get_db] = override_db
    user_a, user_b = users
    symbols = {
        user_a: "ISO-A",
        user_b: "ISO-B",
    }

    def request_for(user_id, method, path, payload=None):
        app.dependency_overrides[routes.current_user_id] = lambda: user_id
        if method == "GET":
            response = client.get(path)
        else:
            response = client.post(path, json=payload)
        return user_id, response.status_code, response.json()

    def reconcile(user_id):
        return request_for(user_id, "GET", "/api/v1/execution/paper/reconcile")

    def entry(user_id):
        return request_for(
            user_id,
            "POST",
            "/api/v1/execution/paper/order",
            {
                "symbol": symbols[user_id],
                "transaction_type": "BUY",
                "price": 100.0 if user_id == user_a else 200.0,
                "quantity": 2,
                "fill_id": f"ISO-ENTRY-{user_id}",
            },
        )

    try:
        # Seed independent positions before concurrent readers/mutations.
        assert entry(user_a)[1] == 200
        assert entry(user_b)[1] == 200

        operations = []
        with ThreadPoolExecutor(max_workers=16) as pool:
            futures = []
            for _ in range(6):
                futures.extend([pool.submit(reconcile, user_a), pool.submit(reconcile, user_b)])
            futures.extend([
                pool.submit(entry, user_a),
                pool.submit(entry, user_b),
                pool.submit(
                    request_for,
                    user_a,
                    "POST",
                    "/api/v1/execution/paper/exit",
                    {"symbol": symbols[user_a], "price": 110.0, "fill_id": f"ISO-EXIT-{user_a}"},
                ),
                pool.submit(
                    request_for,
                    user_b,
                    "POST",
                    "/api/v1/execution/paper/exit",
                    {"symbol": symbols[user_b], "price": 210.0, "fill_id": f"ISO-EXIT-{user_b}"},
                ),
            ])
            for future in as_completed(futures):
                operations.append(future.result())

        for owner, status, payload in operations:
            assert isinstance(payload, dict)
            if payload.get("user_id") is not None:
                assert int(payload["user_id"]) == owner
            if "repair_plan" in payload:
                assert payload["repair_plan"]["apply"] is False

        reconcile_results = [item for item in operations if "repair_plan" in item[2]]
        assert len(reconcile_results) == 12
        assert all(status == 200 for _, status, _ in reconcile_results)

        verify = TestSession()
        try:
            for owner, own_symbol in symbols.items():
                own_orders = verify.query(Order).filter(
                    Order.user_id == owner,
                    Order.is_paper.is_(True),
                    Order.symbol == own_symbol,
                ).all()
                other_symbol = symbols[user_b if owner == user_a else user_a]
                leaked = verify.query(Order).filter(
                    Order.user_id == owner,
                    Order.is_paper.is_(True),
                    Order.symbol == other_symbol,
                ).count()
                assert leaked == 0
                assert own_orders
                assert all(order.symbol == own_symbol for order in own_orders)

                result = _reconcile_paper_ledger(verify, owner)
                assert result["user_id"] == owner
                assert result["repair_plan"]["apply"] is False
                assert all(
                    symbol == own_symbol
                    for symbol in result["reconstructed_positions"]
                )
        finally:
            verify.close()
    finally:
        app.dependency_overrides.pop(routes.current_user_id, None)
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()
