from datetime import datetime, timedelta

import pytest

from app.backtesting.cash_future_backtest_result_ledger import RECORD_TYPE, CashFutureBacktestResultLedger
from app.backtesting.ledger import BacktestLedger
from app.scanner.cash_future_backtest import BacktestConfig, run_multi_contract_backtest
from app.scanner.cash_future_coverage_store import (
    audit_persisted_cash_future_data_quality,
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


def test_persisted_backtest_requires_coverage_report(db_session):
    save_history_points(db_session, [make_point(1, gap=10.0), make_point(2, gap=4.0)])
    config = BacktestConfig(min_entry_gap=8.0, exit_gap=5.0)

    with pytest.raises(ValueError, match="coverage_report is required"):
        run_persisted_cash_future_backtest(db_session, config, symbol="ABC")


def test_persisted_backtest_blocks_incomplete_coverage(db_session):
    save_history_points(db_session, [make_point(1, gap=10.0), make_point(3, gap=4.0)])
    expected = (datetime.fromtimestamp(1), datetime.fromtimestamp(2), datetime.fromtimestamp(3))
    coverage = build_persisted_cash_future_coverage(db_session, expected_timestamps=expected)
    config = BacktestConfig(min_entry_gap=8.0, exit_gap=5.0)

    with pytest.raises(ValueError, match="coverage is incomplete"):
        run_persisted_cash_future_backtest(
            db_session,
            config,
            symbol="ABC",
            coverage_report=coverage,
        )


def test_persisted_backtest_matches_direct_stream_result(db_session):
    points = [
        make_point(1, "2026-09", 10.0),
        make_point(2, "2026-09", 4.0),
        make_point(3, "2026-10", 9.0),
        make_point(4, "2026-10", 3.0),
    ]
    save_history_points(db_session, points)
    config = BacktestConfig(min_entry_gap=8.0, exit_gap=5.0)
    coverage = build_persisted_cash_future_coverage(db_session)
    quality = audit_persisted_cash_future_data_quality(db_session, symbol="ABC", page_size=1)

    persisted = run_persisted_cash_future_backtest(
        db_session,
        config,
        symbol="ABC",
        page_size=1,
        coverage_report=coverage,
        quality_report=quality,
    )
    direct = run_multi_contract_backtest(points, config)

    assert persisted["contract_count"] == 2
    assert persisted["trade_count"] == direct["trade_count"] == 2
    assert persisted["net_profit"] == direct["net_profit"] == 120.0
    assert persisted["invested_capital"] == direct["invested_capital"]
    assert [trade["entry_gap"] for trade in persisted["trades"]] == [10.0, 9.0]


def test_persisted_backtest_can_write_results_to_durable_ledger(db_session):
    points = [
        make_point(1, "2026-09", 10.0),
        make_point(2, "2026-09", 4.0),
    ]
    save_history_points(db_session, points)
    config = BacktestConfig(min_entry_gap=8.0, exit_gap=5.0)
    coverage = build_persisted_cash_future_coverage(db_session)
    quality = audit_persisted_cash_future_data_quality(db_session, symbol="ABC", page_size=1)

    ledger = BacktestLedger()
    ledger.start_run("cf-persisted", "cash-future", "1", 100_000)
    writer = CashFutureBacktestResultLedger(ledger, "cf-persisted")

    result = run_persisted_cash_future_backtest(
        db_session,
        config,
        symbol="ABC",
        page_size=1,
        coverage_report=coverage,
        quality_report=quality,
        result_ledger=writer,
    )

    records = ledger.records("cf-persisted", RECORD_TYPE)
    assert result["trade_count"] == 1
    assert len(records) == 1
    assert records[0].payload["net_profit"] == 60.0
    ledger.close()


def test_persisted_durable_backtest_matches_direct_with_overlapping_symbol_trades(db_session):
    base = datetime(2026, 9, 2, 10, 0)
    points = [
        CashFutureHistoryPoint(
            timestamp=base,
            symbol="ABC",
            contract_month="2026-09",
            cash_price=100.0,
            future_price=110.0,
            gap=10.0,
            gap_pct=10.0,
            lot_size=10,
            margin_required=1000.0,
        ),
        CashFutureHistoryPoint(
            timestamp=base + __import__("datetime").timedelta(hours=1),
            symbol="ABC",
            contract_month="2026-09",
            cash_price=100.0,
            future_price=104.0,
            gap=4.0,
            gap_pct=4.0,
            lot_size=10,
            margin_required=1000.0,
        ),
        CashFutureHistoryPoint(
            timestamp=base + __import__("datetime").timedelta(minutes=30),
            symbol="XYZ",
            contract_month="2026-09",
            cash_price=100.0,
            future_price=109.0,
            gap=9.0,
            gap_pct=9.0,
            lot_size=10,
            margin_required=1000.0,
        ),
        CashFutureHistoryPoint(
            timestamp=base + __import__("datetime").timedelta(hours=2),
            symbol="XYZ",
            contract_month="2026-09",
            cash_price=100.0,
            future_price=103.0,
            gap=3.0,
            gap_pct=3.0,
            lot_size=10,
            margin_required=1000.0,
        ),
    ]
    save_history_points(db_session, points)
    config = BacktestConfig(min_entry_gap=8.0, exit_gap=5.0)
    coverage = build_persisted_cash_future_coverage(db_session)
    quality = audit_persisted_cash_future_data_quality(db_session, page_size=1)

    ledger = BacktestLedger()
    ledger.start_run("cf-equivalence", "cash-future", "1", 100_000)
    writer = CashFutureBacktestResultLedger(ledger, "cf-equivalence")

    durable = run_persisted_cash_future_backtest(
        db_session,
        config,
        page_size=1,
        coverage_report=coverage,
        quality_report=quality,
        result_ledger=writer,
    )
    direct = run_multi_contract_backtest(points, config)

    assert durable["trade_count"] == direct["trade_count"] == 2
    assert durable["wins"] == direct["wins"] == 2
    assert durable["losses"] == direct["losses"] == 0
    assert durable["net_profit"] == direct["net_profit"] == 160.0
    assert durable["invested_capital"] == direct["invested_capital"]
    assert durable["max_drawdown"] == direct["max_drawdown"]
    assert durable["trades"] == direct["trades"]
    assert durable["equity_curve"] == direct["equity_curve"]
    ledger.close()


def test_persisted_durable_aggregation_matches_legacy_tie_break_for_same_entry_time(db_session):
    base = datetime(2026, 9, 2, 10, 0)

    def p(symbol, gap, exit_offset_hours):
        return [
            CashFutureHistoryPoint(
                timestamp=base,
                symbol=symbol,
                contract_month="2026-09",
                cash_price=100.0,
                future_price=100.0 + gap,
                gap=gap,
                gap_pct=gap,
                lot_size=10,
                margin_required=1000.0,
            ),
            CashFutureHistoryPoint(
                timestamp=base + timedelta(hours=exit_offset_hours),
                symbol=symbol,
                contract_month="2026-09",
                cash_price=100.0,
                future_price=104.0,
                gap=4.0,
                gap_pct=4.0,
                lot_size=10,
                margin_required=1000.0,
            ),
        ]

    points = p("XYZ", 12.0, 2) + p("ABC", 10.0, 1)
    save_history_points(db_session, points)
    config = BacktestConfig(min_entry_gap=8.0, exit_gap=5.0)
    coverage = build_persisted_cash_future_coverage(db_session)
    quality = audit_persisted_cash_future_data_quality(db_session, page_size=1)

    ledger = BacktestLedger()
    ledger.start_run("cf-same-entry-time", "cash-future", "1", 100_000)
    writer = CashFutureBacktestResultLedger(ledger, "cf-same-entry-time")

    durable = run_persisted_cash_future_backtest(
        db_session,
        config,
        page_size=1,
        coverage_report=coverage,
        quality_report=quality,
        result_ledger=writer,
    )
    direct = run_multi_contract_backtest(points, config)

    assert [trade["symbol"] for trade in direct["trades"]] == ["ABC", "XYZ"]
    assert durable["trades"] == direct["trades"]
    assert durable["equity_curve"] == direct["equity_curve"]
    assert durable["max_drawdown"] == direct["max_drawdown"]
    ledger.close()


def test_persisted_durable_aggregation_preserves_entry_time_drawdown_order(db_session):
    base = datetime(2026, 9, 2, 10, 0)

    def p(offset_hours, symbol, gap, expiry=None):
        return CashFutureHistoryPoint(
            timestamp=base + timedelta(hours=offset_hours),
            symbol=symbol,
            contract_month="2026-09",
            cash_price=100.0,
            future_price=100.0 + gap,
            gap=gap,
            gap_pct=gap,
            lot_size=10,
            margin_required=1000.0,
            expiry_date=expiry or datetime(2026, 9, 30).date(),
        )

    points = [
        p(0, "ABC", 15.0),
        p(3, "ABC", 10.0),
        p(1, "XYZ", 10.0),
        p(2, "XYZ", 11.0),
        p(2, "PQR", 10.0),
        p(4, "PQR", 11.0),
    ]
    save_history_points(db_session, points)
    config = BacktestConfig(min_entry_gap=8.0, exit_gap=5.0)
    coverage = build_persisted_cash_future_coverage(db_session)
    quality = audit_persisted_cash_future_data_quality(db_session, page_size=1)

    ledger = BacktestLedger()
    ledger.start_run("cf-order", "cash-future", "1", 100_000)
    writer = CashFutureBacktestResultLedger(ledger, "cf-order")

    durable = run_persisted_cash_future_backtest(
        db_session,
        config,
        page_size=1,
        coverage_report=coverage,
        quality_report=quality,
        result_ledger=writer,
    )
    direct = run_multi_contract_backtest(points, config)

    assert durable["trades"] == direct["trades"]
    assert durable["equity_curve"] == direct["equity_curve"]
    assert durable["max_drawdown"] == direct["max_drawdown"] == 20.0
    assert durable["net_profit"] == direct["net_profit"] == 30.0
    ledger.close()
