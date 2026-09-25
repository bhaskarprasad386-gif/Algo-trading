from datetime import date

from app.backtesting import monthly_results_routes as routes


class _FakeDB:
    pass


def _paired_payload(trading_date, symbols, **kwargs):
    points = {
        "A": {
            "trading_date": trading_date,
            "symbol": "A",
            "gap": 17.0,
            "gap_percent": 1.7,
            "weighted_gap": 8500.0,
            "previous_close": 0.0,
            "open": 1005.0,
            "high": 1022.0,
            "low": 1005.0,
            "close": 1022.0,
            "lot_size": 500,
            "contract_month": "2026-09",
            "instrument_key": "NFO:2026-09",
            "gap_high_timestamp": f"{trading_date}T11:15:00",
            "cash_price_at_gap_high": 1005.0,
            "future_price_at_gap_high": 1022.0,
            "margin_required": 150000.0,
            "charges": 25.0,
            "funding_cost": 10.0,
            "net_profit": 8465.0,
            "roi_pct": 5.6433333333,
        },
        "B": {
            "trading_date": trading_date,
            "symbol": "B",
            "gap": 12.0,
            "gap_percent": 1.2,
            "weighted_gap": 12000.0,
            "previous_close": 0.0,
            "open": 2010.0,
            "high": 2022.0,
            "low": 2010.0,
            "close": 2022.0,
            "lot_size": 1000,
            "contract_month": "2026-09",
            "instrument_key": "NFO:2026-09",
            "gap_high_timestamp": f"{trading_date}T13:00:00",
            "cash_price_at_gap_high": 2010.0,
            "future_price_at_gap_high": 2022.0,
            "margin_required": 250000.0,
            "charges": 37.5,
            "funding_cost": 12.25,
            "net_profit": 11950.25,
            "roi_pct": 4.7801,
        },
    }
    return [points[symbol] for symbol in symbols if symbol in points]


def test_date_gap_shorting_uses_paired_intraday_future_cash_payload(monkeypatch):
    trading_date = date(2026, 9, 10)

    monkeypatch.setattr(routes, "_daily_rows", lambda *args, **kwargs: [
        {"symbol": "A"},
        {"symbol": "B"},
    ])
    monkeypatch.setattr(routes, "_cash_future_shorting_payloads", _paired_payload)

    result = routes.date_gap_ranking(
        trading_date=trading_date,
        mode="shorting",
        instrument_type="STOCK",
        symbol=None,
        contract_month=None,
        limit=200,
        db=_FakeDB(),
    )

    assert result["top"]["symbol"] == "B"
    assert result["top"]["gap"] == 12.0
    assert result["top"]["weighted_gap"] == 12000.0
    assert result["top"]["charges"] == 37.5
    assert result["top"]["funding_cost"] == 12.25
    assert result["top"]["net_profit"] == 11950.25
    assert result["top"]["roi_pct"] == 4.7801
    assert result["data"][1]["symbol"] == "A"
    assert result["data"][1]["charges"] == 25.0
    assert result["data"][1]["funding_cost"] == 10.0
    assert result["data"][1]["net_profit"] == 8465.0
    assert result["data"][1]["roi_pct"] == 5.6433333333


def test_monthly_gap_shorting_selects_highest_paired_intraday_gap_value(monkeypatch):
    monkeypatch.setattr(routes, "_daily_rows", lambda *args, **kwargs: [
        {"trading_date": date(2026, 9, 10), "symbol": "A"},
        {"trading_date": date(2026, 9, 11), "symbol": "B"},
    ])
    monkeypatch.setattr(routes, "_cash_future_shorting_payloads", _paired_payload)

    result = routes.monthly_gap_search(
        year=2026,
        month=9,
        mode="shorting",
        instrument_type="STOCK",
        symbol=None,
        contract_month=None,
        db=_FakeDB(),
    )

    assert result["result"]["symbol"] == "B"
    assert result["result"]["trading_date"] == date(2026, 9, 11)
    assert result["result"]["gap"] == 12.0
    assert result["result"]["gap_value"] == 12000.0
    assert result["result"]["gap_high_timestamp"] == "2026-09-11T13:00:00"
    assert result["result"]["cash_price_at_gap_high"] == 2010.0
    assert result["result"]["future_price_at_gap_high"] == 2022.0
    assert result["result"]["charges"] == 37.5
    assert result["result"]["funding_cost"] == 12.25
    assert result["result"]["net_profit"] == 11950.25
    assert result["result"]["roi_pct"] == 4.7801


def test_monthly_gap_top10_ranks_each_stock_by_its_month_high_gap_value(monkeypatch):
    monkeypatch.setattr(routes, "_daily_rows", lambda *args, **kwargs: [
        {"trading_date": date(2026, 9, 10), "symbol": "A"},
        {"trading_date": date(2026, 9, 11), "symbol": "A"},
        {"trading_date": date(2026, 9, 10), "symbol": "B"},
    ])

    def payload(trading_date, symbols, **kwargs):
        values = {
            (date(2026, 9, 10), "A"): 7000.0,
            (date(2026, 9, 11), "A"): 9000.0,
            (date(2026, 9, 10), "B"): 12000.0,
        }
        result = []
        for symbol in symbols:
            weighted = values[(trading_date, symbol)]
            result.append({
                "trading_date": trading_date,
                "symbol": symbol,
                "gap": weighted / 500.0,
                "weighted_gap": weighted,
                "lot_size": 500,
                "contract_month": "2026-09",
                "instrument_key": "NFO:2026-09",
                "gap_high_timestamp": f"{trading_date}T10:30:00",
                "cash_price_at_gap_high": 1000.0,
                "future_price_at_gap_high": 1000.0 + weighted / 500.0,
                "margin_required": 250000.0 if symbol == "B" else 150000.0,
            })
        return result

    monkeypatch.setattr(routes, "_cash_future_shorting_payloads", payload)

    result = routes.monthly_gap_top10(
        year=2026,
        month=9,
        instrument_type="STOCK",
        contract_month=None,
        db=_FakeDB(),
    )

    assert result["count"] == 2
    assert [item["symbol"] for item in result["data"]] == ["B", "A"]
    assert result["data"][0]["gap_value"] == 12000.0
    assert result["data"][0]["margin_required"] == 250000.0
    assert result["data"][0]["gap_high_date"] == date(2026, 9, 10)
    assert result["data"][0]["gap_high_time"] == "10:30:00"
    assert result["data"][1]["gap_value"] == 9000.0
    assert result["data"][1]["gap_high_date"] == date(2026, 9, 11)


class _ReplayMappings:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _ReplayResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return _ReplayMappings(self._rows)


class _ReplayDB:
    def __init__(self, rows):
        self.rows = rows
        self.params = None

    def execute(self, _sql, params):
        self.params = params
        return _ReplayResult(self.rows)


def test_intraday_replay_preserves_ohlc_volume_oi_lot_and_contract_fields():
    trading_day = date(2026, 9, 10)
    rows = [
        {
            "timestamp": "2026-09-10T09:15:00+05:30",
            "open": 100.0,
            "high": 105.0,
            "low": 99.0,
            "close": 103.0,
            "volume": 12500.0,
            "oi": 98765.0,
            "lot_size": 500.0,
            "contract_month": "2026-09",
            "instrument_key": "NFO:2026-09",
        }
    ]
    db = _ReplayDB(rows)

    response = routes.intraday_replay(
        trading_date=trading_day,
        symbol=" test ",
        instrument_type="stock",
        contract_month="2026-09",
        interval_minutes=1,
        db=db,
    )

    assert response["status"] == "success"
    assert response["count"] == 1
    assert response["source_interval_minutes"] == 1
    point = response["series"][0]
    assert point["timestamp"] == "2026-09-10T09:15:00+05:30"
    assert point["open"] == 100.0
    assert point["high"] == 105.0
    assert point["low"] == 99.0
    assert point["close"] == 103.0
    assert point["volume"] == 12500.0
    assert point["oi"] == 98765.0
    assert point["lot_size"] == 500.0
    assert point["contract_month"] == "2026-09"
    assert point["instrument_key"] == "NFO:2026-09"
    assert db.params["symbol"] == "TEST"
    assert db.params["instrument_type"] == "STOCK"
    assert db.params["contract_month"] == "2026-09"
