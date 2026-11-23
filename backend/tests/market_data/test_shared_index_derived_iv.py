from datetime import datetime
from zoneinfo import ZoneInfo
from time import time_ns
from app.market_data.shared_index_derived_iv import implied_volatility, SharedIndexDerivedIV

IST=ZoneInfo("Asia/Kolkata")

def test_iv_inversion_for_call_and_put():
    now=datetime(2026,11,22,10,0,tzinfo=IST)
    call=implied_volatility(220,22000,22000,"2026-11-26","CE",now=now)
    put=implied_volatility(215,22000,22000,"2026-11-26","PE",now=now)
    assert call is not None and 1 < call < 500
    assert put is not None and 1 < put < 500

def test_invalid_and_expired_inputs_return_none():
    now=datetime(2026,11,27,10,0,tzinfo=IST)
    assert implied_volatility(50,22000,22000,"2026-11-26","CE",now=now) is None
    assert implied_volatility(0,22000,22000,"2026-11-30","CE",now=now) is None
    assert implied_volatility(50,0,22000,"2026-11-30","CE",now=now) is None

def test_iv_velocity_requires_one_second_baseline():
    calc=SharedIndexDerivedIV()
    base=time_ns()
    row={"exchange":"NFO","segment":"NFO","token":"123","instrument_type":"option",
          "underlying":"NIFTY","strike":22000,"expiry":"2026-11-26","option_type":"CE",
          "ltp":220,"fresh":True,"timestamp_ns":base}
    first=calc.decorate([row],{"NIFTY":22000})[0]
    assert first["iv_source"]=="derived_bsm"
    assert first["iv_delta_1s"] is None
    later=dict(row,timestamp_ns=base+1_100_000_000,ltp=225)
    second=calc.decorate([later],{"NIFTY":22000})[0]
    assert second["iv_delta_1s"] is not None
