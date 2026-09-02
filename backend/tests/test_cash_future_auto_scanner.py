from app.scanner import auto_routes
from app.scanner.cash_future import CashFutureConfig, CashQuote, FutureQuote, calculate_cash_future


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


def _quote(**overrides):
    values = {
        "symbol": "SBIN",
        "contract_month": "CURRENT",
        "ltp": 101.0,
        "lot_size": 10,
        "margin_required": 1000.0,
        "volume": 5000,
        "oi": 20000,
        "bid": 100.9,
        "ask": 101.1,
    }
    values.update(overrides)
    return FutureQuote(**values)


def test_cash_future_passes_requested_executable_filters():
    result = calculate_cash_future(
        CashQuote(symbol="SBIN", ltp=100.0),
        _quote(),
        CashFutureConfig(min_gap=0.5, min_gap_pct=0.5, min_net_profit=5.0, min_volume=1000, min_oi=10000),
    )
    assert result.executable is True
    assert result.rejection_reasons == ()
    assert result.net_profit == 10.0


def test_cash_future_rejects_gap_profit_volume_and_oi_filters():
    result = calculate_cash_future(
        CashQuote(symbol="SBIN", ltp=100.0),
        _quote(ltp=100.2, volume=100, oi=1000),
        CashFutureConfig(min_gap=0.5, min_gap_pct=0.5, min_net_profit=5.0, min_volume=1000, min_oi=10000),
    )
    assert result.executable is False
    assert "gap_below_minimum" in result.rejection_reasons
    assert "gap_pct_below_minimum" in result.rejection_reasons
    assert "net_profit_below_minimum" in result.rejection_reasons
    assert "volume_below_minimum" in result.rejection_reasons
    assert "oi_below_minimum" in result.rejection_reasons


def test_cash_future_rejects_wide_future_bid_ask_spread():
    result = calculate_cash_future(
        CashQuote(symbol="SBIN", ltp=100.0),
        _quote(bid=99.0, ask=103.0),
        CashFutureConfig(max_bid_ask_spread_pct=2.0),
    )
    assert result.executable is False
    assert "bid_ask_spread_above_maximum" in result.rejection_reasons
