"""Bounded canonical summary reconstruction for durable universal backtest runs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .result_ledger import BacktestResultLedger
from .statistics import BacktestStatistics, EquityPoint, StreamingStatisticsAccumulator


@dataclass(frozen=True)
class CanonicalRunSummary:
    """Scalar completed-run accounting reconstructed without loading result history."""

    run_id: str
    status: str
    provenance: dict[str, Any]
    initial_capital: float
    final_equity: float
    realized_pnl: float
    unrealized_pnl: float
    net_pnl: float
    total_return: float
    sharpe_ratio: float | None
    sortino_ratio: float | None
    max_drawdown: float
    cagr: float | None
    event_count: int
    fill_count: int
    trade_count: int
    equity_count: int

    @classmethod
    def from_completed_run(
        cls,
        ledger: BacktestResultLedger,
        run_id: str,
        *,
        initial_capital: float | None = None,
        equity_page_size: int = 500,
    ) -> "CanonicalRunSummary":
        """Reconstruct canonical scalar results using bounded equity pages.

        initial_capital is explicit because the current durable run schema
        does not persist it in backtest_runs. Performance P&L is derived from
        marked equity, never from SUM(backtest_trades.net_pnl).
        """
        persisted_capital = ledger.run_provenance(run_id).get("initial_capital")
        if initial_capital is None:
            initial_capital = persisted_capital
        elif persisted_capital is not None and float(initial_capital) != float(persisted_capital):
            raise ValueError("initial_capital does not match persisted run provenance")
        if isinstance(initial_capital, bool) or not isinstance(initial_capital, (int, float)) or initial_capital <= 0:
            raise ValueError("initial_capital must be finite and positive")
        if not isinstance(equity_page_size, int) or isinstance(equity_page_size, bool) or equity_page_size <= 0:
            raise ValueError("equity_page_size must be a positive integer")

        run = ledger.run(run_id)
        status = str(run["status"]).upper()
        if status != "COMPLETED":
            raise ValueError("canonical completed-run summary requires COMPLETED status")

        accumulator = StreamingStatisticsAccumulator(float(initial_capital))
        cursor_timestamp = -1
        cursor_id = -1
        equity_count = 0
        latest: Any | None = None

        while True:
            page = ledger.equity(
                run_id,
                limit=equity_page_size,
                after_timestamp_ns=cursor_timestamp,
                after_equity_id=cursor_id,
            )
            if not page:
                break
            for row in page:
                accumulator.update(
                    EquityPoint(
                        int(row["timestamp_ns"]),
                        float(row["equity"]),
                        float(row["realized_pnl"]),
                        float(row["unrealized_pnl"]),
                    )
                )
                latest = row
                equity_count += 1
            cursor_timestamp = int(page[-1]["timestamp_ns"])
            cursor_id = int(page[-1]["equity_id"])
            if len(page) < equity_page_size:
                break

        stats: BacktestStatistics = accumulator.finalize()
        if latest is None:
            final_equity = float(initial_capital)
            realized_pnl = 0.0
            unrealized_pnl = 0.0
        else:
            final_equity = float(latest["equity"])
            realized_pnl = float(latest["realized_pnl"])
            unrealized_pnl = float(latest["unrealized_pnl"])

        return cls(
            run_id=run_id,
            status=status,
            provenance=ledger.run_provenance(run_id),
            initial_capital=float(initial_capital),
            final_equity=final_equity,
            realized_pnl=realized_pnl,
            unrealized_pnl=unrealized_pnl,
            net_pnl=stats.net_pnl,
            total_return=stats.total_return,
            sharpe_ratio=stats.sharpe_ratio,
            sortino_ratio=stats.sortino_ratio,
            max_drawdown=stats.max_drawdown,
            cagr=stats.cagr,
            event_count=ledger.count_events(run_id),
            fill_count=ledger.count_fills(run_id),
            trade_count=ledger.count_trades(run_id),
            equity_count=equity_count,
        )
