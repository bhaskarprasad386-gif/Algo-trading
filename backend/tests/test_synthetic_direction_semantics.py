from app.backtesting.arbitrage_backtester import FutureQuote, OptionQuote, SyntheticCashCarryBacktester
from app.scanner.synthetic_cash_carry import SyntheticScanConfig, scan_synthetic_snapshot


def _quotes(*, future_bid=73560.0, future_ask=73583.0):
    option = OptionQuote(
        timestamp_ns=1_000_000_000,
        underlying="SENSEX",
        expiry=20261029,
        strike=72000.0,
        call_bid=1714.75,
        call_ask=1726.80,
        put_bid=179.80,
        put_ask=180.55,
        lot_size=1,
        instrument_class="INDEX",
    )
    future = FutureQuote(
        timestamp_ns=1_000_000_000,
        underlying="SENSEX",
        expiry=20261029,
        bid=future_bid,
        ask=future_ask,
        lot_size=1,
        instrument_class="INDEX",
    )
    return option, future


def test_synthetic_direction_uses_future_trade_side_and_raw_bid_ask():
    option, future = _quotes()

    # Synthetic Bid = 72000 + CE Bid - PE Ask = 73534.20.
    # Synthetic Ask = 72000 + CE Ask - PE Bid = 73547.00.
    long_opp = SyntheticCashCarryBacktester.evaluate(
        option, future, rate=0.0, time_to_expiry_years=0.5,
        fees_per_unit=99.0, direction="LONG",
    )
    assert long_opp is None

    # SHORT = Future SELL @ bid and Synthetic BUY @ ask => 73560 - 73547 = +13.
    short_raw = SyntheticCashCarryBacktester.evaluate(
        option, future, rate=0.0, time_to_expiry_years=0.5,
        fees_per_unit=0.0, direction="SHORT",
    )
    assert short_raw is not None
    assert short_raw.executable_edge == 13.0
    assert short_raw.direction == "SHORT"


def test_scanner_evaluates_both_directions_and_emits_only_positive_qualified_edge():
    option, future = _quotes()
    rows = scan_synthetic_snapshot(
        [option], future, atm_strike=71000.0,
        config=SyntheticScanConfig(rate=0.05, time_to_expiry_years=0.5, fees_per_unit=99.0),
    )

    assert len(rows) == 1
    assert rows[0].direction == "SHORT"
    assert rows[0].executable_edge == 13.0
    assert rows[0].carry_factor == 1.0
    assert rows[0].fees_per_unit == 0.0


def test_scanner_can_emit_long_when_long_raw_edge_is_positive():
    option, future = _quotes(future_bid=73520.0, future_ask=73530.0)
    rows = scan_synthetic_snapshot(
        [option], future, atm_strike=71000.0,
        config=SyntheticScanConfig(rate=0.05, time_to_expiry_years=0.5),
    )

    assert len(rows) == 1
    assert rows[0].direction == "LONG"
    assert rows[0].executable_edge == 4.2
