"""API surface for historical Cash-Future strategy runs."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from app.backtesting.cash_future_historical_loader import CashFutureHistoricalLoader, CashFutureHistorySelection
from app.backtesting.cash_future_replay_routes import router as cash_future_replay_router
from app.backtesting.cash_future_strategy_runner import CashFutureStrategyConfig, run_cash_future_strategy
from app.backtesting.contract_master import ContractMasterCatalog
from app.backtesting.historical_catalog import HistoricalCatalog
from app.backtesting.ledger import BacktestLedger
from app.core.config import settings
from app.scanner.cash_future_history import CashFutureHistoryPoint

router = APIRouter(prefix="/api/v1/backtesting/cash-future", tags=["Cash-Future Backtesting"])
router.include_router(cash_future_replay_router)


class StrategyPointRequest(BaseModel):
    timestamp: datetime
    symbol: str
    contract_month: str
    cash_price: float
    future_price: float
    gap: float
    gap_pct: float = 0.0
    lot_size: int = Field(gt=0)
    margin_required: float = Field(ge=0)
    volume: int = Field(default=0, ge=0)
    oi: int = Field(default=0, ge=0)
    cash_bid: float | None = Field(default=None, gt=0)
    cash_ask: float | None = Field(default=None, gt=0)
    future_bid: float | None = Field(default=None, gt=0)
    future_ask: float | None = Field(default=None, gt=0)
    charges: float = Field(default=0.0, ge=0)
    funding_cost: float = Field(default=0.0, ge=0)
    expiry_date: date | None = None


class StrategyRunRequest(BaseModel):
    strategy_id: str = Field(min_length=1)
    strategy_version: str = Field(default="1", min_length=1)
    start_date: date | None = None
    end_date: date | None = None
    contract_month: str | None = None
    execution_model: str = Field(default="gap", pattern="^(gap|bid_ask)$")
    charges_per_trade: float = Field(default=0.0, ge=0)
    funding_cost_per_trade: float = Field(default=0.0, ge=0)
    initial_capital: float = Field(default=100_000_000.0, gt=0)
    points: list[StrategyPointRequest] | None = None
    spot_instrument: str | None = None
    exchange: str = "NFO"
    underlying: str | None = None
    timeframe: str = "1m"
    mode: str = Field(default="CURRENT", pattern="^(CURRENT|NEAR)$")
    source: str = "angelone"
    cash_side: str = Field(default="BUY", pattern="^(BUY|SELL)$")
    future_side: str = Field(default="SELL", pattern="^(BUY|SELL)$")
    stop_loss: float | None = Field(default=None, ge=0)
    target: float | None = Field(default=None, ge=0)
    slippage_per_share: float = Field(default=0.0, ge=0)

    @model_validator(mode="after")
    def validate_input_mode(self):
        if self.points is None:
            if self.start_date is None or self.end_date is None:
                raise ValueError("start_date and end_date are required when points are omitted")
            if not self.spot_instrument or not self.underlying:
                raise ValueError("spot_instrument and underlying are required when points are omitted")
        elif not self.points:
            raise ValueError("points cannot be empty")
        return self


def _strategy_registry() -> dict[str, Any]:
    return {
        "gap_threshold": lambda current, history: (
            "BUY" if current.gap > 0 else "SELL" if current.gap < 0 else "HOLD"
        ),
    }


def _serialise_run(ledger: BacktestLedger, run_id: str) -> dict[str, Any]:
    metadata = ledger.run_metadata(run_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail=f"unknown Cash-Future run: {run_id}")
    signals = tuple(record.payload for record in ledger.records(run_id, "signal"))
    trades = tuple(record.payload for record in ledger.records(run_id, "trade"))
    equity = tuple(record.payload for record in ledger.records(run_id, "equity"))
    initial_capital = float(metadata["initial_capital"])
    final_capital = float(equity[-1]["equity"]) if equity else initial_capital
    final_available_capital = float(equity[-1]["available_capital"]) if equity else initial_capital
    final_reserved_margin = float(equity[-1]["reserved_margin"]) if equity else 0.0
    blocked_entry_count = sum(1 for signal in signals if signal.get("execution_status") == "blocked")
    return {
        "status": "success",
        "run_id": run_id,
        "strategy_id": metadata["strategy_id"],
        "strategy_version": metadata["strategy_version"],
        "initial_capital": initial_capital,
        "final_capital": final_capital,
        "final_available_capital": final_available_capital,
        "final_reserved_margin": final_reserved_margin,
        "blocked_entry_count": blocked_entry_count,
        "net_profit": final_capital - initial_capital,
        "signal_count": len(signals),
        "trade_count": len(trades),
        "signals": signals,
        "trades": trades,
        "equity_curve": equity,
    }


def _build_builder_strategy(request: StrategyRunRequest):
    """Adapt the generic gap signal to the builder's selected cash/future legs.

    The runner's BUY/SELL lifecycle represents opening/closing the selected spread.
    Reversing the observed gap for SELL-cash/BUY-future makes the same deterministic
    lifecycle calculate the opposite spread direction without duplicating the engine.
    Stop/target are measured as absolute per-share spread P&L from the opened gap.
    """
    orientation = 1.0 if (request.cash_side, request.future_side) == ("BUY", "SELL") else -1.0
    stop = request.stop_loss
    target = request.target
    state: dict[str, float | None] = {"entry_gap": None}

    def strategy(current: CashFutureHistoryPoint, history: tuple[CashFutureHistoryPoint, ...]):
        effective_gap = orientation * float(current.gap)
        entry_gap = state["entry_gap"]
        if entry_gap is None:
            if effective_gap > 0:
                state["entry_gap"] = effective_gap
                return "BUY"
            return "HOLD"

        spread_profit_per_share = entry_gap - effective_gap
        if target is not None and spread_profit_per_share >= target:
            state["entry_gap"] = None
            return "SELL"
        if stop is not None and spread_profit_per_share <= -stop:
            state["entry_gap"] = None
            return "SELL"
        if effective_gap <= 0:
            state["entry_gap"] = None
            return "SELL"
        return "HOLD"

    return strategy


@router.post("/strategy-run")
def strategy_run(request: StrategyRunRequest):
    strategy = _strategy_registry().get(request.strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail=f"unknown Cash-Future strategy: {request.strategy_id}")
    if request.strategy_id == "gap_threshold":
        strategy = _build_builder_strategy(request)

    catalog: HistoricalCatalog | None = None
    contracts: ContractMasterCatalog | None = None
    ledger: BacktestLedger | None = None
    run_id = f"cash-future-{uuid4().hex}"
    try:
        if request.points is not None:
            points = tuple(CashFutureHistoryPoint(**point.model_dump()) for point in request.points)
        else:
            assert request.start_date is not None and request.end_date is not None
            assert request.spot_instrument is not None and request.underlying is not None
            catalog = HistoricalCatalog(settings.BACKTEST_DATA_DB)
            contracts = ContractMasterCatalog(settings.BACKTEST_CONTRACT_DB)
            selection = CashFutureHistorySelection(
                spot_instrument=request.spot_instrument,
                exchange=request.exchange,
                underlying=request.underlying,
                start_date=request.start_date,
                end_date=request.end_date,
                timeframe=request.timeframe,
                contract_month=request.contract_month,
                mode=request.mode,
                source=request.source,
            )
            points = CashFutureHistoricalLoader(catalog, contracts).iter_points(selection)

        ledger = BacktestLedger(settings.BACKTEST_LEDGER_DB)
        result = run_cash_future_strategy(
            points,
            strategy,
            strategy_id=request.strategy_id,
            strategy_version=request.strategy_version,
            config=CashFutureStrategyConfig(
                initial_capital=request.initial_capital,
                execution_model=request.execution_model,
                charges_per_trade=request.charges_per_trade,
                funding_cost_per_trade=request.funding_cost_per_trade,
                start_date=request.start_date,
                end_date=request.end_date,
                contract_month=request.contract_month,
            ),
            ledger=ledger,
            run_id=run_id,
        )
    except (ValueError, LookupError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        if catalog is not None:
            catalog.close()
        if contracts is not None:
            contracts.close()
        if ledger is not None:
            ledger.close()

    return {
        "status": "success",
        "run_id": run_id,
        "strategy_id": result.strategy_id,
        "strategy_version": result.strategy_version,
        "initial_capital": result.initial_capital,
        "final_capital": result.final_capital,
        "final_available_capital": result.final_available_capital,
        "final_reserved_margin": result.final_reserved_margin,
        "blocked_entry_count": result.blocked_entry_count,
        "net_profit": result.net_profit,
        "signal_count": len(result.signals),
        "trade_count": len(result.trades),
        "signals": result.signals,
        "trades": result.trades,
        "equity_curve": result.equity_curve,
    }


@router.get("/strategy-run/{run_id}")
def strategy_run_result(run_id: str):
    ledger = BacktestLedger(settings.BACKTEST_LEDGER_DB)
    try:
        return _serialise_run(ledger, run_id)
    finally:
        ledger.close()


__all__ = ["router"]
