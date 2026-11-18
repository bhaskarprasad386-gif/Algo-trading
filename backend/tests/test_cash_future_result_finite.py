import math

import pytest

from app.scanner.cash_future import CashFutureConfig, CashQuote, FutureQuote, calculate_cash_future


def test_calculator_rejects_overflowing_result_values():
    cash = CashQuote("ABC", 1.0, 0.9, 1.0)
    future = FutureQuote(
        symbol="ABC",
        contract_month="2026-10",
        ltp=1e308,
        lot_size=100,
        margin_required=20_000.0,
        volume=10_000,
        oi=20_000,
        bid=1e308,
        ask=1e308,
    )

    with pytest.raises(ValueError, match="cash-future result must be finite"):
        calculate_cash_future(cash, future, CashFutureConfig())


def test_normal_result_numeric_fields_are_finite():
    result = calculate_cash_future(
        CashQuote("ABC", 100.0, 99.9, 100.1),
        FutureQuote(
            symbol="ABC",
            contract_month="2026-10",
            ltp=104.0,
            lot_size=100,
            margin_required=20_000.0,
            volume=10_000,
            oi=20_000,
            bid=103.8,
            ask=104.2,
        ),
        CashFutureConfig(),
    )

    numeric_fields = (
        result.cash_ltp,
        result.future_ltp,
        result.gap,
        result.gap_pct,
        result.executable_gap,
        result.executable_gap_pct,
        result.cash_execution_price,
        result.future_execution_price,
        result.cash_bid_ask_spread_pct,
        result.future_bid_ask_spread_pct,
        result.gross_spread_profit,
        result.charges,
        result.funding_cost,
        result.net_profit,
        result.margin_required,
        result.deployed_capital,
        result.roi_pct,
    )
    assert all(value is None or math.isfinite(value) for value in numeric_fields)
