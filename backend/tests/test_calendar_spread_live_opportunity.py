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
    import time
    ts = time.time_ns()
    near=rec("1","NIFTY-CUR",ts,105,106)
    far=rec("2","NIFTY-NEAR",ts,99,100,expiry="2026-11-26")
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
    assert scanner.update(rec("2","NIFTY-NEAR",4_000_000_001,104,105,expiry="2026-11-26")) is None

def test_calendar_crossed_quote_is_rejected_by_canonical_contract():
    scanner=LiveCalendarSpreadScanner()
    try:
        rec("1","NIFTY-CUR",5,101,100)
    except ValueError:
        return
    raise AssertionError("crossed quote must be rejected")



def test_calendar_scanner_does_not_pair_future_and_commodity_families():
    from app.market_data.contracts import InstrumentKey, InstrumentType, MarketDataRecord

    scanner = LiveCalendarSpreadScanner()

    def family_record(kind, token, expiry, timestamp):
        return MarketDataRecord(
            instrument=InstrumentKey(exchange="MCX", segment="MCX", token=token),
            symbol="CRUDEOIL" + expiry,
            instrument_type=kind,
            timestamp_ns=timestamp,
            timeframe="1s",
            ltp=100.0,
            bid=99.0,
            ask=100.0,
            bid_qty=10,
            ask_qty=10,
            underlying="CRUDEOIL",
            expiry=expiry,
            lot_size=100,
            tick_size=0.05,
        )

    scanner.update(family_record(InstrumentType.FUTURE, "f1", "2026-10-29", 3_000_000_000))
    # Same underlying/exchange and an earlier expiry, but a different family.
    assert scanner.update(family_record(InstrumentType.COMMODITY, "c1", "2026-10-22", 3_100_000_000)) is None
    # A second future should pair with the first future, not the commodity.
    paired = scanner.update(family_record(InstrumentType.FUTURE, "f2", "2026-11-26", 3_200_000_000))
    assert paired is not None
    assert paired.near_contract_month == "2026-10-29"
    assert paired.far_contract_month == "2026-11-26"



def test_calendar_rejects_materially_future_dated_live_tick():
    import time

    scanner = LiveCalendarSpreadScanner()
    future_timestamp = time.time_ns() + 10_000_000_000
    assert scanner.update(rec("future", "NIFTY-CUR", future_timestamp, 99, 100)) is None
    assert scanner.pair_snapshot() == ()
