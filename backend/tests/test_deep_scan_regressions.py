from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.routes import _issue_token, logout, me
from app.backtesting.arbitrage_backtester import BoxSpreadBacktester, OptionQuote
from app.backtesting.backtest_ledger import BacktestTradeLedger
from app.backtesting.cash_future import CashFutureBasisTrade
from app.backtesting.cash_future_pnl import CashFutureTrade
from app.backtesting.cash_future_strategy_runner import run_cash_future_strategy
from app.backtesting.engine import BacktestTrade
from app.core.config import settings
from app.core.database import Base
from app.models import TradingAccount, User
from app.risk.engine import RiskEngine, RiskLimits
from app.scanner.cash_future_history import CashFutureHistoryPoint


def test_short_box_uses_high_put_bid_and_ask():
    low = OptionQuote(1, "NIFTY", 20260924, 100, 20, 21, 20, 21, 1, "INDEX")
    high = OptionQuote(1, "NIFTY", 20260924, 110, 10, 11, 10, 11, 1, "INDEX")
    result = BoxSpreadBacktester.evaluate(low, high, direction="SHORT")
    assert result is not None
    assert result.executable_edge == 8


def test_risk_check_cannot_bypass_internal_loss_with_zero_external_pnl():
    engine = RiskEngine(RiskLimits(max_loss=10_000))
    engine._realized_pnl = -15_000
    allowed, reason = engine.check(1, realized_pnl=0)
    assert not allowed
    assert reason == "maximum loss limit reached"


def test_backtest_trade_ledger_is_safe_across_threads():
    ledger = BacktestTradeLedger()
    trade = BacktestTrade(
        datetime.now(timezone.utc), datetime.now(timezone.utc), 100, 101, 1, 1, 0, 1
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(ledger.append, "run", i, [trade]) for i in range(2)]
        assert [future.result() for future in futures] == [1, 1]
    assert ledger.count("run") == 2
    ledger.close()


def test_cash_future_trade_models_are_explicitly_distinct():
    assert CashFutureBasisTrade is not CashFutureTrade
    assert CashFutureBasisTrade.__name__ == "CashFutureBasisTrade"
    assert CashFutureTrade.__name__ == "CashFutureTrade"


def _point(ts: datetime, gap: float) -> CashFutureHistoryPoint:
    return CashFutureHistoryPoint(
        timestamp=ts,
        symbol="SBIN",
        contract_month="2026-09",
        cash_price=100.0,
        future_price=100.0 + gap,
        gap=gap,
        gap_pct=gap,
        lot_size=250,
        margin_required=1_000.0,
    )


def test_cash_future_strategy_trade_quantity_is_lots_not_contract_units():
    points = [
        _point(datetime(2026, 9, 1, 9, 15), 5),
        _point(datetime(2026, 9, 1, 9, 16), 2),
    ]
    result = run_cash_future_strategy(
        points,
        lambda point, history: "BUY" if len(history) == 1 else "SELL",
        strategy_id="quantity-regression",
    )
    assert len(result.trades) == 1
    assert result.trades[0]["lot_size"] == 250
    assert result.trades[0]["quantity"] == 1


def test_logout_invalidates_persisted_access_token(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", "x" * 40)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    try:
        user = User(email="logout@example.com", hashed_password="unused")
        db.add(user)
        db.flush()
        db.add(TradingAccount(user_id=user.id, mode="PAPER", virtual_balance=10_000_000.0, realized_pnl=0.0))
        db.commit()
        token = _issue_token(db, user).access_token
        assert me(token=token, db=db)["id"] == user.id
        assert logout(token=token, db=db)["status"] == "logged_out"
        try:
            me(token=token, db=db)
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 401
        else:
            raise AssertionError("logged-out token remained usable")
    finally:
        db.close()
        engine.dispose()
