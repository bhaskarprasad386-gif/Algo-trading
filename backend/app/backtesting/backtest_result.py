"""Shared run orchestration connecting BacktestRun, payoff analytics and ledger."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from app.execution.payoff import PayoffLeg, payoff_summary

from .backtest_run import BacktestRunSpec
from .result_ledger import BacktestEvent, BacktestResultLedger, EquityPoint


@dataclass(frozen=True)
class PayoffSnapshot:
    """Graph-ready payoff data persisted as an auditable run event."""

    run_id: str
    prices: tuple[float, ...]
    pnl: tuple[float, ...]
    max_profit: float | None
    max_loss: float | None
    break_even_points: tuple[float, ...]


class BacktestRunWriter:
    """Single entry point for independent, incremental backtest result writes.

    The writer creates exactly one ledger partition per ``BacktestRunSpec`` and
    stores payoff/equity/events incrementally. Strategy-specific runners can
    reuse it without duplicating persistence or payoff mathematics.
    """

    def __init__(self, ledger: BacktestResultLedger, spec: BacktestRunSpec, *, created_at_ns: int = 0) -> None:
        self.ledger = ledger
        self.spec = spec
        self.ledger.create_run(spec.run_id, spec.provenance, created_at_ns=created_at_ns)

    def record_event(self, sequence: int, timestamp_ns: int, event_type: str, payload: Mapping[str, Any]) -> int:
        return self.ledger.append_events(
            self.spec.run_id,
            [BacktestEvent(sequence, timestamp_ns, event_type, payload)],
        )

    def record_equity(self, point: EquityPoint) -> int:
        return self.ledger.append_equity(self.spec.run_id, [point])

    def record_payoff(
        self,
        sequence: int,
        timestamp_ns: int,
        legs: tuple[PayoffLeg, ...],
        prices: tuple[float, ...],
    ) -> PayoffSnapshot:
        summary = payoff_summary(legs, prices)
        snapshot = PayoffSnapshot(
            run_id=self.spec.run_id,
            prices=tuple(summary["prices"]),
            pnl=tuple(summary["pnl"]),
            max_profit=summary["max_profit"],
            max_loss=summary["max_loss"],
            break_even_points=tuple(summary["break_even_points"]),
        )
        self.record_event(
            sequence,
            timestamp_ns,
            "PAYOFF_SNAPSHOT",
            {
                "prices": list(snapshot.prices),
                "pnl": list(snapshot.pnl),
                "max_profit": snapshot.max_profit,
                "max_loss": snapshot.max_loss,
                "break_even_points": list(snapshot.break_even_points),
            },
        )
        return snapshot

    def complete(self) -> None:
        self.ledger.set_status(self.spec.run_id, "COMPLETED")

    def fail(self, reason: str) -> None:
        if not reason.strip():
            raise ValueError("failure reason is required")
        self.ledger.set_status(self.spec.run_id, "FAILED")
        self.record_event(0, self.spec.start_ns, "RUN_FAILED", {"reason": reason})
