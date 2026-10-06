from app.market_data.contracts import InstrumentKey, InstrumentType, MarketDataRecord
from app.market_data.cash_future_opportunity import CashFutureOpportunityScanner

def rec(token, symbol, kind, ts, bid, ask, lot=None, underlying=None, expiry=None):
    return MarketDataRecord(
        instrument=InstrumentKey("NSE", "NFO" if kind == InstrumentType.FUTURE else "NSE", token),
        symbol=symbol, instrument_type=kind, timestamp_ns=ts,
        ltp=(bid + ask) / 2, bid=bid, ask=ask,
        lot_size=lot, underlying=underlying, expiry=expiry,
    )

def test_cash_future_uses_cash_ask_and_future_bid_and_lot_size():
    scanner = CashFutureOpportunityScanner(minimum_gap_points=5, minimum_gross_profit=300)
    cash = rec("C1", "ABC-EQ", InstrumentType.EQUITY, 100, 99, 100, underlying="ABC")
    future = rec("F1", "ABC-FUT", InstrumentType.FUTURE, 100, 106, 107, lot=60, underlying="ABC", expiry="2026-10-29")
    result = scanner.update(cash, contract_month="CASH")
    assert result is None
    result = scanner.update(future, contract_month="CURRENT")
    assert result is not None
    assert result.signal.gap_points == 6
    assert result.signal.gross_profit == 360
    assert result.signal.qualifies is True
    assert result.as_dict()["cash_ask"] == 100
    assert result.as_dict()["future_bid"] == 106
    assert result.as_dict()["cash_ltp"] == 99.5
    assert result.as_dict()["future_ltp"] == 106.5

def test_threshold_filters_before_qualified_snapshot():
    scanner = CashFutureOpportunityScanner(minimum_gap_points=10, minimum_gross_profit=1)
    cash = rec("C1", "ABC-EQ", InstrumentType.EQUITY, 100, 99, 100, underlying="ABC")
    future = rec("F1", "ABC-FUT", InstrumentType.FUTURE, 100, 106, 107, lot=60, underlying="ABC")
    result = scanner.update(cash, contract_month="CASH")
    assert result is None
    result = scanner.update(future, contract_month="CURRENT")
    assert result is not None
    assert result.signal.qualifies is False
    assert scanner.snapshot() == ()

def test_stale_timestamp_is_not_paired():
    scanner = CashFutureOpportunityScanner()
    cash = rec("C1", "ABC-EQ", InstrumentType.EQUITY, 100, 99, 100, underlying="ABC")
    future = rec("F1", "ABC-FUT", InstrumentType.FUTURE, 2_000_000_000, 106, 107, lot=60, underlying="ABC")
    scanner.update(cash, contract_month="CASH")
    assert scanner.update(future, contract_month="CURRENT") is None

def test_fresh_timestamp_skew_is_paired():
    scanner = CashFutureOpportunityScanner(minimum_gap_points=5, minimum_gross_profit=300)
    cash = rec("C1", "ABC-EQ", InstrumentType.EQUITY, 1_000_000_000, 99, 100, underlying="ABC")
    future = rec("F1", "ABC-FUT", InstrumentType.FUTURE, 1_500_000_000, 106, 107, lot=60, underlying="ABC")
    scanner.update(cash, contract_month="CASH")
    result = scanner.update(future, contract_month="CURRENT")
    assert result is not None
    assert result.signal.gap_points == 6

def test_near_contract_is_evaluated_even_when_current_exists():
    scanner = CashFutureOpportunityScanner()
    cash = rec("C1", "ABC-EQ", InstrumentType.EQUITY, 1_000_000_000, 99, 100, underlying="ABC")
    current = rec("F1", "ABC-CURRENT", InstrumentType.FUTURE, 1_000_000_100, 101, 102, lot=60, underlying="ABC")
    near = rec("F2", "ABC-NEAR", InstrumentType.FUTURE, 1_000_000_200, 108, 109, lot=60, underlying="ABC")
    scanner.update(cash, contract_month="CASH")
    scanner.update(current, contract_month="CURRENT")
    result = scanner.update(near, contract_month="NEAR")
    assert result is not None
    assert result.contract_month == "NEAR"
    assert result.signal.gap_points == 8

def test_snapshot_clears_opportunity_when_quote_no_longer_qualifies():
    scanner = CashFutureOpportunityScanner(minimum_gap_points=5)
    cash = rec("C1", "ABC-EQ", InstrumentType.EQUITY, 1_000_000_000, 99, 100, underlying="ABC")
    future_good = rec("F1", "ABC-FUT", InstrumentType.FUTURE, 1_000_000_100, 106, 107, lot=60, underlying="ABC")
    scanner.update(cash, contract_month="CASH")
    result = scanner.update(future_good, contract_month="CURRENT")
    assert result is not None
    assert scanner.snapshot() == (result,)

    future_bad = rec("F1", "ABC-FUT", InstrumentType.FUTURE, 1_500_000_000, 100, 101, lot=60, underlying="ABC")
    assert scanner.update(future_bad, contract_month="CURRENT") is None
    assert scanner.snapshot() == ()


def test_crossed_quote_rejected():
    scanner = CashFutureOpportunityScanner()
    try:
        cash = rec("C1", "ABC-EQ", InstrumentType.EQUITY, 100, 101, 100, underlying="ABC")
    except ValueError:
        return
    raise AssertionError("crossed quote must be rejected by MarketDataRecord")


def test_reverse_direction_uses_cash_bid_and_future_ask():
    scanner = CashFutureOpportunityScanner(minimum_gap_points=4, minimum_gross_profit=200)
    cash = rec("C2", "XYZ-EQ", InstrumentType.EQUITY, 200, 110, 111, underlying="XYZ")
    future = rec("F2", "XYZ-FUT", InstrumentType.FUTURE, 200, 105, 106, lot=50, underlying="XYZ")
    scanner.update(cash, contract_month="CASH")
    result = scanner.update(future, contract_month="CURRENT")
    assert result is not None
    assert result.direction == "CASH_SELL_FUTURE_BUY"
    assert result.signal.gap_points == 4
    assert result.signal.gross_profit == 200
    assert result.signal.qualifies is True
