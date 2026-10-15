from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.market_data.live_cash_future_common import LiveCashFutureCommonRunner


class _Master:
    def __init__(self, rows):
        self.rows = rows

    def download(self):
        return self.rows

    def resolve_cash_instrument(self, underlying, exchange):
        if underlying == "NO_CASH" or underlying.startswith("MISS"):
            raise LookupError("missing cash")
        return {"token": "cash-token", "symbol": f"{underlying}-EQ"}


def _expiry(days):
    return (datetime.now(ZoneInfo("Asia/Kolkata")).date() + timedelta(days=days)).strftime("%d%b%Y").upper()


def test_universe_diagnostics_count_each_selection_stage(monkeypatch):
    monkeypatch.setattr(settings, "LIVE_CASH_FUTURE_SYMBOLS", "")
    master = _Master([
        {"exch_seg": "NFO", "instrumenttype": "FUTSTK", "name": "ABC",
         "expiry": _expiry(20), "token": "fut-current", "symbol": "ABC-FUT-C",
         "lotsize": "10"},
        {"exch_seg": "NFO", "instrumenttype": "FUTSTK", "name": "ABC",
         "expiry": _expiry(50), "token": "fut-near", "symbol": "ABC-FUT-N",
         "lotsize": "10"},
        {"exch_seg": "NFO", "instrumenttype": "FUTSTK", "name": "NO_CASH",
         "expiry": _expiry(20), "token": "fut-no-cash", "symbol": "NO_CASH-FUT",
         "lotsize": "10"},
        {"exch_seg": "NSE", "instrumenttype": "EQ", "name": "IGNORE",
         "expiry": "", "token": "ignored", "symbol": "IGNORE-EQ"},
    ])
    runner = LiveCashFutureCommonRunner("ignored", instrument_master=master, manager=object())

    descriptors = runner._build_descriptors()
    stats = runner.snapshot()["universe_diagnostics"]

    assert len(descriptors) == 3  # one cash descriptor and two futures
    assert stats["instrument_master_rows"] == 4
    assert stats["futstk_rows"] == 3
    assert stats["eligible_future_contracts"] == 3
    assert stats["unique_future_contracts"] == 3
    assert stats["cash_resolved_contracts"] == 2
    assert stats["cash_resolution_failures"] == 1
    assert stats["selected_current_near_contracts"] == 2
    assert stats["descriptors_built"] == 3
    assert stats["cash_resolution_failure_samples"] == [
        {"underlying": "NO_CASH", "reason": "LookupError"}
    ]


def test_universe_diagnostic_symbol_samples_are_bounded(monkeypatch):
    monkeypatch.setattr(settings, "LIVE_CASH_FUTURE_SYMBOLS", "")
    rows = [
        {"exch_seg": "NFO", "instrumenttype": "FUTSTK", "name": f"MISS{i}",
         "expiry": _expiry(20), "token": f"token-{i}", "symbol": f"MISS{i}-FUT",
         "lotsize": "1"}
        for i in range(30)
    ]
    runner = LiveCashFutureCommonRunner("ignored", instrument_master=_Master(rows), manager=object())
    runner._build_descriptors()
    stats = runner.snapshot()["universe_diagnostics"]

    assert stats["cash_resolution_failures"] == 30
    assert len(stats["cash_resolution_failure_samples"]) == 20
