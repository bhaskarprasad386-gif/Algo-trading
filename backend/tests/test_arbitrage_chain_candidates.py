from backend.app.backtesting.arbitrage_chain_candidates import (
    HistoricalArbitrageChainCandidates,
    HistoricalChainSnapshot,
)
from backend.app.backtesting.arbitrage_chain_selector import ChainContract


def contracts(instrument_class="STOCK", count=41, *, volume=10000, oi=20000):
    return tuple(
        ChainContract(1, "NSE", "TEST", instrument_class, 20261001, float(i), "CE",
                      50, volume, oi, 10, 10.1)
        for i in range(count)
    )


def snapshot(instrument_class="STOCK", count=41, *, volume=10000, oi=20000):
    values = contracts(instrument_class, count, volume=volume, oi=oi)
    return HistoricalChainSnapshot(1, "TEST", 20261001, values, 20)


def test_box_stock_candidates_are_side_only_and_liquidity_filtered():
    selected = HistoricalArbitrageChainCandidates.box_stock(snapshot())
    assert [c.strike for c in selected] == [15, 16, 17, 18, 19, 21, 22, 23, 24, 25]


def test_synthetic_index_candidates_respect_actual_positions():
    selected = HistoricalArbitrageChainCandidates.synthetic_index(snapshot("INDEX"))
    assert [c.strike for c in selected] == [*range(5, 20), *range(21, 36)]


def test_illiquid_strikes_are_not_used_or_replaced():
    values = list(contracts())
    values[15] = ChainContract(1, "NSE", "TEST", "STOCK", 20261001, 15, "CE",
                               50, 0, 0, 10, 10.1)
    snap = HistoricalChainSnapshot(1, "TEST", 20261001, tuple(values), 20)
    selected = HistoricalArbitrageChainCandidates.synthetic_stock(snap, min_volume=1, min_oi=1)
    assert 15 not in [c.strike for c in selected]
    assert 16 in [c.strike for c in selected]


def test_snapshot_rejects_mixed_timestamp_or_expiry():
    bad = ChainContract(2, "NSE", "TEST", "STOCK", 20261001, 20, "CE", 50, 100, 200, 10, 10.1)
    try:
        HistoricalChainSnapshot(1, "TEST", 20261001, contracts()[:-1] + (bad,), 20)
    except ValueError as exc:
        assert "timestamp" in str(exc)
    else:
        raise AssertionError("mixed snapshot timestamp must be rejected")


def test_selected_strike_pairs_can_be_restricted_to_candidate_universe():
    selected = set(c.strike for c in HistoricalArbitrageChainCandidates.box_stock(snapshot()))
    pairs = HistoricalArbitrageChainCandidates.strike_pairs(
        contracts(), expiry=20261001, option_type="CE", selected_strikes=selected
    )
    assert pairs
    assert all(p.low.strike in selected and p.high.strike in selected for p in pairs)
