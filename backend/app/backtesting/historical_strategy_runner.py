"""Incremental historical strategy runner with auditable result persistence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from .backtest_result import BacktestRunWriter
from .result_ledger import BacktestTrade, EquityPoint


@dataclass(frozen=True)
class Execution:
    trade_id: str
    timestamp_ns: int
    instrument: str
    side: str
    quantity: int
    entry_price: float
    exit_price: float
    gross_pnl: float
    fees: float = 0.0
    slippage: float = 0.0
    contract: str | None = None
    expiry: int | None = None
    strike: float | None = None
    leg: str | None = None
    data_resolution: str | None = None
    metadata: Mapping[str, Any] | None = None


class HistoricalStrategyRunner:
    """Replay arbitrary strategy decisions and persist results immediately."""

    def __init__(self, writer: BacktestRunWriter) -> None:
        self.writer = writer
        self._sequence = 0

    def replay(
        self,
        events: Iterable[Mapping[str, Any]],
        strategy: Callable[[Mapping[str, Any]], Execution | None],
    ) -> int:
        completed = 0
        try:
            for event in events:
                self._sequence += 1
                execution = strategy(event)
                if execution is not None:
                    self.writer.ledger.append_trades(
                        self.writer.spec.run_id,
                        [BacktestTrade(
                            trade_id=execution.trade_id,
                            sequence=self._sequence,
                            timestamp_ns=execution.timestamp_ns,
                            instrument=execution.instrument,
                            side=execution.side,
                            quantity=execution.quantity,
                            entry_price=execution.entry_price,
                            exit_price=execution.exit_price,
                            gross_pnl=execution.gross_pnl,
                            fees=execution.fees,
                            slippage=execution.slippage,
                            net_pnl=execution.gross_pnl - execution.fees - execution.slippage,
                            contract=execution.contract or "",
                            expiry=str(execution.expiry) if execution.expiry is not None else "",
                            strike=execution.strike,
                            leg=execution.leg or "",
                            data_resolution=execution.data_resolution or "",
                            metadata=dict(execution.metadata or {}),
                        )],
                    )
                    completed += 1
                if "equity" in event:
                    self.writer.record_equity(EquityPoint(
                        int(event["timestamp_ns"]), float(event["equity"]),
                        float(event.get("realized_pnl", 0.0)),
                        float(event.get("unrealized_pnl", 0.0)),
                        float(event.get("drawdown", 0.0)),
                    ))
            self.writer.complete()
            return completed
        except Exception as exc:
            self.writer.fail(str(exc))
            raise


__all__ = ["Execution", "HistoricalStrategyRunner"]
