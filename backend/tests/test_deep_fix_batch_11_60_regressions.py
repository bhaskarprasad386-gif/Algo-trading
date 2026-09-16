from datetime import date, datetime, timedelta, timezone

import pytest

from app.backtesting.arbitrage_backtester import FutureQuote, LiquidityPolicy, OptionQuote, SyntheticCashCarryBacktester
from app.backtesting.arbitrage_chain_selector import ChainContract, pair_by_strike, select_calendar_expiries
from app.backtesting.cash_future_strategy_runner import CashFutureStrategyConfig, run_cash_future_strategy
from app.scanner.cash_future_history import CashFutureHistoryPoint, analyze_historical_gap_outcomes


def cf_point(gap: float, minute: int, *, symbol: str = "SBIN", contract: str = "2026-01", timestamp: datetime | None = None) -> CashFutureHistoryPoint:
    return CashFutureHistoryPoint(
        timestamp=timestamp or datetime(2026, 1, 2, 9, minute, tzinfo=timezone.utc),
        symbol=symbol,
        contract_month=contract,
        cash_price=100.0,
        future_price=100.0 + gap,
        gap=gap,
        gap_pct=gap,
        lot_size=10,
        margin_required=1000.0,
        expiry_date=date(2026, 1, 29),
    )


def chain(strike: float, *, expiry: int = 20261001, timestamp: int = 1, venue: str = "NSE", instrument_class: str = "STOCK") -> ChainContract:
    return ChainContract(timestamp, venue, "TEST", instrument_class, expiry, strike, "CE", 50, 1000, 2000, 10, 11)


def test_historical_gap_does_not_exit_after_max_holding_deadline():
    entry_time = datetime(2026, 1, 2, 9, 15, tzinfo=timezone.utc)
    points = [
        cf_point(5, 15, timestamp=entry_time),
        cf_point(4, 16, timestamp=entry_time + timedelta(minutes=1)),
        cf_point(-1, 16, timestamp=entry_time + timedelta(days=2)),
    ]
    outcomes = analyze_historical_gap_outcomes(points, target_gap=5, max_holding_days=1)
    assert outcomes[0].exit_timestamp is None


def test_reverse_cash_future_direction_uses_sell_to_open_and_buy_to_close():
    strategy = lambda point, history: "BUY" if not history else "SELL"
    result = run_cash_future_strategy(
        [cf_point(-5, 15), cf_point(-3, 16)],
        strategy,
        strategy_id="reverse",
        config=CashFutureStrategyConfig(initial_capital=10_000, cash_side="SELL", future_side="BUY"),
    )
    assert len(result.trades) == 1
    assert result.trades[0]["gross_profit"] == 20.0


def test_strategy_rejects_mixed_symbol_stream():
    with pytest.raises(ValueError, match="multiple symbols"):
        run_cash_future_strategy(
            [cf_point(5, 15), cf_point(3, 16, symbol="RELIANCE")],
            lambda point, history: "BUY" if not history else "SELL",
            strategy_id="mixed-symbol",
        )


def test_strategy_rejects_mixed_contract_stream():
    with pytest.raises(ValueError, match="multiple contract months"):
        run_cash_future_strategy(
            [cf_point(5, 15), cf_point(3, 16, contract="2026-02")],
            lambda point, history: "BUY" if not history else "SELL",
            strategy_id="mixed-contract",
        )


def test_synthetic_liquidity_policy_applies_to_option_quote():
    option = OptionQuote(1, "TEST", 20261001, 100, 10, 11, 10, 11, 50, "STOCK", volume=5, oi=5)
    future = FutureQuote(1, "TEST", 20261001, 105, 106, 50, "STOCK", volume=1000, oi=2000)
    policy = LiquidityPolicy(min_option_volume=10, min_option_oi=10)
    assert SyntheticCashCarryBacktester.evaluate(option, future, time_to_expiry_years=0.1, liquidity=policy) is None


def test_duplicate_strike_is_rejected_in_pairing():
    contracts = [chain(100), chain(100)]
    with pytest.raises(ValueError, match="duplicate strike"):
        pair_by_strike(contracts, expiry=20261001, option_type="CE")


def test_calendar_expiry_selection_allows_multiple_expiries_in_one_snapshot():
    contracts = [chain(100, expiry=20261001), chain(100, expiry=20261101)]
    assert select_calendar_expiries(contracts) == ((20261001, 20261101),)


def test_calendar_expiry_selection_rejects_mixed_underlyings():
    contracts = [chain(100), ChainContract(1, "NSE", "OTHER", "STOCK", 20261101, 100, "CE", 50, 1000, 2000, 10, 11)]
    with pytest.raises(ValueError, match="timestamp, underlying"):
        select_calendar_expiries(contracts)
