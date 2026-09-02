from app.scanner import auto_routes


class FakeMaster:
    def __init__(self):
        self.instruments = [
            {"name": "SBIN", "exch_seg": "NFO", "instrumenttype": "FUTSTK", "expiry": "30SEP2026"},
            {"name": "RELIANCE", "exch_seg": "NFO", "instrumenttype": "FUTSTK", "expiry": "30SEP2026"},
            {"name": "NIFTY", "exch_seg": "NFO", "instrumenttype": "FUTIDX", "expiry": "30SEP2026"},
            {"name": "OLD", "exch_seg": "NFO", "instrumenttype": "FUTSTK", "expiry": "30SEP2025"},
            {"name": "ABC", "exch_seg": "NSE", "instrumenttype": "FUTSTK", "expiry": "30SEP2026"},
        ]

    def search(self, exchange=None):
        return self.instruments


def test_discover_cash_future_symbols(monkeypatch):
    monkeypatch.setattr(auto_routes, "InstrumentMaster", FakeMaster)
    result = auto_routes.discover_cash_future_symbols(limit=2)
    assert result == ["RELIANCE", "SBIN"]
    assert "NIFTY" not in result
