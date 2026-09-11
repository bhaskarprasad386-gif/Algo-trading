from datetime import date, datetime

import pytest

from app.backtesting import angelone_cash_future_batch_runner as batch_runner
from app.backtesting.cash_future_universe import CashFutureFnoUniverse, CashFutureUniverseItem
from app.backtesting.historical_job_store import HistoricalJobStore


class FakePipelineResult:
    def __init__(self):
        self.require_calls = 0

    def require_backtest_ready(self):
        self.require_calls += 1


def _universe():
    def item(symbol, token):
        return CashFutureUniverseItem(
            symbol,
            "2026-10",
            token,
            f"{symbol}26OCT",
            date(2026, 10, 29),
            100,
        )

    return CashFutureFnoUniverse(
        stocks=(item("ZZZ", "3"), item("AAA", "1"), item("BBB", "2")),
        indices=(item("NIFTY", "9"),),
    )


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
        ingestion=object(),
        contract_master=object(),
        universe=_universe(),
        master_rows=(),
        start=datetime(2026, 10, 1),
        end=datetime(2026, 10, 2),
        spot_sessions_by_underlying={},
        db=object(),
        catalog=object(),
        config=batch_runner.AngelOneCashFutureRunConfig(
            interval_ns=60_000_000_000,
            max_request_ns=86_400_000_000_000,
        ),
        batch_size=2,
        job_store=store,
        run_id="run-1",
    )

    assert calls == [0, 2]
    assert tuple(item.stock_underlyings for item in output) == (("AAA", "BBB"), ("ZZZ",))
    assert all(not item.skipped for item in output)
    assert all(result.require_calls == 1 for result in results.values())
    assert store.get("run-1:cash-future-stock-batch:0").state == "completed"
    assert store.get("run-1:cash-future-stock-batch:1").state == "completed"
    store.close()


def test_completed_batches_are_skipped_after_restart(monkeypatch):
    calls = []

    def fake_run(**kwargs):
        calls.append(kwargs["config"].stock_batch_offset)
        return FakePipelineResult()

    monkeypatch.setattr(batch_runner, "run_angelone_cash_future_history", fake_run)
    store = HistoricalJobStore()
    config = batch_runner.AngelOneCashFutureRunConfig(
        interval_ns=60_000_000_000,
        max_request_ns=86_400_000_000_000,
    )
    kwargs = dict(
        ingestion=object(),
        contract_master=object(),
        universe=_universe(),
        master_rows=(),
        start=datetime(2026, 10, 1),
        end=datetime(2026, 10, 2),
        spot_sessions_by_underlying={},
        db=object(),
        catalog=object(),
        config=config,
        batch_size=2,
        job_store=store,
        run_id="run-2",
    )

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
    config = batch_runner.AngelOneCashFutureRunConfig(
        interval_ns=60_000_000_000,
        max_request_ns=86_400_000_000_000,
    )
    kwargs = dict(
        ingestion=object(),
        contract_master=object(),
        universe=_universe(),
        master_rows=(),
        start=datetime(2026, 10, 1),
        end=datetime(2026, 10, 2),
        spot_sessions_by_underlying={},
        db=object(),
        catalog=object(),
        config=config,
        batch_size=2,
        job_store=store,
        run_id="run-3",
    )
    batch_runner.run_angelone_cash_future_history_in_batches(**kwargs)

    changed = _universe()
    changed = CashFutureFnoUniverse(
        stocks=changed.stocks + (CashFutureUniverseItem("CCC", "2026-10", "4", "CCC26OCT", date(2026, 10, 29), 100),),
        indices=changed.indices,
    )
    with pytest.raises(ValueError, match="batch plan changed"):
        batch_runner.run_angelone_cash_future_history_in_batches(
            **{**kwargs, "universe": changed}
        )
    store.close()


def test_changed_download_config_is_rejected_after_restart(monkeypatch):
    monkeypatch.setattr(batch_runner, "run_angelone_cash_future_history", lambda **kwargs: FakePipelineResult())
    store = HistoricalJobStore()
    base = batch_runner.AngelOneCashFutureRunConfig(
        interval_ns=60_000_000_000,
        max_request_ns=86_400_000_000_000,
        timeframe="1m",
    )
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
        config=base,
        batch_size=2,
        job_store=store,
        run_id="run-config",
    )
    batch_runner.run_angelone_cash_future_history_in_batches(**kwargs)

    changed = batch_runner.AngelOneCashFutureRunConfig(
        interval_ns=60_000_000_000,
        max_request_ns=86_400_000_000_000,
        timeframe="5m",
    )
    with pytest.raises(ValueError, match="batch plan changed"):
        batch_runner.run_angelone_cash_future_history_in_batches(
            **{**kwargs, "config": changed}
        )
    store.close()


def test_invalid_multi_day_window_is_rejected(monkeypatch):
    monkeypatch.setattr(batch_runner, "run_angelone_cash_future_history", lambda **kwargs: FakePipelineResult())
    store = HistoricalJobStore()
    config = batch_runner.AngelOneCashFutureRunConfig(
        interval_ns=60_000_000_000,
        max_request_ns=86_400_000_000_000,
    )
    with pytest.raises(ValueError, match="start must be before end"):
        batch_runner.run_angelone_cash_future_history_in_batches(
            ingestion=object(),
            contract_master=object(),
            universe=_universe(),
            master_rows=(),
            start=datetime(2026, 10, 3),
            end=datetime(2026, 10, 3),
            spot_sessions_by_underlying={},
            db=object(),
            catalog=object(),
            config=config,
            batch_size=2,
            job_store=store,
            run_id="run-invalid-window",
        )
    store.close()
