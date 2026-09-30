from datetime import date
from app.market_data.contract_resolver import DynamicContractResolver

def test_resolver_selects_current_and_near():
    rows=[{"token":"old","symbol":"ABC","name":"ABC","instrumenttype":"FUTSTK","expiry":"25SEP2026","exch_seg":"NFO"},{"token":"cur","symbol":"ABC","name":"ABC","instrumenttype":"FUTSTK","expiry":"30SEP2026","exch_seg":"NFO"},{"token":"near","symbol":"ABC","name":"ABC","instrumenttype":"FUTSTK","expiry":"28OCT2026","exch_seg":"NFO"},{"token":"next","symbol":"ABC","name":"ABC","instrumenttype":"FUTSTK","expiry":"25NOV2026","exch_seg":"NFO"}]
    assert [c.token for c in DynamicContractResolver(rows,date(2026,9,29)).resolve_futures("ABC")]==["cur","near"]
def test_resolver_returns_atm_window_both_sides():
    rows=[{"token":f"{s}{o}","symbol":f"ABC{s}{o}","name":"ABC","instrumenttype":"OPTSTK","expiry":"30SEP2026","strike":s,"optiontype":o,"exch_seg":"NFO"} for s in (100,110,120,130,140) for o in ("CE","PE")]
    assert [c.strike for c in DynamicContractResolver(rows,date(2026,9,29)).resolve_options("ABC",120,1)]==[110,110,120,120,130,130]
def test_resolver_skips_missing_token_and_invalid_option_side():
    rows=[{"symbol":"ABC","name":"ABC","instrumenttype":"OPTSTK","expiry":"30SEP2026","strike":120,"optiontype":"CE","exch_seg":"NFO"},{"token":"bad","symbol":"ABC","name":"ABC","instrumenttype":"OPTSTK","expiry":"30SEP2026","strike":120,"optiontype":"XX","exch_seg":"NFO"}]
    assert DynamicContractResolver(rows,date(2026,9,29)).resolve_options("ABC",120)==()
