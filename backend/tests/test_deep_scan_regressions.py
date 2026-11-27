from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from app.backtesting.backtest_ledger import BacktestTradeLedger
from app.backtesting.engine import BacktestTrade
from app.risk.engine import RiskEngine, RiskLimits


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
