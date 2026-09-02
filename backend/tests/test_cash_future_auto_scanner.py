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
        CashQuote(symbol="SBIN", ltp=100.0, bid=99.9, ask=100.0),
        _quote(),
        CashFutureConfig(min_gap=0.5, min_gap_pct=0.5, min_net_profit=5.0, min_volume=1000, min_oi=10000),
    )
    assert result.executable is True
    assert result.rejection_reasons == ()
    assert result.net_profit == 9.0
    assert result.deployed_capital == 2000.0
    assert result.roi_pct == 0.45


def test_cash_future_rejects_gap_profit_volume_and_oi_filters():
    result = calculate_cash_future(
        CashQuote(symbol="SBIN", ltp=100.0, bid=99.9, ask=100.0),
        _quote(ltp=100.2, bid=100.2, volume=100, oi=1000),
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
        CashQuote(symbol="SBIN", ltp=100.0, bid=99.9, ask=100.0),
        _quote(bid=99.0, ask=103.0),
        CashFutureConfig(max_bid_ask_spread_pct=2.0),
    )
    assert result.executable is False
    assert "bid_ask_spread_above_maximum" in result.rejection_reasons


def test_cash_future_rejects_insufficient_broker_margin():
    result = calculate_cash_future(
        CashQuote(symbol="SBIN", ltp=100.0, bid=99.9, ask=100.0),
        _quote(margin_required=900.0),
        CashFutureConfig(min_margin=1000.0),
    )
    assert result.executable is False
    assert "margin_below_minimum" in result.rejection_reasons


def test_cash_future_rejects_low_broker_margin_roi():
    result = calculate_cash_future(
        CashQuote(symbol="SBIN", ltp=100.0, bid=99.9, ask=100.0),
        _quote(margin_required=10000.0),
        CashFutureConfig(min_roi_pct=0.2),
    )
    assert result.executable is False
    assert "roi_below_minimum" in result.rejection_reasons


def test_cash_future_live_auto_api_returns_only_executable_rows(monkeypatch):
    class FakeCollector:
        def __init__(self, symbols, config):
            assert symbols == ["SBIN"]
            assert config.require_two_sided_quotes is True

        def collect(self, db):
            return {
                "collected": [
                    {"symbol": "SBIN", "net_profit": 25.0, "roi_pct": 1.2, "executable": True},
                    {"symbol": "SBIN", "net_profit": 100.0, "roi_pct": 5.0, "executable": False},
                ],
                "errors": [],
            }

    monkeypatch.setattr(auto_routes, "discover_cash_future_symbols", lambda limit: ["SBIN"])
    monkeypatch.setattr(auto_routes, "CashFutureHistoryCollector", FakeCollector)

    response = auto_routes.cash_future_live_auto_scanner(db=object())

    assert response["status"] == "success"
    assert response["opportunity_count"] == 1
    assert len(response["data"]) == 1
    assert response["data"][0]["executable"] is True
    assert response["data"][0]["net_profit"] == 25.0


def test_cash_future_live_auto_api_sorts_executable_rows_by_net_profit(monkeypatch):
    class FakeCollector:
        def __init__(self, symbols, config):
            assert config.require_two_sided_quotes is True

        def collect(self, db):
            return {
                "collected": [
                    {"symbol": "SBIN", "net_profit": 20.0, "roi_pct": 3.0, "executable": True},
                    {"symbol": "TCS", "net_profit": 50.0, "roi_pct": 1.0, "executable": True},
                    {"symbol": "INFY", "net_profit": 50.0, "roi_pct": 2.0, "executable": True},
                ],
                "errors": [],
            }

    monkeypatch.setattr(auto_routes, "discover_cash_future_symbols", lambda limit: ["SBIN", "TCS", "INFY"])
    monkeypatch.setattr(auto_routes, "CashFutureHistoryCollector", FakeCollector)

    response = auto_routes.cash_future_live_auto_scanner(db=object())

    assert [item["symbol"] for item in response["data"]] == ["INFY", "TCS", "SBIN"]
    assert response["opportunity_count"] == 3
