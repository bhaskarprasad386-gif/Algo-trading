"""Persisted Cash-Future history streaming helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.cash_future_history import CashFutureHistory
from app.scanner.cash_future_backtest import BacktestConfig, run_multi_contract_backtest_streaming
from app.scanner.cash_future_coverage import CashFutureCoverageReport, build_cash_future_coverage_report
from app.scanner.cash_future_history import CashFutureHistoryPoint
from app.backtesting.cash_future_backtest_result_ledger import CashFutureBacktestResultLedger
from app.backtesting.cash_future_data_quality import (
    CashFutureDataQualityReport,
    audit_cash_future_points,
)


def iter_persisted_cash_future_points(
    db: Session,
    *,
    symbol: str | None = None,
    contract_month: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    page_size: int = 1000,
) -> Iterator[CashFutureHistoryPoint]:
    """Stream persisted observations without loading the whole dataset."""
    if page_size <= 0:
        raise ValueError("page_size must be positive")

    stmt = select(CashFutureHistory)
    if symbol is not None:
        stmt = stmt.where(CashFutureHistory.symbol == symbol.strip().upper())
    if contract_month is not None:
        stmt = stmt.where(CashFutureHistory.contract_month == contract_month.strip())
    if start is not None:
        stmt = stmt.where(CashFutureHistory.timestamp >= start)
    if end is not None:
        stmt = stmt.where(CashFutureHistory.timestamp <= end)

    result = db.scalars(stmt.order_by(
        CashFutureHistory.symbol,
        CashFutureHistory.contract_month,
        CashFutureHistory.timestamp,
    ).yield_per(page_size))
    for row in result:
        yield CashFutureHistoryPoint(
            timestamp=row.timestamp,
            symbol=row.symbol,
            contract_month=row.contract_month,
            cash_price=row.cash_price,
            future_price=row.future_price,
            gap=row.gap,
            gap_pct=row.gap_pct,
            lot_size=row.lot_size,
            margin_required=row.margin_required,
            volume=row.volume,
            oi=row.oi,
            cash_bid=row.cash_bid,
            cash_ask=row.cash_ask,
            future_bid=row.future_bid,
            future_ask=row.future_ask,
            cash_bid_qty=row.cash_bid_qty,
            cash_ask_qty=row.cash_ask_qty,
            future_bid_qty=row.future_bid_qty,
            future_ask_qty=row.future_ask_qty,
            charges=row.charges,
            funding_cost=row.funding_cost,
            net_profit=row.net_profit,
            roi_pct=row.roi_pct,
            expiry_date=row.expiry_date,
        )


def build_persisted_cash_future_coverage(
    db: Session,
    *,
    symbol: str | None = None,
    contract_month: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    expected_timestamps: tuple[datetime, ...] | None = None,
    page_size: int = 1000,
) -> CashFutureCoverageReport:
    """Validate persisted Cash-Future history against authoritative expectations."""
    return build_cash_future_coverage_report(
        iter_persisted_cash_future_points(
            db,
            symbol=symbol,
            contract_month=contract_month,
            start=start,
            end=end,
            page_size=page_size,
        ),
        expected_timestamps=expected_timestamps,
    )


def audit_persisted_cash_future_data_quality(
    db: Session,
    *,
    symbol: str | None = None,
    contract_month: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    page_size: int = 1000,
) -> CashFutureDataQualityReport:
    """Audit the exact persisted backtest scope using a streaming query."""
    return audit_cash_future_points(
        iter_persisted_cash_future_points(
            db,
            symbol=symbol,
            contract_month=contract_month,
            start=start,
            end=end,
            page_size=page_size,
        )
    )


def run_persisted_cash_future_backtest(
    db: Session,
    config: BacktestConfig,
    *,
    symbol: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    page_size: int = 1000,
    coverage_report: CashFutureCoverageReport | None = None,
    quality_report: CashFutureDataQualityReport | None = None,
    result_ledger: CashFutureBacktestResultLedger | None = None,
) -> dict:
    """Run Cash-Future backtest only after coverage and quality readiness gates.

    The caller must supply reports built from the same persisted history scope.
    Observations remain streamed from SQLite, including genuine historical
    bid/ask/depth quantities when the source provided them.
    """
    if coverage_report is None:
        raise ValueError("coverage_report is required before running a persisted Cash-Future backtest")
    if not coverage_report.complete:
        raise ValueError("Cash-Future historical coverage is incomplete; backtest blocked")
    if quality_report is None:
        raise ValueError("quality_report is required before running a persisted Cash-Future backtest")
    quality_report.require_clean()

    result = run_multi_contract_backtest_streaming(
        iter_persisted_cash_future_points(
            db,
            symbol=symbol,
            contract_month=config.contract_month,
            start=start,
            end=end,
            page_size=page_size,
        ),
        config,
    )
    if result_ledger is not None:
        result_ledger.append_many(result["trades"])
    return result


__all__ = [
    "iter_persisted_cash_future_points",
    "build_persisted_cash_future_coverage",
    "audit_persisted_cash_future_data_quality",
    "run_persisted_cash_future_backtest",
]
