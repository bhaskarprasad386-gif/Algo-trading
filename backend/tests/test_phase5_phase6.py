from datetime import date

from app.backtesting.arbitrage_chain_candidates import (
    HistoricalArbitrageChainCandidates,
    HistoricalChainSnapshot,
)
from app.backtesting.arbitrage_discovery import ArbitrageInstrumentDiscovery
from app.backtesting.execution import (
    DepthLevel,
    ExecutionConfig,
    ExecutionSide,
    ExecutionSimulator,
    OrderBook,
    SimOrder,
)


def _row(
    token,
    symbol,
    name="NIFTY",
    expiry="30SEP2026",
    typ="OPTIDX",
    segment="NFO",
    strike="2500000.000000",
    lotsize="75",
):
    return {
        "token": str(token),
        "symbol": symbol,
        "name": name,
        "expiry": expiry,
        "strike": strike,
        "lotsize": lotsize,
        "instrumenttype": typ,
        "exch_seg": segment,
    }


def test_phase6_discovers_dynamic_universe_and_expiries_from_concrete_master_rows():
    rows = [
        _row("1", "NIFTY30SEP2625000CE"),
        _row("2", "NIFTY30SEP2625000PE"),
        _row("3", "NIFTY30OCT2625000CE", expiry="30OCT2026"),
        _row("4", "NIFTY30OCT2625000PE", expiry="30OCT2026"),
        _row(
            "5",
            "SBIN30SEP26600CE",
            name="SBIN",
            typ="OPTSTK",
            strike="60000.000000",
            lotsize="750",
        ),
        _row(
            "6",
            "SBIN30SEP26600PE",
            name="SBIN",
            typ="OPTSTK",
            strike="60000.000000",
            lotsize="750",
        ),
        _row(
            "7",
            "ABC30SEP26100CE",
            name="ABC",
            typ="OPTSTK",
            strike="10000.000000",
            lotsize="100",
        ),
        _row(
            "8",
            "ABC30SEP26100PE",
            name="ABC",
            typ="OPTSTK",
            strike="10000.000000",
            lotsize="100",
        ),
    ]
    universe = ArbitrageInstrumentDiscovery.discover_universe(
        rows,
        as_of=date(2026, 9, 1),
        nifty50_symbols={"SBIN"},
    )
    assert universe.stocks == ("SBIN",)
    assert universe.indexes == ("NIFTY",)
    assert universe.expiries == (date(2026, 9, 30), date(2026, 10, 30))

    expiries = ArbitrageInstrumentDiscovery.discover_expiries(
        rows,
        underlying="NIFTY",
        instrument_class="INDEX",
        as_of=date(2026, 9, 1),
        exchange="NFO",
    )
    assert expiries == (date(2026, 9, 30), date(2026, 10, 30))


def test_phase6_enumerates_full_point_in_time_chain_without_inventing_legs():
    rows = [
        _row("1", "NIFTY30SEP2624900CE", strike="2490000.000000"),
        _row("2", "NIFTY30SEP2624900PE", strike="2490000.000000"),
        _row("3", "NIFTY30SEP2625000CE"),
        _row("4", "NIFTY30SEP2625000PE"),
        _row("5", "NIFTY30SEP2625100CE", strike="2510000.000000"),
        _row("6", "NIFTY30SEP2625100PE", strike="2510000.000000"),
    ]
    quotes = {
        str(i): {
            "timestamp_ns": 9_000,
            "bid": 10 + i,
            "ask": 11 + i,
            "volume": 1000,
            "oi": 2000,
        }
        for i in range(1, 7)
    }
    result = ArbitrageInstrumentDiscovery.enumerate_chain(
        rows,
        underlying="NIFTY",
        instrument_class="INDEX",
        exchange="NFO",
        expiry=date(2026, 9, 30),
        timestamp_ns=9_000,
        quotes_by_token=quotes,
    )
    assert len(result.contracts) == 6
    assert {c.strike for c in result.contracts} == {24900.0, 25000.0, 25100.0}
    assert {c.option_type for c in result.contracts} == {"CE", "PE"}

    snapshot = HistoricalChainSnapshot(
        timestamp_ns=9_000,
        underlying="NIFTY",
        expiry=20260930,
        contracts=result.contracts,
        atm=25000.0,
    )
    # Synthetic selection uses the nearest available strike on each side of
    # ATM; ATM itself is not a synthetic leg. With one strike on each side,
    # the genuine executable candidate set therefore contains four contracts.
    assert len(HistoricalArbitrageChainCandidates.synthetic_index(snapshot)) == 4


def test_phase5_multileg_execution_keeps_partial_leg_failure_non_executable():
    simulator = ExecutionSimulator(
        ExecutionConfig(slippage_bps=5, latency_ns=1_000, fee_per_unit=1)
    )
    buy = SimOrder("L1", "OPT-A", ExecutionSide.BUY, 50, submitted_at_ns=10_000)
    sell = SimOrder("L2", "OPT-B", ExecutionSide.SELL, 50, submitted_at_ns=10_000)
    result = simulator.execute_many_atomic(
        [
            (buy, OrderBook(asks=(DepthLevel(100, 50),)), 11_000),
            (sell, OrderBook(bids=(DepthLevel(99, 20),)), 11_000),
        ]
    )
    assert result.rejected
    assert result.reason == "atomic rollback: one or more legs did not fully execute"
    assert result.fills == ()
    assert result.leg_results[0].fills[0].filled_at_ns == 12_000
    assert result.leg_results[1].remaining_quantity == 30
