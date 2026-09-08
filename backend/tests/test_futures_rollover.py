from datetime import datetime

from app.market_data.futures_rollover import build_futures_chain, map_rollover, select_contract


def _row(symbol: str, token: str, expiry: str, instrumenttype: str = "FUTSTK") -> dict:
    return {
        "symbol": symbol,
        "token": token,
        "exch_seg": "NFO",
        "instrumenttype": instrumenttype,
        "expiry": expiry,
        "lotsize": "250",
    }


def test_build_chain_filters_nfo_futures_and_sorts_by_expiry():
    rows = [
        _row("ABC25MARFUT", "3", "27MAR2025"),
        _row("ABC25JANFUT", "1", "30JAN2025"),
        _row("ABC25FEBFUT", "2", "27FEB2025"),
        _row("ABC-EQ", "99", "27MAR2025", "EQUITY"),
        {"symbol": "ABC25APRFUT", "token": "4", "exch_seg": "NSE", "instrumenttype": "FUTSTK", "expiry": "24APR2025"},
    ]
    chain = build_futures_chain(rows, underlying="ABC")
    assert [c.token for c in chain] == ["1", "2", "3"]
    assert [c.expiry_date.isoformat() for c in chain] == ["2025-01-30", "2025-02-27", "2025-03-27"]
    assert chain[0].instrument.key != chain[1].instrument.key


def test_select_contract_never_selects_expired_contract():
    chain = build_futures_chain(
        [_row("ABCJAN", "1", "30JAN2025"), _row("ABCFEB", "2", "27FEB2025")],
        underlying="ABC",
    )
    assert select_contract(chain, datetime(2025, 1, 15)).token == "1"
    assert select_contract(chain, datetime(2025, 1, 31)).token == "2"
    assert select_contract(chain, datetime(2025, 2, 28)) is None


def test_map_rollover_creates_non_overlapping_contract_windows():
    chain = build_futures_chain(
        [_row("ABCJAN", "1", "30JAN2025"), _row("ABCFEB", "2", "27FEB2025")],
        underlying="ABC",
    )
    windows = map_rollover(chain, datetime(2025, 1, 1), datetime(2025, 3, 1))
    assert [(start.date().isoformat(), end.date().isoformat(), contract.token) for start, end, contract in windows] == [
        ("2025-01-01", "2025-01-30", "1"),
        ("2025-01-30", "2025-02-27", "2"),
    ]
