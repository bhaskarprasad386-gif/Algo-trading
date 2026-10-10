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

    def family_record(kind, token, expiry, timestamp, bid=99.0, ask=100.0):
        return MarketDataRecord(
            instrument=InstrumentKey(exchange="MCX", segment="MCX", token=token),
            symbol="CRUDEOIL" + expiry,
            instrument_type=kind,
            timestamp_ns=timestamp,
            timeframe="1s",
            ltp=100.0,
            bid=bid,
            ask=ask,
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
    paired = scanner.update(family_record(InstrumentType.FUTURE, "f2", "2026-11-26", 3_200_000_000, bid=104.0, ask=105.0))
    assert paired is not None
    assert paired.near_contract_month == "2026-10-29"
    assert paired.far_contract_month == "2026-11-26"



def test_calendar_scanner_skips_pair_when_both_executable_edges_are_non_positive():
    scanner = LiveCalendarSpreadScanner()
    near = rec("no-edge-near", "NIFTY-CUR", 3_000_000_000, 99, 100, expiry="2026-10-29")
    far = rec("no-edge-far", "NIFTY-NEAR", 3_100_000_000, 99, 100, expiry="2026-11-26")

    assert scanner.update(near) is None
    assert scanner.update(far) is None
    assert scanner.pair_snapshot() == ()



def test_calendar_rejects_materially_future_dated_live_tick():
    import time

    scanner = LiveCalendarSpreadScanner()
    future_timestamp = time.time_ns() + 10_000_000_000
    assert scanner.update(rec("future", "NIFTY-CUR", future_timestamp, 99, 100)) is None
    assert scanner.pair_snapshot() == ()



def test_calendar_scanner_rejects_zero_bid_or_ask_quotes():
    scanner = LiveCalendarSpreadScanner()
    assert scanner.update(rec("zero-bid", "NIFTY-CUR", 1, 0, 100)) is None
    assert scanner.update(rec("zero-ask", "NIFTY-NEAR", 2, 0, 0, expiry="2026-11-26")) is None


def test_calendar_scanner_does_not_cross_pair_index_and_stock_future_families():
    from dataclasses import replace

    scanner = LiveCalendarSpreadScanner()

    index_near = replace(
        rec("index-near", "SAME-INDEX-NEAR", 3_000_000_000, 99, 100, lot=50, underlying="SAME", expiry="2026-10-29"),
        payload={"contract_family": "INDEX_FUTURE"},
    )
    stock_near = replace(
        rec("stock-near", "SAME-STOCK-NEAR", 3_100_000_000, 99, 100, lot=50, underlying="SAME", expiry="2026-10-22"),
        payload={"contract_family": "STOCK_FUTURE"},
    )
    index_far = replace(
        rec("index-far", "SAME-INDEX-FAR", 3_200_000_000, 104, 105, lot=50, underlying="SAME", expiry="2026-11-26"),
        payload={"contract_family": "INDEX_FUTURE"},
    )

    assert scanner.update(index_near) is None
    assert scanner.update(stock_near) is None
    paired = scanner.update(index_far)

    assert paired is not None
    assert paired.near_contract_month == "2026-10-29"
    assert paired.far_contract_month == "2026-11-26"


def test_calendar_invalidates_old_signal_when_latest_pair_has_no_positive_edge():
    import time

    scanner = LiveCalendarSpreadScanner()
    # Keep this invalidation test's quotes inside the snapshot's non-future window.
    ts = time.time_ns() - 500_000_000
    near = rec("invalidate-near", "NIFTY-CUR", ts, 99, 100, expiry="2026-10-29")
    far = rec("invalidate-far", "NIFTY-NEAR", ts + 100_000_000, 104, 105, expiry="2026-11-26")
    scanner.update(near)
    first = scanner.update(far)
    assert first is not None
    assert scanner.snapshot()

    updated_near = rec("invalidate-near", "NIFTY-CUR", ts + 200_000_000, 99, 110, expiry="2026-10-29")
    assert scanner.update(updated_near) is None
    updated_far = rec("invalidate-far", "NIFTY-NEAR", ts + 200_000_000, 99, 100, expiry="2026-11-26")
    assert scanner.update(updated_far) is None
    assert scanner.snapshot() == ()
    assert scanner.pair_snapshot() == ()
    diagnostics = scanner.diagnostics_snapshot()
    assert diagnostics["counters"]["no_positive_edge"] == 2  # both updated legs trigger a no-edge evaluation
    assert diagnostics["pairs"][0]["status"] == "no_positive_edge"



def test_calendar_diagnostics_keep_status_with_allowed_future_timestamp():
    import time

    scanner = LiveCalendarSpreadScanner()
    ts = time.time_ns() + 500_000_000
    near = rec("future-near", "NIFTY-CUR", ts, 99, 100, expiry="2026-10-29")
    far = rec("future-far", "NIFTY-NEAR", ts + 100_000_000, 99, 100, expiry="2026-11-26")

    scanner.update(near)
    scanner.update(far)

    diagnostics = scanner.diagnostics_snapshot()
    assert diagnostics["pairs"]
    assert diagnostics["pairs"][0]["status"] == "no_positive_edge"


def test_calendar_positive_gross_edge_without_two_sided_depth_does_not_qualify():
    from dataclasses import replace
    import time

    scanner = LiveCalendarSpreadScanner()
    ts = time.time_ns()
    near = rec("depth-near", "NIFTY-CUR", ts, 99, 100, expiry="2026-10-29")
    far = replace(rec("depth-far", "NIFTY-NEAR", ts + 100_000_000, 104, 105, expiry="2026-11-26"), bid_qty=0)
    scanner.update(near)
    result = scanner.update(far)
    assert result is None
    assert scanner.snapshot() == ()
    assert scanner.diagnostics_snapshot()["counters"]["insufficient_depth"] == 1


def test_calendar_preserves_original_leg_timestamps_and_family_in_signal_metadata():
    from dataclasses import replace
    import time

    scanner = LiveCalendarSpreadScanner()
    ts = time.time_ns()
    near = replace(
        rec("time-near", "NIFTY-CUR", ts, 99, 100, expiry="2026-10-29"),
        payload={"contract_family": "INDEX_FUTURE"},
    )
    far_ts = ts + 250_000_000
    far = replace(
        rec("time-far", "NIFTY-NEAR", far_ts, 104, 105, expiry="2026-11-26"),
        payload={"contract_family": "INDEX_FUTURE"},
    )
    scanner.update(near)
    result = scanner.update(far)
    assert result is not None
    assert result.contract_family == "INDEX_FUTURE"
    assert result.near_timestamp_ns == ts
    assert result.far_timestamp_ns == far_ts
    assert result.timestamp_skew_ns == 250_000_000
    assert result.signal.metadata["near_timestamp_ns"] == ts
    assert result.signal.metadata["far_timestamp_ns"] == far_ts
    assert result.signal.metadata["profit_basis"] == "gross_before_fees_and_slippage"


def test_canonical_executable_quote_rejects_zero_bid_and_zero_ask():
    from dataclasses import replace

    valid = rec("canonical-depth", "NIFTY-CUR", 3_000_000_000, 99, 100)
    assert valid.is_executable_quote is True
    assert replace(valid, bid=0).is_executable_quote is False
    import pytest
    with pytest.raises(ValueError, match="bid cannot exceed ask"):
        replace(valid, ask=0)


def test_calendar_invalid_latest_quote_removes_that_expiry_from_pairing_state():
    import time

    scanner = LiveCalendarSpreadScanner()
    ts = time.time_ns()
    near = rec("invalid-near", "NIFTY-CUR", ts, 99, 100, expiry="2026-10-29")
    far = rec("invalid-far", "NIFTY-NEAR", ts + 100_000_000, 104, 105, expiry="2026-11-26")
    scanner.update(near)
    assert scanner.update(far) is not None

    invalid_near = rec("invalid-near", "NIFTY-CUR", ts + 200_000_000, 0, 100, expiry="2026-10-29")
    assert scanner.update(invalid_near) is None
    assert scanner.snapshot() == ()
    # A subsequent far-leg update must not pair against the removed stale near quote.
    next_far = rec("invalid-far", "NIFTY-NEAR", ts + 300_000_000, 104, 105, expiry="2026-11-26")
    assert scanner.update(next_far) is None
    assert scanner.snapshot() == ()


def test_calendar_capacity_uses_calendar_specific_capital(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "LIVE_CALENDAR_SPREAD_CAPITAL", 1_050_000.0)
    monkeypatch.setattr(settings, "LIVE_CASH_FUTURE_CAPITAL", 100.0)
    scanner = LiveCalendarSpreadScanner()
    near = rec("capital-near", "NIFTY-CUR", 1, 99, 100)
    far = rec("capital-far", "NIFTY-NEAR", 1, 104, 105, expiry="2026-11-26")
    scanner.update(near)
    result = scanner.update(far)

    assert result is not None
    assert result.capacity_lots == 200  # 1,050,000 / (105 * 50)


def test_calendar_rejects_pair_when_stored_counterpart_has_aged_out(monkeypatch):
    import time

    real_now = time.time_ns()
    fake_now = [real_now]
    monkeypatch.setattr(time, "time_ns", lambda: fake_now[0])
    scanner = LiveCalendarSpreadScanner()
    near = rec("stale-near", "NIFTY-CUR", real_now, 99, 100, expiry="2026-10-29")
    assert scanner.update(near) is None

    # The incoming far quote is individually fresh (<5s), and its skew is
    # within 1s, but the stored near quote is already stale (>5s).
    fake_now[0] = real_now + 5_500_000_000
    far = rec("fresh-far", "NIFTY-NEAR", real_now + 900_000_000, 104, 105, expiry="2026-11-26")
    assert scanner.update(far) is None
    assert scanner.pair_snapshot() == ()
    diagnostics = scanner.diagnostics_snapshot()
    assert diagnostics["counters"]["stale_pair"] == 1
    assert diagnostics["pairs"][0]["status"] == "stale_pair"


def test_calendar_result_persistence_applies_configured_retention(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.core.config import settings
    from app.core.database import Base
    from app.models.live_calendar_spread_scanner_result import LiveCalendarSpreadScannerResult

    monkeypatch.setattr(settings, "LIVE_CALENDAR_SPREAD_RESULT_RETENTION_DAYS", 90)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db = Session()
    db.add(LiveCalendarSpreadScannerResult(
        underlying="OLD", exchange="NFO", instrument_type="future", contract_family="INDEX_FUTURE",
        near_contract_month="2026-01-29", far_contract_month="2026-02-26", timestamp_ns=1,
        near_bid=99, near_ask=100, far_bid=104, far_ask=105, lot_size=50,
        edge_long=4, edge_short=-6, edge_pct_long=4, edge_pct_short=-6,
        liquidity_qty=10, capacity_lots=1, rank_score=0.04,
        observed_at=now - timedelta(days=91),
    ))
    db.commit()
    db.close()

    scanner = LiveCalendarSpreadScanner()
    scanner.update(rec("retention-near", "NIFTY-CUR", 1, 99, 100, expiry="2026-10-29"))
    result = scanner.update(rec("retention-far", "NIFTY-NEAR", 1, 104, 105, expiry="2026-11-26"))
    assert result is not None
    scanner._persist(result, Session)

    db = Session()
    try:
        rows = db.query(LiveCalendarSpreadScannerResult).all()
        assert len(rows) == 1
        assert rows[0].underlying == "NIFTY"
    finally:
        db.close()
        engine.dispose()
