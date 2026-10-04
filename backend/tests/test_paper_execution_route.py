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
