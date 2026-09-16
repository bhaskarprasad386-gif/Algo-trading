from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.backtesting import monthly_results_routes as routes
from app.backtesting.cash_future_replay_routes import _graph_points
from app.scanner.cash_future_history import CashFutureHistoryPoint, build_graph_series


IST = ZoneInfo("Asia/Kolkata")


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


class _Close:
    def close(self):
        return None


def _point(ts: datetime, gap: float, contract_month: str = "2026-09") -> CashFutureHistoryPoint:
    return CashFutureHistoryPoint(
        timestamp=ts,
        symbol="AAA",
        contract_month=contract_month,
        cash_price=100.0,
        future_price=100.0 + gap,
        gap=gap,
        gap_pct=gap,
        lot_size=50,
        margin_required=10000.0,
        charges=25.0,
        funding_cost=5.0,
    )


def test_sqlite_text_dates_sort_with_catalog_dates_for_monthly_shorting(monkeypatch):
    monkeypatch.setattr(routes, "_daily_rows", lambda *args, **kwargs: [
        {"trading_date": "2026-09-10", "symbol": "A"},
    ])
    monkeypatch.setattr(routes, "_downloaded_cash_future_symbols", lambda *args, **kwargs: ["A", "B"])
    monkeypatch.setattr(routes, "_downloaded_cash_future_days", lambda *args, **kwargs: {date(2026, 9, 11)})

    def payload(trading_day, symbols, **kwargs):
        assert type(trading_day) is date
        values = {
            date(2026, 9, 10): {"A": 4000.0},
            date(2026, 9, 11): {"B": 9000.0},
        }
        day_values = values.get(trading_day, {})
        return [
            {
                "trading_date": trading_day,
                "symbol": symbol,
                "gap": day_values[symbol] / 500.0,
                "weighted_gap": day_values[symbol],
                "open": 100.0,
                "high": 100.0 + day_values[symbol] / 500.0,
                "low": 100.0,
                "close": 100.0 + day_values[symbol] / 500.0,
                "lot_size": 500,
                "previous_close": 0.0,
                "contract_month": "2026-09",
                "gap_high_timestamp": f"{trading_day.isoformat()}T11:00:00",
                "net_profit": day_values[symbol],
                "roi_pct": 1.0,
            }
            for symbol in symbols
            if symbol in day_values
        ]

    monkeypatch.setattr(routes, "_cash_future_shorting_payloads", payload)
    result = routes.monthly_gap_search(2026, 9, "shorting", "STOCK", None, None, _Db([]))
    assert result["result"]["symbol"] == "B"
    assert result["result"]["trading_date"] == date(2026, 9, 11)


def test_monthly_graph_keeps_one_sorted_bar_per_day_without_mixing_expiries():
    rows = [
        {"trading_date": "2026-09-03", "symbol": "AAA", "open": 103, "high": 106, "low": 101, "close": 104, "lot_size": 50, "contract_month": "2026-09", "instrument_key": "NFO:AAA-SEP", "previous_close": 100, "instrument_type": "STOCK"},
        {"trading_date": date(2026, 9, 1), "symbol": "AAA", "open": 101, "high": 104, "low": 99, "close": 102, "lot_size": 50, "contract_month": "2026-09", "instrument_key": "NFO:AAA-SEP", "previous_close": 100, "instrument_type": "STOCK"},
        {"trading_date": "2026-09-03", "symbol": "AAA", "open": 203, "high": 250, "low": 180, "close": 240, "lot_size": 50, "contract_month": "2026-10", "instrument_key": "NFO:AAA-OCT", "previous_close": 100, "instrument_type": "STOCK"},
    ]
    result = routes.monthly_graph("aaa", 2026, 9, "STOCK", None, _Db(rows))
    assert [item["trading_date"] for item in result["series"]] == [date(2026, 9, 1), date(2026, 9, 3)]
    assert result["count"] == 2
    third = result["series"][1]
    assert third["contract_month"] == "2026-10"
    assert third["high"] == 250


def test_replay_graph_is_android_object_array_sorted_by_timestamp():
    later = _point(datetime(2026, 9, 10, 11, 15), 9.0)
    earlier = _point(datetime(2026, 9, 10, 10, 15), 4.0, contract_month="2026-10")
    graph = _graph_points([later, earlier], contract_month="2026-09")
    assert isinstance(graph, list)
    assert graph == [
        {
            "timestamp": later.timestamp.isoformat(),
            "cash": 100.0,
            "future": 109.0,
            "gap": 9.0,
            "gap_pct": 9.0,
            "contract_month": "2026-09",
        }
    ]
    series = build_graph_series([later, earlier], contract_month="2026-09")
    assert isinstance(series, dict)
    assert series["gap"] == [9.0]


def test_catalog_day_bounds_use_ist_session_calendar():
    start_ns, end_ns = routes._range_ns(date(2026, 9, 10), date(2026, 9, 10))
    start = datetime.fromtimestamp(start_ns / 1_000_000_000, tz=timezone.utc).astimezone(IST)
    end = datetime.fromtimestamp(end_ns / 1_000_000_000, tz=timezone.utc).astimezone(IST)
    assert start.isoformat() == "2026-09-10T00:00:00+05:30"
    assert end.isoformat() == "2026-09-11T00:00:00+05:30"
    nse_open_utc = datetime(2026, 9, 10, 3, 45, tzinfo=timezone.utc)
    assert routes._trading_date_from_ns(int(nse_open_utc.timestamp() * 1_000_000_000)) == date(2026, 9, 10)


def test_shorting_payloads_skip_missing_contracts_instead_of_failing_the_ranking(monkeypatch):
    class _Loader:
        def __init__(self, *_args, **_kwargs):
            return None

        def iter_points(self, selection):
            if selection.underlying == "BAD":
                raise LookupError("no historical cash-future contract")
            return [_point(datetime(2026, 9, 10, 10, 15), 8.0)]

    monkeypatch.setattr(routes, "HistoricalCatalog", lambda *_args, **_kwargs: _Close())
    monkeypatch.setattr(routes, "ContractMasterCatalog", lambda *_args, **_kwargs: _Close())
    monkeypatch.setattr(routes, "CashFutureHistoricalLoader", _Loader)

    payload = routes._cash_future_shorting_payloads(date(2026, 9, 10), ["BAD", "AAA"])
    assert [item["symbol"] for item in payload] == ["AAA"]
    assert payload[0]["weighted_gap"] == 400.0
    assert payload[0]["net_profit"] == 370.0
    assert payload[0]["charges"] == 25.0
    assert payload[0]["funding_cost"] == 5.0


def test_prior_gap_shorting_includes_catalog_only_prior_days(monkeypatch):
    selected_day = date(2026, 9, 10)
    prior_day = date(2026, 9, 8)

    monkeypatch.setattr(routes, "_daily_rows", lambda *_args, **kwargs: (
        [{"trading_date": selected_day, "symbol": "A"}]
        if kwargs.get("symbol") is None and _args[2] == selected_day
        else []
    ))
    monkeypatch.setattr(routes, "_downloaded_cash_future_symbols", lambda start, end, **kwargs: ["A"] if end >= selected_day else ["A"])
    monkeypatch.setattr(routes, "_downloaded_cash_future_days", lambda start, end, **kwargs: {prior_day} if end < selected_day else {selected_day})

    def payload(trading_day, symbols, **kwargs):
        weighted = 5000.0 if trading_day == selected_day else 9000.0
        return [{
            "trading_date": trading_day,
            "symbol": "A",
            "weighted_gap": weighted,
            "gap": weighted / 500.0,
            "lot_size": 500,
        } for _ in symbols]

    monkeypatch.setattr(routes, "_cash_future_shorting_payloads", payload)
    result = routes.prior_gap_comparison(selected_day, "shorting", "STOCK", None, None, 5, _Db([]))
    assert result["has_larger_prior_gap"] is True
    assert result["prior_larger"][0]["trading_date"] == prior_day
    assert result["prior_larger"][0]["weighted_gap"] == 9000.0


def test_date_gap_tie_breaks_symbol_ascending(monkeypatch):
    monkeypatch.setattr(routes, "_daily_rows", lambda *args, **kwargs: [{"symbol": "Z"}, {"symbol": "A"}])
    monkeypatch.setattr(routes, "_downloaded_cash_future_symbols", lambda *args, **kwargs: [])
    monkeypatch.setattr(routes, "_cash_future_shorting_payloads", lambda trading_date, symbols, **kwargs: [
        {"symbol": symbol, "weighted_gap": 1000.0, "gap": 2.0, "lot_size": 500} for symbol in symbols
    ])
    result = routes.date_gap_ranking(date(2026, 9, 10), "shorting", "STOCK", None, None, 200, _Db([]))
    assert [item["symbol"] for item in result["data"]] == ["A", "Z"]
