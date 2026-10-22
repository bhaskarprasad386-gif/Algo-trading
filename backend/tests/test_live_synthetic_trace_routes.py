from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from app.scanner import live_synthetic_trace_routes as trace_routes


def _instrument(symbol, token, instrument_type, expiry="29OCT2026", strike="0", segment="NFO"):
    return {
        "name": "NIFTY", "symbol": symbol, "token": token,
        "instrumenttype": instrument_type, "expiry": expiry,
        "strike": strike, "exch_seg": segment, "lotsize": "65",
    }


def _request(direction="LONG", entry_edge=12.5):
    return trace_routes.TraceStatusRequest(
        underlying="NIFTY", expiry="2026-10-29", strike=25000,
        direction=direction, entry_edge=entry_edge,
        trace_id="test-trace-1", lot_size=65,
        entry_future_bid=25100, entry_future_ask=25102,
        entry_call_bid=118, entry_call_ask=120,
        entry_put_bid=90, entry_put_ask=92,
    )


def test_trace_status_resolves_exact_strike_outside_scanner_range_and_calculates_direction(monkeypatch):
    monkeypatch.setattr(trace_routes, "_save_check", lambda _request, _payload: 1)
    monkeypatch.setattr(trace_routes, "_history_rows", lambda _trace_id: [{"id": 1, "pnl_per_lot": 100}])
    instruments = [
        _instrument("NIFTY29OCT26FUT", "1", "FUTIDX"),
        _instrument("NIFTY29OCT2625000CE", "2", "OPTIDX", strike="2500000"),
        _instrument("NIFTY29OCT2625000PE", "3", "OPTIDX", strike="2500000"),
    ]

    class FakeMaster:
        def __init__(self):
            self.instruments = instruments
        def download(self):
            return self.instruments

    stamp = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%d-%b-%Y %H:%M:%S")

    class FakeClient:
        def quote_many(self, exchange, contracts):
            assert exchange == "NFO"
            assert {item["symboltoken"] for item in contracts} == {"1", "2", "3"}
            return {"status": True, "data": {"fetched": [
                {"symbolToken": "1", "exchFeedTime": stamp, "depth": {"buy": [{"price": 25110}], "sell": [{"price": 25112}]}},
                {"symbolToken": "2", "exchFeedTime": stamp, "depth": {"buy": [{"price": 120}], "sell": [{"price": 122}]}},
                {"symbolToken": "3", "exchFeedTime": stamp, "depth": {"buy": [{"price": 90}], "sell": [{"price": 92}]}},
            ]}}

    monkeypatch.setattr("app.market_data.instruments.InstrumentMaster", FakeMaster)
    monkeypatch.setattr("app.market_data.client.MarketDataClient", FakeClient)

    long = trace_routes.check_trace_live_status(_request("LONG", 12.5))
    assert long["status"] == "LIVE_QUOTES"
    assert long["synthetic_bid"] == 25028
    assert long["synthetic_ask"] == 25032
    assert long["current_edge"] == -84
    assert long["entry_edge"] == 12.5
    assert long["edge_change"] == -96.5
    assert long["broker_orders"] == "OFF"

    short = trace_routes.check_trace_live_status(_request("SHORT", 12.5))
    assert short["current_edge"] == 78  # future bid minus synthetic ask
    assert short["current_edge"] == 78
    assert short["edge_change"] == 65.5


def test_trace_status_fails_closed_when_exact_option_contract_is_missing(monkeypatch):
    class FakeMaster:
        instruments = [_instrument("NIFTY29OCT26FUT", "1", "FUTIDX")]
        def download(self):
            return self.instruments

    monkeypatch.setattr("app.market_data.instruments.InstrumentMaster", FakeMaster)
    with pytest.raises(HTTPException) as exc:
        trace_routes.check_trace_live_status(_request())
    assert exc.value.status_code == 503
    assert "Exact call contract not found" in exc.value.detail


def test_trace_status_rejects_unsupported_underlying_before_market_data(monkeypatch):
    request = trace_routes.TraceStatusRequest(
        underlying="RELIANCE", expiry="2026-10-29", strike=25000,
        direction="LONG", entry_edge=10,
    )
    with pytest.raises(HTTPException) as exc:
        trace_routes.check_trace_live_status(request)
    assert exc.value.status_code == 422

def test_close_pnl_uses_direction_specific_executable_close_prices():
    long_request = _request("LONG")
    long = trace_routes._calculate_close_pnl(
        long_request,
        {"bid": 25110, "ask": 25112},
        synthetic_bid=25028,
        synthetic_ask=25032,
    )
    assert long["pnl_status"] == "GROSS_ESTIMATE"
    assert long["pnl_per_unit"] == (25110 - 25102) + ((25000 + 118 - 92) - 25032)
    assert long["pnl_per_lot"] == long["pnl_per_unit"] * 65

    short_request = _request("SHORT")
    short = trace_routes._calculate_close_pnl(
        short_request,
        {"bid": 25110, "ask": 25112},
        synthetic_bid=25028,
        synthetic_ask=25032,
    )
    assert short["pnl_per_unit"] == (25100 - 25112) + (25028 - (25000 + 120 - 90))


def test_close_pnl_is_unavailable_without_entry_snapshot():
    request = trace_routes.TraceStatusRequest(
        underlying="NIFTY", expiry="2026-10-29", strike=25000,
        direction="LONG", entry_edge=10, trace_id="missing-entry", lot_size=None,
    )
    pnl = trace_routes._calculate_close_pnl(request, {"bid": 100, "ask": 101}, 100, 101)
    assert pnl["pnl_status"] == "UNAVAILABLE"
    assert pnl["pnl_per_lot"] is None

def test_trace_quotes_without_exchange_timestamp_fail_closed():
    with pytest.raises(LookupError, match="no exchange timestamp"):
        trace_routes._validate_quote_freshness({"ltp": 100}, "future")


def test_trace_quotes_older_than_freshness_limit_fail_closed():
    old = (datetime.now(ZoneInfo("Asia/Kolkata")) - timedelta(seconds=30)).strftime("%d-%b-%Y %H:%M:%S")
    with pytest.raises(LookupError, match="is stale"):
        trace_routes._validate_quote_freshness({"exchFeedTime": old}, "call")

def test_trace_snapshot_is_saved_as_a_durable_record(monkeypatch):
    class FakeSession:
        record = None
        def get(self, _model, _key):
            return self.record
        def add(self, row):
            self.record = row
        def commit(self):
            return None
        def close(self):
            return None

    fake_db = FakeSession()
    monkeypatch.setattr("app.core.database.SessionLocal", lambda: fake_db)
    snapshot = {
        "trace_id": "trace-persist-test", "underlying": "NIFTY",
        "expiry": "2026-10-29", "strike": 25000, "direction": "LONG",
        "executable_edge": 8.5, "future_bid": 25100, "future_ask": 25102,
    }
    request = trace_routes.TraceOpportunityRequest(
        trace_id="trace-persist-test", trace_key="NIFTY|2026-10-29|25000|LONG",
        traced_at=datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(), snapshot=snapshot,
    )
    result = trace_routes.save_trace_opportunity(request)
    assert result == {"trace_id": "trace-persist-test", "persisted": True}
    assert fake_db.record.underlying == "NIFTY"
    assert fake_db.record.entry_edge == 8.5
    assert '"future_bid":25100' in fake_db.record.snapshot_json


def test_trace_snapshot_rejects_stock_or_invalid_direction():
    request = trace_routes.TraceOpportunityRequest(
        trace_id="bad-trace", trace_key="bad", traced_at=datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
        snapshot={"trace_id": "bad-trace", "underlying": "RELIANCE", "expiry": "2026-10-29",
                 "strike": 25000, "direction": "LONG", "executable_edge": 5},
    )
    with pytest.raises(HTTPException) as exc:
        trace_routes.save_trace_opportunity(request)
    assert exc.value.status_code == 422
