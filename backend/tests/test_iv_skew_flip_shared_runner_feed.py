from types import SimpleNamespace
from time import time_ns

import pytest

from app.scanner.iv_skew_flip.shared_runner_feed import (
    IVSharedRunnerOptionFeedAdapter,
    IVSharedRunnerSpotFeedAdapter,
)
from app.scanner.iv_skew_flip.universe import (
    ResolvedOptionContract,
    ResolvedOptionUniverse,
)


class FakeSharedState:
    def __init__(self, records):
        self.records = list(records)

    def records_snapshot(self):
        return list(self.records)


class FakeSharedRunner:
    def __init__(self, records, descriptors):
        self.state = FakeSharedState(records)
        self.lifecycle = SimpleNamespace(
            option_descriptors=lambda: tuple(descriptors),
        )
        self.stop_calls = 0

    def snapshot(self):
        return {"enabled": True, "initial_option_bootstrap_done": True}

    def stop(self):
        self.stop_calls += 1


def make_universe(underlying="BANKNIFTY", expiry="2026-11-26"):
    contracts = []
    strikes = (58000.0, 58100.0, 58200.0)
    for strike in strikes:
        for side in ("CE", "PE"):
            token = f"{underlying}-{int(strike)}-{side}"
            contracts.append(ResolvedOptionContract(
                symbol=token, token=token, exchange="NFO", expiry=expiry,
                strike=strike, option_type=side, lot_size=30,
            ))
    return ResolvedOptionUniverse(
        underlying=underlying, expiry_type="MONTHLY", expiry=expiry,
        spot_exchange="NSE", option_exchange="NFO", strike_step=100.0,
        atm_strike=58100.0, strikes=strikes, contracts=tuple(contracts),
    )


def make_records(universe, now):
    records = [
        {
            "instrument_type": "index", "underlying": "NIFTY",
            "timestamp_ns": now, "ltp": 24000.0,
        },
        {
            "instrument_type": "index", "underlying": "BANKNIFTY",
            "timestamp_ns": now, "ltp": 58125.0,
        },
    ]
    for contract in universe.contracts:
        records.append({
            "instrument_type": "option",
            "underlying": universe.underlying,
            "expiry": universe.expiry,
            "token": contract.token,
            "strike": contract.strike,
            "option_type": contract.option_type,
            "timestamp_ns": now,
            "ltp": 120.0,
            "bid": 119.0,
            "ask": 121.0,
            "bid_qty": 100,
            "ask_qty": 120,
            "oi": 50000,
            "volume": 1000,
            "fresh": True,
        })
    return records


def make_descriptors(universe):
    return [
        SimpleNamespace(
            key=SimpleNamespace(token=contract.token),
            underlying=universe.underlying,
            expiry=universe.expiry,
        )
        for contract in universe.contracts
    ]


def test_shared_runner_spot_adapter_reads_both_indices_without_owning_subscriptions():
    now = time_ns()
    universe = make_universe()
    runner = FakeSharedRunner(make_records(universe, now), make_descriptors(universe))
    adapter = IVSharedRunnerSpotFeedAdapter(shared_runner=runner, clock_ns=lambda: now)

    result = adapter.start(object(), ("NIFTY", "BANKNIFTY"))
    assert result["status"] == "ATTACHED"
    assert adapter.spot_snapshot("NIFTY").price == 24000.0
    assert adapter.spot_snapshot("BANKNIFTY").price == 58125.0
    assert adapter.snapshot()["signals_allowed"] is True

    adapter.stop()
    assert runner.stop_calls == 0


def test_shared_runner_option_adapter_reads_only_the_requested_bank_monthly_window():
    now = time_ns()
    universe = make_universe()
    runner = FakeSharedRunner(make_records(universe, now), make_descriptors(universe))
    adapter = IVSharedRunnerOptionFeedAdapter(
        shared_runner=runner, stream_id="BANKNIFTY_MONTHLY",
        clock_ns=lambda: now,
    )

    attached = adapter.start(universe)
    assert attached["status"] == "ATTACHED"
    assert attached["contract_count"] == 6
    assert attached["owns_subscriptions"] is False
    quotes = adapter.latest_quotes()
    assert len(quotes) == 6
    assert {quote.underlying for quote in quotes} == {"BANKNIFTY"}
    assert {quote.expiry for quote in quotes} == {"2026-11-26"}
    assert adapter.snapshot()["status"] == "LIVE"

    adapter.stop()
    assert runner.stop_calls == 0


def test_shared_runner_option_adapter_fails_closed_on_expiry_or_token_mismatch():
    now = time_ns()
    universe = make_universe()
    records = make_records(universe, now)
    descriptors = make_descriptors(universe)
    runner = FakeSharedRunner(records, descriptors)
    wrong_expiry = make_universe(expiry="2026-12-31")
    adapter = IVSharedRunnerOptionFeedAdapter(
        shared_runner=runner, stream_id="BANKNIFTY_MONTHLY",
        clock_ns=lambda: now,
    )

    with pytest.raises(RuntimeError, match="UNIVERSE_MISMATCH|EXPIRY_MISMATCH"):
        adapter.start(wrong_expiry)
    assert adapter.latest_quotes() == ()
    assert runner.stop_calls == 0
