from app.backtesting.arbitrage_backtester import OptionQuote
from app.backtesting.arbitrage_scan_policy import ScanPolicy
from app.scanner.box_spread import BoxSpreadScanConfig, scan_box_snapshot
from app.scanner.live_box_spread_scanner import LiveBoxSpreadScanner

def q(ts,strike,cb,ca,pb,pa):
    return OptionQuote(ts,"ABC",20261231,strike,cb,ca,pb,pa,10,"STOCK",100,100)

def test_box_live_scanner_uses_real_bid_ask_for_both_directions():
    quotes=[q(1,100,100,100.1,99.1,99.2),q(1,110,98.1,98.2,102,102.1)]
    result=scan_box_snapshot(
        quotes,atm_strike=100,instrument_class="STOCK",
        config=BoxSpreadScanConfig(allowed_stock_symbols=frozenset({"ABC"}), max_option_spread_pct=5.0),
        policy=ScanPolicy(stock_box_distances=(1,)),
    )
    # LONG debit = (100.1 - 98.1) + (102.1 - 99.1) = 5.
    # Narrow spreads satisfy the 5% liquidity guard. Width 10 => executable edge 5.
    assert len(result) == 1
    assert result[0].direction == "LONG"
    assert result[0].executable_edge == 5

def test_live_box_scanner_uses_weaker_ce_pe_liquidity():
    scanner = LiveBoxSpreadScanner(
        atm_provider=lambda _s, _t: 100.0,
        config_provider=lambda _s: BoxSpreadScanConfig(
            allowed_stock_symbols=frozenset({"ABC"}),
            min_option_volume=10,
            min_option_oi=10,
        ),
        policy=ScanPolicy(stock_box_distances=(1,)),
    )
    base = {
        "underlying": "ABC", "instrument_class": "STOCK",
        "source_timestamp_ns": 1, "expiry": "31DEC2026",
        "strike": 100, "lot_size": 10, "volume": 100, "oi": 100,
    }
    scanner.observe({**base, "option_type": "CE", "bid": 10, "ask": 10.1})
    scanner.observe({**base, "option_type": "PE", "bid": 1, "ask": 1.01, "volume": 1})
    upper = {**base, "strike": 110}
    scanner.observe({**upper, "option_type": "CE", "bid": 8, "ask": 8.1})
    results = scanner.observe({**upper, "option_type": "PE", "bid": 2, "ask": 2.01})
    assert results == ()


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


def _delayed_fourth_leg_scanner():
    return LiveBoxSpreadScanner(
        atm_provider=lambda _s, _t: 100.0,
        config_provider=lambda _s: BoxSpreadScanConfig(
            allowed_stock_symbols=frozenset({"ABC"}),
            min_option_volume=1,
            min_option_oi=1,
        ),
        policy=ScanPolicy(stock_box_distances=(1,)),
    )


def _delayed_leg_base(ts, strike, option_type, **overrides):
    payload = {
        "underlying": "ABC",
        "instrument_class": "STOCK",
        "source_timestamp_ns": ts,
        "expiry": "31DEC2026",
        "strike": strike,
        "option_type": option_type,
        "lot_size": 10,
        "volume": 100,
        "oi": 100,
    }
    payload.update(overrides)
    return payload


def test_box_scanner_waits_three_seconds_for_missing_fourth_leg_liquidity():
    scanner = _delayed_fourth_leg_scanner()
    t = 10_000_000_000
    scanner.observe(_delayed_leg_base(t, 100, "CE", bid=100, ask=100.1))
    scanner.observe(_delayed_leg_base(t + 100_000_000, 100, "PE", bid=99.1, ask=99.2))
    scanner.observe(_delayed_leg_base(t + 200_000_000, 110, "CE", bid=98.1, ask=98.2))
    # Fourth leg is identified, but has no executable quote/liquidity yet.
    assert scanner.observe(_delayed_leg_base(
        t + 300_000_000, 110, "PE", bid=None, ask=None, volume=0, oi=0
    )) == ()
    # It becomes liquid two seconds later: use the same candidate and accept.
    results = scanner.observe(_delayed_leg_base(
        t + 2_000_000_000, 110, "PE", bid=102, ask=102.1
    ))
    assert any(result.direction == "LONG" for result in results)


def test_box_scanner_rejects_missing_fourth_leg_after_three_second_window():
    scanner = _delayed_fourth_leg_scanner()
    t = 10_000_000_000
    scanner.observe(_delayed_leg_base(t, 100, "CE", bid=100, ask=100.1))
    scanner.observe(_delayed_leg_base(t + 100_000_000, 100, "PE", bid=99.1, ask=99.2))
    scanner.observe(_delayed_leg_base(t + 200_000_000, 110, "CE", bid=98.1, ask=98.2))
    scanner.observe(_delayed_leg_base(
        t + 300_000_000, 110, "PE", bid=None, ask=None, volume=0, oi=0
    ))
    # A late quote after the deadline starts a fresh bucket and cannot combine
    # with the expired three-leg candidate.
    results = scanner.observe(_delayed_leg_base(
        t + 3_500_000_000, 110, "PE", bid=102, ask=102.1
    ))
    assert results == ()


def test_box_scanner_does_not_replace_newer_leg_with_out_of_order_quote():
    scanner = _delayed_fourth_leg_scanner()
    t = 10_000_000_000
    scanner.observe(_delayed_leg_base(t + 1_000_000_000, 100, "CE", bid=100, ask=100.1))
    # Older invalid update for the same contract must not erase its newer quote.
    assert scanner.observe(_delayed_leg_base(
        t + 500_000_000, 100, "CE", bid=None, ask=None, volume=0, oi=0
    )) == ()
    scanner.observe(_delayed_leg_base(t + 1_100_000_000, 100, "PE", bid=99.1, ask=99.2))
    scanner.observe(_delayed_leg_base(t + 1_200_000_000, 110, "CE", bid=98.1, ask=98.2))
    results = scanner.observe(_delayed_leg_base(
        t + 1_300_000_000, 110, "PE", bid=102, ask=102.1
    ))
    assert any(result.direction == "LONG" for result in results)
