"""Generic historical entry-to-exit execution for multi-leg arbitrage strategies."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

from .backtest_result import BacktestRunWriter
from .result_ledger import BacktestTrade, EquityPoint


@dataclass(frozen=True)
class OpenPosition:
    """A real historical entry waiting for a later executable exit."""
    trade_id: str
    timestamp_ns: int
    instrument: str
    side: str
    quantity: float
    entry_price: float
    contract: str = ""
    expiry: str = ""
    strike: float | None = None
    leg: str = ""
    data_resolution: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.trade_id.strip():
            raise ValueError("trade_id is required")
        if self.timestamp_ns < 0 or self.quantity <= 0 or self.entry_price < 0:
            raise ValueError("invalid historical entry")


@dataclass(frozen=True)
class ExitExecution:
    """Later executable strategy result; prices are strategy metrics, not fake fills."""
    timestamp_ns: int
    exit_price: float
    gross_pnl: float
    fees: float = 0.0
    slippage: float = 0.0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.timestamp_ns < 0 or self.exit_price < 0 or self.gross_pnl < 0:
            raise ValueError("invalid historical exit")
        if self.fees < 0 or self.slippage < 0:
            raise ValueError("fees/slippage cannot be negative")


class HistoricalArbitrageRunner:
    """Replay historical events and persist completed executions incrementally."""

    def __init__(self, writer: BacktestRunWriter) -> None:
        self.writer = writer
        self._open: dict[str, OpenPosition] = {}
        self._sequence = 0
        self._audit_sequence = 0
        self._realized_pnl = 0.0
        self.completed = 0

    @property
    def open_positions(self) -> tuple[OpenPosition, ...]:
        return tuple(self._open.values())

    @property
    def open_positions_count(self) -> int:
        return len(self._open)

    @property
    def realized_pnl(self) -> float:
        return self._realized_pnl

    @property
    def audit_sequence(self) -> int:
        return self._audit_sequence

    def _audit(self, timestamp_ns: int, event_type: str, payload: Mapping[str, Any]) -> None:
        self._audit_sequence += 1
        self.writer.record_event(self._audit_sequence, timestamp_ns, event_type, payload)

    def replay(
        self,
        events: Iterable[Mapping[str, Any]],
        entry_selector: Callable[[Mapping[str, Any]], Iterable[OpenPosition]],
        exit_selector: Callable[[OpenPosition, Mapping[str, Any]], ExitExecution | None],
        *,
        equity_selector: Callable[[Mapping[str, Any], float], EquityPoint | None] | None = None,
    ) -> int:
        try:
            for event in events:
                self._sequence += 1
                timestamp_ns = int(event["timestamp_ns"])
                for trade_id, position in tuple(self._open.items()):
                    if timestamp_ns <= position.timestamp_ns:
                        continue
                    execution = exit_selector(position, event)
                    if execution is None:
                        continue
                    if execution.timestamp_ns <= position.timestamp_ns:
                        raise ValueError("exit must occur after entry")
                    net_pnl = execution.gross_pnl - execution.fees - execution.slippage
                    metadata = dict(position.metadata)
                    metadata.update(dict(execution.metadata))
                    metadata.update({
                        "entry_timestamp_ns": position.timestamp_ns,
                        "exit_timestamp_ns": execution.timestamp_ns,
                        "pricing_model": "EXECUTABLE_EDGE",
                        "entry_edge": position.entry_price,
                        "exit_edge": execution.exit_price,
                    })
                    self.writer.ledger.append_trades(self.writer.spec.run_id, [BacktestTrade(
                        trade_id=position.trade_id, sequence=self._sequence,
                        timestamp_ns=execution.timestamp_ns, instrument=position.instrument,
                        side=position.side, quantity=position.quantity,
                        entry_price=position.entry_price, exit_price=execution.exit_price,
                        gross_pnl=execution.gross_pnl, fees=execution.fees,
                        slippage=execution.slippage, net_pnl=net_pnl,
                        contract=position.contract, expiry=position.expiry,
                        strike=position.strike, leg=position.leg,
                        data_resolution=position.data_resolution, metadata=metadata,
                    )])
                    self._realized_pnl += net_pnl
                    self.completed += 1
                    del self._open[trade_id]
                    self._audit(execution.timestamp_ns, "POSITION_CLOSED",
                                {"trade_id": trade_id, "net_pnl": net_pnl})

                for position in entry_selector(event):
                    if position.trade_id in self._open:
                        raise ValueError(f"duplicate open trade: {position.trade_id}")
                    self._open[position.trade_id] = position
                    self._audit(position.timestamp_ns, "POSITION_OPENED", {
                        "trade_id": position.trade_id, "instrument": position.instrument,
                        "entry_price": position.entry_price, "contract": position.contract,
                        "expiry": position.expiry, "strike": position.strike, "leg": position.leg,
                    })
                if equity_selector is not None:
                    point = equity_selector(event, self._realized_pnl)
                    if point is not None:
                        self.writer.record_equity(point)

            for position in tuple(self._open.values()):
                self._audit(position.timestamp_ns, "UNRESOLVED_POSITION", {
                    "trade_id": position.trade_id,
                    "reason": "no later executable exit in supplied historical data",
                    "entry_timestamp_ns": position.timestamp_ns,
                })
            self.writer.complete()
            return self.completed
        except Exception as exc:
            self.writer.fail(str(exc))
            raise


__all__ = ["ExitExecution", "HistoricalArbitrageRunner", "OpenPosition"]
