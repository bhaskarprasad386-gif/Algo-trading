from datetime import datetime

import pytest

from app.backtesting import angelone_cash_future_runner as runner


class FakeAuth:
    def __init__(self):
        self.calls = []

    def get_client(self):
        self.calls.append("get_client")
        return object()


def test_runner_config_rejects_request_window_over_one_day():
    with pytest.raises(ValueError, match="must not exceed one day"):
        runner.AngelOneCashFutureRunConfig(
            interval_ns=60_000_000_000,
            max_request_ns=86_400_000_000_001,
        )


def test_runner_authenticates_before_starting_acquisition(monkeypatch):
    auth = FakeAuth()
    service_marker = object()
    pipeline_calls = {}

    def fake_builder(ingestion, contract_master, **kwargs):
        pipeline_calls["builder"] = kwargs
        return service_marker

    def fake_pipeline(**kwargs):
        pipeline_calls["pipeline"] = kwargs
        return "result"

    monkeypatch.setattr(runner, "build_angelone_cash_future_acquisition_service", fake_builder)
    monkeypatch.setattr(runner, "acquire_and_materialize_cash_future_universe", fake_pipeline)

    config = runner.AngelOneCashFutureRunConfig(
        interval_ns=60_000_000_000,
        max_request_ns=86_400_000_000_000,
    )
    db = object()
    catalog = object()

    result = runner.run_angelone_cash_future_history(
        ingestion=object(),
        contract_master=object(),
        universe=object(),
        master_rows=(),
        start=datetime(2026, 1, 5, 9, 15),
        end=datetime(2026, 1, 5, 15, 30),
        spot_sessions_by_underlying={},
        db=db,
        catalog=catalog,
        config=config,
        auth=auth,
    )

    assert result == "result"
    assert auth.calls == ["get_client"]
    assert pipeline_calls["builder"]["interval_ns"] == config.interval_ns
    assert pipeline_calls["builder"]["max_request_ns"] == config.max_request_ns
    assert pipeline_calls["pipeline"]["service"] is service_marker
    assert pipeline_calls["pipeline"]["db"] is db
    assert pipeline_calls["pipeline"]["catalog"] is catalog
    assert pipeline_calls["pipeline"]["timeframe"] == "1m"
    assert pipeline_calls["pipeline"]["mode"] == "BOTH"
