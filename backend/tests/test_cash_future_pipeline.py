from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.backtesting.cash_future_pipeline import CashFutureBacktestPipeline
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord


class FakeReadiness:
    def __init__(self, complete: bool = True):
        self.complete = complete
        self.calls = []

    def require_complete(self, **kwargs):
        self.calls.append(kwargs)
        if not self.complete:
            raise LookupError("not ready")
        return {"complete": True}


class FakeContractCatalog:
    def __init__(self):
        self.calls = []

    def resolve(self, *, exchange, underlying, as_of, mode):
        self.calls.append((exchange, underlying, as_of, mode))
        values = {
            "CURRENT": {"instrument": "NIFTY-CURRENT", "lot_size": 50},
            "NEAR": {"instrument": "NIFTY-NEAR", "lot_size": 50},
        }
        return values[mode]


def _catalog():
    catalog = HistoricalCatalog()
    rows = []
    for instrument, prices in {
        "SPOT": (100.0, 103.0),
        "NIFTY-CURRENT": (105.0, 101.0),
        "NIFTY-NEAR": (106.0, 102.0),
    }.items():
        for ts, price in zip((1_000_000_000, 2_000_000_000), prices):
            rows.append(HistoricalRecord("test", instrument, "1s", ts, {"close": price}))
    catalog.ingest(rows)
    return catalog


def _run(mode: str):
    contracts = FakeContractCatalog()
    readiness = FakeReadiness()
    pipeline = CashFutureBacktestPipeline(
        contract_catalog=contracts,
        historical_catalog=_catalog(),
        readiness=readiness,
    )
    result = pipeline.run(
        exchange="NSE",
        underlying="NIFTY",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 1, 2, tzinfo=timezone.utc),
        mode=mode,
        source="test",
        timeframe="1s",
        interval_ns=1_000_000_000,
        queue=object(),
        spot_instrument="SPOT",
        quantity=1,
        default_lot_size=25,
        entry_timestamp_ns=1_000_000_000,
        exit_timestamp_ns=2_000_000_000,
    )
    return result, contracts, readiness


def test_current_pipeline_resolves_and_replays():
    result, contracts, readiness = _run("CURRENT")
    assert result.mode == "CURRENT"
    assert result.contract_instruments == {"CURRENT": "NIFTY-CURRENT"}
    assert len(result.bars) == 2
    assert len(result.trades) == 1
    assert result.trades[0].result.gross_pnl == pytest.approx(350.0)
    assert contracts.calls[-1][3] == "CURRENT"
    assert readiness.calls


def test_near_pipeline_resolves_and_replays():
    result, contracts, _ = _run("NEAR")
    assert result.contract_instruments == {"NEAR": "NIFTY-NEAR"}
    assert len(result.trades) == 1
    assert result.trades[0].result.gross_pnl == pytest.approx(350.0)
    assert contracts.calls[-1][3] == "NEAR"


def test_both_pipeline_synchronizes_and_returns_combined_pnl():
    result, contracts, _ = _run("BOTH")
    assert result.contract_instruments == {
        "CURRENT": "NIFTY-CURRENT",
        "NEAR": "NIFTY-NEAR",
    }
    assert len(result.bars) == 2
    trade = result.trades[0]
    assert trade.current_instrument == "NIFTY-CURRENT"
    assert trade.near_instrument == "NIFTY-NEAR"
    assert trade.gross_pnl == pytest.approx(700.0)
    assert {call[3] for call in contracts.calls} == {"CURRENT", "NEAR"}


def test_readiness_failure_happens_before_contract_or_data_replay():
    contracts = FakeContractCatalog()
    readiness = FakeReadiness(complete=False)
    pipeline = CashFutureBacktestPipeline(
        contract_catalog=contracts,
        historical_catalog=_catalog(),
        readiness=readiness,
    )
    with pytest.raises(LookupError, match="not ready"):
        pipeline.run(
            exchange="NSE", underlying="NIFTY",
            start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end=datetime(2026, 1, 2, tzinfo=timezone.utc), mode="CURRENT",
            source="test", timeframe="1s", interval_ns=1_000_000_000,
            queue=object(), spot_instrument="SPOT", quantity=1,
            default_lot_size=25, entry_timestamp_ns=1_000_000_000,
            exit_timestamp_ns=2_000_000_000,
        )
    assert contracts.calls == []


def test_pipeline_does_not_allow_unknown_price_payload():
    catalog = HistoricalCatalog()
    catalog.ingest([
        HistoricalRecord("test", "SPOT", "1s", 1_000_000_000, {"close": 100}),
        HistoricalRecord("test", "NIFTY-CURRENT", "1s", 1_000_000_000, {"volume": 10}),
    ])
    pipeline = CashFutureBacktestPipeline(
        contract_catalog=FakeContractCatalog(),
        historical_catalog=catalog,
        readiness=FakeReadiness(),
    )
    with pytest.raises(ValueError, match="no positive price"):
        pipeline.run(
            exchange="NSE", underlying="NIFTY",
            start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end=datetime(2026, 1, 2, tzinfo=timezone.utc), mode="CURRENT",
            source="test", timeframe="1s", interval_ns=1_000_000_000,
            queue=object(), spot_instrument="SPOT", quantity=1,
            default_lot_size=25, entry_timestamp_ns=1_000_000_000,
            exit_timestamp_ns=2_000_000_000,
        )
