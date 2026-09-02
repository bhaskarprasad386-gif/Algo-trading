from datetime import date, timedelta

import pytest

from app.scanner.cash_future import CashFutureConfig, CashQuote, FutureQuote, calculate_cash_future


def test_cash_future_calculates_gap_profit_and_roi():
    cash = CashQuote(symbol="RELIANCE", ltp=1000.0, bid=999.0, ask=1000.0)
    future = FutureQuote(
        symbol="RELIANCE-FUT",
        contract_month="current",
        ltp=1010.0,
        lot_size=250,
        margin_required=50000.0,
        volume=10000,
        oi=20000,
        bid=1010.0,
        ask=1011.0,
    )
    result = calculate_cash_future(
        cash,
        future,
        CashFutureConfig(charges=500.0, funding_cost=100.0, min_net_profit=1000.0),
    )
    assert result.gap == 10.0
    assert result.gap_pct == pytest.approx(1.0)
    assert result.gross_spread_profit == 2500.0
    assert result.net_profit == 1900.0
    assert result.deployed_capital == 300000.0
    assert result.roi_pct == pytest.approx(1900 / 300000 * 100)
    assert result.executable is True


def test_custom_filters_reject_weak_opportunity():
    cash = CashQuote(symbol="ABC", ltp=100.0, bid=99.9, ask=100.0)
    future = FutureQuote(
        symbol="ABC-FUT",
        contract_month="near",
        ltp=100.2,
        lot_size=50,
        margin_required=10000.0,
        volume=100,
        oi=1000,
        bid=100.1,
        ask=100.3,
        expiry=date.today() + timedelta(days=2),
    )
    result = calculate_cash_future(
        cash,
        future,
        CashFutureConfig(
            min_gap=1.0,
            min_volume=1000,
            min_oi=5000,
            min_days_to_expiry=5,
        ),
    )
    assert result.executable is False
    assert "gap_below_minimum" in result.rejection_reasons
    assert "volume_below_minimum" in result.rejection_reasons
    assert "oi_below_minimum" in result.rejection_reasons
    assert "days_to_expiry_below_minimum" in result.rejection_reasons


def test_bad_prices_are_rejected():
    with pytest.raises(ValueError):
        calculate_cash_future(
            CashQuote(symbol="ABC", ltp=0),
            FutureQuote("ABC-FUT", "current", 101, 10, 1000),
            CashFutureConfig(),
        )
