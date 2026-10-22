from datetime import datetime
from types import SimpleNamespace

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
    )


def test_trace_status_resolves_exact_strike_outside_scanner_range_and_calculates_direction(monkeypatch):
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

    class FakeClient:
        def quote_many(self, exchange, contracts):
            assert exchange == "NFO"
            assert {item["symboltoken"] for item in contracts} == {"1", "2", "3"}
            return {"status": True, "data": {"fetched": [
                {"symbolToken": "1", "depth": {"buy": [{"price": 25110}], "sell": [{"price": 25112}]}},
                {"symbolToken": "2", "depth": {"buy": [{"price": 120}], "sell": [{"price": 122}]}},
                {"symbolToken": "3", "depth": {"buy": [{"price": 90}], "sell": [{"price": 92}]}},
            ]}}

    monkeypatch.setattr("app.market_data.instruments.InstrumentMaster", FakeMaster)
    monkeypatch.setattr("app.market_data.client.MarketDataClient", FakeClient)

    long = trace_routes.check_trace_live_status(_request("LONG", 12.5))
    assert long["status"] == "LIVE_QUOTES"
    assert long["synthetic_bid"] == 25028
    assert long["synthetic_ask"] == 25032
    assert long["current_edge"] == 78
    assert long["entry_edge"] == 12.5
    assert long["edge_change"] == 65.5
    assert long["broker_orders"] == "OFF"

    short = trace_routes.check_trace_live_status(_request("SHORT", 12.5))
    assert short["current_edge"] == -(-25028 + 25112)  # synthetic bid minus future ask
    assert short["current_edge"] == -84
    assert short["edge_change"] == -96.5


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
