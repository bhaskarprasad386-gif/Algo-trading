from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app import alert_routes


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, value):
        return self

    def all(self):
        return self.rows

    def count(self):
        return len(self.rows)


class _DB:
    def __init__(self, rows_by_model):
        self.rows_by_model = rows_by_model

    def query(self, model):
        return _Query(self.rows_by_model.get(model, []))


def _row(**values):
    defaults = {
        "observed_at": datetime(2026, 10, 7, 5, 0, 0),
        "timestamp_ns": 1,
        "symbol": "TEST",
        "underlying": "NIFTY",
        "direction": "LONG",
        "event": "alert",
        "gross_profit": 100.0,
        "net_profit": 90.0,
        "gap": 2.5,
        "gap_points": 3.0,
        "edge_long": 3.0,
        "edge_short": 2.0,
        "near_contract_month": "2026-10",
        "far_contract_month": "2026-11",
        "exchange": "NFO",
        "instrument_class": "OPTION",
        "expiry": "2026-10-29",
        "strike": 25000.0,
        "executable_edge": 4.0,
        "edge_per_lot": 200.0,
        "gross_pnl": 200.0,
        "lot_size": 50,
        "contract_month": "2026-10",
        "cash_ask": 100.0,
        "future_bid": 102.5,
        "gap_pct": 2.5,
        "alert_lots": 1,
        "estimated_cost": 10.0,
        "net_gap_pct": 2.4,
        "annualized_gap_pct": 12.0,
        "liquidity_qty": 100,
        "stable_observations": 3,
        "low_strike": 24900.0,
        "high_strike": 25100.0,
        "low_call_bid": 120.0,
        "low_call_ask": 121.0,
        "low_put_bid": 20.0,
        "low_put_ask": 21.0,
        "high_call_bid": 40.0,
        "high_call_ask": 41.0,
        "high_put_bid": 140.0,
        "high_put_ask": 141.0,
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


def _db_with_history():
    return _DB({
        alert_routes.LiveCashFutureAlertHistory: [
            _row(observed_at=datetime(2026, 10, 7, 5, 1), symbol="CASH")
        ],
        alert_routes.LiveCalendarSpreadAlertHistory: [
            _row(observed_at=datetime(2026, 10, 7, 5, 3), underlying="NIFTY")
        ],
        alert_routes.LiveSyntheticAlertHistory: [
            _row(observed_at=datetime(2026, 10, 7, 5, 4), symbol="SYN")
        ],
        alert_routes.LiveBoxSpreadAlertHistory: [
            _row(observed_at=datetime(2026, 10, 7, 5, 2), symbol="BOX")
        ],
    })


def test_alert_history_normalizes_all_four_scanners_and_sorts(monkeypatch):
    monkeypatch.setattr(alert_routes, "current_user_id", lambda db: 123)

    response = alert_routes.get_alert_history(days=7, limit=10, db=_db_with_history())

    assert response["status"] == "success"
    assert response["history_scope"] == "scanner_global"
    assert response["days"] == 7
    assert response["count"] == 4
    assert [row["strategy_id"] for row in response["data"]] == [
        "synthetic-future-cash-carry",
        "calendar-spread",
        "box-spread",
        "cash-future",
    ]
    assert response["data"][0]["gross_profit"] == 200.0
    assert response["data"][1]["symbol"] == "NIFTY"
    assert response["data"][2]["details"]["low_strike"] == 24900.0
    assert response["data"][3]["details"]["cash_ask"] == 100.0


def test_alert_history_applies_final_limit_after_cross_scanner_merge(monkeypatch):
    monkeypatch.setattr(alert_routes, "current_user_id", lambda db: 123)

    response = alert_routes.get_alert_history(days=30, limit=2, db=_db_with_history())

    assert response["count"] == 2
    assert len(response["data"]) == 2
    assert [row["strategy_id"] for row in response["data"]] == [
        "synthetic-future-cash-carry",
        "calendar-spread",
    ]


@pytest.mark.parametrize("days", [0, 31])
def test_alert_history_rejects_invalid_days(days):
    with pytest.raises(Exception) as exc:
        alert_routes.get_alert_history(days=days, limit=10, db=None)
    assert exc.value.status_code == 422


@pytest.mark.parametrize("limit", [0, 501])
def test_alert_history_rejects_invalid_limit(limit):
    with pytest.raises(Exception) as exc:
        alert_routes.get_alert_history(days=30, limit=limit, db=None)
    assert exc.value.status_code == 422
