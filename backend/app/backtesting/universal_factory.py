"""Production composition helpers for one isolated Universal backtest run."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .backtest_result import BacktestRunWriter
from .backtest_run import BacktestRunSpec
from .contracts import DataSourceProtocol, RunContext, StrategyProtocol
from .universal_engine import UniversalEventBacktestEngine


@dataclass
class UniversalRunResources:
    """Owned resources for one run; the caller closes the ledger when finished."""

    ledger: Any
    writer: BacktestRunWriter
    engine: UniversalEventBacktestEngine
    context: RunContext

    def close(self) -> None:
        self.ledger.close()


def create_universal_ledger(path: str | Path | None = None):
    """Create an isolated Universal result ledger from an explicit DB path."""
    if path is None:
        from app.core.config import settings

        path = settings.BACKTEST_RESULT_LEDGER_DB
    db_path = Path(path)
    if str(db_path) != ":memory:":
        db_path.parent.mkdir(parents=True, exist_ok=True)

    from .result_ledger import BacktestResultLedger

    return BacktestResultLedger(str(db_path))


def create_universal_run(
    spec: BacktestRunSpec,
    *,
    data_source: DataSourceProtocol,
    strategy: StrategyProtocol,
    ledger_path: str | Path | None = None,
    resume: bool = False,
    quantity: int = 1,
    risk_config=None,
    execution_config=None,
    retain_history: bool = False,
    checkpoint_every_events: int | None = None,
) -> UniversalRunResources:
    """Compose ledger, writer, engine and immutable RunContext for one run."""
    if spec.initial_capital is None:
        raise ValueError("Universal durable runs require spec.initial_capital")
    ledger = create_universal_ledger(ledger_path)
    try:
        writer = BacktestRunWriter(ledger, spec, resume=resume)
        engine = UniversalEventBacktestEngine(
            float(spec.initial_capital),
            risk_config=risk_config,
            execution_config=execution_config,
            quantity=quantity,
            result_writer=writer,
            retain_history=retain_history,
            checkpoint_every_events=checkpoint_every_events,
            resume=resume,
        )
        context = RunContext(
            spec=spec,
            clock=engine.clock,
            data_source=data_source,
            strategy=strategy,
            execution=engine.execution,
            portfolio=engine.portfolio,
            result_writer=writer,
        )
        return UniversalRunResources(ledger, writer, engine, context)
    except Exception:
        ledger.close()
        raise


class UniversalBacktestWorker:
    """Execute one Universal run with explicit new-run or recovery ownership."""

    def run(
        self,
        spec: BacktestRunSpec,
        *,
        data_source: DataSourceProtocol,
        strategy: StrategyProtocol,
        ledger_path: str | Path | None = None,
        resume: bool = False,
        quantity: int = 1,
        risk_config=None,
        execution_config=None,
        retain_history: bool = False,
        checkpoint_every_events: int | None = None,
        multi_leg: bool = False,
    ):
        resources = create_universal_run(
            spec,
            data_source=data_source,
            strategy=strategy,
            ledger_path=ledger_path,
            resume=resume,
            quantity=quantity,
            risk_config=risk_config,
            execution_config=execution_config,
            retain_history=retain_history,
            checkpoint_every_events=checkpoint_every_events,
        )
        try:
            claimed = (
                resources.ledger.claim_recoverable(spec.run_id)
                if resume
                else resources.ledger.claim_run(spec.run_id)
            )
            if not claimed:
                expected = "recoverable" if resume else "created"
                raise ValueError(f"run is not {expected}: {spec.run_id}")
            if multi_leg:
                return resources.engine.run_multi_leg_context(resources.context)
            return resources.engine.run_context(resources.context)
        finally:
            resources.close()
