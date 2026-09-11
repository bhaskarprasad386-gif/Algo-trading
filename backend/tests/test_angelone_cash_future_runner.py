from datetime import datetime

from app.backtesting import angelone_cash_future_runner as runner


class FakeAuth:
    def __init__(self):
        self.calls = []

    def get_client(self):
        self.calls.append("get_client")
        return object()


def test_runner_authenticates_before_starting_acquisition(monkeypatch):
    auth = FakeAuth()
    service_marker = object()
    acquired = {}

    def fake_builder(ingestion, contract_master, **kwargs):
        acquired["builder"] = kwargs
        return service_marker

    def fake_acquire(**kwargs):
        acquired["acquire"] = kwargs
        return "result"

    monkeypatch.setattr(runner, "build_angelone_cash_future_acquisition_service", fake_builder)
    monkeypatch.setattr(runner, "acquire_cash_future_universe", fake_acquire)

    config = runner.AngelOneCashFutureRunConfig(
        interval_ns=60_000_000_000,
        max_request_ns=86_400_000_000_000,
    )

    result = runner.run_angelone_cash_future_history(
        ingestion=object(),
        contract_master=object(),
        universe=object(),
        master_rows=(),
        start=datetime(2026, 1, 5, 9, 15),
        end=datetime(2026, 1, 5, 15, 30),
        spot_sessions_by_underlying={},
        config=config,
        auth=auth,
    )

    assert result == "result"
    assert auth.calls == ["get_client"]
    assert acquired["builder"]["interval_ns"] == config.interval_ns
    assert acquired["builder"]["max_request_ns"] == config.max_request_ns
    assert acquired["acquire"]["service"] is service_marker
    assert acquired["acquire"]["timeframe"] == "1m"
    assert acquired["acquire"]["mode"] == "BOTH"
