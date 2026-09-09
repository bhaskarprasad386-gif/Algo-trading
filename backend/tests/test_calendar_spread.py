from app.backtesting.calendar_spread import CalendarQuote, CalendarSpreadBacktester


def quote(expiry: int, bid: float, ask: float) -> CalendarQuote:
    return CalendarQuote(
        timestamp_ns=1_000,
        underlying="NIFTY",
        expiry=expiry,
        bid=bid,
        ask=ask,
        lot_size=50,
        instrument_class="INDEX",
        strike=25_000,
        option_type="CALL",
    )


def test_long_calendar_uses_executable_near_ask_and_far_bid():
    result = CalendarSpreadBacktester.evaluate(
        quote(20260910, 100.0, 101.0),
        quote(20260917, 106.0, 107.0),
        fees_per_unit=1.0,
    )
    assert result is not None
    assert result.executable_edge == 4.0
    assert result.edge_per_lot == 200.0
    assert result.gross_pnl == 200.0


def test_reverse_calendar_uses_near_bid_and_far_ask():
    result = CalendarSpreadBacktester.evaluate(
        quote(20260910, 110.0, 111.0),
        quote(20260917, 102.0, 103.0),
        direction="SHORT_NEAR_LONG_FAR",
    )
    assert result is not None
    assert result.executable_edge == 7.0


def test_calendar_rejects_mismatched_strike_and_expiry_order():
    near = quote(20260917, 100.0, 101.0)
    far = quote(20260910, 110.0, 111.0)
    try:
        CalendarSpreadBacktester.evaluate(near, far)
    except ValueError as exc:
        assert "near expiry" in str(exc)
    else:
        raise AssertionError("expected invalid expiry ordering")


def test_calendar_requires_same_option_contract_shape():
    near = quote(20260910, 100.0, 101.0)
    far = CalendarQuote(
        timestamp_ns=1_000,
        underlying="NIFTY",
        expiry=20260917,
        bid=106.0,
        ask=107.0,
        lot_size=50,
        instrument_class="INDEX",
        strike=25_100,
        option_type="CALL",
    )
    try:
        CalendarSpreadBacktester.evaluate(near, far)
    except ValueError as exc:
        assert "strike" in str(exc)
    else:
        raise AssertionError("expected mismatched strike rejection")
