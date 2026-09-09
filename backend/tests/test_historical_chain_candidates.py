from app.backtesting.arbitrage_chain_candidates import (
    HistoricalArbitrageChainCandidates,
    HistoricalChainSnapshot,
)
from app.backtesting.arbitrage_chain_selector import ChainContract


def make_chain():
    return tuple(
        ChainContract(1, "NSE", "TEST", "STOCK", 20261001, float(i), "CE", 50, 10000, 20000, 10, 10.1)
        for i in range(41)
    )


def test_box_candidates_apply_exact_five_plus_five_rule():
    snapshot = HistoricalChainSnapshot(1, "TEST", 20261001, make_chain(), 20)
    selected = HistoricalArbitrageChainCandidates.box_stock(snapshot)
    assert [c.strike for c in selected] == [15, 16, 17, 18, 19, 21, 22, 23, 24, 25]


def test_liquidity_filter_rejects_non_executable_contracts_without_replacement():
    contracts = list(make_chain())
    contracts[15] = ChainContract(1, "NSE", "TEST", "STOCK", 20261001, 15, "CE", 50, 0, 0, 10, 10.1)
    snapshot = HistoricalChainSnapshot(1, "TEST", 20261001, tuple(contracts), 20)
    try:
        HistoricalArbitrageChainCandidates.box_stock(snapshot, min_volume=1, min_oi=1)
    except ValueError as exc:
        assert "incomplete" in str(exc)
    else:
        raise AssertionError("incomplete historical chain must not be repaired with another strike")


def test_calendar_candidates_preserve_real_expiry_pairs():
    contracts = make_chain() + (
        ChainContract(1, "NSE", "TEST", "STOCK", 20261101, 20, "CE", 50, 100, 200, 10, 10.1),
        ChainContract(1, "NSE", "TEST", "STOCK", 20261201, 20, "CE", 50, 100, 200, 10, 10.1),
    )
    assert HistoricalArbitrageChainCandidates.calendar_expiry_pairs(contracts) == (
        (20261001, 20261101), (20261001, 20261201), (20261101, 20261201)
    )
