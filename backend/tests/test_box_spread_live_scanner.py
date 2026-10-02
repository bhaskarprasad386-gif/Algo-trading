from app.backtesting.arbitrage_backtester import OptionQuote
from app.backtesting.arbitrage_scan_policy import ScanPolicy
from app.scanner.box_spread import BoxSpreadScanConfig, scan_box_snapshot
from app.scanner.live_box_spread_scanner import LiveBoxSpreadScanner

def q(ts,strike,cb,ca,pb,pa):
    return OptionQuote(ts,"ABC",20261231,strike,cb,ca,pb,pa,10)

def test_box_live_scanner_uses_real_bid_ask_for_both_directions():
    quotes=[q(1,100,10,11,1,2),q(1,110,8,9,2,3)]
    result=scan_box_snapshot(
        quotes,atm_strike=100,instrument_class="STOCK",
        config=BoxSpreadScanConfig(allowed_stock_symbols=frozenset({"ABC"}), max_option_spread_pct=5.0),
        policy=ScanPolicy(stock_box_distances=(1,)),
    )
    # With executable bid/ask prices, only the LONG box is profitable here.\n    # LONG debit = 11 + 2 - 8 - 2 = 3; width = 10; edge = 7.\n    assert len(result) == 1\n    assert result[0].direction == "LONG"\n    assert result[0].executable_edge == 7

def test_live_box_scanner_waits_for_matching_ce_pe_pairs():
    scanner=LiveBoxSpreadScanner(
        atm_provider=lambda _s,_t:100.0,
        config_provider=lambda _s: BoxSpreadScanConfig(allowed_stock_symbols=frozenset({"ABC"})),
        policy=ScanPolicy(stock_box_distances=(1,)),
    )
    base={"underlying":"ABC","instrument_class":"STOCK","source_timestamp_ns":1,
          "expiry":"31DEC2026","strike":100,"lot_size":10,"volume":100,"oi":100}
    assert scanner.observe({**base,"option_type":"CE","bid":10,"ask":10.2})==()
    assert scanner.observe({**base,"option_type":"PE","bid":1,"ask":1.02})==()
    upper={**base,"strike":110}
    assert scanner.observe({**upper,"option_type":"CE","bid":8,"ask":8.2})==()
    result=scanner.observe({**upper,"option_type":"PE","bid":2,"ask":2.02})
    assert any(r.direction=="LONG" for r in result)


def test_live_box_scanner_accepts_bse_index_contract_metadata():
    scanner = LiveBoxSpreadScanner(
        atm_provider=lambda _s, _t: 80000.0,
        config_provider=lambda _s: BoxSpreadScanConfig(),
        policy=ScanPolicy(index_box_distances=(1,)),
    )
    base = {"underlying":"SENSEX","instrument_class":"INDEX","source_timestamp_ns":2,
            "expiry":"30SEP2026","lot_size":20,"volume":100,"oi":100}
    assert scanner.observe({**base,"strike":80000,"option_type":"CE","bid":100,"ask":101}) == ()
    assert scanner.observe({**base,"strike":80000,"option_type":"PE","bid":100,"ask":101}) == ()
    upper={**base,"strike":80100}
    assert scanner.observe({**upper,"option_type":"CE","bid":90,"ask":91}) == ()
    result=scanner.observe({**upper,"option_type":"PE","bid":110,"ask":111})
    assert all(r.low.instrument_class == "INDEX" for r in result)
