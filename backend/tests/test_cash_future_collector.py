from datetime import date

import pytest

from app.scanner.cash_future_collector import CashFutureHistoryCollector, _full_quote


class FakeMaster:
    def __init__(self):
        self.instruments = [
            {"symbol": "ABC30SEP2026FUT", "name": "ABC", "token": "201", "exch_seg": "NFO", "instrumenttype": "FUTSTK", "expiry": "30SEP2026", "lotsize": "100"},
            {"symbol": "ABC29OCT2026FUT", "name": "ABC", "token": "202", "exch_seg": "NFO", "instrumenttype": "FUTSTK", "expiry": "29OCT2026", "lotsize": "100"},
        ]

    def search(self, exchange=None):
        return self.instruments

    def get_instrument(self, tradingsymbol, exchange):
        if tradingsymbol == "ABC-EQ" and exchange == "NSE":
            return {"symbol": "ABC-EQ", "token": "101", "exch_seg": "NSE"}
        return None


class FakeMarketClient:
    def quote(self, exchange, tradingsymbol, symboltoken):
        prices = {"ABC-EQ": 100.0, "ABC30SEP2026FUT": 108.0, "ABC29OCT2026FUT": 111.0}
        price = prices[tradingsymbol]
        return {
            "status": True,
            "data": {
                "fetched": [{
                    "ltp": price,
                    "tradeVolume": 5000,
                    "opnInterest": 20000,
                    "depth": {
                        "buy": [{"price": price - 0.1}],
                        "sell": [{"price": price + 0.1}],
                    },
                }]
            },
        }

    def margin(self, positions):
        assert len(positions) == 1
        position = positions[0]
        assert position["exchange"] == "NFO"
        assert position["productType"] == "CARRYFORWARD"
        assert position["tradeType"] == "SELL"
        return {"status": True, "data": {"totalMarginRequired": 50000.0}}


def test_collector_keeps_current_and_near_separate(monkeypatch):
    saved = []

    def fake_save(db, point, expiry_date=None):
        saved.append(point)
        return type("Row", (), {"id": len(saved)})()

    monkeypatch.setattr("app.scanner.cash_future_collector.save_history_point", fake_save)
    collector = CashFutureHistoryCollector(["ABC"], FakeMarketClient(), FakeMaster())
    result = collector.collect_symbol("ABC", object())

    assert [item["contract_month"] for item in result] == ["CURRENT", "NEAR"]
    assert [point.contract_month for point in saved] == ["CURRENT", "NEAR"]
    assert [point.gap for point in saved] == [8.0, 11.0]
    assert [point.margin_required for point in saved] == [50000.0, 50000.0]
    assert saved[0].expiry_date == date(2026, 9, 30)
    assert saved[1].expiry_date == date(2026, 10, 29)
    assert result[0]["volume"] == 5000
    assert result[0]["oi"] == 20000
    assert result[0]["cash_bid"] == 99.9
    assert result[0]["cash_ask"] == 100.1
    assert result[0]["future_bid"] == 107.9
    assert result[0]["future_ask"] == 108.1
    assert result[0]["cash_execution_price"] == 100.1
    assert result[0]["future_execution_price"] == 107.9
    assert result[0]["margin_required"] == 50000.0


def test_contract_selection_ignores_wrong_underlying_expired_and_duplicate_rows(monkeypatch):
    master = FakeMaster()
    master.instruments.extend([
        {"symbol": "ABCBANK30SEP2026FUT", "name": "ABCBANK", "token": "301", "exch_seg": "NFO", "instrumenttype": "FUTSTK", "expiry": "30SEP2026", "lotsize": "100"},
        {"symbol": "ABC31JUL2026FUT", "name": "ABC", "token": "302", "exch_seg": "NFO", "instrumenttype": "FUTSTK", "expiry": "31JUL2026", "lotsize": "100"},
        {"symbol": "ABC30SEP2026FUT", "name": "ABC", "token": "303", "exch_seg": "NFO", "instrumenttype": "FUTSTK", "expiry": "30SEP2026", "lotsize": "100"},
        {"symbol": "ABC30SEP2026OPT", "name": "ABC", "token": "304", "exch_seg": "NFO", "instrumenttype": "OPTSTK", "expiry": "30SEP2026", "lotsize": "100"},
    ])
    collector = CashFutureHistoryCollector(["ABC"], FakeMarketClient(), master)

    selected = collector._future_instruments("ABC")

    assert [item["symbol"] for item in selected] == ["ABC30SEP2026FUT", "ABC29OCT2026FUT"]


def test_contract_selection_ignores_missing_token_and_invalid_lot_size():
    master = FakeMaster()
    master.instruments.extend([
        {"symbol": "ABC30SEP2026BADTOKEN", "name": "ABC", "token": "", "exch_seg": "NFO", "instrumenttype": "FUTSTK", "expiry": "30SEP2026", "lotsize": "100"},
        {"symbol": "ABC30SEP2026ZEROLOT", "name": "ABC", "token": "305", "exch_seg": "NFO", "instrumenttype": "FUTSTK", "expiry": "30SEP2026", "lotsize": "0"},
        {"symbol": "ABC30SEP2026BADLOT", "name": "ABC", "token": "306", "exch_seg": "NFO", "instrumenttype": "FUTSTK", "expiry": "30SEP2026", "lotsize": "not-a-lot"},
    ])
    collector = CashFutureHistoryCollector(["ABC"], FakeMarketClient(), master)

    selected = collector._future_instruments("ABC")

    assert [item["symbol"] for item in selected] == ["ABC30SEP2026FUT", "ABC29OCT2026FUT"]


def test_contract_selection_ignores_malformed_expiry():
    master = FakeMaster()
    master.instruments.append({
        "symbol": "ABCBADDATEFUT", "name": "ABC", "token": "307", "exch_seg": "NFO",
        "instrumenttype": "FUTSTK", "expiry": "not-a-date", "lotsize": "100",
    })
    collector = CashFutureHistoryCollector(["ABC"], FakeMarketClient(), master)

    selected = collector._future_instruments("ABC")

    assert [item["symbol"] for item in selected] == ["ABC30SEP2026FUT", "ABC29OCT2026FUT"]


def test_collect_reports_clear_error_when_no_eligible_contract_exists():
    master = FakeMaster()
    master.instruments = []
    collector = CashFutureHistoryCollector(["ABC"], FakeMarketClient(), master)

    result = collector.collect(object())

    assert result["collected"] == []
    assert result["errors"] == [{
        "symbol": "ABC",
        "error": "no eligible NFO FUTSTK contracts found: ABC",
    }]


def test_collect_symbol_continues_when_current_contract_fails(monkeypatch):
    saved = []

    def fake_save(db, point, expiry_date=None):
        saved.append(point)
        return type("Row", (), {"id": len(saved)})()

    class CurrentFails(FakeMarketClient):
        def quote(self, exchange, tradingsymbol, symboltoken):
            if tradingsymbol == "ABC30SEP2026FUT":
                raise RuntimeError("current quote unavailable")
            return super().quote(exchange, tradingsymbol, symboltoken)

    monkeypatch.setattr("app.scanner.cash_future_collector.save_history_point", fake_save)
    collector = CashFutureHistoryCollector(["ABC"], CurrentFails(), FakeMaster())
    errors = []
    result = collector.collect_symbol("ABC", object(), errors=errors)

    assert [item["contract_month"] for item in result] == ["NEAR"]
    assert [point.contract_month for point in saved] == ["NEAR"]
    assert errors == [{
        "symbol": "ABC",
        "contract_month": "CURRENT",
        "future_symbol": "ABC30SEP2026FUT",
        "error": "current quote unavailable",
    }]


def test_collect_reports_contract_error_and_keeps_successful_near_result(monkeypatch):
    saved = []

    def fake_save(db, point, expiry_date=None):
        saved.append(point)
        return type("Row", (), {"id": len(saved)})()

    class CurrentMarginFails(FakeMarketClient):
        def margin(self, positions):
            if positions[0]["token"] == "201":
                raise RuntimeError("current margin unavailable")
            return super().margin(positions)

    monkeypatch.setattr("app.scanner.cash_future_collector.save_history_point", fake_save)
    collector = CashFutureHistoryCollector(["ABC"], CurrentMarginFails(), FakeMaster())
    result = collector.collect(object())

    assert [item["contract_month"] for item in result["collected"]] == ["NEAR"]
    assert [point.contract_month for point in saved] == ["NEAR"]
    assert result["errors"] == [{
        "symbol": "ABC",
        "contract_month": "CURRENT",
        "future_symbol": "ABC30SEP2026FUT",
        "error": "current margin unavailable",
    }]


def test_full_quote_rejects_missing_fetched_data():
    with pytest.raises(ValueError, match="has no fetched quote"):
        _full_quote({"status": True, "data": {"fetched": []}})


def test_full_quote_does_not_invent_bid_ask():
    quote = _full_quote({
        "status": True,
        "data": {
            "fetched": [{
                "ltp": 100.0,
                "tradeVolume": 10,
                "opnInterest": 20,
                "depth": {"buy": [], "sell": []},
            }]
        },
    })
    assert quote["ltp"] == 100.0
    assert quote["bid"] is None
    assert quote["ask"] is None


def test_full_quote_preserves_non_positive_quote_sides():
    quote = _full_quote({
        "status": True,
        "data": {
            "fetched": [{
                "ltp": 100.0,
                "tradeVolume": 10,
                "opnInterest": 20,
                "depth": {
                    "buy": [{"price": 0}],
                    "sell": [{"price": -1.5}],
                },
            }]
        },
    })
    assert quote["bid"] == 0.0
    assert quote["ask"] == -1.5


def test_full_quote_keeps_unparseable_quote_side_missing():
    quote = _full_quote({
        "status": True,
        "data": {
            "fetched": [{
                "ltp": 100.0,
                "depth": {
                    "buy": [{"price": "not-a-price"}],
                    "sell": [{"price": "101.0"}],
                },
            }]
        },
    })
    assert quote["bid"] is None
    assert quote["ask"] == 101.0
