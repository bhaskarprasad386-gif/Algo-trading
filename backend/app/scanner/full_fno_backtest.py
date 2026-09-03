"""Chunked full-universe Cash-Future paper backtest orchestration."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable

from sqlalchemy.orm import Session

from app.models.cash_future_history import CashFutureHistory
from app.scanner.cash_future_paper_backtest import PaperBacktestConfig, run_cash_future_paper_backtest
from app.scanner.synchronized_replay import iter_persisted_symbol_replay

ProgressCallback = Callable[[int, int, str], None]
CancelCallback = Callable[[], bool]


def persisted_stock_symbols(db: Session) -> list[str]:
    """Discover every stock symbol with persisted historical Cash-Future data."""
    rows = db.query(CashFutureHistory.symbol).distinct().order_by(CashFutureHistory.symbol).all()
    return [symbol.upper() for (symbol,) in rows]


def run_full_fno_backtest(
    db: Session,
    *,
    days: int,
    min_entry_gap: float,
    exit_gap: float,
    charges_per_trade: float,
    funding_cost_per_trade: float,
    max_holding_days: int,
    future_selection: str = "BOTH",
    progress: ProgressCallback | None = None,
    cancelled: CancelCallback | None = None,
) -> dict:
    """Run the persisted F&O stock universe through the synchronized paper engine.

    The worker processes one symbol at a time and streams synchronized historical
    bars, so the API/UI never has to load the full universe or full-year minute
    dataset into memory.
    """
    selection = future_selection.upper()
    if selection not in {"CURRENT", "NEAR", "BOTH"}:
        raise ValueError("future_selection must be CURRENT, NEAR or BOTH")

    symbols = persisted_stock_symbols(db)
    total = len(symbols)
    processed = 0
    results: list[dict] = []
    end = datetime.utcnow()
    start = end - timedelta(days=days)

    for symbol in symbols:
        if cancelled is not None and cancelled():
            return {
                "status": "cancelled",
                "universe": "FULL_FNO_STOCK",
                "future_selection": selection,
                "symbols_total": total,
                "symbols_processed": processed,
                "results": results,
            }

        bars = iter_persisted_symbol_replay(db, symbol, start, end)
        result = run_cash_future_paper_backtest(
            bars,
            PaperBacktestConfig(
                starting_capital=2_000_000.0,
                min_entry_gap=min_entry_gap,
                exit_gap=exit_gap,
                charges_per_leg=charges_per_trade,
                funding_cost_per_day=funding_cost_per_trade,
                future_selection=selection,
                max_holding_days=max_holding_days,
            ),
            cancelled=cancelled,
        )
        results.append({"symbol": symbol, **result})

        processed += 1
        if progress is not None:
            pct = 100.0 if total == 0 else processed / total * 100.0
            progress(processed, total, f"Processed {symbol}")

    return {
        "status": "completed",
        "universe": "FULL_FNO_STOCK",
        "future_selection": selection,
        "symbols_total": total,
        "symbols_processed": processed,
        "contracts_processed": sum(1 for item in results if item.get("status") != "no_entry"),
        "results": results,
    }
