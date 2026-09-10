from datetime import datetime

from app.scanner.cash_future_backtest import BacktestConfig, run_multi_contract_backtest
from app.scanner.cash_future_coverage_store import (
    build_persisted_cash_future_coverage,
    iter_persisted_cash_future_points,
    run_persisted_cash_future_backtest,
)
from app.scanner.cash_future_history import CashFutureHistoryPoint
from app.scanner.cash_future_history_store import save_history_points


def make_point(ts: int, contract: str = "2026-09", gap: float = 2.0):
    return CashFutureHistoryPoint(
        timestamp=datetime.fromtimestamp(ts),
        symbol="ABC",
        contract_month=contract,
        cash_price=100.0,
        future_price=100.0 + gap,
        gap=gap,
        gap_pct=gap,
        lot_size=10,
        margin_required=1000.0,
    )


def test_persisted_coverage_streams_and_keeps_contracts_separate(db_session):
    save_history_points(db_session, [make_point(1), make_point(2), make_point(1, "2026-10")])

    points = list(iter_persisted_cash_future_points(db_session, page_size=1))
    assert [(p.contract_month, p.timestamp) for p in points] == [
        ("2026-09", datetime.fromtimestamp(1)),
        ("2026-09", datetime.fromtimestamp(2)),
        ("2026-10", datetime.fromtimestamp(1)),
    ]

    report = build_persisted_cash_future_coverage(db_session)
    assert report.status == "READY"
    assert [(c.contract_month, c.count) for c in report.contracts] == [
        ("2026-09", 2),
        ("2026-10", 1),
    ]


def test_persisted_coverage_detects_authoritative_missing_timestamp(db_session):
    save_history_points(db_session, [make_point(1), make_point(3)])
    expected = (datetime.fromtimestamp(1), datetime.fromtimestamp(2), datetime.fromtimestamp(3))

    report = build_persisted_cash_future_coverage(db_session, expected_timestamps=expected)
    assert report.status == "INCOMPLETE"
    assert report.contracts[0].missing_timestamps == (datetime.fromtimestamp(2),)


def test_persisted_backtest_matches_direct_stream_result(db_session):
    points = [
        make_point(1, "2026-09", 10.0),
        make_point(2, "2026-09", 4.0),
        make_point(3, "2026-10", 9.0),
        make_point(4, "2026-10", 3.0),
    ]
    save_history_points(db_session, points)
    config = BacktestConfig(min_entry_gap=8.0, exit_gap=5.0)

    persisted = run_persisted_cash_future_backtest(db_session, config, symbol="ABC", page_size=1)
    direct = run_multi_contract_backtest(points, config)

    assert persisted["contract_count"] == 2
    assert persisted["trade_count"] == direct["trade_count"] == 2
    assert persisted["net_profit"] == direct["net_profit"] == 120.0
    assert persisted["invested_capital"] == direct["invested_capital"]
    assert [trade["entry_gap"] for trade in persisted["trades"]] == [10.0, 9.0]
