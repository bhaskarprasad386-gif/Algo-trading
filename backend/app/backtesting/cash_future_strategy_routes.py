"""API surface for historical Cash-Future strategy runs."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.backtesting.cash_future_strategy_runner import (
    CashFutureStrategyConfig,
    run_cash_future_strategy,
)
from app.scanner.cash_future_history import CashFutureHistoryPoint

router = APIRouter(prefix="/api/v1/backtesting/cash-future", tags=["Cash-Future Backtesting"])


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
    initial_capital: float = Field(default=10_000_000.0, gt=0)
    points: list[StrategyPointRequest] = Field(min_length=1)


# API-safe built-in strategy registry. The callable runner remains generic;
# additional strategy adapters can be registered without changing execution.
def _strategy_registry() -> dict[str, Any]:
    return {
        "gap_threshold": lambda current, history: (
            "BUY" if current.gap > 0 else "SELL" if current.gap < 0 else "HOLD"
        ),
    }


@router.post("/strategy-run")
def strategy_run(request: StrategyRunRequest):
    strategy = _strategy_registry().get(request.strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail=f"unknown Cash-Future strategy: {request.strategy_id}")

    points = tuple(CashFutureHistoryPoint(**point.model_dump()) for point in request.points)
    try:
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
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "status": "success",
        "strategy_id": result.strategy_id,
        "strategy_version": result.strategy_version,
        "initial_capital": result.initial_capital,
        "final_capital": result.final_capital,
        "net_profit": result.net_profit,
        "signal_count": len(result.signals),
        "trade_count": len(result.trades),
        "signals": result.signals,
        "trades": result.trades,
        "equity_curve": result.equity_curve,
    }


__all__ = ["router"]
