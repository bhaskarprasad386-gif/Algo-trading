from app.scanner.cash_future import CashFutureConfig, CashQuote, FutureQuote, calculate_cash_future


def _future(**overrides):
    values = {
        "symbol": "ABC",
        "contract_month": "2026-10",
        "ltp": 104.0,
        "lot_size": 100,
        "margin_required": 20_000.0,
        "volume": 10_000,
        "oi": 20_000,
        "bid": 103.8,
        "ask": 104.2,
    }
    values.update(overrides)
    return FutureQuote(**values)


def test_crossed_cash_quote_is_never_executable():
    result = calculate_cash_future(
        CashQuote("ABC", 100.0, bid=101.0, ask=100.0),
        _future(),
        CashFutureConfig(),
    )

    assert result.executable is False
    assert "invalid_cash_bid_ask" in result.rejection_reasons


def test_crossed_future_quote_is_never_executable():
    result = calculate_cash_future(
        CashQuote("ABC", 100.0, bid=99.9, ask=100.1),
        _future(bid=105.0, ask=104.0),
        CashFutureConfig(),
    )

    assert result.executable is False
    assert "invalid_future_bid_ask" in result.rejection_reasons


def test_ltp_outside_valid_quote_is_rejected():
    result = calculate_cash_future(
        CashQuote("ABC", 100.0, bid=99.0, ask=99.5),
        _future(),
        CashFutureConfig(),
    )

    assert result.executable is False
    assert "cash_ltp_outside_bid_ask" in result.rejection_reasons
