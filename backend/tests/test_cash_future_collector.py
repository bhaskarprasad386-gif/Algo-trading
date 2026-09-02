from datetime import date

from app.scanner.cash_future_collector import CashFutureHistoryCollector


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
                "fetched": [
                    {
                        "ltp": price,
                        "tradeVolume": 5000,
                        "opnInterest": 20000,
                        "depth": {
                            "buy": [{"price": price - 0.1}],
                            "sell": [{"price": price + 0.1}],
                        },
                    }
                ]
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
    assert result[0]["future_bid"] == 107.9
    assert result[0]["future_ask"] == 108.1
    assert result[0]["margin_required"] == 50000.0
