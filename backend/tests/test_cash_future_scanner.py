from datetime import date, timedelta

import pytest

from app.scanner.auto_routes import _filtered
from app.scanner.cash_future import CashFutureConfig, CashQuote, FutureQuote, calculate_cash_future


def test_cash_future_calculates_gap_profit_and_roi():
    cash = CashQuote(symbol="RELIANCE", ltp=1000.0, bid=999.0, ask=1000.0)
    future = FutureQuote("RELIANCE-FUT", "current", 1010.0, 250, 50000.0, 10000, 20000, 1010.0, 1011.0)
    result = calculate_cash_future(cash, future, CashFutureConfig(charges=500.0, funding_cost=100.0, min_net_profit=1000.0))
    assert result.gap == 10.0
    assert result.gap_pct == pytest.approx(1.0)
    assert result.gross_spread_profit == 2500.0
    assert result.net_profit == 1900.0
    assert result.deployed_capital == 300000.0
    assert result.roi_pct == pytest.approx(1900 / 300000 * 100)
    assert result.executable is True


def test_custom_filters_reject_weak_opportunity():
    cash = CashQuote(symbol="ABC", ltp=100.0, bid=99.9, ask=100.0)
    future = FutureQuote("ABC-FUT", "near", 100.2, 50, 10000.0, 100, 1000, 100.1, 100.3, date.today() + timedelta(days=2))
    result = calculate_cash_future(cash, future, CashFutureConfig(min_gap=1.0, min_volume=1000, min_oi=5000, min_days_to_expiry=5))
    assert result.executable is False
    assert "gap_below_minimum" in result.rejection_reasons
    assert "volume_below_minimum" in result.rejection_reasons
    assert "oi_below_minimum" in result.rejection_reasons
    assert "days_to_expiry_below_minimum" in result.rejection_reasons


def test_bad_prices_are_rejected():
    with pytest.raises(ValueError):
        calculate_cash_future(CashQuote(symbol="ABC", ltp=0), FutureQuote("ABC-FUT", "current", 101, 10, 1000), CashFutureConfig())


def test_executable_gap_uses_cash_ask_and_future_bid():
    result = calculate_cash_future(
        CashQuote(symbol="ABC", ltp=100.0, bid=99.0, ask=100.5),
        FutureQuote("ABC-FUT", "current", 102.0, 10, 1000, bid=101.5, ask=102.5),
        CashFutureConfig(),
    )
    assert result.executable_gap == pytest.approx(1.0)
    assert result.cash_execution_price == 100.5
    assert result.future_execution_price == 101.5
    assert result.gross_spread_profit == 10.0
    assert result.executable is True


def test_execution_filters_reject_missing_quotes_and_wide_spreads():
    missing = calculate_cash_future(
        CashQuote(symbol="ABC", ltp=100.0, bid=99.0, ask=None),
        FutureQuote("ABC-FUT", "current", 102.0, 10, 1000, bid=101.0, ask=103.0),
        CashFutureConfig(require_two_sided_quotes=True),
    )
    assert missing.executable is False
    assert "missing_executable_quotes" in missing.rejection_reasons

    wide = calculate_cash_future(
        CashQuote(symbol="ABC", ltp=100.0, bid=99.0, ask=100.0),
        FutureQuote("ABC-FUT", "current", 102.0, 10, 1000, bid=100.0, ask=105.0),
        CashFutureConfig(max_bid_ask_spread_pct=2.0),
    )
    assert wide.executable is False
    assert "bid_ask_spread_above_maximum" in wide.rejection_reasons


def test_malformed_quote_direction_is_rejected():
    cash = calculate_cash_future(
        CashQuote(symbol="ABC", ltp=100.0, bid=101.0, ask=100.0),
        FutureQuote("ABC-FUT", "current", 102.0, 10, 1000, bid=101.5, ask=102.5),
        CashFutureConfig(),
    )
    future = calculate_cash_future(
        CashQuote(symbol="ABC", ltp=100.0, bid=99.0, ask=100.0),
        FutureQuote("ABC-FUT", "current", 102.0, 10, 1000, bid=103.0, ask=102.0),
        CashFutureConfig(),
    )
    assert cash.executable is False
    assert "invalid_cash_bid_ask" in cash.rejection_reasons
    assert future.executable is False
    assert "invalid_future_bid_ask" in future.rejection_reasons


def test_non_positive_executable_gap_is_never_executable():
    result = calculate_cash_future(
        CashQuote(symbol="ABC", ltp=100.0, bid=99.0, ask=101.0),
        FutureQuote("ABC-FUT", "current", 102.0, 10, 1000, bid=100.5, ask=102.0),
        CashFutureConfig(),
    )
    assert result.executable_gap == pytest.approx(-0.5)
    assert result.executable is False
    assert "executable_gap_non_positive" in result.rejection_reasons


def test_profit_and_roi_thresholds_are_part_of_executable_decision():
    result = calculate_cash_future(
        CashQuote(symbol="ABC", ltp=100.0, bid=99.0, ask=100.0),
        FutureQuote("ABC-FUT", "current", 101.0, 10, 10000, bid=100.5, ask=101.0),
        CashFutureConfig(min_net_profit=10.0, min_roi_pct=0.06),
    )
    assert result.net_profit == 5.0
    assert result.executable is False
    assert "net_profit_below_minimum" in result.rejection_reasons
    assert "roi_below_minimum" in result.rejection_reasons


def test_auto_route_filter_returns_only_strictly_executable_items():
    data = [
        {"symbol": "GOOD", "executable": True, "net_profit": 20},
        {"symbol": "FALSE", "executable": False, "net_profit": 999},
        {"symbol": "MISSING"},
        {"symbol": "TRUTHY_NOT_TRUE", "executable": 1},
    ]
    assert _filtered(data) == [{"symbol": "GOOD", "executable": True, "net_profit": 20}]
