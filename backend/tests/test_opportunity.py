from app.market_data.contracts import InstrumentKey, InstrumentType, MarketDataRecord
from app.market_data.opportunity import OpportunityLeg, OpportunitySignal, OrderSide, executable_price, gross_profit_from_points, qualifies_opportunity, validate_opportunity_legs

def rec(token, ts, bid=99.0, ask=101.0):
    return MarketDataRecord(
        instrument=InstrumentKey("NSE","NFO",str(token)), symbol="TEST",
        instrument_type=InstrumentType.FUTURE, timestamp_ns=ts,
        ltp=100.0, bid=bid, ask=ask, lot_size=10)

def test_executable_sides():
    r=rec(1,1)
    assert executable_price(r,OrderSide.BUY)==101.0
    assert executable_price(r,OrderSide.SELL)==99.0

def test_missing_quote_rejected():
    r=rec(2,1)
    r=MarketDataRecord(instrument=r.instrument,symbol=r.symbol,instrument_type=r.instrument_type,timestamp_ns=1,lot_size=10)
    try: executable_price(r,OrderSide.BUY)
    except ValueError as e: assert "bid/ask" in str(e)
    else: raise AssertionError("missing quote must fail")

def test_timestamp_validation():
    legs=(OpportunityLeg(rec(3,1),OrderSide.BUY,"buy"),OpportunityLeg(rec(4,2),OrderSide.SELL,"sell"))
    try: validate_opportunity_legs(legs)
    except ValueError as e: assert "same timestamp" in str(e)
    else: raise AssertionError("timestamp mismatch must fail")

def test_timestamp_skew_can_be_configured():
    validate_opportunity_legs((OpportunityLeg(rec(5,100),OrderSide.BUY,"buy"),OpportunityLeg(rec(6,150),OrderSide.SELL,"sell")),max_timestamp_skew_ns=50)

def test_gross_profit():
    assert gross_profit_from_points(5.0,65)==325.0
    assert gross_profit_from_points(5.0,65,2)==650.0

def test_thresholds_are_inclusive():
    assert qualifies_opportunity(gap_points=5,gross_profit=325,minimum_gap_points=5,minimum_gross_profit=325)
    assert not qualifies_opportunity(gap_points=4.99,gross_profit=325,minimum_gap_points=5,minimum_gross_profit=325)
    assert not qualifies_opportunity(gap_points=5,gross_profit=324.99,minimum_gap_points=5,minimum_gross_profit=325)

def test_signal_contract():
    legs=(OpportunityLeg(rec(7,1),OrderSide.BUY,"entry"),OpportunityLeg(rec(8,1),OrderSide.SELL,"exit"))
    s=OpportunitySignal(strategy_id="cash-future",opportunity_type="carry",symbol="TEST",timestamp_ns=1,gap_points=5,gross_profit=325,lot_size=65,qualifies=True,minimum_gap_points=5,minimum_gross_profit=300,legs=legs,expiry="2026-10-29",metadata={"source":"common"})
    assert s.identity==("cash-future","carry","TEST",1)
    assert s.as_dict()["legs"][0]["side"]=="BUY"

def test_signal_rejects_inconsistent_qualification():
    try:
        OpportunitySignal(strategy_id="calendar",opportunity_type="spread",symbol="TEST",timestamp_ns=1,gap_points=2,gross_profit=100,lot_size=10,qualifies=True,minimum_gap_points=5)
    except ValueError as e: assert "qualifies does not match" in str(e)
    else: raise AssertionError("inconsistent qualification must fail")
