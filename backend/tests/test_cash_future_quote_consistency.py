from app.scanner.cash_future import CashFutureConfig, CashQuote, FutureQuote, calculate_cash_future


def _future(*, bid=103.0, ask=104.0, ltp=103.5):
    return FutureQuote(
        symbol="ABC",
        contract_month="2026-10",
        ltp=ltp,
        lot_size=100,
        margin_required=1000.0,
        bid=bid,
        ask=ask,
    )


def test_rejects_cash_ltp_outside_quote():
    result = calculate_cash_future(
        CashQuote(symbol="ABC", ltp=101.0, bid=99.0, ask=100.0),
        _future(),
        CashFutureConfig(),
    )

    assert result.executable is False
    assert "cash_ltp_outside_bid_ask" in result.rejection_reasons


def test_rejects_future_ltp_outside_quote():
    result = calculate_cash_future(
        CashQuote(symbol="ABC", ltp=100.0, bid=99.0, ask=101.0),
        _future(ltp=105.0),
        CashFutureConfig(),
    )

    assert result.executable is False
    assert "future_ltp_outside_bid_ask" in result.rejection_reasons


def test_accepts_ltp_on_quote_boundaries():
    result = calculate_cash_future(
        CashQuote(symbol="ABC", ltp=100.0, bid=100.0, ask=101.0),
        _future(ltp=103.0, bid=103.0, ask=104.0),
        CashFutureConfig(),
    )

    assert result.executable is True
    assert "cash_ltp_outside_bid_ask" not in result.rejection_reasons
    assert "future_ltp_outside_bid_ask" not in result.rejection_reasons


def test_partial_quotes_do_not_add_ltp_consistency_rejection():
    result = calculate_cash_future(
        CashQuote(symbol="ABC", ltp=100.0, ask=100.5),
        _future(bid=103.0, ask=None),
        CashFutureConfig(require_two_sided_quotes=False),
    )

    assert "cash_ltp_outside_bid_ask" not in result.rejection_reasons
    assert "future_ltp_outside_bid_ask" not in result.rejection_reasons
