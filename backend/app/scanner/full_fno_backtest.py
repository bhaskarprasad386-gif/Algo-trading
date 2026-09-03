"""Chunked full-universe Cash-Future backtest orchestration."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable

from sqlalchemy import distinct
from sqlalchemy.orm import Session

from app.models.cash_future_history import CashFutureHistory
from app.scanner.cash_future_backtest import BacktestConfig, run_backtest
from app.scanner.cash_future_history_store import read_history


ProgressCallback = Callable[[int, int, str], None]
CancelCallback = Callable[[], bool]


def persisted_stock_contracts(db: Session) -> list[tuple[str, str]]:
    """Discover every stock/contract pair that actually has persisted history.

    This deliberately reports data-backed coverage. Missing historical data is
    never replaced with synthetic rows or a hard-coded stock list.
    """
    rows = db.query(
        CashFutureHistory.symbol,
        CashFutureHistory.contract_month,
    ).distinct().order_by(
        CashFutureHistory.symbol,
        CashFutureHistory.contract_month,
    ).all()
    return [(symbol.upper(), contract.upper()) for symbol, contract in rows]


def run_full_fno_backtest(
    db: Session,
    *,
    days: int,
    min_entry_gap: float,
    exit_gap: float,
    charges_per_trade: float,
    funding_cost_per_trade: float,
    max_holding_days: int,
    progress: ProgressCallback | None = None,
    cancelled: CancelCallback | None = None,
) -> dict:
    """Run the persisted F&O stock universe in bounded symbol/contract chunks."""
    pairs = persisted_stock_contracts(db)
    total = len(pairs)
    processed = 0
    results: list[dict] = []
    end = datetime.utcnow()
    start = end - timedelta(days=days)

    for symbol, contract_month in pairs:
        if cancelled is not None and cancelled():
            return {
                "status": "cancelled",
                "universe": "FULL_FNO_STOCK",
                "symbols_total": total,
                "symbols_processed": processed,
                "results": results,
            }

        points = read_history(db, symbol, contract_month, start, end)
        if points:
            result = run_backtest(
                points,
                BacktestConfig(
                    min_entry_gap=min_entry_gap,
                    exit_gap=exit_gap,
                    charges_per_trade=charges_per_trade,
                    funding_cost_per_trade=funding_cost_per_trade,
                    max_holding_days=max_holding_days,
                    contract_month=contract_month,
                ),
            )
            results.append({
                "symbol": symbol,
                "contract_month": contract_month,
                "observation_count": len(points),
                **result,
            })

        processed += 1
        if progress is not None:
            pct = 100.0 if total == 0 else processed / total * 100.0
            progress(processed, total, f"Processed {symbol} {contract_month}")

    return {
        "status": "completed",
        "universe": "FULL_FNO_STOCK",
        "symbols_total": total,
        "symbols_processed": processed,
        "contracts_processed": len(results),
        "results": results,
    }
