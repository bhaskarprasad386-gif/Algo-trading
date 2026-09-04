import math

from app.scanner.cash_future import CashFutureConfig, CashQuote, FutureQuote, calculate_cash_future


def _future(**overrides):
    values = {
        "symbol": "ABC", "contract_month": "2026-10", "ltp": 104.0,
        "lot_size": 100, "margin_required": 20_000.0, "volume": 10_000,
        "oi": 20_000, "bid": 103.8, "ask": 104.2,
    }
    values.update(overrides)
    return FutureQuote(**values)


def test_zero_cash_ask_is_never_executable():
    result = calculate_cash_future(CashQuote("ABC", 100.0, bid=99.9, ask=0.0), _future(), CashFutureConfig())
    assert result.executable is False
    assert "missing_executable_quote" in result.rejection_reasons


def test_nonfinite_future_bid_is_never_executable():
    result = calculate_cash_future(CashQuote("ABC", 100.0, bid=99.9, ask=100.1), _future(bid=math.inf), CashFutureConfig())
    assert result.executable is False
    assert "missing_executable_quote" in result.rejection_reasons


def test_negative_cash_ask_is_never_executable():
    result = calculate_cash_future(CashQuote("ABC", 100.0, bid=99.9, ask=-1.0), _future(), CashFutureConfig())
    assert result.executable is False
    assert "missing_executable_quote" in result.rejection_reasons
