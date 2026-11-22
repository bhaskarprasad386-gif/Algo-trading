from app.scanner.short_covering_long_unwinding.liquidity import detect_sweep, detect_reclaim, event_is_fresh

def test_bullish_sweep_then_reclaim():
    sweep=detect_sweep(price=99.5,level=100,side="BULLISH",timestamp_ms=1000,min_sweep=.25)
    assert sweep and sweep.event_type=="LIQUIDITY_SWEEP"
    reclaim=detect_reclaim(price=100.2,level=100,side="BULLISH",timestamp_ms=1800,sweep=sweep)
    assert reclaim and reclaim.event_type=="SWEEP_RECLAIM"
    assert event_is_fresh(reclaim,2000)

def test_bearish_sweep_then_down_rejection():
    sweep=detect_sweep(price=101.2,level=100,side="BEARISH",timestamp_ms=1000,min_sweep=.3)
    assert sweep is not None
    rejected=detect_reclaim(price=99.8,level=100,side="BEARISH",timestamp_ms=2200,sweep=sweep)
    assert rejected is not None and rejected.side=="BEARISH"

def test_liquidity_events_fail_closed_invalid_and_expired():
    assert detect_sweep(price="bad",level=100,side="BULLISH",timestamp_ms=1000,min_sweep=.2) is None
    assert detect_sweep(price=99.9,level=100,side="BULLISH",timestamp_ms=1000,min_sweep=.2) is None
    sweep=detect_sweep(price=99,level=100,side="BULLISH",timestamp_ms=1000,min_sweep=.2,ttl_ms=100)
    assert sweep
    assert detect_reclaim(price=100.1,level=100,side="BEARISH",timestamp_ms=1050,sweep=sweep) is None
    assert detect_reclaim(price=100.1,level=100,side="BULLISH",timestamp_ms=1200,sweep=sweep) is None
    assert not event_is_fresh(sweep,900)
    assert not event_is_fresh(sweep,1200)

def test_cross_sweep_requires_fresh_level_crossing():
    from app.scanner.short_covering_long_unwinding.liquidity import detect_cross_sweep
    assert detect_cross_sweep(previous_price=100.1,price=99.6,level=100,side="BULLISH",timestamp_ms=500,min_sweep=.2)
    assert detect_cross_sweep(previous_price=99.7,price=99.4,level=100,side="BULLISH",timestamp_ms=500,min_sweep=.2) is None
    assert detect_cross_sweep(previous_price=99.9,price=100.5,level=100,side="BEARISH",timestamp_ms=500,min_sweep=.2) is None
    assert detect_cross_sweep(previous_price=99.9,price=100.3,level=100,side="BEARISH",timestamp_ms=500,min_sweep=.2)
