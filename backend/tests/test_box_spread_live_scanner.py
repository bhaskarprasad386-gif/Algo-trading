from app.backtesting.arbitrage_backtester import OptionQuote
from app.scanner.box_spread import scan_box_snapshot
from app.scanner.live_box_spread_scanner import LiveBoxSpreadScanner

def q(ts,strike,cb,ca,pb,pa):
    return OptionQuote(ts,"ABC",20261231,strike,cb,ca,pb,pa,10)

def test_box_live_scanner_uses_real_bid_ask_for_both_directions():
    quotes=[q(1,100,10,11,1,2),q(1,110,8,9,2,3)]
    result=scan_box_snapshot(quotes,atm_strike=100,instrument_class="STOCK",
        config=__import__("app.scanner.box_spread",fromlist=["BoxSpreadScanConfig"]).BoxSpreadScanConfig(
            allowed_stock_symbols=frozenset({"ABC"})),
        policy=__import__("app.backtesting.arbitrage_scan_policy",fromlist=["ScanPolicy"]).ScanPolicy(stock_box_distances=(1,)))
    assert any(r.direction=="LONG" and r.executable_edge==6 for r in result)

def test_live_box_scanner_waits_for_matching_ce_pe_pairs():
    scanner=LiveBoxSpreadScanner(
        atm_provider=lambda _s,_t:100.0,
        config_provider=lambda _s: __import__("app.scanner.box_spread",fromlist=["BoxSpreadScanConfig"]).BoxSpreadScanConfig(allowed_stock_symbols=frozenset({"ABC"})),
        policy=__import__("app.backtesting.arbitrage_scan_policy",fromlist=["ScanPolicy"]).ScanPolicy(stock_box_distances=(1,)))
    base={"underlying":"ABC","instrument_class":"STOCK","source_timestamp_ns":1,"expiry":"31DEC2026","strike":100,"lot_size":10,"volume":100,"oi":100}
    assert scanner.observe({**base,"option_type":"CE","bid":10,"ask":11})==()
    result=scanner.observe({**base,"option_type":"PE","bid":1,"ask":2})
    assert result==()
