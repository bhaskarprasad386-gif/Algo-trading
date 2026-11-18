from datetime import datetime, timedelta
from types import SimpleNamespace

from app.scanner import live_box_spread_routes as routes


class _Query:
    def __init__(self, rows):
        self.rows = rows
    def filter(self, *args):
        return self
    def order_by(self, *args):
        return self
    def yield_per(self, *args):
        return iter(self.rows)
    def all(self):
        return self.rows


class _Session:
    def __init__(self, rows):
        self.rows = rows
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def query(self, *args):
        return _Query(self.rows)


def _row(**overrides):
    values = dict(
        observed_at=datetime.utcnow(),
        symbol="NIFTY", instrument_class="INDEX", expiry=20261123,
        expiry_type="WEEKLY", low_strike=22000, high_strike=22100,
        direction="LONG", timestamp_ns=123,
        low_call_bid=1, low_call_bid_qty=10, low_call_ask=2, low_call_ask_qty=10,
        low_put_bid=3, low_put_bid_qty=10, low_put_ask=4, low_put_ask_qty=10,
        high_call_bid=5, high_call_bid_qty=10, high_call_ask=6, high_call_ask_qty=10,
        high_put_bid=7, high_put_bid_qty=10, high_put_ask=8, high_put_ask_qty=10,
        executable_edge=10, edge_per_lot=250, gross_pnl=250, lot_size=25,
        depth_edge_per_unit=5.0, max_executable_lots=2, tradability_status="TRADABLE",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_alert_history_returns_only_rows_with_full_qualification_proof(monkeypatch):
    valid = _row()
    legacy = _row(depth_edge_per_unit=None, max_executable_lots=None, tradability_status=None)
    below_threshold = _row(depth_edge_per_unit=4.99)
    insufficient_lots = _row(max_executable_lots=1)
    non_tradable = _row(tradability_status="UNTRADABLE")
    monkeypatch.setattr(routes, "SessionLocal", lambda: _Session(
        [valid, legacy, below_threshold, insufficient_lots, non_tradable]
    ))

    payload = routes.alerts(days=30, limit=500)

    assert payload["count"] == 1
    assert len(payload["data"]) == 1
    assert payload["data"][0]["symbol"] == "NIFTY"
    assert payload["data"][0]["depth_edge_per_unit"] == 5.0
    assert payload["data"][0]["max_executable_lots"] == 2
    assert payload["data"][0]["tradability_status"] == "TRADABLE"


def test_alert_history_fails_closed_for_malformed_qualification_metadata(monkeypatch):
    malformed = _row(depth_edge_per_unit="not-a-number")
    monkeypatch.setattr(routes, "SessionLocal", lambda: _Session([malformed]))

    payload = routes.alerts(days=30, limit=500)

    assert payload["count"] == 0
    assert payload["data"] == []
