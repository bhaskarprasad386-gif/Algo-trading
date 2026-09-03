"""Chunked full-universe Cash-Future paper backtest orchestration."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Callable, Sequence, TypeAlias

from sqlalchemy.orm import Session

from app.models.cash_future_history import CashFutureHistory
from app.scanner.cash_future_paper_backtest import PaperBacktestConfig, run_cash_future_paper_backtest
from app.scanner.synchronized_replay import iter_persisted_symbol_replay

ProgressCallback = Callable[[int, int, str], None]
CancelCallback = Callable[[], bool]
ResultSink = Callable[[int, str, dict], None]
HistoricalContract: TypeAlias = tuple[str, date]


def historical_current_near_contracts(
    contracts: Sequence[HistoricalContract],
    as_of: date,
) -> tuple[HistoricalContract | None, HistoricalContract | None]:
    """Select the historical current and next-near contracts by expiry.

    The current contract is the earliest contract whose expiry is on or after
    ``as_of``. The near contract is the next expiry after the current contract.
    Contracts that have already expired are ignored, so a date after the final
    expiry correctly returns ``(None, None)``.
    """
    ordered = sorted(contracts, key=lambda contract: contract[1])
    active = [contract for contract in ordered if contract[1] >= as_of]
    if not active:
        return None, None
    current = active[0]
    near = active[1] if len(active) > 1 else None
    return current, near


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
    result_sink: ResultSink | None = None,
    collect_results: bool = True,
) -> dict:
    """Run the persisted F&O stock universe through the synchronized paper engine.

    The replay itself is streaming. For large jobs, ``collect_results=False`` and
    ``result_sink`` make each symbol result durable immediately instead of building
    a full-universe result list in RAM. The canonical 1-minute market data stays in
    the persistent historical store; the worker only holds the current symbol's
    replay state.
    """
    selection = future_selection.upper()
    if selection not in {"CURRENT", "NEAR", "BOTH"}:
        raise ValueError("future_selection must be CURRENT, NEAR or BOTH")

    symbols = persisted_stock_symbols(db)
    total = len(symbols)
    processed = 0
    chunks_written = 0
    results: list[dict] | None = [] if collect_results else None
    total_net_profit = 0.0
    max_drawdown = 0.0
    completed_symbols = 0
    no_entry_symbols = 0

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
                "chunks_written": chunks_written,
                "total_net_profit": total_net_profit,
                "max_drawdown": max_drawdown,
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
                collect_ledger=collect_results,
            ),
            cancelled=cancelled,
        )
        item = {"symbol": symbol, **result}

        if result_sink is not None:
            result_sink(processed, symbol, item)
            chunks_written += 1
        if collect_results:
            assert results is not None
            results.append(item)

        total_net_profit += float(result.get("net_profit", 0.0) or 0.0)
        max_drawdown = max(max_drawdown, float(result.get("max_drawdown", 0.0) or 0.0))
        if result.get("status") == "no_entry":
            no_entry_symbols += 1
        else:
            completed_symbols += 1

        processed += 1
        if progress is not None:
            progress(processed, total, f"Processed {symbol}")

    return {
        "status": "completed",
        "universe": "FULL_FNO_STOCK",
        "future_selection": selection,
        "symbols_total": total,
        "symbols_processed": processed,
        "chunks_written": chunks_written,
        "contracts_processed": completed_symbols,
        "no_entry_symbols": no_entry_symbols,
        "total_net_profit": total_net_profit,
        "max_drawdown": max_drawdown,
        "results": results,
    }
