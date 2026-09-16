from datetime import date

from app.backtesting import monthly_results_routes as routes


class _Mappings:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return _Mappings(self.rows)


class _Db:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, *_args, **_kwargs):
        return _Result(self.rows)


def _payload(symbol, net_profit, weighted_gap, trading_day=date(2026, 1, 5)):
    return {
        "trading_date": trading_day,
        "symbol": symbol,
        "gap": weighted_gap / 100.0,
        "gap_percent": 1.0,
        "weighted_gap": weighted_gap,
        "lot_size": 100,
        "gap_high_timestamp": f"{trading_day.isoformat()}T10:00:00+05:30",
        "cash_price_at_gap_high": 100.0,
        "future_price_at_gap_high": 101.0,
        "contract_month": "202601",
        "instrument_key": f"NFO:{symbol}-1",
        "expiry_date": date(2026, 1, 29),
        "is_expiry_day": False,
        "margin_required": 100000.0,
        "charges": 10.0,
        "funding_cost": 5.0,
        "net_profit": net_profit,
        "roi_pct": net_profit / 100000.0 * 100.0,
    }


def test_monthly_top10_ranks_by_net_profit_not_weighted_gap(monkeypatch):
    rows = [{"trading_date": date(2026, 1, 5), "symbol": "A", "lot_size": 100}, {"trading_date": date(2026, 1, 5), "symbol": "B", "lot_size": 100}]
    payloads = [_payload("A", 1000.0, 9000.0), _payload("B", 2000.0, 1000.0)]
    monkeypatch.setattr(routes, "_downloaded_cash_future_symbols", lambda *args, **kwargs: [])
    monkeypatch.setattr(routes, "_cash_future_shorting_payloads", lambda *args, **kwargs: payloads)
    response = routes.monthly_gap_top10(2026, 1, "STOCK", None, _Db(rows))
    assert response["ranking_metric"] == "net_profit"
    assert response["data"][0]["symbol"] == "B"
    assert response["data"][0]["rank"] == 1
    assert [item["rank"] for item in response["data"]] == list(range(1, len(response["data"]) + 1))
    assert response["data"][0]["net_profit"] == 2000.0


def test_monthly_top10_unions_partial_db_with_downloaded_catalog(monkeypatch):
    day = date(2026, 1, 5)
    rows = [{"trading_date": day, "symbol": "A", "lot_size": 100}]
    payloads = [_payload("A", 1000.0, 1000.0, day), _payload("B", 1500.0, 800.0, day)]
    monkeypatch.setattr(routes, "_downloaded_cash_future_symbols", lambda *args, **kwargs: ["A", "B"])
    monkeypatch.setattr(routes, "_downloaded_cash_future_days", lambda *args, **kwargs: {day})
    monkeypatch.setattr(routes, "_cash_future_shorting_payloads", lambda trading_day, symbols, **kwargs: [p for p in payloads if p["symbol"] in symbols])
    response = routes.monthly_gap_top10(2026, 1, "STOCK", None, _Db(rows))
    assert {item["symbol"] for item in response["data"]} == {"A", "B"}
    assert [item["rank"] for item in response["data"]] == [1, 2]
