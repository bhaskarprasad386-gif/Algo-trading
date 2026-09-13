from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.backtesting import angelone_cash_future_batch_runner as batch_runner
from app.backtesting.cash_future_universe import CashFutureFnoUniverse, CashFutureUniverseItem
from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord
from app.backtesting.historical_catalog import HistoricalCatalog
from app.backtesting.historical_job_store import HistoricalJobStore
from app.backtesting.session_gap_planner import SessionWindow
from app.core.database import Base
from app.models.cash_future_history import CashFutureHistory

MARKET_TZ = ZoneInfo("Asia/Kolkata")


class FakePipelineResult:
    def __init__(self):
        self.require_calls = 0

    def require_backtest_ready(self):
        self.require_calls += 1


def _universe():
    def item(symbol, token, expiry):
        return CashFutureUniverseItem(symbol, "2026-10", token, f"{symbol}26OCTFUT", expiry, 100)

    return CashFutureFnoUniverse(
        stocks=(item("ZZZ", "3002", date(2026, 10, 29)), item("AAA", "3001", date(2026, 10, 29))),
        indices=(),
    )


def _config(**overrides):
    values = {"interval_ns": 60_000_000_000, "max_request_ns": 86_400_000_000_000, "chunk_days": 30, "retry_attempts": 1, "max_repair_passes": 1}
    values.update(overrides)
    return batch_runner.AngelOneCashFutureRunConfig(**values)


def _session(day: date) -> SessionWindow:
    start = datetime.combine(day, time(9, 15), tzinfo=MARKET_TZ)
    end = datetime.combine(day, time(9, 16), tzinfo=MARKET_TZ)
    return SessionWindow(int(start.timestamp() * 1_000_000_000), int(end.timestamp() * 1_000_000_000))


class FakeClient:
    def __init__(self):
        self.requests = []

    def getCandleData(self, params):
        self.requests.append(dict(params))
        day = datetime.strptime(params["fromdate"], "%Y-%m-%d %H:%M").date()
        token = params["symboltoken"]
        base = 100.0 if token in {"3001", "4001"} else 200.0
        return {"status": True, "data": [
            [f"{day} 09:15", base, base + 1, base - 1, base + 0.5, 1000, 250],
            [f"{day} 09:16", base + 0.5, base + 2, base, base + 1.5, 1200, 275],
        ]}


class FakeAuth:
    def __init__(self, client):
        self.client = client
        self.calls = 0

    def get_client(self):
        self.calls += 1
        return self.client


class FakeLimiter:
    def __init__(self):
        self.calls = 0

    def acquire(self):
        self.calls += 1


def test_batches_run_in_order_and_persist_completion(monkeypatch):
    calls = []
    results = {}

    def fake_run(**kwargs):
        calls.append(kwargs["config"].stock_batch_offset)
        result = FakePipelineResult()
        results[kwargs["config"].stock_batch_offset] = result
        return result

    monkeypatch.setattr(batch_runner, "run_angelone_cash_future_history", fake_run)
    store = HistoricalJobStore()
    output = batch_runner.run_angelone_cash_future_history_in_batches(
        ingestion=object(), contract_master=object(), universe=_universe(), master_rows=(),
        start=datetime(2026, 10, 1), end=datetime(2026, 10, 2), spot_sessions_by_underlying={},
        db=object(), catalog=object(), config=_config(), batch_size=1, job_store=store, run_id="run-1",
    )
    assert calls == [0, 1]
    assert tuple(item.stock_underlyings for item in output) == (("AAA",), ("ZZZ",))
    assert all(not item.skipped for item in output)
    assert all(result.require_calls == 1 for result in results.values())
    assert store.get("run-1:cash-future-stock-batch:0").state == "completed"
    assert store.get("run-1:cash-future-stock-batch:1").state == "completed"
    store.close()


def test_completed_batches_are_skipped_after_restart(monkeypatch):
    calls = []
    monkeypatch.setattr(batch_runner, "run_angelone_cash_future_history", lambda **kwargs: (calls.append(kwargs["config"].stock_batch_offset) or FakePipelineResult()))
    store = HistoricalJobStore()
    kwargs = dict(ingestion=object(), contract_master=object(), universe=_universe(), master_rows=(), start=datetime(2026, 10, 1), end=datetime(2026, 10, 2), spot_sessions_by_underlying={}, db=object(), catalog=object(), config=_config(), batch_size=1, job_store=store, run_id="run-2")
    first = batch_runner.run_angelone_cash_future_history_in_batches(**kwargs)
    calls.clear()
    second = batch_runner.run_angelone_cash_future_history_in_batches(**kwargs)
    assert calls == []
    assert tuple(item.skipped for item in first) == (False, False)
    assert tuple(item.skipped for item in second) == (True, True)
    store.close()


def test_changed_batch_plan_is_rejected(monkeypatch):
    monkeypatch.setattr(batch_runner, "run_angelone_cash_future_history", lambda **kwargs: FakePipelineResult())
    store = HistoricalJobStore()
    kwargs = dict(ingestion=object(), contract_master=object(), universe=_universe(), master_rows=(), start=datetime(2026, 10, 1), end=datetime(2026, 10, 2), spot_sessions_by_underlying={}, db=object(), catalog=object(), config=_config(), batch_size=1, job_store=store, run_id="run-3")
    batch_runner.run_angelone_cash_future_history_in_batches(**kwargs)
    changed = _universe()
    changed = CashFutureFnoUniverse(stocks=changed.stocks + (CashFutureUniverseItem("CCC", "2026-10", "3003", "CCC26OCTFUT", date(2026, 10, 29), 100),), indices=changed.indices)
    with pytest.raises(ValueError, match="batch plan changed"):
        batch_runner.run_angelone_cash_future_history_in_batches(**{**kwargs, "universe": changed})
    store.close()


def test_changed_download_config_is_rejected_after_restart(monkeypatch):
    monkeypatch.setattr(batch_runner, "run_angelone_cash_future_history", lambda **kwargs: FakePipelineResult())
    store = HistoricalJobStore()
    kwargs = dict(ingestion=object(), contract_master=object(), universe=_universe(), master_rows=(), start=datetime(2026, 10, 1), end=datetime(2026, 10, 3), spot_sessions_by_underlying={}, db=object(), catalog=object(), config=_config(timeframe="1m"), batch_size=1, job_store=store, run_id="run-config")
    batch_runner.run_angelone_cash_future_history_in_batches(**kwargs)
    with pytest.raises(ValueError, match="batch plan changed"):
        batch_runner.run_angelone_cash_future_history_in_batches(**{**kwargs, "config": _config(timeframe="5m")})
    store.close()


def test_invalid_multi_day_window_is_rejected(monkeypatch):
    monkeypatch.setattr(batch_runner, "run_angelone_cash_future_history", lambda **kwargs: FakePipelineResult())
    store = HistoricalJobStore()
    with pytest.raises(ValueError, match="start must be before end"):
        batch_runner.run_angelone_cash_future_history_in_batches(
            ingestion=object(), contract_master=object(), universe=_universe(), master_rows=(),
            start=datetime(2026, 10, 3), end=datetime(2026, 10, 3), spot_sessions_by_underlying={},
            db=object(), catalog=object(), config=_config(), batch_size=1, job_store=store,
            run_id="run-invalid-window",
        )
    store.close()


def test_multi_stock_multi_day_execution_preserves_generator_and_window(monkeypatch):
    calls = []
    master_rows = (row for row in ({"symbol": "AAA"}, {"symbol": "BBB"}, {"symbol": "ZZZ"}))
    def fake_run(**kwargs):
        calls.append((kwargs["config"].stock_batch_offset, kwargs["start"], kwargs["end"], tuple(kwargs["master_rows"])))
        return FakePipelineResult()
    monkeypatch.setattr(batch_runner, "run_angelone_cash_future_history", fake_run)
    store = HistoricalJobStore()
    start = datetime(2026, 10, 1, 9, 15)
    end = datetime(2026, 10, 3, 15, 30)
    output = batch_runner.run_angelone_cash_future_history_in_batches(
        ingestion=object(), contract_master=object(), universe=_universe(), master_rows=master_rows,
        start=start, end=end, spot_sessions_by_underlying={}, db=object(), catalog=object(),
        config=_config(), batch_size=1, job_store=store, run_id="run-multi-day",
    )
    assert [call[0] for call in calls] == [0, 1]
    assert all(call[1] == start and call[2] == end for call in calls)
    assert all(call[3] == ({"symbol": "AAA"}, {"symbol": "BBB"}, {"symbol": "ZZZ"}) for call in calls)
    assert tuple(item.skipped for item in output) == (False, False)
    store.close()


def test_real_angelone_multi_stock_multi_day_runner_materializes_sqlite(tmp_path):
    client = FakeClient()
    auth = FakeAuth(client)
    limiter = FakeLimiter()
    contracts = ContractMasterCatalog()
    contracts.upsert_snapshot(date(2026, 10, 1), [
        ContractRecord("NFO", "AAA26OCTFUT", "4001", date(2026, 10, 29), "STOCK_FUTURE", "AAA", 100),
        ContractRecord("NFO", "ZZZ26OCTFUT", "4002", date(2026, 10, 29), "STOCK_FUTURE", "ZZZ", 100),
    ])
    catalog_path = str(tmp_path / "cash_future_catalog.db")
    catalog = HistoricalCatalog(catalog_path)
    from app.backtesting.historical_ingest import HistoricalIngestionService
    ingestion = HistoricalIngestionService(catalog)
    days = (date(2026, 10, 1), date(2026, 10, 2))
    sessions = {symbol: tuple(_session(day) for day in days) for symbol in ("AAA", "ZZZ")}
    future_sessions = {
        "NFO:4001:AAA26OCTFUT": tuple(_session(day) for day in days),
        "NFO:4002:ZZZ26OCTFUT": tuple(_session(day) for day in days),
    }
    master_rows = [
        {"exch_seg": "NSE", "symbol": "AAA-EQ", "name": "AAA", "token": "3001", "instrumenttype": "EQ"},
        {"exch_seg": "NSE", "symbol": "ZZZ-EQ", "name": "ZZZ", "token": "3002", "instrumenttype": "EQ"},
    ]
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    store = HistoricalJobStore()
    db = Session(engine)
    output = batch_runner.run_angelone_cash_future_history_in_batches(
        ingestion=ingestion, contract_master=contracts, universe=_universe(), master_rows=master_rows,
        start=datetime(2026, 10, 1, 9, 15), end=datetime(2026, 10, 2, 9, 16),
        spot_sessions_by_underlying=sessions, future_sessions_by_instrument=future_sessions,
        db=db, catalog=catalog, config=_config(), batch_size=1, job_store=store, run_id="real-e2e",
        auth=auth, limiter=limiter, margin_required=1000.0,
    )
    assert tuple(item.stock_underlyings for item in output) == (("AAA",), ("ZZZ",))
    assert all(item.result.backtest_ready for item in output)
    assert auth.calls == 6
    assert limiter.calls >= 4
    assert len(client.requests) >= 4
    rows = db.scalars(select(CashFutureHistory)).all()
    assert len(rows) == 8
    assert {row.symbol for row in rows} == {"AAA", "ZZZ"}
    assert {row.contract_month for row in rows} == {"2026-10"}
    assert all(row.cash_price is not None and row.future_price is not None for row in rows)
    assert all(row.future_price > row.cash_price for row in rows)
    db.close()
    store.close()
    contracts.close()
    catalog.close()

    reopened = HistoricalCatalog(catalog_path)
    assert reopened.count(source="angelone", timeframe="1m") == 16
    assert len(reopened.records(source="angelone", instrument="NSE:3001:AAA-EQ", timeframe="1m")) == 4
    assert len(reopened.records(source="angelone", instrument="NFO:4002:ZZZ26OCTFUT", timeframe="1m")) == 4
    reopened.close()


def test_failed_batch_is_recoverable_without_replaying_completed_batches(tmp_path, monkeypatch):
    calls = []
    failures_left = {1: 1}

    def fake_run(**kwargs):
        offset = kwargs["config"].stock_batch_offset
        calls.append(offset)
        if failures_left.get(offset, 0):
            failures_left[offset] -= 1
            raise RuntimeError("temporary provider failure")
        return FakePipelineResult()

    monkeypatch.setattr(batch_runner, "run_angelone_cash_future_history", fake_run)
    store_path = str(tmp_path / "jobs.db")
    store = HistoricalJobStore(store_path)
    kwargs = dict(
        ingestion=object(),
        contract_master=object(),
        universe=_universe(),
        master_rows=(),
        start=datetime(2026, 10, 1),
        end=datetime(2026, 10, 3),
        spot_sessions_by_underlying={},
        db=object(),
        catalog=object(),
        config=_config(),
        batch_size=1,
        job_store=store,
        run_id="run-recover",
    )

    with pytest.raises(RuntimeError, match="temporary provider failure"):
        batch_runner.run_angelone_cash_future_history_in_batches(**kwargs)

    assert calls == [0, 1]
    assert store.get("run-recover:cash-future-stock-batch:0").state == "completed"
    assert store.get("run-recover:cash-future-stock-batch:1").state == "recoverable"
    store.close()

    restarted = HistoricalJobStore(store_path)
    output = batch_runner.run_angelone_cash_future_history_in_batches(**kwargs)

    assert calls == [0, 1, 1]
    assert tuple(item.skipped for item in output) == (True, False)
    assert restarted.get("run-recover:cash-future-stock-batch:0").state == "completed"
    assert restarted.get("run-recover:cash-future-stock-batch:1").state == "completed"
    restarted.close()


def test_cancelled_batch_stops_later_batches_and_resumes_explicitly(monkeypatch):
    calls = []
    store = HistoricalJobStore()
    original_run = batch_runner.run_angelone_cash_future_history

    def fake_run(**kwargs):
        offset = kwargs["config"].stock_batch_offset
        calls.append(offset)
        if offset == 0:
            store.cancel("run-cancel:cash-future-stock-batch:0", reason="operator stop")
        return FakePipelineResult()

    monkeypatch.setattr(batch_runner, "run_angelone_cash_future_history", fake_run)
    kwargs = dict(
        ingestion=object(), contract_master=object(), universe=_universe(), master_rows=(),
        start=datetime(2026, 10, 1), end=datetime(2026, 10, 2), spot_sessions_by_underlying={},
        db=object(), catalog=object(), config=_config(), batch_size=1, job_store=store, run_id="run-cancel",
    )

    first = batch_runner.run_angelone_cash_future_history_in_batches(**kwargs)
    assert calls == [0]
    assert first == ()
    assert store.get("run-cancel:cash-future-stock-batch:0").state == "cancelled"

    store.reopen_cancelled("run-cancel:cash-future-stock-batch:0")
    monkeypatch.setattr(batch_runner, "run_angelone_cash_future_history", lambda **kwargs: (calls.append(kwargs["config"].stock_batch_offset) or FakePipelineResult()))
    second = batch_runner.run_angelone_cash_future_history_in_batches(**kwargs)
    assert calls == [0, 0, 1]
    assert tuple(item.skipped for item in second) == (False, False)
    assert store.get("run-cancel:cash-future-stock-batch:0").state == "completed"
    assert store.get("run-cancel:cash-future-stock-batch:1").state == "completed"
    assert original_run is not None
    store.close()