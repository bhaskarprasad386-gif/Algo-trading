from datetime import date, timedelta

from app.scanner.cash_future import CashFutureConfig, CashQuote, FutureQuote, calculate_cash_future


def _future(**overrides):
    values = dict(
        symbol="ABC",
        contract_month="CURRENT",
        ltp=110.0,
        lot_size=100,
        margin_required=2000.0,
        volume=50000,
        oi=25000,
        bid=109.0,
        ask=111.0,
        expiry=date.today() + timedelta(days=20),
    )
    values.update(overrides)
    return FutureQuote(**values)


def test_executable_profit_uses_cash_ask_and_future_bid_not_ltp():
    result = calculate_cash_future(
        CashQuote("ABC", ltp=100.0, bid=99.5, ask=101.0),
        _future(),
        CashFutureConfig(charges=100.0, funding_cost=50.0),
    )

    assert result.executable_gap == 8.0
    assert result.cash_execution_price == 101.0
    assert result.future_execution_price == 109.0
    assert result.gross_spread_profit == 800.0
    assert result.net_profit == 650.0
    assert result.executable is True


def test_missing_executable_quote_is_not_marked_executable():
    result = calculate_cash_future(
        CashQuote("ABC", ltp=100.0, bid=99.5, ask=None),
        _future(),
        CashFutureConfig(min_gap=1.0),
    )

    assert result.executable is False
    assert "missing_executable_quotes" in result.rejection_reasons


def test_wide_cash_spread_is_rejected_when_configured():
    result = calculate_cash_future(
        CashQuote("ABC", ltp=100.0, bid=90.0, ask=110.0),
        _future(),
        CashFutureConfig(max_cash_bid_ask_spread_pct=5.0),
    )

    assert result.executable is False
    assert "cash_bid_ask_spread_above_maximum" in result.rejection_reasons
