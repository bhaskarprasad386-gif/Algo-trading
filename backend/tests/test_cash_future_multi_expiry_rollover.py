from datetime import date

from app.backtesting.cash_future_rollover_plan import build_rollover_segments
from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord


def test_multi_expiry_rollover_chain_uses_exact_contract_tokens_and_non_overlapping_segments(tmp_path):
    catalog = ContractMasterCatalog(tmp_path / "contracts.db")
    catalog.upsert_snapshot(date(2026, 1, 1), [
        ContractRecord("NFO", "SBIN26JANFUT", "101", date(2026, 1, 29), "STOCK_FUTURE", "SBIN", 750),
        ContractRecord("NFO", "SBIN26FEBFUT", "102", date(2026, 2, 26), "STOCK_FUTURE", "SBIN", 750),
        ContractRecord("NFO", "SBIN26MARFUT", "103", date(2026, 3, 26), "STOCK_FUTURE", "SBIN", 750),
    ])

    segments = build_rollover_segments(
        catalog,
        exchange="NFO",
        underlying="SBIN",
        start=date(2026, 1, 29),
        end=date(2026, 3, 26),
        mode="CURRENT",
    )

    assert tuple(segment.future.token for segment in segments) == ("101", "102", "103")
    assert [(segment.start, segment.end) for segment in segments] == [
        (date(2026, 1, 29), date(2026, 1, 29)),
        (date(2026, 1, 30), date(2026, 2, 26)),
        (date(2026, 2, 27), date(2026, 3, 26)),
    ]
    assert tuple(segment.future.expiry for segment in segments) == (
        date(2026, 1, 29),
        date(2026, 2, 26),
        date(2026, 3, 26),
    )
    for previous, current in zip(segments, segments[1:]):
        assert previous.end < current.start

    catalog.close()
