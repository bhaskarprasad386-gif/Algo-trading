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
    assert result["data"][1]["symbol"] == "A"


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
    assert result["data"][0]["gap_high_date"] == date(2026, 9, 10)
    assert result["data"][0]["gap_high_time"] == "10:30:00"
    assert result["data"][1]["gap_value"] == 9000.0
    assert result["data"][1]["gap_high_date"] == date(2026, 9, 11)
