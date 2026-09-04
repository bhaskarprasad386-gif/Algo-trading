"""Chunked full-universe Cash-Future paper backtest orchestration."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Callable, Sequence, TypeAlias

from sqlalchemy.orm import Session

from app.models.cash_future_history import CashFutureHistory
from app.models import BacktestJobResultChunk
from app.scanner.cash_future_paper_backtest import PaperBacktestConfig, run_cash_future_paper_backtest
from app.scanner.synchronized_replay import iter_persisted_symbol_replay

ProgressCallback = Callable[[int, int, str], None]
CancelCallback = Callable[[], bool]
ResultSink = Callable[[int, str, dict], None]
HistoricalContract: TypeAlias = tuple[str, date]


def historical_current_near_contracts(contracts: Sequence[HistoricalContract], as_of: date) -> tuple[HistoricalContract | None, HistoricalContract | None]:
    ordered = sorted(contracts, key=lambda contract: contract[1])
    active = [contract for contract in ordered if contract[1] >= as_of]
    if not active:
        return None, None
    current = active[0]
    near = active[1] if len(active) > 1 else None
    return current, near


def persisted_stock_symbols(db: Session) -> list[str]:
    rows = db.query(CashFutureHistory.symbol).distinct().order_by(CashFutureHistory.symbol).all()
    return [symbol.upper() for (symbol,) in rows]


def _durable_prefix_aggregates(
    db: Session,
    symbols: list[str],
    resume_count: int,
    job_id: str,
) -> tuple[float, float, int, int]:
    """Rebuild summary metrics from this job's durable chunks without replaying them."""
    if resume_count <= 0:
        return 0.0, 0.0, 0, 0
    rows = db.query(BacktestJobResultChunk).filter(
        BacktestJobResultChunk.job_id == job_id,
        BacktestJobResultChunk.sequence < resume_count,
    ).order_by(BacktestJobResultChunk.sequence).all()
    if len(rows) != resume_count:
        raise ValueError("durable full-F&O prefix is incomplete")
    total_net_profit = 0.0
    max_drawdown = 0.0
    completed_symbols = 0
    no_entry_symbols = 0
    for expected_sequence, row in enumerate(rows):
        if row.sequence != expected_sequence or row.symbol.upper() != symbols[expected_sequence].upper():
            raise ValueError("durable full-F&O prefix does not match current symbol universe")
        result = json.loads(row.result_json)
        total_net_profit += float(result.get("net_profit", 0.0) or 0.0)
        max_drawdown = max(max_drawdown, float(result.get("max_drawdown", 0.0) or 0.0))
        if result.get("status") == "no_entry":
            no_entry_symbols += 1
        else:
            completed_symbols += 1
    return total_net_profit, max_drawdown, completed_symbols, no_entry_symbols


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
    resume_after_sequence: int | None = None,
    durable_job_id: str | None = None,
) -> dict:
    """Run the persisted F&O stock universe, optionally resuming after durable chunks."""
    selection = future_selection.upper()
    if selection not in {"CURRENT", "NEAR", "BOTH"}:
        raise ValueError("future_selection must be CURRENT, NEAR or BOTH")
    if resume_after_sequence is not None and resume_after_sequence < -1:
        raise ValueError("resume_after_sequence must be >= -1")
    if resume_after_sequence is not None and resume_after_sequence >= 0 and not durable_job_id:
        raise ValueError("durable_job_id is required when resuming durable full-F&O results")

    symbols = persisted_stock_symbols(db)
    total = len(symbols)
    resume_count = max(0, (resume_after_sequence + 1) if resume_after_sequence is not None else 0)
    if resume_count > total:
        raise ValueError("resume_after_sequence exceeds persisted symbol universe")

    total_net_profit, max_drawdown, completed_symbols, no_entry_symbols = _durable_prefix_aggregates(
        db, symbols, resume_count, durable_job_id or ""
    )
    processed = resume_count
    chunks_written = 0
    results: list[dict] | None = [] if collect_results else None
    end = datetime.utcnow()
    start = end - timedelta(days=days)

    for sequence, symbol in enumerate(symbols):
        if sequence < resume_count:
            continue
        if cancelled is not None and cancelled():
            return {"status": "cancelled", "universe": "FULL_FNO_STOCK", "future_selection": selection,
                    "symbols_total": total, "symbols_processed": processed, "chunks_written": chunks_written,
                    "contracts_processed": completed_symbols, "no_entry_symbols": no_entry_symbols,
                    "total_net_profit": total_net_profit, "max_drawdown": max_drawdown, "results": results}

        bars = iter_persisted_symbol_replay(db, symbol, start, end)
        result = run_cash_future_paper_backtest(
            bars,
            PaperBacktestConfig(starting_capital=2_000_000.0, min_entry_gap=min_entry_gap,
                                exit_gap=exit_gap, charges_per_leg=charges_per_trade,
                                funding_cost_per_day=funding_cost_per_trade, future_selection=selection,
                                max_holding_days=max_holding_days, collect_ledger=collect_results),
            cancelled=cancelled,
        )
        if result.get("status") == "cancelled" or (cancelled is not None and cancelled()):
            return {"status": "cancelled", "universe": "FULL_FNO_STOCK", "future_selection": selection,
                    "symbols_total": total, "symbols_processed": processed, "chunks_written": chunks_written,
                    "contracts_processed": completed_symbols, "no_entry_symbols": no_entry_symbols,
                    "total_net_profit": total_net_profit, "max_drawdown": max_drawdown, "results": results}

        item = {"symbol": symbol, **result}
        if result_sink is not None:
            result_sink(sequence, symbol, item)
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
        processed = sequence + 1
        if progress is not None:
            progress(processed, total, f"Processed {symbol}")

    return {"status": "completed", "universe": "FULL_FNO_STOCK", "future_selection": selection,
            "symbols_total": total, "symbols_processed": processed, "chunks_written": chunks_written,
            "contracts_processed": completed_symbols, "no_entry_symbols": no_entry_symbols,
            "total_net_profit": total_net_profit, "max_drawdown": max_drawdown, "results": results}
