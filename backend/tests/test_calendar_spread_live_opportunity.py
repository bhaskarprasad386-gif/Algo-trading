from app.market_data.contracts import InstrumentKey, InstrumentType, MarketDataRecord
from app.scanner.live_calendar_spread_scanner import LiveCalendarSpreadScanner

def rec(token, symbol, ts, bid, ask, lot=50, underlying="NIFTY", expiry="2026-10-29"):
    return MarketDataRecord(
        instrument=InstrumentKey("NFO","NFO",token),
        symbol=symbol, instrument_type=InstrumentType.FUTURE, timestamp_ns=ts,
        ltp=(bid+ask)/2, bid=bid, ask=ask, bid_qty=100, ask_qty=100,
        underlying=underlying, expiry=expiry, lot_size=lot,
    )

def test_calendar_uses_executable_bid_ask_and_gross_profit():
    scanner=LiveCalendarSpreadScanner(minimum_gap_points=3, minimum_gross_profit=150)
    near=rec("1","NIFTY-CUR",1,99,100)
    far=rec("2","NIFTY-NEAR",1,104,105,expiry="2026-11-26")
    result=scanner.update(near)
    assert result is None
    result=scanner.update(far)
    assert result is not None
    assert result.direction=="LONG_NEAR_SHORT_FAR"
    assert result.gap_points==4
    assert result.gross_profit==200
    assert result.qualifies is True
    assert result.signal.legs[0].executable_price==100
    assert result.signal.legs[1].executable_price==104

def test_calendar_reverse_direction_and_threshold_filter():
    scanner=LiveCalendarSpreadScanner(minimum_gap_points=5, minimum_gross_profit=1)
    near=rec("1","NIFTY-CUR",2,105,106)
    far=rec("2","NIFTY-NEAR",2,99,100,expiry="2026-11-26")
    scanner.update(near)
    result=scanner.update(far)
    assert result is not None
    assert result.direction=="SHORT_NEAR_LONG_FAR"
    assert result.gap_points==5
    assert result.gross_profit==250
    assert result.qualifies is True
    assert scanner.snapshot()[0].gross_profit==250

def test_calendar_subsecond_timestamp_skew_is_paired():
    scanner=LiveCalendarSpreadScanner()
    scanner.update(rec("1","NIFTY-CUR",3_000_000_000,99,100))
    result=scanner.update(rec("2","NIFTY-NEAR",3_250_000_000,104,105,expiry="2026-11-26"))
    assert result is not None
    assert result.gap_points == 4


def test_calendar_timestamp_skew_beyond_tolerance_is_not_paired():
    scanner=LiveCalendarSpreadScanner()
    scanner.update(rec("1","NIFTY-CUR",3_000_000_000,99,100))
    assert scanner.update(rec("2","NIFTY-NEAR",3_500_000_001,104,105,expiry="2026-11-26")) is None

def test_calendar_crossed_quote_is_rejected_by_canonical_contract():
    scanner=LiveCalendarSpreadScanner()
    try:
        rec("1","NIFTY-CUR",5,101,100)
    except ValueError:
        return
    raise AssertionError("crossed quote must be rejected")
