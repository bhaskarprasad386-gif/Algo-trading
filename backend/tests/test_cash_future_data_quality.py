from datetime import datetime

import pytest

from app.backtesting.cash_future_data_quality import (
    audit_cash_future_points,
    audit_cash_future_records,
)
from app.backtesting.historical_catalog import HistoricalRecord
from app.scanner.cash_future_history import CashFutureHistoryPoint


def make_point(ts: int, *, cash_bid: float | None = None, cash_ask: float | None = None, cash_bid_qty: float | None = None):
    return CashFutureHistoryPoint(
        timestamp=datetime.fromtimestamp(ts),
        symbol="ABC",
        contract_month="2026-09",
        cash_price=100.0,
        future_price=105.0,
        gap=5.0,
        gap_pct=5.0,
        lot_size=10,
        margin_required=1000.0,
        cash_bid=cash_bid,
        cash_ask=cash_ask,
        cash_bid_qty=cash_bid_qty,
        cash_ask_qty=10.0,
        future_bid=104.0,
        future_ask=106.0,
        future_bid_qty=10.0,
        future_ask_qty=10.0,
    )


def test_clean_points_pass_without_materializing_history():
    def stream():
        for ts in range(3):
            yield make_point(ts, cash_bid=99.0, cash_ask=101.0, cash_bid_qty=10.0)

    report = audit_cash_future_points(stream())

    assert report.records_checked == 3
    assert report.clean is True
    assert report.issue_count == 0


def test_points_detect_crossed_quotes_and_negative_depth():
    report = audit_cash_future_points(
        [make_point(1, cash_bid=102.0, cash_ask=101.0, cash_bid_qty=-1.0)]
    )

    assert report.crossed_quotes == 1
    assert report.negative_depth == 1
    with pytest.raises(ValueError, match="data-quality gate failed"):
        report.require_clean()


def test_points_detect_duplicate_identity_using_constant_memory():
    report = audit_cash_future_points(
        [
            make_point(1, cash_bid=99.0, cash_ask=101.0, cash_bid_qty=10.0),
            make_point(1, cash_bid=99.0, cash_ask=101.0, cash_bid_qty=10.0),
            make_point(2, cash_bid=99.0, cash_ask=101.0, cash_bid_qty=10.0),
        ]
    )

    assert report.records_checked == 3
    assert report.duplicate_timestamps == 1


def test_historical_record_audit_still_detects_non_adjacent_duplicate():
    records = [
        HistoricalRecord("angelone", "NSE:11:ABC-EQ", "1m", 1, {"cash_price": 100.0, "future_price": 105.0}),
        HistoricalRecord("angelone", "NSE:11:ABC-EQ", "1m", 2, {"cash_price": 101.0, "future_price": 106.0}),
        HistoricalRecord("angelone", "NSE:11:ABC-EQ", "1m", 1, {"cash_price": 100.0, "future_price": 105.0}),
    ]

    report = audit_cash_future_records(records)

    assert report.duplicate_timestamps == 1
